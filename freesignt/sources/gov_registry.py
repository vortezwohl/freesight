"""政府/法定披露免费免 key 源:SEC、USPTO 商标、RDAP 域名、Common Crawl。

实测基准(2026-09-09):
- SEC 政策 10 req/s 且 UA 必须"公司名 邮箱"格式(随意 UA 曾触发 500);
- USPTO/Verisign RDAP/Common Crawl 均无限速头(1.4-3.6s)。
"""

from __future__ import annotations

import json

from freesignt.core.base import BaseSource
from freesignt.core.models import FetchResult, SourceCategory


class SecEdgarSource(BaseSource):
    """SEC EDGAR:上市公司提交历史与全文检索(含私募 Form D,stealth 融资信号)。"""

    name = "sec_edgar"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 8
    rate_period_s = 60  # 政策 10/s,批量采集取 8/min 保守值
    timeout = 30
    cache_ttl_s = 1800.0
    description = "SEC EDGAR 提交历史(CIK)与全文检索(Form D 挖 stealth 融资)"

    def _build_headers(self) -> dict[str, str]:
        """覆盖默认 UA 为 SEC 要求的"公司名 邮箱"申明格式。"""
        ua = (
            self.engine.config.sec_user_agent
            if self.engine
            else "FreeSightResearch admin@example.org"
        )
        return {"User-Agent": ua, "Accept": "application/json"}

    async def fetch(
        self,
        cik: str | None = None,
        query: str | None = None,
        forms: str | None = None,
    ) -> FetchResult:
        """按 CIK 拉提交历史,或全文检索。

        Args:
            cik: 可选,10 位 CIK 字符串(如 "0001318605")。
            query: 可选,全文检索词(带引号精确匹配)。
            forms: 可选,检索限定表单类型(如 "8-K"/"D")。

        Returns:
            data 为 submissions dict 或检索结果 {"hits": {"total", "hits": [...]}}。

        Raises:
            ValueError: cik 与 query 均未提供。
        """
        if cik:
            return await self._get(f"https://data.sec.gov/submissions/CIK{cik}.json")
        if not query:
            raise ValueError("cik 与 query 至少提供一个")
        params: dict[str, object] = {"q": f'"{query}"'}
        if forms:
            params["forms"] = forms
        return await self._get("https://efts.sec.gov/LATEST/search-index", params=params)


class UsptoTrademarkSource(BaseSource):
    """USPTO 商标库(IBD API):商标申请常早于产品发布数月。"""

    name = "uspto_trademark"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 30
    rate_period_s = 60
    timeout = 30
    cache_ttl_s = 21600.0
    description = "USPTO 商标检索(发布前品牌名先行信号)"

    async def fetch(self, search_text: str, rows: int = 20) -> FetchResult:
        """按关键词检索商标文档。

        Args:
            search_text: 商标关键词/品牌名。
            rows: 返回条数。

        Returns:
            data 为 IBD 响应 dict(含 trademark 文档列表)。
        """
        return await self._get(
            "https://developer.uspto.gov/ibd-api/v1/trademark/documents",
            params={"searchText": search_text, "rows": rows},
        )


class RdapDomainSource(BaseSource):
    """Verisign RDAP:.com/.net 域名注册信息(注册时间/注册商/状态)。"""

    name = "rdap_domain"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 60
    rate_period_s = 60
    cache_ttl_s = 86400.0
    description = ".com/.net 域名 RDAP 注册情报(新域名=新产品线索)"

    async def fetch(self, domain: str, tld_api: str | None = None) -> FetchResult:
        """查询域名注册信息。

        Args:
            domain: 完整域名(如 openai.com)。
            tld_api: 可选,自定义 RDAP 服务端点;默认按 .com/.net 走 Verisign。
                其他后缀(.io/.so 等)需传对应注册局端点。

        Returns:
            data 为 RDAP domain 对象(含 events/registrant 结构化数据)。

        Raises:
            ValueError: 域名后缀不受支持且未提供 tld_api。
        """
        if tld_api:
            url = f"{tld_api.rstrip('/')}/domain/{domain}"
        elif domain.endswith((".com", ".net")):
            tld = domain.rsplit(".", 1)[1]
            url = f"https://rdap.verisign.com/{tld}/v1/domain/{domain}"
        else:
            tld = domain.rsplit(".", 1)[-1]
            raise ValueError(f"后缀 {tld} 需提供对应注册局 tld_api")
        return await self._get(url)


class CommonCrawlSource(BaseSource):
    """Common Crawl 索引:按域名检索全网快照收录记录(历史页面发现)。"""

    name = "common_crawl"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 30
    rate_period_s = 60
    timeout = 30
    cache_ttl_s = 86400.0
    description = "Common Crawl 域名收录索引(快照时间/URL/MIME,批量发现用)"

    async def fetch(
        self,
        url: str,
        snapshot: str = "CC-MAIN-2024-33",
        limit: int = 10,
    ) -> FetchResult:
        """查询某 URL/域名在指定快照中的收录记录。

        Args:
            url: 目标域名(可带通配如 example.com/*)。
            snapshot: 快照标识(默认 2024-33 期)。
            limit: 返回条数。

        Returns:
            data 为收录记录列表(list[dict],每行一个 JSON 记录)。
        """
        result = await self._get(
            f"https://index.commoncrawl.org/{snapshot}-index",
            params={"url": url, "output": "json", "limit": limit},
        )
        if result.ok and isinstance(result.data, str):
            # 该接口返回 JSON Lines(整体不是合法 JSON),逐行解析。
            records = []
            for line in result.data.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except ValueError:
                    continue
            result.data = records
        return result

