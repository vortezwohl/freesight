"""freesignt:免费竞品调研多渠道数据访问 SDK。

封装 34 个免费免 key 公开数据源(App Store/HackerNews/GitHub/开发者
生态/招聘板/社媒/法定披露/Steam/基础设施足迹/威胁情报等),只做一件事:
- 每个渠道一层薄的独立访问封装(限流 + 429 冷却 + TTL 缓存 + 单飞合并
  + 源内结构归一化),fetch 一次返回该源独立的 FetchResult。

各渠道参数与结果形态完全不同,SDK 不做任何跨源聚合检索;
结果的合并、筛选、排序、语义分析、agent 工具封装等全部交给调用方。
SDK 不做持久化存储,缓存协议(CacheProtocol)可外接给调用方实现。

两层 API 设计:
- 本层(根包):开箱即用的最小 API——同步 FreeSight 与异步 AsyncFreeSight
  双客户端(同层级、同一套方法面)、结果模型、缓存注入协议与模块级快捷函数;
- 内核层(freesignt.core.*):面向扩展与二次封装,含源注册表、限流/缓存
  原语与 BaseSource(自定义源定义即注册),按需从子模块导入,不在本层导出。

快速上手:
    import freesignt

    result = freesignt.fetch("itunes_search", term="notion")  # 单渠道取数
    print(result.ok, result.data)

    with freesignt.FreeSight() as client:             # 同步
        result = client.itunes_search(term="notion")  # 源名即方法

    from freesignt import AsyncFreeSight              # 异步(服务端/agent 宿主)

    async with AsyncFreeSight() as client:
        result = await client.hn_algolia(query="notion")
"""

from freesignt import sources as _sources  # noqa: F401  (导入即完成全部源注册)
from freesignt.core.cache import CacheProtocol
from freesignt.core.client import AsyncFreeSight, FreeSight
from freesignt.core.models import FetchResult

__version__ = "0.3.0"

__all__ = [
    "AsyncFreeSight",
    "CacheProtocol",
    "FetchResult",
    "FreeSight",
    "fetch",
    "list_sources",
]

# 模块级默认同步客户端:支撑 freesignt.fetch(...) 一行式用法。
_default_client: FreeSight | None = None


def _get_default_client() -> FreeSight:
    """惰性创建进程级默认同步客户端(单例)。"""
    global _default_client
    if _default_client is None:
        _default_client = FreeSight()
    return _default_client


def fetch(name: str, /, **params: object) -> FetchResult:
    """模块级单渠道取数:使用进程级默认客户端。

    Args:
        name: 源名称。
        **params: 源业务参数(refresh/ttl 为管线保留名)。

    Returns:
        FetchResult。
    """
    return _get_default_client().fetch(name, **params)  # type: ignore[arg-type]


def list_sources() -> list[object]:
    """模块级源目录快捷方法(元素为 SourceInfo,含参数 schema 与限速说明)。"""
    return _get_default_client().list_sources()
