import unittest

from bs4 import BeautifulSoup

from republish_arxiv import (
    base_identifier,
    normalize_equation_tables,
    normalize_visual_figures,
    parse_arxiv_url,
    resolved_identifier,
    sanitize_article,
)


class ArxivUrlTests(unittest.TestCase):
    def test_accepts_standard_url_forms(self) -> None:
        expected = "2607.12246v1"
        self.assertEqual(parse_arxiv_url(f"https://arxiv.org/abs/{expected}"), expected)
        self.assertEqual(parse_arxiv_url(f"https://arxiv.org/html/{expected}"), expected)
        self.assertEqual(parse_arxiv_url(f"https://arxiv.org/pdf/{expected}.pdf"), expected)

    def test_accepts_legacy_identifier(self) -> None:
        self.assertEqual(
            parse_arxiv_url("https://arxiv.org/abs/hep-th/9901001v2"),
            "hep-th/9901001v2",
        )

    def test_rejects_other_hosts_and_paths(self) -> None:
        with self.assertRaises(ValueError):
            parse_arxiv_url("https://example.com/abs/2607.12246v1")
        with self.assertRaises(ValueError):
            parse_arxiv_url("https://arxiv.org/search/2607.12246v1")

    def test_base_identifier_removes_only_version_suffix(self) -> None:
        self.assertEqual(base_identifier("2607.12246v12"), "2607.12246")

    def test_resolves_unversioned_semantic_html(self) -> None:
        html = """
        <html><body><div class="arxiv-id">arXiv:2607.12246v3 [cs.LG]</div>
        <article class="ltx_document">paper</article></body></html>
        """
        self.assertEqual(resolved_identifier(html, "2607.12246"), "2607.12246v3")

    def test_rejects_mismatched_explicit_version(self) -> None:
        html = """
        <html><body><div class="arxiv-id">arXiv:2607.12246v2 [cs.LG]</div>
        <article class="ltx_document">paper</article></body></html>
        """
        with self.assertRaises(ValueError):
            resolved_identifier(html, "2607.12246v1")


class ArxivMarkupTests(unittest.TestCase):
    def test_visual_figures_have_only_direct_images_and_caption(self) -> None:
        soup = BeautifulSoup(
            """
            <article><figure id="f1"><div><span><img src="figure-1.jpg"
            alt="diagram"></span></div><div><img src="table-1.jpg"
            alt="table"></div><figcaption>Figure 1.</figcaption></figure></article>
            """,
            "html.parser",
        )
        article = soup.article
        assert article is not None
        normalize_visual_figures(article)
        figure = article.figure
        assert figure is not None
        self.assertEqual(
            [child.name for child in figure.children if getattr(child, "name", None)],
            ["img", "img", "figcaption"],
        )

    def test_equation_layout_table_becomes_plain_math_block(self) -> None:
        soup = BeautifulSoup(
            """
            <article><table><tr><td>(1)</td><td><math alttext="x=1">
            <semantics><mi>x</mi><annotation>x=1</annotation></semantics>
            </math></td></tr></table></article>
            """,
            "html.parser",
        )
        article = soup.article
        assert article is not None
        normalize_equation_tables(article)
        self.assertIsNone(article.table)
        self.assertIsNotNone(article.math)
        self.assertIn("(1)", article.get_text(" ", strip=True))

    def test_generated_images_get_absolute_urls_and_keep_dimensions(self) -> None:
        soup = BeautifulSoup(
            '<article><figure><img src="figure-1.jpg" alt="diagram" '
            'width="1200" height="650"></figure></article>',
            "html.parser",
        )
        article = soup.article
        assert article is not None
        sanitize_article(
            article,
            "https://arxiv.org/html/2607.12246v1",
            "https://example.test/articles/paper/",
        )
        image = article.img
        assert image is not None
        self.assertEqual(
            image["src"],
            "https://example.test/articles/paper/figure-1.jpg",
        )
        self.assertEqual(image["width"], "1200")
        self.assertEqual(image["height"], "650")


if __name__ == "__main__":
    unittest.main()
