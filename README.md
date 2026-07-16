# book-cover-matcher

Matches a photo of a book's cover against listings on eBay, AbeBooks, Amazon,
and Open Library, using title/author/publisher/edition_year text you fill in
(by scanning with your camera, or typing) before anything is searched — then
lets you manually pick which of the full result list is actually the right
book. Built for books that don't have an ISBN to key off of.

The web app and the CLI fill in that text differently:
- **Web app**: no whole-cover auto-extraction step at all. Each field (Title,
  Author, Publisher, Edition year) has its own **Scan** button that opens a
  live camera preview and runs OCR *continuously, client-side, in the
  browser* (Tesseract.js — no photo is ever taken or sent to the server for
  this) — point your camera at just that bit of text and watch the
  recognized text update live below the video; tap "Use this text" once it
  looks right. This sidesteps a whole class of whole-page heuristic mistakes
  (multi-line titles, imprint lines masquerading as titles, missing "by"
  words) since you're telling it exactly what each snippet is, and it
  sidesteps single-photo framing mistakes (capturing more than the intended
  text) since you can watch the live feed and adjust before accepting.
  Manual typing always works too. One tradeoff: this replaced the earlier
  snap-a-photo mechanism, which supported an LLM-vision option — live
  scanning is Tesseract-only (a per-frame LLM call isn't practical live), so
  the web app no longer offers vision-LLM-assisted field scanning.
- **CLI**: no camera to scan with in a terminal, so it keeps the original
  whole-photo approach — OCR/vision-LLM every photo you pass it and guess the
  four fields out of that combined text (see `pipeline.extract`), then you
  edit the guesses at a text prompt. This is the only place the `llm` backend
  still applies.

## Pipeline

1. **Fill in book info** — see above (scan-per-field on the web, whole-photo
   extraction on the CLI). Nothing gets searched on an unconfirmed guess.
2. **Search & rank** (`pipeline.search_and_rank`) — searches `src/search_ebay.py`
   / `src/search_abebooks.py` / `src/search_amazon.py` / `src/search_openlibrary.py`
   using the confirmed text, then scores every
   candidate three ways:
   - **image similarity** against your single cover photo (perceptual hash
     by default, CLIP embeddings optionally).
   - **text similarity** (`pipeline._text_score`) comparing the candidate's
     title/author against your confirmed title/author — case-insensitive and
     tolerant of non-exact matches (typos, extra/missing words, OCR noise)
     via `difflib.SequenceMatcher`, not a strict string-equality check.
   - **year match** (`pipeline._year_score`) comparing your confirmed
     "Pub. date" against the candidate's own publication date — only the
     year, extracted from whatever format the source uses (`pipeline._extract_year`
     handles a bare year, a full date, or a loosely formatted string like
     Open Library's "Jun 24, 2022") via regex, not a strict format match.
     Only Amazon, AbeBooks (parsed out of its "Published by X, YYYY" text —
     see below), and Open Library currently expose a date to compare
     against; eBay listings don't, so this signal is just left out of the
     blend for them rather than counted as a mismatch.

   Amazon goes through Keepa's API (Amazon itself has no public consumer
   search API); Open Library isn't a marketplace — no price for most
   results, just a free bibliographic reference to cross-check the edition
   against. Results are sorted **Amazon first** (a hard priority, not
   blended in — ASIN makes it the most useful match to land on), then
   within each group by a weighted average of image similarity / text
   similarity / year match (50/30/20 — `pipeline._sort_key`); any signal
   that's unavailable for a given candidate (no listing photo, no year data
   on either side, `match_method="none"`) is left out of the average rather
   than counted as a miss, so lacking a field doesn't unfairly sink a result.
3. **Manual select** — every match from all sources is shown, ranked as
   above; you pick whichever one(s) are actually the right book. No
   automatic filtering — cover-art similarity can't distinguish between two
   printings that share the same cover, so a human call is the last word.

## Setup

