#!/usr/bin/env python3
"""Manually sync the three newest limgeek.com RSS posts into one README block.

Only the text between LIMGEEK-RSS markers is ever replaced. No extra packages.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import escape
from html.parser import HTMLParser
from pathlib import Path
import re
import sys
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import xml.etree.ElementTree as ET

FEED_URL = "https://limgeek.com/rss.xml"
README_PATH = Path(__file__).resolve().parents[1] / "README.md"
START = "<!-- LIMGEEK-RSS:START -->"
END = "<!-- LIMGEEK-RSS:END -->"
MAX_FEED_BYTES = 2 * 1024 * 1024
ALLOWED_HOSTS = frozenset({"limgeek.com", "www.limgeek.com"})


@dataclass(frozen=True)
class Post:
    title: str
    url: str
    summary: str
    date: datetime


class TextOnly(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_entityref(self, name: str) -> None:
        self.parts.append(f"&{name};")


def plain_text(value: str, limit: int = 180) -> str:
    parser = TextOnly()
    parser.feed(value)
    parser.close()
    text = re.sub(r"\s+", " ", " ".join(parser.parts)).strip()
    # Remove invisible control characters to keep the README readable.
    text = "".join(ch for ch in text if ch.isprintable())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def validate_article_url(url: str) -> str:
    parsed = urlsplit(urljoin(FEED_URL, url.strip()))
    if (
        parsed.scheme != "https"
        or parsed.hostname not in ALLOWED_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or not parsed.path.startswith("/posts/")
        or any(ch in url for ch in "\r\n\x00")
    ):
        raise ValueError(f"RSS contains an invalid or off-site article link: {url!r}")
    return parsed.geturl()


def parse_feed(xml_bytes: bytes) -> list[Post]:
    if len(xml_bytes) > MAX_FEED_BYTES:
        raise ValueError("RSS feed is too large")
    if re.search(rb"<!\s*(?:DOCTYPE|ENTITY)\b", xml_bytes, flags=re.I):
        raise ValueError("XML DTD/entities are not allowed")
    root = ET.fromstring(xml_bytes)
    if root.tag != "rss" or root.find("channel") is None:
        raise ValueError("Expected an RSS 2.0 feed with channel")

    posts: list[Post] = []
    for item in root.findall("./channel/item"):
        title = plain_text(item.findtext("title") or "", limit=240)
        link = (item.findtext("link") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        description = plain_text(item.findtext("description") or "")
        if not title or not link or not pub_date:
            raise ValueError("RSS item is missing title, link, or pubDate")
        date = parsedate_to_datetime(pub_date)
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        posts.append(Post(title, validate_article_url(link), description, date))

    if not posts:
        raise ValueError("No RSS articles found: refusing to empty README")
    # Keep source order stable when dates are identical.
    return sorted(posts, key=lambda post: post.date, reverse=True)[:3]


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        if target.scheme != "https" or target.hostname not in ALLOWED_HOSTS:
            raise ValueError("RSS fetch redirected outside the approved website")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_feed() -> bytes:
    opener = build_opener(SafeRedirect())
    request = Request(
        FEED_URL,
        headers={
            "User-Agent": "Limzeuk-README-RSS-sync/1.0 (+https://github.com/Limzeuk/Limzeuk)",
            "Accept": "application/rss+xml, application/xml, text/xml;q=0.9",
        },
    )
    with opener.open(request, timeout=10) as response:
        if urlsplit(response.url).hostname not in ALLOWED_HOSTS:
            raise ValueError("Unexpected final RSS host")
        data = response.read(MAX_FEED_BYTES + 1)
    if len(data) > MAX_FEED_BYTES:
        raise ValueError("RSS feed exceeds 2 MiB")
    return data


def render_posts(posts: list[Post]) -> str:
    lines = ['<table width="100%">']
    for post in posts:
        date = post.date.astimezone(timezone.utc).strftime("%Y.%m.%d")
        lines.extend(
            [
                "  <tr>",
                '    <td valign="top">',
                f"      <sub><code>{date}</code></sub>",
                f'      <h3><a href="{escape(post.url, quote=True)}">{escape(post.title)}</a></h3>',
                f"      <p>{escape(post.summary) if post.summary else 'Read the full article ↗'}</p>",
                "    </td>",
                "  </tr>",
            ]
        )
    lines.append("</table>")
    return "\n".join(lines)


def updated_readme(before: str, posts: list[Post]) -> str:
    if before.count(START) != 1 or before.count(END) != 1:
        raise ValueError("Expected exactly one start and one end README marker")
    start = before.index(START) + len(START)
    end = before.index(END)
    if start > end:
        raise ValueError("README markers are in the wrong order")
    return before[:start] + "\n" + render_posts(posts) + "\n" + before[end:]


def main() -> int:
    try:
        # Download and validate BEFORE touching README.
        posts = parse_feed(fetch_feed())
        original = README_PATH.read_text(encoding="utf-8")
        updated = updated_readme(original, posts)
        if updated == original:
            print("README already contains the latest posts. Nothing to commit.")
            return 0
        README_PATH.write_text(updated, encoding="utf-8")
        print(f"Updated README with {len(posts)} RSS article(s).")
        return 0
    except (OSError, ValueError, ET.ParseError, TypeError) as exc:
        print(f"RSS sync failed without changing README: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
