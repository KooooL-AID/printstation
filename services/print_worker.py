"""
services/print_worker.py — Background print job thread.

Runs in a daemon thread; mutates shared active_job state and
delegates all CUPS and PDF work to the service modules.
"""

import os
import time

import config
from services.cups_service import send_to_printer, enable_printer
from services.pdf_service import reverse_pages

TMP_OUTPUT = "/tmp/printstation_output.pdf"


def run(
    filepath: str,
    printer: str,
    start: int,
    end: int,
    copies: int,
    media: str,
    quality: str = "normal",
) -> None:
    """
    Execute a multi-copy print job.

    Reverses pages before each copy (for back-to-front collation),
    sends to CUPS, and updates config.active_job throughout.
    Cleans up temp files on exit.
    """
    fname = os.path.basename(filepath)
    pages = end - start + 1
    job   = config.active_job  # local alias for readability

    try:
        job.update({
            "running": True, "file": fname,
            "printer": printer, "copies": copies,
            "batches": 1, "status": "preparing",
        })

        for copy_num in range(1, copies + 1):
            job.update({
                "copy": copy_num, "batch": 1,
                "message": f"Preparing copy {copy_num} of {copies}...",
                "progress": 0, "total": pages, "status": "reversing",
            })

            out_file = reverse_pages(filepath, start, end, TMP_OUTPUT)

            job.update({
                "progress": pages,
                "message": f"Sending copy {copy_num}/{copies} to {printer}...",
                "status": "printing",
            })

            ok, msg = send_to_printer(out_file, printer, media, quality)

            if ok:
                job["message"] = f"✅ Copy {copy_num}/{copies} sent! {msg}"
                config.log_job(
                    f"Printed {fname}",
                    f"Copy {copy_num}/{copies} · Pages {start}-{end} ({pages}p) · {printer}",
                )
                if copy_num < copies:
                    job["message"] = f"✅ Copy {copy_num} done! Preparing copy {copy_num + 1}..."
                    time.sleep(2)
            else:
                job["message"] = f"❌ Copy {copy_num} failed: {msg}"
                config.log_job(
                    f"Failed {fname}",
                    f"Copy {copy_num}/{copies}: {msg}",
                    status="error", color="red",
                )
                enable_printer(printer)   # attempt recovery
                time.sleep(2)

        suffix = "y" if copies == 1 else "ies"
        job.update({
            "running": False,
            "status": "done",
            "message": f"🎉 All {copies} cop{suffix} of {fname} printed!",
        })

    except Exception as exc:
        job.update({
            "running": False, "status": "error",
            "message": f"❌ Error: {exc}",
        })
        config.log_job(f"Error {fname}", str(exc), status="error", color="red")

    finally:
        # Clean up temp files
        for path in (TMP_OUTPUT, filepath):
            if path and path.startswith("/tmp/printstation"):
                try:
                    os.remove(path)
                except OSError:
                    pass