```
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Tesseract also needs its system binary installed separately (OCR backend):
https://github.com/UB-Mannheim/tesseract/wiki (Windows installer), or on
Windows via `winget install UB-Mannheim.TesseractOCR`. If it's not resolving
on PATH right after install (common — Windows only refreshes PATH for new
sessions), `src/extract.py` falls back to the default install location
(`C:\Program Files\Tesseract-OCR\tesseract.exe`) automatically.

Copy `.env.example` to `.env` and fill in:
- `EBAY_CLIENT_ID` / `EBAY_CLIENT_SECRET` — from an eBay developer account
  (https://developer.ebay.com/my/keys), free tier is enough for search.
- `ANTHROPIC_API_KEY` — only needed if you use `--backend llm` for extraction.
- `KEEPA_API_KEY` — from https://keepa.com/#!api, needed for Amazon search.

Open Library needs no key at all — it's a free, keyless public API.

## Usage

### CLI

```
python -m src.pipeline path\to\cover.jpg path\to\spine.jpg path\to\copyright_page.jpg
```

The first path is always treated as the cover (used for image-similarity
matching); any others are extra photos used only for text extraction. You'll
be prompted to edit the extracted title/author/publisher/edition_year, then
shown every match found and asked which number(s) to select.

Options:
- `--backend {tesseract,llm}` — text extraction method (default: tesseract)
- `--match-method {phash,clip,none}` — image comparison method (default: phash).
  `none` skips image comparison entirely — no listing photos get downloaded,
  results just keep their original per-source order, similarity shows as n/a.
  Faster, and useful when you don't have/trust a clean cover photo to compare
  against and would rather just judge matches by text yourself.
- `--limit N` — max results per source

### Web app

```
python app.py
```

Then open http://127.0.0.1:5000:
1. Upload the book cover (single photo — used for image-similarity matching,
   required).
2. For each of Title/Author/Publisher/Edition year: tap **Scan**, allow
   camera access, and point your camera at just that bit of text. Recognized
   text updates live underneath the video every ~1.2s; once it looks right,
   tap "Use this text" to fill the field and close the camera. Or just type
   the value directly — no camera needed. Pick a match method, then Search.
3. Browse every match from all sources (thumbnail, author, publisher,
   binding, pub. date, ASIN when it's an Amazon listing, similarity score)
   and click Select on whichever one(s) are correct. "Edit info & search
   again" goes back to step 2 (pre-filled with your current info and cover —
   only upload a new cover photo there if you want to replace it) if you
   want to retry with different text.

This is a single-user local tool: job state lives in memory, not a database,
so it resets if you restart the server. Fine for working through books one at
a time; not meant to run unattended or be exposed beyond localhost.

### Deploying so it's reachable without your computer on

The web app (not the CLI) can run on a free-tier host via the included
`Dockerfile` / `requirements-docker.txt`. Only the web app is deployed this
way — Tesseract/anthropic/torch are excluded from the Docker image entirely,
since the live camera scan runs client-side (Tesseract.js) and the CLI's
whole-photo extraction isn't part of what's hosted.

1. Push this repo to GitHub (a git repo + initial commit are already set up
   locally — `.env` is gitignored, so your keys won't be committed):
   ```
   git remote add origin https://github.com/<you>/<repo>.git
   git push -u origin main
   ```
2. Create a free account at [render.com](https://render.com) (no credit card
   needed for the free tier) and create a new **Web Service**, connecting it
   to that GitHub repo. Render will detect the `Dockerfile` automatically.
3. Under the service's **Environment** settings, add the same variables from
   your local `.env` (`EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`, `KEEPA_API_KEY`,
   `EBAY_VERIFICATION_TOKEN`, `EBAY_NOTIFICATION_ENDPOINT_URL` — update the
   eBay one to Render's URL once you have it, same re-verification as the
   ngrok→Render URL change) — `ANTHROPIC_API_KEY` isn't needed, the hosted
   web app never uses it.
4. Deploy. Render gives you a permanent `https://<name>.onrender.com` URL.

Two things carry over from local behavior, now on Render's schedule instead
of yours: **job state still resets on restart** (free tier "sleeps" after
inactivity and wakes on the next request, wiping in-memory `JOBS` — same
tradeoff as restarting the local server, just automatic) and **uploaded
photos are not persisted storage** (same lifecycle as the jobs that
reference them, so nothing goes stale/orphaned — just don't expect a job to
survive a sleep cycle). `gunicorn` is deliberately run with `--workers 1` in
the Dockerfile — `JOBS` is a plain in-memory dict, so multiple worker
*processes* would each hold a separate copy and randomly not see each
other's jobs; don't raise the worker count without also moving job state to
something shared (Redis, a database, ...).

## Known limitations

- **AbeBooks has no public API** for general use (only for approved
  booksellers), so `search_abebooks.py` scrapes their search-results HTML.
  Their markup already changed once (from `data-cy` to `data-test-id`
  attributes) since this was first written, so treat the current selectors
  as similarly temporary — check `src/search_abebooks.py` first if AbeBooks
  results stop showing up. It also appears to soft-throttle (serve a normal
  200 response with zero listings, no CAPTCHA) after many rapid requests from
  the same source — if results disappear during heavy testing, that's likely
  why; spacing out requests should resolve it. If it becomes a bottleneck, an
  aggregator like bookfinder.com or vialibri.net (both index AbeBooks) may be
  a sturdier target. Also: don't pass the author both inside the free-text
  query and as AbeBooks' dedicated author field — doing so over-constrains
  the search and reliably returns zero results (already handled in
  `search_and_rank`, but worth knowing if you touch that code) — the same
  over-constraining risk applies to cramming edition_year into the free-text
  query too, since AbeBooks' keyword search appears to require something
  close to an AND-match across all terms.
