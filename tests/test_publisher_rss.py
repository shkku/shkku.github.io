"""Publisher RSS filtering, real dates, identity and outage behavior."""
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_feeds import feeds, ROOT

SOURCE = next(s for s in json.loads((ROOT / 'feeds/sources.json').read_text()) if s['id'] == 'david-krakauer-papers')
FIXTURE = (ROOT / 'tests/fixtures/krakauer-rss.xml').read_text()


class PublisherRSSTests(unittest.TestCase):
    def test_filters_out_non_papers_and_sorts_by_publisher_dates(self):
        calls = []
        def request(url):
            calls.append(url)
            return FIXTURE
        items = feeds.collect(SOURCE, request)
        self.assertEqual(calls, [SOURCE['rss_url']])
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].title, '2026 Large-Language Models as a Cognitive Virus')
        self.assertEqual(items[0].published, datetime(2026, 9, 4, 17, 24, 5, tzinfo=timezone.utc))
        self.assertTrue(all('papers' in item.categories for item in items))
        self.assertEqual([item.published for item in items], sorted([item.published for item in items], reverse=True))

    def test_hosted_rss_preserves_publisher_identifiers_and_dates(self):
        items = feeds.collect(SOURCE, lambda url: FIXTURE)
        data = feeds.render(SOURCE, items, 'https://shkku.github.io', datetime(2026, 10, 26, tzinfo=timezone.utc))
        self.assertEqual(feeds.read_items(data), items)
        root = feeds.ET.fromstring(data)
        self.assertEqual(root.find(f'channel/{{{feeds.ATOM}}}link').get('href'),
                         'https://shkku.github.io/feeds/david-krakauer-papers.xml')

    def test_missing_dates_or_requested_category_fail(self):
        for failure in ('date', 'category'):
            root = feeds.ET.fromstring(FIXTURE)
            for item in root.findall('channel/item'):
                for node in list(item):
                    if node.tag == ('pubDate' if failure == 'date' else 'category'):
                        item.remove(node)
            broken = feeds.ET.tostring(root)
            with self.subTest(failure=failure), self.assertRaises(ValueError):
                feeds.collect(SOURCE, lambda url: broken)

    def test_malformed_publisher_response_preserves_last_complete_feed(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(feeds, 'ROOT', Path(directory)):
            cache = Path(directory) / 'cache'
            feeds.update(SOURCE, 'https://shkku.github.io', cache, False, lambda url: FIXTURE)
            path = Path(directory) / 'static/feeds/david-krakauer-papers.xml'
            before = path.read_bytes()
            status = feeds.update(SOURCE, 'https://shkku.github.io', cache, True, lambda url: '<html>Unavailable</html>')
            self.assertEqual(status['status'], 'stale')
            self.assertEqual(path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
