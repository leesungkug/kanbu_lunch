import json
from unittest.mock import patch

import pytest

from instagram_apify import fetch_latest_public_post, select_latest_apify_post
from instagram_public import PublicProfileError


def item(code="Latest", time="2026-09-14T06:43:24.000Z"):
    return dict(
        id="123",
        shortCode=code,
        timestamp=time,
        ownerUsername="lunch11_14",
        displayUrl="https://scontent.cdninstagram.com/menu.jpg",
        url=f"https://www.instagram.com/p/{code}/",
        caption="Lunch",
    )


def test_selects_latest_time_instead_of_pinned_order():
    body = json.dumps([item("Pinned", "2025-07-14T00:00:00Z"), item()]).encode()
    post = select_latest_apify_post("lunch11_14", body)
    assert (post.shortcode, post.timestamp, post.timestamp_precision) == (
        "Latest",
        1789368204,
        "second",
    )


@pytest.mark.parametrize(
    "change",
    [
        {"ownerUsername": "other"},
        {"timestamp": "2026-09-14"},
        {"timestamp": "2099-01-01T00:00:00Z"},
        {"timestamp": "bad"},
        {"displayUrl": "https://evil.test/menu.jpg"},
        {"shortCode": "../evil"},
        {"url": "https://www.instagram.com/p/Other/"},
        {"id": None},
    ],
)
def test_invalid_results_fail_closed(change):
    post = item() | change
    with pytest.raises(PublicProfileError):
        select_latest_apify_post("lunch11_14", json.dumps([post]).encode())


@pytest.mark.parametrize(
    "body",
    [
        b"[]",
        b"{}",
        b"not json",
        json.dumps([item(), item()]).encode(),
        json.dumps([item(), item("Tie")]).encode(),
        b'[{"error":"blocked"}]',
    ],
)
def test_empty_error_duplicate_or_ambiguous_feed_fails(body):
    with pytest.raises(PublicProfileError):
        select_latest_apify_post("lunch11_14", body)


def test_no_secret_fails_before_network(monkeypatch):
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    with patch("urllib.request.urlopen") as network:
        with pytest.raises(PublicProfileError, match="Missing APIFY_TOKEN"):
            fetch_latest_public_post("lunch11_14")
        network.assert_not_called()


def test_request_has_bounded_cost_no_token_in_url_and_no_retry(monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "test-token")
    with patch("urllib.request.urlopen", side_effect=TimeoutError) as network:
        with pytest.raises(PublicProfileError, match="no retry"):
            fetch_latest_public_post("lunch11_14")
        network.assert_called_once()
        request = network.call_args.args[0]
        assert "maxTotalChargeUsd=0.04" in request.full_url
        assert "timeout=120" in request.full_url
        assert "test-token" not in request.full_url
        assert request.get_header("Authorization") == "Bearer test-token"
        assert json.loads(request.data)["resultsLimit"] == 12


def test_regional_cdn_normalization_preserves_exact_signed_resource():
    post = item() | {
        "displayUrl": "https://instagram.fcps4-2.fna.fbcdn.net/v/menu.heic?stp=dst-jpg&oh=signed"
    }
    selected = select_latest_apify_post("lunch11_14", json.dumps([post]).encode())
    assert (
        selected.display_url
        == "https://scontent.cdninstagram.com/v/menu.heic?stp=dst-jpg&oh=signed"
    )


def test_http_error_does_not_expose_response_url_or_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from email.message import Message
    from urllib.error import HTTPError

    # Given a server error whose diagnostic text contains a credential.
    monkeypatch.setenv("APIFY_TOKEN", "private-token")
    failure = HTTPError(
        "https://example.invalid/private-token", 403, "private-token", Message(), None
    )
    with patch("urllib.request.urlopen", side_effect=failure) as network:
        # When the cloud adapter runs once.
        with pytest.raises(PublicProfileError) as caught:
            fetch_latest_public_post("lunch11_14")
        # Then only status and safe context are exposed, without retry.
        assert str(caught.value) == "Apify HTTP 403; no retry or fallback attempted."
        network.assert_called_once()
        assert network.call_args.kwargs["timeout"] == 150
