"""Synthetic fixtures shaped from the official API docs, never recorded user data."""

import os
import socket
import tempfile
from copy import deepcopy

import pytest

os.environ["COGNEE_SKIP_CONNECTION_TEST"] = "true"
os.environ["DLT_TELEMETRY"] = "false"
os.environ["RUNTIME__DLTHUB_TELEMETRY"] = "false"
os.environ["ENABLE_TELEMETRY"] = "false"
os.environ["SYSTEM_ROOT_DIRECTORY"] = tempfile.mkdtemp(prefix="otter-system-")
os.environ["DATA_ROOT_DIRECTORY"] = tempfile.mkdtemp(prefix="otter-data-")


@pytest.fixture(autouse=True)
def no_external_network(monkeypatch):
    def blocked(*_args, **_kwargs):
        raise AssertionError("Offline tests must not open network connections.")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)


def conversation(conversation_id="a"):
    return {
        "id": conversation_id,
        "title": "Design review",
        "url": f"https://otter.ai/u/{conversation_id}",
        "created_at": "2026-01-01T12:00:00Z",
        "owner": {"id": "owner", "name": "Robin", "email": "private@example.test"},
        "calendar_guests": [{"name": "Sam"}, {"name": "Pat"}],
        "shared_channels": [{"channel": {"id": "team", "name": "Engineering"}}],
        "abstract_summary": "We agreed to document the rollout.",
        "process_status": {"abstract_summary": "finished"},
        "relationships": {
            "transcript": {
                "content": "Robin  00:00\nShip on Friday.\n\nSpeaker 2  00:07\nAgreed.",
                "format": "txt",
            },
            "action_items": [
                {
                    "id": "action",
                    "text": "Write the guide",
                    "assignee": {"name": "Sam"},
                    "status": {"completed": False},
                }
            ],
            "insights": [{"topic": "Timing", "text": ["Friday is the target."]}],
            "outline": [{"section": "Release", "text": ["Discussed rollout."]}],
        },
    }


class FakeAPI:
    def __init__(self, records=None):
        self.records = records if records is not None else [conversation()]
        self.list_calls = []
        self.detail_calls = []
        self.errors = {}

    def list_conversations(self, **kwargs):
        self.list_calls.append(kwargs)
        return [item["id"] for item in self.records]

    def get_conversation(self, conversation_id):
        self.detail_calls.append(conversation_id)
        if conversation_id in self.errors:
            raise self.errors[conversation_id]
        return deepcopy(next(item for item in self.records if item["id"] == conversation_id))


@pytest.fixture
def api():
    return FakeAPI()
