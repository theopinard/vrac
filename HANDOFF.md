# Kobo + Instapaper article republisher handoff

## Purpose of this document

This is the working context for a follow-up session. It combines the earlier ChatGPT investigation with the implementation and experiments performed in Codex.

The project began by debugging why diagrams from complex Substack/ByteByteGo articles became broken-image icons on a Kobo Clara after the article was saved through Instapaper. It now has two working republishing pipelines: one for Substack articles and one for arXiv papers with semantic HTML. Both have been validated through the full GitHub Pages → Instapaper → Kobo Clara path.

## Current status at a glance

- Repository: `theopinard/vrac`
- Local repository: `/home/theodore/github/vrac`
- GitHub Pages root: <https://theopinard.github.io/vrac/>
- Substack validation source: <https://blog.bytebytego.com/p/the-new-american-ai-model-designed>
- Clean generated Substack article: <https://theopinard.github.io/vrac/articles/the-new-american-ai-model-designed/>
- arXiv validation source: <https://arxiv.org/abs/2607.12246v1>
- Clean generated arXiv article: <https://theopinard.github.io/vrac/articles/proximity-features-privacy-compliant-cold-start-personalization-at-airbnb/>
- arXiv citation-compatibility source: <https://arxiv.org/abs/2607.23749v1>
- Citation-compatibility article: <https://theopinard.github.io/vrac/articles/breaking-the-loop-an-empirical-comparison-of-strategies-for-novelty-and-freshness-in-youtube-music/>
- Current arXiv implementation commit: `af525e1` (`Use simple citation links for Kobo`)
- GitHub Pages publishes the repository root from `origin/main`.
- Local checkout is currently on `add-instapaper-image-tag`. This branch contains the latest commits and points at the same commit as `origin/main`; the local `main` branch is stale at the initial commit.
- The latest Substack revision is published, HTTP-verified, and Kobo-validated: all article pictures render successfully.
- The Substack strategy is clean static HTML, bare `<img>` elements for uncaptained images, and full-resolution enclosing Substack image-link URLs for real diagrams.
- The arXiv strategy is semantic arXiv HTML plus figures/tables cropped from the matching PDF and encoded as baseline, non-progressive RGB JPEGs.
- The final arXiv page contains six direct, absolute JPEG image tags: three diagrams and three formatted tables. This version is Kobo-validated.
- Current arXiv HTML with nested layout tables is supported: only the outer data table is rasterized.
- LaTeXML bibliographies are converted to ordinary numbered paragraphs, and citation targets are remapped to stable generated IDs.
- Citation text and parentheses now survive Kobo because `<cite>` wrappers are removed. Links remain ordinary absolute `<a>` elements and display correctly, but Kobo's Instapaper "My Articles" reader does not activate them when tapped.

## Original problem

On the Kobo Clara, after saving the original ByteByteGo Substack article to Instapaper:

- article text renders correctly;
- a sponsored image near the top renders correctly;
- most or all later technical diagrams appear as a small broken-image icon;
- the same saved article and images look correct in Instapaper on the web.

The original candidate explanations included WebP/AVIF negotiation, large PNG dimensions, transparency, CDN access, image count, cumulative payload, and complex surrounding Substack markup.

One representative broken image was this Instapaper-generated tag:

```html
<img
  src="https://substackcdn.com/image/fetch/$s_!Btgc!,f_auto,q_auto:good,fl_progressive:steep/https%3A%2F%2Fsubstack-post-media.s3.amazonaws.com%2Fpublic%2Fimages%2F87a3ec64-dd86-41e8-b95f-2efe63b53321_2834x2942.png"
  alt="https://substackcdn.com/image/fetch/$s_!Btgc!,f_auto,q_auto:good,fl_progressive:steep/https%3A%2F%2Fsubstack-post-media.s3.amazonaws.com%2Fpublic%2Fimages%2F87a3ec64-dd86-41e8-b95f-2efe63b53321_2834x2942.png"
  width="797"
  height="827"
  class="shrinkToFit transparent">
```

