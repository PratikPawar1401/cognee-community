"""Path B document source: complete REST snapshots with deterministic row content."""

from collections.abc import Iterable

import dlt
from cognee.tasks.ingestion.dlt_utils import DOCUMENT_SOURCE_ATTR, PIPELINE_SCOPE_ATTR

from .client import OtterClient, OtterHTTPError
from .render import conversation_row

OTTER_TABLE_NAME = "otter_conversations"


def _ids(values, name):
    if values is None:
        return None
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        raise ValueError(f"{name} must be a collection of non-empty strings.")
    values = list(values)
    if not values or any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError(f"{name} must contain at least one non-empty ID.")
    return sorted({value.strip() for value in values})


def snapshot(client, *, conversation_ids=None, channel_ids=None, include_shared=False):
    """Return rows only after every request and render succeeds."""
    selected = conversation_ids
    if selected is None:
        selected = sorted(
            {
                conversation_id
                for channel in channel_ids or [None]
                for conversation_id in client.list_conversations(
                    channel_id=channel, include_shared=include_shared
                )
            }
        )
    rows = []
    for conversation_id in selected:
        try:
            data = client.get_conversation(conversation_id)
        except OtterHTTPError as exc:
            # Only an explicit ID lookup can interpret the documented "not found"
            # response as absence. A discovered ID disappearing mid-run aborts.
            if conversation_ids is not None and exc.status_code == 404:
                continue
            raise
        rows.append(conversation_row(data))
    return rows


@dlt.source(name="otter")
def _configured_source(
    api_key: str = dlt.secrets.value,
    conversation_ids=None,
    channel_ids=None,
    include_shared=False,
    client=None,
):
    @dlt.resource(
        name=OTTER_TABLE_NAME,
        primary_key="id",
        write_disposition="replace",
        columns={name: {"data_type": "text"} for name in ("id", "title", "url", "content")},
    )
    def conversations():
        api = client if client is not None else OtterClient(api_key)
        try:
            rows = snapshot(
                api,
                conversation_ids=conversation_ids,
                channel_ids=channel_ids,
                include_shared=include_shared,
            )
            # An empty list with the explicit schema creates an empty replace
            # load, allowing cognee to reconcile removal of the final document.
            yield rows
        finally:
            if client is None:
                api.close()

    setattr(conversations, DOCUMENT_SOURCE_ATTR, "otter")
    setattr(conversations, PIPELINE_SCOPE_ATTR, "otter")
    return conversations


def otter_source(
    api_key=None,
    *,
    conversation_ids=None,
    channel_ids=None,
    include_shared=False,
    client=None,
):
    """Build an Otter document source for ``cognee.add`` / ``cognee.remember``.

    Omit api_key to resolve ``sources.otter.api_key`` through dlt secrets.
    Explicit conversation IDs and channel IDs are mutually exclusive. Channels
    include shared conversations automatically, as specified by Otter's API.
    ``client`` is an injectable list/detail client used by offline tests.
    """
    selected = _ids(conversation_ids, "conversation_ids")
    channels = _ids(channel_ids, "channel_ids")
    if selected is not None and channels is not None:
        raise ValueError("Choose conversation_ids or channel_ids, not both.")
    if type(include_shared) is not bool:
        raise ValueError("include_shared must be a boolean.")
    kwargs = {
        "conversation_ids": selected,
        "channel_ids": channels,
        "include_shared": include_shared,
        "client": client,
    }
    if api_key is not None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("api_key must be a non-empty string.")
        kwargs["api_key"] = api_key
    elif client is not None:
        kwargs["api_key"] = "injected-client"
    source = _configured_source(**kwargs)
    setattr(source, DOCUMENT_SOURCE_ATTR, "otter")
    setattr(source, PIPELINE_SCOPE_ATTR, "otter")
    return source
