"""Steam 生态免费免 key 源:商店搜索/详情/精选与 SteamSpy 估算。

实测基准(2026-09-09):
- storesearch 宽松(1.0-1.2s,并发全过);
- appdetails 隐性限速约 200 次/5min(20 并发实测仅 1/40 存活),
  本模块声明 4/min 节流与 300s 429 冷却;
- SteamSpy 并发即封(约 1 req/s),按串行 1/2s 节流。

与原实现的差异:Steam 429 的 5 分钟等待不再阻塞式 sleep,而是声明
cooldown_s=300 交给限流引擎做全键冷却;冷却较长时直接返回携带
冷却信息的失败结果,由调用方决定是否稍后重试。
"""

from __future__ import annotations

from freesignt.core.base import BaseSource
from freesignt.core.models import FetchResult, SourceCategory


class SteamStoreSource(BaseSource):
    """Steam 商店公开接口:目录搜索与单个应用详情。"""

    name = "steam_store"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 4
    rate_period_s = 60  # appdetails 隐性限速 ~200/5min;搜索接口可更宽,统一取严值
    timeout = 30
    cache_ttl_s = 3600.0
    cooldown_s = 300.0  # 实测 429 后需约 5 分钟冷却
    description = "Steam 商店搜索/应用详情(appdetails 隐性限速约 200/5min,429 需冷却 5min)"

    async def fetch(
        self,
        term: str | None = None,
        appid: int | None = None,
        cc: str = "us",
        lang: str = "en",
    ) -> FetchResult:
        """搜索游戏目录或拉取应用详情。

        Args:
            term: 可选,搜索词(目录搜索)。
            appid: 可选,应用 ID(详情接口,优先于 term)。
            cc: 国家代码(影响价格区域)。
            lang: 返回语言。

        Returns:
            data 为搜索结果或 {"{appid}": {"success", "data"}} 详情结构。

        Raises:
            ValueError: term 与 appid 均未提供。
        """
        if appid is not None:
            return await self._get(
                "https://store.steampowered.com/api/appdetails",
                params={"appids": appid, "cc": cc, "l": lang},
            )
        if not term:
            raise ValueError("term 与 appid 至少提供一个")
        return await self._get(
            "https://store.steampowered.com/api/storesearch/",
            params={"term": term, "cc": cc, "l": lang},
        )


class SteamSpySource(BaseSource):
    """SteamSpy API:玩家数/销量区间估算(top100 榜/单游戏详情)。

    注意 SteamSpy 并发即封(实测 10 并发 0/10),本模块按串行 1/2s 节流;
    估算精度有限,只作方向性参考。
    """

    name = "steamspy"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 1
    rate_period_s = 2
    timeout = 30
    cache_ttl_s = 21600.0
    schema_overrides = {
        "request": {
            "enum": ["top100in2weeks", "top100forever", "top100weekly", "all", "appdetails"],
            "description": "查询类型:双周榜/总榜/周榜/全部/单游戏详情",
        },
    }
    description = "SteamSpy 玩家/销量估算(串行限速;top100 榜/单游戏详情)"

    async def fetch(
        self,
        request: str = "top100in2weeks",
        appid: int | None = None,
    ) -> FetchResult:
        """拉取 SteamSpy 数据。

        Args:
            request: 查询类型 top100in2weeks/top100forever/all 或 appdetails。
            appid: 当 request="appdetails" 时的游戏 ID。

        Returns:
            data 为 {"appid": {...}} 映射或单游戏详情 dict。
        """
        params: dict[str, object] = {"request": request}
        if appid is not None:
            params["appid"] = appid
        return await self._get("https://steamspy.com/api.php", params=params)

