"""
config.py — Shared state and settings management for PrintStation.

Do NOT import from services here — this module is imported by everyone.
"""

import os
import json
from datetime import datetime

# ── Paths ──────────────────────────────────────────────────────
SETTINGS_FILE = os.path.expanduser("~/.printstation_settings.json")
TMP_OUTPUT    = "/tmp/printstation_output.pdf"
TMP_COMBINED  = "/tmp/printstation_combined_{ts}.pdf"
TMP_IMG       = "/tmp/printstation_img_{ts}_{n}.pdf"

# ── Default Settings ───────────────────────────────────────────
settings: dict = {
    "default_printer": "L5290",
    "default_copies": 1,
    "scan_folders": [
        os.path.expanduser("~/Downloads"),
        os.path.expanduser("~/Documents"),
    ],
    "auto_reverse": True,
    "media": "Letter",
}

# ── Job State ──────────────────────────────────────────────────
active_job: dict = {
    "running": False, "status": "idle", "message": "",
    "progress": 0, "total": 0, "file": "", "printer": "",
    "copy": 0, "copies": 0, "batch": 0, "batches": 0, "queue": "",
}

job_history: list = []

# ── Settings Persistence ───────────────────────────────────────
def load_settings() -> None:
    """Load saved settings from disk, merging into defaults."""
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE) as f:
                settings.update(json.load(f))
    except Exception:
        pass


def save_settings() -> None:
    """Persist current settings to disk."""
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(settings, f, indent=2)
    except Exception:
        pass


# ── History Helper ─────────────────────────────────────────────
def log_job(action: str, details: str, status: str = "done", color: str = "green") -> None:
    """Prepend an entry to job_history, capped at 100 entries."""
    job_history.insert(0, {
        "id": len(job_history) + 1,
        "time": datetime.now().strftime("%b %d %H:%M:%S"),
        "action": action,
        "details": details,
        "status": status,
        "color": color,
    })
    if len(job_history) > 100:
        job_history.pop()


# ── Formatting Helper ──────────────────────────────────────────
def format_size(size_bytes: int) -> str:
    """Return a human-readable file size string."""
    if size_bytes < 1024 * 1024:
        return f"{size_bytes // 1024}KB"
    return f"{size_bytes // (1024 * 1024)}MB"
