"""公司注册/法定披露免费免 key 源(2026-09 实测)。

覆盖各国"官方或官方同源"的公司主体数据渠道:
- jp_houjin_bangou: 日本国税庁法人番号系统 Web API(按官方规格实现;
  实测部分网络环境 TLS 握手异常,属网络层可达性问题);
- fr_sirene: 法国 DGFiP recherche-entreprises(Sirene/RNE 同源,
  3100 万+实体,实测 200);
- fr_bodacc: 法国 BODACC 法定公告(opendatasoft 免 key 端点,
  承担 INPI RNE 的披露角色——INPI 官方 API 有 Cloudflare 墙);
- no_brreg: 挪威 Brønnøysund 注册中心开放 API(实测 200);
- fdic_banks: 美国 FDIC 银行机构数据库(实测 200)。

实测出局项(2026-09-20,详见 README 说明与边界):
- EDINET 书类一览:v1 已 403 关闭,v2 需 Subscription-Key(免 key 不可达);
- 德国 Handelsregister:JSF 表单站且 TLS 握手超时,OffeneRegister 镜像 502。
"""

from __future__ import annotations

from freesignt.core.base import BaseSource
from freesignt.core.models import FetchResult, SourceCategory


class JpHoujinBangouSource(BaseSource):
    """日本国税庁法人番号 Web API:按名称或 13 位法人番号查法人基础信息。

    官方公开免 key 端点(仕様:type=12 返回 Unicode JSON;mode=1 预览
    模式最多 10 条)。注意实测部分非日本网络环境 TLS 握手失败,属
    网络层可达性问题而非接口变更。
    """

    name = "jp_houjin_bangou"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 10
    rate_period_s = 60
    timeout = 30
    cache_ttl_s = 86400.0
    description = "日本法人番号系统 Web API(国税庁官方,名称/番号检索)"

    async def fetch(self, name: str | None = None,
                    number: str | None = None) -> FetchResult:
        """按商号或法人番号检索日本法人。

        Args:
            name: 可选,商号关键词(日文)。
            number: 可选,13 位法人番号(优先于 name)。

        Returns:
            data 为 {"message", "count", "corporations": [...]}(官方字段原样);
            无匹配时 count=0 仍为成功。

        Raises:
            ValueError: name 与 number 均未提供,或 number 非 13 位数字。
        """
        if number:
            if not (len(number) == 13 and number.isdigit()):
                raise ValueError("法人番号必须为 13 位数字字符串")
            result = await self._get(
                "https://ws.houjin-bangou.nta.go.jp/SMBC/v1/houjinNo",
                params={"number": number, "type": "12"},
            )
        elif name:
            result = await self._get(
                "https://ws.houjin-bangou.nta.go.jp/SMBC/v1/name",
                params={"name": name, "type": "12", "mode": "1"},
            )
        else:
            raise ValueError("name 与 number 至少提供一个")
        if not result.ok or not isinstance(result.data, dict):
            return result
        result.data = {
            "message": result.data.get("message"),
            "count": result.data.get("count", 0),
            "corporations": result.data.get("corporation") or [],
        }
        return result


class FrSireneSource(BaseSource):
    """法国公司检索(DGFiP recherche-entreprises):Sirene/RNE 同源数据。

    官方免 key 聚合端点,覆盖 SIREN/SIRET、法人代表、行业(NAP)、
    规模分类等;INSEE 官方 API 需免费 token,本源为其免 key 等价物。
    """

    name = "fr_sirene"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 15
    rate_period_s = 60  # 官方建议 7 req/s 内,取保守值
    timeout = 30
    cache_ttl_s = 86400.0
    description = "法国公司检索 recherche-entreprises(Sirene/RNE 同源,免 key)"

    async def fetch(self, query: str, page: int = 1, limit: int = 10) -> FetchResult:
        """按关键词检索法国企业。

        Args:
            query: 名称/SIREN/SIRET 关键词。
            page: 页码(1 起)。
            limit: 每页条数(上限 20)。

        Returns:
            data 为 {"total_results", "results": [...], "page"};

        Raises:
            ValueError: query 为空。
        """
        if not query:
            raise ValueError("query 不能为空")
        result = await self._get(
            "https://recherche-entreprises.api.gouv.fr/search",
            params={"q": query, "page": page, "per_page": min(limit, 20)},
        )
        if not result.ok or not isinstance(result.data, dict):
            return result
        results = result.data.get("results") or []
        result.data = {
            "total_results": result.data.get("total_results", len(results)),
            "results": results,
            "page": page,
        }
        return result


