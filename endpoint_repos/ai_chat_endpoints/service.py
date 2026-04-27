"""AI Chat service layer — LLM orchestration, session management, learning loop.

All business logic lives here. Never apply @action_endpoint or @streaming_endpoint
decorators in this file — those belong in endpoints.py.

Session state (loaded skills, current estimate, iteration context) is kept in a
process-level dict so it survives across multiple HTTP requests within the same
server process. On session resume from a fresh process the state is rebuilt lazily
from the stored ChatMessage history on the next send_message call.
"""

from __future__ import annotations

import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

from ai_chat_endpoints.llm import (
    AVAILABLE_MODELS,
    ApiKeyRequiredError,
    LLMProvider,
    resolve_provider,
)
from ai_chat_endpoints.skills import write_skill
from models.models import ChatMessage, ChatSession

# ── In-process session state ──────────────────────────────────────────────────

_lock = threading.Lock()
_session_cache: dict[str, dict] = {}


def _get_state(session_id: str) -> dict:
    with _lock:
        if session_id not in _session_cache:
            _session_cache[session_id] = {
                "loaded_skills": [],
                "current_estimate": None,
                "current_reasoning": None,
                "previous_estimates": [],
                "phase": "initial",
            }
        return dict(_session_cache[session_id])


def _update_state(session_id: str, **kwargs: Any) -> None:
    with _lock:
        if session_id not in _session_cache:
            _session_cache[session_id] = {}
        _session_cache[session_id].update(kwargs)


def _evict_state(session_id: str) -> None:
    with _lock:
        _session_cache.pop(session_id, None)


# ── Session CRUD ──────────────────────────────────────────────────────────────


def create_session(title: str, mode: str, model_id: str) -> dict:
    sid = f"sess-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()
    ChatSession.create(
        id=sid,
        title=title,
        mode=mode,
        model_id=model_id,
        created_at=now,
        updated_at=now,
    )
    return {
        "session_id": sid,
        "title": title,
        "mode": mode,
        "model_id": model_id,
        "created_at": now,
        "messages": [],
    }


def resume_session(session_id: str) -> dict | None:
    session = ChatSession.get(session_id)
    if session is None:
        return None
    messages = sorted(
        ChatMessage.filter(session_id=session_id), key=lambda m: m.created_at
    )
    return {
        "session_id": session.id,
        "title": session.title,
        "mode": session.mode,
        "model_id": session.model_id,
        "created_at": session.created_at,
        "messages": [
            {"role": m.role, "content": m.content, "created_at": m.created_at}
            for m in messages
        ],
    }


def list_sessions() -> list[dict]:
    sessions = ChatSession.all()
    return sorted(
        [
            {
                "session_id": s.id,
                "title": s.title,
                "mode": s.mode,
                "model_id": s.model_id,
                "updated_at": s.updated_at,
                "created_at": s.created_at,
            }
            for s in sessions
        ],
        key=lambda d: d["updated_at"],
        reverse=True,
    )


def update_session(
    session_id: str,
    mode: str | None = None,
    model_id: str | None = None,
) -> dict | None:
    session = ChatSession.get(session_id)
    if session is None:
        return None

    old_mode = session.mode
    old_model_id = session.model_id
    now = datetime.now(timezone.utc).isoformat()
    updates: dict = {"updated_at": now}
    if mode is not None:
        updates["mode"] = mode
    if model_id is not None:
        updates["model_id"] = model_id
    session.update(**updates)

    log_message: str | None = None

    # Inject a system message so the conversation log reflects the change.
    if mode is not None and mode != old_mode:
        notice = f"[Session switched to {mode.upper()} mode]"
        ChatMessage.create(
            id=f"sys-{uuid.uuid4().hex}",
            session_id=session_id,
            role="system",
            content=notice,
            created_at=now,
        )
        log_message = notice
        # Clear in-process state so the next message starts fresh for the new mode.
        _evict_state(session_id)
    elif model_id is not None and model_id != old_model_id:
        log_message = f"Model changed to: {model_id}"

    result = resume_session(session_id)
    if result is not None and log_message is not None:
        result["log_message"] = log_message
    return result


def delete_session(session_id: str) -> None:
    session = ChatSession.get(session_id)
    if session:
        session.remove()
    for msg in ChatMessage.filter(session_id=session_id):
        msg.remove()
    _evict_state(session_id)


# ── System prompts ────────────────────────────────────────────────────────────

