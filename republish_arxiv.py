#!/usr/bin/env python3
"""Republish an arXiv paper as simple static HTML for Instapaper and Kobo."""

from __future__ import annotations

import argparse
import io
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

import pymupdf
import requests
from bs4 import BeautifulSoup, Tag
from PIL import Image, ImageOps

from republisher.rendering import render_article_page
from republisher.workflow import add_article


ARXIV_HOSTS = {"arxiv.org", "www.arxiv.org"}
ARXIV_ID_PATTERN = re.compile(
    r"(?:\d{4}\.\d{4,5}|[a-z][a-z0-9.-]*/\d{7})(?:v\d+)?",
    re.IGNORECASE,
)
VERSION_PATTERN = re.compile(r"v\d+$", re.IGNORECASE)
ARXIV_STAMP_PATTERN = re.compile(
    r"arXiv:(?P<identifier>(?:\d{4}\.\d{4,5}|[a-z][a-z0-9.-]*/\d{7})v\d+)",
    re.IGNORECASE,
)
ARXIV_DATE_PATTERN = re.compile(
    r"arXiv:(?:\d{4}\.\d{4,5}|[a-z][a-z0-9.-]*/\d{7})v\d+"
    r"\s*(?:\[[^]]+])?\s*(?P<date>\d{1,2}\s+[A-Z][a-z]{2}\s+\d{4})",
)
FIGURE_NUMBER_PATTERN = re.compile(r"Figure\s+(?P<number>\d+)", re.IGNORECASE)
TABLE_NUMBER_PATTERN = re.compile(r"Table\s+(?P<number>\d+)", re.IGNORECASE)
CAPTION_LABEL_PATTERN = re.compile(r"\s*(?:Table|Figure)\s+\d+[:.]", re.IGNORECASE)

# Author and date bylines are chrome, not readable body. LaTeXML renders
# decorative ORCID badges as inline SVGs inside the author byline, so these
# must be dropped before render_svg_figures treats every SVG as paper artwork.
BYLINE_SELECTORS = (".ltx_authors", ".ltx_dates")

DROP_TAGS = {
    "script",
    "style",
    "noscript",
    "template",
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

USER_AGENT = (
    "Mozilla/5.0 (compatible; ArxivKoboRepublisher/1.0; "
    "+https://github.com/theopinard/vrac)"
)
DEFAULT_PUBLIC_ARTICLES_URL = "https://theopinard.github.io/vrac/articles/"
REPOSITORY_ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class ArxivSource:
    requested_id: str
    resolved_id: str
    html_url: str
    pdf_url: str
    html: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Republish an arXiv paper as reader-friendly static HTML."
    )
    parser.add_argument("url", help="arXiv /abs, /html, or /pdf URL")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path(__file__).resolve().parent / "articles",
        help="Directory that will contain <title-slug>/index.html and figures",
    )
    parser.add_argument(
        "--public-articles-url",
        default=DEFAULT_PUBLIC_ARTICLES_URL,
        help="Public base URL used to make generated image URLs absolute",
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=REPOSITORY_ROOT / "articles.json",
        help="Article source catalog",
    )
    parser.add_argument(
        "--homepage",
        type=Path,
        default=REPOSITORY_ROOT / "index.html",
        help="Generated global article index",
    )
    return parser.parse_args()


def parse_arxiv_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in ARXIV_HOSTS:
        raise ValueError("Expected an http(s) URL on arxiv.org")

    path = parsed.path.strip("/")
    route, separator, identifier = path.partition("/")
    if not separator or route not in {"abs", "html", "pdf"}:
        raise ValueError("Expected an arXiv /abs, /html, or /pdf URL")
    if route == "pdf" and identifier.lower().endswith(".pdf"):
        identifier = identifier[:-4]
    identifier = identifier.strip("/")
    if not ARXIV_ID_PATTERN.fullmatch(identifier):
        raise ValueError(f"Unsupported or malformed arXiv identifier: {identifier!r}")
    return identifier


def base_identifier(identifier: str) -> str:
    return VERSION_PATTERN.sub("", identifier)


def fetch_response(url: str) -> requests.Response:
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=60)
    response.raise_for_status()
    return response


