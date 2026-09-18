"""客户端门面:AsyncFreeSight(异步核心)与 FreeSight(同步桥)。

人体工学设计:
- 同一套方法面(fetch/search/describe/list_sources/call_tool/...),
  异步版本可直接进 agent/服务端事件循环,同步版本面向脚本与人;
- 属性糖:client.itunes_search(term="notion") 等价于
  client.fetch("itunes_search", term="notion"),源名即方法名;
- fetch 的缓存管线:内存缓存 -> 单飞合并 -> 限流引擎,三层递进;
  refresh=True 可强制绕过缓存,ttl=秒数可临时覆盖缓存时间。

并发与安全模型:
- AsyncFreeSight 绑定单个事件循环(创建它的那个);跨线程请用 FreeSight;
- FreeSight 内部持有一个专属后台事件循环线程,所有同步方法经
  run_coroutine_threadsafe 桥接,线程安全;禁止在运行中的事件循环内
  调用(会阻塞该循环),此时应直接用 AsyncFreeSight;
- SDK 不做持久化存储;cache 参数可注入任何满足 CacheProtocol 的
  外部缓存实现(存储能力外包给调用方)。
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import replace
from typing import Any

from freesignt.core import registry
from freesignt.core.cache import CacheProtocol, NullCache, SingleFlight, TTLCache, cache_key
from freesignt.core.errors import SyncClientInAsyncContextError
from freesignt.core.http import DEFAULT_SEC_UA, DEFAULT_UA, HttpConfig, HttpEngine
from freesignt.core.models import FetchResult, SearchResponse, SourceCategory, SourceInfo
from freesignt.core.search import EmbeddingProvider, unified_search


class AsyncFreeSight:
    """异步客户端:所有能力的第一实现(同步门面仅做桥接)。

    用法:
        async with AsyncFreeSight() as client:
            result = await client.itunes_search(term="notion")
            resp = await client.search("notion")

    Attributes:
        engine: 底层 HTTP 引擎(限流/重试治理)。
    """

    def __init__(
        self,
        *,
        cache: CacheProtocol | str | None = "memory",
        cache_maxsize: int = 512,
        default_ttl_s: float | None = None,
        rate_multiplier: float = 1.0,
        max_concurrency_per_host: int = 5,
        timeout_s: float = 20.0,
        user_agent: str = DEFAULT_UA,
        sec_user_agent: str = DEFAULT_SEC_UA,
        http2: bool = True,
        trust_env: bool = True,
        verify: bool = True,
        retry_5xx: bool = True,
        transport: Any | None = None,
        embedder: EmbeddingProvider | None = None,
    ) -> None:
        """初始化异步客户端。

        Args:
            cache: 缓存策略;"memory"(默认,进程内 TTL+LRU)、None(关闭)
                或任何 CacheProtocol 实现(调用方外挂存储)。
            cache_maxsize: 内存缓存最大条目数。
            default_ttl_s: 全局 TTL 覆盖(秒);None 时用各源声明值,
                0 表示默认不缓存。
            rate_multiplier: 全局限速缩放;C 端大流量部署建议 0.2-0.5。
            max_concurrency_per_host: 每 host 最大在途请求数。
            timeout_s: 默认请求超时(源声明优先)。
            user_agent: 默认 UA。
            sec_user_agent: SEC 源专用申明式 UA(建议填"公司名 邮箱")。
            http2: 启用 HTTP/2 多路复用。
            trust_env: 遵循代理环境变量。
            verify: 校验 TLS 证书。
            retry_5xx: 5xx 参与退避重试。
            transport: 自定义 httpx 传输(测试注入;生产为 None)。
            embedder: 默认语义嵌入提供方(EmbeddingProvider 协议),
                供 search(semantic=True) 使用;None 时词法排序。
        """
        if cache == "memory":
            self._cache: CacheProtocol = TTLCache(maxsize=cache_maxsize)
        elif cache is None:
            self._cache = NullCache()
        else:
            self._cache = cache
        self._default_ttl_s = default_ttl_s
        self._singleflight = SingleFlight()
        self._embedder = embedder
        self._instances: dict[str, Any] = {}
        self.engine = HttpEngine(
            HttpConfig(
                timeout_s=timeout_s,
                user_agent=user_agent,
                sec_user_agent=sec_user_agent,
                http2=http2,
                trust_env=trust_env,
                verify=verify,
                rate_multiplier=rate_multiplier,
                max_concurrency_per_host=max_concurrency_per_host,
                retry_5xx=retry_5xx,
            ),
            transport=transport,
        )

    # ---- 生命周期 -----------------------------------------------------------

    async def close(self) -> None:
        """关闭底层连接池并清空缓存;幂等。"""
        await self.engine.aclose()
        self._cache.clear()

    async def __aenter__(self) -> AsyncFreeSight:
        """进入异步上下文。"""
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        """退出异步上下文并关闭。"""
        await self.close()

    # ---- 核心取数 -----------------------------------------------------------

    def _source_instance(self, name: str) -> Any:
        """取源类并实例化(带引擎,实例按客户端缓存复用)。"""
        if name not in self._instances:
            self._instances[name] = registry.get(name)(engine=self.engine)
        return self._instances[name]

    async def fetch(
        self,
        name: str,
        /,
        *,
        refresh: bool = False,
        ttl: float | None = None,
        **params: Any,
    ) -> FetchResult:
        """执行一次带缓存与单飞保护的数据获取。

        管线顺序:缓存命中 -> 单飞合并(并发同参共享一次网络请求)
        -> 限流引擎(令牌桶/冷却/重试)。仅成功结果进缓存;失败结果
        原样返回,由调用方决策重试策略。

        Args:
            name: 源名称(见 list_sources())。
            refresh: True 时绕过缓存强制打网络(仍会写缓存)。
            ttl: 本次调用覆盖缓存 TTL(秒);None 用 default_ttl_s 或源声明值。
            **params: 透传给源 fetch 的业务参数(refresh/ttl 为管线保留名)。

        Returns:
            统一 FetchResult。

        Raises:
            SourceNotFoundError: 源名不存在。
            ValueError: 未通过源参数校验。
        """
        source = self._source_instance(name)
        effective_ttl = (
            ttl if ttl is not None
            else (self._default_ttl_s if self._default_ttl_s is not None else source.cache_ttl_s)
        )
        key = cache_key(name, params)
        if not refresh and effective_ttl > 0:
            hit = self._cache.get(key)
            if hit is not None:
                return replace(hit, cached=True)

        async def _factory() -> FetchResult:
            result = await source.fetch(**params)
            if result.ok and effective_ttl > 0:
                self._cache.set(key, result, effective_ttl)
            return result

        return await self._singleflight.run(key, _factory)

    async def search(
        self,
        query: str,
        *,
        sources: list[str] | None = None,
        limit_per_source: int = 5,
        semantic: bool = True,
        embedder: EmbeddingProvider | None = None,
        refresh: bool = False,
    ) -> SearchResponse:
        """多源扇出统一搜索:一次查询,聚合多源归一化结果。

        Args:
            query: 查询词(产品名/公司名/关键词/域名皆可)。
            sources: 参与源名称列表;None 用默认扇出集合
                (search_default=True 的检索型源)。域名情报类源
                (crt_sh/rdap_domain/common_crawl)需显式指定。
            limit_per_source: 每源条数上限。
            semantic: 注入 embedder 时优先语义排序(默认尽力)。
            embedder: 本次搜索的嵌入提供方;None 用客户端默认。
            refresh: True 时绕过缓存。

        Returns:
            SearchResponse(单源失败记入 per_source,不影响整体)。

        Raises:
            ValueError: 指定了不支持关键词扇出的源(无 search_kwarg)。
        """
        if sources is None:
            classes = registry.default_search_sources()
        else:
            classes = [registry.get(name) for name in sources]
            unsearchable = [c.name for c in classes if c.search_kwarg is None]
            if unsearchable:
                searchable_names = [
                    c.name for c in registry.all_sources().values() if c.search_kwarg
                ]
                raise ValueError(
                    f"以下源不支持关键词扇出(无查询词参数): {unsearchable};"
                    f"可用扇出源: {searchable_names}"
                )
        effective_embedder = embedder if embedder is not None else self._embedder

        async def _fetcher(source_name: str, params: dict[str, Any]) -> FetchResult:
            return await self.fetch(source_name, refresh=refresh, **params)

        return await unified_search(
            _fetcher,
            classes,
            query,
            limit_per_source=limit_per_source,
            semantic=semantic,
            embedder=effective_embedder,
        )

    # ---- 自省与观测 ----------------------------------------------------------

    def list_sources(self, category: SourceCategory | None = None) -> list[SourceInfo]:
        """列出源元信息目录。

        Args:
            category: 可选分类过滤。

        Returns:
            SourceInfo 列表(注册序)。
        """
        infos = registry.catalogue()
        if category is not None:
            infos = [i for i in infos if i.category == category]
        return infos

    def describe(self, name: str) -> SourceInfo:
        """查看单个源的元信息(含参数 JSON Schema)。

        Args:
            name: 源名称。

        Returns:
            SourceInfo。
        """
        return registry.get(name).info()

    def rate_snapshot(self) -> dict[str, dict[str, float]]:
        """各限流键的速率与剩余冷却快照(C 端观测/告警用)。"""
        return self.engine.governors.snapshot()

    # ---- Agent 工具 -----------------------------------------------------------

    def build_agent_tools(
        self,
        sources: list[str] | None = None,
        *,
        include_search: bool = True,
        include_catalogue: bool = True,
    ) -> list[dict[str, Any]]:
        """生成 agent 工具声明(OpenAI function calling 格式)。

        Args:
            sources: 暴露为独立工具的源列表;None 为全部。
            include_search: 是否包含聚合搜索复合工具 freesight_search。
            include_catalogue: 是否包含源目录工具 freesight_list_sources。

        Returns:
            工具声明列表,可直接并入 LLM tools 参数。
        """
        from freesignt.core import agent

        return agent.build_agent_tools(
            self, sources=sources, include_search=include_search,
            include_catalogue=include_catalogue,
        )

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """执行一个 agent 工具(名称与 build_agent_tools 对应)。

        Args:
            name: 工具名(源名称或 freesight_search/freesight_list_sources)。
            arguments: 工具参数 dict。

        Returns:
            可 JSON 序列化的结果 dict;执行失败返回 {"ok": False, "error": ...}。
        """
        from freesignt.core import agent

        return await agent.call_tool_async(self, name, arguments or {})

    # ---- 属性糖:源名即方法 -----------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        """把源名动态解析为异步方法(client.itunes_search(...))。"""
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            registry.get(name)
        except Exception:
            raise AttributeError(
                f"未知数据源: {name};可用源见 client.list_sources()"
            ) from None

        async def _source_call(**params: Any) -> FetchResult:
            return await self.fetch(name, **params)

        _source_call.__name__ = name
        _source_call.__qualname__ = f"AsyncFreeSight.{name}"
        return _source_call


class _SyncBridge:
    """专属后台事件循环线程:同步门面的异步执行底座。

    线程为 daemon,进程退出自动回收;close() 会先优雅关闭客户端
    再停循环。启动采用双重检查锁,保证多线程并发首次调用时只创建
    一个事件循环(httpx 客户端跨循环复用会挂死,必须杜绝)。
    """

    def __init__(self) -> None:
        """初始化(惰性启动,首次调用 run() 才建线程)。"""
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready: threading.Event | None = None
        self._start_lock = threading.Lock()

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        """确保后台循环线程已启动(线程安全);异步上下文中调用同步门面时直接报错。

        Returns:
            后台事件循环。

        Raises:
            SyncClientInAsyncContextError: 当前线程存在运行中的事件循环。
        """
        if self._loop is not None:
            return self._loop
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise SyncClientInAsyncContextError(
                "检测到运行中的事件循环:请改用 AsyncFreeSight,避免阻塞该循环"
            )
        # 双重检查锁:并发首次调用只允许一个线程建循环,其余复用。
        with self._start_lock:
            if self._loop is None:
                ready = threading.Event()
                self._ready = ready
                self._thread = threading.Thread(
                    target=self._loop_main, args=(ready,),
                    name="freesignt-sync-bridge", daemon=True,
                )
                self._thread.start()
                ready.wait()
        return self._loop

    def _loop_main(self, ready: threading.Event) -> None:
        """后台线程主函数:跑一个永久事件循环。"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        ready.set()
        try:
            loop.run_forever()
        finally:
            loop.close()

    def run(self, coro: Any) -> Any:
        """把协程提交到后台循环并阻塞等待结果(线程安全)。

        Args:
            coro: 待执行的协程。

        Returns:
            协程结果。
        """
        loop = self._ensure_loop()
        # 600s 兜底上限:覆盖最慢源(crt.sh 90s 超时 x 多次退避重试)仍留裕量,
        # 防御性避免调用线程永久挂起。
        return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=600)

    def close(self, aclose_coro: Any) -> None:
        """先在后台循环中执行收尾协程,再停止循环线程。

        Args:
            aclose_coro: 通常是 AsyncFreeSight.close() 协程。
        """
        with self._start_lock:
            loop, thread = self._loop, self._thread
            self._loop = None
            self._thread = None
        if loop is None:
            # 从未发起过调用:在临时线程里收尾,兼容"处于异步上下文"的场景
            # (此时当前线程不允许/不宜直接 asyncio.run)。
            def _runner() -> None:
                asyncio.run(aclose_coro)

            finisher = threading.Thread(target=_runner, name="freesignt-close", daemon=True)
            finisher.start()
            finisher.join(timeout=30)
            return
        asyncio.run_coroutine_threadsafe(aclose_coro, loop).result(timeout=30)
        loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=10)


