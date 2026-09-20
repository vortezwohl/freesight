"""基础设施足迹源(免 key 档):子域/被动 DNS/CT 冗余/存档索引/IP 画像。

端点行为参照 theHarvester 社区实测(2026-09, master 分支)与各服务公开文档;
限速为保守声明(未逐源实测复核),真实 429 由引擎自适应冷却兜底。
与 crt_sh 的关系:本模块的 certspotter/rapiddns/subdomain_center 提供子域
发现的第二/第三来源,用于对冲 crt.sh 单机部署的不稳定。
"""

from __future__ import annotations

import html as html_lib
import ipaddress
import re

from freesignt.core.base import BaseSource
from freesignt.core.models import FetchResult, SourceCategory


class RapidDnsSource(BaseSource):
    """RapidDNS 子域聚合:HTML 表格,免 key 无官方 API。

    返回页为纯 HTML(无 JSON 接口),源内用正则做必要的表格压平;
    表尾"类型"列区分 A/AAAA(带 IP 映射)与其他记录类型。
    """

    name = "rapiddns"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 10
    rate_period_s = 60
    timeout = 30
    cache_ttl_s = 21600.0
    description = "RapidDNS 子域聚合(HTML 表格解析,含 A/AAAA 记录的 IP 映射)"

    async def fetch(self, domain: str) -> FetchResult:
        """查询某域名在 RapidDNS 聚合库中的全部子域记录。

        Args:
            domain: 主域名(如 example.com)。

        Returns:
            data 为 {"subdomains": [...], "ip_map": {...}, "records": int};
            records 为表格有效行数(含无 IP 的记录类型);
            网络失败重试耗尽时 ok=False。

        Raises:
            ValueError: domain 为空。
        """
        if not domain:
            raise ValueError("domain 不能为空")
        result = await self._get(f"https://rapiddns.io/subdomain/{domain}", params={"full": "1"})
        if not result.ok or not isinstance(result.data, str):
            return result
        hosts: set[str] = set()
        ip_map: dict[str, str] = {}
        rows = 0
        for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", result.data, re.S):
            cells = [
                html_lib.unescape(re.sub(r"<[^>]+>", "", c)).strip()
                for c in re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.S)
            ]
            # 有效数据行:主机名列非空且至少 3 列;表头行为 <th> 不产生 <td>。
            if len(cells) < 3 or not cells[0]:
                continue
            rows += 1
            hosts.add(cells[0])
            # 末列为记录类型;A/AAAA 行的第 2 列为解析 IP。
            if cells[-1] in {"A", "AAAA"} and cells[1]:
                ip_map[cells[0]] = cells[1]
        result.data = {"subdomains": sorted(hosts), "ip_map": ip_map, "records": rows}
        return result


class SubdomainCenterSource(BaseSource):
    """Subdomain Center 子域聚合 API:JSON 数组直出,免 key。

    响应为子域字符串数组;错误时服务返回 dict 形态,据此判失败。
    """

    name = "subdomain_center"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 10
    rate_period_s = 60
    cache_ttl_s = 21600.0
    description = "Subdomain Center 子域聚合 API(JSON 直出)"

    async def fetch(self, domain: str) -> FetchResult:
        """查询某域名的全部已知子域。

        Args:
            domain: 主域名(如 example.com)。

        Returns:
            data 为 {"subdomains": [...], "count": int};
            响应非数组(错误页/错误对象)时 ok=False。

        Raises:
            ValueError: domain 为空。
        """
        if not domain:
            raise ValueError("domain 不能为空")
        result = await self._get("https://api.subdomain.center/", params={"domain": domain})
        if not result.ok:
            return result
        if isinstance(result.data, list):
            names = sorted({str(n) for n in result.data if isinstance(n, str) and n})
            result.data = {"subdomains": names, "count": len(names)}
            return result
        return FetchResult(
            ok=False, status=result.status, latency_s=result.latency_s,
            error="subdomain_center 返回了非数组结构(可能为临时错误页)", source=self.name,
        )


