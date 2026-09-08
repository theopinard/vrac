"""Small, explicit client for Instapaper's Simple API."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin

import requests

from .rendering import GeneratedArticle, generated_article_from_html


ADD_URL = "https://www.instapaper.com/api/add"
USER_AGENT = (
    "Mozilla/5.0 (compatible; VracArticleRepublisher/1.0; "
    "+https://github.com/theopinard/vrac)"
)


@dataclass(frozen=True)
class InstapaperSave:
    public_url: str
    title: str


def public_article_url(public_base_url: str, slug: str) -> str:
    return urljoin(public_base_url.rstrip("/") + "/", f"articles/{slug}/")


def verify_published_article(
    article: GeneratedArticle,
    public_base_url: str,
    *,
    session: requests.Session,
) -> str:
    url = public_article_url(public_base_url, article.slug)
    response = session.get(
        url,
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    response.raise_for_status()
    try:
        published = generated_article_from_html(response.text)
    except ValueError as error:
        raise ValueError(
            "The published page was not generated with the current article rules"
        ) from error
    if published.content_sha256 != article.content_sha256:
        raise ValueError(
            "The published page does not match the local build; wait for deployment "
            "to finish and try again"
        )
    return url


def send_to_instapaper(
    article: GeneratedArticle,
    public_base_url: str,
    username: str,
    password: str,
    *,
    session: requests.Session | None = None,
) -> InstapaperSave:
    if not username.strip():
        raise ValueError("Instapaper username must not be empty")
    client = session or requests.Session()
    url = verify_published_article(
        article,
        public_base_url,
        session=client,
    )
    response = client.post(
        ADD_URL,
        data={"url": url, "title": article.title},
        auth=(username, password),
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    if response.status_code == 201:
        return InstapaperSave(public_url=url, title=article.title)
    if response.status_code == 400:
        raise ValueError("Instapaper rejected the request or its rate limit was exceeded")
    if response.status_code == 403:
        raise ValueError("Instapaper rejected the username or password")
    if response.status_code >= 500:
        raise ValueError("Instapaper is temporarily unavailable; try again later")
    raise ValueError(f"Instapaper returned unexpected status {response.status_code}")
