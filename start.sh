#!/bin/bash
set -e

# Start Streamlit on internal port 8501
streamlit run main.py \
  --server.port 8501 \
  --server.headless true \
  --server.enableCORS false \
  --server.enableXsrfProtection false \
  &

# Wait until Streamlit is accepting connections (up to 30s)
for i in $(seq 1 15); do
  if curl -sf http://localhost:8501/_stcore/health > /dev/null 2>&1; then
    echo "Streamlit is ready."
    break
  fi
  echo "Waiting for Streamlit... ($i/15)"
  sleep 2
done

# Start FastAPI on Render's external port
exec uvicorn api:app --host 0.0.0.0 --port "${PORT:-10000}"
