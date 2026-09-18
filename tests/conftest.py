"""测试公共设施:可编程路由式 MockTransport 与客户端夹具。

RouterTransport 按"匹配器 -> 处理器"路由请求并记录全部调用,
使测试可以离线验证:限流、缓存、单飞、归一化与扇出逻辑。
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


# ---- 默认扇出集合的共享桩数据 ------------------------------------------------
# 供 test_search / test_agent / test_client 复用的 9 个默认检索源路由。

DEFAULT_SEARCH_PAYLOADS: dict[str, Any] = {
    # itunes_search
    "itunes.apple.com/search": {
        "resultCount": 2,
        "results": [
            {
                "trackName": "Notion: Notes, AI, Docs",
                "trackViewUrl": "https://apps.apple.com/app/id123",
                "trackId": 123,
                "bundleId": "notion.id",
                "artistName": "Notion Labs, Inc.",
                "averageUserRating": 4.7,
                "genres": ["Productivity", "Business"],
                "formattedPrice": "Free",
            },
            {
                "trackName": "Notion Calendar",
                "trackViewUrl": "https://apps.apple.com/app/id456",
                "trackId": 456,
                "artistName": "Notion Labs, Inc.",
                "averageUserRating": 4.3,
                "genres": ["Productivity"],
            },
        ],
    },
    # hn_algolia
    "hn.algolia.com": {
        "nbHits": 2,
        "page": 0,
        "hits": [
            {
                "objectID": "a1",
                "title": "Show HN: Notion – all-in-one workspace",
                "url": "https://notion.so",
                "points": 300,
                "num_comments": 120,
                "author": "alice",
                "created_at": "2024-01-01T00:00:00Z",
            },
            {"objectID": "a2", "title": "Ask HN: Notion alternatives?",
             "points": 10, "num_comments": 5},
        ],
    },
    # github_public(搜索路径)
    "api.github.com/search": {
        "total_count": 1,
        "items": [
            {
                "full_name": "makenotion/notion-api",
                "html_url": "https://github.com/makenotion/notion-api",
                "description": "Notion API SDK",
                "stargazers_count": 4500,
                "forks_count": 300,
                "language": "Python",
            }
        ],
    },
    # npm_registry
    "registry.npmjs.org": {
        "objects": [
            {
                "package": {
                    "name": "@notionhq/client",
                    "description": "Official Notion API client",
                    "version": "2.2.0",
                    "publisher": {"username": "notion"},
                },
                "score": {"final": 0.9123},
            }
        ]
    },
    # pypi_metadata(注意 pypi.org 也会命中 pypistats 域名,键须更具体)
    "pypi.org/pypi": {
        "info": {
            "name": "notion-client", "summary": "Notion API client",
            "version": "2.2.1", "author": "Notion",
        },
        "releases": {"1.0.0": [], "2.2.1": []},
    },
    # huggingface_hub
    "huggingface.co/api": [
        {"id": "notion-summary", "pipeline_tag": "text2text-generation",
         "downloads": 1200, "likes": 30}
    ],
    # bluesky(默认 actor_search)
    "public.api.bsky.app": {
        "actors": [
            {"handle": "notion.bsky.social", "displayName": "Notion",
             "description": "The all-in-one workspace",
             "did": "did:plc:x", "followersCount": 5000}
        ]
    },
    # uspto_trademark
    "developer.uspto.gov": {
        "response": {
            "numFound": 1,
            "docs": [
                {"trademarkName": "NOTION", "statusLabel": "Registered", "serialNumber": "88123456"}
            ],
        }
    },
    # steam_store(搜索路径)
    "store.steampowered.com/api/storesearch": {
        "total": 1,
        "items": [{"id": 990080, "name": "Hollow Knight", "tiny_description": "2D action adventure",
                   "price": {"final": 9900, "currency": "USD"}}],
    },
}


def install_default_search_routes(router: RouterTransport) -> None:
    """为默认扇出集合的 9 个源注册桩路由(子串匹配,先具体后宽泛)。"""
    # pypi.org/pypi 必须先于宽泛键注册,避免被其他键抢先。
    ordered = sorted(DEFAULT_SEARCH_PAYLOADS.items(), key=lambda kv: -len(kv[0]))
    for fragment, payload in ordered:
        router.add_json(fragment, payload)
