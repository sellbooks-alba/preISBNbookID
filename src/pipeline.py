import argparse
import difflib
import re
from concurrent.futures import ThreadPoolExecutor

from . import (
    match,
    search_abebooks,
    search_amazon,
    search_ebay,
    search_openlibrary,
)
from .extract import extract_book_info


def extract(cover_path, other_paths=None, backend="tesseract"):
    """Extraction uses every photo you have (cover + spine/title/copyright
    page/...) — more text to OCR/reason over only helps."""
    other_paths = list(other_paths or [])
    return extract_book_info([cover_path] + other_paths, backend=backend)


def build_query(info, fields=("title", "author", "publisher")):
    """edition_year is deliberately excluded from every search query — confirmed
    live that Amazon/Keepa and Open Library both return zero results the
    moment a bare year gets folded into their free-text search (same
    over-constraining behavior already documented for AbeBooks+author).
    edition_year still matters for ranking (pipeline._year_score), just not
    for narrowing the query text — none of these search engines have a
    reliable way to receive "and also matches this year" as a keyword.
    """
    parts = [info.get(f) for f in fields if info.get(f)]
    return " ".join(parts) if parts else (info.get("raw_text") or "")[:80]


def search_and_rank(cover_path, info, match_method="phash", limit=15):
    """Search runs only against the confirmed title/author/publisher text
    (see build_query's docstring on why edition_year is excluded), and
    similarity is scored only against the single designated cover photo —
    the other photos (spine, copyright page, ...) were only ever useful for
    extraction, not for visually matching a listing's photo.

    match_method="none" skips image comparison entirely (no listing photos
    get downloaded at all) — useful when you'd rather just eyeball text
    matches yourself, or don't have/trust a clean cover photo to compare
    against. Every result gets similarity=None and keeps its original
    per-source order instead of being ranked.
    """
    query = build_query(info)
    if not query.strip():
        raise ValueError("Not enough info to build a search query — fill in at least a title or author")

    # AbeBooks has a dedicated author field ("an") — passing author again
    # inside the keyword text on top of that over-constrains the search and
    # reliably returns zero results.
    abebooks_query = build_query(info, fields=("title", "publisher"))

    # The 4 sources are independent network calls with no shared state —
    # running them one after another just adds up their latencies for no
    # reason. In parallel, total time is roughly the slowest single source
    # instead of the sum of all four.
    source_calls = [
        ("ebay", lambda: search_ebay.search(query, limit=limit)),
        # delay=0: the built-in "be polite between calls" pause in
        # search_abebooks.search only matters when looping over many queries
        # back-to-back; it's just dead time on a single user-triggered search.
        ("abebooks", lambda: search_abebooks.search(abebooks_query, author=info.get("author"), limit=limit, delay=0)),
        ("amazon", lambda: search_amazon.search(query, limit=limit)),
        ("openlibrary", lambda: search_openlibrary.search(query, limit=limit)),
    ]

    candidates = []
    with ThreadPoolExecutor(max_workers=len(source_calls)) as pool:
        futures = {pool.submit(call): name for name, call in source_calls}
        for future in futures:
            name = futures[future]
            try:
                candidates += future.result()
            except Exception as e:
                print(f"[{name}] search failed: {e}")

    if match_method == "none":
        scored = [{**c, "similarity": None} for c in candidates]
    else:
        # Decode the reference cover once and reuse it for every candidate —
        # re-decoding the same file per-candidate (up to ~60x for a full
        # multi-source search) is exactly what overloaded Render's free tier
        # and got the worker SIGKILLed. See match.load_reference.
        try:
            reference = match.load_reference(cover_path)
        except Exception as e:
            raise ValueError(f"Could not read the cover photo: {e}")

        def _score(c):
            if not c.get("image_url"):
                return {**c, "similarity": None}
            try:
                score = match.compare(reference, c["image_url"], method=match_method)
            except Exception as e:
                score = None
                print(f"[match] failed comparing {c.get('url')}: {e}")
            return {**c, "similarity": score}

        # Each comparison downloads a remote image (network-bound) and
        # decodes it (Pillow releases the GIL during the actual C-level
        # decode) — threads give real speedup on both. Capped at 5, not
        # "as many as candidates," to keep peak memory bounded on a
        # constrained host — several full-size images decoded at once is
        # exactly the kind of load that caused the original OOM.
        with ThreadPoolExecutor(max_workers=5) as pool:
            scored = list(pool.map(_score, candidates))

    for c in scored:
        c["text_score"] = _text_score(info, c)
        c["year_score"] = _year_score(info, c)

    scored.sort(key=_sort_key)
    return scored


def _text_similarity(a, b):
    """Case-insensitive, tolerant of non-exact matches (typos, extra/missing
    words, OCR noise) — a plain substring/equality check would reject good
    matches over trivial differences."""
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _text_score(info, candidate):
    query_text = " ".join(filter(None, [info.get("title"), info.get("author")]))
    candidate_text = " ".join(filter(None, [candidate.get("title"), candidate.get("author")]))
    return _text_similarity(query_text, candidate_text)


