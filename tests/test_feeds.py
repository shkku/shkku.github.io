from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("update_feeds", ROOT / "scripts/update_feeds.py")
feeds = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = feeds
spec.loader.exec_module(feeds)
SOURCE = json.loads((ROOT / "feeds/sources.json").read_text())[0]
FIXTURES = Path(__file__).parent / "fixtures"


def listing(title="A &amp; B", date="15 October 2026", link="/science/test", next_href=None):
    page = f'''<div class="fidings_card"><p fs-cmsfilter-field="name">{title}</p>
    <div class="fidings_card-right"><p class="text-size-small">{date}</p></div>
    <a class="press_link-full" href="{link}"></a></div>'''
    if next_href:
        page += f'<div class="fidings_items-wrapper"><a class="w-pagination-next" href="{next_href}">Next</a></div>'
    return page


class FeedTests(unittest.TestCase):
    def test_actual_three_page_listing_includes_pinned_agenda_once(self):
        pages = {
            SOURCE["source_url"]: (FIXTURES / "apollo-page1.html").read_text(),
            SOURCE["source_url"] + "?717f2ceb_page=2": (FIXTURES / "apollo-page2.html").read_text(),
            SOURCE["source_url"] + "?717f2ceb_page=3": (FIXTURES / "apollo-page3.html").read_text(),
        }
        calls = []
        def fetch(url):
            calls.append(url)
            return pages[url]
        items = feeds.collect(SOURCE, fetch)
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(items), 26)
        agenda = [i for i in items if i.url.endswith("/science-of-scheming")]
        self.assertEqual(len(agenda), 1)
        self.assertEqual(items[0].published.isoformat(), "2026-10-15T00:00:00+00:00")
        self.assertEqual(items[-1].published.year, 2023)
        self.assertIn("Notes", next(i.categories for i in items if "precursor" in i.url))
        self.assertFalse(any("system-card" in i.url or "/blog/" in i.url for i in items))

    def test_pagination_loop_and_offsite_pagination_fail(self):
        for next_href in (SOURCE["source_url"], "https://example.com/science?page=2"):
            with self.subTest(next_href=next_href), self.assertRaises(ValueError):
                feeds.collect(SOURCE, lambda url: listing(next_href=next_href))

    def test_partial_pages_and_bad_dates_fail(self):
        for bad in ("<p>Layout changed</p>", listing(date="not a date")):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                feeds.collect(SOURCE, lambda url: bad)

    def test_rss_round_trip_escapes_titles_and_uses_stable_guids(self):
        items = feeds.collect(SOURCE, lambda url: listing())
        now = datetime(2026, 10, 23, tzinfo=timezone.utc)
        data = feeds.render(SOURCE, items, "https://shkku.github.io/", now)
        self.assertIn(b"A &amp; B", data)
        self.assertEqual(feeds.read_items(data), items)
        root = ET.fromstring(data)
        self.assertEqual(root.findtext("./channel/item/guid"), items[0].url)
        self.assertEqual(root.find(f"./channel/{{{feeds.ATOM}}}link").get("href"),
                         "https://shkku.github.io/feeds/apollo-science.xml")

    def test_outage_keeps_previous_feed_and_reports_stale(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(feeds, "ROOT", Path(directory)):
            cache = Path(directory) / "cache"
            initial = feeds.update(SOURCE, "https://shkku.github.io", cache, False, lambda url: listing())
            path = Path(directory) / "static/feeds/apollo-science.xml"
            before = path.read_bytes()
            def broken(url):
                raise TimeoutError("source timed out")
            status = feeds.update(SOURCE, "https://shkku.github.io", cache, True, broken)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(status["status"], "stale")
            self.assertEqual(status["last_successful"], initial["last_successful"])

    def test_failed_later_page_does_not_publish_partial_results(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(feeds, "ROOT", Path(directory)):
            cache = Path(directory) / "cache"
            feeds.update(SOURCE, "https://shkku.github.io", cache, False, lambda url: listing())
            path = Path(directory) / "static/feeds/apollo-science.xml"
            before = path.read_bytes()
            def broken_second_page(url):
                if "?" in url:
                    raise TimeoutError("page two unavailable")
                return listing(link="/science/new", next_href="?page=2")
            feeds.update(SOURCE, "https://shkku.github.io", cache, True, broken_second_page)
            self.assertEqual(path.read_bytes(), before)

    def test_history_retained_and_polling_does_not_change_feed_bytes(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(feeds, "ROOT", Path(directory)):
            cache = Path(directory) / "cache"
            feeds.update(SOURCE, "https://shkku.github.io", cache, False, lambda url: listing())
            feeds.update(SOURCE, "https://shkku.github.io", cache, False,
                         lambda url: listing(title="New paper", link="/science/new"))
            path = Path(directory) / "static/feeds/apollo-science.xml"
            before = path.read_bytes()
            self.assertEqual(len(feeds.read_items(before)), 2)
            feeds.update(SOURCE, "https://shkku.github.io", cache, False,
                         lambda url: listing(title="New paper", link="/science/new"))
            self.assertEqual(path.read_bytes(), before)

    def test_second_feed_has_independent_url_and_history(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(feeds, "ROOT", Path(directory)):
            cache = Path(directory) / "cache"
            second = dict(SOURCE, id="another-source", title="Another source")
            feeds.update(SOURCE, "https://shkku.github.io", cache, False, lambda url: listing())
            feeds.update(second, "https://shkku.github.io", cache, False,
                         lambda url: listing(title="Other", link="/science/other"))
            first_items = feeds.read_items((cache / "apollo-science.xml").read_bytes())
            second_items = feeds.read_items((cache / "another-source.xml").read_bytes())
            self.assertEqual(len(first_items), 1)
            self.assertNotEqual(first_items[0].url, second_items[0].url)
            self.assertIn(b"/feeds/another-source.xml", (cache / "another-source.xml").read_bytes())

    def test_new_feed_without_valid_snapshot_cannot_silently_fail(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(feeds, "ROOT", Path(directory)):
            with self.assertRaises(ValueError):
                feeds.update(SOURCE, "https://shkku.github.io", Path(directory) / "cache", True,
                             lambda url: "<html>No listing</html>")


if __name__ == "__main__":
    unittest.main()
