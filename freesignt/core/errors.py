"""freesignt 异常体系。

设计原则:数据获取失败不抛异常(以 FetchResult.ok=False 表达,便于
高并发下的部分失败容忍);异常只用于编程错误与配置错误。
"""

from __future__ import annotations


class FreeSightError(Exception):
    """freesignt 所有自定义异常的基基类。"""


class SourceNotFoundError(FreeSightError):
    """按名称取源时未注册该源。

    附带可用源列表提示,便于开发者在第一时间修正拼写。
    """

    def __init__(self, name: str, available: list[str] | None = None) -> None:
        """构造异常。

        Args:
            name: 调用方请求的源名称。
            available: 当前已注册的全部源名称。
        """
        hint = f";可用源: {sorted(available)}" if available else ""
        super().__init__(f"未注册的数据源: {name}{hint}")
        self.name = name
        self.available = available or []


class SyncClientInAsyncContextError(FreeSightError):
    """在运行中的事件循环内调用了同步门面客户端。

    同步门面依赖专属后台事件循环线程,在异步上下文中阻塞等待会损害
    事件循环吞吐;此时应改用 AsyncFreeSight。
    """
