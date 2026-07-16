"""Compare a reference cover photo against a candidate listing image."""

import io

import imagehash
import pillow_heif
import requests
from PIL import Image, ImageOps

pillow_heif.register_heif_opener()  # lets PIL open iPhone-default HEIC/HEIF photos


def _load_image(path_or_url):
    # EXIF orientation isn't applied automatically by PIL — a sideways phone
    # photo (either the reference cover or a seller's listing photo) would
    # otherwise score as a poor match even against the correct book.
    if str(path_or_url).startswith("http"):
        resp = requests.get(path_or_url, timeout=15)
        resp.raise_for_status()
        image = Image.open(io.BytesIO(resp.content))
    else:
        image = Image.open(path_or_url)
    return ImageOps.exif_transpose(image).convert("RGB")


def phash_similarity(ref, candidate):
    """Fast, no-GPU. Good for near-identical images (same scan/printing),
    weak if the listing photo is a different angle/lighting/edition."""
    h1 = imagehash.phash(_load_image(ref))
    h2 = imagehash.phash(_load_image(candidate))
    max_distance = len(h1.hash) ** 2  # 64 for the default 8x8 hash
    return 1 - (h1 - h2) / max_distance


_clip_model = {"model": None, "preprocess": None}


def _load_clip():
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
