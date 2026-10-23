"""Recorded public API excerpts: pagination, dates, deduplication and outages."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_feeds import feeds, ROOT

FIXTURE = json.loads((ROOT / 'tests/fixtures/pattern.json').read_text())
SOURCE = next(s for s in json.loads((ROOT / 'feeds/sources.json').read_text()) if s['id'] == 'pattern-swarma')
NOW = datetime(2026, 10, 23, 10, tzinfo=timezone.utc)


class PatternTests(unittest.TestCase):
    def requester(self, broken=False):
        calls = []
        def request(url, method='GET', payload=None):
            calls.append((url, method, payload))
            if '/learn_paths?' in url:
                key = 'articles_page2' if '&page=2&' in url else 'articles_page1'
                return deepcopy(FIXTURE[key])
            if broken:
                raise TimeoutError('paper listing unavailable')
            # A real paper appearing in both curated sections must stay one item.
            return deepcopy(FIXTURE['papers']) if payload['top_issue_category'] == 'complexity' else deepcopy(FIXTURE['papers'][:1])
        return request, calls

    def test_combined_feed_paginates_articles_and_deduplicates_papers(self):
        request, calls = self.requester()
        items = feeds.collect_pattern(SOURCE, request, NOW)
        self.assertEqual(len(items), 5)
        self.assertEqual(len(calls), 4)
        article = next(i for i in items if i.url.endswith('/article/401'))
        self.assertEqual(article.published.isoformat(), '2026-10-20T02:54:27+00:00')
        self.assertEqual(article.categories, ('Article',))
        paper = next(i for i in items if i.url.endswith(FIXTURE['papers'][0]['uuid']))
        self.assertEqual(paper.categories, ('Paper', 'Complexity', 'AI+Science'))
        self.assertEqual(paper.published.isoformat(), '2026-10-19T00:00:00+00:00')
        self.assertEqual(calls[-1][1], 'PUT')
        self.assertEqual(calls[-1][2]['start'], '2026-07-25')
        self.assertEqual(calls[-1][2]['end'], '2026-10-23')

    def test_partial_pagination_and_invalid_paper_dates_fail(self):
        for failure in ('missing_article', 'invalid_date', 'unexpected_shape', 'changed_total'):
            request, _ = self.requester()
            def damaged(url, method='GET', payload=None):
                result = request(url, method, payload)
                if failure == 'missing_article' and '&page=2&' in url:
                    result['data'] = []
                if failure == 'changed_total' and '&page=2&' in url:
                    result['meta']['total'] += 1
                if '/top_issue' in url:
                    if failure == 'invalid_date':
                        result[0]['submitTime'] = 'unknown'
                    if failure == 'unexpected_shape':
                        return {'error': 'not a paper list'}
                return result
            with self.subTest(failure=failure), self.assertRaises(ValueError):
                feeds.collect_pattern(SOURCE, damaged, NOW)

    def test_pagination_limit_rejects_runaway_listing(self):
        def request(url):
            result = deepcopy(FIXTURE['articles_page1'])
            result['meta']['last_page'] = 1000
            return result
        with self.assertRaises(ValueError):
            feeds.collect_pattern(SOURCE, request, NOW)

    def test_empty_curated_section_is_valid(self):
        request, _ = self.requester()
        def empty_ai(url, method='GET', payload=None):
            if payload and payload['top_issue_category'] == 'ai+science':
                return []
            return request(url, method, payload)
        self.assertEqual(len(feeds.collect_pattern(SOURCE, empty_ai, NOW)), 5)

    def test_paper_outage_preserves_entire_previous_combined_feed(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(feeds, 'ROOT', Path(directory)):
            cache = Path(directory) / 'cache'
            request, _ = self.requester()
            feeds.update(SOURCE, 'https://shkku.github.io', cache, False, request)
            path = Path(directory) / 'static/feeds/pattern-swarma.xml'
            before = path.read_bytes()
            broken, _ = self.requester(broken=True)
            status = feeds.update(SOURCE, 'https://shkku.github.io', cache, True, broken)
            self.assertEqual(status['status'], 'stale')
            self.assertEqual(path.read_bytes(), before)
            root = feeds.ET.fromstring(before)
            self.assertEqual(root.findtext('channel/language'), 'zh-CN')
            self.assertIn('/feeds/pattern-swarma.xml', root.find(f'channel/{{{feeds.ATOM}}}link').get('href'))


if __name__ == '__main__':
    unittest.main()
