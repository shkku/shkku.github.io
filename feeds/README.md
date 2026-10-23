# Homemade RSS feeds + a Hugo blog

This repository serves both the normal Hugo blog and independent RSS feeds through the existing GitHub Pages deployment.

- Feed directory: <https://shkku.github.io/feeds/>
- Apollo Research: <https://shkku.github.io/feeds/apollo-science.xml>
- Swarma articles and papers: <https://shkku.github.io/feeds/pattern-swarma.xml>
- Michael Levin publications: <https://shkku.github.io/feeds/michael-levin-publications.xml>
- Blog: <https://shkku.github.io/blog/>
- Blog RSS: <https://shkku.github.io/blog/index.xml>

## How publishing works

`.github/workflows/hugo.yaml` refreshes feeds, builds Hugo and deploys the whole site on pushes to `main`, manual runs, and every six hours (00:23, 06:23, 12:23 and 18:23 UTC). No separate repository, hosting subscription or secrets are needed. Feed content is generated during the build; the workflow does not commit back into the repository.

Each source has its own stable `static/feeds/<id>.xml`. The generated `data/feeds.json` powers the `/feeds/` directory, including item counts and refresh status. Blog posts continue to live under `content/blog/`; the external publications are never turned into blog posts or mixed into the blog RSS.

GitHub may delay scheduled runs, and [automatically disables schedules in public repositories after 60 days without repository activity](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/disable-and-enable-workflows). Check Actions if the displayed refresh time stops advancing; re-enable the workflow there or run it manually. This is scheduled polling, not a real-time service.

## Add another feed

1. Inspect the source's HTML. The included scraper supports server-rendered HTML lists, CSS selectors and optional next-page links. JavaScript-only pages, APIs, authentication and unusual dates need an additional scraper implementation. Pattern already has a dedicated public-API collector selected with `kind: pattern`.
2. Append an object to `feeds/sources.json`, using a unique lowercase hyphenated `id`:

   ```json
   {
     "id": "another-research-site",
     "title": "Another Research Site (unofficial)",
     "description": "Unofficial feed; links to original publications.",
     "source_url": "https://example.org/research",
     "date_format": "%Y-%m-%d",
     "selectors": {
       "item": "article.publication",
       "title": "h2",
       "date": ".published-date",
       "link": "h2 a",
       "next": "a.next"
     }
   }
   ```

   `category` is an optional selector within each item. Date text must match Python's `datetime.strptime` format (English month names for `%B` in the GitHub runner). The scraper reads the element's text, not its `datetime` attribute. Date-only publications use midnight UTC. Pagination must remain on the same host, scheme and listing path; query parameters may change. The limit is 50 pages per source.
3. Run the local commands below and inspect the resulting XML. Include a fixture and a test for the new source's structure.
4. Commit the configuration, generated initial XML, generated catalog, and any scraper/test changes, then push to `main`. The new feed appears on `/feeds/` automatically at `/feeds/another-research-site.xml`.

Keep feed ids and entry identities stable: most sources use their original article URL as the RSS GUID. Levin's bibliography uses a normalized year/title identity because publisher links can be missing or reused. Refreshes merge new entries with existing ones, update their metadata, and retain older entries that disappear from the source listing. Identical polls keep the XML bytes and `lastBuildDate` unchanged. Feeds contain titles, original links, available dates and categories, without copying article bodies.

## Local refresh and verification

From the repository root:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r scripts/requirements-feeds.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/update_feeds.py
hugo server
```

Open `/feeds/` on the local Hugo server. `--base-url` overrides the public URL embedded in the RSS self-link. The default is `https://shkku.github.io/`.

A strict local refresh fails on a fetch or parsing error. In deployment, `--keep-on-error` instead retains a valid previous feed, emits an Actions warning and marks that source stale in the directory. A new source with no valid snapshot fails the build, so an empty or partial feed is never published as successful. There are three fetch attempts with a 30-second timeout per request. A failed later page leaves the previous XML intact.

The workflow caches `.feed-cache/` between runs, including XML history and last-success times. Committed `static/feeds/*.xml` files provide initial/fallback snapshots if that cache is unavailable. GitHub Actions caches are evictable, so they are not a durable archive of entries that have disappeared upstream. To preserve such entries permanently, periodically refresh locally and commit the XML snapshots. Freshness timestamps shown on the site describe the last deployed build, not a live health check.

## Apollo scope

The initial feed was verified against the live `/science` listing on 2026-10-23: 26 unique entries across three pages, including the pinned research agenda. Repeated pinned entries are deduplicated. Separate system-card panels, general blog posts, and science URLs absent from the All research listing are excluded. The feed is unofficial and attributes its source in both the channel description and directory.

## Pattern scope

`pattern-swarma.xml` combines the public **Articles** listing (`/articles`, all paginated entries) with curated papers from **Complexity Express** (`/top_issue`) and **AI+Science**. It excludes WeChat posts, reading groups, learning paths, the arXiv daily stream, and the user-recommended paper stream. The original Chinese article titles and English paper titles are preserved; links point to Pattern's `/article/<id>` and `/paper/<uuid>` pages.

Each refresh fetches the complete article listing and a rolling 90-day window of curated paper publication dates. Existing collected items remain in the feed as they age beyond that window. The AI+Science endpoint currently returns an empty list; the collector also checks it on future refreshes. The initial live scrape on 2026-10-23 returned 220 articles and 206 curated papers.

Article publication dates use `created_at`, with the API's naive timestamps interpreted as Asia/Shanghai (an explicit timezone assumption). Paper dates use `submitTime` at midnight UTC; these are original publication dates, not the time Pattern curated the paper. Edits preserve the same original URL as the RSS GUID.

The collector calls the same public API paths referenced by the website's shipped JavaScript: `GET https://api.swarma.org/api/index/learn_paths?serious=0&...` and the read-only listing query `PUT https://api.swarma.org/api/express/top_issue` with date/category filters. Neither needs login credentials. Missing article pages, changed pagination totals, malformed dates or API errors preserve the prior complete feed during deployment rather than publishing only half of the combined source. Recorded fixtures retain only ids, titles, dates and pagination metadata.

## Michael Levin scope

`michael-levin-publications.xml` follows the main year-grouped bibliography on <https://www.drmichaellevin.org/publications/>, including its listed papers, chapters and books. The separate “Conferences, Abstracts, and Poster Sessions” section is excluded. Both ordinary paragraphs and entries with book/journal cover images are included.

The page generally supplies publication years rather than full dates. The feed preserves those years as categories and uses **January 1 of each year as an explicit sorting placeholder** in RSS `pubDate`. The channel and each item explain that these are not exact publication dates. This lets readers sort newest years first; omitting dates caused some readers to import the archive in reverse order. Ordering within the same year does not imply a known publication sequence. Existing GUIDs stay unchanged. The real feed generation time remains in `lastBuildDate`.

Entries link to the listed PDF/publisher/book URL, or to the bibliography's year anchor if no valid external link is provided (the source currently includes a malformed `hhttps:` link). GUIDs use a SHA-256 digest of the normalized year and title, so changing the publisher URL preserves the entry's identity. Repeated citations with the same normalized title/year are deduplicated (including a journal/book citation of the same 2008 work); distinct titles sharing a URL remain separate. A substantive title correction or changed publication year will create a new identity.

## Keep blogging

Create a post with `hugo new content blog/my-post.md`, write it, and set `draft = false` (or remove the draft flag) when ready. Commit and push to `main` as usual. Hugo builds the blog and its RSS alongside the homemade feeds.
