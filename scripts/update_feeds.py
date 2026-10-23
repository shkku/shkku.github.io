#!/usr/bin/env python3
"""Build independent RSS feeds from paginated HTML listings; see feeds/README.md."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime
import json
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urldefrag, urljoin, urlsplit
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
ATOM = "http://www.w3.org/2005/Atom"
ET.register_namespace("atom", ATOM)
USER_AGENT = "HomemadeRSS/1.0 (+https://shkku.github.io/feeds/)"


@dataclass(frozen=True)
class Item:
    title: str
    url: str
    published: datetime
    categories: tuple[str, ...] = ()


def http_url(url: str) -> str:
    url = urldefrag(url)[0]
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError(f"Not an HTTP(S) URL: {url}")
    return url


def fetch(url: str) -> str:
    for attempt in range(3):
        try:
            request = Request(http_url(url), headers={"User-Agent": USER_AGENT})
            with urlopen(request, timeout=30) as response:
                if "text/html" not in response.headers.get("Content-Type", ""):
                    raise ValueError(f"Expected HTML from {url}")
                return response.read().decode("utf-8")
        except (HTTPError, URLError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)
    raise AssertionError("unreachable")


def collect(source: dict, fetch_page=fetch) -> list[Item]:
    """Treat missing cards, bad dates and incomplete pagination as errors."""
    selectors = source["selectors"]
    current = http_url(source["source_url"])
    origin = urlsplit(current)
    seen_pages: set[str] = set()
    items: dict[str, Item] = {}
    for _ in range(50):
        if current in seen_pages:
            raise ValueError(f"Pagination loop at {current}")
        seen_pages.add(current)
        soup = BeautifulSoup(fetch_page(current), "html.parser")
        cards = soup.select(selectors["item"])
        if not cards:
            raise ValueError(f"No research cards at {current}; check selectors")
        for card in cards:
            title = card.select_one(selectors["title"])
            date = card.select_one(selectors["date"])
            link = card.select_one(selectors["link"])
            if not title or not date or not link or not link.get("href"):
                raise ValueError(f"Incomplete research card at {current}")
            title_text = title.get_text(" ", strip=True)
            if not title_text:
                raise ValueError(f"Empty research title at {current}")
            published = datetime.strptime(
                date.get_text(" ", strip=True), source["date_format"]
            ).replace(tzinfo=timezone.utc)
            url = http_url(urljoin(current, link["href"]))
            categories = tuple(dict.fromkeys(
                tag.get_text(" ", strip=True)
                for tag in card.select(selectors.get("category", ":not(*)"))
                if tag.get_text(" ", strip=True)
            ))
            item = Item(title_text, url, published, categories)
            if url in items and items[url] != item:
                raise ValueError(f"Conflicting duplicate article: {url}")
            items[url] = item
        next_link = soup.select_one(selectors["next"])
        if not next_link:
            return sorted(items.values(), key=lambda i: (i.published, i.url), reverse=True)
        if not next_link.get("href"):
            raise ValueError("Pagination link has no href")
        current = http_url(urljoin(current, next_link["href"]))
        parts = urlsplit(current)
        if (parts.scheme, parts.netloc, parts.path) != (origin.scheme, origin.netloc, origin.path):
            raise ValueError(f"Pagination left the source listing: {current}")
    raise ValueError("Pagination exceeded 50 pages")


def read_items(data: bytes) -> list[Item]:
    root = ET.fromstring(data)
    if root.tag != "rss" or root.get("version") != "2.0":
        raise ValueError("Expected RSS 2.0")
    items = [Item(
        node.findtext("title") or "", http_url(node.findtext("link") or ""),
        parsedate_to_datetime(node.findtext("pubDate") or ""),
        tuple(tag.text or "" for tag in node.findall("category")),
    ) for node in root.findall("./channel/item")]
    if not items or any(not i.title or i.published.tzinfo is None for i in items):
        raise ValueError("Feed has no valid dated items")
    return items


def render(source: dict, items: list[Item], base_url: str, now: datetime) -> bytes:
    root = ET.Element("rss", version="2.0")
    channel = ET.SubElement(root, "channel")
    for key, value in {
        "title": source["title"], "link": source["source_url"],
        "description": source["description"], "language": source.get("language", "en"),
        "lastBuildDate": format_datetime(now, usegmt=True),
        "pubDate": format_datetime(max(i.published for i in items), usegmt=True),
        "ttl": "360", "generator": "HomemadeRSS",
    }.items():
        ET.SubElement(channel, key).text = value
    ET.SubElement(channel, f"{{{ATOM}}}link", {
        "href": f"{base_url.rstrip('/')}/feeds/{source['id']}.xml",
        "rel": "self", "type": "application/rss+xml",
    })
    for item in items:
        node = ET.SubElement(channel, "item")
        ET.SubElement(node, "title").text = item.title
        ET.SubElement(node, "link").text = item.url
        ET.SubElement(node, "guid", isPermaLink="true").text = item.url
        ET.SubElement(node, "pubDate").text = format_datetime(item.published, usegmt=True)
        for category in item.categories:
            ET.SubElement(node, "category").text = category
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True) + b"\n"


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def update(source: dict, base_url: str, cache_dir: Path, keep_on_error: bool,
           fetch_page=fetch) -> dict:
    feed_id = source["id"]
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", feed_id):
        raise ValueError(f"Invalid feed id: {feed_id}")
    output = ROOT / "static" / "feeds" / f"{feed_id}.xml"
    cached = cache_dir / f"{feed_id}.xml"
    status_file = cache_dir / f"{feed_id}.json"
    previous = None
    history: dict[str, Item] = {}
    for candidate in (output, cached):
        if candidate.exists():
            try:
                data = candidate.read_bytes()
                candidate_items = read_items(data)
                history.update({item.url: item for item in candidate_items})
                previous = data
            except (ValueError, ET.ParseError, TypeError):
                print(f"Ignoring invalid previous feed: {candidate}", file=sys.stderr)
    old_items = list(history.values())
    now = datetime.now(timezone.utc).replace(microsecond=0)
    status = {
        "id": feed_id, "title": source["title"],
        "source_url": source["source_url"], "description": source["description"],
        "url": f"/feeds/{feed_id}.xml", "last_checked": now.isoformat(),
    }
    try:
        incoming = collect(source, fetch_page)
        merged = {item.url: item for item in old_items}
        merged.update({item.url: item for item in incoming})
        items = sorted(merged.values(), key=lambda i: (i.published, i.url), reverse=True)
        data = render(source, items, base_url, now)
        # No artificial feed update when only the polling time changed.
        if previous is not None:
            old_tree, new_tree = ET.fromstring(previous), ET.fromstring(data)
            for tree in (old_tree, new_tree):
                tree.find("./channel/lastBuildDate").text = ""
            if ET.tostring(old_tree) == ET.tostring(new_tree):
                data = previous
        status.update(status="ok", last_successful=now.isoformat(), items=len(items))
    except Exception as error:
        if not keep_on_error or previous is None:
            raise
        data = previous
        last_successful = None
        if status_file.exists():
            last_successful = json.loads(status_file.read_text()).get("last_successful")
        status.update(status="stale", last_successful=last_successful,
                      items=len(read_items(previous)), error=str(error))
        print(f"::warning::{feed_id}: refresh failed; keeping previous feed: {error}", file=sys.stderr)
    # Commit outputs only after fetching and validating the entire listing.
    read_items(data)
    atomic_write(output, data)
    atomic_write(cached, data)
    atomic_write(status_file, (json.dumps(status, indent=2, ensure_ascii=False) + "\n").encode())
    print(f"{feed_id}: {status['items']} items ({status['status']})")
    return status


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="https://shkku.github.io/")
    parser.add_argument("--cache-dir", type=Path, default=ROOT / ".feed-cache")
    parser.add_argument("--keep-on-error", action="store_true")
    args = parser.parse_args()
    sources = json.loads((ROOT / "feeds" / "sources.json").read_text())
    ids = [source["id"] for source in sources]
    if len(ids) != len(set(ids)):
        raise ValueError("Feed ids must be unique")
    statuses = [update(source, args.base_url, args.cache_dir, args.keep_on_error) for source in sources]
    atomic_write(ROOT / "data" / "feeds.json", (json.dumps(statuses, indent=2, ensure_ascii=False) + "\n").encode())


if __name__ == "__main__":
    main()
