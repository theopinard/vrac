# Static article library

This repository turns Substack articles and arXiv papers into deliberately
simple static HTML for Instapaper and Kobo. The generated homepage lists every
source in `articles.json`, and every article links back to that library.

The Substack extractor keeps readable article structure and remote images while
removing the application shell and interactive UI. The arXiv extractor uses
semantic HTML for reading order and the matching PDF to turn SVG figures and
formatted data tables into local baseline RGB JPEGs.

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
