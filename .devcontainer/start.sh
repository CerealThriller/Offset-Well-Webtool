#!/usr/bin/env bash
# Start the app in the background on port 8000. The log is in /tmp/owt.log.
cd "$(dirname "$0")/../backend"
export OWT_DATA_DIR="${OWT_DATA_DIR:-$HOME/owt-data}"
nohup python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 > /tmp/owt.log 2>&1 &
echo "Offset Well Webtool starts on port 8000. Log: /tmp/owt.log"