_ASK_SYSTEM = """\
You are an AI assistant operating in ASK MODE.

Your skills are automatically available via the Skill tool — use them to ground
every answer in domain-specific knowledge you have been trained on.

Rules (absolute — no exceptions):
- Answer using only knowledge encoded in your loaded skills.
- Never suggest improvements to your own logic.
- If the user implies you should learn or correct yourself, respond:
  "I am in Ask mode and cannot update my skills right now. Please switch to Train mode."
- No files are written. No skills are updated.
"""

_TRAIN_SYSTEM = """\
You are an AI assistant operating in TRAIN MODE. Your purpose is to learn from this
conversation and permanently improve your skill files.

== REQUIRED BEHAVIOURS ==

TRANSPARENCY: State which skills you loaded and the reasoning chain you used.

BASELINE: When asked about a domain task, acknowledge what you know, then ask for a
concrete example before producing an estimate. Never jump to an answer.

ESTIMATION: Use all loaded skills (and their dependency chains) to produce your best
current answer. Label uncertainty explicitly.

FEEDBACK: After every answer ask "How did I do?" or equivalent.

APPROVAL: When the user approves (e.g. "that's right", "approved", "save this"), emit a
<SKILL_UPDATE> block immediately after your confirmation (format below).

CORRECTION: When the user rejects an answer, ask which correction modality fits:
  - Explain the gap in plain English
  - Provide a reference table, formula, or pricing document
  - Share a URL for me to reason from
  - Upload a file
After the correction, produce a new answer AND present both answers side by side,
explaining the reasoning difference. Ask which logic is more defensible.

ITERATION: The most recent estimate is always the baseline — never compare against the
original if there have been revisions.

REJECTION EVIDENCE: When revising, note what context made the old approach fail:
"In the context of [X], the previous approach underestimated because [Y]."

BLIND SPOTS: If training examples are all from one narrow context, say so:
"My skill has only been trained on [type]. Should we add variety before consolidating?"

CONSOLIDATION: When a skill grows large, propose consolidating into principles.
Never consolidate without explicit user approval. Archive previous version first.

SPLIT: When examples cluster into distinct sub-domains with a clear routing rule,
propose a split. State the discriminator explicitly.

== SKILL_UPDATE FORMAT ==
Emit ONLY after explicit user approval. Parsed by the server to write the skill file.

<SKILL_UPDATE>
skill_name: <slug — e.g. project-estimation>
action: update
description: <one sentence>
depends_on:
  - <dep-skill-slug>
triggers:
  - <trigger phrase>
approved_logic: |
  <principles that worked and why>
approved_example: |
  Task: <description>
  Answer: <answer>
  Key variables: <what mattered>
rejection_notes: |
  <optional — what failed and in what context>
</SKILL_UPDATE>

{session_context}
"""


def _build_system_prompt(session: Any, state: dict) -> str:
    if session.mode == "ask":
        return _ASK_SYSTEM

    phase_notes: list[str] = []
    if state["current_estimate"]:
        phase_notes.append(
            f"Current estimate on the table:\n{state['current_estimate']}"
        )
    if state["current_reasoning"]:
        phase_notes.append(f"Reasoning used:\n{state['current_reasoning']}")
    if state["previous_estimates"]:
        prev = state["previous_estimates"][-1]
        phase_notes.append(
            f"Previous estimate (v{len(state['previous_estimates'])}):\n"
            f"{prev['estimate']}\nReasoning: {prev['reasoning']}"
        )
    session_context_text = (
        "\n\n".join(phase_notes) if phase_notes else "No estimate produced yet."
    )

    return _TRAIN_SYSTEM.format(
        session_context=f"== SESSION CONTEXT ==\n\n{session_context_text}",
    )


# ── SKILL_UPDATE extraction ───────────────────────────────────────────────────

_SKILL_UPDATE_RE = re.compile(r"<SKILL_UPDATE>\s*(.*?)\s*</SKILL_UPDATE>", re.DOTALL)


def _extract_skill_updates(text: str) -> list[dict]:
    updates: list[dict] = []
    for match in _SKILL_UPDATE_RE.finditer(text):
        try:
            import yaml

            parsed = yaml.safe_load(match.group(1))
            if isinstance(parsed, dict) and parsed.get("skill_name"):
                updates.append(parsed)
        except Exception:
            pass
    return updates


