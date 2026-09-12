import json
import tempfile
import unittest
from pathlib import Path

from bs4 import BeautifulSoup

from republisher.catalog import ArticleSpec, infer_source_type, load_catalog
from republisher.rendering import (
    generated_article_from_html,
    render_article_page,
    render_index,
)


def generated(
    slug: str,
    title: str,
    date: str,
    source_type: str = "substack",
) -> str:
    source_url = f"https://example.test/{slug}"
    return render_article_page(
        slug=slug,
        source_type=source_type,
        source_url=source_url,
        metadata={"title": title, "author": "Author", "date": date},
        content_html="<p>Readable content.</p>",
        source_links=((source_url, source_url),),
        library_url="https://library.test/vrac/",
    )


class CatalogTests(unittest.TestCase):
    def test_infers_arxiv_and_article_source_types(self) -> None:
        self.assertEqual(
            infer_source_type("https://arxiv.org/abs/1234.5678"), "arxiv"
        )
        self.assertEqual(
            infer_source_type("https://blog.example.test/p/article"), "substack"
        )

    def test_rejects_duplicate_slugs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "articles.json"
            path.write_text(
                json.dumps(
                    {
                        "articles": [
                            {
                                "source_type": "substack",
                                "source_url": "https://example.test/one",
                                "slug": "same",
                            },
                            {
                                "source_type": "substack",
                                "source_url": "https://example.test/two",
                                "slug": "same",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "Duplicate catalog slug"):
                load_catalog(path)


class RenderingTests(unittest.TestCase):
    def test_article_has_catalog_link_and_machine_metadata(self) -> None:
        html = generated("example", "Example", "2026-09-07")
        soup = BeautifulSoup(html, "html.parser")
        self.assertEqual(
            soup.select_one(".library-link a")["href"], "https://library.test/vrac/"
        )
        self.assertEqual(soup.select_one("meta[name='vrac-slug']")["content"], "example")
        parsed = generated_article_from_html(html)
        self.assertEqual(parsed.title, "Example")
        self.assertEqual(len(parsed.content_sha256), 64)

    def test_index_is_newest_first_and_links_every_article(self) -> None:
        older = generated_article_from_html(
            generated("older", "Older", "12 Jul 2021", "arxiv")
        )
        newer = generated_article_from_html(
            generated("newer", "Newer", "Aug 18, 2026")
        )
        html = render_index([older, newer])
        soup = BeautifulSoup(html, "html.parser")
        links = soup.select(".article-list h2 a")
        self.assertEqual([link.get_text() for link in links], ["Newer", "Older"])
        self.assertEqual(
            [link["href"] for link in links],
            ["articles/newer/", "articles/older/"],
        )


if __name__ == "__main__":
    unittest.main()
