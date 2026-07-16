"""Compare a reference cover photo against a candidate listing image."""

import io
import threading

import imagehash
import pillow_heif
import requests
from PIL import Image, ImageOps

pillow_heif.register_heif_opener()  # lets PIL open iPhone-default HEIC/HEIF photos


def _load_image(path_or_url):
    # Already-decoded (the caller pre-loaded a reference image to reuse
    # across many comparisons — see load_reference) — pass it through as-is.
    if isinstance(path_or_url, Image.Image):
        return path_or_url

    if str(path_or_url).startswith("http"):
        resp = requests.get(path_or_url, timeout=15)
        resp.raise_for_status()
        image = Image.open(io.BytesIO(resp.content))
    else:
        image = Image.open(path_or_url)

    # JPEG-only fast path: hints libjpeg to decode directly at a much lower
    # resolution via its built-in DCT scaling, instead of fully decoding a
    # multi-megapixel phone photo just to immediately shrink it — phash only
    # ever looks at a 32x32 thumbnail of it anyway. draft() is a documented
    # no-op for any format it doesn't support, so this is safe unconditionally.
    image.draft("RGB", (128, 128))

    # EXIF orientation isn't applied automatically by PIL — a sideways phone
    # photo (either the reference cover or a seller's listing photo) would
    # otherwise score as a poor match even against the correct book.
    return ImageOps.exif_transpose(image).convert("RGB")


def load_reference(path):
    """Decode + orient the reference cover photo once, so callers comparing
    it against many candidates (search_and_rank does this per-candidate, up
    to ~60 times for a full multi-source search) aren't re-decoding the same
    file from scratch on every single comparison. That redundant decode work
    is exactly what caused a real Render crash under load — the free tier's
    constrained CPU/memory couldn't keep up, hit gunicorn's worker timeout,
    and got SIGKILLed. Pass the returned object anywhere `ref` is expected;
    _load_image recognizes an already-decoded image and reuses it directly.
    """
    return _load_image(path)


def phash_similarity(ref, candidate):
    """Fast, no-GPU. Good for near-identical images (same scan/printing),
    weak if the listing photo is a different angle/lighting/edition."""
    h1 = imagehash.phash(_load_image(ref))
    h2 = imagehash.phash(_load_image(candidate))
    max_distance = len(h1.hash) ** 2  # 64 for the default 8x8 hash
    return 1 - (h1 - h2) / max_distance


_clip_model = {"model": None, "preprocess": None}
_clip_lock = threading.Lock()


def _load_clip():
    # Comparisons now run on a thread pool (search_and_rank) — without the
    # lock, concurrent first-calls could each see model=None and race to
    # load their own copy of the model.
    with _clip_lock:
        if _clip_model["model"] is None:
            import open_clip
            import torch

            model, _, preprocess = open_clip.create_model_and_transforms(
                "ViT-B-32", pretrained="openai"
            )
            model.eval()
            _clip_model["model"] = model
            _clip_model["preprocess"] = preprocess
            _clip_model["torch"] = torch
        return _clip_model["model"], _clip_model["preprocess"], _clip_model["torch"]


def clip_similarity(ref, candidate):
    """Slower, needs torch/open_clip, but tolerates loose matches — different
    photo of the same cover design, cropping, minor recolor between printings."""
    model, preprocess, torch = _load_clip()

    with torch.no_grad():
        images = torch.stack(
            [preprocess(_load_image(ref)), preprocess(_load_image(candidate))]
        )
        features = model.encode_image(images)
        features /= features.norm(dim=-1, keepdim=True)
        return float((features[0] @ features[1]).item())


def compare(ref, candidate, method="phash"):
    if method == "clip":
        return clip_similarity(ref, candidate)
    return phash_similarity(ref, candidate)
