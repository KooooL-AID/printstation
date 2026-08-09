"""
services/pdf_service.py — PDF and image processing utilities.

Handles:
  - File validation (PDF page count, image verify)
  - Image-to-PDF conversion with fit/fill/natural modes, filters, and auto-rotation
  - PDF page reversal for back-to-front collation
"""

import os
import time
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from PIL import Image, ImageOps, ImageEnhance

# ── Supported file extensions ──────────────────────────────────
IMAGE_EXTS = frozenset(("jpg", "jpeg", "png", "webp", "bmp"))
PDF_EXT    = "pdf"

# ── Page sizes in points (72 pt = 1 inch) ─────────────────────
PAGE_SIZES: dict[str, tuple[int, int]] = {
    "A4":     (595, 842),
    "Letter": (612, 792),
    "Legal":  (612, 1008),
}

MARGIN_PX: dict[str, int] = {
    "none":   0,
    "small":  28,   # ~10 mm
    "medium": 56,   # ~20 mm
}

# ── Display colour overrides for CUPS ink markers ──────────────
INK_DISPLAY_COLORS: dict[str, str] = {
    "#000000": "#555",
    "#00FFFF": "#00d4ff",
    "#FF00FF": "#ff69b4",
    "#FFFF00": "#ffd93d",
}


def get_file_ext(filepath: str) -> str:
    return Path(filepath).suffix.lstrip(".").lower()


def get_file_info(filepath: str) -> dict:
    """Return page count, validity, and type for a PDF or image file."""
    ext = get_file_ext(filepath)
    if ext == PDF_EXT:
        try:
            r = PdfReader(filepath)
            return {"pages": len(r.pages), "valid": True, "type": "pdf"}
        except Exception as e:
            return {"pages": 0, "valid": False, "error": str(e), "type": "pdf"}
    elif ext in IMAGE_EXTS:
        try:
            with Image.open(filepath) as img:
                img.verify()
            return {"pages": 1, "valid": True, "type": "image"}
        except Exception as e:
            return {"pages": 0, "valid": False, "error": str(e), "type": "image"}
    return {"pages": 0, "valid": False, "error": "Unsupported file format", "type": "unknown"}


def _apply_filter(img: Image.Image, filter_type: str) -> Image.Image:
    """Apply grayscale or scanner-style contrast filter to a PIL image."""
    if filter_type == "grayscale":
        return ImageOps.grayscale(img).convert("RGB")
    if filter_type == "scanner":
        gray = ImageOps.grayscale(img)
        gray = ImageEnhance.Contrast(gray).enhance(2.5)
        gray = ImageEnhance.Brightness(gray).enhance(1.2)
        return gray.convert("RGB")
    return img


def _scale_image(
    img: Image.Image,
    printable_w: int,
    printable_h: int,
    fit: str,
) -> Image.Image:
    """Resize/crop image according to the chosen fit mode."""
    iw, ih = img.size
    if fit == "fit":
        ratio = min(printable_w / iw, printable_h / ih)
        return img.resize((int(iw * ratio), int(ih * ratio)), Image.Resampling.LANCZOS)
    if fit == "fill":
        ratio = max(printable_w / iw, printable_h / ih)
        nw, nh = int(iw * ratio), int(ih * ratio)
        img = img.resize((nw, nh), Image.Resampling.LANCZOS)
        left = (nw - printable_w) / 2
        top  = (nh - printable_h) / 2
        return img.crop((left, top, left + printable_w, top + printable_h))
    # natural — only downscale if too large
    if iw > printable_w or ih > printable_h:
        ratio = min(printable_w / iw, printable_h / ih)
        return img.resize((int(iw * ratio), int(ih * ratio)), Image.Resampling.LANCZOS)
    return img


def images_to_pdf(
    image_paths: list[str],
    output_path: str,
    media: str = "A4",
    fit: str = "fit",
    margin: str = "small",
    filter_type: str = "none",
    auto_rotate: bool = True,
) -> int:
    """
    Convert a list of image files into a single multi-page PDF.

    Returns the number of pages written.
    Raises ValueError if no images could be processed.
    """
    pw, ph = PAGE_SIZES.get(media, PAGE_SIZES["A4"])
    margin_px = MARGIN_PX.get(margin, MARGIN_PX["small"])
    printable_w = pw - 2 * margin_px
    printable_h = ph - 2 * margin_px

    pages: list[Image.Image] = []
    for path in image_paths:
        try:
            img = ImageOps.exif_transpose(Image.open(path))
            if img.mode != "RGB":
                img = img.convert("RGB")
            img = _apply_filter(img, filter_type)

            if auto_rotate:
                page_portrait = ph > pw
                img_portrait  = img.height > img.width
                if page_portrait != img_portrait:
                    img = img.rotate(90, expand=True)

            scaled = _scale_image(img, printable_w, printable_h, fit)
            canvas = Image.new("RGB", (int(pw), int(ph)), "white")
            paste_x = (int(pw) - scaled.width)  // 2
            paste_y = (int(ph) - scaled.height) // 2
            canvas.paste(scaled, (paste_x, paste_y))
            pages.append(canvas)
        except Exception as exc:
            print(f"[pdf_service] Skipping {path}: {exc}")

    if not pages:
        raise ValueError("No valid images could be processed.")

    pages[0].save(
        output_path, "PDF", resolution=72.0,
        save_all=True, append_images=pages[1:],
    )
    return len(pages)


def reverse_pages(filepath: str, start: int, end: int, out_path: str) -> str:
    """
    Write pages [start..end] in reverse order to out_path.
    Returns out_path.
    """
    reader = PdfReader(filepath)
    writer = PdfWriter()
    for i in reversed(range(start - 1, end)):
        writer.add_page(reader.pages[i])
    with open(out_path, "wb") as f:
        writer.write(f)
    return out_path


def build_combined_pdf(
    filepaths: list[str],
    media: str,
    fit: str,
    margin: str,
    filter_type: str,
    auto_rotate: bool,
) -> tuple[str, int]:
    """
    Merge a list of PDFs and/or images into a single combined PDF.

    Returns (combined_pdf_path, total_pages).
    Cleans up any intermediate per-image temp files.
    """
    writer      = PdfWriter()
    temp_images: list[str] = []

    try:
        ts = int(time.time())
        for idx, path in enumerate(filepaths):
            ext = get_file_ext(path)
            if ext in IMAGE_EXTS:
                img_tmp = f"/tmp/printstation_img_{ts}_{idx}.pdf"
                images_to_pdf(
                    [path], img_tmp,
                    media=media, fit=fit, margin=margin,
                    filter_type=filter_type, auto_rotate=auto_rotate,
                )
                temp_images.append(img_tmp)
                for page in PdfReader(img_tmp).pages:
                    writer.add_page(page)
            elif ext == PDF_EXT:
                for page in PdfReader(path).pages:
                    writer.add_page(page)
            else:
                raise ValueError(f"Unsupported file type: .{ext}")

        combined = f"/tmp/printstation_combined_{ts}.pdf"
        with open(combined, "wb") as f:
            writer.write(f)
        return combined, len(writer.pages)

    finally:
        for f in temp_images:
            try:
                os.remove(f)
            except OSError:
                pass