class FreeSight:
    """同步门面客户端:方法面与 AsyncFreeSight 一致,面向脚本与人类。

    用法:
        with FreeSight() as client:
            result = client.itunes_search(term="notion")
            for hit in client.search("notion").hits[:10]:
                print(hit.title, hit.url)

    线程安全;不可在运行中的事件循环内使用(改用 AsyncFreeSight)。
    """

    def __init__(self, **kwargs: Any) -> None:
        """初始化同步客户端;参数与 AsyncFreeSight 完全一致。"""
        self._async = AsyncFreeSight(**kwargs)
        self._bridge = _SyncBridge()

    @property
    def async_client(self) -> AsyncFreeSight:
        """底层异步客户端(同进程异步上下文中可直接复用)。"""
        return self._async

    def fetch(
        self,
        name: str,
        /,
        *,
        refresh: bool = False,
        ttl: float | None = None,
        **params: Any,
    ) -> FetchResult:
        """同步 fetch,语义与 AsyncFreeSight.fetch 一致。"""
        return self._bridge.run(
            self._async.fetch(name, refresh=refresh, ttl=ttl, **params)
        )

    def search(
        self,
        query: str,
        *,
        sources: list[str] | None = None,
        limit_per_source: int = 5,
        semantic: bool = True,
        embedder: EmbeddingProvider | None = None,
        refresh: bool = False,
    ) -> SearchResponse:
        """同步 search,语义与 AsyncFreeSight.search 一致。"""
        return self._bridge.run(
            self._async.search(
                query,
                sources=sources,
                limit_per_source=limit_per_source,
                semantic=semantic,
                embedder=embedder,
                refresh=refresh,
            )
        )

    def list_sources(self, category: SourceCategory | None = None) -> list[SourceInfo]:
        """列出源目录(纯内存操作,不经过桥)。"""
        return self._async.list_sources(category)

    def describe(self, name: str) -> SourceInfo:
        """查看源元信息(纯内存操作,不经过桥)。"""
        return self._async.describe(name)

    def rate_snapshot(self) -> dict[str, dict[str, float]]:
        """限流观测快照(纯内存操作,不经过桥)。"""
        return self._async.rate_snapshot()

    def build_agent_tools(
        self,
        sources: list[str] | None = None,
        *,
        include_search: bool = True,
        include_catalogue: bool = True,
    ) -> list[dict[str, Any]]:
        """生成 agent 工具声明(纯内存操作,不经过桥)。"""
        return self._async.build_agent_tools(
            sources, include_search=include_search, include_catalogue=include_catalogue
        )

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """同步执行 agent 工具。"""
        return self._bridge.run(self._async.call_tool(name, arguments or {}))

    def close(self) -> None:
        """关闭底层资源;幂等。"""
        self._bridge.close(self._async.close())

    def __enter__(self) -> FreeSight:
        """进入上下文。"""
        return self

    def __exit__(self, *exc_info: Any) -> None:
        """退出上下文并关闭。"""
        self.close()

    def __getattr__(self, name: str) -> Any:
        """把源名动态解析为同步方法(client.itunes_search(...))。"""
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            registry.get(name)
        except Exception:
            raise AttributeError(
                f"未知数据源: {name};可用源见 client.list_sources()"
            ) from None

        def _source_call(**params: Any) -> FetchResult:
            return self.fetch(name, **params)

        _source_call.__name__ = name
        _source_call.__qualname__ = f"FreeSight.{name}"
        return _source_call
