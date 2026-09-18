"""Agent 工具封装:把 SDK 能力声明为 LLM 可直接调用的工具。

设计目标:一个或若干个灵活的 tool,而不是 25 个平铺选项压垮 agent:
- freesight_search: 聚合搜索复合工具(默认扇出或按源限定);
- freesight_list_sources: 源目录发现工具,让 agent 自行查阅可用源与参数;
- 每个源一个细粒度工具(名称即源名,parameters 为自动派生的 JSON Schema),
  需要精确单源调用时使用。

工具声明为 OpenAI function calling 格式(function 节点),该格式同样
可映射到 Anthropic tool use / MCP 等主流协议,无需额外依赖。
工具执行入口 call_tool_async 返回纯 dict,便于各框架直接序列化。
"""

from __future__ import annotations

from typing import Any

from freesignt.core import registry
from freesignt.core.models import FetchResult

SEARCH_TOOL = "freesight_search"
CATALOGUE_TOOL = "freesight_list_sources"

_SEARCH_TOOL_DEF: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": SEARCH_TOOL,
        "description": (
            "跨多个免费公开数据源做竞品/产品/公司情报聚合搜索,"
            "返回归一化命中的标题/链接/摘要与来源。适合回答"
            "'这个产品/公司怎么样、有没有竞品、社区口碑如何'类问题。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "查询词:产品名/公司名/关键词;域名需配合 sources 参数",
                },
                "sources": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "可选,限定参与的数据源名称;缺省用默认扇出集合;"
                    "域名情报请显式传 crt_sh/rdap_domain/common_crawl",
                },
                "limit_per_source": {
                    "type": "integer",
                    "default": 5,
                    "description": "每个源返回条数上限(1-10)",
                },
            },
            "required": ["query"],
        },
    },
}

_CATALOGUE_TOOL_DEF: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": CATALOGUE_TOOL,
        "description": (
            "列出全部可用数据源及其能力:限速、参数 JSON Schema、"
            "是否支持关键词搜索。调用其他细粒度工具前可先查阅本目录。"
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}


def _source_tool_def(info: Any) -> dict[str, Any]:
    """把 SourceInfo 组装为单源工具声明。

    Args:
        info: SourceInfo(registry.catalogue() 的元素)。

    Returns:
        OpenAI function calling 格式的工具声明。
    """
    description = info.description or info.name
    doc_summary = (info.doc or "").split("\n\n", 1)[0].replace("\n", " ").strip()
    if doc_summary and doc_summary not in description:
        description = f"{description}。{doc_summary}"
    return {
        "type": "function",
        "function": {
            "name": info.name,
            "description": description[:512],
            "parameters": info.input_schema,
        },
    }


def build_agent_tools(
    client: Any,
    sources: list[str] | None = None,
    *,
    include_search: bool = True,
    include_catalogue: bool = True,
) -> list[dict[str, Any]]:
    """生成 agent 工具声明列表。

    Args:
        client: AsyncFreeSight 或 FreeSight(仅用其 describe/list_sources)。
        sources: 暴露为独立工具的源名称列表;None 为全部源。
        include_search: 包含聚合搜索复合工具。
        include_catalogue: 包含源目录发现工具。

    Returns:
        工具声明列表(OpenAI function calling 格式)。

    Raises:
        SourceNotFoundError: sources 中含未知源名。
    """
    tools: list[dict[str, Any]] = []
    if include_search:
        tools.append(_SEARCH_TOOL_DEF)
    if include_catalogue:
        tools.append(_CATALOGUE_TOOL_DEF)
    names = sources if sources is not None else registry.names()
    for name in names:
        tools.append(_source_tool_def(client.describe(name)))
    return tools


async def call_tool_async(
    client: Any,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """执行一个 agent 工具并返回纯 dict 结果。

    失败(含源参数校验错误)统一折叠为 {"ok": False, "error": ...},
    不向 agent 框架抛异常;参数中的保留字会被剔除以防误用。

    Args:
        client: AsyncFreeSight(异步客户端)。
        name: 工具名(源名 / freesight_search / freesight_list_sources)。
        arguments: 工具参数。

    Returns:
        可 JSON 序列化的结果 dict。
    """
    if name == SEARCH_TOOL:
        query = str(arguments.get("query", "")).strip()
        if not query:
            return {"ok": False, "error": "query 不能为空"}
        sources = arguments.get("sources")
        limit = arguments.get("limit_per_source", 5)
        try:
            response = await client.search(
                query,
                sources=list(sources) if isinstance(sources, list) else None,
                limit_per_source=max(1, min(int(limit), 10)),
                semantic=False,  # agent 路径默认词法排序,避免嵌入副作用。
            )
        except Exception as exc:  # noqa: BLE001 - 工具边界统一折叠错误。
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        payload = response.to_dict(include_raw=False)
        payload["ok"] = True
        return payload

    if name == CATALOGUE_TOOL:
        return {
            "ok": True,
            "sources": [info.to_dict() for info in client.list_sources()],
        }

    # 细粒度单源工具;未知名交给注册表报错并折叠为工具失败。
    try:
        result: FetchResult = await client.fetch(name, **arguments)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    payload = result.to_dict()
    return payload