class FrBodaccSource(BaseSource):
    """法国 BODACC 法定公告(opendatasoft 免 key 端点)。

    公司设立/变更/破产/诉讼等法定公告流水;作为 INPI RNE 开放
    API 的替代(INPI 官方端点被 Cloudflare 浏览器验证墙拦截)。
    """

    name = "fr_bodacc"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 15
    rate_period_s = 60
    timeout = 30
    cache_ttl_s = 21600.0
    description = "法国 BODACC 法定公告检索(INPI RNE 披露的免 key 替代)"

    async def fetch(self, query: str, limit: int = 20, start: int = 0) -> FetchResult:
        """按关键词检索法国法定公告。

        Args:
            query: 公司名/SIREN/公告关键词。
            limit: 返回条数(上限 100)。
            start: 分页偏移。

        Returns:
            data 为 {"nhits", "records": [...]}(opendatasoft 标准结构裁剪);

        Raises:
            ValueError: query 为空。
        """
        if not query:
            raise ValueError("query 不能为空")
        result = await self._get(
            "https://bodacc-datadila.opendatasoft.com/api/records/1.0/search/",
            params={
                "dataset": "annonces-commerciales",
                "q": query, "rows": min(limit, 100), "start": start,
            },
        )
        if not result.ok or not isinstance(result.data, dict):
            return result
        result.data = {
            "nhits": result.data.get("nhits", 0),
            "records": result.data.get("records") or [],
        }
        return result


class NoBrregSource(BaseSource):
    """挪威 Brønnøysund 注册中心开放 API:企业主体与分支机构检索。

    官方免 key;支持按组织号直查与按名称搜索(实测均 200)。
    """

    name = "no_brreg"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 15
    rate_period_s = 60
    timeout = 30
    cache_ttl_s = 86400.0
    description = "挪威企业注册开放 API(Brønnøysund,组织号/名称检索)"

    async def fetch(self, orgnr: str | None = None,
                    name: str | None = None) -> FetchResult:
        """按组织号或名称检索挪威企业。

        Args:
            orgnr: 可选,9 位组织号码(优先)。
            name: 可选,名称关键词。

        Returns:
            按号查询 data 为企业对象 dict;按名查询 data 为
            {"page", "items": [...], "embedded_total"(若响应含)}。

        Raises:
            ValueError: orgnr 与 name 均未提供。
        """
        if orgnr:
            if not (len(orgnr) == 9 and orgnr.isdigit()):
                raise ValueError("组织号码必须为 9 位数字字符串")
            return await self._get(
                f"https://data.brreg.no/enhetsregisteret/api/enheter/{orgnr}"
            )
        if not name:
            raise ValueError("orgnr 与 name 至少提供一个")
        result = await self._get(
            "https://data.brreg.no/enhetsregisteret/api/enheter",
            params={"navn": name},
        )
        if not result.ok or not isinstance(result.data, dict):
            return result
        embedded = result.data.get("_embedded") or {}
        items = embedded.get("enheter") or []
        result.data = {"page": result.data.get("page"), "items": items}
        return result


class FdicBanksSource(BaseSource):
    """美国 FDIC 银行机构数据库 API:全美银行/储蓄机构画像与历史。"""

    name = "fdic_banks"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 15
    rate_period_s = 60
    timeout = 30
    cache_ttl_s = 86400.0
    description = "美国 FDIC 银行机构数据库(资产/网点/状态/历史)"

    async def fetch(self, name: str | None = None, filters: str | None = None,
                    fields: str = "NAME,CERT,CITY,STALP,ASSET,ACTIVE",
                    limit: int = 20) -> FetchResult:
        """检索美国银行机构。

        Args:
            name: 可选,机构名关键词(走 search=NAME: 前缀匹配)。
            filters: 可选,Elasticsearch 风格过滤式直传(如 "STALP:CA AND ACTIVE:1")。
            fields: 返回字段(逗号分隔)。
            limit: 返回条数。

        Returns:
            data 为 {"total", "institutions": [...], "fields"};

        Raises:
            ValueError: name 与 filters 均未提供。
        """
        if not name and not filters:
            raise ValueError("name 与 filters 至少提供一个")
        params: dict[str, object] = {
            "fields": fields, "format": "json", "limit": limit,
        }
        if name:
            params["search"] = f'NAME:"{name}"'
        if filters:
            params["filters"] = filters
        result = await self._get(
            "https://banks.data.fdic.gov/api/institutions", params=params
        )
        if not result.ok or not isinstance(result.data, dict):
            return result
        rows = result.data.get("data") or []
        institutions = [r.get("data") for r in rows if isinstance(r, dict)]
        result.data = {
            "total": (result.data.get("meta") or {}).get("total", len(institutions)),
            "institutions": institutions,
            "fields": fields,
        }
        return result
