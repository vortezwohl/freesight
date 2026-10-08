"""风投/创业信息免费免 key 源(2026-09 实测)。

- yc_companies: yc-oss 社区维护的 YC 公司目录静态 JSON(GitHub Pages
  部署,每日 GitHub Actions 更新,6200+ 公司,免 key 且稳定);
- sec_form_d: SEC EDGAR 全文检索限定 Form D(美国私募融资法定披露),
  继承 sec_edgar 的申明式 UA 与治理参数;
- signal_nfx: Signal NFX 投资人列表(React 页面,源内提取内嵌
  application/json 数据脚本)。

实测出局项(2026-09-20):
- OpenVC(openvc.app): Cloudflare 浏览器验证墙,非浏览器客户端 403;
  可经 jina_reader 源组合访问(渲染代理或可穿透);
- Crunchbase API: 2025 起免费层彻底取消,不在免 key 范畴。
"""

from __future__ import annotations

import json
import re
from typing import Any

from freesignt.core.base import BaseSource
from freesignt.core.models import FetchResult, SourceCategory
from freesignt.sources.gov_registry import SecEdgarSource


class YcCompaniesSource(BaseSource):
    """YC 公司目录(yc-oss/api 静态 JSON):批次/行业/标签多维检索。

    数据由社区项目每日从 YC 官网 Algolia 索引构建并部署到
    GitHub Pages,是 YC 目录唯一稳定的免 key 程序化通道
    (官网搜索为 JS 渲染,Algolia 前端 key 绑定浏览器上下文)。
    """

    name = "yc_companies"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 30
    rate_period_s = 60  # GitHub Pages CDN 宽松,取保守值
    cache_ttl_s = 21600.0
    schema_overrides = {
        "kind": {
            "enum": ["meta", "all", "top", "hiring", "nonprofit",
                     "batch", "industry", "tag", "changes"],
            "description": "目录维度;batch/industry/tag 需配 slug",
        },
    }
    description = "YC 公司目录静态 JSON(批次/行业/标签,yc-oss 社区维护)"

    _PATH = {
        "meta": "meta.json",
        "all": "companies/all.json",
        "top": "companies/top.json",
        "hiring": "companies/hiring.json",
        "nonprofit": "companies/nonprofit.json",
        "changes": "changes/latest.json",
    }

    async def fetch(self, kind: str = "meta", slug: str = "") -> FetchResult:
        """拉取 YC 公司目录的一个维度。

        Args:
            kind: 目录维度;meta(统计)/all/top/hiring/nonprofit/changes
                为固定清单,batch/industry/tag 需提供 slug。
            slug: kind 为 batch(如 "s2026")/industry(如 "fintech")/
                tag(如 "artificial-intelligence")时的标识。

        Returns:
            data 为该维度的原始 JSON(公司数组或统计对象);
            all 维度数 MB 级,注意内存与缓存开销。

        Raises:
            ValueError: kind 不合法,或 batch/industry/tag 缺 slug。
        """
        if kind in self._PATH:
            path = self._PATH[kind]
        elif kind == "batch":
            if not slug:
                raise ValueError("kind=batch 需要 slug(如 s2026)")
            path = f"batches/{slug}.json"
        elif kind == "industry":
            if not slug:
                raise ValueError("kind=industry 需要 slug(如 fintech)")
            path = f"industries/{slug}.json"
        elif kind == "tag":
            if not slug:
                raise ValueError("kind=tag 需要 slug(如 saas)")
            path = f"tags/{slug}.json"
        else:
            raise ValueError(f"不支持的目录维度: {kind}")
        return await self._get(f"https://yc-oss.github.io/api/{path}")


class SecFormDSource(SecEdgarSource):
    """SEC Form D 检索:美国私募融资(Reg D)法定披露的定向通道。

    继承 sec_edgar 的申明式 UA(SEC 政策要求)与治理参数,
    仅收敛检索范围为 forms=D。
    """

    name = "sec_form_d"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 8
    rate_period_s = 60
    timeout = 30
    cache_ttl_s = 1800.0
    description = "SEC Form D 私募融资披露检索(stealth 融资信号,限定 forms=D)"

    async def fetch(self, query: str, date_range: str | None = None,
                    limit: int = 30) -> FetchResult:
        """全文检索 Form D 披露。

        Args:
            query: 检索词(公司名/品牌名,服务端做引号精确匹配)。
            date_range: 可选,时间窗(如 "10d"/"1y"/"2026-01-01,")。
            limit: 返回条数(取响应 hits 前若干)。

        Returns:
            data 为 {"total", "hits": [...]}(每条含公司名/CIK/日期);

        Raises:
            ValueError: query 为空。
        """
        if not query:
            raise ValueError("query 不能为空")
        params: dict[str, object] = {"q": f'"{query}"', "forms": "D"}
        if date_range:
            params["dateRange"] = date_range
        result = await self._get("https://efts.sec.gov/LATEST/search-index", params=params)
        if not result.ok or not isinstance(result.data, dict):
            return result
        hits = (result.data.get("hits") or {}).get("hits") or []
        total = (result.data.get("hits") or {}).get("total", {}).get("value", len(hits))
        trimmed = [
            {
                "_id": h.get("_id"),
                "cik": (h.get("_source") or {}).get("cik"),
                "display_names": (h.get("_source") or {}).get("display_names"),
                "filed_at": (h.get("_source") or {}).get("file_date"),
                "file_type": (h.get("_source") or {}).get("file_type"),
            }
            for h in hits[:limit] if isinstance(h, dict)
        ]
        result.data = {"total": total, "hits": trimmed}
        return result


class SignalNfxSource(BaseSource):
    """Signal NFX 投资人列表:React 页面内嵌 JSON 数据脚本提取。

    页面为服务端渲染 + application/json 数据脚本(Gatsby 风格),
    源内提取最大 JSON 脚本并解析;页面改版时结构可能变化。
    """

    name = "signal_nfx"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 6
    rate_period_s = 60
    timeout = 30
    cache_ttl_s = 86400.0
    description = "Signal NFX 投资人列表(内嵌 JSON 提取,页面改版敏感)"

    _JSON_SCRIPT = re.compile(
        r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', re.S
    )

    async def fetch(self, list_slug: str = "fin-tech") -> FetchResult:
        """拉取一份 Signal NFX 投资人列表。

        Args:
            list_slug: 列表标识(如 fin-tech / ai-ml / b2b;
            完整目录见 signal.nfx.com/investor-lists)。

        Returns:
            data 为页面内嵌 JSON 解析结果(结构随列表略有差异);
            提取/解析失败时 ok=False。

        Raises:
            ValueError: list_slug 为空。
        """
        if not list_slug:
            raise ValueError("list_slug 不能为空")
        result = await self._get(
            f"https://signal.nfx.com/investor-lists/{list_slug}"
        )
        if not result.ok or not isinstance(result.data, str):
            return result
        # 页面可能含多个 JSON 脚本(统计/主题/数据),取体积最大的数据脚本。
        best: tuple[int, Any] = (0, None)
        for candidate in self._JSON_SCRIPT.findall(result.data):
            try:
                parsed = json.loads(candidate)
            except ValueError:
                continue
            if len(candidate) > best[0]:
                best = (len(candidate), parsed)
        if best[1] is None:
            return FetchResult(
                ok=False, status=result.status, latency_s=result.latency_s,
                error="signal_nfx 页面未找到可解析的内嵌 JSON(可能已改版)",
                source=self.name,
            )
        result.data = best[1]
        return result