The underlying source asset is:

```text
https://substack-post-media.s3.amazonaws.com/public/images/87a3ec64-dd86-41e8-b95f-2efe63b53321_2834x2942.png
```

## Diagnostic pages A through I

All tests used the actual path that matters:

```text
GitHub Pages test URL
  -> save URL to Instapaper
  -> sync/open on Kobo Clara
```

| Test | Content | Kobo result | What it established |
| --- | --- | --- | --- |
| A | Original full-size Substack CDN `f_auto` URL | Works | The CDN URL is not inherently unsupported. |
| B | Direct original S3 PNG | Works | The source PNG and direct S3 access are fine. |
| C | White-flattened 1200px RGB JPEG | Works | Conservative conversion is a viable fallback. |
| D | White-flattened 800px RGB JPEG | Works | A smaller conservative image also works. |
| E | Exact Instapaper `<img>` tag, including dimensions/classes | Works | Instapaper's `width`, `height`, `shrinkToFit`, and `transparent` attributes are not the cause. |
| F | Linked Substack-like `<a><picture><img></picture></a>` structure | Works | The full-size link around a resized image does not reproduce the failure. |
| G | `<picture><img></picture>` without the anchor | Works | The anchor is not required for success. |
| H | All nine real diagrams, in article order, using full-resolution Substack URLs | Works | Multiple large original diagrams and cumulative payload work in simple HTML. |
| I | The same nine diagrams/order as 1200px quality-85 RGB JPEGs | Works | Multiple conservative local images also work. |

The main test index is <https://theopinard.github.io/vrac/>. H and I also have direct pages:

- H: <https://theopinard.github.io/vrac/h-original-diagrams.html>
- I: <https://theopinard.github.io/vrac/i-converted-diagrams.html>

## What A through I ruled out

These are no longer good primary hypotheses:

- WebP versus PNG versus JPEG;
- AVIF/content negotiation in general;
- `f_auto` by itself;
- Substack CDN reachability;
- direct S3 reachability;
- PNG transparency by itself;
- individual image dimensions;
- full-resolution images by themselves;
- resized images by themselves;
- Instapaper's generated image attributes/classes;
- a single `<picture>` wrapper;
- a single `<source>`/responsive image structure;
- a single image wrapped by `<a>`;
- Substack's full-size-anchor versus resized-inner-image pattern in isolation;
- multiple images on a simple page;
- a simple total image-count or cumulative-payload limit.

The strongest general conclusion is:

> The same image assets work after Instapaper extracts them from simple static HTML, but fail when Instapaper processes the original complex Substack application page.

That shifted the project from image conversion toward clean article extraction.

## Relevant Substack structure

The real article body is available in the normal server-rendered response. It appears under structures similar to:

```html
<div class="dt-post-body">
  <div class="available-content">
    <div class="body markup">
      ...
    </div>
  </div>
</div>
```

Substack image blocks are typically much more complex:

```html
<div class="captioned-image-container">
  <figure>
    <a href="FULL_RESOLUTION_IMAGE" class="image-link ...">
      <div class="image2-inset">
        <picture>
          <source srcset="...f_webp...">
          <img src="...w_1456,c_limit,f_auto...">
        </picture>
      </div>
    </a>
  </figure>
</div>
```

An important distinction is that the enclosing anchor normally points to the full-resolution CDN form without `w_1456,c_limit`, while the nested image uses a transformed 1456px-wide URL.

## J: the real republisher

The first real tool is [republish.py](republish.py). It is intentionally Substack-focused and does not use browser automation.

Run it with uv:

```bash
uv sync
uv run python republish.py "https://blog.bytebytego.com/p/the-new-american-ai-model-designed"
```

It writes:

```text
articles/the-new-american-ai-model-designed/index.html
```

The current implementation:

