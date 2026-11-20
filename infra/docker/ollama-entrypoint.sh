#!/bin/sh
# Ollama in the live stack (infra/compose.release.yaml): serve, and make sure the embedding
# model is there. The stock image runs `ollama serve` and nothing else, so a fresh volume
# would answer every embed request with "model not found" until someone pulled it by hand.
#
# `ollama pull` is a no-op once the model is in the volume, so this is safe on every restart
# and the model is downloaded exactly once, on the first start of a new volume.
set -eu
MODEL="${JAP_MODEL_EMBED:-nomic-embed-text}"

ollama serve &
server=$!

# `ollama list` talks to the server, so it answering is the signal that serve is up.
tries=0
until ollama list > /dev/null 2>&1; do
  tries=$((tries + 1))
  [ "$tries" -gt 60 ] && { echo "ollama serve did not come up in 60s" >&2; exit 1; }
  sleep 1
done

ollama pull "$MODEL"
echo "ollama: $MODEL ready"

# Hand the container back to the server process, so its exit is the container's exit.
wait "$server"
