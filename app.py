#!/usr/bin/env python3
"""
PrintStation - Local Print Management Server
A web-based dashboard for managing CUPS print jobs on Linux.
Run: python3 app.py
Open: http://localhost:5000
"""

from flask import Flask, jsonify, request, send_from_directory, Response
from flask_cors import CORS
import subprocess, threading, os, json, time
from datetime import datetime
from pypdf import PdfReader, PdfWriter
import glob
from PIL import Image, ImageOps, ImageEnhance

app = Flask(__name__, static_folder='static')
CORS(app)

# ── State ──────────────────────────────────────────────────
job_history = []
active_job = {
    "running": False, "status": "idle", "message": "",
    "progress": 0, "total": 0, "file": "", "printer": "",
    "copy": 0, "copies": 0, "batch": 0, "batches": 0
}
settings = {
    "default_printer": "L5290",
    "default_copies": 1,
    "scan_folders": [
        os.path.expanduser("~/Downloads"),
        os.path.expanduser("~/Documents"),
    ],
    "auto_reverse": True,
    "media": "Letter"
}

SETTINGS_FILE = os.path.expanduser("~/.printstation_settings.json")

def load_settings():
    global settings
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE) as f:
                settings.update(json.load(f))
    except: pass

def save_settings():
    try:
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(settings, f, indent=2)
    except: pass

def log_job(action, details, status="done", color="green"):
    job_history.insert(0, {
        "id": len(job_history) + 1,
        "time": datetime.now().strftime("%b %d %H:%M:%S"),
        "action": action,
        "details": details,
        "status": status,
        "color": color
    })
    if len(job_history) > 100:
        job_history.pop()

# ── PDF & Image Utils ────────────────────────────────────────
def get_file_info(filepath):
    ext = filepath.lower().split('.')[-1]
    if ext == 'pdf':
        try:
            r = PdfReader(filepath)
            return {"pages": len(r.pages), "valid": True, "type": "pdf"}
        except Exception as e:
            return {"pages": 0, "valid": False, "error": str(e), "type": "pdf"}
    elif ext in ('jpg', 'jpeg', 'png', 'webp', 'bmp'):
        try:
            with Image.open(filepath) as img:
                img.verify()
            return {"pages": 1, "valid": True, "type": "image"}
        except Exception as e:
            return {"pages": 0, "valid": False, "error": str(e), "type": "image"}
    else:
        return {"pages": 0, "valid": False, "error": "Unsupported file format", "type": "unknown"}

def get_pdf_info(filepath):
    return get_file_info(filepath)

