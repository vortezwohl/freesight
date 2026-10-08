"""行业/宏观统计免费免 key 源(2026-09 实测与规格核对)。

- worldbank: 世界银行指标 API(免 key,国家 x 指标时序);
- eurostat: 欧盟统计局 dissemination API(免 key,JSON-stat);
- oecd: OECD SDMX-JSON(免 key,路径含数据集/维度过滤);
- imf: IMF SDMX JSON CompactData(免 key,key 语法定位序列);
- cn_stats: 中国国家统计局数据接口(easyquery;实测本网络 403,
  服务端有反爬,作为尽力而为源保留)。

宏观 API 的共同形态:调用方需要知道数据集/指标代码(SDMX 语义),
SDK 只负责通道与响应原样透传,不做指标目录发现。
"""

from __future__ import annotations

import json

from freesignt.core.base import BaseSource
from freesignt.core.models import FetchResult, SourceCategory


class WorldBankSource(BaseSource):
    """世界银行指标 API:全球发展指标时序(GDP/人口/贸易等 16000+)。"""

    name = "worldbank"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 15
    rate_period_s = 60
    cache_ttl_s = 86400.0
    description = "世界银行指标 API(国家 x 指标时序,免 key)"

    async def fetch(self, indicator: str = "NY.GDP.MKTP.CD",
                    country: str = "all", date: str | None = None,
                    limit: int = 100) -> FetchResult:
        """查询世界银行指标数据。

        Args:
            indicator: 指标代码(如 NY.GDP.MKTP.CD 名义 GDP;
                SP.POP.TOTL 人口;目录见 api.worldbank.org/v2/indicator)。
            country: 国家 ISO 码或 all(可分号多国,如 "US;CN;JP")。
            date: 可选,时间窗(如 "2020:2025" 或单年 "2025")。
            limit: 每页条数(服务端默认 50,上限约 32512)。

        Returns:
            data 为 {"meta", "data": [...]}(服务端 [meta, rows] 双元结构);

        """
        params: dict[str, object] = {"format": "json", "per_page": limit}
        if date:
            params["date"] = date
        result = await self._get(
            f"https://api.worldbank.org/v2/country/{country}/indicator/{indicator}",
            params=params,
        )
        if not result.ok or not isinstance(result.data, list) or len(result.data) != 2:
            return result
        result.data = {"meta": result.data[0], "data": result.data[1]}
        return result


class EurostatSource(BaseSource):
    """欧盟统计局 API:欧元区官方统计(JSON-stat 2.0 输出)。"""

    name = "eurostat"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 15
    rate_period_s = 60
    timeout = 30
    cache_ttl_s = 86400.0
    description = "欧盟统计局 API(数据集 x 维度过滤,JSON-stat 输出)"

    async def fetch(self, dataset: str,
                    filters: dict[str, str] | None = None,
                    limit: int = 100) -> FetchResult:
        """查询一个 Eurostat 数据集。

        Args:
            dataset: 数据集代码(如 prc_hicp_indr 通胀;t20205010 人口;
                目录见 ec.europa.eu/eurostat/databrowser)。
            filters: 可选,维度过滤(如 {"geo": "DE", "time": "2024"}),
                值支持逗号多选(服务端即或语义)。
            limit: lastUpdates 之外的条目截断(响应整体偏大时保护内存)。

        Returns:
            data 为 JSON-stat 2.0 对象原样(class/dimension/value 结构);
            filters 值为列表时逗号拼接。
        """
        params: dict[str, object] = {"format": "JSON", "lang": "EN"}
        for key, value in (filters or {}).items():
            params[key] = ",".join(value) if isinstance(value, list) else value
        return await self._get(
            f"https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/{dataset}",
            params=params,
        )