def _apply_skill_update(update: dict) -> Path | None:
    try:
        skill_name: str = update["skill_name"]
        meta = {
            "description": update.get("description", ""),
            "depends_on": update.get("depends_on") or [],
            "triggers": update.get("triggers") or [],
        }
        body_parts: list[str] = []
        if logic := (update.get("approved_logic") or "").strip():
            body_parts.append(f"## Approved Logic\n\n{logic}")
        if example := (update.get("approved_example") or "").strip():
            body_parts.append(f"## Approved Example\n\n{example}")
        if notes := (update.get("rejection_notes") or "").strip():
            body_parts.append(f"## Rejection Evidence\n\n{notes}")
        body = "\n\n".join(body_parts) if body_parts else "No content yet."
        return write_skill(skill_name, meta, body)
    except Exception:
        return None


# ── Main send_message logic ───────────────────────────────────────────────────


def send_message_stream(
    session_id: str,
    message: str,
) -> Generator[tuple[str, str], None, None]:
    """Core generator yielding (event_type, data) tuples.

    event_type values:
      "token"       — incremental LLM text token
      "skill_saved" — "<skill_name>:<path>" when a skill file is written
      "error"       — error description
    """
    session = ChatSession.get(session_id)
    if session is None:
        yield ("error", f"Session {session_id!r} not found.")
        return

    state = _get_state(session_id)

    # Resolve provider before saving the user message so that if the API key is
    # missing the message is not stored and the user can retry cleanly.
    try:
        provider: LLMProvider = resolve_provider(session.model_id)
    except ApiKeyRequiredError as exc:
        yield ("api_key_required", exc.provider)
        return
    except Exception as exc:
        yield ("error", str(exc))
        return

    now = datetime.now(timezone.utc).isoformat()
    ChatMessage.create(
        id=f"msg-{uuid.uuid4().hex}",
        session_id=session_id,
        role="user",
        content=message,
        created_at=now,
    )
    ChatSession.get(session_id).update(updated_at=now)  # type: ignore[union-attr]

    all_msgs = sorted(
        ChatMessage.filter(session_id=session_id), key=lambda m: m.created_at
    )
    llm_messages = [
        {"role": "user" if m.role == "user" else "assistant", "content": m.content}
        for m in all_msgs
        if m.role in ("user", "assistant")
    ]

    system_prompt = _build_system_prompt(session, state)

    print(
        f"[DEBUG send_message] session={session_id} model={session.model_id} mode={session.mode} msgs={len(llm_messages)}",
        flush=True,
    )
    for i, m in enumerate(llm_messages):
        print(
            f"[DEBUG send_message]   msg[{i}] role={m['role']} content={repr(m['content'][:80])}",
            flush=True,
        )

    full_response: list[str] = []
    token_count = 0
    try:
        for token in provider.stream_chat(llm_messages, system=system_prompt):
            full_response.append(token)
            token_count += 1
            yield ("token", token)
    except Exception as exc:
        print(f"[DEBUG send_message] LLM exception: {exc}", flush=True)
        yield ("error", f"LLM error: {exc}")
        return

    print(f"[DEBUG send_message] stream complete, tokens={token_count}", flush=True)

    complete_response = "".join(full_response)
    ChatMessage.create(
        id=f"msg-{uuid.uuid4().hex}",
        session_id=session_id,
        role="assistant",
        content=complete_response,
        created_at=datetime.now(timezone.utc).isoformat(),
    )

    if session.mode == "train":
        for update in _extract_skill_updates(complete_response):
            path = _apply_skill_update(update)
            if path:
                skill_name = update.get("skill_name", "unknown")
                _update_state(
                    session_id,
                    current_estimate=update.get("approved_example"),
                    current_reasoning=update.get("approved_logic"),
                    phase="awaiting_feedback",
                )
                yield ("skill_saved", f"{skill_name}:{path}")


# ── Context upload ────────────────────────────────────────────────────────────


def upload_context(session_id: str, content_type: str, content: str) -> dict:
    """Inject training material into the session history as a system message."""
    session = ChatSession.get(session_id)
    if session is None:
        raise ValueError(f"Session {session_id!r} not found.")
    if session.mode == "ask":
        raise PermissionError(
            "Context upload is not allowed in Ask mode. Switch to Train mode."
        )

    if content_type == "url":
        context_text = (
            f"[Reference URL provided for training]\n\nURL: {content}\n\n"
            "Reason about the pricing, rules, or procedures at this URL when answering. "
            "On approval, incorporate the relevant details into the skill file."
        )
    elif content_type == "text":
        context_text = f"[Training material provided directly]\n\n{content}"
    else:
        raise ValueError(
            f"Unsupported content_type {content_type!r}. Use 'text' or 'url'."
        )

    ChatMessage.create(
        id=f"sys-{uuid.uuid4().hex}",
        session_id=session_id,
        role="system",
        content=context_text,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    return {
        "status": "ok",
        "message": "Training context injected into session history.",
    }
