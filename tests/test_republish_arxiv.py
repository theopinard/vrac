import unittest

from republish_arxiv import base_identifier, parse_arxiv_url, resolved_identifier


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


if __name__ == "__main__":
    unittest.main()
