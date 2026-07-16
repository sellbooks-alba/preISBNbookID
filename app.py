"""Web UI: upload a book cover photo, fill in title/author/publisher/
edition_year by scanning each one live with your camera (client-side OCR via
Tesseract.js — see templates/upload.html — no photo ever reaches the server
for this) or typing directly, then manually select whichever of the full
result list (across all sources) is actually the right book.

Single-user, local-only tool — job state lives in memory (`JOBS`), so it's
gone if the process restarts. That's an intentional simplification: fine for
matching a book at a time on your own machine, not meant to be deployed
multi-user or left running unattended with jobs you care about keeping.
"""

import hashlib
import os
import uuid

from flask import Flask, jsonify, redirect, render_template, request, url_for
from werkzeug.utils import secure_filename

from src import config
from src.pipeline import search_and_rank

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "static", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__)

JOBS = {}

EDITABLE_FIELDS = ("title", "author", "publisher", "edition_year")


def _save_upload(job_dir, file_storage, name):
    if not file_storage or not file_storage.filename:
        return None
    ext = os.path.splitext(secure_filename(file_storage.filename))[1] or ".jpg"
    path = os.path.join(job_dir, f"{name}{ext}")
    file_storage.save(path)
    return path


def _to_url(job_id, path):
    return url_for("static", filename=f"uploads/{job_id}/{os.path.basename(path)}")


def _info_from_form():
    return {field: (request.form.get(field) or "").strip() or None for field in EDITABLE_FIELDS}


def _run_search(job_id, cover_path, cover_url, info, match_method, limit):
    try:
        ranked = search_and_rank(cover_path, info, match_method=match_method, limit=limit)
    except Exception as e:
        return render_template(
            "upload.html", job_id=job_id, info=info, cover_url=cover_url, error=f"Search failed: {e}"
        ), 400

    JOBS[job_id] = {
        "cover_path": cover_path,
        "cover_url": cover_url,
        "info": info,
        "match_method": match_method,
        "limit": limit,
        "results": [{**r, "id": i, "selected": False} for i, r in enumerate(ranked)],
    }
    return redirect(url_for("review", job_id=job_id))


@app.route("/", methods=["GET"])
def upload_form():
    return render_template("upload.html")


@app.route("/search", methods=["POST"])
def do_search():
    job_id = uuid.uuid4().hex
    job_dir = os.path.join(UPLOAD_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)

    cover_path = _save_upload(job_dir, request.files.get("cover"), "cover")
    if not cover_path:
        return render_template("upload.html", error="A book cover photo is required"), 400

    info = _info_from_form()
    match_method = request.form.get("match_method", "phash")
    limit = int(request.form.get("limit") or 5)

    return _run_search(job_id, cover_path, _to_url(job_id, cover_path), info, match_method, limit)


@app.route("/edit/<job_id>", methods=["GET"])
def edit(job_id):
    job = JOBS.get(job_id)
    if not job:
        return redirect(url_for("upload_form"))

    return render_template(
        "upload.html",
        job_id=job_id,
        info=job["info"],
        cover_url=job["cover_url"],
        match_method=job["match_method"],
        limit=job["limit"],
    )


@app.route("/edit/<job_id>", methods=["POST"])
def do_edit(job_id):
    job = JOBS.get(job_id)
    if not job:
        return redirect(url_for("upload_form"))

    job_dir = os.path.join(UPLOAD_DIR, job_id)
    new_cover_path = _save_upload(job_dir, request.files.get("cover"), "cover")
    cover_path = new_cover_path or job["cover_path"]
    cover_url = _to_url(job_id, cover_path) if new_cover_path else job["cover_url"]

    info = _info_from_form()
    match_method = request.form.get("match_method", "phash")
    limit = int(request.form.get("limit") or 5)

    return _run_search(job_id, cover_path, cover_url, info, match_method, limit)


@app.route("/review/<job_id>", methods=["GET"])
def review(job_id):
    job = JOBS.get(job_id)
    if not job or job["results"] is None:
        return redirect(url_for("upload_form"))

    results = job["results"]
    return render_template(
        "review.html",
        job_id=job_id,
        info=job["info"],
        cover_url=job["cover_url"],
        results=results,
        selected=[r for r in results if r["selected"]],
    )


@app.route("/review/<job_id>/select", methods=["POST"])
def select(job_id):
    job = JOBS.get(job_id)
    if not job or job["results"] is None:
        return redirect(url_for("upload_form"))

    result_id = int(request.form.get("id"))
    action = request.form.get("action")
    for r in job["results"]:
        if r["id"] == result_id:
            r["selected"] = action == "select"
            break

    return redirect(url_for("review", job_id=job_id))


@app.route("/ebay/account-deletion", methods=["GET", "POST"])
def ebay_account_deletion():
    """eBay's required Marketplace Account Deletion/Closure notification
    endpoint. GET is eBay's one-time verification challenge; POST is the
    actual notification eBay sends when a user deletes their account. This
    app never stores eBay account data, so the POST handler just acknowledges
    receipt — there's nothing to delete on our side.
    """
    if request.method == "GET":
        challenge_code = request.args.get("challenge_code", "")
        token = config.EBAY_VERIFICATION_TOKEN or ""
        endpoint = config.EBAY_NOTIFICATION_ENDPOINT_URL or ""
        digest = hashlib.sha256((challenge_code + token + endpoint).encode("utf-8")).hexdigest()
        return jsonify({"challengeResponse": digest}), 200

    return "", 200


if __name__ == "__main__":
    app.run(debug=True)
