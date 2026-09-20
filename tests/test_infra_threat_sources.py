"""基础设施/威胁情报 9 源移植验证:fetch 与源内归一化逻辑(全部离线桩)。

覆盖 infra_intel(7 源)与 threat_intel(2 源):正常归一化、上游错误
形态识别(hackertarget 错误文本/certspotter 错误对象/CDX 空存档)、
参数校验 ValueError,以及域名情报模式的聚合扇出。
"""

from __future__ import annotations

import pytest

from tests.conftest import RouterTransport

# rapiddns 表格桩:含数据行/表头行(th 无 td)/HTML 实体/非 A 记录。
RAPIDDNS_HTML = """<html><body><table>
<thead><tr><th>Domain</th><th>IP</th><th>Type</th></tr></thead>
<tbody>
<tr><td>api.example.com</td><td>1.2.3.4</td><td>2024-01-01</td><td>US</td><td>A</td></tr>
<tr><td>cdn.example.com</td><td>2001:db8::1</td><td>2024-02-01</td><td>US</td><td>AAAA</td></tr>
<tr><td>alias.example.com</td><td></td><td>2024-03-01</td><td>-</td><td>CNAME</td></tr>
</tbody></table></body></html>"""


async def test_rapiddns_table_normalization(client, router) -> None:
    """rapiddns:HTML 表格压平,A/AAAA 出 IP 映射,CNAME 只计子域。"""
    router.add_text("rapiddns.io", RAPIDDNS_HTML, content_type="text/html")
    result = await client.fetch("rapiddns", domain="example.com")
    assert result.ok
    assert result.data["subdomains"] == [
        "alias.example.com", "api.example.com", "cdn.example.com"
    ]
    assert result.data["ip_map"] == {
        "api.example.com": "1.2.3.4", "cdn.example.com": "2001:db8::1"
    }
    assert result.data["records"] == 3


async def test_subdomain_center_list_and_error_dict(client, router) -> None:
    """subdomain_center:JSON 数组去重;错误 dict 形态判失败。"""
    router.add_json("api.subdomain.center", ["b.example.com", "a.example.com", "a.example.com"])
    result = await client.fetch("subdomain_center", domain="example.com")
    assert result.ok
    assert result.data["subdomains"] == ["a.example.com", "b.example.com"]
    assert result.data["count"] == 2

    router2 = RouterTransport()
    router2.add_json("api.subdomain.center", {"error": "upstream down"})
    from freesignt.core.client import AsyncFreeSight
    async with AsyncFreeSight(transport=router2, rate_multiplier=1000.0) as c2:
        failed = await c2.fetch("subdomain_center", domain="example.com")
    assert not failed.ok and "非数组" in failed.error


async def test_otx_passive_dns_dedup(client, router) -> None:
    """otx_passive_dns:hostname 去重,records 保留原始计数。"""
    router.add_json("otx.alienvault.com", {"passive_dns": [
        {"hostname": "www.example.com", "record_type": "A", "first_seen": "2024-01-01"},
        {"hostname": "www.example.com", "record_type": "A", "first_seen": "2024-02-01"},
        {"hostname": "old.example.com", "record_type": "A", "first_seen": "2023-01-01"},
    ]})
    result = await client.fetch("otx_passive_dns", domain="example.com")
    assert result.ok
    assert result.data["subdomains"] == ["old.example.com", "www.example.com"]
    assert result.data["records"] == 3


async def test_hackertarget_csv_and_quota_error(client, router) -> None:
    """hackertarget:CSV 行解析为 ip_map;配额耗尽文本判失败。"""
    router.add_text(
        "api.hackertarget.com",
        "api.example.com,1.2.3.4\nwww.example.com,1.2.3.5\nbad-line-only-host",
    )
    result = await client.fetch("hackertarget", domain="example.com")
    assert result.ok
    assert result.data["ip_map"] == {
        "api.example.com": "1.2.3.4", "www.example.com": "1.2.3.5"
    }
    assert result.data["count"] == 2

    router2 = RouterTransport()
    router2.add_text("api.hackertarget.com", "API count exceeded - 50 per day")
    from freesignt.core.client import AsyncFreeSight
    async with AsyncFreeSight(transport=router2, rate_multiplier=1000.0) as c2:
        failed = await c2.fetch("hackertarget", domain="example.com")
    assert not failed.ok and "上游返回错误" in failed.error


async def test_shodan_internetdb_passthrough_and_validation(client, router) -> None:
    """shodan_internetdb:JSON 原样透传;非法 ip 参数抛 ValueError。"""
    router.add_json("internetdb.shodan.io", {
        "ip": "1.2.3.4", "ports": [80, 443], "cpes": ["cpe:/a:nginx:nginx"],
        "hostnames": ["example.com"], "vulns": ["CVE-2024-0001"], "tags": [],
    })
    result = await client.fetch("shodan_internetdb", ip="1.2.3.4")
    assert result.ok and result.data["ports"] == [80, 443]
    assert result.data["vulns"] == ["CVE-2024-0001"]
    with pytest.raises(ValueError, match="ip 参数不是合法 IP"):
        await client.fetch("shodan_internetdb", ip="not-an-ip")


