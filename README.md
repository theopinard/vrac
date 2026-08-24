# Simple article republisher

This repository contains small tools that turn Substack articles and arXiv papers into deliberately simple static HTML for testing with Instapaper and Kobo.

It keeps the readable article structure and remote image URLs, while removing Substack's application shell, scripts, navigation, reactions, recommendations, and other interactive UI. Images are not downloaded, resized, or converted.

## Usage

```bash
uv sync
uv run python republish.py <URL>
```

For example:

```bash
uv run python republish.py "https://blog.bytebytego.com/p/the-new-american-ai-model-designed"
```

Generated articles are written to:

```text
articles/<slug>/index.html
```

The slug normally comes from the final component of the article URL. When the repository root is published with GitHub Pages, the generated article is available at `/articles/<slug>/`.

## arXiv papers

The arXiv republisher accepts normal abstract, HTML, and PDF URLs:

```bash
uv run python republish_arxiv.py "https://arxiv.org/abs/2607.12246v1"
```

It uses arXiv's semantic HTML for the reading order and downloads the matching
PDF revision to turn inline SVG figures and data tables into local,
Kobo-compatible PNG files. Equation-layout tables are reduced to simple math
blocks rather than retained as presentational HTML tables.
An unversioned URL is resolved to the latest available version, which is then
recorded in the generated page. The source PDF is temporary and is not copied
into the repository.

The command writes the page and its figure assets beneath:

```text
articles/<paper-title-slug>/
```

This command requires network access and supports only papers for which arXiv
provides semantic HTML. It does not perform OCR or generic PDF layout
reconstruction.

## Current scope

The Substack tool prioritizes normal server-rendered article HTML and retains
remote image URLs. The arXiv tool prioritizes semantic arXiv HTML and rasterizes
its SVG figures from the matching PDF. Neither tool uses browser automation,
calls the Instapaper API, provides a web UI, or attempts generic extraction from
every website.
