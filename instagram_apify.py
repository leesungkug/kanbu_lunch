"""Bounded cloud retrieval of public Instagram posts; no alternate-source fallback."""

import json
import os
import re
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import Final
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from instagram_client import Post
from instagram_public import PublicProfileError, validate_image_url

RUN_URL: Final = (
    "https://api.apify.com/v2/acts/apify~instagram-scraper/run-sync-get-dataset-items"
    "?timeout=120&maxTotalChargeUsd=0.04&restartOnError=false&format=json"
)


class ScrapedPost(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    post_id: str = Field(alias="id", pattern=r"^\d+$")
    shortcode: str = Field(alias="shortCode", pattern=r"^[A-Za-z0-9_-]+$")
    timestamp: str
    owner: str = Field(alias="ownerUsername")
    image_url: str = Field(alias="displayUrl")
    url: str
    caption: str


POSTS: Final = TypeAdapter(list[ScrapedPost])


def select_latest_apify_post(username: str, body: bytes) -> Post:
    try:
        items = POSTS.validate_json(body)
    except ValidationError:
        raise PublicProfileError("MATCHING FAILED: invalid Apify post data.") from None
    if not items or len({item.shortcode for item in items}) != len(items):
        raise PublicProfileError("MATCHING FAILED: empty or duplicate Apify posts.")
    posts: list[Post] = []
    for item in items:
        if item.owner.casefold() != username.casefold():
            raise PublicProfileError("MATCHING FAILED: Apify returned another account.")
        permalink = f"https://www.instagram.com/p/{item.shortcode}/"
        if item.url != permalink:
            raise PublicProfileError("MATCHING FAILED: post URL and shortcode disagree.")
        try:
            published = datetime.fromisoformat(item.timestamp)
            if (
                published.tzinfo is None
                or not 0 < published.timestamp() <= datetime.now(UTC).timestamp() + 300
            ):
                raise ValueError
        except ValueError:
            raise PublicProfileError("MATCHING FAILED: invalid publication timestamp.") from None
        validate_image_url(item.image_url)
        # Regional CDN hosts can be IPv6-only. Preserve the signed resource exactly
        # and use Instagram's globally routed image host for Slack's image fetch.
        image_url = urlunsplit(
            urlsplit(item.image_url)._replace(netloc="scontent.cdninstagram.com")
        )
        posts.append(
            Post(
                item.post_id,
                item.shortcode,
                permalink,
                int(published.timestamp()),
                item.caption,
                image_url,
                None,
            )
        )
    latest_time = max(post.timestamp for post in posts)
    latest = [post for post in posts if post.timestamp == latest_time]
    if len(latest) != 1:
        raise PublicProfileError("MATCHING FAILED: latest publication time is ambiguous.")
    return latest[0]


def fetch_latest_public_post(username: str) -> Post:
    if re.fullmatch(r"[A-Za-z0-9_.]{1,30}", username) is None:
        raise PublicProfileError("Invalid Instagram username.")
    token = os.environ.get("APIFY_TOKEN", "").strip()
    if not token:
        raise PublicProfileError("Missing APIFY_TOKEN environment variable.")
    request = urllib.request.Request(
        RUN_URL,
        data=json.dumps(
            {
                "directUrls": [f"https://www.instagram.com/{username}/"],
                "resultsType": "posts",
                "resultsLimit": 12,
            }
        ).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=150) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        raise PublicProfileError(
            f"Apify HTTP {exc.code}; no retry or fallback attempted."
        ) from None
    except (urllib.error.URLError, TimeoutError):
        raise PublicProfileError(
            "Apify connection failed; no retry or fallback attempted."
        ) from None
    return select_latest_apify_post(username, body)
