import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from instagram_apify import fetch_latest_public_post as fetch_latest_apify_post
from instagram_browser import fetch_latest_public_post
from instagram_client import Post
from instagram_public import PublicProfileError

DEFAULT_CONFIG_NAME = "config.json"
DEFAULT_STATE_NAME = ".instagram_state.json"
KST = ZoneInfo("Asia/Seoul")
NOTIFICATION_START_HOUR_KST = 10
NOTIFICATION_START_MINUTE_KST = 30
NOTIFICATION_END_HOUR_KST = 11
NOTIFICATION_END_MINUTE_KST = 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check a public Instagram profile and post new updates to Slack."
    )
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG_NAME,
        help=f"Path to config JSON. Defaults to {DEFAULT_CONFIG_NAME}.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and print the latest detected post without sending to Slack.",
    )
    parser.add_argument(
        "--force-notify",
        action="store_true",
        help="Send the latest post to Slack even if it is not new.",
    )
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def print_json(payload: dict[str, Any]) -> None:
    try:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    except UnicodeEncodeError:
        print(json.dumps(payload, ensure_ascii=True, indent=2))


def now_kst() -> datetime:
    return datetime.now(KST)


def is_automatic_notification_window(current_kst: datetime) -> bool:
    current_time = (current_kst.hour, current_kst.minute)
    start_time = (NOTIFICATION_START_HOUR_KST, NOTIFICATION_START_MINUTE_KST)
    end_time = (NOTIFICATION_END_HOUR_KST, NOTIFICATION_END_MINUTE_KST)
    return start_time <= current_time <= end_time


def format_timestamp(timestamp: int, precision: str = "second") -> str:
    if precision == "day":
        return datetime.fromtimestamp(timestamp, tz=UTC).strftime("%Y-%m-%d (publication date)")
    dt = datetime.fromtimestamp(timestamp, tz=UTC).astimezone(KST)
    return dt.strftime("%Y-%m-%d %H:%M:%S KST")


def build_slack_payload(
    profile_url: str,
    username: str,
    post: Post,
    force_notify: bool = False,
) -> dict[str, Any]:
    summary = "Instagram notifier test" if force_notify else "Instagram update detected"
    text = f"{summary}: @{username} {post.permalink}"

    return {
        "text": text,
        "blocks": [
            {
                "type": "image",
                "image_url": post.display_url,
                "alt_text": f"{username} Instagram post",
            },
        ],
    }


def post_to_slack(webhook_url: str, payload: dict[str, Any]) -> None:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        result = response.read().decode("utf-8").strip()
        if result != "ok":
            raise RuntimeError(f"Slack webhook returned unexpected response: {result}")


