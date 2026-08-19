#!/usr/bin/env bash
set -e

if [ ! -d "venv" ]; then
    echo "Creating virtual environment and installing dependencies..."
    python3 -m venv venv
    ./venv/bin/pip install -r requirements.txt
fi

echo "Starting Distributed Secure File Storage & Deduplication Service..."
echo "Web Dashboard available at: http://localhost:8000"
echo "Interactive Swagger API Docs: http://localhost:8000/docs"

./venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