class OecdSource(BaseSource):
    """OECD SDMX-JSON API:经合组织官方统计(数据集/维度/时间窗)。"""

    name = "oecd"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 15
    rate_period_s = 60
    timeout = 30
    cache_ttl_s = 86400.0
    description = "OECD SDMX-JSON 统计 API(数据集 + 维度过滤,免 key)"

    async def fetch(self, dataset: str, filter: str = "all",
                    start: str | None = None, end: str | None = None) -> FetchResult:
        """查询一个 OECD 数据集。

        Args:
            dataset: 数据集标识(如 "OECD.SDD.NAD,DSD_NAMAIN1@DF_QNA"。
                注意维度数随数据集演进变化(DF_QNA 现为 13 维),维度键
                应以当前 DSD 为准,如 "Q..BEL...B1GQ......."。
            filter: 维度过滤 SDMX 语法,点位数须与数据集维度数一致,
                空位表示不过滤,all 表示整体不过滤。
            start: 可选,起始期(如 "2023-Q1")。
            end: 可选,截止期。

        Returns:
            data 为 SDMX-JSON 对象原样(data/dataSets 结构);
            start/end 经 startPeriod/endPeriod 查询参数下推,可单独使用。
            时间窗不得拼入路径:SDMX 2.1 路径中 key 之后是 provider 段,
            误占会导致 403 Invalid structure(2026-10-08 实测)。
        """
        params: dict[str, object] = {
            "format": "jsondata",
            "dimensionAtObservation": "AllDimensions",
        }
        if start:
            params["startPeriod"] = start
        if end:
            params["endPeriod"] = end
        return await self._get(
            f"https://sdmx.oecd.org/public/rest/data/{dataset}/{filter}",
            params=params,
        )


class ImfSource(BaseSource):
    """IMF SDMX JSON API:国际货币基金组织统计(CompactData 输出)。

    注意:2026-09 实测部分网络环境 TLS 握手异常(网络层可达性
    问题,接口本身公开免 key),部署前建议先行验证。
    """

    name = "imf"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 15
    rate_period_s = 60
    timeout = 30
    cache_ttl_s = 86400.0
    description = "IMF SDMX 统计 API(CompactData,IFS/宝典类数据集)"

    async def fetch(self, dataset: str = "IFS", key: str = "A.US.NGDP",
                    start: str | None = None, end: str | None = None) -> FetchResult:
        """查询一个 IMF 数据序列。

        Args:
            dataset: 数据集代码(IFS 国际金融/ WEO 世界经济展望等)。
            key: 序列定位键(频率.国家.指标,如 A.US.NGDP;
                点位数量随数据集维度不同)。
            start: 可选,起始期(如 "2020")。
            end: 可选,截止期。

        Returns:
            data 为 CompactData JSON 原样(DataSet/Series 结构);
        """
        params: dict[str, object] = {}
        if start:
            params["startPeriod"] = start
        if end:
            params["endPeriod"] = end
        return await self._get(
            f"https://dataservices.imf.org/REST/SDMX_JSON.svc/CompactData/{dataset}/{key}",
            params=params,
        )


class CnStatsSource(BaseSource):
    """中国国家统计局数据接口(easyquery):国家数据库指标查询。

    注意:该服务有反爬策略,2026-09 实测本网络环境直接请求 403
    (浏览器 UA 亦被拦,推测需要会话 cookie);本源按公开接口
    规格实现,作为尽力而为通道保留,不可用时应以官方网页为准。
    """

    name = "cn_stats"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 6
    rate_period_s = 60
    timeout = 30
    cache_ttl_s = 86400.0
    description = "中国国家统计局数据接口(有反爬,尽力而为通道)"

    async def fetch(self, zb_code: str = "A0201", dbcode: str = "hgnd") -> FetchResult:
        """查询一个国家统计指标序列。

        Args:
            zb_code: 指标代码(如 A0201 GDP 总值;A0202 GDP 增速;
                目录可在官方数据浏览器逐级获取)。
            dbcode: 数据库代码(hgnd 年度 / hgyd 月度 / hgjd 季度)。

        Returns:
            data 为官方 returndata 结构原样(wdnodes/zbdata/strdata);
            被 403 拦截时 ok=False。
        """
        dfwds = json.dumps([{"wdcode": "zb", "valuecode": zb_code}])
        return await self._get(
            "https://data.stats.gov.cn/easyquery.htm",
            params={
                "m": "QueryData", "dbcode": dbcode, "rowcode": "zb",
                "colcode": "sj", "wds": "[]", "dfwds": dfwds, "k1": "1",
            },
        )
