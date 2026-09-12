import json
import tempfile
import unittest
from pathlib import Path

from bs4 import BeautifulSoup

from republisher.catalog import load_catalog
from republisher.rendering import render_article_page
from republisher.workflow import add_article, rebuild_library


class WorkflowTests(unittest.TestCase):
    def test_add_records_source_and_reuses_its_stable_slug(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            articles = root / "articles"
            catalog = root / "articles.json"
            homepage = root / "index.html"
            requested_slugs: list[str | None] = []

            def builder(url: str, output_root: Path, slug: str | None) -> Path:
                requested_slugs.append(slug)
                generated_slug = slug or "stable-slug"
                output = output_root / generated_slug / "index.html"
                output.parent.mkdir(parents=True)
                output.write_text(
                    render_article_page(
                        slug=generated_slug,
                        source_type="substack",
                        source_url=url,
                        metadata={"title": "Title"},
                        content_html="<p>content</p>",
                        source_links=((url, url),),
                        library_url="https://library.test/vrac/",
                    ),
                    encoding="utf-8",
                )
                return output

            arguments = {
                "source_url": "https://example.test/article",
                "source_type": "substack",
                "catalog_path": catalog,
                "output_root": articles,
                "homepage_path": homepage,
                "builder": builder,
            }
            add_article(**arguments)
            add_article(**arguments)

            self.assertEqual(requested_slugs, [None, "stable-slug"])
            self.assertEqual(len(load_catalog(catalog)), 1)
            self.assertTrue((articles / "stable-slug" / "index.html").is_file())
            self.assertIn(
                "articles/stable-slug/",
                homepage.read_text(encoding="utf-8"),
            )

    def test_failed_rebuild_does_not_replace_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            articles = root / "articles"
            old_article = articles / "one" / "index.html"
            old_article.parent.mkdir(parents=True)
            old_article.write_text("old article", encoding="utf-8")
            homepage = root / "index.html"
            homepage.write_text("old homepage", encoding="utf-8")
            catalog = root / "articles.json"
            catalog.write_text(
                json.dumps(
                    {
                        "articles": [
                            {
                                "source_type": "substack",
                                "source_url": "https://example.test/one",
                                "slug": "one",
                            },
                            {
                                "source_type": "substack",
                                "source_url": "https://example.test/two",
                                "slug": "two",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            def builder(url: str, output_root: Path, slug: str | None) -> Path:
                if url.endswith("/two"):
                    raise ValueError("source failed")
                assert slug is not None
                output = output_root / slug / "index.html"
                output.parent.mkdir(parents=True)
                output.write_text(
                    render_article_page(
                        slug=slug,
                        source_type="substack",
                        source_url=url,
                        metadata={"title": slug},
                        content_html="<p>new</p>",
                        source_links=((url, url),),
                        library_url="https://library.test/vrac/",
                    ),
                    encoding="utf-8",
                )
                return output

            with self.assertRaisesRegex(ValueError, "source failed"):
                rebuild_library(
                    catalog_path=catalog,
                    output_root=articles,
                    homepage_path=homepage,
                    builders={"substack": builder},
                )
            self.assertEqual(old_article.read_text(encoding="utf-8"), "old article")
            self.assertEqual(homepage.read_text(encoding="utf-8"), "old homepage")
            self.assertFalse((articles / "two").exists())

    def test_successful_rebuild_writes_articles_and_index(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            articles = root / "articles"
            homepage = root / "index.html"
            catalog = root / "articles.json"
            catalog.write_text(
                json.dumps(
                    {
                        "articles": [
                            {
                                "source_type": "substack",
                                "source_url": "https://example.test/older",
                                "slug": "older",
                            },
                            {
                                "source_type": "arxiv",
                                "source_url": "https://arxiv.org/abs/1234.5678",
                                "slug": "newer",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            def build(source_type: str, date: str):
                def builder(url: str, output_root: Path, slug: str | None) -> Path:
                    assert slug is not None
                    output = output_root / slug / "index.html"
                    output.parent.mkdir(parents=True)
                    output.write_text(
                        render_article_page(
                            slug=slug,
                            source_type=source_type,
                            source_url=url,
                            metadata={"title": slug.title(), "date": date},
                            content_html="<p>content</p>",
                            source_links=((url, url),),
                            library_url="https://library.test/vrac/",
                        ),
                        encoding="utf-8",
                    )
                    return output

                return builder

            rebuild_library(
                catalog_path=catalog,
                output_root=articles,
                homepage_path=homepage,
                builders={
                    "substack": build("substack", "12 Jul 2021"),
                    "arxiv": build("arxiv", "14 Jul 2026"),
                },
            )
            self.assertTrue((articles / "older" / "index.html").is_file())
            self.assertTrue((articles / "newer" / "index.html").is_file())
            soup = BeautifulSoup(homepage.read_text(encoding="utf-8"), "html.parser")
            self.assertEqual(
                [link.get_text() for link in soup.select(".article-list h2 a")],
                ["Newer", "Older"],
            )


if __name__ == "__main__":
    unittest.main()
