"""AI Chat module — persistent model definitions.

Dataset UUIDs are permanent and must never change after first use.
They identify these datasets across every Forge project that installs this module.
"""

from __future__ import annotations

from forge.model import ForgeSnapshotModel, ForgeStreamModel, field_def, forge_model

# Dataset UUIDs are owned by the pipelines that produce them.
# The model layer imports them — dependency flows downward (pipeline → model).
from pipelines.init_datasets import (
    CHAT_SESSION_DATASET_ID,
    CHAT_MESSAGE_DATASET_ID,
)  # noqa: E402
from pipelines.index_skills import SKILL_INDEX_DATASET_ID  # noqa: E402


# ── Models ────────────────────────────────────────────────────────────────────


@forge_model(mode="snapshot", backing_dataset=CHAT_SESSION_DATASET_ID)
class ChatSession(ForgeSnapshotModel):
    """A persistent chat conversation with a chosen mode and model."""

    id: str = field_def(primary_key=True, display="ID")
    title: str = field_def(display="Title")
    mode: str = field_def(display="Mode")  # "train" | "ask"
    model_id: str = field_def(display="Model")
    created_at: str = field_def(display="Created", display_hint="datetime")
    updated_at: str = field_def(display="Updated", display_hint="datetime")


@forge_model(mode="snapshot", backing_dataset=CHAT_MESSAGE_DATASET_ID)
class ChatMessage(ForgeSnapshotModel):
    """A single message within a chat session."""

    id: str = field_def(primary_key=True, display="ID")
    session_id: str = field_def(display="Session")
    role: str = field_def(display="Role")  # "user" | "assistant" | "system"
    content: str = field_def(display="Content")
    created_at: str = field_def(display="Created", display_hint="datetime")


@forge_model(mode="stream", backing_dataset=SKILL_INDEX_DATASET_ID)
class SkillIndex(ForgeStreamModel):
    """Skill file metadata index — populated by the index_skills pipeline, not by CRUD.

    The skill file *content* lives on disk; this dataset exists so skills are
    visible in the Forge Suite UI and queryable by the service layer.
    """

    id: str = field_def(primary_key=True, display="ID")
    name: str = field_def(display="Name")
    description: str = field_def(display="Description", nullable=True)
    version: int = field_def(display="Version")
    depends_on: str = field_def(display="Dependencies")  # JSON array of skill names
    triggers: str = field_def(display="Triggers")  # JSON array of trigger phrases
    file_path: str = field_def(display="File Path")
    source: str = field_def(display="Source")  # "project" | "package"
    last_indexed_at: str = field_def(display="Last Indexed", display_hint="datetime")
