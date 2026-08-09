#!/usr/bin/env python3
"""
PrintStation - Local Print Management Server
A web-based dashboard for managing CUPS print jobs on Linux.

Run:  python3 app.py
Open: http://localhost:5000
"""

import os
import threading

from flask import Flask
from flask_cors import CORS

import config
from config import log, settings
from routes.api import bp as api_blueprint


def _start_queue_worker() -> None:
    """Start the persistent print queue daemon thread."""
    from services.print_worker import queue_worker
    t = threading.Thread(target=queue_worker, name="PrintQueueWorker", daemon=True)
    t.start()
    log.info("Print queue worker thread started.")


def _start_watchdog() -> None:
    """
    Start folder watchers for any configured watch_folders.
    Requires the 'watchdog' package (pip install watchdog).
    Silently skips if watchdog is not installed.
    """
    watch_folders = settings.get("watch_folders", [])
    if not watch_folders:
        return

    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
        from services.pdf_service import IMAGE_EXTS

        class _AutoPrintHandler(FileSystemEventHandler):
            def on_created(self, event):
                if event.is_directory:
                    return
                path = event.src_path
                ext  = path.rsplit(".", 1)[-1].lower() if "." in path else ""
                if ext not in (IMAGE_EXTS | {"pdf"}):
                    return
                if not os.path.exists(path):
                    return

                import time; time.sleep(0.5)   # let the file finish writing

                is_img = ext in IMAGE_EXTS
                job_id = config.enqueue_job(
                    filepath=path,
                    printer=settings["default_printer"],
                    start=1, end=1,
                    copies=settings.get("default_copies", 1),
                    media=settings.get("media", "Letter"),
                    quality="normal",
                    duplex=False,
                )
                config.log_job(
                    f"Auto-queued {os.path.basename(path)}",
                    f"Watch folder · {settings['default_printer']}",
                    "queued", "yellow",
                )
                log.info(f"Auto-queued {path} → job {job_id}")

        observer = Observer()
        for folder in watch_folders:
            if os.path.isdir(folder):
                observer.schedule(_AutoPrintHandler(), folder, recursive=False)
                log.info(f"Watching folder: {folder}")

        observer.daemon = True
        observer.start()
        log.info("Watchdog observer started.")

    except ImportError:
        log.info("watchdog not installed — auto-print watch folders disabled. "
                 "Install with: pip install watchdog")
    except Exception:
        log.exception("Failed to start watchdog observer")


def create_app() -> Flask:
    """Flask Application Factory."""
    app = Flask(__name__, static_folder="static")
    CORS(app)
    app.register_blueprint(api_blueprint)
    return app


if __name__ == "__main__":
    config.load_settings()
    config.init_db()
    os.makedirs("static", exist_ok=True)

    _start_queue_worker()
    _start_watchdog()

    print("\n" + "=" * 50)
    print("  🖨️  PrintStation")
    print("=" * 50)
    print("  Open: http://localhost:5000")
    print("  Ctrl+C to stop")
    print("=" * 50 + "\n")

    app = create_app()
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
