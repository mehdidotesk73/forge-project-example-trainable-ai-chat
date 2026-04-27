"""Multi-provider LLM abstraction.

Add a new provider by subclassing LLMProvider and registering it in
AVAILABLE_MODELS below. No other file needs to change.

Required environment variables (only the providers you use):
    ANTHROPIC_API_KEY   (Claude Agent SDK — skill-aware Anthropic provider)
    OPENAI_API_KEY      (OpenAI provider)
"""

from __future__ import annotations

import os
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Generator


# ── API key error ─────────────────────────────────────────────────────────────


class ApiKeyRequiredError(RuntimeError):
    """Raised when a required provider API key is missing."""

    def __init__(self, provider: str) -> None:
        self.provider = provider
        super().__init__(f"{provider.upper()}_API_KEY environment variable is not set.")


# ── Abstract base ─────────────────────────────────────────────────────────────


class LLMProvider(ABC):
    """Common interface for all LLM providers."""

    @abstractmethod
    def stream_chat(
        self,
        messages: list[dict],  # [{"role": "user"|"assistant", "content": "..."}]
        system: str = "",
        max_tokens: int = 4096,
    ) -> Generator[str, None, None]:
        """Yield text tokens as they arrive from the model."""
        ...

    @abstractmethod
    def chat(
        self,
        messages: list[dict],
        system: str = "",
        max_tokens: int = 4096,
    ) -> str:
        """Return the full response in one call (used for skill extraction)."""
        ...


# ── Claude Agent SDK (Anthropic) ──────────────────────────────────────────────
# Module root is four levels up from this file:
#   endpoint_repos/ai_chat_endpoints/ai_chat_endpoints/llm.py  →  ai-chat/
_MODULE_ROOT: Path = Path(__file__).parent.parent.parent.parent


class ClaudeAgentSDKProvider(LLMProvider):
    """Anthropic provider backed by the Claude Agent SDK.

    Skills are automatically discovered from .claude/skills/ in the module root.
    Claude invokes them autonomously based on context — no manual trigger matching
    needed.  The service layer still parses <SKILL_UPDATE> blocks from Claude's
    text response and writes the resulting SKILL.md files itself, so the Write
    tool is intentionally excluded from allowed_tools.

    Conversation history is injected into the system_prompt so that the single-
    prompt SDK query() interface has full context on every turn.
    """

    def __init__(self, model: str = "claude-3-5-sonnet-20241022") -> None:
        try:
            from dotenv import load_dotenv

            load_dotenv(_MODULE_ROOT / ".env", override=False)
        except ImportError:
            pass
        try:
            from claude_agent_sdk import (
                AssistantMessage,
                ClaudeAgentOptions,
                ResultMessage,
                query,
            )
        except ImportError as exc:
            raise RuntimeError(
                "claude-agent-sdk not installed. "
                "Run: pip install forge-modules-ai-chat[anthropic]"
            ) from exc
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise ApiKeyRequiredError("anthropic")
        self._query = query
        self._Options = ClaudeAgentOptions
        self._AssistantMessage = AssistantMessage
        self._ResultMessage = ResultMessage
        self.model = model
        # cwd must contain .claude/skills/ for SDK skill discovery
        self._cwd = str(_MODULE_ROOT)

    # ── helpers ───────────────────────────────────────────────────────────────

    def _format_history(self, messages: list[dict]) -> str:
        """Format all but the last message as a conversation history block."""
        prior = messages[:-1]
        if not prior:
            return ""
        lines = ["== CONVERSATION HISTORY =="]
        for msg in prior:
            role = "Human" if msg["role"] == "user" else "Assistant"
            lines.append(f"\n{role}: {msg['content']}")
        return "\n".join(lines)

    def _make_options(self, system: str, history: str) -> object:
        full_system = "\n\n".join(part for part in [system, history] if part)
        return self._Options(
            cwd=self._cwd,
            setting_sources=["project"],  # discovers .claude/skills/ in cwd
            allowed_tools=["Skill"],  # automatic skill use; no file writes
            permission_mode="dontAsk",  # deny any tool not in allowed_tools
            model=self.model,
            system_prompt=full_system or None,
        )

    # ── LLMProvider interface ─────────────────────────────────────────────────

    def stream_chat(
        self,
        messages: list[dict],
        system: str = "",
        max_tokens: int = 4096,
    ) -> Generator[str, None, None]:
        import asyncio
        import queue as _q

        current_prompt = messages[-1]["content"] if messages else ""
        history = self._format_history(messages)
        options = self._make_options(system, history)

        sentinel = object()
        token_q: _q.Queue = _q.Queue()

        async def _run() -> None:
            try:
                async for msg in self._query(prompt=current_prompt, options=options):
                    print(
                        f"[DEBUG claude_sdk] msg type={type(msg).__name__}", flush=True
                    )
                    if isinstance(msg, self._AssistantMessage):
                        for block in msg.content:
                            print(
                                f"[DEBUG claude_sdk]   block type={type(block).__name__} has_text={hasattr(block, 'text')}",
                                flush=True,
                            )
                            if hasattr(block, "text") and isinstance(block.text, str):
                                token_q.put(block.text)
            except Exception as exc:
                print(f"[DEBUG claude_sdk] _run exception: {exc}", flush=True)
                token_q.put(exc)
            finally:
                token_q.put(sentinel)

        thread = threading.Thread(target=lambda: asyncio.run(_run()), daemon=True)
        thread.start()

        while True:
            item = token_q.get()
            if item is sentinel:
                break
            if isinstance(item, Exception):
                raise item
            yield item

    def chat(
        self,
        messages: list[dict],
        system: str = "",
        max_tokens: int = 4096,
    ) -> str:
        return "".join(self.stream_chat(messages, system=system, max_tokens=max_tokens))


