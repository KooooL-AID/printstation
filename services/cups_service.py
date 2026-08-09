"""
services/cups_service.py — CUPS and system printer interaction.

Wraps all subprocess calls to lp, lpstat, cupsenable, etc.
Each function returns structured data; no Flask imports here.
"""

import time
import subprocess

from config import log

# ── Display colour overrides for CUPS ink markers ──────────────
INK_DISPLAY_COLORS: dict[str, str] = {
    "#000000": "#555",
    "#00FFFF": "#00d4ff",
    "#FF00FF": "#ff69b4",
    "#FFFF00": "#ffd93d",
}

QUALITY_MAP = {"draft": "3", "normal": "4", "high": "5"}


# ── Internal helper ────────────────────────────────────────────
def _run(*cmd: str, timeout: int = 10) -> subprocess.CompletedProcess:
    """Run a command, log errors, never raise."""
    try:
        return subprocess.run(
            list(cmd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        log.warning(f"Command timed out: {' '.join(cmd)}")
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="timeout")
    except Exception as exc:
        log.error(f"Command failed {cmd}: {exc}")
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr=str(exc))


# ── Printer discovery ──────────────────────────────────────────
def list_printers() -> list[dict]:
    """Return a list of printer dicts with name and status."""
    result = _run("lpstat", "-p")
    printers = []
    for line in result.stdout.splitlines():
        if not line.startswith("printer"):
            continue
        parts    = line.split()
        name     = parts[1]
        busy     = "processing" in line
        online   = "idle" in line or busy
        disabled = "disabled" in line
        status   = "busy" if busy else ("online" if online else ("disabled" if disabled else "offline"))
        printers.append({"name": name, "status": status, "raw": line})
    return printers


# ── Ink levels ─────────────────────────────────────────────────
def get_ink_levels(printer: str) -> list[dict]:
    """Parse CUPS marker data and return ink level dicts."""
    result = _run("lpstat", "-p", printer, "-l")

    levels: list[int] = []
    names:  list[str] = []
    colors: list[str] = []
    lows:   list[int] = []

    for raw in result.stdout.splitlines():
        line = raw.strip()
        if "marker-levels=" in line:
            levels = [int(v) for v in line.split("=")[1].split(",")]
        elif "marker-names=" in line:
            names = [
                n.replace("\\", "").replace("'", "").strip()
                for n in line.split("=")[1].split(",")
            ]
        elif "marker-colors=" in line:
            colors = line.split("=")[1].split(",")
        elif "marker-low-levels=" in line:
            lows = [int(v) for v in line.split("=")[1].split(",")]

    ink_data = []
    for i, (name, level) in enumerate(zip(names, levels)):
        raw_color = colors[i] if i < len(colors) else "#888888"
        display   = INK_DISPLAY_COLORS.get(raw_color, "#888888")
        low_mark  = lows[i] if i < len(lows) else 15
        ink_data.append({
            "name":     name.replace(" ink", "").replace(" Ink", ""),
            "level":    level,
            "color":    display,
            "low":      level <= low_mark,
            "critical": level <= 10,
        })
    return ink_data


# ── CUPS queue ─────────────────────────────────────────────────
def get_cups_queue() -> str:
    result = _run("lpstat", "-o")
    return result.stdout.strip() or "Empty"


# ── Printer control ────────────────────────────────────────────
def enable_printer(printer: str) -> None:
    _run("sudo", "cupsenable", printer)
    _run("sudo", "cupsaccept", printer)


def cancel_jobs(printer: str) -> None:
    _run("cancel", "-a", printer)


def restart_cups(default_printer: str) -> None:
    _run("sudo", "systemctl", "restart", "cups", timeout=30)
    time.sleep(2)
    _run("sudo", "cupsenable", default_printer)


# ── Print command ──────────────────────────────────────────────
def send_to_printer(
    filepath: str,
    printer: str,
    media: str,
    quality: str,
    duplex: bool = False,
) -> tuple[bool, str]:
    """
    Send filepath to CUPS via `lp`.
    Supports duplex (two-sided long-edge) printing.
    Returns (success, message).
    """
    cups_quality = QUALITY_MAP.get(quality, "4")
    cmd = [
        "lp", "-d", printer,
        "-o", f"PageSize={media}",
        "-o", "MediaType=Stationery",
        "-o", "ColorModel=RGB",
        "-o", f"print-quality={cups_quality}",
        "-o", "print-scaling=none",
    ]
    if duplex:
        cmd += ["-o", "sides=two-sided-long-edge"]

    cmd.append(filepath)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        return True, result.stdout.strip()
    return False, result.stderr.strip() or result.stdout.strip()
