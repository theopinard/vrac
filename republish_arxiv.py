#!/usr/bin/env python3
"""Republish an arXiv paper as simple static HTML for Instapaper and Kobo."""

from __future__ import annotations

import argparse
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
            caption_blocks = [
                pymupdf.Rect(block[:4])
                for block in page.get_text("blocks")
                if re.match(
                    rf"\s*{re.escape(kind)}\s+{re.escape(number)}:",
                    str(block[4]),
                    re.IGNORECASE,
                )
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


def render_svg_figures(
    article: Tag,
    document: pymupdf.Document,
    asset_directory: Path,
) -> int:
    svgs = list(article.find_all("svg"))
    for ordinal, svg in enumerate(svgs, start=1):
        figure = svg.find_parent("figure")
        if figure is None:
            raise ValueError("Found an article SVG outside a figure")
        caption = figure.find("figcaption")
        caption_text = caption.get_text(" ", strip=True) if caption else ""
        number_match = FIGURE_NUMBER_PATTERN.search(caption_text)
        if not number_match:
            raise ValueError("Could not identify the caption for an SVG figure")
        figure_number = number_match.group("number")
        page, clip = figure_clip(document, svg, figure_number)

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
        image["alt"] = caption_text or f"Figure {figure_number}"
        image["width"] = str(pixmap.width)
        image["height"] = str(pixmap.height)
        svg.replace_with(image)
    return len(svgs)


def visible_cell_text(cell: Tag) -> str:
    fragment = BeautifulSoup(str(cell), "html.parser")
    for annotation in fragment.find_all("annotation"):
        annotation.decompose()
    return re.sub(r"\s+", " ", fragment.get_text(" ", strip=True)).strip()


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
        before_caption = False
    elif figure_match:
        kind = "Figure"
        number = figure_match.group("number")
        before_caption = True
    else:
        raise ValueError("Could not identify a caption for a data table")

    page, caption_rect, lane = find_numbered_caption_page(
        document, kind, number
    )
    labels: list[str] = []
    for cell in table.find_all(["th", "td"]):
        label = visible_cell_text(cell)
        if len(label) >= 2 and label not in labels:
            labels.append(label)

    matches: list[pymupdf.Rect] = []
    for label in labels:
        candidates = [
            rectangle
            for rectangle in page.search_for(label)
            if rectangle.x0 >= lane.x0 - 4
            and rectangle.x1 <= lane.x1 + 4
            and (
                rectangle.y1 < caption_rect.y0
                if before_caption
                else rectangle.y0 > lane.y1
            )
        ]
        if not candidates:
            continue
        choice = (
            max(candidates, key=lambda rectangle: rectangle.y1)
            if before_caption
            else min(candidates, key=lambda rectangle: rectangle.y0)
        )
        matches.append(choice)

    if len(matches) < 3:
        raise ValueError(f"Could not map the data for {kind} {number} into the PDF")
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
    clip = pymupdf.Rect(
        max(lane.x0 - 4, content.x0 - 6),
        max(page.rect.y0 + 36, content.y0 - 6),
        min(lane.x1 + 4, content.x1 + 6),
        min(caption_rect.y0 - 3, content.y1 + 6)
        if before_caption
        else min(page.rect.y1 - 36, content.y1 + 6),
    )
    if clip.is_empty or clip.width < 50 or clip.height < 20:
        raise ValueError(f"Computed an invalid PDF crop for {kind} {number}")
    return page, clip


def render_data_tables(
    article: Tag,
    document: pymupdf.Document,
    asset_directory: Path,
) -> int:
    tables = [
        table
        for table in article.find_all("table")
        if table.find_parent("figure")
        and (
            TABLE_NUMBER_PATTERN.search(
                table.find_parent("figure").get_text(" ", strip=True)
            )
            or table.find_parent("figure").find("img")
        )
    ]
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


def sanitize_article(article: Tag, source_url: str, asset_base_url: str) -> None:
    for selector in (
        "h1.ltx_title_document",
        ".ltx_authors",
        ".ltx_dates",
    ):
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
        link["href"] = href if href.startswith("#") else urljoin(source_url, href)
    for image in article.find_all("img", src=True):
        src = str(image["src"])
        image["src"] = (
            urljoin(asset_base_url, src)
            if re.fullmatch(r"(?:figure|table)-\d+\.jpe?g", src)
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
) -> str:
    shell = BeautifulSoup(
        "<!doctype html><html lang='en'><head></head><body></body></html>",
        "html.parser",
    )
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
    head.append(style)

    output_article = shell.new_tag("article")
    heading = shell.new_tag("h1")
    heading.string = metadata["title"]
    output_article.append(heading)

    details = [
        metadata.get("author"),
        metadata.get("publication"),
        metadata.get("date"),
    ]
    byline = shell.new_tag("p")
    byline["class"] = "article-meta"
    byline.string = " · ".join(detail for detail in details if detail)
    output_article.append(byline)

    links = shell.new_tag("p")
    links["class"] = "article-meta"
    links.append("Original: ")
    html_link = shell.new_tag("a", href=source.html_url)
    html_link.string = "arXiv HTML"
    links.append(html_link)
    links.append(" · ")
    pdf_link = shell.new_tag("a", href=source.pdf_url)
    pdf_link.string = "PDF"
    links.append(pdf_link)
    output_article.append(links)

    fragment = BeautifulSoup(article.decode_contents(formatter="minimal"), "html.parser")
    for child in list(fragment.contents):
        output_article.append(child)
    body.append(output_article)
    return "<!doctype html>\n" + shell.html.decode(formatter="minimal") + "\n"


def republish(
    url: str,
    output_root: Path,
    public_articles_url: str = DEFAULT_PUBLIC_ARTICLES_URL,
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

    slug = slug_from_title(metadata["title"])
    asset_base_url = urljoin(public_articles_url.rstrip("/") + "/", slug + "/")
    output_directory = output_root / slug
    with tempfile.TemporaryDirectory(prefix="arxiv-republish-") as temporary:
        temporary_directory = Path(temporary)
        pdf_path = temporary_directory / f"{source.resolved_id.replace('/', '-')}.pdf"
        asset_directory = temporary_directory / "assets"
        asset_directory.mkdir()
        download_pdf(source.pdf_url, pdf_path)
        with pymupdf.open(pdf_path) as document:
            if len(document) == 0:
                raise ValueError("The downloaded PDF contains no pages")
            rendered = render_svg_figures(article, document, asset_directory)
            rendered_tables = render_data_tables(
                article, document, asset_directory
            )
        if rendered == 0:
            print("warning: the article contained no SVG figures", file=sys.stderr)
        if rendered_tables == 0:
            print("warning: the article contained no data tables", file=sys.stderr)
        normalize_equation_tables(article)
        normalize_visual_figures(article)
        sanitize_article(article, source.html_url, asset_base_url)
        if article.find("svg"):
            raise ValueError("An SVG remained after figure conversion")
        html = render_page(metadata, article, source)

        output_directory.mkdir(parents=True, exist_ok=True)
        for pattern in ("figure-*.png", "figure-*.jpg", "table-*.png", "table-*.jpg"):
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
        output_path = republish(
            args.url,
            args.output_root,
            args.public_articles_url,
        )
    except (requests.RequestException, pymupdf.FileDataError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