# ── OpenAI ────────────────────────────────────────────────────────────────────


class OpenAIProvider(LLMProvider):
    def __init__(self, model: str = "gpt-4o") -> None:
        try:
            from dotenv import load_dotenv

            load_dotenv(_MODULE_ROOT / ".env", override=False)
        except ImportError:
            pass
        try:
            import openai as _oai
        except ImportError as exc:
            raise RuntimeError(
                "openai package not installed. "
                "Run: pip install forge-modules-ai-chat[openai]"
            ) from exc
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise ApiKeyRequiredError("openai")
        self._client = _oai.OpenAI(api_key=api_key)
        self.model = model

    def _build_messages(self, messages: list[dict], system: str) -> list[dict]:
        full: list[dict] = []
        if system:
            full.append({"role": "system", "content": system})
        full.extend(messages)
        return full

    def stream_chat(
        self,
        messages: list[dict],
        system: str = "",
        max_tokens: int = 4096,
    ) -> Generator[str, None, None]:
        stream = self._client.chat.completions.create(
            model=self.model,
            messages=self._build_messages(messages, system),  # type: ignore[arg-type]
            max_tokens=max_tokens,
            stream=True,
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content or ""
            if delta:
                yield delta

    def chat(
        self,
        messages: list[dict],
        system: str = "",
        max_tokens: int = 4096,
    ) -> str:
        response = self._client.chat.completions.create(
            model=self.model,
            messages=self._build_messages(messages, system),  # type: ignore[arg-type]
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content or ""


# ── Registry ──────────────────────────────────────────────────────────────────

#: All models the module knows about — surfaced to the frontend via list_available_models.
AVAILABLE_MODELS: list[dict] = [
    {"id": "claude-opus-4-5", "name": "Claude Opus 4.5", "provider": "anthropic"},
    {"id": "claude-sonnet-4-6", "name": "Claude 4.6 Sonnet", "provider": "anthropic"},
    {"id": "claude-3-opus-20240229", "name": "Claude 3 Opus", "provider": "anthropic"},
    {
        "id": "claude-haiku-4-5-20251001",
        "name": "Claude 3 Haiku",
        "provider": "anthropic",
    },
    {"id": "gpt-4o", "name": "GPT-4o", "provider": "openai"},
    {"id": "gpt-4o-mini", "name": "GPT-4o Mini", "provider": "openai"},
    {"id": "gpt-4-turbo", "name": "GPT-4 Turbo", "provider": "openai"},
    {"id": "o1", "name": "o1", "provider": "openai"},
    {"id": "o3-mini", "name": "o3-mini", "provider": "openai"},
]

_EXACT: dict[str, type[LLMProvider]] = {
    m["id"]: ClaudeAgentSDKProvider if m["provider"] == "anthropic" else OpenAIProvider
    for m in AVAILABLE_MODELS
}


def resolve_provider(model_id: str) -> LLMProvider:
    """Resolve a model_id string to a ready-to-use LLMProvider instance."""
    if model_id in _EXACT:
        return _EXACT[model_id](model_id)  # type: ignore[call-arg]

    lower = model_id.lower()
    if "claude" in lower:
        return ClaudeAgentSDKProvider(model_id)
    if "gpt" in lower or lower.startswith("o1") or lower.startswith("o3"):
        return OpenAIProvider(model_id)

    raise ValueError(
        f"Unknown model_id {model_id!r}. "
        f"Supported: {[m['id'] for m in AVAILABLE_MODELS]}"
    )
