#!/usr/bin/env python3
"""
PrintStation - Local Print Management Server
A web-based dashboard for managing CUPS print jobs on Linux.

Run:  python3 app.py
Open: http://localhost:5000
"""

import os
from flask import Flask
from flask_cors import CORS

import config
from routes.api import bp as api_blueprint


def create_app() -> Flask:
    app = Flask(__name__, static_folder="static")
    CORS(app)
    app.register_blueprint(api_blueprint)
    return app


if __name__ == "__main__":
    config.load_settings()
    os.makedirs("static", exist_ok=True)

    print("\n" + "=" * 50)
    print("  🖨️  PrintStation")
    print("=" * 50)
    print("  Open: http://localhost:5000")
    print("  Ctrl+C to stop")
    print("=" * 50 + "\n")

    app = create_app()
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
