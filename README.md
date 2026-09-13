# Static article library

This repository turns Substack articles and arXiv papers into deliberately
simple static HTML for Instapaper and Kobo. The generated homepage lists every
source in `articles.json`, and every article links back to that library.

The Substack extractor keeps readable article structure and remote images while
removing the application shell and interactive UI. The arXiv extractor uses
semantic HTML for reading order and the matching PDF to turn SVG figures and
formatted data tables into local baseline RGB JPEGs.
The arXiv extractor downloads source raster images and converts them to local baseline RGB
JPEGs, with transparency flattened onto white and dimensions bounded to
1200 × 1600 pixels. Readers therefore do not need to fetch figures from source sites.

## Kobo image compatibility: verified September 2026

Kobo displayed broken images in the Airbnb Journey Ranker article and IDProxy
Figure 1, even though the original arXiv PNG URLs returned HTTP 200. IDProxy
Figure 2 worked. Airbnb's PNGs used transparency, but both IDProxy figures were
RGB PNGs, so transparency alone did not explain the failures. The precise
Kobo/Instapaper decoder or retrieval failure was not established.

After hosting baseline RGB JPEG versions locally, the user confirmed that the
images worked on Kobo. Preserve this tested output contract for **all arXiv articles**:

- Download every image; never leave remote image fallbacks in generated HTML.
- Flatten transparency onto white, normalize to RGB, and save non-progressive
  JPEGs. Bound source raster images to 1200 × 1600 pixels without upscaling.
- Use absolute public URLs pointing to files inside the article's own directory.
- Abort a staged build on download or decode failure instead of publishing
  broken assets. Keep local figure/table JPEGs generated from PDFs.

The implementation is `localize_raster_images` in `republish_arxiv.py`. Run
`uv run python republish.py rebuild` and `uv run python -m unittest discover -v`
before publishing. The checked-in-library regression test verifies every arXiv article
image is local, exists, decodes, and is a baseline RGB JPEG. A successful source
HTTP request or desktop preview alone is not evidence of Kobo compatibility.
After publication, verify updated articles through the actual Kobo reading flow.

## Setup

```bash
uv sync
```

## Add or update one article

The unified command detects whether a source is arXiv or Substack, generates
the article, adds it to the catalog, and refreshes the homepage:

```bash
uv run python republish.py add "https://blog.bytebytego.com/p/example"
uv run python republish.py add "https://arxiv.org/abs/2607.12246v1"
```

For compatibility, a URL can still be passed without the `add` subcommand:

```bash
uv run python republish.py "https://blog.bytebytego.com/p/example"
```

The existing arXiv entry point is also retained as a catalog-aware wrapper:

```bash
uv run python republish_arxiv.py "https://arxiv.org/abs/2607.12246v1"
```

Generated files are written beneath `articles/<slug>/`. Slugs recorded in the
catalog are reused on later builds so public URLs remain stable.

## Rebuild the library

```bash
uv run python republish.py rebuild
```

Every catalog source is built in a staging directory first. The committed
article pages, arXiv image assets, and root `index.html` are replaced only after
all sources build successfully.

The default public location is:

```text
https://theopinard.github.io/vrac/
```

Forks can change it with `--public-base-url`. This also controls the absolute
arXiv image URLs embedded for Instapaper.

## Send a published article to Instapaper

Publish the rebuilt files first and wait for GitHub Pages to update. Then send
one article by its catalog slug:

```bash
uv run python republish.py send proximity-features-privacy-compliant-cold-start-personalization-at-airbnb
```

The command fetches the public page and compares its content fingerprint with
the local build before calling Instapaper. It refuses to send if deployment is
missing or stale.

Credentials are requested interactively. The password prompt is hidden, allows
an empty value for passwordless Instapaper accounts, and neither credential is
stored. Each command sends only the explicitly selected article through the
[Instapaper Simple API](https://www.instapaper.com/developers/v1/simple-api/adding-urls).

Adding an already-saved URL does not create a duplicate; Instapaper marks it
unread and moves it to the top of the list.

## Validation

```bash
uv run python -m unittest discover -v
```

The republishers require network access. arXiv sources must provide semantic
HTML; generic PDF layout reconstruction and OCR are not supported.
