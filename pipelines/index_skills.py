"""index_skills pipeline — scans both skill locations and writes the SkillIndex dataset.

Run this pipeline whenever skills are added, updated, or deleted to keep the
Forge Suite UI in sync with the filesystem.

The pipeline owns all file-scanning logic directly; it has no knowledge of the
endpoint or view layers above it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml

from forge.pipeline.decorator import ForgeOutput, pipeline

# This pipeline is the authoritative producer of the skill index dataset.
# The UUID is declared here; the model layer imports it from this module.
SKILL_INDEX_DATASET_ID = "a1ca4000-0001-0000-0000-000000000003"

# The module skills/ directory lives two levels above this file:
#   pipelines/index_skills.py → pipelines/ → module_root/
_MODULE_ROOT: Path = Path(__file__).parent.parent
_PACKAGE_SKILLS_DIR: Path = _MODULE_ROOT / "skills"


def _parse_frontmatter(content: str) -> dict:
    if not content.startswith("---"):
        return {}
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        return yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}


def _build_index_rows() -> list[dict]:
    """Scan both skill locations and return one dict per skill file."""
    seen: set[str] = set()
    rows: list[dict] = []
    now = datetime.now(timezone.utc).isoformat()

    # 1. Project-local mutable skills take priority
    try:
        from forge.config import find_project_root

        proj_skills = find_project_root() / ".forge" / "skills" / "ai_chat"
        proj_skills.mkdir(parents=True, exist_ok=True)
        for path in sorted(proj_skills.glob("*.md")):
            meta = _parse_frontmatter(path.read_text(encoding="utf-8"))
            skill_id = meta.get("name") or path.stem
            if skill_id in seen:
                continue
            seen.add(skill_id)
            rows.append(_row(skill_id, meta, str(path), "project", now))
    except Exception:
        pass

    # 2. Package-default read-only skills
    if _PACKAGE_SKILLS_DIR.exists():
        for path in sorted(_PACKAGE_SKILLS_DIR.glob("*.md")):
            meta = _parse_frontmatter(path.read_text(encoding="utf-8"))
            skill_id = meta.get("name") or path.stem
            if skill_id in seen:
                continue
            seen.add(skill_id)
            rows.append(_row(skill_id, meta, str(path), "package", now))

    return rows


def _row(skill_id: str, meta: dict, file_path: str, source: str, now: str) -> dict:
    return {
        "id": skill_id,
        "name": skill_id,
        "description": meta.get("description", ""),
        "version": meta.get("version", 1),
        "depends_on": str(meta.get("depends_on") or []),
        "triggers": str(meta.get("triggers") or []),
        "file_path": file_path,
        "source": source,
        "last_indexed_at": now,
    }


@pipeline(
    pipeline_id="a1ca4000-0003-0000-0000-000000000001",
    name="index_skills",
    inputs={},
    outputs={"skill_index": ForgeOutput(SKILL_INDEX_DATASET_ID)},
)
def run(inputs, outputs) -> None:  # type: ignore[override]
    """Scan both skill locations and write one row per skill file to SkillIndex."""
    rows = _build_index_rows()
    df = pd.DataFrame(rows) if rows else _empty_dataframe()
    outputs.skill_index.write(df)


def _empty_dataframe() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "id": pd.Series([], dtype="str"),
            "name": pd.Series([], dtype="str"),
            "description": pd.Series([], dtype="str"),
            "version": pd.Series([], dtype="int64"),
            "depends_on": pd.Series([], dtype="str"),
            "triggers": pd.Series([], dtype="str"),
            "file_path": pd.Series([], dtype="str"),
            "source": pd.Series([], dtype="str"),
            "last_indexed_at": pd.Series([], dtype="str"),
        }
    )