def images_to_a4_pdf(image_paths, output_pdf_path, media="A4", fit="fit", margin="small", filter_type="none", auto_rotate=True):
    sizes = {
        "A4": (595, 842),
        "Letter": (612, 792),
        "Legal": (612, 1008)
    }
    
    page_width, page_height = sizes.get(media, (595, 842))
    
    margin_dims = {
        "none": 0,
        "small": 28,  # ~10mm
        "medium": 56  # ~20mm
    }
    margin_px = margin_dims.get(margin, 28)
    
    printable_width = page_width - 2 * margin_px
    printable_height = page_height - 2 * margin_px
    
    pil_images = []
    
    for path in image_paths:
        try:
            img = Image.open(path)
            img = ImageOps.exif_transpose(img)
            
            if img.mode != 'RGB':
                img = img.convert('RGB')
                
            if filter_type == 'grayscale':
                img = ImageOps.grayscale(img).convert('RGB')
            elif filter_type == 'scanner':
                gray = ImageOps.grayscale(img)
                # boost contrast & brightness to make document look scanned
                enhancer_contrast = ImageEnhance.Contrast(gray)
                gray = enhancer_contrast.enhance(2.5)
                enhancer_brightness = ImageEnhance.Brightness(gray)
                gray = enhancer_brightness.enhance(1.2)
                img = gray.convert('RGB')
            
            img_w, img_h = img.size
            
            if auto_rotate:
                page_is_portrait = page_height > page_width
                img_is_portrait = img_h > img_w
                if page_is_portrait != img_is_portrait:
                    img = img.rotate(90, expand=True)
                    img_w, img_h = img.size
            
            canvas = Image.new('RGB', (int(page_width), int(page_height)), 'white')
            
            if fit == 'fit':
                ratio = min(printable_width / img_w, printable_height / img_h)
                new_w = int(img_w * ratio)
                new_h = int(img_h * ratio)
                img_resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            elif fit == 'fill':
                ratio = max(printable_width / img_w, printable_height / img_h)
                new_w = int(img_w * ratio)
                new_h = int(img_h * ratio)
                img_resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
                left = (new_w - printable_width) / 2
                top = (new_h - printable_height) / 2
                img_resized = img_resized.crop((left, top, left + printable_width, top + printable_height))
            else:
                if img_w > printable_width or img_h > printable_height:
                    ratio = min(printable_width / img_w, printable_height / img_h)
                    new_w = int(img_w * ratio)
                    new_h = int(img_h * ratio)
                    img_resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
                else:
                    img_resized = img
                
            paste_x = int((page_width - img_resized.width) / 2)
            paste_y = int((page_height - img_resized.height) / 2)
            canvas.paste(img_resized, (paste_x, paste_y))
            
            pil_images.append(canvas)
        except Exception as e:
            print(f"Error processing image {path}: {e}")
            
    if not pil_images:
        raise ValueError("No valid images processed")
        
    first_img = pil_images[0]
    first_img.save(output_pdf_path, "PDF", resolution=72.0, save_all=True, append_images=pil_images[1:])
    return len(pil_images)

def reverse_pages(filepath, start, end):
    reader = PdfReader(filepath)
    writer = PdfWriter()
    pages = list(range(start - 1, end))
    for i in reversed(pages):
        writer.add_page(reader.pages[i])
    out = "/tmp/printstation_output.pdf"
    with open(out, "wb") as f:
        writer.write(f)
    return out

