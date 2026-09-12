#!/usr/bin/env python3
"""Republish a Substack article as simple, static HTML."""

from __future__ import annotations

import argparse
import getpass
import json
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

from republisher.catalog import infer_source_type, load_catalog
from republisher.instapaper import send_to_instapaper
from republisher.rendering import read_generated_article, render_article_page
from republisher.workflow import add_article, rebuild_library


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

REPOSITORY_ROOT = Path(__file__).resolve().parent
DEFAULT_PUBLIC_BASE_URL = "https://theopinard.github.io/vrac/"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0].startswith(("http://", "https://")):
        arguments.insert(0, "add")

    parser = argparse.ArgumentParser(
        description="Build and manage the static article library."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_paths(command: argparse.ArgumentParser) -> None:
        command.add_argument(
            "--catalog",
            type=Path,
            default=REPOSITORY_ROOT / "articles.json",
            help="Article source catalog",
        )
        command.add_argument(
            "--output-root",
            type=Path,
            default=REPOSITORY_ROOT / "articles",
            help="Directory that contains generated article directories",
        )
        command.add_argument(
            "--homepage",
            type=Path,
            default=REPOSITORY_ROOT / "index.html",
            help="Generated global article index",
        )

    add_parser = subparsers.add_parser("add", help="Add or rebuild one source URL")
    add_parser.add_argument("url", help="Public Substack or arXiv URL")
    add_paths(add_parser)
    add_parser.add_argument(
        "--public-base-url",
        default=DEFAULT_PUBLIC_BASE_URL,
        help="Published repository URL used for arXiv assets",
    )

    rebuild_parser = subparsers.add_parser(
        "rebuild", help="Rebuild every catalog article and the global index"
    )
    add_paths(rebuild_parser)
    rebuild_parser.add_argument(
        "--public-base-url",
        default=DEFAULT_PUBLIC_BASE_URL,
        help="Published repository URL used for arXiv assets",
    )

    send_parser = subparsers.add_parser(
        "send", help="Send one published catalog article to Instapaper"
    )
    send_parser.add_argument("slug", help="Catalog slug to send")
    send_parser.add_argument(
        "--catalog",
        type=Path,
        default=REPOSITORY_ROOT / "articles.json",
        help="Article source catalog",
    )
    send_parser.add_argument(
        "--output-root",
        type=Path,
        default=REPOSITORY_ROOT / "articles",
        help="Directory that contains generated article directories",
    )
    send_parser.add_argument(
        "--public-base-url",
        default=DEFAULT_PUBLIC_BASE_URL,
        help="Published repository URL containing the generated articles",
    )
    return parser.parse_args(arguments)


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


def is_substack_image_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.hostname == "substackcdn.com":
        return parsed.path.startswith("/image/fetch/")
    return (
        parsed.hostname == "substack-post-media.s3.amazonaws.com"
        and parsed.path.startswith("/public/images/")
    )


def preferred_figure_image_src(source: Tag, source_image: Tag) -> str:
    image_link = source.find("a", href=True)
    if image_link:
        href = str(image_link["href"])
        if is_substack_image_url(href):
            return href
    return str(source_image["src"])


def clean_figure(source: Tag, output: BeautifulSoup) -> Tag | None:
    source_image = source.find("img")
    if not source_image or not source_image.get("src"):
        return None

    image = output.new_tag("img")
    image["src"] = preferred_figure_image_src(source, source_image)
    if source_image.get("alt"):
        image["alt"] = source_image["alt"]
    else:
        image["alt"] = ""

    source_caption = source.find("figcaption")
    if not source_caption or not source_caption.get_text(" ", strip=True):
        return image

    figure = output.new_tag("figure")
    figure.append(image)
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


def render_page(
    metadata: dict[str, str],
    body_html: str,
    source_url: str,
    slug: str,
    public_base_url: str,
) -> str:
    return render_article_page(
        slug=slug,
        source_type="substack",
        source_url=source_url,
        metadata=metadata,
        content_html=body_html,
        source_links=((source_url, source_url),),
        library_url=public_base_url,
    )


def republish(
    url: str,
    output_root: Path,
    *,
    slug_override: str | None = None,
    public_base_url: str = DEFAULT_PUBLIC_BASE_URL,
) -> Path:
    html = fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")
    metadata = extract_metadata(soup)
    source_body = find_article_body(soup)
    body_html = simplify_body(source_body, url)
    slug = slug_override or slug_from_url(url, metadata["title"])
    output_path = output_root / slug / "index.html"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        render_page(metadata, body_html, url, slug, public_base_url),
        encoding="utf-8",
    )
    return output_path


def _builders(public_base_url: str):
    def build_substack(url: str, output_root: Path, slug: str | None) -> Path:
        return republish(
            url, output_root, slug_override=slug, public_base_url=public_base_url
        )

    def build_arxiv(url: str, output_root: Path, slug: str | None) -> Path:
        import republish_arxiv

        public_articles_url = urljoin(
            public_base_url.rstrip("/") + "/", "articles/"
        )
        return republish_arxiv.republish(
            url,
            output_root,
            public_articles_url,
            slug_override=slug,
        )

    return {"substack": build_substack, "arxiv": build_arxiv}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "add":
            source_type = infer_source_type(args.url)
            builders = _builders(args.public_base_url)
            _spec, output_path = add_article(
                source_url=args.url,
                source_type=source_type,
                catalog_path=args.catalog,
                output_root=args.output_root,
                homepage_path=args.homepage,
                builder=builders[source_type],
            )
            print(output_path)
            return 0
        if args.command == "rebuild":
            output_paths = rebuild_library(
                catalog_path=args.catalog,
                output_root=args.output_root,
                homepage_path=args.homepage,
                builders=_builders(args.public_base_url),
            )
            for output_path in output_paths:
                print(output_path)
            return 0
        if args.command == "send":
            specs = load_catalog(args.catalog)
            if not any(spec.slug == args.slug for spec in specs):
                raise ValueError(f"Unknown catalog slug: {args.slug}")
            article_path = args.output_root / args.slug / "index.html"
            if not article_path.is_file():
                raise ValueError(f"Generated article does not exist: {article_path}")
            article = read_generated_article(str(article_path))
            username = input("Instapaper email address or username: ").strip()
            password = getpass.getpass("Password, if you have one: ")
            saved = send_to_instapaper(
                article,
                args.public_base_url,
                username,
                password,
            )
            print(f"Saved to Instapaper: {saved.title} ({saved.public_url})")
            return 0
        raise ValueError(f"Unknown command: {args.command}")
    except (OSError, requests.RequestException, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
