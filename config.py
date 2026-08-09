"""
config.py — Shared state, settings, and job history for PrintStation.

Do NOT import from services here — this module is imported by everyone.
"""

import os
import json
import uuid
import queue
import sqlite3
import logging
import threading
from datetime import datetime
from pathlib import Path

# ── Logging ────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("printstation")

# ── App directory ──────────────────────────────────────────────
APP_DIR       = Path.home() / ".printstation"
SETTINGS_FILE = APP_DIR / "settings.json"
DB_FILE       = APP_DIR / "history.db"

# ── Default settings ───────────────────────────────────────────
DEFAULT_SETTINGS: dict = {
    "default_printer":      "L5290",
    "default_copies":       1,
    "scan_folders": [
        str(Path.home() / "Downloads"),
        str(Path.home() / "Documents"),
    ],
    "auto_reverse":         True,
    "media":                "Letter",
    # Security
    "pin":                  "",           # empty = no PIN required
    # Cost estimator
    "cost_per_page_bw":     0.05,
    "cost_per_page_color":  0.15,
    "currency":             "PHP",
    # Profiles & watch
    "profiles":             [],           # saved printer profiles
    "watch_folders":        [],           # auto-print watched folders
}

settings: dict = dict(DEFAULT_SETTINGS)


# ── Thread-safe active job ─────────────────────────────────────
_job_lock = threading.Lock()

_active_job: dict = {
    "running":  False,
    "status":   "idle",
    "message":  "",
    "progress": 0,
    "total":    0,
    "file":     "",
    "printer":  "",
    "copy":     0,
    "copies":   0,
    "batch":    0,
    "batches":  0,
    "queue":    "",
    "job_id":   None,
}


def get_active_job() -> dict:
    """Return a snapshot of the current job state (thread-safe)."""
    with _job_lock:
        return dict(_active_job)


def update_active_job(**kwargs) -> None:
    """Merge kwargs into active_job (thread-safe)."""
    with _job_lock:
        _active_job.update(kwargs)


def is_job_running() -> bool:
    with _job_lock:
        return bool(_active_job["running"])


# ── Print job queue ────────────────────────────────────────────
job_queue: queue.Queue = queue.Queue()

_pending: list[dict] = []
_pending_lock = threading.Lock()


def enqueue_job(**kwargs) -> str:
    """
    Create a job dict, add it to the queue and pending list.
    Returns the new job_id.
    """
    job_id = str(uuid.uuid4())[:8]
    job = {"job_id": job_id, **kwargs}
    with _pending_lock:
        _pending.append(job)
    job_queue.put(job)
    return job_id


def remove_pending(job_id: str) -> None:
    with _pending_lock:
        _pending[:] = [j for j in _pending if j["job_id"] != job_id]


def get_pending() -> list[dict]:
    with _pending_lock:
        return list(_pending)


# ── SQLite job history ─────────────────────────────────────────
def init_db() -> None:
    """Create the history DB and table if they don't exist."""
    APP_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with sqlite3.connect(DB_FILE) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS job_history (
                    id      INTEGER PRIMARY KEY AUTOINCREMENT,
                    time    TEXT    NOT NULL,
                    action  TEXT    NOT NULL,
                    details TEXT    NOT NULL,
                    status  TEXT    NOT NULL DEFAULT 'done',
                    color   TEXT    NOT NULL DEFAULT 'green'
                )
            """)
            conn.commit()
    except Exception:
        log.exception("Failed to initialize history DB")


def log_job(
    action: str,
    details: str,
    status: str = "done",
    color: str = "green",
) -> None:
    """Persist a job event to the SQLite history DB."""
    ts = datetime.now().strftime("%b %d %H:%M:%S")
    try:
        with sqlite3.connect(DB_FILE) as conn:
            conn.execute(
                "INSERT INTO job_history (time, action, details, status, color) VALUES (?,?,?,?,?)",
                (ts, action, details, status, color),
            )
            conn.commit()
    except Exception:
        log.exception("Failed to write job history")


def get_history(limit: int = 100) -> list[dict]:
    """Return the most recent job history entries."""
    try:
        with sqlite3.connect(DB_FILE) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM job_history ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        log.exception("Failed to read job history")
        return []


# ── Settings persistence ───────────────────────────────────────
def load_settings() -> None:
    """Load saved settings from disk, merging into defaults."""
    APP_DIR.mkdir(parents=True, exist_ok=True)
    try:
        if SETTINGS_FILE.exists():
            with open(SETTINGS_FILE) as f:
                settings.update(json.load(f))
    except Exception:
        log.exception("Failed to load settings — using defaults")


def save_settings() -> None:
    """Persist current settings to disk."""
    APP_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(settings, f, indent=2)
    except Exception:
        log.exception("Failed to save settings")


# ── Path validation ────────────────────────────────────────────
def is_allowed_path(filepath: str) -> bool:
    """
    Return True only if filepath resolves to within a configured
    scan_folder or the app temp directory. Prevents path traversal.
    """
    resolved = Path(filepath).resolve()
    allowed_roots = [Path(f).resolve() for f in settings.get("scan_folders", [])]
    allowed_roots.append(Path("/tmp"))   # temp files we create ourselves
    return any(
        resolved == root or resolved.is_relative_to(root)
        for root in allowed_roots
    )


# ── Formatting helper ──────────────────────────────────────────
def format_size(size_bytes: int) -> str:
    if size_bytes < 1024 * 1024:
        return f"{size_bytes // 1024}KB"
    return f"{size_bytes // (1024 * 1024)}MB"
