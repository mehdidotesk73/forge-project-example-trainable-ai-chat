"""
Pipeline Layer � init_datasets — creates empty backing datasets for ChatSession and ChatMessage.

Must be run once before `forge model build` in any project that installs this module.
Running it again on an existing project is safe — it only writes if the dataset is empty.

Dataset UUIDs are declared here because this pipeline is their producer.
The model layer imports them from this module.
"""

from __future__ import annotations

import pandas as pd

from forge.pipeline.decorator import ForgeOutput, pipeline

# These UUIDs are permanent. Never change them after first use.
CHAT_SESSION_DATASET_ID = "a1ca4000-0001-0000-0000-000000000001"
CHAT_MESSAGE_DATASET_ID = "a1ca4000-0001-0000-0000-000000000002"


@pipeline(
    pipeline_id="a1ca4000-0003-0000-0000-000000000002",
    name="init_datasets",
    inputs={},
    outputs={
        "chat_sessions": ForgeOutput(CHAT_SESSION_DATASET_ID),
        "chat_messages": ForgeOutput(CHAT_MESSAGE_DATASET_ID),
    },
)
def run(inputs, outputs) -> None:  # type: ignore[override]
    """Write empty DataFrames to establish the chat dataset schemas."""
    outputs.chat_sessions.write(
        pd.DataFrame(
            {
                "id": pd.Series([], dtype="str"),
                "title": pd.Series([], dtype="str"),
                "mode": pd.Series([], dtype="str"),
                "model_id": pd.Series([], dtype="str"),
                "created_at": pd.Series([], dtype="str"),
                "updated_at": pd.Series([], dtype="str"),
            }
        )
    )
    outputs.chat_messages.write(
        pd.DataFrame(
            {
                "id": pd.Series([], dtype="str"),
                "session_id": pd.Series([], dtype="str"),
                "role": pd.Series([], dtype="str"),
                "content": pd.Series([], dtype="str"),
                "created_at": pd.Series([], dtype="str"),
            }
        )
    )