1. Fetches the normal HTML response with `requests`.
2. Extracts title, author, publication, and date from normal metadata with JSON-LD fallbacks.
3. Finds a substantial article body using ordered Substack selectors and broader fallbacks.
4. Removes scripts, interactive elements, Substack UI, recommendation widgets, hidden UI, and application chrome.
5. Preserves semantic reading content: headings, paragraphs, lists, blockquotes, pre/code, links, tables, emphasis, and horizontal rules.
6. Resolves relative links against the original article URL.
7. Removes `srcset`, `<source>`, `<picture>`, loading attributes, JavaScript, and Substack data attributes.
8. Emits deliberately boring static HTML with reader-friendly CSS.
9. Does not download, resize, convert, or rehost images.

Dependency management is uv-only:

- `pyproject.toml`
- `uv.lock`
- `beautifulsoup4==4.13.4`
- `requests==2.32.4`

There is intentionally no `requirements.txt`.

## J extraction validation

The generated article was compared with the extracted source body:

- source body: about 4,216 words;
- generated article: about 4,235 words, with the small increase explained by the clean title/byline/source header;
- 12 article `h2` headings plus the page `h1`;
- 139+ paragraphs;
- 15 lists;
- all references, including the final twelfth reference;
- 11 in-body images: nine real diagrams and two sponsored images;
- sponsored content was deliberately retained because it is inside the article body;
- no scripts, `<picture>`, `<source>`, `srcset`, lazy-loading attributes, forms, buttons, iframes, or Substack application data attributes.

The live Pages file was downloaded and compared byte-for-byte with the generated local file after each published revision.

## J Kobo results and subsequent changes

### J revision 1: simple article with `<figure>` around every image

Commits:

- `9a9e088` — `Add Substack article republisher`
- `df6b026` — `Keep generated HTML whitespace clean`

Behavior:

- Every source figure became `<figure><img></figure>`, even without a caption.
- Images used the nested source `<img src>`, normally the `w_1456,c_limit` CDN form.

Kobo result:

- The first/sponsored image rendered.
- Later technical diagrams were broken.

This showed that cleaning the whole article was not sufficient by itself.

### J revision 2: bare images when there is no caption

Commit:

- `80c0a8b` — `Simplify uncaptained article images`

Behavior:

- An image without a real caption becomes a bare `<img>`.
- `<figure><img><figcaption>` is retained only when a non-empty caption exists.
- The ByteByteGo J article has no real captions, so it now contains 11 bare image tags and zero figure wrappers.

Kobo result:

- Rendering improved substantially.
- One specific remaining failure was reported: the image immediately after the sentence ending, “The trick is where the bias gets applied.”
- No other remaining broken image was identified in that report, so the investigation narrowed to this specific asset/URL form rather than the earlier all-later-images pattern.

The remaining failing diagram was “Load Balancing Experts”:

```text
UUID: 7f9dbcb8-842e-4e86-b5c5-b457f94b0447_3654x1536.png
```

J was using this transformed source:

```text
https://substackcdn.com/image/fetch/$s_!dEzn!,w_1456,c_limit,f_auto,q_auto:good,fl_progressive:steep/https%3A%2F%2Fsubstack-post-media.s3.amazonaws.com%2Fpublic%2Fimages%2F7f9dbcb8-842e-4e86-b5c5-b457f94b0447_3654x1536.png
```

Live diagnostics showed:

- transformed variant: 1456×612, sRGB, non-interlaced, 8-bit RGBA, 202,522 bytes;
- full-resolution variant: 3654×1536, sRGB, non-interlaced, 8-bit indexed-color PNG, 102,829 bytes.

The transformed variant was unexpectedly almost twice the byte size despite its smaller dimensions. However, the following working transformed image was also RGBA, so alpha/transparency alone is not a sufficient explanation.

### J revision 3: prefer full-resolution Substack image links

Commit:

- `7c7ee5a` — `Prefer full-resolution Substack image links`

