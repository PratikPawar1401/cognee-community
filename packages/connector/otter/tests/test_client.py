from datetime import UTC

import httpx
import pytest

from cognee_community_connector_otter.client import OtterClient, OtterError, retry_delay


def page(ids, more=False, cursor=None):
    return {
        "data": [{"id": value} for value in ids],
        "meta": {"has_more": more, "next_cursor": cursor},
    }


def test_auth_pagination_selection_and_detail():
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer secret"
        assert request.url.host == "api.otter.ai"
        if request.url.path.endswith("/conversations/a"):
            assert request.url.params["include"] == "transcript,action_items,insights,outline"
            return httpx.Response(200, json={"data": {"id": "a"}})
        assert request.url.params["channel_id"] == "team"
        assert request.url.params["limit"] == "100"
        if "cursor" not in request.url.params:
            return httpx.Response(200, json=page(["a"], True, "next"))
        assert request.url.params["cursor"] == "next"
        return httpx.Response(200, json=page(["b"]))

    api = OtterClient("secret", transport=httpx.MockTransport(handler), sleep=lambda _: None)
    try:
        assert list(api.list_conversations(channel_id="team")) == ["a", "b"]
        assert api.get_conversation("a") == {"id": "a"}
        assert len(calls) == 3
    finally:
        api.close()


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"data": [], "meta": {}},
        {"data": {}, "meta": {"has_more": False}},
        page(["a"], True, None),
        page([], True, "next"),
        {"data": [{"title": "missing ID"}], "meta": {"has_more": False}},
        {"data": [], "meta": {"has_more": "false"}},
        page(["a", "a"]),
    ],
)
def test_malformed_page_aborts(bad):
    api = OtterClient(
        "secret",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=bad)),
        sleep=lambda _: None,
    )
    try:
        with pytest.raises(OtterError):
            list(api.list_conversations())
    finally:
        api.close()


def test_repeated_cursor_aborts():
    counter = 0

    def handler(_request):
        nonlocal counter
        counter += 1
        return httpx.Response(200, json=page([str(counter)], True, "same"))

    api = OtterClient("secret", transport=httpx.MockTransport(handler), sleep=lambda _: None)
    try:
        with pytest.raises(OtterError, match="cursor"):
            list(api.list_conversations())
        assert counter == 2
    finally:
        api.close()


@pytest.mark.parametrize(
    "status,expected_calls", [(401, 1), (403, 1), (404, 1), (302, 1), (429, 5), (503, 5)]
)
def test_http_errors_are_bounded_and_do_not_expose_response(status, expected_calls):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status,
            text="private transcript and secret",
            headers={"Location": "https://untrusted.example/", "Retry-After": "0"},
        )

    api = OtterClient("secret", transport=httpx.MockTransport(handler), sleep=lambda _: None)
    try:
        with pytest.raises(OtterError) as exc:
            list(api.list_conversations())
        assert "secret" not in str(exc.value)
        assert "transcript" not in str(exc.value)
        assert len(calls) == expected_calls
    finally:
        api.close()


def test_rate_limit_retries_then_succeeds():
    waits = []
    calls = []

    def handler(request):
        calls.append(request)
        return (
            httpx.Response(429, headers={"Retry-After": "3"})
            if len(calls) == 1
            else httpx.Response(200, json=page([]))
        )

    api = OtterClient("secret", transport=httpx.MockTransport(handler), sleep=waits.append)
    try:
        assert list(api.list_conversations()) == []
        assert 3 in waits
        assert len(calls) == 2
    finally:
        api.close()


@pytest.mark.parametrize("value", [None, "NaN", "inf", "-1", "broken"])
def test_invalid_retry_after_uses_finite_backoff(value):
    assert retry_delay(value, 2) == 4


def test_large_retry_after_aborts_instead_of_retrying_early():
    with pytest.raises(OtterError):
        retry_delay("3600", 0)


def test_http_date_retry_after():
    from datetime import datetime, timedelta
    from email.utils import format_datetime

    future = datetime.now(UTC) + timedelta(seconds=30)
    assert 28 <= retry_delay(format_datetime(future), 0) <= 30


def test_transport_failure_exhausts_budget_without_echoing_error():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("secret response contents")

    api = OtterClient("secret", transport=httpx.MockTransport(handler), sleep=lambda _: None)
    try:
        with pytest.raises(OtterError, match="connection failed") as exc:
            list(api.list_conversations())
        assert len(calls) == 5
        assert "secret" not in str(exc.value)
    finally:
        api.close()


def test_invalid_json_fails_without_echoing_body():
    api = OtterClient(
        "secret",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text="secret invalid body")),
        sleep=lambda _: None,
    )
    try:
        with pytest.raises(OtterError, match="invalid JSON") as exc:
            list(api.list_conversations())
        assert "secret" not in str(exc.value)
    finally:
        api.close()


@pytest.mark.parametrize("payload", [[], {"data": {"id": "wrong"}}, {"data": None}])
def test_invalid_detail_envelope_aborts(payload):
    api = OtterClient(
        "secret",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)),
        sleep=lambda _: None,
    )
    try:
        with pytest.raises(OtterError):
            api.get_conversation("a")
    finally:
        api.close()