async def test_certspotter_dns_names_and_rate_limit_object(client, router) -> None:
    """certspotter:SAN 域名去重去通配;错误对象(code)判失败。"""
    router.add_json("api.certspotter.com", [
        {"id": "1", "dns_names": ["example.com", "www.example.com", "*.wild.example.com"]},
        {"id": "2", "dns_names": ["www.example.com", "api.example.com"]},
    ])
    result = await client.fetch("certspotter", domain="example.com")
    assert result.ok
    assert result.data["subdomains"] == [
        "api.example.com", "example.com", "www.example.com"
    ]
    assert result.data["certificates"] == 2

    router2 = RouterTransport()
    router2.add_json("api.certspotter.com", {"code": "rate_limited"})
    from freesignt.core.client import AsyncFreeSight
    async with AsyncFreeSight(transport=router2, rate_multiplier=1000.0) as c2:
        failed = await c2.fetch("certspotter", domain="example.com")
    assert not failed.ok and "rate_limited" in failed.error


async def test_wayback_cdx_rows_and_empty_archive(client, router) -> None:
    """wayback_cdx:表头跳过/时间戳转 ISO/'-'归 None;空存档计 0 成功。"""
    router.add_json("web.archive.org", [
        ["timestamp", "original", "statuscode", "mimetype"],
        ["20240101120000", "https://example.com/", "200", "text/html"],
        ["20250630235959", "https://example.com/pricing", "-", "-"],
    ])
    result = await client.fetch("wayback_cdx", domain="example.com", limit=2)
    assert result.ok and result.data["count"] == 2
    first, second = result.data["urls"]
    assert first["url"] == "https://example.com/" and first["last_seen"] == "2024-01-01T12:00:00Z"
    assert second["status_code"] is None and second["mimetype"] is None
    assert second["last_seen"] == "2025-06-30T23:59:59Z"

    router2 = RouterTransport()
    router2.add_text("web.archive.org", "", content_type="text/plain")
    from freesignt.core.client import AsyncFreeSight
    async with AsyncFreeSight(transport=router2, rate_multiplier=1000.0) as c2:
        empty = await c2.fetch("wayback_cdx", domain="no-archive.example")
    assert empty.ok and empty.data == {"urls": [], "count": 0}


async def test_urlscan_search_normalization(client, router) -> None:
    """urlscan:page/task 两层字段压平为 scans 条目。"""
    router.add_json("urlscan.io", {
        "total": 43, "has_more": True,
        "results": [
            {"page": {"url": "https://example.com/", "domain": "example.com",
                      "ip": "1.2.3.4", "asn": "AS15169", "asnname": "GOOGLE"},
             "task": {"time": "2026-09-01T00:00:00.000Z"}},
            {"page": {"url": "https://example.com/app"}, "task": {}},
        ],
    })
    result = await client.fetch("urlscan", domain="example.com", limit=2)
    assert result.ok
    assert result.data["total"] == 43 and result.data["has_more"] is True
    assert result.data["scans"][0]["asn_name"] == "GOOGLE"
    assert result.data["scans"][0]["scan_time"] == "2026-09-01T00:00:00.000Z"
    assert result.data["scans"][1]["ip"] is None


async def test_hudsonrock_emails_merge_and_null_fields(client, router) -> None:
    """hudsonrock:infections/employees 邮箱合并去重;null 字段计 0。"""
    router.add_json("cavalier.hudsonrock.com", {
        "corporates": {"stealer_logs_count": 12, "third_party_domains_count": 3},
        "infections": [
            {"emails": ["Bob@Example.com", "alice@example.com"]},
            {"emails": ["alice@example.com"]},
        ],
        "employees": [{"emails": ["hr@example.com"]}],
        "domain": "example.com",
        "latest_fingerprint": "abc123",
    })
    result = await client.fetch("hudsonrock", domain="example.com")
    assert result.ok
    assert result.data["emails"] == [
        "alice@example.com", "bob@example.com", "hr@example.com"
    ]
    assert result.data["infections_count"] == 2 and result.data["employees_count"] == 1
    assert result.data["corporate"]["stealer_logs_count"] == 12

    router2 = RouterTransport()
    router2.add_json("cavalier.hudsonrock.com", {
        "corporates": None, "infections": None, "employees": None,
        "domain": "example.com",
    })
    from freesignt.core.client import AsyncFreeSight
    async with AsyncFreeSight(transport=router2, rate_multiplier=1000.0) as c2:
        empty = await c2.fetch("hudsonrock", domain="example.com")
    assert empty.ok
    assert empty.data["emails"] == [] and empty.data["infections_count"] == 0


async def test_domain_intel_fanout_with_new_sources(client, router) -> None:
    """域名情报模式:新源参与显式指定源的聚合扇出(含部分失败容忍)。"""
    router.add_json("api.subdomain.center", ["a.example.com"])
    router.add_json("cavalier.hudsonrock.com", {
        "corporates": None, "infections": None, "employees": None,
    })
    # rapiddns 不注册桩 -> 走 404 失败路径,验证部分失败不中断聚合。
    agg = await client.search(
        "example.com",
        sources=["subdomain_center", "hudsonrock", "rapiddns"],
    )
    assert agg.ok_sources == ["subdomain_center", "hudsonrock"]
    assert agg.failed_sources == ["rapiddns"]
    assert agg.results["subdomain_center"].data["count"] == 1


async def test_new_sources_not_in_default_search_set(client, router) -> None:
    """新源均为 search_default=False:不进入默认扇出,不打扰既有语义。"""
    from freesignt.core import registry
    for name in ("rapiddns", "subdomain_center", "otx_passive_dns", "hackertarget",
                 "shodan_internetdb", "certspotter", "wayback_cdx", "urlscan", "hudsonrock"):
        info = registry.get(name).info()
        assert info.searchable, name
        assert not info.search_default, name
        assert info.category.value == "free_nokey", name
