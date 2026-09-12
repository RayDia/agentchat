#!/bin/bash
cd "$(dirname "$0")/frontend"
npm run dev &
cd ..
source .venv/bin/activate
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
