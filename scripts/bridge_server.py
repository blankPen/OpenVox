"""Bridge server: OpenAI-compatible proxy from :8765 to Hermes api_server :8642.

Adapted from ~/.hermes/skills/livekit-agent-bridge/references/bridge-server-template.md.

LiveKit side: openai.LLM(base_url=http://127.0.0.1:8765/v1, api_key=BRIDGE_API_KEY,
extra_headers={"X-LiveKit-Room": ..., "X-LiveKit-User": ...})
Hermes side: Hermes gateway's OpenAI-compatible api_server (default :8642).

Endpoints:
  GET  /health                 -> {"ok": true, ...}
  POST /v1/chat/completions    -> passthrough to $HERMES_API_BASE/chat/completions
  POST /v1/models              -> passthrough to $HERMES_API_BASE/models
  GET  /v1/models              -> passthrough to $HERMES_API_BASE/models
"""

import json
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

# scripts/ 目录不在 pythonpath；手动加项目根以便 import config
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from config import get_config  # noqa: E402

logger = logging.getLogger("bridge.server")

# ──────────────────────────────────────────────────────────────────
# Config (从 ~/.openvox/config.json 读，schema 见 config.py 模块注释)
# ──────────────────────────────────────────────────────────────────
_cfg = get_config()

BRIDGE_HOST: str = _cfg.require("bridge_server.host")
BRIDGE_PORT: int = int(_cfg.require("bridge_server.port"))
HERMES_API_BASE: str = _cfg.require("hermes.api_base")
HERMES_API_KEY: str = _cfg.get("hermes.api_key", "")
BRIDGE_API_KEY: str = _cfg.require("bridge.api_key")
DEFAULT_MODEL: str = _cfg.require("bridge.model")
HTTP_TIMEOUT: float = float(_cfg.require("bridge_server.http_timeout"))
BRIDGE_LOG_LEVEL: str = _cfg.require("bridge_server.log_level")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.client = httpx.AsyncClient(
        timeout=httpx.Timeout(HTTP_TIMEOUT, connect=5.0),
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
    )
    logger.info("bridge started: listen=%s:%d upstream=%s", BRIDGE_HOST, BRIDGE_PORT, HERMES_API_BASE)
    try:
        yield
    finally:
        await app.state.client.aclose()
        logger.info("bridge stopped")


app = FastAPI(lifespan=lifespan, title="livekit-hermes-bridge")


def _check_auth(request: Request) -> JSONResponse | None:
    """Optional auth: require X-API-Key or Authorization Bearer to match BRIDGE_API_KEY.

    Set bridge.api_key="" to disable auth (local dev only).
    """
    if not BRIDGE_API_KEY:
        return None
    auth = request.headers.get("authorization", "")
    api_key_header = request.headers.get("x-api-key", "")
    expected = f"Bearer {BRIDGE_API_KEY}"
    if auth == expected or api_key_header == BRIDGE_API_KEY:
        return None
    return JSONResponse({"error": "unauthorized"}, status_code=401)


def _upstream_headers(request: Request) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": request.headers.get("accept", "application/json"),
    }
    if HERMES_API_KEY:
        headers["Authorization"] = f"Bearer {HERMES_API_KEY}"
    # forward LiveKit room/user routing headers as-is
    for h in ("x-livekit-room", "x-livekit-user", "x-livekit-agent"):
        v = request.headers.get(h)
        if v:
            headers[h] = v
    return headers


def _is_stream_request(body: bytes) -> bool:
    try:
        parsed = json.loads(body)
    except Exception:
        return False
    return bool(parsed.get("stream"))


@app.get("/health")
async def health():
    return {
        "ok": True,
        "upstream": HERMES_API_BASE,
        "model": DEFAULT_MODEL,
        "bridge_listen": f"{BRIDGE_HOST}:{BRIDGE_PORT}",
    }


@app.get("/v1/models")
async def list_models(request: Request):
    err = _check_auth(request)
    if err is not None:
        return err
    client: httpx.AsyncClient = request.app.state.client
    try:
        resp = await client.get(f"{HERMES_API_BASE}/models", headers=_upstream_headers(request))
    except httpx.HTTPError as exc:
        logger.warning("upstream /models failed: %s", exc)
        return JSONResponse({"error": f"upstream unreachable: {exc}"}, status_code=502)
    return Response(content=resp.content, status_code=resp.status_code, media_type=resp.headers.get("content-type", "application/json"))


@app.post("/v1/models")
async def list_models_post(request: Request):
    return await list_models(request)


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    err = _check_auth(request)
    if err is not None:
        return err

    body = await request.body()
    if not body:
        return JSONResponse({"error": "empty body"}, status_code=400)

    upstream_headers = _upstream_headers(request)
    # stream must use text/event-stream on the wire
    if _is_stream_request(body):
        upstream_headers["Accept"] = "text/event-stream"

    client: httpx.AsyncClient = request.app.state.client
    url = f"{HERMES_API_BASE}/chat/completions"

    if _is_stream_request(body):
        async def event_stream():
            try:
                async with client.stream("POST", url, content=body, headers=upstream_headers) as resp:
                    if resp.status_code >= 400:
                        err_body = await resp.aread()
                        logger.warning("upstream stream error %d: %s", resp.status_code, err_body[:200])
                        yield err_body
                        return
                    async for chunk in resp.aiter_bytes():
                        yield chunk
            except httpx.HTTPError as exc:
                logger.exception("upstream stream failed")
                yield json.dumps({"error": str(exc)}).encode("utf-8")

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    try:
        resp = await client.post(url, content=body, headers=upstream_headers)
    except httpx.HTTPError as exc:
        logger.warning("upstream POST failed: %s", exc)
        return JSONResponse({"error": f"upstream unreachable: {exc}"}, status_code=502)
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type", "application/json"),
    )


if __name__ == "__main__":
    uvicorn.run(
        "bridge_server:app",
        host=BRIDGE_HOST,
        port=BRIDGE_PORT,
        log_level=BRIDGE_LOG_LEVEL,
        factory=False,
    )