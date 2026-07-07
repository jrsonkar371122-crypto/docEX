#!/bin/sh
# ============================================================
# Ollama entrypoint: start the server, then pull required models
# in the background. Runs fully offline once models are cached
# in the ollama_data volume.
# ============================================================
set -e

# Start the Ollama server in the background.
ollama serve &
OLLAMA_PID=$!

# Wait for the server to accept connections.
echo "[ollama-entrypoint] waiting for server..."
until ollama list >/dev/null 2>&1; do
  sleep 2
done
echo "[ollama-entrypoint] server is up."

# Pull each configured model if not already present.
MODELS="${OLLAMA_PULL_MODELS:-}"
for MODEL in $MODELS; do
  if ollama list | awk '{print $1}' | grep -qx "$MODEL"; then
    echo "[ollama-entrypoint] model already present: $MODEL"
  else
    echo "[ollama-entrypoint] pulling model: $MODEL"
    ollama pull "$MODEL" || echo "[ollama-entrypoint] WARNING: failed to pull $MODEL (offline?)"
  fi
done

echo "[ollama-entrypoint] model preparation complete."

# Keep the container attached to the server process.
wait "$OLLAMA_PID"
