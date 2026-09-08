"""Catalog-aware build orchestration."""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Callable, Mapping

from .catalog import ArticleSpec, atomic_write, load_catalog, upsert_article, write_catalog
from .rendering import GeneratedArticle, read_generated_article, render_index


ProviderBuilder = Callable[[str, Path, str | None], Path]


def _validate_generated(
    spec: ArticleSpec, output_path: Path
) -> GeneratedArticle:
    if output_path.name != "index.html" or output_path.parent.name != spec.slug:
        raise ValueError(
            f"Provider wrote {output_path}, expected an index for slug {spec.slug!r}"
        )
    article = read_generated_article(str(output_path))
    if article.slug != spec.slug:
        raise ValueError(
            f"Generated slug {article.slug!r} does not match catalog slug {spec.slug!r}"
        )
    if article.source_type != spec.source_type:
        raise ValueError(
            f"Generated source type {article.source_type!r} does not match the catalog"
        )
    if article.source_url != spec.source_url:
        raise ValueError("Generated source URL does not match the catalog")
    return article


def _replace_directory(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    incoming = destination.parent / f".{destination.name}.incoming-{token}"
    backup = destination.parent / f".{destination.name}.backup-{token}"
    shutil.copytree(source, incoming)
    had_destination = destination.exists()
    try:
        if had_destination:
            os.replace(destination, backup)
        os.replace(incoming, destination)
    except BaseException:
        if had_destination and backup.exists() and not destination.exists():
            os.replace(backup, destination)
        shutil.rmtree(incoming, ignore_errors=True)
        raise
    if backup.exists():
        shutil.rmtree(backup)


def _read_library(
    specs: list[ArticleSpec], article_roots: Mapping[str, Path]
) -> list[GeneratedArticle]:
    generated: list[GeneratedArticle] = []
    for spec in specs:
        root = article_roots[spec.slug]
        output_path = root / "index.html"
        if not output_path.is_file():
            raise ValueError(f"Missing generated article: {output_path}")
        generated.append(_validate_generated(spec, output_path))
    return generated


def rebuild_library(
    *,
    catalog_path: Path,
    output_root: Path,
    homepage_path: Path,
    builders: Mapping[str, ProviderBuilder],
) -> list[Path]:
    specs = load_catalog(catalog_path)
    if not specs:
        raise ValueError("The article catalog is empty")
    output_root.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(
        dir=output_root.parent, prefix=".vrac-build-"
    ) as temporary_name:
        staging_root = Path(temporary_name) / "articles"
        staged_roots: dict[str, Path] = {}
        for spec in specs:
            builder = builders.get(spec.source_type)
            if builder is None:
                raise ValueError(f"No provider for source type {spec.source_type!r}")
            output_path = builder(spec.source_url, staging_root, spec.slug)
            _validate_generated(spec, output_path)
            staged_roots[spec.slug] = output_path.parent

        generated = _read_library(specs, staged_roots)
        homepage_html = render_index(generated)

        written: list[Path] = []
        for spec in specs:
            destination = output_root / spec.slug
            _replace_directory(staged_roots[spec.slug], destination)
            written.append(destination / "index.html")
        atomic_write(homepage_path, homepage_html)
        written.append(homepage_path)
        return written


def add_article(
    *,
    source_url: str,
    source_type: str,
    catalog_path: Path,
    output_root: Path,
    homepage_path: Path,
    builder: ProviderBuilder,
) -> tuple[ArticleSpec, Path]:
    specs = load_catalog(catalog_path)
    existing = next(
        (article for article in specs if article.source_url == source_url),
        None,
    )
    requested_slug = existing.slug if existing else None
    output_root.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(
        dir=output_root.parent, prefix=".vrac-build-"
    ) as temporary_name:
        staging_root = Path(temporary_name) / "articles"
        output_path = builder(source_url, staging_root, requested_slug)
        slug = output_path.parent.name
        spec = ArticleSpec(
            source_type=source_type,
            source_url=source_url,
            slug=slug,
        )
        _validate_generated(spec, output_path)
        updated_specs = upsert_article(specs, spec)

        roots: dict[str, Path] = {}
        for article in updated_specs:
            roots[article.slug] = (
                output_path.parent
                if article.slug == spec.slug
                else output_root / article.slug
            )
        generated = _read_library(updated_specs, roots)
        homepage_html = render_index(generated)

        destination = output_root / spec.slug
        _replace_directory(output_path.parent, destination)
        write_catalog(catalog_path, updated_specs)
        atomic_write(homepage_path, homepage_html)
        return spec, destination / "index.html"
