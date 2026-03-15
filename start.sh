#!/bin/bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-}"
if [ -z "$PYTHON_BIN" ]; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
  elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python)"
  else
    echo "No python interpreter found in PATH." >&2
    exit 1
  fi
fi

PYTHON_CMD=("$PYTHON_BIN")
if ! "${PYTHON_CMD[@]}" -c "import uvicorn, streamlit" >/dev/null 2>&1; then
  if command -v conda >/dev/null 2>&1 && conda run -n nutrissistant python -c "import uvicorn, streamlit" >/dev/null 2>&1; then
    PYTHON_CMD=(conda run -n nutrissistant python)
  else
    echo "Interpreter '$PYTHON_BIN' is missing required modules (uvicorn, streamlit)." >&2
    echo "Install dependencies or set PYTHON_BIN to an environment that has them." >&2
    exit 1
  fi
fi

# Start Streamlit on internal port 8501
export STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
STREAMLIT_STARTUP_TIMEOUT_SECONDS="${STREAMLIT_STARTUP_TIMEOUT_SECONDS:-90}"
STREAMLIT_STARTUP_CHECK_INTERVAL_SECONDS="${STREAMLIT_STARTUP_CHECK_INTERVAL_SECONDS:-1}"
STREAMLIT_HEALTH_URL="${STREAMLIT_HEALTH_URL:-http://127.0.0.1:8501/_stcore/health}"

"${PYTHON_CMD[@]}" -m streamlit run main.py \
  --server.address 127.0.0.1 \
  --server.port 8501 \
  --server.headless true \
  --server.enableCORS false \
  --server.enableXsrfProtection false \
  &

STREAMLIT_PID=$!

# Wait for Streamlit so first requests after cold starts do not fail through the proxy.
STARTED_AT=$(date +%s)
while true; do
  if ! kill -0 "$STREAMLIT_PID" >/dev/null 2>&1; then
    echo "Streamlit process exited before becoming ready." >&2
    exit 1
  fi

  if curl -fsS --max-time 2 "$STREAMLIT_HEALTH_URL" >/dev/null 2>&1; then
    break
  fi

  NOW=$(date +%s)
  ELAPSED=$((NOW - STARTED_AT))
  if [ "$ELAPSED" -ge "$STREAMLIT_STARTUP_TIMEOUT_SECONDS" ]; then
    echo "Streamlit did not become ready within ${STREAMLIT_STARTUP_TIMEOUT_SECONDS}s." >&2
    exit 1
  fi

  sleep "$STREAMLIT_STARTUP_CHECK_INTERVAL_SECONDS"
done

# Start FastAPI on Render's external port
exec "${PYTHON_CMD[@]}" -m uvicorn api:app --host 0.0.0.0 --port "${PORT:-10000}"
