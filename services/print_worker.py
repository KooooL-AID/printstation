"""
services/print_worker.py — Queue-based background print job processor.

A single persistent daemon thread pulls jobs from config.job_queue
and executes them sequentially. Each job has its own cancel event,
so individual jobs can be cancelled without affecting the queue.
"""

import os
import time
import tempfile
import threading

import config
from config import log, update_active_job, get_active_job, log_job, remove_pending
from services.cups_service import send_to_printer, enable_printer
from services.pdf_service import reverse_pages

# ── Per-job cancel events: {job_id: threading.Event} ──────────
_cancel_events: dict[str, threading.Event] = {}
_cancel_lock   = threading.Lock()


def _register(job_id: str) -> threading.Event:
    event = threading.Event()
    with _cancel_lock:
        _cancel_events[job_id] = event
    return event


def _unregister(job_id: str) -> None:
    with _cancel_lock:
        _cancel_events.pop(job_id, None)


def cancel_job(job_id: str) -> bool:
    """Signal a specific job to stop. Returns True if found."""
    with _cancel_lock:
        ev = _cancel_events.get(job_id)
        if ev:
            ev.set()
            return True
    return False


def cancel_current() -> None:
    """Cancel whichever job is currently active."""
    job_id = get_active_job().get("job_id")
    if job_id:
        cancel_job(job_id)


# ── Core print logic ───────────────────────────────────────────
def _run_job(job: dict) -> None:
    """Execute one print job. Called exclusively by queue_worker."""
    job_id  = job["job_id"]
    filepath = job["filepath"]
    printer  = job["printer"]
    start    = job["start"]
    end      = job["end"]
    copies   = job["copies"]
    media    = job["media"]
    quality  = job.get("quality",  "normal")
    duplex   = job.get("duplex",   False)

    fname  = os.path.basename(filepath)
    pages  = end - start + 1
    cancel = _register(job_id)
    tmp_out: str | None = None

    try:
        update_active_job(
            running=True, job_id=job_id, file=fname,
            printer=printer, copies=copies,
            batches=1, status="preparing", message="",
        )

        for copy_num in range(1, copies + 1):
            if cancel.is_set():
                update_active_job(
                    status="cancelled",
                    message=f"🚫 Cancelled after {copy_num - 1} cop{'y' if copy_num == 2 else 'ies'}",
                )
                log_job(f"Cancelled {fname}", f"After {copy_num - 1}/{copies} copies · {printer}", "warn", "yellow")
                return

            update_active_job(
                copy=copy_num, batch=1,
                message=f"Preparing copy {copy_num} of {copies}...",
                progress=0, total=pages, status="reversing",
            )

            # Isolated temp file — avoids /tmp conflicts between jobs
            with tempfile.NamedTemporaryFile(
                suffix=".pdf", prefix="ps_out_", delete=False
            ) as tf:
                tmp_out = tf.name

            reverse_pages(filepath, start, end, tmp_out)

            update_active_job(
                progress=pages,
                message=f"Sending copy {copy_num}/{copies} to {printer}...",
                status="printing",
            )

            ok, msg = send_to_printer(tmp_out, printer, media, quality, duplex)

            if ok:
                update_active_job(message=f"✅ Copy {copy_num}/{copies} sent! {msg}")
                log_job(
                    f"Printed {fname}",
                    f"Copy {copy_num}/{copies} · Pages {start}-{end} ({pages}p) · {printer}",
                )
                if copy_num < copies and not cancel.is_set():
                    update_active_job(message=f"✅ Copy {copy_num} done! Preparing next...")
                    time.sleep(2)
            else:
                update_active_job(message=f"❌ Copy {copy_num} failed: {msg}")
                log_job(f"Failed {fname}", f"Copy {copy_num}/{copies}: {msg}", "error", "red")
                enable_printer(printer)   # attempt recovery
                time.sleep(2)

        suffix = "y" if copies == 1 else "ies"
        update_active_job(
            running=False, status="done",
            message=f"🎉 All {copies} cop{suffix} of {fname} printed!",
        )

    except Exception as exc:
        log.exception(f"Job {job_id} crashed")
        update_active_job(running=False, status="error", message=f"❌ Error: {exc}")
        log_job(f"Error {fname}", str(exc), "error", "red")

    finally:
        _unregister(job_id)
        remove_pending(job_id)
        # Clean up temp files we created
        for path in filter(None, [tmp_out, filepath]):
            if path.startswith("/tmp/") and "ps_" in os.path.basename(path):
                try:
                    os.remove(path)
                except OSError:
                    pass


# ── Queue worker (daemon thread) ───────────────────────────────
def queue_worker() -> None:
    """
    Persistent daemon thread. Blocks on config.job_queue and
    processes jobs one at a time, sequentially.
    """
    log.info("🖨️  Print queue worker started.")
    while True:
        job = config.job_queue.get()   # blocks until a job arrives
        try:
            _run_job(job)
        except Exception:
            log.exception("Unhandled error in queue worker — continuing")
        finally:
            config.job_queue.task_done()
