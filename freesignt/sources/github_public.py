"""GitHub 匿名 REST 源(免 key 档):仓库搜索与单仓详情。

实测基准(2026-09-09,响应头直读):
- 匿名档 core 60 次/小时、search 10 次/分钟;
- 本模块按匿名档最严约束节流;生产应注册免费 token 后替换为
  5000/h(届时调高 rate_limit 或迁入 free_key 分包)。
"""

from __future__ import annotations

from freesignt.core.base import BaseSource
from freesignt.core.models import FetchResult, SourceCategory


class GithubPublicSource(BaseSource):
    """GitHub 匿名 REST:仓库搜索/单仓元数据(星数/语言/更新时间)。

    注意匿名配额极小(搜索 10/min、core 60/h),只适合探测性调用;
    批量采集请走 ecosyste_ms 源(匿名 5000/h 的 GitHub 镜像)。
    """

    name = "github_public"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 8
    rate_period_s = 60
    cache_ttl_s = 1800.0
    description = "GitHub 匿名 REST(搜索 10/min,core 60/h;批量请用 ecosyste_ms)"

    async def fetch(
        self,
        repo: str | None = None,
        query: str | None = None,
        per_page: int = 10,
    ) -> FetchResult:
        """拉取单仓详情或按 query 搜索仓库。

        Args:
            repo: 可选,"owner/name" 形式的仓库全名(优先)。
            query: 可选,搜索语法(如 "competitive intelligence language:python")。
            per_page: 搜索结果每页条数(上限 100)。

        Returns:
            data 为单仓 dict 或 {"total_count", "items": [...]}。

        Raises:
            ValueError: repo 与 query 至少提供一个。
        """
        if repo:
            return await self._get(f"https://api.github.com/repos/{repo}")
        if not query:
            raise ValueError("repo 与 query 至少提供一个")
        return await self._get(
            "https://api.github.com/search/repositories",
            params={"q": query, "per_page": per_page},
        )
