"""Bibliography coverage, stable identity and explicitly approximate sorting dates."""
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_feeds import feeds, ROOT

SOURCE = next(s for s in json.loads((ROOT / 'feeds/sources.json').read_text()) if s['id'] == 'michael-levin-publications')
FIXTURE = (ROOT / 'tests/fixtures/levin.html').read_text()


class LevinTests(unittest.TestCase):
    def test_main_bibliography_includes_books_and_shared_links_but_not_abstracts(self):
        items = feeds.collect(SOURCE, lambda url: FIXTURE)
        self.assertEqual(len(items), 5)
        self.assertEqual(len({i.guid for i in items}), 5)
        self.assertEqual(items[0].categories, ('Publication', '2026'))
        self.assertEqual(items[-1].categories, ('Publication', '1985'))
        self.assertFalse(any('Excluded abstract' == i.title for i in items))
        self.assertTrue(all(i.published.year == int(i.categories[-1]) for i in items))
        shared = [i for i in items if 'pdb.emo101' in i.url]
        self.assertEqual(len(shared), 1)  # Same work cited as a journal paper and a book chapter.
        book = next(i for i in items if i.categories[-1] == '2016')
        self.assertIn('Ahead of the Curve', book.title)
        self.assertEqual(book.url, SOURCE['source_url'] + '#2016')

    def test_malformed_publisher_link_falls_back_to_bibliography_year(self):
        malformed = FIXTURE.replace('https://www.mdpi.com/2409-9287/11/5/161',
                                    'hhttps://www.mdpi.com/2409-9287/11/5/161')
        items = feeds.collect(SOURCE, lambda url: malformed)
        paper = next(i for i in items if i.title.startswith('Ingressing Minds'))
        self.assertEqual(paper.url, SOURCE['source_url'] + '#2026')
        original = feeds.collect(SOURCE, lambda url: FIXTURE)
        self.assertEqual({i.guid for i in items}, {i.guid for i in original})

    def test_distinct_titles_sharing_one_url_keep_distinct_guids(self):
        soup = feeds.BeautifulSoup(FIXTURE, 'html.parser')
        first = soup.find('h2', id='2008').find_next_sibling('p')
        first.find_next_sibling('p').strong.string = 'A distinct publication sharing the publisher URL'
        items = feeds.collect(SOURCE, lambda url: str(soup))
        shared = [i for i in items if 'pdb.emo101' in i.url]
        self.assertEqual(len(shared), 2)
        self.assertNotEqual(shared[0].guid, shared[1].guid)

    def test_rss_dates_sort_newest_years_first_and_disclose_placeholders(self):
        items = feeds.collect(SOURCE, lambda url: FIXTURE)
        data = feeds.render(SOURCE, items, 'https://shkku.github.io', datetime(2026, 10, 23, tzinfo=timezone.utc))
        root = feeds.ET.fromstring(data)
        dates = [feeds.parsedate_to_datetime(node.text) for node in root.findall('channel/item/pubDate')]
        self.assertEqual([d.year for d in dates], [2026, 2026, 2016, 2008, 1985])
        self.assertEqual(dates, sorted(dates, reverse=True))
        self.assertTrue(all((d.month, d.day) == (1, 1) for d in dates))
        self.assertTrue(all('sorting placeholder' in node.text for node in root.findall('channel/item/description')))
        self.assertIsNotNone(root.find('channel/lastBuildDate'))
        self.assertTrue(all(node.get('isPermaLink') == 'false' for node in root.findall('channel/item/guid')))
        self.assertEqual(feeds.read_items(data), items)

    def test_legacy_undated_archive_gets_sort_dates_without_changing_guids(self):
        items = feeds.collect(SOURCE, lambda url: FIXTURE)
        legacy = [feeds.replace(item, published=None) for item in items]
        data = feeds.render(SOURCE, legacy, 'https://shkku.github.io', datetime(2026, 10, 23, tzinfo=timezone.utc))
        migrated = feeds.read_items(data)
        self.assertEqual([item.guid for item in migrated], [item.guid for item in items])
        self.assertEqual([item.published.year for item in migrated], [2026, 2026, 2016, 2008, 1985])

    def test_publisher_url_change_updates_same_entry(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(feeds, 'ROOT', Path(directory)):
            cache = Path(directory) / 'cache'
            feeds.update(SOURCE, 'https://shkku.github.io', cache, False, lambda url: FIXTURE)
            path = Path(directory) / 'static/feeds/michael-levin-publications.xml'
            before = feeds.read_items(path.read_bytes())
            changed = FIXTURE.replace('https://www.mdpi.com/2409-9287/11/5/161', 'https://example.org/new-paper-link')
            feeds.update(SOURCE, 'https://shkku.github.io', cache, False, lambda url: changed)
            after = feeds.read_items(path.read_bytes())
            self.assertEqual(len(after), 5)
            self.assertEqual({i.guid for i in before}, {i.guid for i in after})
            self.assertTrue(any(i.url == 'https://example.org/new-paper-link' for i in after))

    def test_identical_poll_is_byte_stable_for_year_dated_entries(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(feeds, 'ROOT', Path(directory)):
            cache = Path(directory) / 'cache'
            feeds.update(SOURCE, 'https://shkku.github.io', cache, False, lambda url: FIXTURE)
            path = Path(directory) / 'static/feeds/michael-levin-publications.xml'
            before = path.read_bytes()
            feeds.update(SOURCE, 'https://shkku.github.io', cache, False, lambda url: FIXTURE)
            self.assertEqual(path.read_bytes(), before)

    def test_broken_bibliography_keeps_previous_snapshot(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(feeds, 'ROOT', Path(directory)):
            cache = Path(directory) / 'cache'
            feeds.update(SOURCE, 'https://shkku.github.io', cache, False, lambda url: FIXTURE)
            path = Path(directory) / 'static/feeds/michael-levin-publications.xml'
            before = path.read_bytes()
            malformed = '<div class="content"><h1>Peer-Reviewed Papers</h1><h2>2026</h2><p>Missing title markup</p></div>'
            status = feeds.update(SOURCE, 'https://shkku.github.io', cache, True, lambda url: malformed)
            self.assertEqual(status['status'], 'stale')
            self.assertEqual(path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
