"""内存缓存与单飞(single-flight)合并层。

SDK 不做持久化存储(职责外包给调用方):本模块只提供
- TTLCache: 进程内 TTL + LRU 缓存,作为默认缓存实现;
- NullCache: 显式关闭缓存;
- CacheProtocol: 缓存接入协议,调用方可实现自有缓存(如 Redis 外挂),
  只需满足 get/set/clear 三个同步方法即可注入客户端;
- SingleFlight: 相同 key 的并发请求合并为一次网络调用(C 端高并发下
  "千人同查一个竞品"只打一次上游),等待者共享同一结果;
- cache_key: (源名, 参数) -> 稳定缓存键。

注意:缓存返回的对象被视为只读;若调用方需要修改 data,请自行深拷贝。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from freesignt.core.models import FetchResult


def cache_key(source: str, params: dict[str, Any]) -> str:
    """生成 (源名, 参数集) 的稳定缓存键。

    参数先按键名排序再序列化,保证同一参数集任意构造顺序得到同一键。

    Args:
        source: 源名称。
        params: fetch 参数。

    Returns:
        16 位十六进制摘要键。
    """
    canonical = json.dumps(
        [source, sorted(params.items())],
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:16]


@runtime_checkable
class CacheProtocol(Protocol):
    """缓存接入协议:调用方外挂自有存储时实现本协议即可。

    方法为同步签名(纯内存实现足够;外部存储的 IO 成本由实现方自行权衡,
    SDK 会在事件循环内直接调用,重 IO 实现应尽量快或改为内存前置)。
    """

    def get(self, key: str) -> FetchResult | None:
        """按键取缓存值;未命中返回 None。"""
        ...

    def set(self, key: str, value: FetchResult, ttl_s: float) -> None:
        """写入缓存值与该键的 TTL(秒)。"""
        ...

    def clear(self) -> None:
        """清空全部缓存。"""
        ...


class NullCache:
    """空缓存:显式关闭缓存语义(每次 fetch 都走网络)。"""

    def get(self, key: str) -> FetchResult | None:
        """永远未命中。

        Args:
            key: 缓存键(忽略)。

        Returns:
            None。
        """
        return None

    def set(self, key: str, value: FetchResult, ttl_s: float) -> None:
        """不存储任何内容。

        Args:
            key: 缓存键(忽略)。
            value: 结果(忽略)。
            ttl_s: TTL(忽略)。
        """

    def clear(self) -> None:
        """无操作。"""


class TTLCache:
    """线程安全的 TTL + LRU 内存缓存。

    同时按容量(LRU 淘汰)与过期时间(TTL 惰性淘汰)管理条目;时间源可
    注入以便测试。过期条目在 get/set 时顺带清理,不启动后台线程。

    Attributes:
        maxsize: 最大条目数。
    """

    def __init__(
        self,
        maxsize: int = 512,
        *,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        """初始化缓存。

        Args:
            maxsize: 最大条目数,超过后淘汰最久未使用条目。
            time_fn: 单调时间源(测试可注入)。
        """
        self.maxsize = maxsize
        self._time_fn = time_fn
        self._lock = threading.Lock()
        self._entries: OrderedDict[str, tuple[float, FetchResult]] = OrderedDict()

    def get(self, key: str) -> FetchResult | None:
        """取未过期的缓存值并刷新 LRU 热度。

        Args:
            key: 缓存键。

        Returns:
            缓存的 FetchResult;未命中或已过期返回 None。
        """
        now = self._time_fn()
        with self._lock:
            item = self._entries.get(key)
            if item is None:
                return None
            expires_at, value = item
            if expires_at <= now:
                # 惰性清理:过期即删除,避免占位。
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return value

    def set(self, key: str, value: FetchResult, ttl_s: float) -> None:
        """写入缓存值。

        Args:
            key: 缓存键。
            value: 结果(只读约定)。
            ttl_s: 存活秒数;<=0 视为不缓存。
        """
        if ttl_s <= 0:
            return
        now = self._time_fn()
        with self._lock:
            self._entries[key] = (now + ttl_s, value)
            self._entries.move_to_end(key)
            while len(self._entries) > self.maxsize:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        """清空全部条目。"""
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        """当前条目数(含未清理的过期条目)。"""
        with self._lock:
            return len(self._entries)


class SingleFlight:
    """相同 key 的并发调用合并器。

    首个调用者成为 leader 实际执行 factory;后续同 key 调用者挂到同一
    Future 上共享结果(不计为网络请求)。leader 被取消时,等待者会自动
    重新竞选 leader,保证调用方拿到结果而不是连带取消。
    """

    def __init__(self) -> None:
        """初始化(无内部锁,须在同一事件循环内使用)。"""
        self._inflight: dict[str, asyncio.Future[FetchResult]] = {}

    async def run(self, key: str, factory: Callable[[], Any]) -> FetchResult:
        """按 key 合并执行 factory。

        Args:
            key: 合并键(通常为缓存键)。
            factory: 实际执行网络获取的协程工厂。

        Returns:
            leader 的 FetchResult(所有共享者拿到同一对象)。

        Raises:
            Exception: factory 抛出的异常(所有共享者收到同一异常)。
        """
        while True:
            fut = self._inflight.get(key)
            if fut is not None:
                # 已有同 key 在途:共享其结果。shield 防 leader 取消连带 waiter:
                # waiter 自身被取消时 shield 只取消外层 future,不会动 fut 本身。
                try:
                    return await asyncio.shield(fut)
                except asyncio.CancelledError:
                    # 仅当 fut 本身被取消(leader 失踪)才重新竞选;
                    # 若是调用方取消了等待者自身,必须尊重取消向外传播。
                    if fut.cancelled() and asyncio.current_task().cancelling() == 0:
                        continue
                    raise
            fut = asyncio.get_running_loop().create_future()
            self._inflight[key] = fut
            try:
                result = await factory()
            except asyncio.CancelledError:
                fut.cancel()
                raise
            except BaseException as exc:
                if not fut.done():
                    fut.set_exception(exc)
                    # 无等待者认领时由回调消化异常,避免
                    # "Future exception was never retrieved" 告警;有等待者
                    # 时 await 仍会收到同一异常,行为不变。
                    fut.add_done_callback(lambda f: f.exception())
                raise
            else:
                if not fut.done():
                    fut.set_result(result)
                return result
            finally:
                self._inflight.pop(key, None)
