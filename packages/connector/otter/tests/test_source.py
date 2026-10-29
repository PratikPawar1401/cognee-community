from copy import deepcopy

import dlt
import httpx
import pytest
from conftest import FakeAPI, conversation
from dlt.pipeline.exceptions import PipelineStepFailed

from cognee_community_connector_otter import otter_source
from cognee_community_connector_otter.client import OtterClient, OtterError, OtterHTTPError
from cognee_community_connector_otter.otter import snapshot
from cognee_community_connector_otter.render import conversation_row


def test_render_preserves_speakers_timestamps_and_supported_metadata():
    data = conversation()
    row = conversation_row(data)
    assert data["relationships"]["transcript"]["content"] in row["content"]
    for value in [
        "Robin",
        "Speaker 2",
        "Created: 2026",
        "Engineering (team)",
        "Write the guide",
        "Assignee: Sam",
        "Friday is the target.",
        "Release",
    ]:
        assert value in row["content"]
    assert row["url"] == data["url"]
    assert "private@example.test" not in str(row)


def test_volatile_metadata_and_unordered_guests_do_not_change_row():
    data = conversation()
    original = conversation_row(data)
    data["retrieved_at"] = "later"
    data["calendar_guests"].reverse()
    data["relationships"]["action_items"][0]["status"]["last_modified_at"] = "later"
    assert conversation_row(data) == original


def test_old_transcript_speaker_correction_changes_row():
    data = conversation()
    original = conversation_row(data)
    data["relationships"]["transcript"]["content"] = (
        "Robin  00:00\nShip on Friday.\nSam  00:07\nAgreed."
    )
    assert conversation_row(data) != original
    assert data["created_at"] == "2026-01-01T12:00:00Z"


@pytest.mark.parametrize("mutation", ["missing", "null", "format", "type", "pending"])
def test_incomplete_content_aborts(mutation):
    data = conversation()
    if mutation == "missing":
        del data["relationships"]["transcript"]
    elif mutation == "null":
        data["relationships"]["transcript"] = None
    elif mutation == "format":
        data["relationships"]["transcript"]["format"] = "unknown"
    elif mutation == "type":
        data["relationships"]["insights"][0]["text"] = "unexpected"
    else:
        data["process_status"]["abstract_summary"] = "processing"
    with pytest.raises(OtterError):
        conversation_row(data)


def test_explicit_ids_deduplicate_without_listing(api):
    rows = list(otter_source(client=api, conversation_ids=["a", "a"]))
    assert len(rows) == 1
    assert api.detail_calls == ["a"]
    assert not api.list_calls


def test_channels_union_deduplicates_and_shared_selection_is_forwarded(api):
    assert len(list(otter_source(client=api, channel_ids=["x", "y"], include_shared=True))) == 1
    assert api.list_calls == [
        {"channel_id": "x", "include_shared": True},
        {"channel_id": "y", "include_shared": True},
    ]
    assert api.detail_calls == ["a"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"conversation_ids": []},
        {"channel_ids": "team"},
        {"conversation_ids": [None]},
        {"conversation_ids": ["a"], "channel_ids": ["team"]},
        {"include_shared": "false"},
        {"api_key": ""},
    ],
)
def test_invalid_selection_rejected(kwargs, api):
    with pytest.raises(ValueError):
        otter_source(client=api, **kwargs)


def test_secrets_environment_provider(monkeypatch, api):
    from cognee_community_connector_otter import otter

    keys = []

    def make_client(api_key):
        keys.append(api_key)
        api.close = lambda: None
        return api

    monkeypatch.setattr(otter, "OtterClient", make_client)
    monkeypatch.setenv("SOURCES__OTTER__API_KEY", "test-secret")
    source = otter_source()
    assert source.cognee_document_source == "otter"
    assert source.cognee_pipeline_scope == "otter"
    assert source.resources["otter_conversations"].write_disposition == "replace"
    assert len(list(source)) == 1
    assert keys == ["test-secret"]


