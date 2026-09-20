"""测试公共设施:可编程路由式 MockTransport 与客户端夹具。

RouterTransport 按"匹配器 -> 处理器"路由请求并记录全部调用,
使测试可以离线验证:限流、缓存、单飞与源内归一化逻辑。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from freesignt.core.client import AsyncFreeSight

Matcher = Callable[[httpx.Request], bool]
Handler = Callable[[httpx.Request], httpx.Response]


class RouterTransport(httpx.AsyncBaseTransport):
    """可编程异步传输:注册路由并记录每次请求(测试观测用)。"""

    def __init__(self) -> None:
        """初始化空路由。"""
        self.routes: list[tuple[Matcher, Handler]] = []
        self.calls: list[httpx.Request] = []

    def add(self, matcher: Matcher, handler: Handler) -> None:
        """追加一条路由(先注册优先)。

        Args:
            matcher: 请求匹配谓词。
            handler: 返回 httpx.Response 的处理函数。
        """
        self.routes.append((matcher, handler))

    def add_json(self, url_contains: str, payload: Any, *, status: int = 200,
                 headers: dict[str, str] | None = None) -> None:
        """便捷注册:URL 包含指定子串即返回固定 JSON。"""
        self.add(
            lambda req, s=url_contains: s in str(req.url),
            lambda req, p=payload, st=status, h=headers or {}: httpx.Response(
                st, json=p, headers=h
            ),
        )

    def add_text(self, url_contains: str, text: str, *, status: int = 200,
                 content_type: str = "text/plain") -> None:
        """便捷注册:URL 包含指定子串即返回固定文本。"""
        self.add(
            lambda req, s=url_contains: s in str(req.url),
            lambda req, t=text, st=status, ct=content_type: httpx.Response(
                st, text=t, headers={"content-type": ct}
            ),
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """按注册顺序匹配路由;未命中返回 404。

        Args:
            request: 待处理请求。

        Returns:
            匹配的响应。
        """
        self.calls.append(request)
        for matcher, handler in self.routes:
            if matcher(request):
                return handler(request)
        return httpx.Response(404, json={"error": f"no test route for {request.url}"})


@pytest.fixture
def router() -> RouterTransport:
    """空路由传输。"""
    return RouterTransport()


@pytest.fixture
async def client(router: RouterTransport) -> AsyncFreeSight:
    """默认测试客户端:走 mock 传输,限速放大到不影响测试节奏。"""
    c = AsyncFreeSight(transport=router, rate_multiplier=1000.0)
    yield c
    await c.close()


def url_of(request: httpx.Request) -> str:
    """取请求的完整 URL 字符串。"""
    return str(request.url)
