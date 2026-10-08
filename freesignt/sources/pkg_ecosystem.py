"""包/依赖生态免费免 key 源(2026-09 实测)。

补齐 npm/PyPI 之外的包管理生态,用于竞品底层技术栈指纹:
- rubygems: Ruby 包(gem 详情/搜索);
- crates: Rust crate 详情/搜索(crates.io 要求描述性 UA);
- packagist: PHP 包详情/搜索;
- nuget: .NET 包版本清单(flatcontainer)与官方搜索服务;
- dockerhub: Docker Hub 镜像仓库详情/搜索(匿名有 IP 限额);
- repology: 跨 250+ 发行版/仓库的包版本聚合(本 SDK 网络
  实测 TLS 异常,按官方规格保留)。
"""

from __future__ import annotations

from freesignt.core.base import BaseSource
from freesignt.core.models import FetchResult, SourceCategory


class RubyGemsSource(BaseSource):
    """RubyGems 官方 API:gem 元数据与关键词搜索。"""

    name = "rubygems"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 15
    rate_period_s = 60
    cache_ttl_s = 3600.0
    description = "RubyGems 包详情/搜索(下载量/版本/依赖)"

    async def fetch(self, name: str | None = None,
                    search: str | None = None) -> FetchResult:
        """查询 Ruby gem 详情或搜索。

        Args:
            name: 可选,gem 名(优先;返回单包元数据)。
            search: 可选,搜索词(name 未提供时生效)。

        Returns:
            gem 模式 data 为单包 dict;搜索模式 data 为包数组裁剪;

        Raises:
            ValueError: name 与 search 均未提供。
        """
        if name:
            return await self._get(f"https://rubygems.org/api/v1/gems/{name}")
        if not search:
            raise ValueError("name 与 search 至少提供一个")
        return await self._get(
            "https://rubygems.org/api/v1/search.json", params={"query": search}
        )


class CratesSource(BaseSource):
    """crates.io API:Rust crate 元数据/版本与搜索(要求描述性 UA)。"""

    name = "crates"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 15
    rate_period_s = 60
    cache_ttl_s = 3600.0
    description = "crates.io Rust 包详情/搜索(版本/下载量)"

    async def fetch(self, crate: str | None = None, search: str | None = None,
                    limit: int = 10) -> FetchResult:
        """查询 crate 详情或搜索。

        Args:
            crate: 可选,crate 名(优先)。
            search: 可选,搜索词。
            limit: 搜索返回条数。

        Returns:
            crate 模式 data 为 {"crate", "versions"} 官方结构原样;
            搜索模式 data 为 {"crates": [...], "total"?;

        Raises:
            ValueError: crate 与 search 均未提供。
        """
        if crate:
            return await self._get(f"https://crates.io/api/v1/crates/{crate}")
        if not search:
            raise ValueError("crate 与 search 至少提供一个")
        result = await self._get(
            "https://crates.io/api/v1/crates",
            params={"q": search, "per_page": limit},
        )
        if not result.ok or not isinstance(result.data, dict):
            return result
        crates = result.data.get("crates") or []
        result.data = {"crates": crates, "total": result.data.get("meta", {}).get("total")}
        return result


class PackagistSource(BaseSource):
    """Packagist API:PHP 包元数据与搜索。"""

    name = "packagist"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 15
    rate_period_s = 60
    cache_ttl_s = 3600.0
    description = "Packagist PHP 包详情/搜索(版本/下载量/维护者)"

    async def fetch(self, package: str | None = None, search: str | None = None,
                    limit: int = 10) -> FetchResult:
        """查询 PHP 包详情或搜索。

        Args:
            package: 可选,vendor/name 全名(优先)。
            search: 可选,搜索词。
            limit: 搜索返回条数。

        Returns:
            package 模式 data 为 {"package", "versions"} 官方结构;
            搜索模式 data 为 {"results": [...], "total"};

        Raises:
            ValueError: package 与 search 均未提供。
        """
        if package:
            return await self._get(f"https://packagist.org/packages/{package}.json")
        if not search:
            raise ValueError("package 与 search 至少提供一个")
        result = await self._get(
            "https://packagist.org/search.json",
            params={"q": search, "per_page": limit},
        )
        if not result.ok or not isinstance(result.data, dict):
            return result
        results = result.data.get("results") or []
        result.data = {"results": results, "total": len(results)}
        return result


