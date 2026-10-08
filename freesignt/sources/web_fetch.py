"""网页获取/无头渲染替代免费免 key 源(2026-09 实测)。

面向"拿一个 URL 的内容"的通道:
- jina_reader: r.jina.ai 无头渲染为 Markdown(免 key 约 20 RPM/IP),
  JS 重页面/部分 Cloudflare 页的首选免 key 方案;
- allorigins / codetabs / corsproxy: 三个免 key 原始内容代理,
  适合静态页直取与绕过 CORS/UA 锁,不做任何渲染。

边界:登录墙、强验证码、需交互(点击/滚动)的页面没有稳定的
公共服务替代,应自建无头浏览器(Playwright 等)。
"""

from __future__ import annotations

from urllib.parse import quote

from freesignt.core.base import BaseSource
from freesignt.core.models import FetchResult, SourceCategory


def _require_http_url(url: str) -> None:
    """校验目标 URL 形态,防止把代理端点本身当作目标请求。"""
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"url 必须以 http(s):// 开头: {url!r}")


class JinaReaderSource(BaseSource):
    """Jina Reader:r.jina.ai 把目标页(含 JS 渲染)转为 Markdown。

    免 key 匿名约 20 RPM/IP;URL 直接前缀拼接即可,
    返回的 Markdown 文本可直接喂给 LLM 或正则抽取。
    """

    name = "jina_reader"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 10
    rate_period_s = 60  # 免 key 约 20 RPM,取保守值
    timeout = 60  # 无头渲染耗时显著高于普通请求
    cache_ttl_s = 1800.0
    description = "Jina Reader 无头渲染取页(r.jina.ai,免 key 约 20 RPM/IP)"

    async def fetch(self, url: str) -> FetchResult:
        """渲染并读取一个网页。

        Args:
            url: 目标页完整 URL(http/https)。

        Returns:
            data 为 Markdown 文本(字符串);渲染失败/超时 ok=False。

        Raises:
            ValueError: url 形态非法。
        """
        _require_http_url(url)
        return await self._get(f"https://r.jina.ai/{url}")


class AllOriginsSource(BaseSource):
    """AllOrigins 免 key 内容代理:raw 端点原样返回目标响应体。"""

    name = "allorigins"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 15
    rate_period_s = 60
    timeout = 30
    cache_ttl_s = 600.0
    description = "AllOrigins 原始内容代理(免 key,不渲染)"

    async def fetch(self, url: str) -> FetchResult:
        """经 AllOrigins 获取目标 URL 原始内容。

        Args:
            url: 目标页完整 URL。

        Returns:
            data 为原始响应体(JSON 优先解析,否则文本)。

        Raises:
            ValueError: url 形态非法。
        """
        _require_http_url(url)
        return await self._get(
            "https://api.allorigins.win/raw", params={"url": url}
        )


class CodetabsSource(BaseSource):
    """codetabs.com 免 key 代理:v1/proxy 原样转发目标请求。"""

    name = "codetabs"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 20
    rate_period_s = 60  # 文档级限额 5 req/s,取保守值
    timeout = 30
    cache_ttl_s = 600.0
    description = "codetabs 原始内容代理(免 key,不渲染)"

    async def fetch(self, url: str) -> FetchResult:
        """经 codetabs 代理获取目标 URL 原始内容。

        Args:
            url: 目标页完整 URL。

        Returns:
            data 为原始响应体(JSON 优先解析,否则文本)。

        Raises:
            ValueError: url 形态非法。
        """
        _require_http_url(url)
        return await self._get(
            "https://api.codetabs.com/v1/proxy", params={"quest": quote(url, safe="")}
        )


class CorsProxySource(BaseSource):
    """corsproxy.io 免 key 代理:浏览器 CORS 场景常用的轻量转发。"""

    name = "corsproxy"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 10
    rate_period_s = 60
    timeout = 30
    cache_ttl_s = 600.0
    description = "corsproxy.io 原始内容代理(免 key,不渲染)"

    async def fetch(self, url: str) -> FetchResult:
        """经 corsproxy.io 获取目标 URL 原始内容。

        Args:
            url: 目标页完整 URL。

        Returns:
            data 为原始响应体(JSON 优先解析,否则文本)。

        Raises:
            ValueError: url 形态非法。
        """
        _require_http_url(url)
        return await self._get(
            "https://corsproxy.io/", params={"url": quote(url, safe="")}
        )
