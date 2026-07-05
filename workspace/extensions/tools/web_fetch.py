"""web_fetch tool — 抓取网页并转为可读文本。

实现要点（见 docs/superpowers/specs/2026-07-05-search-and-claudecode-bridge-design.md §3.1）：
- httpx 异步 GET，UA 设为常见浏览器，超时 15s
- 用 beautifulsoup4 解析 HTML，去掉 script/style/nav/footer 等噪音标签
- 把页面纯文本按行输出（不是真 Markdown，但已足够小语综合）
- 截断策略：head 80% + tail 20%，附 "[TRUNCATED at N/M chars]"
- 非 2xx、超时、非 text/html、>5MB → [ERROR]
"""
from __future__ import annotations

import logging
import re

import httpx
from bs4 import BeautifulSoup

from livekit.agents import function_tool

logger = logging.getLogger("volcengine-agent")

_MIN_CHARS = 500
_MAX_CHARS = 50_000
_DEFAULT_CHARS = 8_000
_MAX_BYTES = 5 * 1024 * 1024  # 5MB

# 噪音标签：直接删，不保留其文本
_NOISE_TAGS = {"script", "style", "nav", "footer", "header", "aside", "noscript", "iframe"}

# 这些标签转成换行（保留可读性）
_BLOCK_TAGS = {"p", "div", "section", "article", "li", "h1", "h2", "h3", "h4", "h5", "h6",
               "br", "tr", "td", "th", "blockquote", "pre"}

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(_NOISE_TAGS):
        tag.decompose()

    lines: list[str] = []
    for elem in soup.find_all(_BLOCK_TAGS):
        text = elem.get_text(" ", strip=True)
        if text:
            lines.append(text)

    # 兜底：soup 里所有可见文本（防止页面全是 span 没用 block 标签）
    if not lines:
        body = soup.get_text("\n", strip=True)
        return re.sub(r"\n{3,}", "\n\n", body).strip()

    return "\n\n".join(lines).strip()


def _truncate(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    head = int(max_chars * 0.8)
    tail = max_chars - head
    truncated = text[:head] + "\n\n[... TRUNCATED ...]\n\n" + text[-tail:]
    return truncated, True


@function_tool()
async def web_fetch(url: str, max_chars: int = _DEFAULT_CHARS) -> str:
    """抓取网页并转为可读文本。

    适用于：搜索后需要看某个 URL 的正文内容时。

    Args:
        url: 完整 URL（http/https）。
        max_chars: 返回的最大字符数，默认 8000；范围 [500, 50000]。

    Returns:
        页面纯文本；截断时附 "[... TRUNCATED ...]" 标记。
        抓取失败返回 "[ERROR] <reason>"。
    """
    if not (isinstance(max_chars, int) and _MIN_CHARS <= max_chars <= _MAX_CHARS):
        return f"[ERROR] max_chars 必须在 {_MIN_CHARS}-{_MAX_CHARS} 之间，收到 {max_chars!r}"
    if not url.startswith(("http://", "https://")):
        return f"[ERROR] url 必须以 http:// 或 https:// 开头，收到 {url!r}"

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(15.0, connect=10.0),
            headers={"User-Agent": _UA},
        ) as client:
            resp = await client.get(url)

        if resp.status_code != 200:
            return f"[ERROR] HTTP {resp.status_code} {url}"

        ctype = resp.headers.get("content-type", "")
        if "text/html" not in ctype and "application/xhtml" not in ctype:
            return f"[ERROR] 非 HTML 页面 (content-type={ctype!r})"

        if len(resp.content) > _MAX_BYTES:
            return f"[ERROR] 页面过大 ({len(resp.content)} bytes > {_MAX_BYTES})"

        text = _html_to_text(resp.text)
        text, truncated = _truncate(text, max_chars)
        if truncated:
            text += f"\n\n[TRUNCATED at {max_chars}/{len(text) + max_chars} chars]"

        logger.info(
            "[web_fetch] OK url=%r status=%d bytes=%d text=%dc truncated=%s",
            url, resp.status_code, len(resp.content), len(text), truncated,
        )
        return text

    except httpx.TimeoutException:
        logger.warning("[web_fetch] TIMEOUT url=%r", url)
        return f"[ERROR] 抓取超时（15s）: {url}"
    except httpx.HTTPError as e:
        logger.warning("[web_fetch] HTTP_ERROR url=%r err=%r", url, e)
        return f"[ERROR] 网络错误: {e}"
    except Exception as e:
        logger.warning("[web_fetch] ERROR url=%r err=%r", url, e)
        return f"[ERROR] {type(e).__name__}: {e}"


def register() -> list:
    """返回本文件提供的 tool 列表。"""
    return [web_fetch]