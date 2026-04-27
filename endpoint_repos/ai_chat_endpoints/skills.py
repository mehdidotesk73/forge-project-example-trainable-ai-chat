"""Skill file management — loading, writing, dependency resolution.

Primary skill location (Claude Agent SDK format):
  {module_root}/.claude/skills/<name>/SKILL.md  — read/write; SDK auto-discovers these

Legacy fallback (read-only, for migration):
  {module_root}/skills/<name>.md                — flat Markdown with YAML frontmatter

SKILL.md files use YAML frontmatter (name, description, version, depends_on,
triggers) followed by freeform Markdown that Claude reads when it invokes the
skill via the Skill tool.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

# Module root is four levels up from this file:
#   endpoint_repos/ai_chat_endpoints/ai_chat_endpoints/skills.py  →  ai-chat/
_MODULE_ROOT: Path = Path(__file__).parent.parent.parent.parent

# Legacy flat-file skills directory (read-only, kept for migration)
PACKAGE_SKILLS_DIR: Path = _MODULE_ROOT / "skills"


def _claude_skills_dir() -> Path:
    """Return .claude/skills/ in the module root (created if absent).

    This is the directory the Claude Agent SDK discovers skills from when cwd
    is set to the module root.
    """
    d = _MODULE_ROOT / ".claude" / "skills"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Frontmatter parsing ───────────────────────────────────────────────────────


def _parse_frontmatter(content: str) -> tuple[dict, str]:
    if not content.startswith("---"):
        return {}, content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        meta = {}
    return meta, parts[2].strip()


def _write_frontmatter(meta: dict, body: str) -> str:
    fm = yaml.dump(
        meta, default_flow_style=False, allow_unicode=True, sort_keys=False
    ).strip()
    return f"---\n{fm}\n---\n\n{body}\n"


# ── Listing ───────────────────────────────────────────────────────────────────


def list_all_skills() -> list[dict]:
    """Return metadata dicts for all available skills.

    Primary source: .claude/skills/<name>/SKILL.md in the module root.
    Fallback: legacy flat .md files in skills/ (read-only, for migration).
    """
    seen: set[str] = set()
    skills: list[dict] = []

    # Primary: SDK-format skill directories
    claude_dir = _claude_skills_dir()
    for skill_dir in sorted(d for d in claude_dir.iterdir() if d.is_dir()):
        path = skill_dir / "SKILL.md"
        if not path.exists():
            continue
        meta, _ = _parse_frontmatter(path.read_text(encoding="utf-8"))
        skill_id = meta.get("name") or skill_dir.name
        if skill_id in seen:
            continue
        seen.add(skill_id)
        skills.append(
            {**meta, "name": skill_id, "source": "module", "file_path": str(path)}
        )

    # Fallback: legacy flat .md files
    if PACKAGE_SKILLS_DIR.exists():
        for path in sorted(PACKAGE_SKILLS_DIR.glob("*.md")):
            meta, _ = _parse_frontmatter(path.read_text(encoding="utf-8"))
            skill_id = meta.get("name") or path.stem
            if skill_id in seen:
                continue
            seen.add(skill_id)
            skills.append(
                {**meta, "name": skill_id, "source": "package", "file_path": str(path)}
            )

    return skills


# ── Single skill access ───────────────────────────────────────────────────────


def _normalize(name: str) -> str:
    return name.lower().replace(" ", "-").replace("_", "-")


def load_skill(skill_name: str) -> tuple[dict, str] | None:
    """Load a skill by name. Returns (meta, body) or None."""
    norm = _normalize(skill_name)

    # Primary: SDK-format .claude/skills/<name>/SKILL.md
    claude_dir = _claude_skills_dir()
    for skill_dir in claude_dir.iterdir():
        if not skill_dir.is_dir():
            continue
        path = skill_dir / "SKILL.md"
        if not path.exists():
            continue
        meta, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
        if _normalize(meta.get("name", skill_dir.name)) == norm:
            return meta, body

    # Fallback: legacy flat .md
    if PACKAGE_SKILLS_DIR.exists():
        for path in PACKAGE_SKILLS_DIR.glob("*.md"):
            meta, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
            if _normalize(meta.get("name", path.stem)) == norm:
                return meta, body

    return None


def load_skill_tree(
    skill_name: str, _visited: set[str] | None = None
) -> dict[str, tuple[dict, str]]:
    """Recursively load a skill and all its declared dependencies."""
    visited = _visited if _visited is not None else set()
    norm = _normalize(skill_name)
    if norm in visited:
        return {}
    visited.add(norm)

    skill = load_skill(skill_name)
    if skill is None:
        return {}

    meta, body = skill
    result: dict[str, tuple[dict, str]] = {meta.get("name", skill_name): (meta, body)}
    for dep in meta.get("depends_on") or []:
        result.update(load_skill_tree(dep, visited))
    return result


# ── Trigger matching ──────────────────────────────────────────────────────────


def build_skill_context(skill_names: list[str]) -> str:
    if not skill_names:
        return ""
    all_loaded: dict[str, tuple[dict, str]] = {}
    for name in skill_names:
        all_loaded.update(load_skill_tree(name))

    sections: list[str] = []
    for name, (meta, body) in all_loaded.items():
        header = f"## Skill: {meta.get('name', name)}"
        if meta.get("description"):
            header += f"\n*{meta['description']}*"
        sections.append(f"{header}\n\n{body}")
    return "\n\n---\n\n".join(sections)


# ── Writing ───────────────────────────────────────────────────────────────────


def write_skill(skill_name: str, meta: dict, body: str) -> Path:
    """Write or update a skill in .claude/skills/<name>/SKILL.md.

    The SDK discovers this file automatically on the next query.
    Archives the previous version before overwriting and increments version number.
    """
    claude_dir = _claude_skills_dir()
    skill_dir = claude_dir / _normalize(skill_name)
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"

    existing = load_skill(skill_name)
    if existing:
        old_meta, _ = existing
        old_version = old_meta.get("version", 0)
        new_version = old_version + 1
        if skill_file.exists():
            archive_dir = skill_dir / "archive"
            archive_dir.mkdir(exist_ok=True)
            shutil.copy2(skill_file, archive_dir / f"SKILL.v{old_version}.md")
    else:
        new_version = 1

    full_meta = {
        "name": skill_name,
        "description": meta.get("description", ""),
        "version": new_version,
        "depends_on": meta.get("depends_on") or [],
        "triggers": meta.get("triggers") or [],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    skill_file.write_text(_write_frontmatter(full_meta, body), encoding="utf-8")
    return skill_file
