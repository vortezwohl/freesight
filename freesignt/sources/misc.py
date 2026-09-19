"""杂项免费免 key 源:itch.io RSS(独立游戏)与 crt.sh(CT 证书日志)。

实测基准(2026-09-09):
- itch.io RSS 宽松(1.4s);
- crt.sh 为单台服务器,5 并发仅 3/5 成功且 P50 高达 17s,
  声明串行 + 长间隔 + 更多重试次数,由引擎指数退避兜底。
"""

from __future__ import annotations

import asyncio

from freesignt.core.base import BaseSource
from freesignt.core.models import FetchResult, SourceCategory


class ItchIoFeedSource(BaseSource):
    """itch.io RSS:独立游戏最新发布流(原始 XML,to_hits 顺带解析条目)。"""

    name = "itchio_feed"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 30
    rate_period_s = 60
    cache_ttl_s = 1800.0
    schema_overrides = {
        "feed": {"enum": ["newest", "popular", "free"]},
    }
    description = "itch.io 最新独立游戏 RSS(原始 XML + 归一化条目)"

    async def fetch(self, feed: str = "newest") -> FetchResult:
        """拉取 itch.io 游戏 RSS。

        Args:
            feed: newest(最新)/popular(热门)/free(免费)。

        Returns:
            data 为 RSS XML 原始文本(字符串);to_hits 会解析出条目。
        """
        return await self._get(f"https://itch.io/games/{feed}.xml")


class CrtShSource(BaseSource):
    """crt.sh CT 证书日志:某域名的全部已签发证书与子域名。

    隐形产品预发布环境的最强免费信号(新子域名早于官方公告),
    但服务为单机部署且不稳定,失败重试由引擎指数退避兜底
    (max_retries=3,退避上限见 HttpConfig)。
    """

    name = "crt_sh"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 1
    rate_period_s = 20  # 单机服务,串行 + 长间隔
    timeout = 90
    max_retries = 3
    cache_ttl_s = 86400.0
    search_kwarg = "domain"
    description = "CT 证书透明日志子域名发现(慢源:串行/长超时/指数退避)"

    async def fetch(self, domain: str) -> FetchResult:
        """查询某域名相关的全部证书记录并提取唯一子域名。

        Args:
            domain: 主域名(如 openai.com)。

        Returns:
            data 为 {"subdomains": [...], "records": int};
            网络失败重试耗尽时 ok=False。
        """
        result = await self._get(
            "https://crt.sh/", params={"q": f"%.{domain}", "output": "json"}
        )
        if not result.ok:
            # crt.sh 高频 504/超时,补一次静默等待重试(引擎已按退避重试过,
            # 此处仅对"返回 200 但结构异常"与偶发失败做最后兜底)。
            await asyncio.sleep(10)
            result = await self._get(
                "https://crt.sh/",
                params={"q": f"%.{domain}", "output": "json"},
            )
        if result.ok and isinstance(result.data, list):
            names: set[str] = set()
            for rec in result.data:
                if not isinstance(rec, dict):
                    continue
                for name in str(rec.get("name_value", "")).splitlines():
                    name = name.strip()
                    if name and not name.startswith("*"):
                        names.add(name)
            result.data = {"subdomains": sorted(names), "records": len(result.data)}
        elif result.ok:
            result = FetchResult(
                ok=False, status=result.status, latency_s=result.latency_s,
                error="crt.sh 返回了非 JSON 结构(可能为临时错误页)", source=self.name,
            )
        return result

