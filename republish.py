#!/usr/bin/env python3
"""Republish a Substack article as simple, static HTML."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, NavigableString, Tag


BODY_SELECTORS = (
    ".dt-post-body .available-content .body.markup",
    ".available-content .body.markup",
    ".dt-post-body .body.markup",
    "article .body.markup",
    "article .available-content",
    "main article",
    "article",
)

DROP_TAGS = {
    "script",
    "style",
    "noscript",
    "template",
    "svg",
    "button",
    "form",
    "input",
    "select",
    "textarea",
    "iframe",
    "canvas",
    "audio",
    "video",
    "source",
}

CONTENT_TAGS = {
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "p",
    "ul",
    "ol",
    "li",
    "blockquote",
    "pre",
    "code",
    "a",
    "strong",
    "em",
    "b",
    "i",
    "s",
    "del",
    "sup",
    "sub",
    "hr",
    "br",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
}

REMOVE_SELECTORS = (
    ".image-link-expand",
    ".subscription-widget-wrap",
    ".subscribe-widget",
    ".post-ufi",
    ".recommendation-widget",
    ".recommendations",
    "[data-testid='subscribe-widget']",
    "[data-testid='post-ufi']",
    "[aria-hidden='true']",
    "[hidden]",
)

USER_AGENT = (
    "Mozilla/5.0 (compatible; SimpleSubstackRepublisher/1.0; "
    "+https://github.com/theopinard/vrac)"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Republish a Substack article as reader-friendly static HTML."
    )
    parser.add_argument("url", help="Public Substack article URL")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path(__file__).resolve().parent / "articles",
        help="Directory that will contain <slug>/index.html",
    )
    return parser.parse_args()


def fetch_html(url: str) -> str:
    response = requests.get(
        url,
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    response.raise_for_status()
    return response.text


def meta_content(soup: BeautifulSoup, *selectors: str) -> str | None:
    for selector in selectors:
        element = soup.select_one(selector)
        if element:
            value = element.get("content") or element.get_text(" ", strip=True)
            if value:
                return str(value).strip()
    return None


def extract_metadata(soup: BeautifulSoup) -> dict[str, str]:
    title = meta_content(
        soup,
        "meta[property='og:title']",
        "meta[name='twitter:title']",
        "article h1.post-title",
        "article h1",
        "title",
    )
    if not title:
        raise ValueError("Could not find the article title")

    metadata = {"title": title}
    author = meta_content(
        soup,
        "meta[name='author']",
        "article [rel='author']",
        "article .byline-wrapper a[href*='@']",
    )
    publication = meta_content(soup, "meta[property='og:site_name']")
    date = meta_content(
        soup,
        "meta[property='article:published_time']",
        "article time[datetime]",
        "time[datetime]",
    )

    structured_article = find_structured_article(soup)
    if structured_article:
        if not author:
            author = structured_name(structured_article.get("author"))
        if not publication:
            publication = structured_name(structured_article.get("publisher"))
        if not date:
            published = structured_article.get("datePublished")
            if isinstance(published, str):
                date = published

    if author:
        metadata["author"] = author
    if publication:
        metadata["publication"] = publication
    if date:
        metadata["date"] = date
    return metadata


def find_structured_article(soup: BeautifulSoup) -> dict[str, object] | None:
    for script in soup.select("script[type='application/ld+json']"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        candidates = data if isinstance(data, list) else [data]
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            nested = candidate.get("@graph")
            if isinstance(nested, list):
                candidates.extend(nested)
            article_type = candidate.get("@type")
            types = article_type if isinstance(article_type, list) else [article_type]
            if any(value in {"Article", "NewsArticle", "BlogPosting"} for value in types):
                return candidate
    return None


def structured_name(value: object) -> str | None:
    if isinstance(value, list):
        for item in value:
            name = structured_name(item)
            if name:
                return name
    if isinstance(value, dict) and isinstance(value.get("name"), str):
        return value["name"].strip()
    if isinstance(value, str):
        return value.strip()
    return None


def find_article_body(soup: BeautifulSoup) -> Tag:
    for selector in BODY_SELECTORS:
        candidate = soup.select_one(selector)
        if candidate and len(candidate.get_text(" ", strip=True)) >= 200:
            return candidate
    raise ValueError("Could not find a substantial Substack article body")


def remove_ui(body: Tag) -> None:
    for selector in REMOVE_SELECTORS:
        for element in body.select(selector):
            element.decompose()


def absolutize_urls(body: Tag, source_url: str) -> None:
    for link in body.find_all("a", href=True):
        link["href"] = urljoin(source_url, link["href"])
    for image in body.find_all("img", src=True):
        image["src"] = urljoin(source_url, image["src"])


def copy_children(source: Tag, destination: Tag, output: BeautifulSoup) -> None:
    for child in source.children:
        cleaned = clean_node(child, output)
        if cleaned is None:
            continue
        if isinstance(cleaned, list):
            for item in cleaned:
                destination.append(item)
        else:
            destination.append(cleaned)


def clean_figure(source: Tag, output: BeautifulSoup) -> Tag | None:
    source_image = source.find("img")
    if not source_image or not source_image.get("src"):
        return None

    figure = output.new_tag("figure")
    image = output.new_tag("img")
    image["src"] = source_image["src"]
    if source_image.get("alt"):
        image["alt"] = source_image["alt"]
    else:
        image["alt"] = ""
    figure.append(image)

    source_caption = source.find("figcaption")
    if source_caption and source_caption.get_text(" ", strip=True):
        caption = output.new_tag("figcaption")
        copy_children(source_caption, caption, output)
        figure.append(caption)
    return figure


def clean_node(
    source: NavigableString | Tag, output: BeautifulSoup
) -> NavigableString | Tag | list[NavigableString | Tag] | None:
    if isinstance(source, NavigableString):
        return NavigableString(str(source))
    if not isinstance(source, Tag):
        return None

    name = source.name.lower()
    if name in DROP_TAGS:
        return None
    if name == "figure":
        return clean_figure(source, output)
    if name == "picture":
        source_image = source.find("img", recursive=False) or source.find("img")
        return clean_node(source_image, output) if source_image else None
    if name == "img":
        src = source.get("src")
        if not src:
            return None
        image = output.new_tag("img")
        image["src"] = src
        image["alt"] = source.get("alt", "")
        return image

    if name in CONTENT_TAGS:
        element = output.new_tag(name)
        if name == "a" and source.get("href"):
            element["href"] = source["href"]
        if name == "ol" and source.get("start"):
            element["start"] = source["start"]
        if name in {"th", "td"}:
            for attribute in ("colspan", "rowspan"):
                if source.get(attribute):
                    element[attribute] = source[attribute]
        copy_children(source, element, output)
        return element

    children: list[NavigableString | Tag] = []
    for child in source.children:
        cleaned = clean_node(child, output)
        if cleaned is None:
            continue
        if isinstance(cleaned, list):
            children.extend(cleaned)
        else:
            children.append(cleaned)
    return children


def simplify_body(source_body: Tag, source_url: str) -> str:
    remove_ui(source_body)
    absolutize_urls(source_body, source_url)
    output = BeautifulSoup("", "html.parser")
    container = output.new_tag("div")
    copy_children(source_body, container, output)

    for element in list(container.find_all()):
        if element.name in {"p", "li", "blockquote", "figcaption"}:
            if not element.get_text(" ", strip=True) and not element.find("img"):
                element.decompose()
    return container.decode_contents(formatter="minimal")


def slug_from_url(url: str, title: str) -> str:
    path_name = Path(urlparse(url).path.rstrip("/")).name
    source = path_name or title
    slug = re.sub(r"[^a-z0-9]+", "-", source.lower()).strip("-")
    if not slug:
        raise ValueError("Could not generate an output slug")
    return slug


def render_page(metadata: dict[str, str], body_html: str, source_url: str) -> str:
    shell = BeautifulSoup("<!doctype html><html lang='en'><head></head><body></body></html>", "html.parser")
    head = shell.head
    body = shell.body
    assert head is not None and body is not None

    charset = shell.new_tag("meta")
    charset["charset"] = "utf-8"
    head.append(charset)
    viewport = shell.new_tag("meta")
    viewport["name"] = "viewport"
    viewport["content"] = "width=device-width, initial-scale=1"
    head.append(viewport)
    title = shell.new_tag("title")
    title.string = metadata["title"]
    head.append(title)

    style = shell.new_tag("style")
    style.string = """
