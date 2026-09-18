"""统一搜索与语义排序:一次查询扇出多源,归一化合并为可比命中列表。

扇出策略(为 C 端高并发与"拿全数据"平衡设计):
- 默认只扇出 search_default=True 的轻量检索型源(9 个);
- 单源失败/限流冷却不中断整体,状态记录进 per_source;
- 各源结果先经 to_hits 归一化,再按 limit_per_source 截断,避免单源
  淹没整体;扇出请求本身经过缓存与单飞(复用 client.fetch 管线),
  热门竞品词在缓存窗口内不重复打上游。

排序策略:
- 默认词法排序:BM25 风格的饱和词频 + 跨源 IDF,标题权重 3 倍,
  中文按单字切分,无需任何外部依赖;
- 语义排序:调用方注入 EmbeddingProvider(协议:texts -> 向量列表),
  命中与查询词分别嵌入后按余弦相似度排序;未注入时自动回退词法。
  SDK 不捆绑任何嵌入模型依赖,保持"免费免 key"的定位。
"""

from __future__ import annotations

import asyncio
import math
import re
import time
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from freesignt.core.models import FetchResult, Hit, SearchResponse, SourceFetchStatus

# 英文/数字词元;中文在 _tokenize 中按单字切分。
_WORD = re.compile(r"[a-z0-9]+")


class EmbeddingProvider(Protocol):
    """语义嵌入提供方协议(由调用方注入,SDK 不内置模型)。

    任何 "输入文本列表,返回等长向量列表" 的可调用对象都满足本协议,
    例如 sentence-transformers 的 model.encode 或 OpenAI embedding 封装。
    """

    def __call__(self, texts: list[str]) -> list[Sequence[float]]:
        """把文本列表编码为向量列表(顺序一致)。

        Args:
            texts: 待编码文本。

        Returns:
            与 texts 等长、顺序一致的向量列表。
        """
        ...


def _tokenize(text: str) -> list[str]:
    """轻量分词:英文/数字连续段 + 中文逐字。

    Args:
        text: 原始文本。

    Returns:
        小写词元列表。
    """
    tokens = _WORD.findall(text.lower())
    tokens.extend(ch for ch in text if "\u4e00" <= ch <= "\u9fff")
    return tokens


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """计算两个向量的余弦相似度。

    Args:
        a: 向量 a。
        b: 向量 b。

    Returns:
        相似度 [-1, 1];零向量返回 0。
    """
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def score_hits_lexical(query: str, hits: list[Hit]) -> None:
    """就地计算 BM25 风格词法得分并写入 hit.score。

    IDF 跨当前命中集合计算:只出现在少数源/条目的词更具区分度;
    标题与摘要分别做词频饱和(k=1.2)后加权求和(标题权重 3 倍)。
    若把标题词频直接乘 3 再饱和,会出现"标题 1 次 == 摘要 3 次"的
    失真,故两路必须独立饱和再加权。

    Args:
        query: 查询词。
        hits: 待打分的命中列表。
    """
    query_terms = _tokenize(query)
    if not query_terms or not hits:
        return
    docs: list[tuple[list[str], list[str]]] = [
        (_tokenize(h.title), _tokenize(h.snippet)) for h in hits
    ]
    total = len(docs)
    df: dict[str, int] = {}
    for title_terms, snippet_terms in docs:
        for term in set(title_terms + snippet_terms):
            df[term] = df.get(term, 0) + 1
    for hit, (title_terms, snippet_terms) in zip(hits, docs, strict=False):
        score = 0.0
        for term in query_terms:
            tf_title = title_terms.count(term)
            tf_snippet = snippet_terms.count(term)
            if tf_title == 0 and tf_snippet == 0:
                continue
            idf = math.log(1 + (total - df.get(term, 0) + 0.5) / (df.get(term, 0) + 0.5))
            title_part = 3.0 * (tf_title / (tf_title + 1.2))
            snippet_part = 1.0 * (tf_snippet / (tf_snippet + 1.2))
            score += idf * (title_part + snippet_part)
        hit.score = score


