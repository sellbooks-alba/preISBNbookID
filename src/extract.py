"""Pull title/author/publisher/edition-year out of however many photos you took
of the book (cover, spine, title page, copyright page, ...)."""

import base64
import io
import json
import os
import re
import shutil

import pillow_heif
from PIL import Image, ImageOps
import pytesseract

from . import config

pillow_heif.register_heif_opener()  # lets PIL open iPhone-default HEIC/HEIF photos

if not shutil.which("tesseract"):
    # Windows installers (e.g. the UB-Mannheim build) don't refresh PATH for
    # processes already running — fall back to the default install location
    # instead of requiring a full logout/login before this works.
    for _candidate in (
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ):
        if os.path.exists(_candidate):
            pytesseract.pytesseract.tesseract_cmd = _candidate
            break


def load_oriented_image(path):
    """Phone photos routinely carry an EXIF orientation tag (rotate 90/180/270
    to display upright) that PIL does NOT apply automatically — browsers do
    this for you in <img> tags, which is why a sideways photo looks fine in
    the web UI but turns into garbage the moment raw pixels reach Tesseract,
    Claude's vision API, or the image-similarity comparison."""
    return ImageOps.exif_transpose(Image.open(path))


def ocr_text(image_path):
    """pytesseract gates on PIL's `.format` tag against its own whitelist,
    which doesn't include HEIF — so a HEIC photo that PIL decodes just fine
    still gets rejected unless we clear that tag and let it fall back to PNG."""
    image = load_oriented_image(image_path)
    if image.format not in pytesseract.pytesseract.SUPPORTED_FORMATS:
        image.format = None
    return pytesseract.image_to_string(image)


_YEAR_RE = re.compile(r"\b(1[89]\d{2}|20[0-3]\d)\b")
_BY_LINE_RE = re.compile(r"^\s*by\s+([A-Z][\w.'-]+(?:\s+[A-Z][\w.'-]+){0,3})\s*$", re.IGNORECASE)
_PUBLISHED_BY_RE = re.compile(
    r"published\s+by[:\-]?[ \t]*([A-Z][\w&.,'-]*(?:[ \t]+[A-Z][\w&.,'-]*){0,5})", re.IGNORECASE
)
_PUBLISHER_SUFFIX_RE = re.compile(
    r"([A-Z][\w&.,'-]*(?:[ \t]+[A-Z][\w&.,'-]*){0,4}[ \t]+"
    r"(?:Press|Publishing|Publishers|Books|House|Sons))\b"
)


def _guess_year(text):
    # "This edition" names the specific printing you're holding, so it
    # outranks "first edition"/"first printing" (the *original* printing's
    # year, not necessarily this copy's) and the generic last-4-digits fallback.
    lines = text.splitlines()

    for line in lines:
        if re.search(r"this edition", line, re.IGNORECASE):
            years = _YEAR_RE.findall(line)
            if years:
                return years[-1]

    for line in lines:
        if re.search(r"first (edition|printing)", line, re.IGNORECASE):
            years = _YEAR_RE.findall(line)
            if years:
                return years[-1]

    years = _YEAR_RE.findall(text)
    return years[-1] if years else None


def _guess_title(cover_text):
    """Best-effort, not reliable: assumes the first photo is the cover and
    that the title is the most prominent (here: longest) line near the top,
    excluding the "by <author>" line itself — true often enough to be a
    useful default, wrong often enough that you should sanity-check it
    against the photo."""
    lines = [line.strip() for line in cover_text.splitlines() if line.strip()]
    lines = [line for line in lines if not _BY_LINE_RE.match(line)]
    candidates = lines[:6]
    return max(candidates, key=len) if candidates else None


def _guess_author(all_lines):
    for line in all_lines:
        m = _BY_LINE_RE.match(line.strip())
        if m:
            return m.group(1).strip()
    return None


def _guess_publisher(combined_text):
    m = _PUBLISHED_BY_RE.search(combined_text)
    if m:
        return m.group(1).strip()
    m = _PUBLISHER_SUFFIX_RE.search(combined_text)
    return m.group(1).strip() if m else None


def extract_book_info_tesseract(image_paths):
    """Zero-config baseline: raw OCR of every photo, plus pattern-matching
    guesses for title/author/publisher/edition_year. Works offline, no API
    key — but these are heuristics, not structured extraction, and can guess
    wrong on unusual cover layouts. Treat `raw_text` as the source of truth
    to fall back on when a guess looks off.
    """
    texts = [ocr_text(p) for p in image_paths]
    combined = "\n".join(texts)
    all_lines = [line for text in texts for line in text.splitlines()]

    return {
        "title": _guess_title(texts[0]) if texts else None,
        "author": _guess_author(all_lines),
        "publisher": _guess_publisher(combined),
        "edition_year": _guess_year(combined),
        "raw_text": combined.strip(),
    }


def _image_to_data_url(path):
    """Always decode, fix orientation, and re-encode as JPEG: normalizes any
    format PIL can open (HEIC/HEIF, BMP, TIFF, ...) into what Claude's vision
    API accepts, and makes sure a sideways phone photo doesn't get sent to
    the model sideways."""
    buf = io.BytesIO()
    load_oriented_image(path).convert("RGB").save(buf, format="JPEG")
    return "image/jpeg", base64.standard_b64encode(buf.getvalue()).decode("utf-8")


def extract_book_info_llm(image_paths):
    """Vision-LLM backend: more reliable on stylized fonts and can reason about
    "first edition" / printing-year wording on the copyright page instead of
    just regexing for a 4-digit number. Requires ANTHROPIC_API_KEY.
    """
    import anthropic

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    content = [
        {
            "type": "text",
            "text": (
                "These are photos of a single book — could be the cover, spine, "
                "title page, copyright page, or any combination. Extract the title, "
                "author, publisher, and the edition/printing year (prefer the year "
                "tied to 'first edition'/'first printing' if stated, otherwise the "
                "copyright year). Respond with ONLY a JSON object with keys title, "
                "author, publisher, edition_year, notes (put ambiguity or multiple "
                "candidate years in notes)."
            ),
        }
    ]

    for path in image_paths:
        mime, data = _image_to_data_url(path)
        content.append(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": mime, "data": data},
            }
        )

    message = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=1024,
        messages=[{"role": "user", "content": content}],
    )

    text = message.content[0].text.strip()
    text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    return json.loads(text)


def extract_book_info(image_paths, backend="tesseract"):
    if isinstance(image_paths, str):
        image_paths = [image_paths]
    if backend == "llm":
        return extract_book_info_llm(image_paths)
    return extract_book_info_tesseract(image_paths)