Current behavior:

- When a figure's enclosing link points to a recognized Substack image asset (`substackcdn.com/image/fetch/...` or the direct `substack-post-media` S3 image path), that link becomes the clean image `src`.
- When an enclosing link is external, as with sponsored content, the tool keeps the nested image's `src` rather than accidentally using the advertiser landing page as an image.
- All nine real diagrams now use the full-resolution Substack URLs proven to work on H.
- The two sponsored images retain their original `w_1456,c_limit` nested sources.
- The J output still contains 11 bare images and zero figure wrappers.

The exact previously failing location now contains:

```html
<p>
  ...The trick is where the bias gets applied.
</p>
<img
  alt=""
  src="https://substackcdn.com/image/fetch/$s_!dEzn!,f_auto,q_auto:good,fl_progressive:steep/https%3A%2F%2Fsubstack-post-media.s3.amazonaws.com%2Fpublic%2Fimages%2F7f9dbcb8-842e-4e86-b5c5-b457f94b0447_3654x1536.png">
```

GitHub Pages successfully built `7c7ee5a`, and the public file was verified to contain nine full-resolution real-diagram URLs.

Kobo result:

- **Success: every picture in the generated article renders on the Kobo Clara through Instapaper.**
- This includes the previously failing “Load Balancing Experts” diagram after “The trick is where the bias gets applied.”
- Revision `7c7ee5a` is therefore the confirmed working baseline.

The practical workaround is now proven end-to-end:

```text
Original Substack article
  -> republish.py
  -> clean static GitHub Pages article
  -> save to Instapaper
  -> sync to Kobo Clara
  -> complete text and images render successfully
```

The experiments support an interaction between Instapaper's extraction of the complex original Substack document, repeated image containers, and the transformed nested image sources. They do not establish one single internal Kobo or Instapaper defect with certainty. The important engineering result is that the current normalizer removes the problematic combination without converting or rehosting images.

## arXiv republisher

The second tool is [republish_arxiv.py](republish_arxiv.py). It is separate from the Substack extractor because the source and image pipelines are fundamentally different.

Run it with an arXiv abstract, HTML, or PDF URL:

```bash
uv sync
uv run python republish_arxiv.py "https://arxiv.org/abs/2607.12246v1"
```

An unversioned URL is resolved and pinned to the latest available version for that run. The tool accepts modern and legacy arXiv identifiers, rejects non-arXiv hosts, and requires arXiv semantic HTML. It deliberately fails rather than attempting generic PDF layout reconstruction when semantic HTML is unavailable.

For the validation paper it writes:

```text
articles/proximity-features-privacy-compliant-cold-start-personalization-at-airbnb/
  index.html
  figure-1.jpg
  figure-2.jpg
  figure-3.jpg
  table-1.jpg
  table-2.jpg
  table-3.jpg
```

The implementation:

1. Normalizes and versions the input arXiv URL.
2. Fetches the matching semantic HTML and PDF revision.
3. Uses the semantic HTML for title, all authors, abstract, sections, paragraphs, lists, links, citations, algorithms, captions, references, and reading order.
4. Locates each inline SVG figure in the exact PDF using its numbered caption, text labels, column bounds, and vector-drawing bounds.
5. Locates each real data table in the PDF using its caption and cell labels.
6. Renders diagram/table crops on white at no more than 1199px width.
7. Encodes every crop as 8-bit RGB JFIF baseline JPEG, quality 88, non-progressive.
8. Replaces presentational equation tables with simple MathML blocks.
9. Emits direct `<figure><img ...><figcaption>...</figcaption></figure>` structures without nested arXiv spans/divs around images.
10. Uses absolute GitHub Pages image URLs, with explicit width and height. Forks can override the default base using `--public-articles-url`.
11. Removes scripts, SVG, HTML data tables, `<picture>`, `<source>`, `srcset`, and lazy-loading markup from the output.
12. Selects only outer data tables for PDF rasterization; nested tables used by current arXiv HTML to lay out cells are replaced with their outer table in one operation.
13. Converts LaTeXML bibliography lists to numbered paragraphs, removes navigation-only "Cited by" backlinks, and preserves external DOI/source links.
14. Removes `<cite>` wrappers while keeping their parentheses, citation text, and simple absolute `<a>` links.
15. Downloads the source PDF only into temporary storage; it is not committed.

