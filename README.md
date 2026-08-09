# 🖨️ PrintStation

A lightweight, web-based print management dashboard for Linux. Built on top of CUPS, it gives you a clean browser UI to browse, queue, and monitor print jobs — with real-time progress tracking, ink level monitoring, and multi-copy support.

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Platform](https://img.shields.io/badge/platform-Linux-orange)

---

## 💡 The Origin Story: Why I Built This

I built **PrintStation** out of necessity while preparing to print chapters of my **graduation manuscript/thesis**. 

Midway through writing, my laptop's GPU suffered hardware damage (RAM artifacting), forcing me to switch over to a Linux environment. While Linux was great for performance, managing high-volume document printing and fine-tuning print settings (reverse page collating, multi-copy batches, ink status checks, and image-to-PDF formatting) through standard dialogs and stock CUPS interfaces felt clunky and frustrating during crunch time.

Rather than fighting with standard print options, I decided to engineer my own web-based solution to streamline the entire workflow directly from the browser.

---

## ✨ Features

- 📂 **Browse local folders** for PDF and image files (JPG, PNG, WebP, BMP)
- 🖨️ **Print with full control** — page range, copies, paper size, print quality
- 🔄 **Real-time job status** via Server-Sent Events
- 🎨 **Image-to-PDF conversion** — fit, fill, or natural sizing with margin control
- 🖼️ **Image filters** — none, grayscale, or scanner-style high-contrast
- 📊 **Ink level monitoring** from CUPS marker data
- 🗂️ **Job history** — track every print action
- ⚙️ **Configurable settings** — default printer, scan folders, media size
- 📤 **File upload** via drag-and-drop in browser
- 🔁 **Auto-restart & enable printer** via CUPS integration
- 🧩 **systemd service** — auto-start on boot

---

## 🧰 Requirements

- Linux with [CUPS](https://www.cups.org/) installed and configured
- Python 3.10+
- A CUPS-compatible printer

---

## 🚀 Quick Start

### 1. Clone the repository

```bash
git clone https://github.com/YOUR_USERNAME/printstation.git
cd printstation
```

### 2. Install Python dependencies

```bash
pip install -r requirements.txt
# or, on system Python without virtualenv:
pip install -r requirements.txt --break-system-packages
```

### 3. Run the server

```bash
python3 app.py
```

Open your browser at **http://localhost:5000**

---

## 📦 Install as a System Service (Auto-start on Boot)

### Option A: Automated install script

```bash
chmod +x install.sh
./install.sh
```

This will:
- Copy files to `~/.printstation/`
- Install Python dependencies
- Register and enable a systemd service
- Add a `sudoers` rule for printer management (no password prompt)
- Create a desktop launcher

### Option B: Manual systemd setup

1. Edit `printstation.service` — replace `YOUR_USERNAME` with your actual Linux username
2. Copy to systemd:
   ```bash
   sudo cp printstation.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now printstation
   ```

---

## ⚙️ Configuration

Settings are persisted in `~/.printstation_settings.json`. You can configure them from the **Settings** panel in the UI, or edit the JSON directly:

```json
{
  "default_printer": "YourPrinterName",
  "default_copies": 1,
  "scan_folders": [
    "~/Downloads",
    "~/Documents"
  ],
  "auto_reverse": true,
  "media": "Letter"
}
```

> **Tip:** Add any custom folders here and they'll appear in the folder dropdown.

---

## 🖨️ Printer Setup (CUPS)

PrintStation talks directly to CUPS. Make sure your printer is already set up:

```bash
# Check installed printers
lpstat -p

# The printer name shown there is what PrintStation uses
```

If your printer keeps going offline, use the **"Enable Printer"** button in the dashboard or the **"Restart CUPS"** button.

### Passwordless sudo for printer management

The install script adds a sudoers rule so PrintStation can re-enable printers without a password prompt. You can add it manually:

```bash
echo "$USER ALL=(ALL) NOPASSWD: /usr/bin/cupsenable, /usr/bin/cupsaccept, /usr/sbin/lpadmin, /usr/bin/systemctl restart cups" \
  | sudo tee /etc/sudoers.d/printstation
```

---

## 📡 API Reference

| Endpoint | Method | Description |
|---|---|---|
| `/api/printers` | GET | List all CUPS printers |
| `/api/ink?printer=NAME` | GET | Ink levels for a printer |
| `/api/files?folder=PATH` | GET | List printable files in a folder |
| `/api/pdf-info?path=PATH` | GET | Page count & validation for a file |
| `/api/print` | POST | Submit a print job |
| `/api/status` | GET | Current job status |
| `/api/stream` | GET | SSE stream of live job status |
| `/api/history` | GET | Recent job history |
| `/api/cancel` | POST | Cancel all jobs on a printer |
| `/api/enable` | POST | Re-enable a disabled printer |
| `/api/restart-cups` | POST | Restart the CUPS service |
| `/api/settings` | GET/POST | Get or update settings |
| `/api/upload` | POST | Upload a file to a scan folder |

---

## 🗂️ Project Structure

```
printstation/
├── app.py                  # Flask backend — all API routes & print logic
├── static/
│   └── index.html          # Single-page frontend (vanilla HTML/CSS/JS)
├── install.sh              # One-shot system installer
├── printstation.service    # systemd service template
├── requirements.txt        # Python dependencies
└── LICENSE
```

---

## 🤝 Contributing

Pull requests are welcome! For major changes, please open an issue first.

1. Fork the repo
2. Create your feature branch: `git checkout -b feature/my-feature`
3. Commit your changes: `git commit -m 'Add some feature'`
4. Push to the branch: `git push origin feature/my-feature`
5. Open a Pull Request

---

## 📄 License

MIT — see [LICENSE](LICENSE) for details.
