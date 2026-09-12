"""Shared static HTML rendering for articles and the global index."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Mapping, Sequence

from bs4 import BeautifulSoup


ARTICLE_STYLE = """
body {
  max-width: 760px;
  margin: 2rem auto;
  padding: 0 1rem;
  color: #202020;
  background: #fff;
  font-family: Georgia, serif;
  line-height: 1.55;
}
a { color: #315f8c; }
.library-link { margin-bottom: 2rem; }
img {
  display: block;
  max-width: 100%;
  height: auto;
  margin: 1.5rem auto;
}
figure { margin: 2rem 0; }
figcaption, .article-meta { font-size: 0.9em; }
pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
blockquote {
  margin-left: 0;
  padding-left: 1rem;
  border-left: 3px solid #999;
}
math[display="block"] { margin: 1rem auto; overflow-x: auto; }
""".strip()

INDEX_STYLE = """
body {
  max-width: 760px;
  margin: 2rem auto;
  padding: 0 1rem;
  color: #202020;
  background: #fff;
  font-family: Georgia, serif;
  line-height: 1.55;
}
a { color: #315f8c; }
.article-list { list-style: none; margin: 2.5rem 0; padding: 0; }
.article-list li { margin: 0 0 2rem; }
.article-list h2 { margin: 0 0 0.25rem; font-size: 1.25rem; }
.article-meta { margin: 0; color: #555; font-size: 0.9em; }
""".strip()


@dataclass(frozen=True)
class GeneratedArticle:
    slug: str
    source_type: str
    source_url: str
    title: str
    author: str = ""
    publication: str = ""
    published: str = ""
    content_sha256: str = ""


def _new_shell(title_text: str, style_text: str) -> BeautifulSoup:
    shell = BeautifulSoup(
        "<!doctype html><html lang='en'><head></head><body></body></html>",
        "html.parser",
    )
    assert shell.head is not None
    charset = shell.new_tag("meta", charset="utf-8")
    shell.head.append(charset)
    viewport = shell.new_tag("meta")
    viewport["name"] = "viewport"
    viewport["content"] = "width=device-width, initial-scale=1"
    shell.head.append(viewport)
    title = shell.new_tag("title")
    title.string = title_text
    shell.head.append(title)
    style = shell.new_tag("style")
    style.string = style_text
    shell.head.append(style)
    return shell


def _append_meta(shell: BeautifulSoup, name: str, content: str) -> None:
    if not content:
        return
    assert shell.head is not None
    element = shell.new_tag("meta")
    element["name"] = name
    element["content"] = content
    shell.head.append(element)


def render_article_page(
    *,
    slug: str,
    source_type: str,
    source_url: str,
    metadata: Mapping[str, str],
    content_html: str,
    source_links: Sequence[tuple[str, str]],
    library_url: str,
) -> str:
    fingerprint_input = json.dumps(
        {
            "slug": slug,
            "source_type": source_type,
            "source_url": source_url,
            "metadata": dict(metadata),
            "content": content_html,
            "source_links": list(source_links),
        },
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    fingerprint = hashlib.sha256(fingerprint_input).hexdigest()

    shell = _new_shell(metadata["title"], ARTICLE_STYLE)
    _append_meta(shell, "vrac-slug", slug)
    _append_meta(shell, "vrac-source-type", source_type)
    _append_meta(shell, "vrac-source-url", source_url)
    _append_meta(shell, "vrac-title", metadata["title"])
    _append_meta(shell, "vrac-author", metadata.get("author", ""))
    _append_meta(shell, "vrac-publication", metadata.get("publication", ""))
    _append_meta(shell, "vrac-published", metadata.get("date", ""))
    _append_meta(shell, "vrac-content-sha256", fingerprint)

    assert shell.body is not None
    navigation = shell.new_tag("p")
    navigation["class"] = "library-link"
    library_link = shell.new_tag("a", href=library_url)
    library_link.string = "All articles"
    navigation.append(library_link)
    shell.body.append(navigation)

    article = shell.new_tag("article")
    heading = shell.new_tag("h1")
    heading.string = metadata["title"]
    article.append(heading)

    details = [
        metadata.get("author", ""),
        metadata.get("publication", ""),
        metadata.get("date", ""),
    ]
    details = [detail for detail in details if detail]
    if details:
        byline = shell.new_tag("p")
        byline["class"] = "article-meta"
        byline.string = " · ".join(details)
        article.append(byline)

    sources = shell.new_tag("p")
    sources["class"] = "article-meta"
    sources.append("Original: ")
    for ordinal, (label, url) in enumerate(source_links):
        if ordinal:
            sources.append(" · ")
        link = shell.new_tag("a", href=url)
        link.string = label
        sources.append(link)
    article.append(sources)

    fragment = BeautifulSoup(content_html, "html.parser")
    for child in list(fragment.contents):
        article.append(child)
    shell.body.append(article)

    rendered = shell.html.decode(formatter="minimal")
    rendered = "\n".join(line.rstrip() for line in rendered.splitlines())
    return "<!doctype html>\n" + rendered + "\n"


def generated_article_from_html(html: str) -> GeneratedArticle:
    soup = BeautifulSoup(html, "html.parser")

    def value(name: str, *, required: bool = False) -> str:
        element = soup.select_one(f"meta[name='{name}']")
        result = str(element.get("content", "")).strip() if element else ""
        if required and not result:
            raise ValueError(f"Generated article is missing {name!r} metadata")
        return result

    return GeneratedArticle(
        slug=value("vrac-slug", required=True),
        source_type=value("vrac-source-type", required=True),
        source_url=value("vrac-source-url", required=True),
        title=value("vrac-title", required=True),
        author=value("vrac-author"),
        publication=value("vrac-publication"),
        published=value("vrac-published"),
        content_sha256=value("vrac-content-sha256", required=True),
    )


def read_generated_article(path: str) -> GeneratedArticle:
    with open(path, encoding="utf-8") as handle:
        return generated_article_from_html(handle.read())


def _published_timestamp(value: str) -> float:
    formats = (
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%b %d, %Y",
        "%d %b %Y",
    )
    normalized = value.strip()
    if len(normalized) >= 10 and normalized[4:5] == "-" and normalized[7:8] == "-":
        normalized = normalized[:10]
    for format_string in formats:
        try:
            return datetime.strptime(normalized, format_string).timestamp()
        except ValueError:
            continue
    return 0.0


def sorted_articles(
    articles: Iterable[GeneratedArticle],
) -> list[GeneratedArticle]:
    return sorted(
        articles,
        key=lambda article: (-_published_timestamp(article.published), article.title.casefold()),
    )


def render_index(articles: Iterable[GeneratedArticle]) -> str:
    ordered = sorted_articles(articles)
    shell = _new_shell("Article Library", INDEX_STYLE)
    assert shell.body is not None
    heading = shell.new_tag("h1")
    heading.string = "Article Library"
    shell.body.append(heading)
    introduction = shell.new_tag("p")
    introduction.string = "Reader-friendly editions for Instapaper and Kobo."
    shell.body.append(introduction)

    article_list = shell.new_tag("ul")
    article_list["class"] = "article-list"
    for article in ordered:
        item = shell.new_tag("li")
        item_heading = shell.new_tag("h2")
        link = shell.new_tag("a", href=f"articles/{article.slug}/")
        link.string = article.title
        item_heading.append(link)
        item.append(item_heading)
        details = [article.author, article.publication, article.published]
        details = [detail for detail in details if detail]
        if details:
            metadata = shell.new_tag("p")
            metadata["class"] = "article-meta"
            metadata.string = " · ".join(details)
            item.append(metadata)
        article_list.append(item)
    shell.body.append(article_list)

    rendered = shell.html.decode(formatter="minimal")
    rendered = "\n".join(line.rstrip() for line in rendered.splitlines())
    return "<!doctype html>\n" + rendered + "\n"
