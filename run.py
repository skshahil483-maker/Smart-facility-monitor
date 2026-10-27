"""
run.py – Application entry point.

Usage:
    python run.py
    flask run  (with FLASK_APP=run.py)
"""

import os
from app import create_app

app = create_app(os.environ.get("FLASK_ENV", "development"))

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)
