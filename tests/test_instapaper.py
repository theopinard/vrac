import unittest

import requests

from republisher.instapaper import ADD_URL, send_to_instapaper
from republisher.rendering import generated_article_from_html, render_article_page


def article_html(content: str = "Current content") -> str:
    return render_article_page(
        slug="example",
        source_type="substack",
        source_url="https://source.example/article",
        metadata={"title": "Example article"},
        content_html=f"<p>{content}</p>",
        source_links=(("Source", "https://source.example/article"),),
        library_url="https://library.test/vrac/",
    )


class FakeResponse:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")


class FakeSession:
    def __init__(self, published_html: str, add_status: int = 201) -> None:
        self.published_html = published_html
        self.add_status = add_status
        self.posts: list[tuple[str, dict[str, str], tuple[str, str]]] = []

    def get(self, url: str, **_kwargs) -> FakeResponse:
        return FakeResponse(200, self.published_html)

    def post(self, url: str, *, data, auth, **_kwargs) -> FakeResponse:
        self.posts.append((url, data, auth))
        return FakeResponse(self.add_status, str(self.add_status))


class InstapaperTests(unittest.TestCase):
    def test_sends_matching_published_article_with_basic_auth(self) -> None:
        html = article_html()
        article = generated_article_from_html(html)
        session = FakeSession(html)
        saved = send_to_instapaper(
            article,
            "https://pages.example/library/",
            "reader@example.test",
            "",
            session=session,  # type: ignore[arg-type]
        )
        self.assertEqual(
            saved.public_url,
            "https://pages.example/library/articles/example/",
        )
        self.assertEqual(
            session.posts,
            [
                (
                    ADD_URL,
                    {"url": saved.public_url, "title": "Example article"},
                    ("reader@example.test", ""),
                )
            ],
        )

    def test_refuses_to_send_a_stale_public_page(self) -> None:
        article = generated_article_from_html(article_html("New content"))
        session = FakeSession(article_html("Old content"))
        with self.assertRaisesRegex(ValueError, "does not match"):
            send_to_instapaper(
                article,
                "https://pages.example/",
                "reader",
                "password",
                session=session,  # type: ignore[arg-type]
            )
        self.assertEqual(session.posts, [])

    def test_maps_documented_api_errors(self) -> None:
        html = article_html()
        article = generated_article_from_html(html)
        expected = {
            400: "rate limit",
            403: "username or password",
            500: "temporarily unavailable",
        }
        for status, message in expected.items():
            with self.subTest(status=status):
                with self.assertRaisesRegex(ValueError, message):
                    send_to_instapaper(
                        article,
                        "https://pages.example/",
                        "reader",
                        "password",
                        session=FakeSession(html, status),  # type: ignore[arg-type]
                    )


if __name__ == "__main__":
    unittest.main()