body {
  max-width: 760px;
  margin: 2rem auto;
  padding: 0 1rem;
  font-family: Georgia, serif;
  line-height: 1.55;
}
img {
  display: block;
  max-width: 100%;
  height: auto;
  margin: 1.5rem auto;
}
figure {
  margin: 2rem 0;
}
figcaption, .article-meta {
  font-size: 0.9em;
}
pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
blockquote {
  margin-left: 0;
  padding-left: 1rem;
  border-left: 3px solid #999;
}
""".strip()
    head.append(style)

    article = shell.new_tag("article")
    heading = shell.new_tag("h1")
    heading.string = metadata["title"]
    article.append(heading)

    details = [
        metadata.get("author"),
        metadata.get("publication"),
        metadata.get("date"),
    ]
    details = [detail for detail in details if detail]
    if details:
        byline = shell.new_tag("p")
        byline["class"] = "article-meta"
        byline.string = " · ".join(details)
        article.append(byline)

    source = shell.new_tag("p")
    source["class"] = "article-meta"
    source.append("Original: ")
    source_link = shell.new_tag("a", href=source_url)
    source_link.string = source_url
    source.append(source_link)
    article.append(source)

    body_fragment = BeautifulSoup(body_html, "html.parser")
    for child in list(body_fragment.contents):
        article.append(child)
    body.append(article)

    return (
        "<!doctype html>\n"
        + shell.html.prettify(formatter="minimal").rstrip()
        + "\n"
    )


def republish(url: str, output_root: Path) -> Path:
    html = fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")
    metadata = extract_metadata(soup)
    source_body = find_article_body(soup)
    body_html = simplify_body(source_body, url)
    slug = slug_from_url(url, metadata["title"])
    output_path = output_root / slug / "index.html"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        render_page(metadata, body_html, url),
        encoding="utf-8",
    )
    return output_path


def main() -> int:
    args = parse_args()
    try:
        output_path = republish(args.url, args.output_root)
    except (requests.RequestException, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
