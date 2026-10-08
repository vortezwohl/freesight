"""产品/竞品社区免费免 key 源(2026-09 实测)。

- discourse: 任意 Discourse 论坛的公开 JSON 端点(latest/top/
  分类/主题),海量开发者与产品社区的结构化入口;
- fdroid: F-Droid 安卓开源应用目录的包详情 API(实测 200)。
"""

from __future__ import annotations

from urllib.parse import quote

from freesignt.core.base import BaseSource
from freesignt.core.models import FetchResult, SourceCategory


class DiscourseSource(BaseSource):
    """Discourse 论坛公开 JSON 端点:latest/top/分类/主题通用读取。

    绝大多数 Discourse 实例对匿名请求开放 .json 端点(管理员可关),
    是替代网页爬取的结构化通道;目标论坛由调用方经 base_url 指定。
    """

    name = "discourse"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 15
    rate_period_s = 60
    cache_ttl_s = 600.0
    description = "Discourse 论坛公开 JSON 读取(latest/top/分类,任意实例)"

    async def fetch(self, base_url: str, resource: str = "latest",
                    page: int = 1) -> FetchResult:
        """读取一个 Discourse 论坛的资源清单。

        Args:
            base_url: 论坛基址(如 https://meta.discourse.org)。
            resource: 资源路径(如 latest/top/c/feedback;
                分类可用 c/{slug} 形式,服务端 301 到带 id 路径)。
            page: 页码(1 起)。

        Returns:
            data 为 Discourse 响应原样(含 users 与 topic_list/topics);

        Raises:
            ValueError: base_url 形态非法。
        """
        if not base_url.startswith(("http://", "https://")):
            raise ValueError(f"base_url 必须以 http(s):// 开头: {base_url!r}")
        path = resource.strip("/").removesuffix(".json")
        url = f"{base_url.rstrip('/')}/{quote(path, safe='/')}.json"
        return await self._get(url, params={"page": page})


class FDroidSource(BaseSource):
    """F-Droid 包详情 API:安卓开源应用的版本与签名元数据。"""

    name = "fdroid"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 15
    rate_period_s = 60
    cache_ttl_s = 21600.0
    description = "F-Droid 安卓开源应用包详情(版本/签名/权限元数据)"

    async def fetch(self, package: str) -> FetchResult:
        """查询单个 F-Droid 应用的包元数据。

        Args:
            package: 应用包名(如 org.fdroid.fdroid)。

        Returns:
            data 为 {"packageName", "suggestedVersionCode", "packages": [...]}
            形态的官方响应原样;

        Raises:
            ValueError: package 为空。
        """
        if not package:
            raise ValueError("package 不能为空")
        return await self._get(f"https://f-droid.org/api/v1/packages/{package}")