_YEAR_RE = re.compile(r"\b(1[89]\d{2}|20[0-3]\d)\b")


def _extract_year(text):
    """Pulls a plain 4-digit year out of whatever format a source happens to
    use — a bare year, a full date, or a loosely formatted string (Open
    Library's publish_date alone shows up as "2020", "Jun 24, 2022", and
    "1951-01-01" depending on the edition). Takes the last match, same
    convention as extract._guess_year, in case a string has more than one
    number that looks like a year."""
    if not text:
        return None
    matches = _YEAR_RE.findall(str(text))
    return matches[-1] if matches else None


def _year_score(info, candidate):
    """Only the year is compared — not month/day — since that's all any
    source (including what you confirm) reliably has. Returns None (not 0)
    when either side is missing, so search_and_rank/_sort_key can exclude it
    from the blend instead of treating "no data" as "wrong year"."""
    query_year = _extract_year(info.get("edition_year"))
    candidate_year = _extract_year(candidate.get("publication_date"))
    if not query_year or not candidate_year:
        return None
    return 1.0 if query_year == candidate_year else 0.0


def _sort_key(c):
    # Amazon first (its ASIN makes it the most useful match to land on) —
    # a hard priority, not blended in. Within that: text similarity, image
    # similarity, and year match are weighted-averaged (50/30/20 — text
    # prioritized over image) — any signal that's unavailable for a given
    # candidate (no listing photo, no year data on either side, ...) is left
    # out of the average rather than counted as a miss, so lacking a field
    # doesn't unfairly sink a result.
    is_amazon = c["source"] != "amazon"
    weighted = [(c.get("text_score"), 0.5), (c["similarity"], 0.3), (c.get("year_score"), 0.2)]
    available = [(score, weight) for score, weight in weighted if score is not None]
    combined = sum(score * weight for score, weight in available) / sum(w for _, w in available) if available else 0
    return (is_amazon, -combined)


_FIELD_LABELS = {"title": "title", "author": "author", "publisher": "publisher", "edition_year": "pub. date"}


def _prompt_edit_info(info):
    print()
    print("Extracted info — press Enter to keep, or type a replacement:")
    for field in ("title", "author", "publisher", "edition_year"):
        current = info.get(field)
        response = input(f"  {_FIELD_LABELS[field]} [{current}]: ").strip()
        if response:
            info[field] = response
    return info


def _fmt_score(value):
    return f"{value:.3f}" if value is not None else "n/a"


def _prompt_select(ranked):
    print()
    print("All matches:")
    for i, r in enumerate(ranked):
        sim = _fmt_score(r["similarity"])
        text = _fmt_score(r.get("text_score"))
        year = _fmt_score(r.get("year_score"))
        extra = f"  ASIN:{r['asin']}" if r.get("asin") else ""
        print(
            f"  [{i}] (img={sim} txt={text} yr={year}) {r['source']:8s} "
            f"{r['title']!r} {r.get('price')}{extra} {r.get('url')}"
        )

    raw = input("Select match number(s), comma-separated (blank for none): ").strip()
    if not raw:
        return []
    indices = [int(x.strip()) for x in raw.split(",") if x.strip().isdigit()]
    return [ranked[i] for i in indices if 0 <= i < len(ranked)]


def _main():
    parser = argparse.ArgumentParser(description="Match a book to online listings")
    parser.add_argument("cover", help="Path to the single book cover photo")
    parser.add_argument(
        "other_photos", nargs="*", help="Paths to other photos (spine, title page, copyright page, ...)"
    )
    parser.add_argument("--backend", choices=["tesseract", "llm"], default="tesseract")
    parser.add_argument(
        "--match-method",
        choices=["phash", "clip", "none"],
        default="phash",
        help="'none' skips image comparison entirely and ranks by keyword match only",
    )
    parser.add_argument("--limit", type=int, default=15)
    args = parser.parse_args()

    info = extract(args.cover, args.other_photos, backend=args.backend)
    info = _prompt_edit_info(info)

    ranked = search_and_rank(args.cover, info, match_method=args.match_method, limit=args.limit)

    print()
    print("Confirmed info:", info)
    print()
    for r in ranked:
        sim = _fmt_score(r["similarity"])
        text = _fmt_score(r.get("text_score"))
        year = _fmt_score(r.get("year_score"))
        print(f"[img={sim} txt={text} yr={year}] {r['source']:8s} {r['title']!r} {r.get('price')} {r.get('url')}")

    selected = _prompt_select(ranked)

    print()
    print("Selected matches:")
    if selected:
        for m in selected:
            sim = f"{m['similarity']:.3f}" if m["similarity"] is not None else "n/a"
            extra = f"  ASIN: {m['asin']}" if m.get("asin") else ""
            print(f"  [{sim}] {m['source']:8s} {m['title']!r} {m.get('url')}{extra}")
    else:
        print("  none selected")


if __name__ == "__main__":
    _main()