arXiv support added these dependencies to the uv project:

- `pymupdf==1.26.4` for PDF inspection and rasterization;
- `pillow==11.3.0` for explicit baseline/non-progressive JPEG encoding.

There is still intentionally no `requirements.txt`.

## arXiv validation paper and results

Validation source:

<https://arxiv.org/abs/2607.12246v1>

Title:

```text
Proximity Features: Privacy-Compliant Cold-Start Personalization at Airbnb
```

Published page:

<https://theopinard.github.io/vrac/articles/proximity-features-privacy-compliant-cold-start-personalization-at-airbnb/>

The original is an untagged, two-column, eight-page PDF. Its text is extractable, but `pdfimages` finds no raster images: its three diagrams/charts are PDF vector drawing commands. arXiv's semantic HTML contains exactly three article SVGs, semantic tables/algorithms/references, and MathML expressions.

The final generated page retains the complete readable paper through References. It contains:

- three diagram JPEGs;
- three formatted data-table JPEGs;
- zero SVG elements;
- zero HTML `<table>` elements;
- zero `<picture>`, `<source>`, `srcset`, scripts, or lazy-loading attributes;
- 94 retained MathML expressions outside the rasterized figure/table contents;
- six direct image children of semantic `<figure>` elements;
- absolute public image URLs and explicit image dimensions.

The final assets are:

| Asset | Dimensions | Encoding |
| --- | ---: | --- |
| `figure-1.jpg` | 1199×651 | RGB baseline JPEG, quality 88 |
| `figure-2.jpg` | 994×545 | RGB baseline JPEG, quality 88 |
| `figure-3.jpg` | 1001×435 | RGB baseline JPEG, quality 88 |
| `table-1.jpg` | 677×278 | RGB baseline JPEG, quality 88 |
| `table-2.jpg` | 909×341 | RGB baseline JPEG, quality 88 |
| `table-3.jpg` | 581×258 | RGB baseline JPEG, quality 88 |

All public HTML/image files were downloaded after deployment and compared byte-for-byte with their committed local versions.

## Current arXiv references and citations

The second arXiv validation source is:

<https://arxiv.org/abs/2607.23749v1>

Title:

```text
Breaking the Loop: An Empirical Comparison of Strategies for Novelty and Freshness in YouTube Music
```

Published page:

<https://theopinard.github.io/vrac/articles/breaking-the-loop-an-empirical-comparison-of-strategies-for-novelty-and-freshness-in-youtube-music/>

This paper exposed two newer LaTeXML patterns:

- one semantic data table contains 48 nested layout tables, so the previous renderer attempted to process 49 tables and failed after replacing the outer one;
- every bibliography entry contains navigation-only "Cited by" backlinks, producing a link-heavy `<section id="bib"><ul><li>...</li></ul></section>` block that reader extraction can discard as navigation or boilerplate.

The converter now renders only top-level data tables and rewrites the bibliography as 29 ordinary numbered paragraphs. It removes the "Cited by" blocks, remaps every in-text citation to the new reference ID, and retains real external links.

The citation investigation then established that URL form was not the main cause of missing citation text. The failing markup was:

```html
<cite>(<a href="#reference-14">Jiang et al., 2019</a>)</cite>
```

On Kobo, the complete `<cite>` node was removed, including the linked author/year text and the parentheses. Making the nested link absolute did not help because the `<cite>` wrapper remained.

The useful comparison page was:

<https://vladfeinberg.com/2026/05/10/how-to-land-a-job-at-a-frontier-lab.html>

