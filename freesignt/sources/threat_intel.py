"""威胁与泄露情报源(免 key 档):公开扫描记录与 infostealer 泄漏指标。

端点行为参照 theHarvester 社区实测(2026-09, master 分支)与各服务公开文档;
限速为保守声明(未逐源实测复核),真实 429 由引擎自适应冷却兜底。
注意:hudsonrock 返回的是真实泄漏数据,调用方须自行确保用途合规。
"""

from __future__ import annotations

from freesignt.core.base import BaseSource
from freesignt.core.models import FetchResult, SourceCategory


class UrlscanSearchSource(BaseSource):
    """urlscan.io 公开扫描记录:社区提交的页面扫描,免 key 可搜索。

    每条记录含页面 URL/IP/ASN/扫描时间,可观察竞品站点的页面变化
    与基础设施迁移;本源只取单页结果(不做 search_after 游标翻页,
    深翻页由调用方按需自建)。
    """

    name = "urlscan"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 10
    rate_period_s = 60
    cache_ttl_s = 3600.0
    description = "urlscan.io 公开扫描记录(页面 URL/IP/ASN/扫描时间)"

    async def fetch(self, domain: str, limit: int = 20) -> FetchResult:
        """搜索某域名相关的公开页面扫描记录。

        Args:
            domain: 主域名(如 example.com)。
            limit: 返回条数上限(urlscan 上限 10000,默认 20)。

        Returns:
            data 为 {"total": int, "has_more": bool, "scans": [
            {"url", "domain", "ip", "asn", "asn_name", "scan_time"}]};
            网络失败重试耗尽时 ok=False。

        Raises:
            ValueError: domain 为空。
        """
        if not domain:
            raise ValueError("domain 不能为空")
        result = await self._get(
            "https://urlscan.io/api/v1/search/",
            params={"q": f"domain:{domain}", "size": limit},
        )
        if not result.ok or not isinstance(result.data, dict):
            return result
        scans = []
        for item in result.data.get("results") or []:
            if not isinstance(item, dict):
                continue
            page = item.get("page") or {}
            task = item.get("task") or {}
            scans.append({
                "url": page.get("url"),
                "domain": page.get("domain"),
                "ip": page.get("ip"),
                "asn": page.get("asn"),
                "asn_name": page.get("asnname"),
                "scan_time": task.get("time"),
            })
        result.data = {
            "total": result.data.get("total", len(scans)),
            "has_more": bool(result.data.get("has_more")),
            "scans": scans,
        }
        return result


class HudsonrockSource(BaseSource):
    """Hudson Rock infostealer 泄漏查询:免 key 免费端点。

    返回与域名关联的凭据命中规模(员工/用户/第三方)与泄漏 URL 清单,
    是评估目标公司安全水位的暗面信号;数据变化缓慢,建议长缓存。
    端点为 osint-tools/search-by-domain(2026-10-08 实测;旧 v2/free/domain
    已 404 下线),邮箱级明细须走官方 search-by-email 端点另行查询。
    """

    name = "hudsonrock"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 5
    rate_period_s = 60
    cache_ttl_s = 86400.0
    description = "Hudson Rock infostealer 域名泄漏画像(凭据命中规模/泄漏 URL,涉敏感数据)"

    async def fetch(self, domain: str) -> FetchResult:
        """查询某域名关联的 infostealer 泄漏概览与泄漏 URL 清单。

        Args:
            domain: 主域名(如 example.com)。

        Returns:
            data 为 {"domain", "total"(命中凭据总数), "total_stealers"
            (全网窃密木马规模), "employees_count", "users_count",
            "third_parties_count", "employee_urls", "client_urls",
            "all_urls"(条目含 url/occurrence/type), "logo"}——
            无泄漏数据时各计数为 0、URL 清单为空列表(仍为成功结果)。

        Raises:
            ValueError: domain 为空。
        """
        if not domain:
            raise ValueError("domain 不能为空")
        result = await self._get(
            "https://cavalier.hudsonrock.com/api/json/v2/osint-tools/search-by-domain",
            params={"domain": domain},
        )
        if not result.ok or not isinstance(result.data, dict):
            return result
        data: dict = result.data
        # data 子对象在无泄漏域名上可能缺失,统一按空处理;
        # 计数字段上游可能给 null,归一化为 0 以稳定调用方分支。
        detail = data.get("data") or {}
        result.data = {
            "domain": domain,
            "total": data.get("total") or 0,
            "total_stealers": data.get("totalStealers") or 0,
            "employees_count": data.get("employees") or 0,
            "users_count": data.get("users") or 0,
            "third_parties_count": data.get("third_parties") or 0,
            "employee_urls": detail.get("employees_urls") or [],
            "client_urls": detail.get("clients_urls") or [],
            "all_urls": detail.get("all_urls") or [],
            "logo": data.get("logo"),
        }
        return result
