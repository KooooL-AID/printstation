"""
routes/api.py — All Flask API route handlers.

Each route is thin: validate input, call a service, return JSON.
No business logic lives here.
"""

import os
import json
import threading
from datetime import datetime

from flask import Blueprint, jsonify, request, send_from_directory, Response

import config
from config import settings, active_job, job_history, log_job, save_settings, format_size
from services.pdf_service import get_file_info, build_combined_pdf, IMAGE_EXTS
from services.cups_service import (
    list_printers, get_ink_levels, get_queue,
    enable_printer, cancel_jobs, restart_cups,
)
from services import print_worker

bp = Blueprint("api", __name__)

# ── Static ─────────────────────────────────────────────────────
@bp.route("/")
def index():
    return send_from_directory("static", "index.html")


# ── Printers ───────────────────────────────────────────────────
@bp.route("/api/printers")
def get_printers():
    try:
        printers = list_printers()
        return jsonify({"printers": printers, "default": settings["default_printer"]})
    except Exception as e:
        return jsonify({"printers": [], "error": str(e)})


# ── Ink ────────────────────────────────────────────────────────
@bp.route("/api/ink")
def get_ink():
    printer = request.args.get("printer", settings["default_printer"])
    try:
        ink = get_ink_levels(printer)
        return jsonify({"ink": ink, "printer": printer})
    except Exception as e:
        return jsonify({"ink": [], "error": str(e)})


# ── File listing ───────────────────────────────────────────────
SUPPORTED_EXTS = IMAGE_EXTS | {"pdf"}

@bp.route("/api/files")
def list_files():
    folder = request.args.get("folder", settings["scan_folders"][0])
    files  = []
    try:
        if os.path.isdir(folder):
            for fname in sorted(os.listdir(folder)):
                ext = fname.rsplit(".", 1)[-1].lower()
                if ext not in SUPPORTED_EXTS:
                    continue
                path = os.path.join(folder, fname)
                info = get_file_info(path)
                if not info.get("valid"):
                    continue
                size = os.path.getsize(path)
                files.append({
                    "name":     fname,
                    "path":     path,
                    "pages":    info.get("pages", 0),
                    "type":     info.get("type", "pdf"),
                    "size":     format_size(size),
                    "modified": datetime.fromtimestamp(os.path.getmtime(path)).strftime("%b %d %H:%M"),
                })
        return jsonify({"files": files, "folder": folder, "folders": settings["scan_folders"]})
    except Exception as e:
        return jsonify({"files": [], "error": str(e)})


# ── PDF info ───────────────────────────────────────────────────
@bp.route("/api/pdf-info")
def pdf_info():
    path = request.args.get("path", "")
    if not path or not os.path.exists(path):
        return jsonify({"error": "File not found"}), 404
    return jsonify(get_file_info(path))


# ── Print ──────────────────────────────────────────────────────
@bp.route("/api/print", methods=["POST"])
def print_file():
    if active_job["running"]:
        return jsonify({"error": "A print job is already running! Wait for it to finish."}), 400

    data      = request.json or {}
    filepaths = data.get("filepaths") or ([data["filepath"]] if data.get("filepath") else [])
    printer   = data.get("printer",  settings["default_printer"])
    copies    = int(data.get("copies", 1))
    media     = data.get("media",    settings["media"])
    quality   = data.get("quality",  "normal")

    if not filepaths:
        return jsonify({"error": "No files specified"}), 400

    missing = [f for f in filepaths if not os.path.exists(f)]
    if missing:
        return jsonify({"error": f"File not found: {missing[0]}"}), 400

    is_image  = lambda p: p.rsplit(".", 1)[-1].lower() in IMAGE_EXTS
    needs_combine = len(filepaths) > 1 or is_image(filepaths[0])

    if needs_combine:
        opts        = data.get("image_options", {})
        fit         = opts.get("fit",         "fit")
        margin      = opts.get("margin",      "small")
        filter_type = opts.get("filter",      "none")
        auto_rotate = opts.get("auto_rotate", True)
        try:
            filepath, total_pages = build_combined_pdf(
                filepaths, media, fit, margin, filter_type, auto_rotate,
            )
            start, end = 1, total_pages
        except Exception as e:
            return jsonify({"error": f"Failed to prepare files: {e}"}), 500
    else:
        filepath = filepaths[0]
        start    = int(data.get("start", 1))
        end      = int(data.get("end",   1))
        if start > end:
            return jsonify({"error": "Start page must be ≤ end page"}), 400

    # Best-effort: wake the printer before sending
    enable_printer(printer)

    thread = threading.Thread(
        target=print_worker.run,
        args=(filepath, printer, start, end, copies, media, quality),
        daemon=True,
    )
    thread.start()

    suffix = "y" if copies == 1 else "ies"
    label  = os.path.basename(filepaths[0]) if len(filepaths) == 1 else f"{len(filepaths)} files combined"
    log_job(f"Queued {label}", f"Pages {start}-{end} · {copies} cop{suffix} · {printer}", "queued", "yellow")

    return jsonify({"message": "Print job started!", "file": label})


