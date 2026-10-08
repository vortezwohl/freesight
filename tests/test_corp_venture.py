"""公司注册与风投类源的离线单测(MockTransport)。

覆盖:日本法人番号、法国 Sirene/BODACC、挪威 Brreg、FDIC、
YC 目录、SEC Form D、Signal NFX。
"""

from __future__ import annotations

import pytest

# ---- 公司注册 ----------------------------------------------------------------


async def test_jp_houjin_bangou_by_name(client, router) -> None:
    """按商号检索归一化出 corporations 列表。"""
    router.add_json("houjin-bangou.nta.go.jp", {
        "message": None, "count": 1,
        "corporation": [{"name": "トヨタ自動車", "houjinNumber": "0180001017579"}],
    })
    result = await client.fetch("jp_houjin_bangou", name="トヨタ")
    assert result.ok
    assert result.data["corporations"][0]["houjinNumber"] == "0180001017579"


async def test_jp_houjin_bangou_number_validation(client) -> None:
    """法人番号必须 13 位数字。"""
    with pytest.raises(ValueError):
        await client.fetch("jp_houjin_bangou", number="123")
    with pytest.raises(ValueError):
        await client.fetch("jp_houjin_bangou")


async def test_fr_sirene_search(client, router) -> None:
    """法国公司检索归一化。"""
    router.add_json("recherche-entreprises.api.gouv.fr/search", {
        "total_results": 3, "results": [{"siren": "383474814", "nom_complet": "AIRBUS"}],
    })
    result = await client.fetch("fr_sirene", query="airbus")
    assert result.ok
    assert result.data["total_results"] == 3
    assert result.data["results"][0]["nom_complet"] == "AIRBUS"


async def test_fr_bodacc_records(client, router) -> None:
    """BODACC 公告检索归一化。"""
    router.add_json("bodacc-datadila.opendatasoft.com", {
        "nhits": 5, "records": [{"recordid": "r1"}],
    })
    result = await client.fetch("fr_bodacc", query="airbus")
    assert result.ok and result.data["nhits"] == 5


async def test_no_brreg_orgnr_and_name(client, router) -> None:
    """挪威:按号直查透传;按名检索取 _embedded。"""
    router.add_json("enhetsregisteret/api/enheter/923609016",
                    {"organisasjonsnummer": "923609016", "navn": "EQUINOR ASA"})
    result = await client.fetch("no_brreg", orgnr="923609016")
    assert result.ok and result.data["navn"] == "EQUINOR ASA"

    router.add_json("enhetsregisteret/api/enheter", {
        "_embedded": {"enheter": [{"organisasjonsnummer": "923609016"}]},
        "page": {"totalElements": 1},
    })
    named = await client.fetch("no_brreg", name="equinor", refresh=True)
    assert named.ok and named.data["items"][0]["organisasjonsnummer"] == "923609016"


async def test_no_brreg_orgnr_validation(client) -> None:
    """组织号码必须 9 位数字。"""
    with pytest.raises(ValueError):
        await client.fetch("no_brreg", orgnr="12")


async def test_fdic_banks_search(client, router) -> None:
    """FDIC 机构检索:data 数组展平为 institutions。"""
    router.add_json("banks.data.fdic.gov/api/institutions", {
        "meta": {"total": 4232},
        "data": [{"data": {"NAME": "FIRST BANK", "CERT": 123}}],
    })
    result = await client.fetch("fdic_banks", name="first")
    assert result.ok
    assert result.data["total"] == 4232
    assert result.data["institutions"][0]["NAME"] == "FIRST BANK"


# ---- 风投/创业 ----------------------------------------------------------------


async def test_yc_companies_meta_and_batch_guard(client, router) -> None:
    """meta 维度透传;batch 维度缺 slug 报参数错误。"""
    router.add_json("yc-oss.github.io/api/meta.json",
                    {"companiesCount": 6241, "batchesCount": 51})
    result = await client.fetch("yc_companies")
    assert result.ok and result.data["companiesCount"] == 6241

    with pytest.raises(ValueError):
        await client.fetch("yc_companies", kind="batch")

    router.add_json("yc-oss.github.io/api/batches/s2026.json", [{"name": "Acme"}])
    batch = await client.fetch("yc_companies", kind="batch", slug="s2026")
    assert batch.ok and batch.data[0]["name"] == "Acme"


async def test_sec_form_d_normalization(client, router) -> None:
    """Form D 检索裁剪为关键 hit 字段。"""
    router.add_json("efts.sec.gov/LATEST/search-index", {
        "hits": {
            "total": {"value": 7},
            "hits": [{"_id": "x1", "_source": {
                "cik": ["000123"], "display_names": ["ACME CORP"],
                "file_date": "2026-09-01", "file_type": "D",
            }}],
        },
    })
    result = await client.fetch("sec_form_d", query="acme")
    assert result.ok
    assert result.data["total"] == 7
    assert result.data["hits"][0]["file_type"] == "D"


async def test_signal_nfx_extracts_json_script(client, router) -> None:
    """从页面提取最大的 application/json 数据脚本。"""
    router.add_text(
        "signal.nfx.com/investor-lists",
        '<script type="application/json">{"small": 1}</script>'
        '<script type="application/json">{"investors": ["a", "b"]}</script>',
    )
    result = await client.fetch("signal_nfx", list_slug="fin-tech")
    assert result.ok and result.data == {"investors": ["a", "b"]}


async def test_signal_nfx_no_json_script(client, router) -> None:
    """页面无 JSON 脚本(改版)时显式失败。"""
    router.add_text("signal.nfx.com/investor-lists", "<html><body>redesigned</body></html>")
    result = await client.fetch("signal_nfx", list_slug="x")
    assert not result.ok and "改版" in result.error
