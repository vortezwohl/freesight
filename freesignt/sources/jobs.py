"""招聘信号免费免 key 源:Greenhouse 与 Lever 公开招聘板 JSON。

实测基准(2026-09-09):
- Greenhouse(Anthropic 板)2.4s,Lever(Palantir 板)6.5s,均 200;
- 两家均无限速头;slug 不存在返回 404,上层按"未知公司"处理。
- JD 中的技术栈/岗位结构是 stealth 公司产品方向的最结构化免费信号。
"""

from __future__ import annotations

from typing import Any

from freesignt.core.base import BaseSource
from freesignt.core.models import FetchResult, Hit, SourceCategory


class GreenhouseJobsSource(BaseSource):
    """Greenhouse 公开招聘板:按公司 slug 拉取全部在招职位。"""

    name = "greenhouse_jobs"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 30
    rate_period_s = 60
    timeout = 30
    cache_ttl_s = 7200.0
    search_kwarg = "company"
    description = "Greenhouse ATS 公开招聘板(职位/地点/部门/更新时间)"

    async def fetch(self, company: str) -> FetchResult:
        """拉取某公司 Greenhouse 板的职位列表。

        Args:
            company: 公司 slug(Greenhouse 板标识,如 anthropic;非公司全名)。

        Returns:
            data 为 {"jobs": [{"title","location","absolute_url","updated_at"}, ...]}。
        """
        return await self._get(f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs")

    def to_hits(self, data: Any, params: dict[str, Any] | None = None) -> list[Hit]:
        """把职位列表归一化为 Hit 列表。"""
        if not isinstance(data, dict):
            return []
        hits = []
        for job in data.get("jobs", []):
            if not isinstance(job, dict):
                continue
            departments = [
                d.get("name", "")
                for d in job.get("departments", [])
                if isinstance(d, dict)
            ]
            hits.append(
                Hit(
                    source=self.name,
                    title=job.get("title", ""),
                    url=job.get("absolute_url", ""),
                    snippet=(job.get("location") or {}).get("name", ""),
                    extra={
                        "updated_at": job.get("updated_at"),
                        "departments": departments,
                    },
                    raw=job,
                )
            )
        return hits


class LeverJobsSource(BaseSource):
    """Lever 公开招聘板:按公司 slug 拉取全部在招职位。"""

    name = "lever_jobs"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 30
    rate_period_s = 60
    timeout = 30
    cache_ttl_s = 7200.0
    search_kwarg = "company"
    description = "Lever ATS 公开招聘板(职位/地点/团队/远程标记)"

    async def fetch(self, company: str) -> FetchResult:
        """拉取某公司 Lever 板的职位列表。

        Args:
            company: 公司 slug(如 palantir;不存在时返回 404)。

        Returns:
            data 为职位列表(list[dict]),失败时 data 为错误结构。
        """
        url = f"https://api.lever.co/v0/postings/{company}"
        return await self._get(url, params={"mode": "json"})

    def to_hits(self, data: Any, params: dict[str, Any] | None = None) -> list[Hit]:
        """把职位列表归一化为 Hit 列表。"""
        if not isinstance(data, list):
            return []
        hits = []
        for posting in data:
            if not isinstance(posting, dict):
                continue
            categories = posting.get("categories", {}) or {}
            hits.append(
                Hit(
                    source=self.name,
                    title=posting.get("text", ""),
                    url=posting.get("hostedUrl", ""),
                    snippet=" · ".join(
                        part
                        for part in (
                            categories.get("location"),
                            categories.get("team"),
                            categories.get("workplaceType"),
                        )
                        if part
                    ),
                    extra={
                        "team": categories.get("team"),
                        "commitment": categories.get("commitment"),
                        "workplace_type": categories.get("workplaceType"),
                        "created_at": posting.get("createdAt"),
                    },
                    raw=posting,
                )
            )
        return hits
