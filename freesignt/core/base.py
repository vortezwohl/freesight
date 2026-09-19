"""数据源抽象基类:声明式元数据 + 异步获取。

与原 Industry-Research 实现保持的概念一致性:
- 每个源是一个 BaseSource 子类,通过类属性声明限速/超时/缓存等元数据;
- 子类定义即自动注册到全局注册表(无需手动注册);
- fetch() 内部通过 self._get() 发请求,自动获得限流/重试/冷却保护。

本版本的增强:
- 全异步(_get 基于 HttpEngine);
- 新增缓存 TTL、限流键覆盖、冷却覆盖、突发额度等声明项;
- 新增 search_kwarg / search_default / limit_kwarg 声明,驱动聚合检索扇出;
- 参数 JSON Schema 从签名与 docstring 自动派生(schema_overrides 兜底),
  供人类查阅与高级调用方二次封装(agent 工具等)自行取用。

限速值为 2026-09-09 实测快照(响应头/行为实测);平台可能随时调整,
生产环境应结合响应头自适应机制(见 ratelimit.py)动态应对。
"""

from __future__ import annotations

from typing import Any, ClassVar

from freesignt.core.http import HttpEngine
from freesignt.core.models import FetchResult, SourceCategory, SourceInfo
from freesignt.core.schema import derive_input_schema


class BaseSource:
    """所有数据源的抽象基类。

    子类约定:
        - 必须覆盖类属性 name / category / rate_limit / rate_period_s / description;
        - 必须实现异步 fetch(),内部通过 self._get() 发请求以获得治理保护;
        - 子类定义时自动注册到全局注册表,无需手动调用注册函数。

    Class Attributes:
        name: 源唯一名称(小写下划线)。
        category: 源分类(SourceCategory),子类必须显式声明。
        rate_limit: 限速窗口内允许的请求数(来自实测/文档)。
        rate_period_s: 限速窗口长度(秒);与 rate_limit 共同决定放行速率。
        timeout: 单请求超时秒数。
        max_retries: 网络层异常最大重试次数。
        burst: 令牌桶突发容量(默认 1 = 纯匀速放行,对免费 API 最友好)。
        cache_ttl_s: 结果缓存 TTL 建议(秒);0 表示默认不缓存。
        limit_key: 限流键覆盖;None 按 URL host(同 host 多源共享预算)。
        cooldown_s: 429 冷却秒数覆盖(无 Retry-After 头时使用,如 Steam 300)。
        description: 源的一句话中文说明。
        search_kwarg: 聚合检索时接收查询词的 fetch 参数名;None 表示
            该源无法参与关键词扇出(浏览型/复合参数型源)。
        search_default: 是否进入 client.search() 的默认扇出集合。
        limit_kwarg: 聚合检索时控制条数的 fetch 参数名;None 表示源无此参数。
        search_defaults: 聚合检索扇出时附加的默认参数(如 method=post_search)。
        schema_overrides: 参数 schema 补充片段(如 enum 取值),详见 schema.py。
    """

    name: str = ""
    category: SourceCategory
    rate_limit: int = 60
    rate_period_s: int = 60
    timeout: int = 20
    max_retries: int = 2
    burst: int = 1
    cache_ttl_s: float = 600.0
    limit_key: str | None = None
    cooldown_s: float | None = None
    description: str = ""
    search_kwarg: str | None = None
    search_default: bool = False
    limit_kwarg: str | None = None
    search_defaults: ClassVar[dict[str, Any]] = {}
    schema_overrides: ClassVar[dict[str, dict[str, Any]]] = {}

    def __init__(self, engine: HttpEngine | None = None) -> None:
        """初始化源实例。

        Args:
            engine: 所属 HTTP 引擎;由客户端注入。独立构造(如查阅元信息)
                时可为 None,但此时不能调用 fetch/_get。
        """
        self.engine = engine

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """子类定义即自动注册;缺失必要类属性时立即报错,防止带病上线。

        Raises:
            TypeError: 声明了 name 但缺少 category。
        """
        super().__init_subclass__(**kwargs)
        # 跳过中间抽象层(自身未声明 name 的占位基类不注册)。
        if not cls.name:
            return
        if getattr(cls, "category", None) is None:
            raise TypeError(f"源 {cls.__name__} 声明了 name 但缺少 category")
        from freesignt.core import registry

        registry.register(cls)

    # ---- 元信息 ------------------------------------------------------------

    @property
    def min_interval_s(self) -> float:
        """按声明限速换算的最小调用间隔(秒,未含全局缩放系数)。"""
        return self.rate_period_s / max(self.rate_limit, 1)

    @property
    def searchable(self) -> bool:
        """是否可参与聚合检索(存在接收查询词的参数)。"""
        return self.search_kwarg is not None

    @classmethod
    def input_schema(cls) -> dict[str, Any]:
        """派生 fetch 参数的 JSON Schema(人读参数表与高级调用方封装共用)。"""
        return derive_input_schema(cls.fetch, overrides=cls.schema_overrides)

    @classmethod
    def info(cls) -> SourceInfo:
        """导出该源的静态元信息(SourceInfo)。"""
        return SourceInfo(
            name=cls.name,
            category=cls.category,
            description=cls.description,
            rate_limit=cls.rate_limit,
            rate_period_s=cls.rate_period_s,
            timeout=cls.timeout,
            max_retries=cls.max_retries,
            cache_ttl_s=cls.cache_ttl_s,
            searchable=cls.search_kwarg is not None,
            search_default=cls.search_default,
            input_schema=cls.input_schema(),
            doc=(cls.fetch.__doc__ or "").strip(),
        )

    # ---- 子类可用基础设施 ---------------------------------------------------

    def _build_headers(self) -> dict[str, str]:
        """构造默认请求头;需要特殊 UA 的源(如 SEC)在子类覆盖。

        Returns:
            含 User-Agent 的请求头 dict。
        """
        ua = self.engine.config.user_agent if self.engine else "FreeSight/0.1"
        return {"User-Agent": ua}

    async def _get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> FetchResult:
        """带限流/重试/冷却/计时的异步 GET 封装。

        Args:
            url: 目标 URL。
            params: 查询参数。
            headers: 追加请求头(与 _build_headers 合并,同名覆盖)。

        Returns:
            统一的 FetchResult;网络失败重试耗尽后 ok=False。

        Raises:
            RuntimeError: 实例未注入引擎(未通过客户端创建)。
        """
        if self.engine is None:
            raise RuntimeError(
                f"源 {self.name} 未注入 HttpEngine;请通过 FreeSight/AsyncFreeSight 客户端调用"
            )
        merged = {**self._build_headers(), **(headers or {})}
        return await self.engine.request(
            source=self.name,
            url=url,
            params=params,
            headers=merged,
            timeout=self.timeout,
            max_retries=self.max_retries,
            rate_limit=self.rate_limit,
            rate_period_s=self.rate_period_s,
            limit_key=self.limit_key,
            burst=self.burst,
            cooldown_s=self.cooldown_s,
        )

    # ---- 子类必须/可选实现 ---------------------------------------------------

    async def fetch(self, **kwargs: Any) -> FetchResult:
        """子类必须实现的业务入口:发起一次数据获取。

        Raises:
            NotImplementedError: 子类未实现时抛出。
        """
        raise NotImplementedError
