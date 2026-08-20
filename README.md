# Simple Substack republisher

This repository contains a small Substack-focused tool that turns an article into deliberately simple static HTML for testing with Instapaper and Kobo.

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

## Current scope

This first version prioritizes normal Substack article HTML. It fetches the server-rendered response directly and does not use browser automation. It does not convert or rehost images, call the Instapaper API, provide a web UI, or attempt generic extraction from every website.
