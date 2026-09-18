"""免费免 key 数据源分包:导入本包即完成该分类全部源的自动注册。

覆盖 25 个实测可用源(2026-09-09 验证):
itunes(3)/hackernews(2)/github_public(1)/dev_ecosystem(6)/jobs(2)/
social(3)/gov_registry(4)/steam(2)/misc(2)。
"""

from freesignt.sources import (  # noqa: F401
    dev_ecosystem,
    github_public,
    gov_registry,
    hackernews,
    itunes,
    jobs,
    misc,
    social,
    steam,
)
