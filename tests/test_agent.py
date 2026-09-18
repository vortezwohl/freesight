"""Agent 工具封装测试:声明生成、目录发现与工具执行闭环。"""

from __future__ import annotations

from tests.conftest import install_default_search_routes

SEARCH_TOOL = "freesight_search"
CATALOGUE_TOOL = "freesight_list_sources"


def test_build_agent_tools_default_full_set(client) -> None:
    """默认生成 2 个复合工具 + 25 个单源工具,结构合法。"""
    tools = client.build_agent_tools()
    names = {t["function"]["name"] for t in tools}
    assert len(tools) == 27
    assert SEARCH_TOOL in names and CATALOGUE_TOOL in names
    assert "itunes_search" in names
    for tool in tools:
        fn = tool["function"]
        assert tool["type"] == "function"
        assert fn["description"]
        params = fn["parameters"]
        assert params["type"] == "object"
        assert isinstance(params["properties"], dict)


def test_build_agent_tools_subset(client) -> None:
    """限定源子集与关闭复合工具。"""
    tools = client.build_agent_tools(
        ["hn_algolia", "crt_sh"], include_search=False, include_catalogue=False
    )
    names = [t["function"]["name"] for t in tools]
    assert names == ["hn_algolia", "crt_sh"]
    crt = tools[1]["function"]
    assert "慢源" in crt["description"]
    assert crt["parameters"]["properties"]["domain"]["type"] == "string"


def test_source_tool_schema_from_signature(client) -> None:
    """单源工具 parameters 来自签名派生:必填/默认值/enum 齐全。"""
    tools = {t["function"]["name"]: t["function"] for t in client.build_agent_tools()}
    itunes = tools["itunes_search"]["parameters"]
    assert itunes["required"] == ["term"]
    assert itunes["properties"]["country"]["default"] == "us"
    v2ex = tools["v2ex"]["parameters"]
    assert v2ex["properties"]["kind"]["enum"] == ["hot", "latest"]


async def test_call_tool_search(client, router) -> None:
    """复合搜索工具:参数透传,返回结构化 dict。"""
    install_default_search_routes(router)
    payload = await client.call_tool(SEARCH_TOOL, {"query": "notion", "limit_per_source": 2})
    assert payload["ok"] is True
    assert payload["total"] > 0
    assert payload["hits"][0]["source"]
    assert "raw" not in payload["hits"][0]
    assert "per_source" in payload


async def test_call_tool_search_source_filter(client, router) -> None:
    """复合搜索工具 sources 参数限定单源。"""
    install_default_search_routes(router)
    payload = await client.call_tool(
        SEARCH_TOOL, {"query": "notion", "sources": ["itunes_search"]}
    )
    assert payload["ok"] is True
    assert set(payload["per_source"]) == {"itunes_search"}


async def test_call_tool_search_empty_query(client) -> None:
    """空查询被工具层拒绝。"""
    payload = await client.call_tool(SEARCH_TOOL, {"query": "  "})
    assert payload["ok"] is False
    assert "query" in payload["error"]


async def test_call_tool_catalogue(client) -> None:
    """目录发现工具返回全部源元信息。"""
    payload = await client.call_tool(CATALOGUE_TOOL, {})
    assert payload["ok"] is True
    assert len(payload["sources"]) == 25
    itunes = next(s for s in payload["sources"] if s["name"] == "itunes_search")
    assert itunes["input_schema"]["required"] == ["term"]


async def test_call_tool_single_source(client, router) -> None:
    """单源工具执行:fetch 语义透传。"""
    router.add_json("v2ex.com/api/topics/hot", [{"title": "t"}])
    payload = await client.call_tool("v2ex", {"kind": "hot"})
    assert payload["ok"] is True
    assert payload["source"] == "v2ex"
    assert payload["data"][0]["title"] == "t"


async def test_call_tool_invalid_arguments_folded(client) -> None:
    """源参数校验错误折叠为工具失败,不抛异常。"""
    payload = await client.call_tool("github_public", {})
    assert payload["ok"] is False
    assert "ValueError" in payload["error"]


async def test_call_tool_unknown_name(client) -> None:
    """未知名工具返回错误 dict。"""
    payload = await client.call_tool("nope_tool", {})
    assert payload["ok"] is False
    assert "nope_tool" in payload["error"]


def test_sync_client_agent_tools_roundtrip() -> None:
    """同步客户端同样具备工具生成与执行能力。"""
    from freesignt import FreeSight
    from tests.conftest import RouterTransport

    router = RouterTransport()
    install_default_search_routes(router)
    with FreeSight(transport=router, rate_multiplier=1000.0) as client:
        tools = client.build_agent_tools(
            ["hn_algolia"], include_search=False, include_catalogue=False
        )
        assert tools[0]["function"]["name"] == "hn_algolia"
        payload = client.call_tool("hn_algolia", {"query": "notion"})
        assert payload["ok"] is True
