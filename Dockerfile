# LiveKit Agents worker image.
# Build context = repo root (so plugins/livekit-plugins-volcengine/ is available
# for the editable install of the vendored Volcengine plugin).
FROM python:3.11-slim

WORKDIR /app

# Build deps: gcc for any wheel-less C extensions. (No git needed anymore —
# the Volcengine plugin source is now vendored under plugins/, not cloned.)
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential \
 && rm -rf /var/lib/apt/lists/*

# Copy only what pip needs first to maximise Docker layer caching.
COPY plugins/ ./plugins/
COPY main.py ./

# Install all Python deps.  --no-deps on the volcengine editable install lets
# its pyproject's livekit-agents pin (==1.5.4 in the fork, 1.2.9 in our .venv)
# resolve against what we install on the very next line.
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir \
      "livekit-agents[otel,silero,turn-detector]~=1.5" \
      python-dotenv \
      -e ./plugins/livekit-plugins-volcengine \
      --no-deps

ENV PYTHONUNBUFFERED=1

# Healthcheck that mirrors what lk/main.py does: ping the LiveKit server.
# Disabled by default — the worker connects OUT to the server, not the other way.
# HEALTHCHECK --interval=30s ...

CMD ["python", "main.py", "start"]
