"""
routes/api.py — All Flask API route handlers.

Each route is thin: validate → call service → return JSON.
No business logic lives here.

New in this version:
  - PIN auth decorator
  - /api/queue          — pending job list
  - /api/cancel/<id>    — per-job cancellation
  - /api/cost           — print cost estimator
  - /api/profiles       — saved printer profiles CRUD
  - /api/watch          — manage auto-print watch folders
  - Duplex option in /api/print
  - Path validation on all file inputs
  - SSE disconnect detection via GeneratorExit
"""

import os
import json
import time
from datetime import datetime
from functools import wraps

from flask import Blueprint, jsonify, request, send_from_directory, Response

import config
from config import (
    settings, log,
    get_active_job, update_active_job, is_job_running,
    get_pending, enqueue_job,
    log_job, get_history,
    save_settings, format_size,
    is_allowed_path,
)
from services.pdf_service import get_file_info, build_combined_pdf, IMAGE_EXTS
from services.cups_service import (
    list_printers, get_ink_levels, get_cups_queue,
    enable_printer, cancel_jobs, restart_cups,
)
from services.print_worker import cancel_job, cancel_current

bp = Blueprint("api", __name__)

SUPPORTED_EXTS = IMAGE_EXTS | {"pdf"}


# ── PIN auth decorator ─────────────────────────────────────────
def require_pin(f):
    """
    Protect a route with an optional PIN.
    If settings["pin"] is non-empty, the request must supply it via
    the X-PIN header or a 'pin' field in the JSON body.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        pin = settings.get("pin", "")
        if pin:
            provided = (
                request.headers.get("X-PIN")
                or (request.json or {}).get("pin", "")
                or request.form.get("pin", "")
            )
            if provided != pin:
                return jsonify({"error": "Unauthorized: invalid or missing PIN"}), 401
        return f(*args, **kwargs)
    return decorated


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
    except Exception as exc:
        log.exception("get_printers failed")
        return jsonify({"printers": [], "error": str(exc)})


# ── Ink ────────────────────────────────────────────────────────
@bp.route("/api/ink")
def get_ink():
    printer = request.args.get("printer", settings["default_printer"])
    try:
        return jsonify({"ink": get_ink_levels(printer), "printer": printer})
    except Exception as exc:
        log.exception("get_ink failed")
        return jsonify({"ink": [], "error": str(exc)})


# ── File listing ───────────────────────────────────────────────
@bp.route("/api/files")
def list_files():
    folder = request.args.get("folder", settings["scan_folders"][0] if settings["scan_folders"] else "")
    files  = []
    try:
        if os.path.isdir(folder):
            for fname in sorted(os.listdir(folder)):
                ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
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
    except Exception as exc:
        log.exception("list_files failed")
        return jsonify({"files": [], "error": str(exc)})


# ── PDF info ───────────────────────────────────────────────────
@bp.route("/api/pdf-info")
def pdf_info():
    path = request.args.get("path", "")
    if not path or not os.path.exists(path):
        return jsonify({"error": "File not found"}), 404
    if not is_allowed_path(path):
        return jsonify({"error": "Access denied: path outside allowed folders"}), 403
    return jsonify(get_file_info(path))


# ── Print ──────────────────────────────────────────────────────
@bp.route("/api/print", methods=["POST"])
@require_pin
def print_file():
    data      = request.json or {}
    filepaths = data.get("filepaths") or ([data["filepath"]] if data.get("filepath") else [])
    printer   = data.get("printer",  settings["default_printer"])
    copies    = max(1, int(data.get("copies", 1)))
    media     = data.get("media",    settings["media"])
    quality   = data.get("quality",  "normal")
    duplex    = bool(data.get("duplex", False))

    if not filepaths:
        return jsonify({"error": "No files specified"}), 400

    # Validate paths
    for fp in filepaths:
        if not os.path.exists(fp):
            return jsonify({"error": f"File not found: {fp}"}), 400
        if not is_allowed_path(fp):
            return jsonify({"error": f"Access denied: {fp}"}), 403

    is_image = lambda p: p.rsplit(".", 1)[-1].lower() in IMAGE_EXTS
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
        except Exception as exc:
            log.exception("build_combined_pdf failed")
            return jsonify({"error": f"Failed to prepare files: {exc}"}), 500
    else:
        filepath = filepaths[0]
        start    = int(data.get("start", 1))
        end      = int(data.get("end",   1))
        if start > end:
            return jsonify({"error": "Start page must be ≤ end page"}), 400

    # Enqueue — the daemon worker will pick it up
    label  = os.path.basename(filepaths[0]) if len(filepaths) == 1 else f"{len(filepaths)} files combined"
    suffix = "y" if copies == 1 else "ies"

    job_id = enqueue_job(
        filepath=filepath, printer=printer,
        start=start, end=end,
        copies=copies, media=media,
        quality=quality, duplex=duplex,
        label=label,
    )
    log_job(f"Queued {label}", f"Pages {start}-{end} · {copies} cop{suffix} · {printer}", "queued", "yellow")

    return jsonify({
        "message": f"Job queued! (id: {job_id})",
        "job_id":  job_id,
        "file":    label,
        "queue_length": config.job_queue.qsize(),
    })


# ── Print queue ────────────────────────────────────────────────
@bp.route("/api/queue")
def get_queue_status():
    """Return the current active job and list of pending queued jobs."""
    active  = get_active_job()
    pending = get_pending()
    # Strip the job that is currently running from pending
    running_id = active.get("job_id")
    pending = [j for j in pending if j.get("job_id") != running_id]
    return jsonify({
        "active":  active,
        "pending": pending,
        "length":  config.job_queue.qsize(),
    })


# ── Status ─────────────────────────────────────────────────────
@bp.route("/api/status")
def get_status():
    job = get_active_job()
    try:
        job["cups_queue"] = get_cups_queue()
    except Exception:
        job["cups_queue"] = ""
    return jsonify(job)


# ── History ────────────────────────────────────────────────────
@bp.route("/api/history")
def get_history_route():
    limit = min(int(request.args.get("limit", 100)), 500)
    return jsonify({"history": get_history(limit)})


# ── Cancel ─────────────────────────────────────────────────────
@bp.route("/api/cancel", methods=["POST"])
@require_pin
def cancel_all():
    """Cancel the current job and all CUPS jobs on a printer."""
    printer = (request.json or {}).get("printer", settings["default_printer"])
    cancel_current()
    cancel_jobs(printer)
    update_active_job(running=False, status="cancelled", message=f"Cancelled all jobs on {printer}")
    log_job("Cancelled", f"All jobs on {printer}", "warn", "yellow")
    return jsonify({"message": f"Cancelled all jobs on {printer}"})


@bp.route("/api/cancel/<job_id>", methods=["POST"])
@require_pin
def cancel_specific(job_id: str):
    """Cancel a specific queued or active job by its ID."""
    if cancel_job(job_id):
        config.remove_pending(job_id)
        log_job("Cancelled", f"Job {job_id}", "warn", "yellow")
        return jsonify({"message": f"Job {job_id} cancelled"})
    return jsonify({"error": f"Job {job_id} not found or already finished"}), 404


# ── Enable printer ─────────────────────────────────────────────
@bp.route("/api/enable", methods=["POST"])
@require_pin
def enable():
    printer = (request.json or {}).get("printer", settings["default_printer"])
    enable_printer(printer)
    log_job("Enabled", f"Printer {printer} re-enabled", "done", "green")
    return jsonify({"message": f"Printer {printer} re-enabled"})


# ── Restart CUPS ───────────────────────────────────────────────
@bp.route("/api/restart-cups", methods=["POST"])
@require_pin
def do_restart_cups():
    restart_cups(settings["default_printer"])
    log_job("CUPS Restarted", "Print service restarted", "done", "blue")
    return jsonify({"message": "CUPS restarted successfully"})


# ── Settings ───────────────────────────────────────────────────
@bp.route("/api/settings", methods=["GET"])
def get_settings():
    # Never expose the raw PIN over the API
    safe = dict(settings)
    safe["pin"] = "••••" if settings.get("pin") else ""
    return jsonify(safe)


@bp.route("/api/settings", methods=["POST"])
@require_pin
def update_settings():
    data = request.json or {}
    # Don't allow overwriting profiles/watch via bulk settings update
    data.pop("profiles", None)
    data.pop("watch_folders", None)
    settings.update(data)
    save_settings()
    return jsonify({"message": "Settings saved!", "settings": settings})


# ── File upload ────────────────────────────────────────────────
@bp.route("/api/upload", methods=["POST"])
@require_pin
def upload_file():
    try:
        if "file" not in request.files:
            return jsonify({"error": "No file in request"}), 400
        file = request.files["file"]
        if not file.filename:
            return jsonify({"error": "No filename"}), 400

        target = request.form.get("folder", settings["scan_folders"][0] if settings["scan_folders"] else "")
        if not target:
            return jsonify({"error": "No target folder configured"}), 400
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
    except Exception as exc:
        log.exception("upload_file failed")
        return jsonify({"error": str(exc)}), 500


# ── Print cost estimator ───────────────────────────────────────
@bp.route("/api/cost")
def estimate_cost():
    """
    Estimate print cost for a given job configuration.
    Query params: pages, copies, color (true/false)
    """
    pages  = max(1, int(request.args.get("pages",  1)))
    copies = max(1, int(request.args.get("copies", 1)))
    color  = request.args.get("color", "false").lower() == "true"
    duplex = request.args.get("duplex", "false").lower() == "true"

    rate = settings["cost_per_page_color"] if color else settings["cost_per_page_bw"]
    # Duplex halves the number of physical sheets
    sheets = (pages * copies + 1) // 2 if duplex else pages * copies
    total  = sheets * rate

    return jsonify({
        "pages":    pages,
        "copies":   copies,
        "sheets":   sheets,
        "color":    color,
        "duplex":   duplex,
        "rate":     rate,
        "total":    round(total, 2),
        "currency": settings["currency"],
    })


# ── Printer profiles ───────────────────────────────────────────
@bp.route("/api/profiles", methods=["GET"])
def get_profiles():
    return jsonify({"profiles": settings.get("profiles", [])})


@bp.route("/api/profiles", methods=["POST"])
@require_pin
def save_profile():
    """Save or update a named printer profile."""
    data = request.json or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "Profile name is required"}), 400

    profiles = settings.setdefault("profiles", [])
    for p in profiles:
        if p.get("name") == name:
            p.update(data)
            break
    else:
        profiles.append(data)

    save_settings()
    return jsonify({"message": f"Profile '{name}' saved.", "profiles": profiles})


@bp.route("/api/profiles/<name>", methods=["DELETE"])
@require_pin
def delete_profile(name: str):
    profiles = settings.get("profiles", [])
    settings["profiles"] = [p for p in profiles if p.get("name") != name]
    save_settings()
    return jsonify({"message": f"Profile '{name}' deleted.", "profiles": settings["profiles"]})


# ── Watch folders (auto-print) ─────────────────────────────────
@bp.route("/api/watch", methods=["GET"])
def get_watch_folders():
    return jsonify({"watch_folders": settings.get("watch_folders", [])})


@bp.route("/api/watch", methods=["POST"])
@require_pin
def add_watch_folder():
    data   = request.json or {}
    folder = data.get("folder", "").strip()
    if not folder:
        return jsonify({"error": "folder is required"}), 400
    if not os.path.isdir(folder):
        return jsonify({"error": f"Directory does not exist: {folder}"}), 400

    watches = settings.setdefault("watch_folders", [])
    if folder not in watches:
        watches.append(folder)
        # Also add to scan_folders so it appears in the file browser
        if folder not in settings["scan_folders"]:
            settings["scan_folders"].append(folder)
        save_settings()
    return jsonify({"message": f"Watching '{folder}'", "watch_folders": watches})


@bp.route("/api/watch", methods=["DELETE"])
@require_pin
def remove_watch_folder():
    data   = request.json or {}
    folder = data.get("folder", "").strip()
    watches = settings.get("watch_folders", [])
    if folder in watches:
        watches.remove(folder)
        save_settings()
    return jsonify({"message": f"Stopped watching '{folder}'", "watch_folders": watches})


# ── SSE stream (with disconnect detection) ─────────────────────
@bp.route("/api/stream")
def stream():
    def generate():
        try:
            while True:
                yield f"data: {json.dumps(get_active_job())}\n\n"
                time.sleep(1)
        except GeneratorExit:
            # Client disconnected — clean exit, no resource leak
            pass

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