- **AbeBooks' publisher/year come from a text field, not a dedicated one** —
  listing cards have no separate "published" field, but the publisher line
  reads "Published by X, YYYY" (confirmed against live listings), which
  `search_abebooks.py` parses with a regex. Non-greedy + end-anchored so it
  correctly finds the *last* ", YYYY" even when the publisher name itself
  has commas (e.g. "Penguin Books, Limited, 2001" → publisher "Penguin
  Books, Limited", year "2001") — but it's still scraped free text, so an
  unusual listing (no year at all, a range, ...) will just fail to match
  and both fields come back `None` rather than something wrong.
- **EXIF orientation is corrected before any processing** (`extract.load_oriented_image`,
  used by OCR, the LLM vision path, and `match._load_image`). Phone photos
  routinely carry an EXIF tag saying "rotate 90/180/270 to display upright"
  that PIL does not apply automatically — browsers do this for `<img>` tags,
  which is why an un-rotated photo looks fine in the web UI but turned into
  complete OCR garbage before this was fixed (confirmed: a real sideways
  photo produced `'AOdladLVd'` from Tesseract; after the fix, the same photo
  read correctly). If a fresh bug ever produces OCR/matching garbage again,
  check orientation handling first — it's an easy regression to reintroduce.
- **Tesseract OCR** struggles with stylized cover fonts. On the **CLI's**
  whole-photo path, its title/author/publisher/edition_year fields are
  pattern-matched guesses, not structured extraction (see
  `_guess_title`/`_guess_author`/`_guess_publisher` in `src/extract.py`).
  Author (`by X` lines), publisher (`Published by X` / name +
  Press/Books/Publishing/House/Sons), and edition_year (prefers a year next
  to "this edition" wording, then "first edition"/"first printing", then a
  generic last-4-digits fallback) are reasonably reliable. **Title is the
  weakest of the four** — it's just "the longest line near the top of the
  cover photo," which is often right but will occasionally grab an
  imprint/series line (e.g. "PENGUIN CLASSICS") instead of the actual title.
  The **web app's** per-field live Scan sidesteps this whole class of
  mistakes — since you're pointing your camera at just the title (or
  author, ...), there's no multi-field guessing to get wrong, only plain OCR
  of whatever's in frame; it also lets you correct framing in real time
  before accepting, rather than committing to one photo that might have
  grabbed extra text. On the CLI, the `llm` backend reads stylized fonts
  more reliably than Tesseract, at the cost of an API key and a small
  per-call charge — the web app's live scan is Tesseract-only, per the
  tradeoff noted above.
- **The web app's live Scan needs Tesseract.js** (loaded from a CDN,
  `templates/upload.html`) — first use per page load downloads the OCR
  engine + English language data (a few MB), so there's a short "Loading OCR
  engine…" pause. It also needs camera permission and a secure context
  (HTTPS or localhost — the ngrok tunnel or `http://localhost:5000` both
  qualify, a plain `http://<lan-ip>:5000` from another device on your
  network will not). Recognition re-runs on a fresh frame roughly every
  1.2s (`setInterval` in the scan script) — a deliberate throttle so it
  doesn't queue up recognition calls faster than a phone can process them;
  lower it if you're on a fast device and want snappier updates.
- **phash** only catches near-identical images (same scan/printing). If
  listing photos are differently lit/cropped/angled versions of the same
  cover, use `--match-method clip` instead (slower, needs `torch`+`open_clip`).
- **Keepa's schema** in `src/search_amazon.py` is now confirmed against a
  live account (an earlier version was guessed from docs and had real bugs:
  `/search` returns `products` directly rather than an `asinList`, images
  come as a list of dicts under `images`, not a CSV string under
  `imagesCSV`, `publisher` is frequently null — `brand` is used as a
  fallback — and `publicationDate` is a raw `YYYYMMDD` int with `0` meaning
  "no data," not a real date; `_format_keepa_date` pulls out just the year).
  Keepa can still change their API in the future; re-verify field names
  against a live response if Amazon results silently go to zero.
- **Open Library search is work-level, not edition-level** — `/search.json`
  aggregates every publisher/printing of a book into one blended record,
  which is useless for identifying a specific edition. `search_openlibrary.py`
  works around this with a second call to each matching work's
  `/editions.json` to get real per-edition publisher/date/cover data, at the
  cost of more HTTP calls per search (capped at 5 works × 3 editions each).
  It's a reference source, not a marketplace — no price, no "buy" link.
- **Alibris is not integrated.** It sits behind a Cloudflare bot-challenge
  ("Just a moment..." JS challenge page) rather than serving plain HTML like
  AbeBooks does — a scraper here would need to defeat that challenge, which
  is bot-detection bypass and out of scope regardless of how it's approached.
  If Alibris coverage matters, the realistic paths are an official
  partner/affiliate API (if Alibris still offers one — unconfirmed) or an
  aggregator like bookfinder.com/vialibri.net that may already index it.