class OtxPassiveDnsSource(BaseSource):
    """AlienVault OTX 被动 DNS:社区威胁情报平台的域名历史解析记录,免 key。

    被动 DNS 记录是"曾经解析到"的历史事实,可发现已下线的内部子域。
    """

    name = "otx_passive_dns"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 20
    rate_period_s = 60
    cache_ttl_s = 21600.0
    description = "AlienVault OTX 被动 DNS 历史(域名历史解析记录去重)"

    async def fetch(self, domain: str) -> FetchResult:
        """查询某域名的被动 DNS 历史解析记录。

        Args:
            domain: 主域名(如 example.com)。

        Returns:
            data 为 {"subdomains": [...], "records": int};
            records 为平台返回的原始记录总数(未去重)。

        Raises:
            ValueError: domain 为空。
        """
        if not domain:
            raise ValueError("domain 不能为空")
        result = await self._get(
            f"https://otx.alienvault.com/api/v1/indicators/domain/{domain}/passive_dns"
        )
        if not result.ok or not isinstance(result.data, dict):
            return result
        entries = result.data.get("passive_dns")
        if not isinstance(entries, list):
            return FetchResult(
                ok=False, status=result.status, latency_s=result.latency_s,
                error="otx 响应缺少 passive_dns 数组", source=self.name,
            )
        hosts = {
            str(e.get("hostname"))
            for e in entries
            if isinstance(e, dict) and e.get("hostname")
        }
        result.data = {"subdomains": sorted(hosts), "records": len(entries)}
        return result


class HackerTargetSource(BaseSource):
    """HackerTarget hostsearch:子域+IP 映射,免 key 每日限量。

    响应为 CSV 文本("host,ip" 每行);免 key 配额较小(约每日 50 次),
    适合低频补充查询,上游配额耗尽时返回带错误标记的文本而非 HTTP 错误。
    """

    name = "hackertarget"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 5
    rate_period_s = 60
    cache_ttl_s = 21600.0
    description = "HackerTarget hostsearch(免 key 每日限量,子域+IP 映射)"

    async def fetch(self, domain: str) -> FetchResult:
        """查询某域名的子域与 IP 映射(hostsearch 端点)。

        Args:
            domain: 主域名(如 example.com)。

        Returns:
            data 为 {"ip_map": {host: ip}, "count": int};
            上游配额耗尽/输入非法时 ok=False 并透出上游提示。

        Raises:
            ValueError: domain 为空。
        """
        if not domain:
            raise ValueError("domain 不能为空")
        result = await self._get(
            "https://api.hackertarget.com/hostsearch/", params={"q": domain}
        )
        if not result.ok or not isinstance(result.data, str):
            return result
        text = result.data.strip()
        # 上游配额/校验错误以 200 + 错误文本返回,须显式识别(小写比对)。
        lowered = text.lower()
        for marker in ("api count exceeded", "invalid input", "error check"):
            if marker in lowered:
                return FetchResult(
                    ok=False, status=result.status, latency_s=result.latency_s,
                    error=f"hackertarget 上游返回错误: {text[:120]}", source=self.name,
                )
        ip_map: dict[str, str] = {}
        for line in text.splitlines():
            host, _, ip = line.partition(",")
            host, ip = host.strip(), ip.strip()
            if host and ip:
                ip_map[host] = ip
        result.data = {"ip_map": ip_map, "count": len(ip_map)}
        return result


class ShodanInternetdbSource(BaseSource):
    """Shodan InternetDB:IP 开放端口/服务指纹/漏洞标签,官方免费端点免 key。

    入参为 IP 而非域名(本 SDK 是纯 HTTP 聚合层,不做 DNS 解析;
    域名到 IP 的解析由调用方完成)。404 表示该 IP 无公开数据。
    """

    name = "shodan_internetdb"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 30
    rate_period_s = 60
    cache_ttl_s = 86400.0
    description = "Shodan InternetDB IP 画像(开放端口/CPE/漏洞标签,免 key)"

    async def fetch(self, ip: str) -> FetchResult:
        """查询某 IP 的公开暴露画像(端口/主机名/CPE/漏洞/CVE 标签)。

        Args:
            ip: IPv4 或 IPv6 地址字符串。

        Returns:
            data 为服务原样 JSON dict(ip/ports/cpes/hostnames/vulns/tags);
            404(无公开数据)按引擎统一语义记为 ok=False。

        Raises:
            ValueError: ip 不是合法 IP 地址。
        """
        try:
            ipaddress.ip_address(ip)
        except ValueError as exc:
            raise ValueError(f"ip 参数不是合法 IP 地址: {ip!r}") from exc
        return await self._get(f"https://internetdb.shodan.io/{ip}")


