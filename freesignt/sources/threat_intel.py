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
    search_kwarg = "domain"
    limit_kwarg = "limit"
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


def _collect_entry_emails(entries: list | None) -> list[str]:
    """从泄漏条目列表中提取去重邮箱。

    Args:
        entries: infections/employees 条目列表;None 视为空。

    Returns:
        小写去重排序后的邮箱列表。
    """
    emails: set[str] = set()
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        for email in entry.get("emails") or []:
            if isinstance(email, str) and email:
                emails.add(email.lower())
    return sorted(emails)


class HudsonrockSource(BaseSource):
    """Hudson Rock infostealer 泄漏查询:免 key 免费端点。

    返回与域名关联的企业感染(infostealer 受害主机)与员工邮箱泄漏
    规模,是评估目标公司安全水位的暗面信号;数据变化缓慢,建议长缓存。
    """

    name = "hudsonrock"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 5
    rate_period_s = 60
    cache_ttl_s = 86400.0
    search_kwarg = "domain"
    description = "Hudson Rock infostealer 域名泄漏画像(企业感染/员工邮箱,涉敏感数据)"

    async def fetch(self, domain: str) -> FetchResult:
        """查询某域名关联的 infostealer 泄漏概览与邮箱清单。

        Args:
            domain: 主域名(如 example.com)。

        Returns:
            data 为 {"domain", "corporate"(企业概览 dict 或 None),
            "infections_count", "employees_count", "emails": [...],
            "latest_fingerprint"}——emails 为两类条目合并去重的邮箱清单;
            无泄漏数据时各计数为 0、emails 为空列表(仍为成功结果)。

        Raises:
            ValueError: domain 为空。
        """
        if not domain:
            raise ValueError("domain 不能为空")
        result = await self._get(
            "https://cavalier.hudsonrock.com/api/v2/free/domain",
            params={"domain": domain},
        )
        if not result.ok or not isinstance(result.data, dict):
            return result
        data: dict = result.data
        infections = data.get("infections") or []
        employees = data.get("employees") or []
        emails = _collect_entry_emails(infections) + _collect_entry_emails(employees)
        result.data = {
            "domain": data.get("domain", domain),
            "corporate": data.get("corporates"),
            "infections_count": len(infections),
            "employees_count": len(employees),
            "emails": sorted(set(emails)),
            "latest_fingerprint": data.get("latest_fingerprint"),
        }
        return result
