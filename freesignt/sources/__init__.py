"""免费免 key 数据源分包:导入本包即完成该分类全部源的自动注册。

覆盖 74 个源(33 个为 2026-09-09/20 实测验证;41 个为 2026-09-20
新增,其中端点形态逐一直连探测,个别源受网络环境限制按官方规格保留;
2026-10-08 全源复测移除 uspto_trademark——上游 IBD API 迁至需 key 网关):
itunes(3)/hackernews(2)/github_public(1)/dev_ecosystem(6)/jobs(2)/
social(3)/gov_registry(3)/steam(2)/misc(2)/infra_intel(7)/threat_intel(2)/
search_engines(8)/web_fetch(4)/corp_registry(5)/venture(3)/macro_stats(5)/
product_community(2)/social_feed(5)/academic(3)/pkg_ecosystem(6)。
"""

from freesignt.sources import (  # noqa: F401
    academic,
    corp_registry,
    dev_ecosystem,
    github_public,
    gov_registry,
    hackernews,
    infra_intel,
    itunes,
    jobs,
    macro_stats,
    misc,
    pkg_ecosystem,
    product_community,
    search_engines,
    social,
    social_feed,
    steam,
    threat_intel,
    venture,
    web_fetch,
)