class CertspotterSource(BaseSource):
    """Certificate Spotter CT 日志:cert.sh 的第二 CT 来源,基础查询免 key。

    免 key 档有小时级配额,配额耗尽时服务返回 {"code": ...} 错误对象
    而非 HTTP 429,须在源内识别。
    """

    name = "certspotter"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 5
    rate_period_s = 60
    timeout = 30
    cache_ttl_s = 21600.0
    description = "Certificate Spotter CT 日志(crt_sh 的冗余源,含子域展开)"

    async def fetch(self, domain: str) -> FetchResult:
        """查询某域名(含子域)的全部证书记录并提取 SAN 域名。

        Args:
            domain: 主域名(如 example.com)。

        Returns:
            data 为 {"subdomains": [...], "certificates": int};
            通配符证书(*.domain)的泛名已被剔除;
            上游配额耗尽(错误对象)时 ok=False。

        Raises:
            ValueError: domain 为空。
        """
        if not domain:
            raise ValueError("domain 不能为空")
        result = await self._get(
            "https://api.certspotter.com/v1/issuances",
            params={"domain": domain, "include_subdomains": "true", "expand": "dns_names"},
        )
        if not result.ok:
            return result
        if isinstance(result.data, dict):
            # 免 key 配额耗尽等错误以 200 + {"code": ...} 形态返回。
            return FetchResult(
                ok=False, status=result.status, latency_s=result.latency_s,
                error=f"certspotter 错误响应: {result.data.get('code', 'unknown')}",
                source=self.name,
            )
        if not isinstance(result.data, list):
            return FetchResult(
                ok=False, status=result.status, latency_s=result.latency_s,
                error="certspotter 返回了非数组结构", source=self.name,
            )
        names: set[str] = set()
        for cert in result.data:
            if not isinstance(cert, dict):
                continue
            for name in cert.get("dns_names") or []:
                if isinstance(name, str) and name and not name.startswith("*"):
                    names.add(name)
        result.data = {"subdomains": sorted(names), "certificates": len(result.data)}
        return result


def _format_cdx_timestamp(ts: str) -> str:
    """CDX 时间戳(YYYYMMDDhhmmss)转 ISO 8601,提升调研可读性。

    Args:
        ts: CDX 原始 timestamp 字段(可能带时区后缀)。

    Returns:
        "YYYY-MM-DDTHH:MM:SSZ";长度不足 14 位时原样返回。
    """
    digits = re.sub(r"\D", "", ts)[:14]
    if len(digits) != 14:
        return ts
    return (
        f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
        f"T{digits[8:10]}:{digits[10:12]}:{digits[12:14]}Z"
    )


class WaybackCdxSource(BaseSource):
    """Wayback Machine CDX 索引:历史存档 URL 清单,免 key。

    与 common_crawl(非营利全网页库)互补:CDX 是 archive.org 自有索引,
    粒度为"曾经被抓取过的 URL",collapse=urlkey 去重后适合追踪
    竞品站点页面结构的历史演变。
    """

    name = "wayback_cdx"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 5
    rate_period_s = 60
    timeout = 30
    cache_ttl_s = 86400.0
    description = "Wayback Machine CDX 存档索引(域名下历史 URL 清单)"

    async def fetch(self, domain: str, limit: int = 100) -> FetchResult:
        """查询某域名下被 Wayback Machine 抓取过的去重 URL 清单。

        Args:
            domain: 主域名(如 example.com)。
            limit: 返回条数上限(CDX 侧生效,建议 <= 1000)。

        Returns:
            data 为 {"urls": [{"url", "status_code", "mimetype", "last_seen"}],
            "count": int};status_code/mimetype 为 "-" 时归一化为 None;
            空存档(CDX 返回空体文本)记为 count=0 的成功结果。

        Raises:
            ValueError: domain 为空。
        """
        if not domain:
            raise ValueError("domain 不能为空")
        result = await self._get(
            "https://web.archive.org/cdx/search/cdx",
            params={
                "url": domain,
                "matchType": "domain",
                "output": "json",
                "collapse": "urlkey",
                "fl": "timestamp,original,statuscode,mimetype",
                "limit": limit,
            },
        )
        if not result.ok:
            return result
        data = result.data
        if isinstance(data, str):
            # 无存档时 CDX 返回空文本(非 JSON),归一化为空清单。
            if not data.strip():
                result.data = {"urls": [], "count": 0}
                return result
            return FetchResult(
                ok=False, status=result.status, latency_s=result.latency_s,
                error="wayback_cdx 返回了非 JSON 结构(可能为临时错误页)",
                source=self.name,
            )
        if not isinstance(data, list) or not data:
            return FetchResult(
                ok=False, status=result.status, latency_s=result.latency_s,
                error="wayback_cdx 响应结构异常(非二维数组)", source=self.name,
            )
        urls = []
        for row in data[1:]:  # 首行为列名表头。
            if not isinstance(row, list) or len(row) < 4:
                continue
            timestamp, original, status_code, mimetype = row[:4]
            urls.append({
                "url": original,
                "status_code": None if status_code == "-" else status_code,
                "mimetype": None if mimetype == "-" else mimetype,
                "last_seen": _format_cdx_timestamp(str(timestamp)),
            })
        result.data = {"urls": urls, "count": len(urls)}
        return result
