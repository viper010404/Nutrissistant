#!/bin/bash
set -e

# Start Streamlit on internal port 8501
export STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
streamlit run main.py \
  --server.address 127.0.0.1 \
  --server.port 8501 \
  --server.headless true \
  --server.enableCORS false \
  --server.enableXsrfProtection false \
  &

# Start FastAPI on Render's external port
exec uvicorn api:app --host 0.0.0.0 --port "${PORT:-10000}"
