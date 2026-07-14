import importlib.util
import unittest
from datetime import date
from pathlib import Path

spec = importlib.util.spec_from_file_location("update_news", Path(__file__).parents[1] / "scripts" / "update_news.py")
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)


class NewsTests(unittest.TestCase):
    def test_google_news_items_get_clean_titles_and_summaries(self):
        raw = """<rss><channel><item>
          <title>Cyclospora update - Associated Press</title>
          <link>https://news.google.com/rss/articles/example</link>
          <description>Cyclospora update - Associated Press</description>
          <pubDate>Tue, 14 Jul 2026 12:00:00 GMT</pubDate>
          <source url="https://apnews.com">Associated Press</source>
        </item></channel></rss>"""
        item = module.parse_rss(raw)[0]
        self.assertEqual(item["title"], "Cyclospora update")
        self.assertEqual(
            item["summary"],
            "Recent Cyclospora coverage from Associated Press. Open the article for the full report.",
        )

    def test_rss_validation_and_sanitizing(self):
        raw = (Path(__file__).parent / "fixtures" / "news.xml").read_text(encoding="utf-8")
        candidates = module.parse_rss(raw)
        item = module.validate(candidates[0], date(2026, 7, 14))
        self.assertEqual(item["kind"], "official")
        self.assertNotIn("?", item["url"])
        self.assertIn("Cyclospora", item["title"])

    def test_rejects_untrusted_host(self):
        with self.assertRaises(ValueError):
            module.validate({"title": "Cyclospora alert", "summary": "Cyclospora details", "source": "Blog", "url": "https://blog.example/x", "published_at": "2026-07-13"}, date(2026, 7, 14))

    def test_stable_id_ignores_tracking_query(self):
        self.assertEqual(module.stable_id("CDC", "https://cdc.gov/x?a=1"), module.stable_id("CDC", "https://cdc.gov/x?b=2"))

    def test_google_news_link_requires_allowlisted_publisher(self):
        item = module.validate({
            "title": "Cyclospora outbreak update",
            "summary": "Recent Cyclospora coverage from AP.",
            "source": "Associated Press",
            "url": "https://news.google.com/rss/articles/example",
            "publisher_url": "https://apnews.com/",
            "published_at": "2026-07-14",
        }, date(2026, 7, 14))
        self.assertEqual(item["kind"], "major-media")
        with self.assertRaises(ValueError):
            module.validate({**item, "publisher_url": "https://blog.example/"}, date(2026, 7, 14))

    def test_clean_decodes_html_entities(self):
        self.assertEqual(module.clean("Cyclospora&nbsp;update", 100), "Cyclospora update")

    def test_outbreak_generates_stable_official_events(self):
        snapshot = {"sources": {"cdc": {"official_as_of": "2026-07-13", "cases": 1645, "hospitalizations": 141, "deaths": 0, "states": 34, "source_url": "https://www.cdc.gov/cyclosporiasis/php/surveillance/index.html"}}}
        self.assertEqual(module.outbreak_items(snapshot)[0]["id"], "cdc-surveillance")


if __name__ == "__main__": unittest.main()
