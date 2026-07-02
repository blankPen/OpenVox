# LiveKit Agents worker image.
# Build context = repo root (so vendor/ is available for the volcengine plugin).
FROM python:3.11-slim

WORKDIR /app

# Build deps: gcc for any wheel-less C extensions, git for the editable volcengine
# plugin install (it reads livekit/plugins/volcengine/ from vendor/).
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential \
      git \
 && rm -rf /var/lib/apt/lists/*

# Copy only what pip needs first to maximise Docker layer caching.
COPY vendor/ ./vendor/
COPY main.py ./

# Install all Python deps.  --no-deps on the volcengine editable install lets
# its pyproject's livekit-agents pin (==1.5.4 in the fork, 1.2.9 in our .venv)
# resolve against what we install on the very next line.
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir \
      "livekit-agents[otel,silero,turn-detector]~=1.5" \
      python-dotenv \
      -e ./vendor/volcengine-src/livekit-plugins/livekit-plugins-volcengine \
      --no-deps

# After deps are resolved, copy any leftover source-code overrides if any.
ENV PYTHONUNBUFFERED=1

# Healthcheck that mirrors what lk/main.py does: ping the LiveKit server.
# Disabled by default — the worker connects OUT to the server, not the other way.
# HEALTHCHECK --interval=30s ...

CMD ["python", "main.py", "start"]