Its article body has zero `<cite>` elements. Links use ordinary prose markup such as `<p>...<a href="https://...">text</a>...</p>`. The arXiv output now follows that pattern:

```html
<p>
  The mechanism that produces this failure is well understood in the literature
  (<a href="https://theopinard.github.io/.../#reference-14">Jiang et al., 2019</a>).
</p>
```

Confirmed result on Kobo:

- `(Jiang et al., 2019)` and all other citation text remain visible;
- the parentheses remain visible;
- all 29 numbered reference paragraphs remain visible;
- the linked text remains present, but tapping it does nothing in Kobo's Instapaper "My Articles" reader.

The last point is an integration limitation, not a remaining source-HTML failure. The live page contains 57 normal absolute links, zero fragment-only links, and zero `<cite>` elements. Kobo and Instapaper document syncing, reading, liking, archiving, and deleting articles, but do not document interactive hyperlink navigation in "My Articles":

- <https://www.instapaper.com/docs/ereaders/kobo>
- <https://help.kobo.com/hc/en-us/articles/33359968957463-Use-Instapaper-with-your-Kobo-eReader>

If clickable references become a requirement, the practical next path is a sideloaded EPUB rather than further HTML changes. For the current Instapaper workflow, citations and references must remain understandable without clicking.

The relevant published commits are:

- `0f30e1d` — `Preserve arXiv references for Kobo`: flatten bibliography lists and ignore nested layout tables;
- `664fb72` — `Make arXiv links absolute for Kobo`: useful negative test because absolute URLs alone did not preserve `<cite>` contents;
- `a3bfdf8` — `Preserve citation text on Kobo`: confirmed that plain citation text survives;
- `af525e1` — `Use simple citation links for Kobo`: retain simple links without `<cite>` wrappers.

## arXiv failure sequence and confirmed fix

### Initial arXiv version

Commit:

- `5132718` — `Add arXiv paper republisher`

Behavior:

- PDF vector figures were rasterized to local 8-bit RGB, non-interlaced PNGs.
- The article retained complex LaTeXML wrappers around the images.
- Data tables remained semantic HTML tables.

Kobo result:

- All pictures were broken.
- Tables appeared as unformatted/raw cell text.

### Simplified figures and rasterized tables

Commit:

- `9995a20` — `Simplify arXiv figures and rasterize tables`

Behavior:

- Diagram images became direct children of `<figure>`.
- The three data tables were cropped from the PDF and converted to PNG images.
- Equation-layout tables became simple math blocks.
- No SVG or HTML data tables remained.

Kobo result:

- All pictures were still broken.

This ruled out the remaining LaTeXML image wrappers and raw HTML table formatting as sufficient explanations.

### Absolute image URLs and dimensions

Commit:

- `109de40` — `Use absolute arXiv image URLs`

Behavior:

- All six images used complete `https://theopinard.github.io/...` URLs.
- All `<img>` tags included explicit width and height.
- Markup remained direct and minimal.

Kobo result:

- All pictures were still broken.

This ruled out relative URL resolution as the primary cause.

### Baseline JPEG conversion — confirmed success

Commit:

- `f4ad6d6` — `Convert arXiv assets to baseline JPEG`

Behavior:

- All six generated PNGs were replaced by JFIF baseline JPEGs.
- Encoding is 8-bit RGB, quality 88, non-progressive, with a white background.
- PyMuPDF's direct JPEG writer was not used because inspection showed that it emitted progressive JPEGs. Pillow is used with `progressive=False` and `optimize=False` to guarantee the required profile.
- Absolute URLs, explicit dimensions, and direct figure/image markup were retained.

Kobo result:

- **Success: every diagram and formatted table renders through Instapaper on the Kobo Clara.**

