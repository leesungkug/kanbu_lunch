import json
from io import BytesIO
from unittest.mock import patch

import pytest

from instagram_apify import fetch_latest_public_post, select_latest_apify_post
from instagram_public import PublicProfileError
from tests.test_apify import item


def blocked_response() -> bytes:
    return json.dumps(
        [
            {
                "url": "https://www.instagram.com/lunch11_14/",
                "inputUrl": "https://www.instagram.com/lunch11_14/",
                "error": "no_items",
                "errorDescription": "Empty or private data for provided input",
                "requestErrorMessages": [
                    "Error: Request got blocked. Will retry with different session"
                ]
                * 11,
            }
        ]
    ).encode()


def profile_response(**changes: str | bool | list[dict[str, str]]) -> bytes:
    profile = {
        "username": "lunch11_14",
        "url": "https://www.instagram.com/lunch11_14",
        "inputUrl": "https://www.instagram.com/lunch11_14/",
        "private": False,
        "latestPosts": [item("Pinned", "2025-07-15T06:19:57Z"), item()],
    } | changes
    return json.dumps([profile]).encode()


def test_reports_confirmed_block_instead_of_invalid_post_data() -> None:
    with pytest.raises(PublicProfileError, match="Instagram blocked Apify"):
        select_latest_apify_post("lunch11_14", blocked_response())


def test_fetches_latest_post_from_profile_details(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APIFY_TOKEN", "test-token")
    with patch("urllib.request.urlopen", return_value=BytesIO(profile_response())) as network:
        post = fetch_latest_public_post("lunch11_14")
        assert post.shortcode == "Latest"
        network.assert_called_once()
        request = network.call_args.args[0]
        assert json.loads(request.data)["resultsType"] == "details"
        assert json.loads(request.data)["resultsLimit"] == 1
        assert "maxTotalChargeUsd=0.04" in request.full_url


@pytest.mark.parametrize(
    "changes",
    [
        {"username": "other"},
        {"private": True},
        {"latestPosts": []},
        {"url": "https://www.instagram.com/other"},
        {"inputUrl": "https://www.instagram.com/other/"},
        {"latestPosts": [item() | {"ownerUsername": "other"}]},
        {"latestPosts": [item() | {"displayUrl": "https://evil.test/menu.jpg"}]},
    ],
)
def test_rejects_untrusted_profile_results(monkeypatch, changes):
    monkeypatch.setenv("APIFY_TOKEN", "test-token")
    with patch(
        "urllib.request.urlopen", return_value=BytesIO(profile_response(**changes))
    ) as network:
        with pytest.raises(PublicProfileError):
            fetch_latest_public_post("lunch11_14")
        network.assert_called_once()


@pytest.mark.parametrize(
    "body",
    [
        blocked_response(),
        b"[]",
        b"{}",
        b"not json",
        b'[{"error":"other","errorDescription":"secret-token"}]',
        json.dumps([item()]).encode(),
    ],
)
def test_failed_collection_does_not_retry_or_expose_payload(monkeypatch, body):
    monkeypatch.setenv("APIFY_TOKEN", "test-token")
    with patch("urllib.request.urlopen", return_value=BytesIO(body)) as network:
        with pytest.raises(PublicProfileError) as caught:
            fetch_latest_public_post("lunch11_14")
        assert "secret-token" not in str(caught.value)
        network.assert_called_once()
