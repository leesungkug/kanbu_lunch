import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from instagram_client import Post

MONTHS: Final = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
PUBLICATION_PREFIX: Final = re.compile(
    r"^Photo by @?(?P<owner>[A-Za-z0-9_.]+) on "
    r"(?P<month>[A-Za-z]+) (?P<day>\d{1,2}), (?P<year>\d{4})(?:[.\s]|$)"
)


@dataclass(frozen=True, slots=True)
class PublicProfileError(RuntimeError):
    detail: str

    def __str__(self) -> str:
        return self.detail


class PublicTile(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    permalink: str
    description: str
    image_url: str


TILES: Final[TypeAdapter[tuple[PublicTile, ...]]] = TypeAdapter(tuple[PublicTile, ...])


def _parse_tile(username: str, tile: PublicTile) -> Post:
    permalink = re.fullmatch(
        rf"https://(?:www\.)?instagram\.com/(?:{re.escape(username)}/)?"
        r"p/(?P<shortcode>[A-Za-z0-9_-]+)/?",
        tile.permalink,
        flags=re.IGNORECASE,
    )
    if permalink is None:
        raise PublicProfileError("Untrusted public post permalink.")

    prefix = PUBLICATION_PREFIX.match(tile.description)
    if prefix is None:
        raise PublicProfileError("Public post publication date or owner is missing.")
    if prefix["owner"].casefold() != username.casefold():
        raise PublicProfileError("Public post owner does not match the requested account.")

    try:
        month = MONTHS.index(prefix["month"]) + 1
        publication_day = datetime(int(prefix["year"]), month, int(prefix["day"]), tzinfo=UTC)
    except ValueError as exc:
        raise PublicProfileError("Public post publication date is invalid.") from exc

    validate_image_url(tile.image_url)

    shortcode = permalink["shortcode"]
    return Post(
        post_id=shortcode,
        shortcode=shortcode,
        permalink=f"https://www.instagram.com/p/{shortcode}/",
        timestamp=int(publication_day.timestamp()),
        caption=tile.description.strip(),
        display_url=tile.image_url,
        is_pinned=None,
        timestamp_precision="day",
    )


def validate_image_url(image_url: str) -> None:
    """Reject image URLs outside Instagram HTTPS CDN hosts."""
    try:
        image = urlsplit(image_url)
        host = image.hostname or ""
        trusted_host = any(
            host == suffix or host.endswith(f".{suffix}")
            for suffix in ("cdninstagram.com", "fbcdn.net")
        )
        safe_image = (
            image.scheme == "https"
            and trusted_host
            and image.username is None
            and image.password is None
            and image.port in (None, 443)
            and not image.fragment
            and bool(image.path)
            and not any(character.isspace() or ord(character) < 32 for character in image_url)
        )
    except ValueError as exc:
        raise PublicProfileError("Public post image URL is invalid.") from exc
    if not safe_image:
        raise PublicProfileError("Public post image URL is not a trusted HTTPS Instagram CDN URL.")



def select_latest_public_post(username: str, serialized_tiles: str) -> Post:
    """Select only an unambiguous latest publication day from public profile tiles."""
    if re.fullmatch(r"[A-Za-z0-9_.]{1,30}", username) is None:
        raise PublicProfileError("Invalid Instagram username.")
    try:
        tiles = TILES.validate_json(serialized_tiles)
    except ValidationError as exc:
        raise PublicProfileError("Invalid public profile tile data.") from exc
    if not tiles:
        raise PublicProfileError("No public posts are visible; the page may require login.")

    posts: dict[str, Post] = {}
    for tile in tiles:
        post = _parse_tile(username, tile)
        previous = posts.get(post.shortcode)
        if previous is not None and previous.timestamp != post.timestamp:
            raise PublicProfileError("Repeated public post has conflicting publication dates.")
        posts[post.shortcode] = post

    latest_day = max(post.timestamp for post in posts.values())
    latest = [post for post in posts.values() if post.timestamp == latest_day]
    if len(latest) != 1:
        raise PublicProfileError("Multiple public posts share the same publication day.")
    return latest[0]
