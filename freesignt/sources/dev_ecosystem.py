"""开发者生态免费免 key 源:npm/PyPI/pypistats/ecosyste.ms/WordPress/HuggingFace。

实测基准(2026-09-09,响应头直读):
- ecosyste.ms 匿名 5000/h(GitHub 的 83 倍补充通道);
- Hugging Face 匿名 500 次/5min(RateLimit-Policy 头实测);
- npm/PyPI/pypistats/WordPress 无限速头,WordPress 20 并发实测 QPS 37。
"""

from __future__ import annotations

from freesignt.core.base import BaseSource
from freesignt.core.models import FetchResult, SourceCategory


class NpmRegistrySource(BaseSource):
    """npm registry:包搜索与下载量(开源需求量化)。"""

    name = "npm_registry"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 60
    rate_period_s = 60
    cache_ttl_s = 1800.0
    description = "npm 包搜索(月下载量/依赖数)"

    async def fetch(self, text: str, size: int = 20) -> FetchResult:
        """按关键词搜索 npm 包。

        Args:
            text: 搜索关键词。
            size: 返回条数(上限 250)。

        Returns:
            data 为 {"objects": [{"package": {...}, "score": {...}}, ...]}。
        """
        return await self._get(
            "https://registry.npmjs.org/-/v1/search",
            params={"text": text, "size": size},
        )


class PyPiSource(BaseSource):
    """PyPI:包元数据 JSON(版本/作者/依赖)。"""

    name = "pypi_metadata"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 60
    rate_period_s = 60
    cache_ttl_s = 3600.0
    description = "PyPI 包元数据(版本/依赖/分类)"

    async def fetch(self, package: str) -> FetchResult:
        """拉取单个 PyPI 包的完整元数据。

        Args:
            package: 包名(如 requests)。

        Returns:
            data 为 {"info": {...}, "releases": {...}}。
        """
        return await self._get(f"https://pypi.org/pypi/{package}/json")


class PyPiStatsSource(BaseSource):
    """pypistats:PyPI 包最近下载量。"""

    name = "pypi_downloads"
    category = SourceCategory.FREE_NOKEY
    # pypistats 存在未文档化限速(smoke 实测 429 RATE LIMIT EXCEEDED),
    # 取保守 10/min;引擎的自适应冷却会在真实 429 时进一步保护。
    rate_limit = 10
    rate_period_s = 60
    cache_ttl_s = 21600.0
    description = "PyPI 包最近 30 天下载量"

    async def fetch(self, package: str) -> FetchResult:
        """查询某包最近下载量。

        Args:
            package: 包名。

        Returns:
            data 为 {"data": {"last_month": int, ...}}。
        """
        return await self._get(f"https://pypistats.org/api/packages/{package}/recent")


class EcosysteMsSource(BaseSource):
    """ecosyste.ms:GitHub 等开源生态的镜像元数据(匿名 5000/h,远高于 GitHub 匿名档)。"""

    name = "ecosyste_ms"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 100
    rate_period_s = 60  # 匿名实测 5000/h,取 100/min 保守值
    cache_ttl_s = 3600.0
    description = "开源生态镜像元数据(仓库/依赖),GitHub 匿名批量的替代通道"

    async def fetch(self, host: str = "GitHub", owner: str = "", repo: str = "") -> FetchResult:
        """拉取仓库级镜像元数据。

        Args:
            host: 代码托管平台(GitHub/GitLab)。
            owner: 仓库所有者。
            repo: 仓库名。

        Returns:
            data 为仓库元数据 dict(含描述/star/fork/license 等)。

        Raises:
            ValueError: owner/repo 缺失。
        """
        if not (owner and repo):
            raise ValueError("需要提供 owner 与 repo")
        return await self._get(
            f"https://repos.ecosyste.ms/api/v1/hosts/{host}/repositories/{owner}/{repo}"
        )


class WordPressPluginsSource(BaseSource):
    """WordPress 插件目录 API:插件装机量(生态位研究的完美数据)。"""

    name = "wordpress_plugins"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 30
    rate_period_s = 60
    cache_ttl_s = 21600.0
    schema_overrides = {
        "browse": {"enum": ["popular", "new", "updated", "top-rated"]},
    }
    description = "WordPress 插件目录(6.7 万+ 插件的安装量/评分,实测约 3.4 万页)"

    async def fetch(
        self,
        page: int = 1,
        per_page: int = 50,
        browse: str = "popular",
    ) -> FetchResult:
        """分页拉取插件列表。

        Args:
            page: 页码(1 起)。
            per_page: 每页条数(上限 100)。
            browse: 排序维度 popular/new/updated/top-rated。

        Returns:
            data 为 {"info": {"results": 总数, "pages": 总页数}, "plugins": [...]}。
        """
        return await self._get(
            "https://api.wordpress.org/plugins/info/1.2/",
            params={"action": "query_plugins", "request[page]": page,
                    "request[per_page]": per_page, "request[browse]": browse},
        )


class HuggingFaceSource(BaseSource):
    """Hugging Face Hub API:模型/数据集/Spaces 榜单(未发布 AI 产品的试验场)。

    匿名限速 500 次/5min(RateLimit-Policy 头实测);spaces 榜单按
    点赞排序可发现热门 demo,即 AI 产品需求先行指标。
    """

    name = "huggingface_hub"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 100
    rate_period_s = 60  # 实测窗口 500/5min ≈ 100/min
    cache_ttl_s = 1800.0
    schema_overrides = {
        "kind": {"enum": ["models", "datasets", "spaces"]},
    }
    description = "HuggingFace 模型/数据集/Spaces 检索与趋势榜(AI 产品雷达)"

    async def fetch(
        self,
        kind: str = "models",
        search: str = "",
        sort: str = "",
        limit: int = 20,
    ) -> FetchResult:
        """检索 Hub 资源。

        Args:
            kind: 资源类型 models/datasets/spaces。
            search: 可选关键词。
            sort: 可选排序字段(spaces 趋势用 likes;配 direction 由内部处理)。
            limit: 返回条数。

        Returns:
            data 为资源列表(list[dict])。

        Raises:
            ValueError: kind 不合法。
        """
        if kind not in ("models", "datasets", "spaces"):
            raise ValueError(f"不支持的资源类型: {kind}")
        params: dict[str, object] = {"limit": limit}
        if search:
            params["search"] = search
        if sort:
            params["sort"] = sort
            params["direction"] = -1  # 降序(Hub API 约定数值排序需配方向)
        return await self._get(f"https://huggingface.co/api/{kind}", params=params)