def resolve_path(base_dir: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else base_dir / path


def load_state(state_path: Path) -> dict[str, Any]:
    if state_path.exists():
        return load_json(state_path)
    return {}


def load_previous_post_id(state: dict[str, Any]) -> str | None:
    previous_post_id = state.get("last_notified_post_id")
    if previous_post_id is None:
        previous_post_id = state.get("last_seen_post_id")
    return str(previous_post_id) if previous_post_id is not None else None


def load_last_automated_notification_date(state: dict[str, Any]) -> str | None:
    value = state.get("last_automated_notification_date")
    return str(value) if value else None


def save_state(
    state_path: Path,
    state: dict[str, Any],
    *,
    post: Post | None = None,
    automated_notification_date: str | None = None,
) -> None:
    next_state = dict(state)

    if post is not None:
        next_state["last_notified_post_id"] = post.post_id
        next_state["last_notified_shortcode"] = post.shortcode
        next_state["last_notified_timestamp"] = post.timestamp
        next_state["last_seen_post_id"] = post.post_id
        next_state["last_seen_shortcode"] = post.shortcode
        next_state["last_seen_timestamp"] = post.timestamp
        next_state["last_notified_timestamp_precision"] = post.timestamp_precision

    if automated_notification_date is not None:
        next_state["last_automated_notification_date"] = automated_notification_date

    save_json(
        state_path,
        next_state,
    )


def main() -> int:
    args = parse_args()
    config_path = Path(args.config).resolve()

    if not config_path.exists():
        print(
            f"Config file not found: {config_path}\n"
            f"Copy config.example.json to {config_path.name} and fill it in first.",
            file=sys.stderr,
        )
        return 1

    config = load_json(config_path)
    base_dir = config_path.parent
    state_path = resolve_path(base_dir, config.get("state_file", DEFAULT_STATE_NAME))
    notify_on_first_run = bool(config.get("notify_on_first_run", False))
    username = config["instagram_username"]
    profile_url = config["instagram_profile_url"]
    webhook_url = config.get("slack_webhook_url", "")

    state = load_state(state_path) if not args.dry_run else {}
    previous_post_id = load_previous_post_id(state)
    current_kst = now_kst()
    if not args.dry_run and not args.force_notify:
        if not is_automatic_notification_window(current_kst):
            print(
                "Outside the 10:30-11:00 KST notification window; no Instagram request made. "
                f"Current Asia/Seoul time: {current_kst.strftime('%Y-%m-%d %H:%M:%S KST')}"
            )
            return 0
        if load_last_automated_notification_date(state) == current_kst.date().isoformat():
            print("Today's automatic notification was already sent; no Instagram request made.")
            return 0

    try:
        match config.get("instagram_source", "browser"):
            case "browser":
                post = fetch_latest_public_post(username)
            case "apify":
                post = fetch_latest_apify_post(username)
            case _:
                raise PublicProfileError("Unknown instagram_source; expected browser or apify.")
    except (KeyError, urllib.error.URLError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"Failed to fetch Instagram profile: {exc}", file=sys.stderr)
        return 1

    print_json(
        {
            "latest_post_id": post.post_id,
            "shortcode": post.shortcode,
            "permalink": post.permalink,
            "timestamp": post.timestamp,
            "formatted_time": format_timestamp(post.timestamp, post.timestamp_precision),
            "timestamp_precision": post.timestamp_precision,
            "is_pinned": post.is_pinned,
            "caption": post.caption,
        }
    )

    if args.dry_run:
        return 0

    current_kst = now_kst()
    current_kst_date = current_kst.date().isoformat()
    if args.force_notify:
        if not webhook_url:
            print("Missing slack_webhook_url in config.json.", file=sys.stderr)
            return 1

        try:
            payload = build_slack_payload(
                profile_url=profile_url,
                username=username,
                post=post,
                force_notify=True,
            )
            post_to_slack(webhook_url, payload)
        except (urllib.error.URLError, RuntimeError) as exc:
            print(f"Failed to post to Slack: {exc}", file=sys.stderr)
            return 1

        print("Manual test notification sent to Slack. Automated state was not changed.")
        return 0

    if not is_automatic_notification_window(current_kst):
        print(
            "Outside the 10:30-11:00 KST notification window. "
            f"Current Asia/Seoul time: {current_kst.strftime('%Y-%m-%d %H:%M:%S KST')}"
        )
        return 0

    previous_shortcode = state.get("last_notified_shortcode", state.get("last_seen_shortcode"))
    if previous_post_id == post.post_id or previous_shortcode == post.shortcode:
        print("No new Instagram post found. Later scheduled checks can still send an update.")
        return 0

    previous_timestamp = state.get("last_notified_timestamp", state.get("last_seen_timestamp"))
    if previous_timestamp is not None:
        previous_precision = state.get("last_notified_timestamp_precision", "second")
        if (
            type(previous_timestamp) is not int
            or post.timestamp <= previous_timestamp
            or (previous_precision == "day" and post.timestamp < previous_timestamp + 86400)
        ):
            print(
                "MATCHING FAILED: candidate is not provably newer than the previous delivery; "
                "Slack and state unchanged.",
                file=sys.stderr,
            )
            return 1

    if previous_post_id is None and not notify_on_first_run:
        save_state(
            state_path,
            state,
            post=post,
        )
        print(
            "First scheduled 10:30-11:00 KST run detected. State saved without sending a Slack message."
        )
        return 0

    if not webhook_url:
        print("Missing slack_webhook_url in config.json.", file=sys.stderr)
        return 1

    try:
        payload = build_slack_payload(
            profile_url=profile_url,
            username=username,
            post=post,
        )
        post_to_slack(webhook_url, payload)
    except (urllib.error.URLError, RuntimeError) as exc:
        print(f"Failed to post to Slack: {exc}", file=sys.stderr)
        return 1

    save_state(
        state_path,
        state,
        post=post,
        automated_notification_date=current_kst_date,
    )
    print("Latest Instagram post was sent for today's 10:30-11:00 KST check.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