def resolved_identifier(html: str, requested_id: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    article = soup.select_one("article.ltx_document")
    if article is None:
        raise ValueError(
            "arXiv does not provide semantic HTML for this paper; "
            "generic PDF conversion is not supported"
        )

    stamp = soup.select_one(".arxiv-id")
    haystacks = [stamp.get_text(" ", strip=True)] if stamp else []
    haystacks.append(soup.get_text(" ", strip=True)[:5000])
    for text in haystacks:
        match = ARXIV_STAMP_PATTERN.search(text)
        if not match:
            continue
        candidate = match.group("identifier")
        if base_identifier(candidate).lower() != base_identifier(requested_id).lower():
            continue
        if VERSION_PATTERN.search(requested_id) and candidate.lower() != requested_id.lower():
            raise ValueError(
                f"arXiv returned {candidate}, but the requested version was {requested_id}"
            )
        return candidate
    raise ValueError("Could not determine the resolved arXiv paper version")


def fetch_arxiv_source(url: str) -> ArxivSource:
    requested_id = parse_arxiv_url(url)
    requested_html_url = f"https://arxiv.org/html/{requested_id}"
    first_html = fetch_response(requested_html_url).text
    resolved_id = resolved_identifier(first_html, requested_id)
    html_url = f"https://arxiv.org/html/{resolved_id}"
    html = first_html
    if resolved_id.lower() != requested_id.lower():
        html = fetch_response(html_url).text
        if resolved_identifier(html, resolved_id).lower() != resolved_id.lower():
            raise ValueError("The resolved arXiv HTML version changed while downloading")
    return ArxivSource(
        requested_id=requested_id,
        resolved_id=resolved_id,
        html_url=html_url,
        pdf_url=f"https://arxiv.org/pdf/{resolved_id}",
        html=html,
    )


def download_pdf(url: str, destination: Path) -> None:
    response = fetch_response(url)
    if not response.content.startswith(b"%PDF-"):
        raise ValueError("arXiv did not return a PDF document")
    destination.write_bytes(response.content)


def text_without_children(element: Tag, selector: str) -> str:
    fragment = BeautifulSoup(str(element), "html.parser")
    for child in fragment.select(selector):
        child.decompose()
    return fragment.get_text(" ", strip=True)


def extract_metadata(soup: BeautifulSoup, resolved_id: str) -> dict[str, str]:
    article = soup.select_one("article.ltx_document")
    if article is None:
        raise ValueError("Could not find the semantic arXiv article")
    title_element = article.select_one("h1.ltx_title_document")
    if title_element is None:
        raise ValueError("Could not find the paper title")
    title = text_without_children(title_element, ".ltx_pubnotes")

    authors: list[str] = []
    for element in article.select(".ltx_personname"):
        name = element.get_text(" ", strip=True)
        if name and name not in authors:
            authors.append(name)
    if not authors:
        raise ValueError("Could not find the paper authors")

    metadata = {
        "title": title,
        "author": ", ".join(authors),
        "publication": f"arXiv:{resolved_id}",
    }
    stamp = soup.select_one(".arxiv-id")
    stamp_text = stamp.get_text(" ", strip=True) if stamp else soup.get_text(" ", strip=True)
    date_match = ARXIV_DATE_PATTERN.search(stamp_text)
    if date_match:
        metadata["date"] = date_match.group("date")
    return metadata


def svg_dimensions(svg: Tag) -> tuple[float, float]:
    view_box = str(svg.get("viewBox") or svg.get("viewbox") or "").split()
    if len(view_box) == 4:
        try:
            width, height = float(view_box[2]), float(view_box[3])
            if width > 0 and height > 0:
                return width, height
        except ValueError:
            pass
    try:
        width = float(re.sub(r"[^0-9.]", "", str(svg.get("width", ""))))
        height = float(re.sub(r"[^0-9.]", "", str(svg.get("height", ""))))
    except ValueError as error:
        raise ValueError("Could not determine an SVG figure's dimensions") from error
    if width <= 0 or height <= 0:
        raise ValueError("Could not determine an SVG figure's dimensions")
    return width, height


def svg_labels(svg: Tag) -> list[str]:
    labels: list[str] = []
    for candidate in svg.stripped_strings:
        label = re.sub(r"\s+", " ", candidate).strip()
        if (
            len(label) < 4
            or len(label) > 80
            or label.startswith("\\")
            or label.isnumeric()
            or label in labels
        ):
            continue
        labels.append(label)
    return sorted(labels, key=len, reverse=True)


def union_rect(rectangles: list[pymupdf.Rect]) -> pymupdf.Rect:
    if not rectangles:
        raise ValueError("Cannot combine an empty set of figure bounds")
    result = pymupdf.Rect(rectangles[0])
    for rectangle in rectangles[1:]:
        result.include_rect(rectangle)
    return result


def find_numbered_caption_page(
    document: pymupdf.Document,
    kind: str,
    number: str,
) -> tuple[pymupdf.Page, pymupdf.Rect, pymupdf.Rect]:
    needle = f"{kind} {number}:"
    candidates: list[tuple[pymupdf.Page, pymupdf.Rect, pymupdf.Rect]] = []
    for page in document:
        for rectangle in page.search_for(needle):
            # The caption text can either start its own block (caption above the
            # artwork) or trail the artwork inside a shared block (caption
            # below). Selecting the block that encloses the located caption text
            # handles both layouts, where matching on a leading "Table N:" would
            # miss the caption-below case entirely.
            center = pymupdf.Point(
                (rectangle.x0 + rectangle.x1) / 2,
                (rectangle.y0 + rectangle.y1) / 2,
            )
            caption_blocks = [
                pymupdf.Rect(block[:4])
                for block in page.get_text("blocks")
                if pymupdf.Rect(block[:4]).contains(center)
            ]
            if len(caption_blocks) != 1:
                raise ValueError(
                    f"Could not determine the PDF column for {kind} {number}"
                )
            candidates.append((page, rectangle, caption_blocks[0]))
    if len(candidates) != 1:
        raise ValueError(
            f"Expected one PDF caption for {kind} {number}, found {len(candidates)}"
        )
    return candidates[0]


def find_caption_page(
    document: pymupdf.Document, figure_number: str
) -> tuple[pymupdf.Page, pymupdf.Rect, pymupdf.Rect]:
    return find_numbered_caption_page(document, "Figure", figure_number)


def matching_label_rectangles(
    page: pymupdf.Page,
    labels: list[str],
    caption: pymupdf.Rect,
    lane: pymupdf.Rect,
) -> list[pymupdf.Rect]:
    occurrences: list[list[pymupdf.Rect]] = []
    for label in labels:
        label_matches: list[pymupdf.Rect] = []
        for rectangle in page.search_for(label):
            if rectangle.y1 >= caption.y0:
                continue
            if rectangle.x0 < lane.x0 - 4 or rectangle.x1 > lane.x1 + 4:
                continue
            if rectangle not in label_matches:
                label_matches.append(rectangle)
        if label_matches:
            clusters: list[pymupdf.Rect] = []
            for rectangle in sorted(label_matches, key=lambda item: (item.y0, item.x0)):
                joined = False
                for cluster in clusters:
                    same_line = abs(cluster.y0 - rectangle.y0) <= 3
                    horizontal_gap = max(
                        0.0,
                        rectangle.x0 - cluster.x1,
                        cluster.x0 - rectangle.x1,
                    )
                    if same_line and horizontal_gap <= 3:
                        cluster.include_rect(rectangle)
                        joined = True
                        break
                if not joined:
                    clusters.append(pymupdf.Rect(rectangle))
            occurrences.append(clusters)

    unique_matches = [matches[0] for matches in occurrences if len(matches) == 1]
    if unique_matches:
        seed = union_rect(unique_matches)
    elif occurrences:
        seed = pymupdf.Rect(occurrences[0][0])
    else:
        return []

    selected: list[pymupdf.Rect] = []
    seed_center = (seed.x0 + seed.x1) / 2, (seed.y0 + seed.y1) / 2
    for matches in occurrences:
        if len(matches) == 1:
            choice = matches[0]
        else:
            choice = min(
                matches,
                key=lambda rectangle: (
                    ((rectangle.x0 + rectangle.x1) / 2 - seed_center[0]) ** 2
                    + ((rectangle.y0 + rectangle.y1) / 2 - seed_center[1]) ** 2
                ),
            )
        if choice not in selected:
            selected.append(choice)
        if len(selected) >= 12:
            break
    return selected


def expand_to_aspect_ratio(
    rectangle: pymupdf.Rect,
    ratio: float,
    bounds: pymupdf.Rect,
) -> pymupdf.Rect:
    rectangle = pymupdf.Rect(rectangle)
    if rectangle.width / rectangle.height < ratio:
        desired_width = rectangle.height * ratio
        delta = (desired_width - rectangle.width) / 2
        rectangle.x0 -= delta
        rectangle.x1 += delta
    else:
        desired_height = rectangle.width / ratio
        delta = (desired_height - rectangle.height) / 2
        rectangle.y0 -= delta
        rectangle.y1 += delta

    if rectangle.x0 < bounds.x0:
        rectangle.x1 += bounds.x0 - rectangle.x0
        rectangle.x0 = bounds.x0
    if rectangle.x1 > bounds.x1:
        rectangle.x0 -= rectangle.x1 - bounds.x1
        rectangle.x1 = bounds.x1
    if rectangle.y0 < bounds.y0:
        rectangle.y1 += bounds.y0 - rectangle.y0
        rectangle.y0 = bounds.y0
    if rectangle.y1 > bounds.y1:
        rectangle.y0 -= rectangle.y1 - bounds.y1
        rectangle.y1 = bounds.y1
    return rectangle & bounds


def figure_clip(
    document: pymupdf.Document,
    svg: Tag,
    figure_number: str,
) -> tuple[pymupdf.Page, pymupdf.Rect]:
    page, caption, lane = find_caption_page(document, figure_number)
    labels = svg_labels(svg)
    matches = matching_label_rectangles(page, labels, caption, lane)
    if len(matches) < 2:
        raise ValueError(
            f"Could not uniquely map Figure {figure_number} labels into the PDF"
        )

    label_bounds = union_rect(matches)
    figure = svg.find_parent("figure")
    has_semantic_table = bool(figure and figure.find("table"))
    compound_bottom = label_bounds.y1 + 8
    nearby_drawings: list[pymupdf.Rect] = []
    horizontal_margin = max(18.0, label_bounds.width * 0.12)
    vertical_margin = max(18.0, label_bounds.height * 0.35)
    neighborhood = pymupdf.Rect(
        label_bounds.x0 - horizontal_margin,
        label_bounds.y0 - vertical_margin,
        label_bounds.x1 + horizontal_margin,
        min(caption.y0 - 2, label_bounds.y1 + vertical_margin),
    )
    for drawing in page.get_drawings():
        rectangle = pymupdf.Rect(drawing["rect"])
        if (
            rectangle.y1 < caption.y0
            and (not has_semantic_table or rectangle.y1 <= compound_bottom)
            and rectangle.intersects(neighborhood)
        ):
            nearby_drawings.append(rectangle)

    content = union_rect(matches + nearby_drawings)
    padded = pymupdf.Rect(
        content.x0 - 12,
        content.y0 - 12,
        content.x1 + 12,
        content.y1 + (4 if has_semantic_table else 12),
    )
    svg_width, svg_height = svg_dimensions(svg)
    page_bounds = pymupdf.Rect(
        max(page.rect.x0 + 18, lane.x0 - 4),
        page.rect.y0 + 36,
        min(page.rect.x1 - 18, lane.x1 + 4),
        caption.y0 - 3,
    )
    clip = expand_to_aspect_ratio(padded, svg_width / svg_height, page_bounds)
    if clip.is_empty or clip.width < 50 or clip.height < 30:
        raise ValueError(f"Computed an invalid PDF crop for Figure {figure_number}")
    return page, clip


def adjacent_group(
    rectangles: list[pymupdf.Rect], before_caption: bool, gap: float = 42.0
) -> list[pymupdf.Rect]:
    """Return the contiguous vertical cluster of artwork nearest the caption.

    Figure artwork forms a block of drawings with small vertical gaps, separated
    from body text (or an adjacent figure) by a larger gap. Grouping by vertical
    gaps and keeping the cluster touching the caption isolates a single figure.
    """
    ordered = sorted(rectangles, key=lambda rectangle: rectangle.y0)
    groups: list[list[pymupdf.Rect]] = [[ordered[0]]]
    bounds = pymupdf.Rect(ordered[0])
    for rectangle in ordered[1:]:
        if rectangle.y0 - bounds.y1 <= gap:
            groups[-1].append(rectangle)
            bounds.include_rect(rectangle)
        else:
            groups.append([rectangle])
            bounds = pymupdf.Rect(rectangle)
    if before_caption:
        return max(groups, key=lambda group: union_rect(group).y1)
    return min(groups, key=lambda group: union_rect(group).y0)


def object_figure_clip(
    document: pymupdf.Document,
    figure_number: str,
) -> tuple[pymupdf.Page, pymupdf.Rect]:
    """Locate a PDF figure (possibly a multi-panel composite) by its caption.

    The figure's own SVG carries no PDF coordinates, so the artwork is found
    geometrically: pick the column the caption sits in (or the full width for a
    spanning caption), gather the vector drawings and raster panels on the
    artwork side of the caption, and keep the cluster adjacent to it.
    """
    page, caption, lane = find_caption_page(document, figure_number)
    page_width = page.rect.width
    center = page_width / 2
    top = page.rect.y0 + 36
    bottom = page.rect.y1 - 36
    if lane.width > 0.6 * page_width:
        column = (page.rect.x0 + 18, page.rect.x1 - 18)
    elif (lane.x0 + lane.x1) / 2 < center:
        column = (page.rect.x0 + 18, center - 4)
    else:
        column = (center + 4, page.rect.x1 - 18)

    def in_column(rectangle: pymupdf.Rect) -> bool:
        midpoint = (rectangle.x0 + rectangle.x1) / 2
        return column[0] - 4 <= midpoint <= column[1] + 4

    primitives: list[pymupdf.Rect] = []
    for drawing in page.get_drawings():
        rectangle = pymupdf.Rect(drawing["rect"])
        if top <= rectangle.y0 and rectangle.y1 <= bottom and in_column(rectangle):
            primitives.append(rectangle)
    for image in page.get_images(full=True):
        for rectangle in page.get_image_rects(image[0]):
            rectangle = pymupdf.Rect(rectangle)
            if top <= rectangle.y0 and rectangle.y1 <= bottom and in_column(rectangle):
                primitives.append(rectangle)

    above = [rectangle for rectangle in primitives if rectangle.y1 <= caption.y0]
    below = [rectangle for rectangle in primitives if rectangle.y0 >= lane.y1]

    def area(rectangles: list[pymupdf.Rect]) -> float:
        return sum(max(0.0, r.width) * max(0.0, r.height) for r in rectangles)

    artwork = above if area(above) >= area(below) else below
    if not artwork:
        raise ValueError(
            f"Could not locate the PDF artwork for Figure {figure_number}"
        )
    before_caption = artwork is above
    artwork = adjacent_group(artwork, before_caption)
    artwork_bounds = union_rect(artwork)

    # Span the full column horizontally so axis titles and legends beside the
    # artwork are kept; constrain vertically to the artwork so body text above
    # or below the figure is excluded.
    band = pymupdf.Rect(
        column[0],
        artwork_bounds.y0 - 6,
        column[1],
        artwork_bounds.y1 + 22,
    )
    text_blocks = [
        pymupdf.Rect(block[:4])
        for block in page.get_text("blocks")
        if pymupdf.Rect(block[:4]).intersects(band)
        and in_column(pymupdf.Rect(block[:4]))
        and pymupdf.Rect(block[:4]).y0 >= artwork_bounds.y0 - 4
        and (
            pymupdf.Rect(block[:4]).y1 <= caption.y0
            if before_caption
            else pymupdf.Rect(block[:4]).y0 >= lane.y1
        )
    ]
    content = union_rect(artwork + text_blocks)
    clip = pymupdf.Rect(
        max(column[0] - 4, content.x0 - 6),
        max(top, content.y0 - 6)
        if before_caption
        else max(top, lane.y1 + 1, content.y0 - 6),
        min(column[1] + 4, content.x1 + 6),
        min(caption.y0 - 3, content.y1 + 6)
        if before_caption
        else min(bottom, content.y1 + 6),
    )
    if clip.is_empty or clip.width < 50 or clip.height < 30:
        raise ValueError(f"Computed an invalid PDF crop for Figure {figure_number}")
    return page, clip


def inline_picture_clip(
    document: pymupdf.Document,
    svg: Tag,
) -> tuple[pymupdf.Page, pymupdf.Rect]:
    """Locate an inline LaTeX picture (e.g. a boxed example) by its text labels."""
    labels = svg_labels(svg)
    if len(labels) < 2:
        raise ValueError("Could not locate an inline SVG picture in the PDF")
    for label in labels:
        for page in document:
            anchors = page.search_for(label)
            if len(anchors) != 1:
                continue
            anchor = pymupdf.Rect(anchors[0])
            boxes = [
                pymupdf.Rect(drawing["rect"])
                for drawing in page.get_drawings()
                if pymupdf.Rect(drawing["rect"]).contains(anchor)
            ]
            if not boxes:
                continue
            box = min(boxes, key=lambda rectangle: rectangle.width * rectangle.height)
            clip = pymupdf.Rect(box.x0 - 3, box.y0 - 3, box.x1 + 3, box.y1 + 3)
            if clip.is_empty or clip.width < 50 or clip.height < 30:
                continue
            return page, clip
    raise ValueError("Could not locate an inline SVG picture in the PDF")


def render_svg_figures(
    article: Tag,
    document: pymupdf.Document,
    asset_directory: Path,
) -> int:
    svgs = list(article.find_all("svg"))
    for ordinal, svg in enumerate(svgs, start=1):
        figure = svg.find_parent("figure")
        if figure is None:
            # An inline LaTeX picture (a boxed example or diagram in the text
            # flow) has no caption to anchor on; locate it by its own labels.
            page, clip = inline_picture_clip(document, svg)
            caption_text = ""
        else:
            caption = figure.find("figcaption")
            caption_text = caption.get_text(" ", strip=True) if caption else ""
            number_match = FIGURE_NUMBER_PATTERN.search(caption_text)
            if not number_match:
                raise ValueError("Could not identify the caption for an SVG figure")
            page, clip = figure_clip(document, svg, number_match.group("number"))

        filename = f"figure-{ordinal}.jpg"
        zoom = min(4.0, 1198.0 / clip.width)
        pixmap = page.get_pixmap(
            matrix=pymupdf.Matrix(zoom, zoom),
            clip=clip,
            colorspace=pymupdf.csRGB,
            alpha=False,
        )
        pixmap.pil_save(
            asset_directory / filename,
            format="JPEG",
            quality=88,
            progressive=False,
            optimize=False,
            subsampling=2,
        )

        image = BeautifulSoup("", "html.parser").new_tag("img")
        image["src"] = filename
        image["alt"] = caption_text or "Illustration"
        image["width"] = str(pixmap.width)
        image["height"] = str(pixmap.height)
        svg.replace_with(image)
    return len(svgs)


def localize_raster_images(article: Tag, source_url: str, asset_directory: Path) -> int:
    """Normalize source images to the same reader-safe format as PDF crops.

    Download/decode failures abort the staged build rather than publishing a
    remote fallback that can silently break in Instapaper or on a Kobo.
    """
    converted: dict[str, tuple[str, tuple[int, int]]] = {}
    for image in article.find_all("img", src=True):
        src = str(image["src"])
        if re.fullmatch(r"(?:figure|table)-\d+\.jpe?g", src) and (asset_directory / src).is_file():
            continue
        # Follow the source page's URL semantics: arXiv can emit a version
        # prefix in src ("2607.23749v1/plot.png"). Appending a slash to the
        # page URL would duplicate that prefix and turn a valid image into 404.
        url = urljoin(source_url, src)
        if url not in converted:
            response = fetch_response(url)
            with Image.open(io.BytesIO(response.content)) as original:
                rgba = ImageOps.exif_transpose(original).convert("RGBA")
                rgb = Image.new("RGB", rgba.size, "white")
                rgb.paste(rgba, mask=rgba.getchannel("A"))
                rgb.thumbnail((1200, 1600), Image.Resampling.LANCZOS)
                filename = f"image-{len(converted) + 1}.jpg"
                rgb.save(asset_directory / filename, format="JPEG", quality=88,
                         progressive=False, optimize=False, subsampling=2)
                converted[url] = filename, rgb.size
        filename, (width, height) = converted[url]
        image["src"] = filename
        image["width"] = str(width)
        image["height"] = str(height)
    return len(converted)


def numbered_figure(element: Tag) -> tuple[Tag | None, str | None]:
    """Return the nearest ancestor figure with a numbered caption and its number.

    A subfigure panel carries only a letter caption like "(a) Yahoo"; the figure
    number lives on the enclosing composite figure's own caption.
    """
    figure = element.find_parent("figure")
    while figure is not None:
        caption = figure.find("figcaption", recursive=False)
        if caption is not None:
            match = FIGURE_NUMBER_PATTERN.search(caption.get_text(" ", strip=True))
            if match:
                return figure, match.group("number")
        figure = figure.find_parent("figure")
    return None, None


def collapse_figure_to_image(figure: Tag, image: Tag) -> None:
    """Replace a figure's artwork (all panels) with one image, keeping its caption."""
    caption = figure.find("figcaption", recursive=False)
    for child in list(figure.children):
        if child is caption:
            continue
        child.extract()
    if caption is not None:
        caption.insert_before(image)
    else:
        figure.append(image)


def render_object_figures(
    article: Tag,
    document: pymupdf.Document,
    asset_directory: Path,
    starting_ordinal: int,
) -> int:
    objects = [
        element
        for element in article.find_all("object")
        if str(element.get("type", "")).lower() == "image/svg+xml"
    ]
    # Several panels can belong to one composite figure; render each numbered
    # figure once, in first-appearance order.
    figures: list[tuple[Tag, str]] = []
    seen: set[int] = set()
    for element in objects:
        figure, figure_number = numbered_figure(element)
        if figure is None or figure_number is None:
            raise ValueError("Could not identify the caption for an SVG object figure")
        if id(figure) not in seen:
            seen.add(id(figure))
            figures.append((figure, figure_number))

    for offset, (figure, figure_number) in enumerate(figures):
        page, clip = object_figure_clip(document, figure_number)

        filename = f"figure-{starting_ordinal + offset}.jpg"
        zoom = min(4.0, 1198.0 / clip.width)
        pixmap = page.get_pixmap(
            matrix=pymupdf.Matrix(zoom, zoom),
            clip=clip,
            colorspace=pymupdf.csRGB,
            alpha=False,
        )
        pixmap.pil_save(
            asset_directory / filename,
            format="JPEG",
            quality=88,
            progressive=False,
            optimize=False,
            subsampling=2,
        )

        caption = figure.find("figcaption", recursive=False)
        caption_text = caption.get_text(" ", strip=True) if caption else ""
        image = BeautifulSoup("", "html.parser").new_tag("img")
        image["src"] = filename
        image["alt"] = caption_text or f"Figure {figure_number}"
        image["width"] = str(pixmap.width)
        image["height"] = str(pixmap.height)
        collapse_figure_to_image(figure, image)
    return len(figures)


def visible_cell_text(cell: Tag) -> str:
    fragment = BeautifulSoup(str(cell), "html.parser")
    for annotation in fragment.find_all("annotation"):
        annotation.decompose()
    return re.sub(r"\s+", " ", fragment.get_text(" ", strip=True)).strip()


def stroke_bounds(rectangles: list[pymupdf.Rect]) -> pymupdf.Rect:
    """Bounding box of rectangles, including zero-area strokes (ruling lines).

    pymupdf treats a zero-height rectangle as empty and ignores it in
    include_rect, so ruling lines must be combined by explicit coordinates.
    """
    return pymupdf.Rect(
        min(rectangle.x0 for rectangle in rectangles),
        min(rectangle.y0 for rectangle in rectangles),
        max(rectangle.x1 for rectangle in rectangles),
        max(rectangle.y1 for rectangle in rectangles),
    )


def table_rule_clip(
    page: pymupdf.Page,
    caption_rect: pymupdf.Rect,
    lane: pymupdf.Rect,
) -> tuple[pymupdf.Page, pymupdf.Rect]:
    """Bound a text-heavy table by its horizontal ruling lines.

    Prose cells (long sentences with LaTeXML artifacts) do not match the PDF
    text, so the precise cell mapping fails. Booktabs tables are still framed by
    full-width rules, which delimit the table between this caption and the next.
    """
    page_width = page.rect.width
    center = page_width / 2
    top = page.rect.y0 + 36
    bottom = page.rect.y1 - 36
    if lane.width > 0.6 * page_width:
        column = (page.rect.x0 + 18, page.rect.x1 - 18)
    elif (lane.x0 + lane.x1) / 2 < center:
        column = (page.rect.x0 + 18, center - 4)
    else:
        column = (center + 4, page.rect.x1 - 18)

    def in_column(rectangle: pymupdf.Rect) -> bool:
        midpoint = (rectangle.x0 + rectangle.x1) / 2
        return column[0] - 4 <= midpoint <= column[1] + 4

    rules = [
        pymupdf.Rect(drawing["rect"])
        for drawing in page.get_drawings()
        if pymupdf.Rect(drawing["rect"]).height <= 3
        and pymupdf.Rect(drawing["rect"]).width >= 40
        and in_column(pymupdf.Rect(drawing["rect"]))
    ]
    above = [rectangle for rectangle in rules if rectangle.y1 <= caption_rect.y0]
    below = [rectangle for rectangle in rules if rectangle.y0 >= lane.y1]

    def span(rectangles: list[pymupdf.Rect]) -> float:
        return sum(rectangle.width for rectangle in rectangles)

    ruling = above if span(above) > span(below) else below
    if len(ruling) < 2:
        raise ValueError("Could not bound the table by its ruling lines")
    before_caption = ruling is above
    ruling.sort(key=lambda rectangle: rectangle.y0)

    caption_edges = [
        pymupdf.Rect(block[:4]).y0
        for block in page.get_text("blocks")
        if CAPTION_LABEL_PATTERN.match(str(block[4]))
        and in_column(pymupdf.Rect(block[:4]))
        and not pymupdf.Rect(block[:4]).intersects(lane)
    ]
    if before_caption:
        boundary = max((y for y in caption_edges if y <= ruling[-1].y0), default=top)
        ruling = [r for r in ruling if r.y0 >= boundary - 1 and r.y1 <= caption_rect.y0]
    else:
        boundary = min((y for y in caption_edges if y >= ruling[0].y1), default=bottom)
        ruling = [r for r in ruling if r.y0 >= lane.y1 - 1 and r.y0 < boundary]

    ruling_bounds = stroke_bounds(ruling)
    band = pymupdf.Rect(
        ruling_bounds.x0 - 6,
        ruling_bounds.y0 - 2,
        ruling_bounds.x1 + 6,
        ruling_bounds.y1 + 2,
    )
    text_blocks = [
        pymupdf.Rect(block[:4])
        for block in page.get_text("blocks")
        if pymupdf.Rect(block[:4]).intersects(band)
        and in_column(pymupdf.Rect(block[:4]))
        and pymupdf.Rect(block[:4]).y0 >= ruling_bounds.y0 - 2
        and pymupdf.Rect(block[:4]).y1 <= ruling_bounds.y1 + 2
    ]
    content = stroke_bounds(ruling + text_blocks)
    clip = pymupdf.Rect(
        content.x0 - 6,
        max(top, content.y0 - 4),
        content.x1 + 6,
        min(caption_rect.y0 - 3, content.y1 + 4)
        if before_caption
        else min(bottom, content.y1 + 4),
    )
    if clip.is_empty or clip.width < 50 or clip.height < 20:
        raise ValueError("Computed an invalid PDF crop for a ruled table")
    return page, clip


def table_clip(
    document: pymupdf.Document,
    table: Tag,
) -> tuple[pymupdf.Page, pymupdf.Rect]:
    figure = table.find_parent("figure")
    caption = figure.find("figcaption") if figure else None
    caption_text = caption.get_text(" ", strip=True) if caption else ""
    table_match = TABLE_NUMBER_PATTERN.search(caption_text)
    figure_match = FIGURE_NUMBER_PATTERN.search(caption_text)
    if table_match:
        kind = "Table"
        number = table_match.group("number")
    elif figure_match:
        kind = "Figure"
        number = figure_match.group("number")
    else:
        raise ValueError("Could not identify a caption for a data table")

    page, caption_rect, lane = find_numbered_caption_page(
        document, kind, number
    )
    # Two independent bounds, then their union: ruling lines frame the whole
    # table but miss unruled trailing rows, while cell text catches those rows
    # but truncates prose cells that cannot be matched. Together they cover both.
    rule_clip: pymupdf.Rect | None = None
    if kind == "Table":
        try:
            _, rule_clip = table_rule_clip(page, caption_rect, lane)
        except ValueError:
            rule_clip = None

    labels: list[str] = []
    for cell in table.find_all(["th", "td"]):
        label = visible_cell_text(cell)
        if len(label) >= 2 and label not in labels:
            labels.append(label)

    def match_labels(above_caption: bool) -> list[pymupdf.Rect]:
        found: list[pymupdf.Rect] = []
        for label in labels:
            candidates = [
                rectangle
                for rectangle in page.search_for(label)
                if rectangle.x0 >= lane.x0 - 4
                and rectangle.x1 <= lane.x1 + 4
                and (
                    rectangle.y1 < caption_rect.y0
                    if above_caption
                    else rectangle.y0 > lane.y1
                )
            ]
            if not candidates:
                continue
            choice = (
                max(candidates, key=lambda rectangle: rectangle.y1)
                if above_caption
                else min(candidates, key=lambda rectangle: rectangle.y0)
            )
            found.append(choice)
            if not above_caption and len(label) > 120:
                found.extend(
                    rectangle
                    for rectangle in candidates
                    if choice.y0 < rectangle.y0 <= choice.y1 + 30
                )
        return found

    if kind == "Figure":
        # Data rendered as a figure keeps its artwork above the caption.
        before_caption = True
        matches = match_labels(above_caption=True)
    else:
        # Tables appear either above or below their caption. Pick whichever side
        # the cell text actually maps onto so both layouts render correctly.
        above = match_labels(above_caption=True)
        below = match_labels(above_caption=False)
        before_caption = len(above) > len(below)
        matches = above if before_caption else below

    cell_clip: pymupdf.Rect | None = None
    if len(matches) >= 3:
        label_bounds = union_rect(matches)
        nearby_drawings: list[pymupdf.Rect] = []
        drawing_region = pymupdf.Rect(
            lane.x0 - 4,
            label_bounds.y0 - 10,
            lane.x1 + 4,
            label_bounds.y1 + 10,
        )
        for drawing in page.get_drawings():
            rectangle = pymupdf.Rect(drawing["rect"])
            if rectangle.intersects(drawing_region):
                nearby_drawings.append(rectangle)
        content = union_rect(matches + nearby_drawings)
        cell_clip = pymupdf.Rect(
            max(lane.x0 - 4, content.x0 - 6),
            max(page.rect.y0 + 36, content.y0 - 6),
            min(lane.x1 + 4, content.x1 + 6),
            min(caption_rect.y0 - 3, content.y1 + 6)
            if before_caption
            else min(page.rect.y1 - 36, content.y1 + 6),
        )

    clip: pymupdf.Rect | None = None
    for candidate in (rule_clip, cell_clip):
        if candidate is None:
            continue
        clip = candidate if clip is None else clip | candidate
    if clip is None:
        raise ValueError(f"Could not map the data for {kind} {number} into the PDF")
    if clip.is_empty or clip.width < 50 or clip.height < 20:
        raise ValueError(f"Computed an invalid PDF crop for {kind} {number}")
    return page, clip


def render_data_tables(
    article: Tag,
    document: pymupdf.Document,
    asset_directory: Path,
) -> int:
    tables = data_tables_to_render(article)
    for ordinal, table in enumerate(tables, start=1):
        figure = table.find_parent("figure")
        caption = figure.find("figcaption") if figure else None
        caption_text = caption.get_text(" ", strip=True) if caption else "Data table"
        page, clip = table_clip(document, table)
        filename = f"table-{ordinal}.jpg"
        zoom = min(4.0, 1198.0 / clip.width)
        pixmap = page.get_pixmap(
            matrix=pymupdf.Matrix(zoom, zoom),
            clip=clip,
            colorspace=pymupdf.csRGB,
            alpha=False,
        )
        pixmap.pil_save(
            asset_directory / filename,
            format="JPEG",
            quality=88,
            progressive=False,
            optimize=False,
            subsampling=2,
        )
        image = BeautifulSoup("", "html.parser").new_tag("img")
        image["src"] = filename
        image["alt"] = re.sub(r"\s+", " ", caption_text)[:240]
        image["width"] = str(pixmap.width)
        image["height"] = str(pixmap.height)
        table.replace_with(image)
    return len(tables)


def data_tables_to_render(article: Tag) -> list[Tag]:
    return [
        table
        for table in article.find_all("table")
        # Recent arXiv HTML uses nested tables to lay out individual cells.
        # Rendering the outer table replaces all of them in one operation.
        if table.find_parent("table") is None
        if table.find_parent("figure")
        and (
            TABLE_NUMBER_PATTERN.search(
                table.find_parent("figure").get_text(" ", strip=True)
            )
            or table.find_parent("figure").find("img")
        )
    ]


def normalize_equation_tables(article: Tag) -> None:
    for table in list(article.find_all("table")):
        if table.find_parent("figure"):
            continue
        math = table.find("math")
        if math is None:
            continue
        number = next(
            (
                text
                for cell in table.find_all(["th", "td"])
                if (text := visible_cell_text(cell)).startswith("(")
            ),
            "",
        )
        paragraph = BeautifulSoup("", "html.parser").new_tag("p")
        paragraph.append(math.extract())
        if number:
            paragraph.append(f" {number}")
        table.replace_with(paragraph)


def normalize_visual_figures(article: Tag) -> None:
    for figure in list(article.find_all("figure")):
        images = figure.find_all("img")
        if not images:
            continue
        caption = figure.find("figcaption")
        normalized = BeautifulSoup("", "html.parser").new_tag("figure")
        if figure.get("id"):
            normalized["id"] = str(figure["id"])
        for image in images:
            simple_image = BeautifulSoup("", "html.parser").new_tag("img")
            simple_image["src"] = str(image.get("src", ""))
            simple_image["alt"] = re.sub(
                r"\s+", " ", str(image.get("alt", ""))
            )[:240]
            if image.get("width"):
                simple_image["width"] = str(image["width"])
            if image.get("height"):
                simple_image["height"] = str(image["height"])
            normalized.append(simple_image)
        if caption:
            normalized.append(caption.extract())
        figure.replace_with(normalized)


def normalize_bibliography(article: Tag) -> None:
    """Turn LaTeXML's bibliography list into reader-extractor-safe prose."""
    bibliographies = list(article.select("section.ltx_bibliography"))
    for bibliography in bibliographies:
        items = list(bibliography.select("li.ltx_bibitem"))
        if not items:
            continue

        soup = BeautifulSoup("", "html.parser")
        normalized = soup.new_tag("div")
        heading = soup.new_tag("h2", id="works-cited")
        heading.string = "References"
        normalized.append(heading)

        target_map: dict[str, str] = {"bib": "works-cited"}
        for ordinal, item in enumerate(items, start=1):
            old_id = str(item.get("id", ""))
            new_id = f"reference-{ordinal}"
            if old_id:
                target_map[old_id] = new_id

            # arXiv adds navigation-only backlinks to every entry. Besides not
            # being bibliographic data, their density can make reader services
            # classify the entire reference list as site navigation.
            for cited_by in item.select(".ltx_bib_cited"):
                cited_by.decompose()
            label = next(
                (
                    child
                    for child in item.find_all(recursive=False)
                    if "ltx_tag_bibitem" in child.get("class", [])
                ),
                None,
            )
            if label:
                label.decompose()

            paragraph = soup.new_tag("p", id=new_id)
            paragraph.append(f"[{ordinal}] ")
            for child in list(item.contents):
                paragraph.append(child.extract())
            normalized.append(paragraph)

        for link in article.find_all("a", href=True):
            href = str(link["href"])
            if href.startswith("#") and href[1:] in target_map:
                link["href"] = f"#{target_map[href[1:]]}"
        bibliography.replace_with(normalized)


def unwrap_citations(article: Tag) -> None:
    """Keep citation text and links without Kobo-hostile cite wrappers."""
    for citation in list(article.find_all("cite")):
        citation.unwrap()


def strip_byline_chrome(article: Tag) -> None:
    for selector in BYLINE_SELECTORS:
        for element in article.select(selector):
            element.decompose()


def sanitize_article(article: Tag, source_url: str, asset_base_url: str) -> None:
    document_title = article.select_one("h1.ltx_title_document")
    if document_title:
        # LaTeXML occasionally emits failed preamble commands as a paragraph
        # immediately before the title. Nothing before the semantic document
        # title belongs to the readable paper body.
        for sibling in list(document_title.previous_siblings):
            sibling.extract()
    for selector in ("h1.ltx_title_document", *BYLINE_SELECTORS):
        for element in article.select(selector):
            element.decompose()
    for name in DROP_TAGS:
        for element in article.find_all(name):
            element.decompose()

    abstract_title = article.select_one(".ltx_title_abstract")
    if abstract_title:
        abstract_title.name = "h2"
        abstract_title.string = "Abstract"

    for link in article.find_all("a", href=True):
        href = str(link["href"])
        link["href"] = urljoin(
            asset_base_url if href.startswith("#") else source_url,
            href,
        )
    for image in article.find_all("img", src=True):
        src = str(image["src"])
        image["src"] = (
            urljoin(asset_base_url, src)
            if re.fullmatch(r"(?:figure|table|image)-\d+\.jpe?g", src)
            else urljoin(source_url, src)
        )

    math_names = {
        element.name
        for math in article.find_all("math")
        for element in [math, *math.find_all()]
    }
    for element in article.find_all(True):
        if element.name in math_names or element.find_parent("math"):
            continue
        allowed: dict[str, str | list[str]] = {}
        if element.get("id"):
            allowed["id"] = str(element["id"])
        if element.name == "a" and element.get("href"):
            allowed["href"] = str(element["href"])
        if element.name == "img":
            allowed["src"] = str(element.get("src", ""))
            allowed["alt"] = str(element.get("alt", ""))
            if element.get("width"):
                allowed["width"] = str(element["width"])
            if element.get("height"):
                allowed["height"] = str(element["height"])
        if element.name == "ol" and element.get("start"):
            allowed["start"] = str(element["start"])
        if element.name in {"th", "td"}:
            for attribute in ("colspan", "rowspan", "scope"):
                if element.get(attribute):
                    allowed[attribute] = str(element[attribute])
        element.attrs = allowed


def slug_from_title(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    if not slug:
        raise ValueError("Could not generate an output slug")
    return slug


def render_page(
    metadata: dict[str, str],
    article: Tag,
    source: ArxivSource,
    source_url: str,
    slug: str,
    library_url: str,
) -> str:
    return render_article_page(
        slug=slug,
        source_type="arxiv",
        source_url=source_url,
        metadata=metadata,
        content_html=article.decode_contents(formatter="minimal"),
        source_links=(("arXiv HTML", source.html_url), ("PDF", source.pdf_url)),
        library_url=library_url,
    )


def republish(
    url: str,
    output_root: Path,
    public_articles_url: str = DEFAULT_PUBLIC_ARTICLES_URL,
    *,
    slug_override: str | None = None,
) -> Path:
    source = fetch_arxiv_source(url)
    soup = BeautifulSoup(source.html, "html.parser")
    metadata = extract_metadata(soup, source.resolved_id)
    source_article = soup.select_one("article.ltx_document")
    if source_article is None:
        raise ValueError("Could not find the semantic arXiv article")
    article_soup = BeautifulSoup(str(source_article), "html.parser")
    article = article_soup.select_one("article")
    if article is None:
        raise ValueError("Could not copy the semantic arXiv article")

    slug = slug_override or slug_from_title(metadata["title"])
    articles_base_url = public_articles_url.rstrip("/") + "/"
    asset_base_url = urljoin(articles_base_url, slug + "/")
    library_url = urljoin(articles_base_url, "../")
    output_directory = output_root / slug
    with tempfile.TemporaryDirectory(prefix="arxiv-republish-") as temporary:
        temporary_directory = Path(temporary)
        pdf_path = temporary_directory / f"{source.resolved_id.replace('/', '-')}.pdf"
        asset_directory = temporary_directory / "assets"
        asset_directory.mkdir()
        download_pdf(source.pdf_url, pdf_path)
        strip_byline_chrome(article)
        with pymupdf.open(pdf_path) as document:
            if len(document) == 0:
                raise ValueError("The downloaded PDF contains no pages")
            rendered = render_svg_figures(article, document, asset_directory)
            rendered += render_object_figures(
                article,
                document,
                asset_directory,
                starting_ordinal=rendered + 1,
            )
            rendered_tables = render_data_tables(
                article, document, asset_directory
            )
        localize_raster_images(article, source.html_url, asset_directory)
        if rendered == 0:
            print("warning: the article contained no SVG figures", file=sys.stderr)
        if rendered_tables == 0:
            print("warning: the article contained no data tables", file=sys.stderr)
        normalize_equation_tables(article)
        normalize_visual_figures(article)
        normalize_bibliography(article)
        unwrap_citations(article)
        sanitize_article(article, source.html_url, asset_base_url)
        if article.find("svg") or article.find("object", type="image/svg+xml"):
            raise ValueError("An SVG remained after figure conversion")
        html = render_page(metadata, article, source, url, slug, library_url)

        output_directory.mkdir(parents=True, exist_ok=True)
        for pattern in ("figure-*.png", "figure-*.jpg", "table-*.png", "table-*.jpg", "image-*.jpg"):
            for stale_asset in output_directory.glob(pattern):
                stale_asset.unlink()
        for asset in asset_directory.iterdir():
            shutil.copy2(asset, output_directory / asset.name)
        output_path = output_directory / "index.html"
        output_path.write_text(html, encoding="utf-8")
    return output_path


def main() -> int:
    args = parse_args()
    try:
        def builder(url: str, output_root: Path, slug: str | None) -> Path:
            return republish(
                url,
                output_root,
                args.public_articles_url,
                slug_override=slug,
            )

        _spec, output_path = add_article(
            source_url=args.url,
            source_type="arxiv",
            catalog_path=args.catalog,
            output_root=args.output_root,
            homepage_path=args.homepage,
            builder=builder,
        )
    except (requests.RequestException, pymupdf.FileDataError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