async def score_hits_semantic(
    query: str,
    hits: list[Hit],
    embedder: Callable[[list[str]], Any],
) -> bool:
    """用注入的嵌入提供方对命中做语义重排(就地写 hit.score)。

    Args:
        query: 查询词。
        hits: 待打分的命中列表。
        embedder: EmbeddingProvider 兼容的可调用对象。

    Returns:
        是否成功完成语义打分(False 时调用方应回退词法)。
    """
    if not hits:
        return True
    texts = [query] + [f"{h.title} {h.snippet}".strip() for h in hits]
    try:
        vectors = embedder(texts)
        vectors = list(vectors)
        if len(vectors) != len(texts):
            return False
        query_vec = vectors[0]
        for hit, vec in zip(hits, vectors[1:], strict=False):
            hit.score = _cosine(query_vec, vec)
        return True
    except Exception:  # noqa: BLE001 - 嵌入方异常不应拖垮搜索,回退词法。
        return False


def _source_query_params(source_cls: Any, query: str, limit_per_source: int) -> dict[str, Any]:
    """按源的声明组装扇出参数(查询词 + 条数 + 源默认值)。"""
    params: dict[str, Any] = {source_cls.search_kwarg: query}
    params.update(source_cls.search_defaults)
    if source_cls.limit_kwarg:
        params[source_cls.limit_kwarg] = limit_per_source
    return params


async def unified_search(
    fetcher: Callable[[str, dict[str, Any]], Any],
    source_classes: list[Any],
    query: str,
    *,
    limit_per_source: int = 5,
    semantic: bool = True,
    embedder: Callable[[list[str]], Any] | None = None,
) -> SearchResponse:
    """执行一次多源扇出统一搜索。

    Args:
        fetcher: 异步取数函数(通常为 client.fetch(name, **params)),
            返回 FetchResult;签名 (source_name, params)。
        source_classes: 参与扇出的源类列表(已解析好,含声明元数据)。
        query: 查询词。
        limit_per_source: 每源条数上限(经源的 limit_kwarg 生效)。
        semantic: 注入 embedder 时是否启用语义排序。
        embedder: EmbeddingProvider 兼容对象;None 时词法排序。

    Returns:
        SearchResponse(per_source 含每个源的成败与降级信息)。
    """
    started = time.perf_counter()

    async def _one(cls: Any) -> tuple[Any, dict[str, Any], FetchResult]:
        params = _source_query_params(cls, query, limit_per_source)
        try:
            result = await fetcher(cls.name, params)
        except Exception as exc:  # noqa: BLE001 - 参数校验等异常归为该源失败。
            result = FetchResult(
                ok=False,
                status=None,
                latency_s=0.0,
                error=f"{type(exc).__name__}: {exc}",
                source=cls.name,
            )
        return cls, params, result

    outcomes = await asyncio.gather(*(_one(cls) for cls in source_classes))

    hits: list[Hit] = []
    per_source: dict[str, SourceFetchStatus] = {}
    for cls, params, result in outcomes:
        if not result.ok:
            per_source[cls.name] = SourceFetchStatus(
                ok=False, error=result.error, latency_s=result.latency_s
            )
            continue
        source_hits = cls(engine=None).to_hits(result.data, params)[:limit_per_source]
        for hit in source_hits:
            hit.source = cls.name
        hits.extend(source_hits)
        per_source[cls.name] = SourceFetchStatus(
            ok=True,
            count=len(source_hits),
            cached=result.cached,
            latency_s=result.latency_s,
        )

    used_semantic = False
    if embedder is not None and semantic:
        used_semantic = await score_hits_semantic(query, hits, embedder)
        if not used_semantic:
            score_hits_lexical(query, hits)
    else:
        score_hits_lexical(query, hits)
    # 稳定排序:得分同分时保持注册序(多源信息密度优先的确定性输出)。
    hits.sort(key=lambda h: h.score, reverse=True)

    return SearchResponse(
        query=query,
        hits=hits,
        semantic=used_semantic,
        took_s=time.perf_counter() - started,
        per_source=per_source,
    )