class NugetSource(BaseSource):
    """NuGet 服务:.NET 包版本清单(flatcontainer)与官方搜索。"""

    name = "nuget"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 15
    rate_period_s = 60
    cache_ttl_s = 3600.0
    description = "NuGet .NET 包版本清单/搜索(flatcontainer + 搜索服务)"

    async def fetch(self, package_id: str | None = None, search: str | None = None,
                    take: int = 10) -> FetchResult:
        """查询 .NET 包版本清单或搜索。

        Args:
            package_id: 可选,包 ID(优先;返回全部版本号数组)。
            search: 可选,搜索词。
            take: 搜索返回条数。

        Returns:
            package_id 模式 data 为 {"id", "versions": [...]};
            搜索模式 data 为 {"total", "results": [...]}(官方搜索结构);

        Raises:
            ValueError: package_id 与 search 均未提供。
        """
        if package_id:
            return await self._get(
                f"https://api.nuget.org/v3-flatcontainer/{package_id.lower()}/index.json"
            )
        if not search:
            raise ValueError("package_id 与 search 至少提供一个")
        result = await self._get(
            "https://azuresearch-usnc.nuget.org/query",
            params={"q": search, "take": take},
        )
        if not result.ok or not isinstance(result.data, dict):
            return result
        results = result.data.get("data") or []
        result.data = {"total": result.data.get("totalHits", len(results)), "results": results}
        return result


class DockerHubSource(BaseSource):
    """Docker Hub v2 API:公开镜像仓库详情与搜索(匿名限额)。"""

    name = "dockerhub"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 10
    rate_period_s = 60  # 匿名档有 IP 级限额,取保守值
    timeout = 30
    cache_ttl_s = 3600.0
    description = "Docker Hub 镜像仓库详情/搜索(拉取量/星标,匿名限额)"

    async def fetch(self, repo: str | None = None, search: str | None = None,
                    limit: int = 10) -> FetchResult:
        """查询镜像仓库详情或搜索。

        Args:
            repo: 可选,仓库名(官方镜像需 library/ 前缀,如 library/nginx)。
            search: 可选,搜索词。
            limit: 搜索返回条数。

        Returns:
            repo 模式 data 为仓库对象(pull_count/star_count 等);
            搜索模式 data 为 {"results": [...], "count"};

        Raises:
            ValueError: repo 与 search 均未提供。
        """
        if repo:
            return await self._get(f"https://hub.docker.com/v2/repositories/{repo.strip('/')}/")
        if not search:
            raise ValueError("repo 与 search 至少提供一个")
        result = await self._get(
            "https://hub.docker.com/v2/search/repositories/",
            params={"query": search, "page_size": limit},
        )
        if not result.ok or not isinstance(result.data, dict):
            return result
        results = result.data.get("results") or []
        result.data = {"results": results, "count": result.data.get("count", len(results))}
        return result


class RepologySource(BaseSource):
    """Repology API:一个软件项目在 250+ 仓库/发行版中的版本聚合。

    注意:repology.org 官方 API 免 key 公开,但 2026-09 本 SDK
    所在网络实测 TLS 握手异常(网络层可达性问题),按官方规格保留。
    """

    name = "repology"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 6
    rate_period_s = 60  # 官方建议低频
    timeout = 30
    cache_ttl_s = 21600.0
    description = "Repology 跨发行版包版本聚合(部分网络环境不可达)"

    async def fetch(self, project: str) -> FetchResult:
        """查询单个软件项目的跨仓库版本聚合。

        Args:
            project: 项目标识(如 firefox / python)。

        Returns:
            data 为仓库条目数组裁剪 [{"repo", "version", "status"...}];

        Raises:
            ValueError: project 为空。
        """
        if not project:
            raise ValueError("project 不能为空")
        result = await self._get(f"https://repology.org/api/v1/project/{project}")
        if not result.ok or not isinstance(result.data, list):
            return result
        entries = [
            {
                "repo": e.get("repo"), "version": e.get("version"),
                "status": e.get("status"), "visiblename": e.get("visiblename"),
            }
            for e in result.data if isinstance(e, dict)
        ]
        result.data = {"entries": entries, "count": len(entries)}
        return result
