"""Read-only Otter Public API transport. No transcript or credential logging."""

import math
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import httpx


class OtterError(RuntimeError):
    """An API failure or incomplete response makes this snapshot unusable."""


class OtterHTTPError(OtterError):
    def __init__(self, status_code):
        self.status_code = status_code
        super().__init__(f"Otter API returned HTTP {status_code}; snapshot aborted.")


def retry_delay(value, attempt):
    """Honor numeric/HTTP-date Retry-After; reject excessive waits rather than retry early."""
    try:
        delay = float(value)
    except (TypeError, ValueError):
        try:
            delay = (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            delay = 2**attempt
    if not math.isfinite(delay) or delay < 0:
        delay = 2**attempt
    if delay > 120:
        raise OtterError("Otter requested a retry delay over 120 seconds; retry the sync later.")
    return delay


class OtterClient:
    """One connection per snapshot, paced below the documented 10 requests/second limit."""

    def __init__(self, api_key, *, transport=None, sleep=time.sleep, clock=time.monotonic):
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("An Otter Enterprise API key is required.")
        self._http = httpx.Client(
            base_url="https://api.otter.ai/v1/",
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            timeout=30,
            follow_redirects=False,
            transport=transport,
        )
        self._sleep = sleep
        self._clock = clock
        self._last_request = None

    def close(self):
        self._http.close()

    def _get(self, path, params):
        for attempt in range(5):
            if self._last_request is not None:
                self._sleep(max(0, 0.11 - (self._clock() - self._last_request)))
            self._last_request = self._clock()
            try:
                response = self._http.get(path, params=params)
            except httpx.TransportError:
                if attempt == 4:
                    raise OtterError("Otter connection failed; snapshot aborted.") from None
                self._sleep(2**attempt)
                continue
            if (response.status_code == 429 or 500 <= response.status_code < 600) and attempt < 4:
                self._sleep(retry_delay(response.headers.get("Retry-After"), attempt))
                continue
            if response.status_code != 200:
                raise OtterHTTPError(response.status_code)
            try:
                payload = response.json()
            except ValueError:
                raise OtterError("Otter returned invalid JSON; snapshot aborted.") from None
            if not isinstance(payload, dict):
                raise OtterError("Otter returned an invalid response envelope.")
            return payload
        raise OtterError("Otter retry budget exhausted.")

    def list_conversations(self, *, channel_id=None, include_shared=False):
        params = {"limit": 100, "include_shared": str(include_shared).lower()}
        if channel_id is not None:
            params["channel_id"] = channel_id
        cursors = set()
        ids = set()
        while True:
            payload = self._get("conversations", params)
            data, meta = payload.get("data"), payload.get("meta")
            if not isinstance(data, list) or not isinstance(meta, dict):
                raise OtterError("Otter conversation page has invalid data or metadata.")
            if type(meta.get("has_more")) is not bool:
                raise OtterError("Otter conversation page is missing a boolean has_more.")
            for item in data:
                conversation_id = checked_id(item)
                if conversation_id in ids:
                    raise OtterError(
                        "Otter repeated a conversation within one paginated inventory."
                    )
                ids.add(conversation_id)
                yield conversation_id
            if not meta["has_more"]:
                return
            cursor = meta.get("next_cursor")
            if not data or not isinstance(cursor, str) or not cursor or cursor in cursors:
                raise OtterError("Otter pagination is incomplete or repeated a cursor.")
            cursors.add(cursor)
            params["cursor"] = cursor

    def get_conversation(self, conversation_id):
        payload = self._get(
            f"conversations/{quote(conversation_id, safe='')}",
            {"include": "transcript,action_items,insights,outline"},
        )
        data = payload.get("data")
        if checked_id(data) != conversation_id:
            raise OtterError("Otter detail ID does not match the requested conversation.")
        return data


def checked_id(item):
    if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
        raise OtterError("Otter returned a record without a non-empty string ID.")
    return item["id"]
