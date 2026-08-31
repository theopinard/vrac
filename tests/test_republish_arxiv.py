import unittest

from bs4 import BeautifulSoup

from republish_arxiv import (
    base_identifier,
    data_tables_to_render,
    normalize_bibliography,
    normalize_equation_tables,
    normalize_visual_figures,
    parse_arxiv_url,
    resolved_identifier,
    sanitize_article,
    unwrap_citations,
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
    def test_only_outer_data_table_is_rendered(self) -> None:
        soup = BeautifulSoup(
            """
            <article><figure><figcaption>Table 1. Results</figcaption>
            <table id="outer"><tr><td><table id="cell-layout">
            <tr><td>1.0</td></tr></table></td></tr></table>
            </figure></article>
            """,
            "html.parser",
        )
        article = soup.article
        assert article is not None
        self.assertEqual(
            [table["id"] for table in data_tables_to_render(article)],
            ["outer"],
        )

    def test_bibliography_becomes_plain_link_preserving_paragraphs(self) -> None:
        soup = BeautifulSoup(
            """
            <article><p>Prior work <cite><a href="#bib.bib7">Smith</a></cite>.</p>
            <section id="bib" class="ltx_bibliography">
            <h2>References</h2><ul class="ltx_biblist">
            <li id="bib.bib7" class="ltx_bibitem">
            <span class="ltx_tag_bibitem">Smith (2025)</span>
            <span>Jane Smith. <a href="https://doi.org/example">A paper</a>.</span>
            <span class="ltx_bib_cited">Cited by: <a href="#intro">section 1</a>.</span>
            </li></ul></section></article>
            """,
            "html.parser",
        )
        article = soup.article
        assert article is not None
        normalize_bibliography(article)

        self.assertIsNone(article.select_one("section.ltx_bibliography"))
        self.assertIsNone(article.find("li"))
        reference = article.select_one("p#reference-1")
        assert reference is not None
        reference_text = reference.get_text(" ", strip=True)
        self.assertIn("[1] Jane Smith.", reference_text)
        self.assertIn("A paper", reference_text)
        self.assertEqual(reference.a["href"], "https://doi.org/example")
        self.assertNotIn("Cited by", reference.get_text(" ", strip=True))
        self.assertEqual(article.cite.a["href"], "#reference-1")

    def test_discards_latexml_preamble_artifacts_before_title(self) -> None:
        soup = BeautifulSoup(
            """
            <article><div><p>*[inlinelist,1]label=),itemjoin=, and</p></div>
            <h1 class="ltx_title_document">Paper title</h1>
            <div class="ltx_authors">Authors</div>
            <div class="ltx_abstract"><p>Real abstract.</p></div></article>
            """,
            "html.parser",
        )
        article = soup.article
        assert article is not None
        sanitize_article(
            article,
            "https://arxiv.org/html/2107.05720v1",
            "https://example.test/articles/paper/",
        )
        self.assertNotIn("inlinelist", article.get_text(" ", strip=True))
        self.assertIn("Real abstract.", article.get_text(" ", strip=True))

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

    def test_citations_become_simple_external_links(self) -> None:
        soup = BeautifulSoup(
            '<article><p>Prior work <cite>(<a href="#reference-1">'
            'Smith, 2025</a>)</cite>; see <a href="#section-2">section 2</a>.</p>'
            '<p><a href="https://doi.org/example">External source</a></p></article>',
            "html.parser",
        )
        article = soup.article
        assert article is not None
        unwrap_citations(article)
        sanitize_article(
            article,
            "https://arxiv.org/html/2607.23749v1",
            "https://example.test/articles/paper/",
        )

        self.assertEqual(
            article.p.get_text(),
            "Prior work (Smith, 2025); see section 2.",
        )
        self.assertIsNone(article.cite)
        self.assertEqual(len(article.find_all("a")), 3)
        self.assertEqual(
            article.a["href"],
            "https://example.test/articles/paper/#reference-1",
        )


if __name__ == "__main__":
    unittest.main()