This is the strongest controlled conclusion for the arXiv path because the immediately preceding absolute/direct PNG version failed and the baseline JPEG version succeeded. It does not mean that Kobo rejects every PNG—the earlier Substack diagnostics include working PNGs. It establishes that conservative baseline JPEG is the reliable output format for PDF-derived arXiv figures and tables in this workflow.

The working arXiv path is therefore:

```text
arXiv URL
  -> resolve exact version
  -> semantic arXiv HTML for reading structure
  -> matching PDF for diagram/table crops
  -> baseline RGB JPEG assets
  -> clean static GitHub Pages article
  -> save fresh URL to Instapaper
  -> sync to Kobo Clara
  -> complete text, diagrams, and formatted tables render
```

The test suite currently has 13 tests covering arXiv URL forms/version handling, invalid input, direct visual markup, equation-table simplification, generated image URLs/dimensions, nested data-table selection, bibliography flattening/link preservation, and `<cite>` removal with simple absolute links.

## The nine real diagrams in order

| # | Diagram | Source asset UUID/dimensions |
| --- | --- | --- |
| 1 | One Token's Path Through Inkling | `87a3ec64-dd86-41e8-b95f-2efe63b53321_2834x2942.png` |
| 2 | Mixture of Experts | `a1d2fc17-29cf-4f2f-901c-54590d0c22cc_2834x2324.png` |
| 3 | Load Balancing Experts | `7f9dbcb8-842e-4e86-b5c5-b457f94b0447_3654x1536.png` |
| 4 | Sigmoid Scores for Experts | `decf4d2f-7ba4-44c3-b40c-2feefed68ee2_2280x1116.png` |
| 5 | Attention Layers | `8e9cb247-2d0b-4f03-9b09-e3560ec015ea_3292x1938.png` |
| 6 | Position Encoding | `4c2d7ec9-2491-4cf9-86fe-a75b3ac8ac84_3024x1536.png` |
| 7 | Four Short Convolutions in Every Layer | `c05ab48f-6b5b-4d1e-abbd-3e3bf500507e_2624x1680.png` |
| 8 | Multimodal Input | `075f9d40-31ba-40ac-81b6-4295735cf4a7_2998x2026.png` |
| 9 | Thinking Effort and Cost | `36881c12-5c86-4d0f-9645-b92eec74296f_3470x2286.png` |

The two retained sponsored image assets are:

- `eff39c80-5839-42a4-a2f5-72bf84468c68_1598x840.png`
- `fd8c0c4e-3545-4931-872c-7d1ef959acdd_1600x840.png`

## Recommended next step

Preserve `7c7ee5a` as the known-good baseline and run `republish.py` against one or more additional Substack articles before adding automation. The next validation should check that:

1. titles, metadata, text, headings, lists, links, captions, and images remain complete;
2. real images use their full-resolution Substack image-link URLs where available;
3. sponsored external links do not become image sources;
4. uncaptained images remain bare `<img>` elements;
5. a freshly saved Instapaper copy renders all pictures on Kobo.

When retesting a regenerated URL, remember that Instapaper may cache an already-saved article. Remove/re-save it or otherwise force a fresh extraction before judging a new revision.

Only after the extractor succeeds on a small variety of Substack articles should the project consider automating GitHub publication or Instapaper submission.

## Scope and constraints to preserve

For now:

- do not download or rehost images;
- do not resize or convert images unless a new controlled experiment specifically requires it;
- do not add an Instapaper API integration;
- do not add a browser extension, bookmarklet, or web UI;
- do not add GitHub Actions automation;
- do not add Playwright/browser rendering unless the normal Substack HTML response stops containing the article;
- keep the extractor small and Substack-focused;
- keep generated HTML simple, static, semantic, and JavaScript-free.

The target workflow remains:

```text
Original Substack URL
  -> extract server-rendered article body
  -> sanitize to simple static HTML
  -> publish under GitHub Pages
  -> save clean URL to Instapaper
  -> sync to Kobo Clara
```

Only after this end-to-end path is reliable should the project consider automatic publishing or automatic Instapaper submission.
