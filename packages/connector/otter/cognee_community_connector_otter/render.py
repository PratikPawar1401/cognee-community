"""Render the documented API fields deterministically, preserving transcript text."""

from .client import OtterError, checked_id


def _text(value):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise OtterError("Otter returned a non-text value in a text field.")
    return value


def _object(value):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise OtterError("Otter returned an invalid object field.")
    return value


def _list(value):
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise OtterError("Otter returned an invalid object list.")
    return value


def conversation_row(data):
    conversation_id = checked_id(data)
    relationships = _object(data.get("relationships"))
    for key in ("transcript", "action_items", "insights", "outline"):
        if key not in relationships:
            raise OtterError(f"Otter omitted the requested {key} relationship.")
    transcript = relationships["transcript"]
    if transcript is None:
        raise OtterError("Otter transcript is unavailable; retry after processing completes.")
    transcript = _object(transcript)
    if transcript.get("format") != "txt" or not isinstance(transcript.get("content"), str):
        raise OtterError("Otter transcript must contain text in the documented txt format.")
    for status in _object(data.get("process_status")).values():
        if status is not None and status != "finished":
            raise OtterError("Otter generated content is not finished; retry this snapshot later.")

    lines = [f"Conversation ID: {conversation_id}"]
    created = _text(data.get("created_at"))
    if created:
        lines.append(f"Created: {created}")
    owner = _object(data.get("owner"))
    owner_label = _text(owner.get("name")) or _text(owner.get("id"))
    if owner_label:
        lines.append(f"Owner: {owner_label}")
    guests = sorted(
        {_text(guest.get("name")) for guest in _list(data.get("calendar_guests"))} - {""}
    )
    if guests:
        lines.append("Participants: " + ", ".join(guests))
    channels = set()
    for shared in _list(data.get("shared_channels")):
        channel = _object(shared.get("channel"))
        channels.add(f"{_text(channel.get('name'))} ({checked_id(channel)})")
    if channels:
        lines.append("Channels: " + ", ".join(sorted(channels)))
    summary = _text(data.get("abstract_summary"))
    if summary:
        lines.extend(["", "## Summary", summary])
    lines.extend(["", "## Transcript", transcript["content"]])

    actions = []
    for action in _list(relationships["action_items"]):
        status = _object(action.get("status"))
        completed = status.get("completed")
        if completed is not None and type(completed) is not bool:
            raise OtterError("Otter action completion must be boolean or null.")
        assignee = _object(action.get("assignee"))
        name = _text(assignee.get("name")) or _text(assignee.get("id"))
        marker = "- " if completed is None else ("- [x] " if completed else "- [ ] ")
        suffix = f" (Assignee: {name})" if name else ""
        actions.append(f"{marker}{_text(action.get('text'))}{suffix}")
    if actions:
        lines.extend(["", "## Action items", *sorted(actions)])
    for key, heading, label in (
        ("insights", "Insights", "topic"),
        ("outline", "Outline", "section"),
    ):
        blocks = []
        for item in _list(relationships[key]):
            texts = item.get("text")
            if not isinstance(texts, list) or any(not isinstance(text, str) for text in texts):
                raise OtterError(f"Otter {key} must contain a list of text strings.")
            blocks.append("\n".join([f"### {_text(item.get(label))}", *texts]))
        if blocks:
            lines.extend(["", f"## {heading}", *(sorted(blocks) if key == "insights" else blocks)])
    # No retrieved_at, response metadata, or processing timestamps: unchanged rows
    # retain cognee's content-derived identity even when the retrieval time changes.
    return {
        "id": conversation_id,
        "title": _text(data.get("title")),
        "url": _text(data.get("url")),
        "content": "\n".join(lines),
    }
