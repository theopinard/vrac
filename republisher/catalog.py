"""Read and validate the source catalog used to rebuild the article library."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlparse


SLUG_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
SOURCE_TYPES = {"arxiv", "substack"}


@dataclass(frozen=True)
class ArticleSpec:
    source_type: str
    source_url: str
    slug: str

    def __post_init__(self) -> None:
        if self.source_type not in SOURCE_TYPES:
            raise ValueError(f"Unsupported source type: {self.source_type!r}")
        parsed = urlparse(self.source_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError(f"Invalid article source URL: {self.source_url!r}")
        if not SLUG_PATTERN.fullmatch(self.slug):
            raise ValueError(f"Invalid article slug: {self.slug!r}")


def infer_source_type(url: str) -> str:
    hostname = (urlparse(url).hostname or "").lower()
    return "arxiv" if hostname in {"arxiv.org", "www.arxiv.org"} else "substack"


def load_catalog(path: Path) -> list[ArticleSpec]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid catalog JSON in {path}: {error}") from error

    if not isinstance(data, dict) or not isinstance(data.get("articles"), list):
        raise ValueError("The catalog must contain an 'articles' array")

    articles: list[ArticleSpec] = []
    slugs: set[str] = set()
    source_urls: set[str] = set()
    for ordinal, item in enumerate(data["articles"], start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Catalog article {ordinal} must be an object")
        try:
            article = ArticleSpec(
                source_type=str(item["source_type"]),
                source_url=str(item["source_url"]),
                slug=str(item["slug"]),
            )
        except KeyError as error:
            raise ValueError(
                f"Catalog article {ordinal} is missing {error.args[0]!r}"
            ) from error
        if article.slug in slugs:
            raise ValueError(f"Duplicate catalog slug: {article.slug}")
        if article.source_url in source_urls:
            raise ValueError(f"Duplicate catalog source URL: {article.source_url}")
        slugs.add(article.slug)
        source_urls.add(article.source_url)
        articles.append(article)
    return articles


def write_catalog(path: Path, articles: list[ArticleSpec]) -> None:
    payload = {"articles": [asdict(article) for article in articles]}
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    atomic_write(path, text)


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def upsert_article(
    articles: list[ArticleSpec], article: ArticleSpec
) -> list[ArticleSpec]:
    updated: list[ArticleSpec] = []
    found = False
    for current in articles:
        if current.source_url == article.source_url or current.slug == article.slug:
            if found:
                raise ValueError("The catalog contains conflicting article entries")
            updated.append(article)
            found = True
        else:
            updated.append(current)
    if not found:
        updated.append(article)
    return updated