# ── Status ─────────────────────────────────────────────────────
@bp.route("/api/status")
def get_status():
    try:
        active_job["queue"] = get_queue()
    except Exception:
        active_job["queue"] = ""
    return jsonify(active_job)


# ── History ────────────────────────────────────────────────────
@bp.route("/api/history")
def get_history():
    return jsonify({"history": job_history})


# ── Cancel ─────────────────────────────────────────────────────
@bp.route("/api/cancel", methods=["POST"])
def cancel():
    printer = (request.json or {}).get("printer", settings["default_printer"])
    cancel_jobs(printer)
    active_job.update({"running": False, "status": "cancelled",
                        "message": f"Cancelled all jobs on {printer}"})
    log_job("Cancelled", f"All jobs on {printer}", "warn", "yellow")
    return jsonify({"message": f"Cancelled all jobs on {printer}"})


# ── Enable printer ─────────────────────────────────────────────
@bp.route("/api/enable", methods=["POST"])
def enable():
    printer = (request.json or {}).get("printer", settings["default_printer"])
    enable_printer(printer)
    log_job("Enabled", f"Printer {printer} re-enabled", "done", "green")
    return jsonify({"message": f"Printer {printer} re-enabled"})


# ── Restart CUPS ───────────────────────────────────────────────
@bp.route("/api/restart-cups", methods=["POST"])
def do_restart_cups():
    restart_cups(settings["default_printer"])
    log_job("CUPS Restarted", "Print service restarted", "done", "blue")
    return jsonify({"message": "CUPS restarted successfully"})


# ── Settings ───────────────────────────────────────────────────
@bp.route("/api/settings", methods=["GET"])
def get_settings():
    return jsonify(settings)


@bp.route("/api/settings", methods=["POST"])
def update_settings():
    data = request.json or {}
    settings.update(data)
    save_settings()
    return jsonify({"message": "Settings saved!", "settings": settings})


# ── File upload ────────────────────────────────────────────────
@bp.route("/api/upload", methods=["POST"])
def upload_file():
    try:
        if "file" not in request.files:
            return jsonify({"error": "No file in request"}), 400
        file = request.files["file"]
        if not file.filename:
            return jsonify({"error": "No filename"}), 400

        target = request.form.get("folder", settings["scan_folders"][0])
        os.makedirs(target, exist_ok=True)

        base, ext = os.path.splitext(os.path.basename(file.filename))
        dest      = os.path.join(target, f"{base}{ext}")
        counter   = 1
        while os.path.exists(dest):
            dest = os.path.join(target, f"{base}_{counter}{ext}")
            counter += 1

        file.save(dest)
        info = get_file_info(dest)
        size = os.path.getsize(dest)

        return jsonify({
            "success": True,
            "file": {
                "name":     os.path.basename(dest),
                "path":     dest,
                "pages":    info.get("pages", 1),
                "type":     info.get("type", "image"),
                "size":     format_size(size),
                "modified": datetime.fromtimestamp(os.path.getmtime(dest)).strftime("%b %d %H:%M"),
            },
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── SSE stream ─────────────────────────────────────────────────
@bp.route("/api/stream")
def stream():
    def generate():
        while True:
            yield f"data: {json.dumps(active_job)}\n\n"
            import time; time.sleep(1)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