def test_explicit_missing_id_is_absent_but_discovered_missing_aborts(api):
    api.errors["a"] = OtterHTTPError(404)
    assert snapshot(api, conversation_ids=["a"]) == []
    with pytest.raises(OtterHTTPError):
        snapshot(api)


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_explicit_access_and_transient_errors_are_not_deletion(api, status):
    api.errors["a"] = OtterHTTPError(status)
    with pytest.raises(OtterHTTPError):
        snapshot(api, conversation_ids=["a"])


def test_no_rows_escape_if_later_detail_fails():
    api = FakeAPI([conversation("a"), conversation("b")])
    api.errors["b"] = OtterHTTPError(403)
    emitted = []
    with pytest.raises(Exception, match="403"):
        for row in otter_source(client=api):
            emitted.append(row)
    assert emitted == []


def pipeline_at(tmp_path):
    return dlt.pipeline(
        pipeline_name="otter_test",
        destination=dlt.destinations.sqlalchemy(f"sqlite:///{tmp_path / 'test.db'}"),
        dataset_name="otter_ds",
        pipelines_dir=str(tmp_path / "state"),
    )


def read_rows(pipeline):
    with (
        pipeline.sql_client() as sql,
        sql.execute_query("SELECT id, content FROM otter_conversations ORDER BY id") as cursor,
    ):
        return dict(cursor.fetchall())


def test_replace_edit_delete_and_final_empty_snapshot(tmp_path):
    pipeline = pipeline_at(tmp_path)
    api = FakeAPI([conversation("a"), conversation("b")])
    pipeline.run(otter_source(client=api))
    initial = read_rows(pipeline)
    assert set(initial) == {"a", "b"}
    pipeline.run(otter_source(client=api))
    assert read_rows(pipeline) == initial
    api.records[0]["relationships"]["transcript"]["content"] = "Sam  00:01\nCorrected."
    api.records.pop()
    pipeline.run(otter_source(client=api))
    assert set(read_rows(pipeline)) == {"a"}
    assert "Corrected." in read_rows(pipeline)["a"]
    api.records.clear()
    pipeline.run(otter_source(client=api))
    assert read_rows(pipeline) == {}


def test_failed_replacement_preserves_destination_and_source_state(tmp_path):
    pipeline = pipeline_at(tmp_path)
    api = FakeAPI([conversation("a"), conversation("b")])
    pipeline.run(otter_source(client=api))
    initial_rows = read_rows(pipeline)
    initial_state = deepcopy(pipeline.state.get("sources", {}))
    api.errors["b"] = OtterHTTPError(503)
    with pytest.raises(PipelineStepFailed):
        pipeline.run(otter_source(client=api))
    assert read_rows(pipeline) == initial_rows
    assert pipeline.state.get("sources", {}) == initial_state


def test_real_transport_to_destination_and_failed_second_page(tmp_path):
    broken = False

    def handler(request):
        if request.url.path.endswith("/conversations/a"):
            return httpx.Response(200, json={"data": conversation("a")})
        if request.url.params.get("cursor"):
            return httpx.Response(403)
        return httpx.Response(
            200,
            json={
                "data": [{"id": "a"}],
                "meta": {"has_more": broken, "next_cursor": "next" if broken else None},
            },
        )

    api = OtterClient("secret", transport=httpx.MockTransport(handler), sleep=lambda _: None)
    pipeline = pipeline_at(tmp_path)
    try:
        pipeline.run(otter_source(client=api))
        initial = read_rows(pipeline)
        assert "Ship on Friday" in initial["a"]
        broken = True
        with pytest.raises(PipelineStepFailed):
            pipeline.run(otter_source(client=api))
        assert read_rows(pipeline) == initial
    finally:
        api.close()


def test_optional_null_relationships_and_unknown_speaker_are_preserved():
    data = conversation()
    for field in ("action_items", "insights", "outline"):
        data["relationships"][field] = None
    row = conversation_row(data)
    assert "Speaker 2  00:07" in row["content"]
    assert "## Action items" not in row["content"]


def test_unknown_action_status_is_not_rendered_as_incomplete():
    data = conversation()
    data["relationships"]["action_items"][0]["status"] = None
    row = conversation_row(data)
    assert "- Write the guide (Assignee: Sam)" in row["content"]
    assert "- [ ]" not in row["content"]