# ── Print Worker ────────────────────────────────────────────
def print_worker(filepath, printer, start, end, copies, media, quality="normal"):
    global active_job
    fname = os.path.basename(filepath)
    pages = end - start + 1

    try:
        active_job.update({
            "running": True, "file": fname,
            "printer": printer, "copies": copies,
            "batches": 1, "status": "preparing"
        })

        for copy_num in range(1, copies + 1):
            active_job.update({
                "copy": copy_num,
                "batch": 1,
                "message": f"Preparing copy {copy_num} of {copies}...",
                "progress": 0, "total": pages, "status": "reversing"
            })

            # Reverse pages
            out_file = reverse_pages(filepath, start, end)

            active_job.update({
                "progress": pages,
                "message": f"Sending copy {copy_num}/{copies} to {printer}...",
                "status": "printing"
            })

            # Build lp command with quality option
            # print-quality: 3=Draft, 4=Normal, 5=High
            quality_map = {"draft": "3", "normal": "4", "high": "5"}
            cups_quality = quality_map.get(quality, "4")
            cmd = [
                "lp", "-d", printer,
                "-o", f"PageSize={media}",
                "-o", "MediaType=Stationery",
                "-o", "ColorModel=RGB",
                "-o", f"print-quality={cups_quality}",
                "-o", "print-scaling=none",
                out_file
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode == 0:
                job_id = result.stdout.strip()
                active_job["message"] = f"✅ Copy {copy_num}/{copies} sent! {job_id}"
                log_job(
                    f"Printed {fname}",
                    f"Copy {copy_num}/{copies} · Pages {start}-{end} ({pages}p) · {printer}",
                    "done", "green"
                )
                # Wait between copies
                if copy_num < copies:
                    active_job["message"] = f"✅ Copy {copy_num} done! Preparing copy {copy_num+1}..."
                    time.sleep(2)
            else:
                err = result.stderr.strip() or result.stdout.strip()
                active_job["message"] = f"❌ Copy {copy_num} failed: {err}"
                log_job(f"Failed {fname}", f"Copy {copy_num}/{copies}: {err}", "error", "red")
                # Try to re-enable printer
                subprocess.run(["sudo", "cupsenable", printer], capture_output=True)
                time.sleep(2)

        active_job.update({
            "running": False,
            "status": "done",
            "message": f"🎉 All {copies} cop{'y' if copies==1 else 'ies'} of {fname} printed!"
        })

    except Exception as e:
        active_job.update({
            "running": False, "status": "error",
            "message": f"❌ Error: {str(e)}"
        })
        log_job(f"Error {fname}", str(e), "error", "red")
    finally:
        try: os.remove("/tmp/printstation_output.pdf")
        except: pass
        if filepath.startswith("/tmp/printstation_combined"):
            try: os.remove(filepath)
            except: pass

# ── API Routes ──────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/api/printers')
def get_printers():
    try:
        result = subprocess.run(['lpstat', '-p'], capture_output=True, text=True)
        printers = []
        for line in result.stdout.strip().split('\n'):
            if line.startswith('printer'):
                parts = line.split()
                name = parts[1]
                online = 'idle' in line or 'processing' in line
                busy = 'processing' in line
                disabled = 'disabled' in line
                status = 'busy' if busy else ('online' if online else ('disabled' if disabled else 'offline'))
                printers.append({"name": name, "status": status, "raw": line})
        return jsonify({"printers": printers, "default": settings["default_printer"]})
    except Exception as e:
        return jsonify({"printers": [], "error": str(e)})

@app.route('/api/ink')
def get_ink():
    try:
        printer = request.args.get('printer', 'L5290')
        result = subprocess.run(['lpstat', '-p', printer, '-l'], capture_output=True, text=True)
        levels, names, colors_map, highs, lows = [], [], {}, [], []

        for line in result.stdout.split('\n'):
            line = line.strip()
            if 'marker-levels=' in line:
                levels = [int(v) for v in line.split('=')[1].split(',')]
            if 'marker-names=' in line:
                raw = line.split('=')[1]
                names = [n.replace('\\','').replace("'",'').strip() for n in raw.split(',')]
            if 'marker-colors=' in line:
                raw = line.split('=')[1]
                colors_map = raw.split(',')
            if 'marker-high-levels=' in line:
                highs = [int(v) for v in line.split('=')[1].split(',')]
            if 'marker-low-levels=' in line:
                lows = [int(v) for v in line.split('=')[1].split(',')]

        ink_data = []
        display_colors = {'#000000': '#555', '#00FFFF': '#00d4ff', '#FF00FF': '#ff69b4', '#FFFF00': '#ffd93d'}

        for i, (name, level) in enumerate(zip(names, levels)):
            raw_color = colors_map[i] if i < len(colors_map) else '#888888'
            display = display_colors.get(raw_color, '#888888')
            low = lows[i] if i < len(lows) else 15
            ink_data.append({
                "name": name.replace(' ink','').replace(' Ink',''),
                "level": level,
                "color": display,
                "low": level <= low,
                "critical": level <= 10
            })

        return jsonify({"ink": ink_data, "printer": printer})
    except Exception as e:
        return jsonify({"ink": [], "error": str(e)})

@app.route('/api/files')
def list_files():
    try:
        folder = request.args.get('folder', settings['scan_folders'][0])
        files = []
        if os.path.exists(folder):
            for f in sorted(os.listdir(folder)):
                ext = f.lower().split('.')[-1]
                if ext in ('pdf', 'jpg', 'jpeg', 'png', 'webp', 'bmp'):
                    path = os.path.join(folder, f)
                    info = get_file_info(path)
                    if info.get("valid"):
                        size = os.path.getsize(path)
                        files.append({
                            "name": f,
                            "path": path,
                            "pages": info.get("pages", 0),
                            "type": info.get("type", "pdf"),
                            "size": f"{size // 1024}KB" if size < 1024*1024 else f"{size // (1024*1024)}MB",
                            "modified": datetime.fromtimestamp(os.path.getmtime(path)).strftime("%b %d %H:%M")
                        })
        return jsonify({"files": files, "folder": folder, "folders": settings["scan_folders"]})
    except Exception as e:
        return jsonify({"files": [], "error": str(e)})

@app.route('/api/print', methods=['POST'])
def print_file():
    global active_job
    if active_job["running"]:
        return jsonify({"error": "A print job is already running! Wait for it to finish."}), 400

    data = request.json
    filepath = data.get('filepath')
    filepaths = data.get('filepaths', [])
    printer = data.get('printer', settings['default_printer'])
    copies = int(data.get('copies', 1))
    media = data.get('media', settings['media'])
    quality = data.get('quality', 'normal')  # 'draft', 'normal', or 'high'

    if not filepaths and filepath:
        filepaths = [filepath]

    if not filepaths:
        return jsonify({"error": "No files specified"}), 400

    # Validate that all files exist
    for f in filepaths:
        if not os.path.exists(f):
            return jsonify({"error": f"File not found: {f}"}), 400

    needs_combine = len(filepaths) > 1 or (len(filepaths) == 1 and filepaths[0].lower().split('.')[-1] in ('jpg', 'jpeg', 'png', 'webp', 'bmp'))

    if needs_combine:
        img_opts = data.get('image_options', {})
        fit = img_opts.get('fit', 'fit')
        margin = img_opts.get('margin', 'small')
        filter_type = img_opts.get('filter', 'none')
        auto_rotate = img_opts.get('auto_rotate', True)

        temp_files_to_clean = []
        writer = PdfWriter()

        try:
            for path in filepaths:
                ext = path.lower().split('.')[-1]
                if ext in ('jpg', 'jpeg', 'png', 'webp', 'bmp'):
                    img_temp = f"/tmp/printstation_img_{int(time.time())}_{len(temp_files_to_clean)}.pdf"
                    images_to_a4_pdf(
                        [path], img_temp,
                        media=media, fit=fit, margin=margin,
                        filter_type=filter_type, auto_rotate=auto_rotate
                    )
                    temp_files_to_clean.append(img_temp)
                    reader = PdfReader(img_temp)
                    for page in reader.pages:
                        writer.add_page(page)
                elif ext == 'pdf':
                    reader = PdfReader(path)
                    for page in reader.pages:
                        writer.add_page(page)
                else:
                    raise ValueError(f"Unsupported file type: {ext}")

            temp_pdf_path = f"/tmp/printstation_combined_{int(time.time())}.pdf"
            with open(temp_pdf_path, "wb") as f:
                writer.write(f)

            filepath = temp_pdf_path
            start = 1
            end = len(writer.pages)

            for f in temp_files_to_clean:
                try: os.remove(f)
                except: pass
        except Exception as e:
            for f in temp_files_to_clean:
                try: os.remove(f)
                except: pass
            return jsonify({"error": f"Failed to prepare files: {str(e)}"}), 500
    else:
        # Single PDF
        filepath = filepaths[0]
        start = int(data.get('start', 1))
        end = int(data.get('end', 1))

        if start > end:
            return jsonify({"error": "Start page must be ≤ end page"}), 400

    # Re-enable printer just in case
    subprocess.run(["sudo", "cupsenable", printer], capture_output=True)

    thread = threading.Thread(target=print_worker, args=(filepath, printer, start, end, copies, media, quality))
    thread.daemon = True
    thread.start()

    fname = os.path.basename(filepaths[0]) if len(filepaths) == 1 else f"{len(filepaths)} files combined"
    log_job(f"Queued {fname}", f"Pages {start}-{end} · {copies} cop{'y' if copies==1 else 'ies'} · {printer}", "queued", "yellow")

    return jsonify({
        "message": "Print job started!",
        "command": f"p {fname} {printer} {start}-{end}" + (f" {copies}" if copies > 1 else "")
    })

@app.route('/api/status')
def get_status():
    # Also get CUPS queue
    try:
        q = subprocess.run(['lpstat', '-o'], capture_output=True, text=True)
        active_job["queue"] = q.stdout.strip() or "Empty"
    except:
        active_job["queue"] = ""
    return jsonify(active_job)

@app.route('/api/history')
def get_history():
    return jsonify({"history": job_history})

@app.route('/api/cancel', methods=['POST'])
def cancel():
    printer = request.json.get('printer', settings['default_printer'])
    subprocess.run(['cancel', '-a', printer], capture_output=True)
    active_job.update({"running": False, "status": "cancelled", "message": f"Cancelled all jobs on {printer}"})
    log_job("Cancelled", f"All jobs on {printer}", "warn", "yellow")
    return jsonify({"message": f"Cancelled all jobs on {printer}"})

@app.route('/api/enable', methods=['POST'])
def enable_printer():
    printer = request.json.get('printer', settings['default_printer'])
    subprocess.run(['sudo', 'cupsenable', printer], capture_output=True)
    subprocess.run(['sudo', 'cupsaccept', printer], capture_output=True)
    log_job("Enabled", f"Printer {printer} re-enabled", "done", "green")
    return jsonify({"message": f"Printer {printer} re-enabled"})

@app.route('/api/restart-cups', methods=['POST'])
def restart_cups():
    subprocess.run(['sudo', 'systemctl', 'restart', 'cups'], capture_output=True)
    time.sleep(2)
    subprocess.run(['sudo', 'cupsenable', settings['default_printer']], capture_output=True)
    log_job("CUPS Restarted", "Print service restarted", "done", "blue")
    return jsonify({"message": "CUPS restarted successfully"})

@app.route('/api/settings', methods=['GET'])
def get_settings():
    return jsonify(settings)

@app.route('/api/settings', methods=['POST'])
def update_settings():
    global settings
    data = request.json
    settings.update(data)
    save_settings()
    return jsonify({"message": "Settings saved!", "settings": settings})

@app.route('/api/pdf-info')
def pdf_info():
    path = request.args.get('path')
    if not path or not os.path.exists(path):
        return jsonify({"error": "File not found"}), 404
    return jsonify(get_pdf_info(path))

@app.route('/api/upload', methods=['POST'])
def upload_file():
    try:
        if 'file' not in request.files:
            return jsonify({"error": "No file part in the request"}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({"error": "No selected file"}), 400
            
        target_folder = request.form.get('folder', settings['scan_folders'][0])
        if not os.path.exists(target_folder):
            os.makedirs(target_folder, exist_ok=True)
            
        filename = os.path.basename(file.filename)
        dest_path = os.path.join(target_folder, filename)
        
        base, ext = os.path.splitext(filename)
        counter = 1
        while os.path.exists(dest_path):
            dest_path = os.path.join(target_folder, f"{base}_{counter}{ext}")
            counter += 1
            
        file.save(dest_path)
        
        info = get_file_info(dest_path)
        size = os.path.getsize(dest_path)
        
        return jsonify({
            "success": True,
            "file": {
                "name": os.path.basename(dest_path),
                "path": dest_path,
                "pages": info.get("pages", 1),
                "type": info.get("type", "image"),
                "size": f"{size // 1024}KB" if size < 1024*1024 else f"{size // (1024*1024)}MB",
                "modified": datetime.fromtimestamp(os.path.getmtime(dest_path)).strftime("%b %d %H:%M")
            }
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── SSE Status Stream ───────────────────────────────────────
@app.route('/api/stream')
def stream():
    def generate():
        while True:
            data = json.dumps(active_job)
            yield f"data: {data}\n\n"
            time.sleep(1)
    return Response(generate(), mimetype='text/event-stream',
                   headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

if __name__ == '__main__':
    load_settings()
    os.makedirs('static', exist_ok=True)
    print("\n" + "="*50)
    print("  🖨️  PrintStation")
    print("="*50)
    print("  Open: http://localhost:5000")
    print("  Ctrl+C to stop")
    print("="*50 + "\n")
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
