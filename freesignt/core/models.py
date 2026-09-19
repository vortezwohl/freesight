"""统一数据模型:获取结果、聚合结果与源元信息。

本模块是 freesignt 的公共数据契约层,不依赖任何 IO 设施:
- FetchResult: 单次数据获取的统一返回结构(兼容原 Industry-Research 形态,
  在此基础上增加 cached / fetched_at / meta 观测字段);
- AggregateResult: 聚合检索的整体返回,只做"把各源结果装进一个容器",
  不做任何筛选、排序或相关性判断——那是调用方的职责;
- SourceInfo: 源的静态元信息(供人类查阅与高级调用方二次封装)。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class SourceCategory(StrEnum):
    """数据源获取方式分类,沿用原信息源地图的层级约定。

    FREE_NOKEY 为当前已实现分类;FREE_KEY / CRAWLER 为预留扩展位,
    子类落地后按类别自动归组。
    """

    FREE_NOKEY = "free_nokey"
    FREE_KEY = "free_key"
    CRAWLER = "crawler"


@dataclass
class FetchResult:
    """单次数据获取的统一返回结构。

    所有数据源的 fetch() 与缓存层都产出本结构;失败不抛异常(参数校验类
    ValueError 除外),由调用方按 ok 字段分流,便于高并发下的部分失败容忍。

    Attributes:
        ok: 请求是否成功(HTTP 200 且解析无致命错误)。
        status: HTTP 状态码;网络层异常时为 None。
        latency_s: 单次网络请求耗时(秒);命中缓存时为 0。
        data: 解析后的业务数据(通常为 dict/list);失败时为 None。
        error: 失败时的错误描述(异常名+信息或 HTTP 错误),成功时为 None。
        source: 产生本结果的源名称,便于上层追溯。
        meta: 观测元信息(重试次数、限速冷却、响应头摘要等)。
        cached: 本结果是否来自内存缓存(或单飞共享)。
        fetched_at: 底层网络请求完成的墙钟时间戳(time.time(),秒)。
    """

    ok: bool
    status: int | None
    latency_s: float
    data: Any = None
    error: str | None = None
    source: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    cached: bool = False
    fetched_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        """导出为可直接 JSON 序列化的 dict。

        Returns:
            与字段一一对应的 dict;data 原样透传,由调用方决定裁剪策略。
        """
        return {
            "ok": self.ok,
            "status": self.status,
            "latency_s": self.latency_s,
            "data": self.data,
            "error": self.error,
            "source": self.source,
            "meta": self.meta,
            "cached": self.cached,
            "fetched_at": self.fetched_at,
        }


@dataclass
class AggregateResult:
    """聚合检索的整体返回:各源完整结果的容器。

    设计边界:SDK 只负责把多个源的结果原样聚合到一起(含失败源的降级
    信息),不做任何筛选、排序、去重或相关性判断——这些完全交给调用方。
    results 的键序为参与源列表顺序(确定性的,不隐含重要性排序)。

    Attributes:
        query: 原始查询词。
        results: {源名: FetchResult},data 为该源返回的全部业务数据。
        took_s: 整体耗时(秒,含网络与缓存命中)。
    """

    query: str
    results: dict[str, FetchResult] = field(default_factory=dict)
    took_s: float = 0.0

    @property
    def ok_sources(self) -> list[str]:
        """本次获取成功的源名称列表(列表序)。"""
        return [name for name, r in self.results.items() if r.ok]

    @property
    def failed_sources(self) -> list[str]:
        """本次获取失败的源名称列表(错误详情见对应 FetchResult.error)。"""
        return [name for name, r in self.results.items() if not r.ok]

    def to_dict(self) -> dict[str, Any]:
        """导出为可直接 JSON 序列化的 dict。

        Returns:
            {query, took_s, results: {源名: FetchResult.to_dict()}}。
        """
        return {
            "query": self.query,
            "took_s": round(self.took_s, 4),
            "results": {k: v.to_dict() for k, v in self.results.items()},
        }


@dataclass
class SourceInfo:
    """源的静态元信息(注册表导出给人看,也供高级调用方二次封装取用)。

    Attributes:
        name: 源唯一名称。
        category: 获取方式分类。
        description: 一句话中文说明。
        rate_limit: 限速窗口内请求数(实测快照)。
        rate_period_s: 限速窗口长度(秒)。
        timeout: 单请求超时秒数。
        max_retries: 网络层异常最大重试次数。
        cache_ttl_s: 建议缓存 TTL(秒);0 表示不缓存。
        searchable: 是否可参与聚合检索(有可用的关键词参数)。
        search_default: 是否进入默认扇出集合。
        input_schema: fetch 参数的 JSON Schema。
        doc: fetch 的完整中文 docstring。
    """

    name: str
    category: SourceCategory
    description: str = ""
    rate_limit: int = 60
    rate_period_s: int = 60
    timeout: int = 20
    max_retries: int = 2
    cache_ttl_s: float = 600.0
    searchable: bool = False
    search_default: bool = False
    input_schema: dict[str, Any] = field(default_factory=dict)
    doc: str = ""

    def to_dict(self) -> dict[str, Any]:
        """导出 dict。"""
        return {
            "name": self.name,
            "category": self.category.value,
            "description": self.description,
            "rate_limit": self.rate_limit,
            "rate_period_s": self.rate_period_s,
            "timeout": self.timeout,
            "max_retries": self.max_retries,
            "cache_ttl_s": self.cache_ttl_s,
            "searchable": self.searchable,
            "search_default": self.search_default,
            "input_schema": self.input_schema,
        }
