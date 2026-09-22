import json
from collections.abc import Callable
from pathlib import Path

import pytest

import instagram_slack_notifier as app


def test_unknown_source_fails_without_fetch(
    config_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given an unsupported source.
    config = app.load_json(config_path)
    config["instagram_source"] = "unknown"
    config_path.write_text(json.dumps(config))
    # When the notifier runs.
    result = app.main()
    # Then neither source nor Slack is contacted.
    assert result == 1
    assert "Unknown instagram_source" in capsys.readouterr().err


@pytest.mark.parametrize("source", ["browser", "apify"])
def test_explicit_source_uses_only_selected_adapter(
    source: str,
    config_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provide_post: Callable[[str, int], None],
) -> None:
    # Given one configured source and an adapter returning a baseline post.
    config = app.load_json(config_path)
    config["instagram_source"] = source
    config_path.write_text(json.dumps(config))
    provide_post("post", 200)
    selected_adapter = app.fetch_latest_public_post
    calls: list[str] = []

    def fetch(username: str) -> app.Post:
        calls.append(username)
        return selected_adapter(username)

    monkeypatch.setattr(
        app, "fetch_latest_apify_post" if source == "apify" else "fetch_latest_public_post", fetch
    )
    if source == "apify":

        def unexpected_browser(username: str) -> app.Post:
            pytest.fail("Browser fallback must not run")

        monkeypatch.setattr(app, "fetch_latest_public_post", unexpected_browser)
    # When the notifier runs.
    result = app.main()
    # Then only the selected source runs exactly once.
    assert result == 0
    assert calls == ["test_lunch"]


@pytest.mark.parametrize("timestamp,precision", [(199, "second"), (200, "second"), (201, "day")])
def test_older_or_ambiguous_candidate_does_not_send_or_advance_state(
    timestamp: int,
    precision: str,
    config_path: Path,
    provide_post: Callable[[str, int], None],
    deliveries: list[str],
) -> None:
    # Given a different shortcode that is older or not provably newer.
    state_path = config_path.parent / "state.json"
    original = json.dumps(
        {
            "last_notified_post_id": "previous",
            "last_notified_timestamp": 200,
            "last_notified_timestamp_precision": precision,
        }
    )
    state_path.write_text(original)
    provide_post("candidate", timestamp)
    # When the notifier runs.
    result = app.main()
    # Then delivery and saved state remain unchanged.
    assert result == 1
    assert deliveries == []
    assert state_path.read_text() == original


def test_apify_without_token_fails_without_browser_fallback(
    config_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given an explicitly selected cloud adapter without a configured secret.
    config = app.load_json(config_path)
    config["instagram_source"] = "apify"
    config_path.write_text(json.dumps(config))
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    # When the notifier runs (the fixture rejects browser or Slack requests).
    result = app.main()
    # Then it stops instead of silently changing source or saving state.
    assert result == 1
    assert "Missing APIFY_TOKEN" in capsys.readouterr().err
    assert not (config_path.parent / "state.json").exists()
