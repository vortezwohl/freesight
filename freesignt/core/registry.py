"""数据源全局注册表:按名称与类别检索已定义的源类。

注册机制由 BaseSource.__init_subclass__ 自动完成(见 base.py),
本模块只维护映射与查询接口,不感知任何具体源。
"""

from __future__ import annotations

from freesignt.core.base import BaseSource
from freesignt.core.errors import SourceNotFoundError
from freesignt.core.models import SourceCategory, SourceInfo

# 源名称 -> 源类 的全局映射;同名重复注册视为编码错误,立即抛出。
_REGISTRY: dict[str, type[BaseSource]] = {}


def register(cls: type[BaseSource]) -> None:
    """注册一个数据源类;名称冲突时抛出异常以便在导入期暴露问题。

    Args:
        cls: BaseSource 子类。

    Raises:
        ValueError: 名称已被其他源占用。
    """
    existing = _REGISTRY.get(cls.name)
    if existing is not None and existing is not cls:
        raise ValueError(f"数据源名称冲突: {cls.name} 已由 {existing.__module__} 注册")
    _REGISTRY[cls.name] = cls


def get(name: str) -> type[BaseSource]:
    """按名称取源类。

    Args:
        name: 源唯一名称。

    Returns:
        对应的 BaseSource 子类。

    Raises:
        SourceNotFoundError: 名称不存在(附带可用源列表)。
    """
    if name not in _REGISTRY:
        raise SourceNotFoundError(name, list(_REGISTRY))
    return _REGISTRY[name]


def by_category(category: SourceCategory) -> dict[str, type[BaseSource]]:
    """按类别列出全部源类。

    Args:
        category: 目标分类。

    Returns:
        该分类下 {源名称: 源类} 映射。
    """
    return {k: v for k, v in _REGISTRY.items() if v.category == category}


def all_sources() -> dict[str, type[BaseSource]]:
    """返回全部已注册源。

    Returns:
        {源名称: 源类} 映射副本。
    """
    return dict(_REGISTRY)


def names() -> list[str]:
    """全部已注册源名称(注册序)。

    Returns:
        源名称列表。
    """
    return list(_REGISTRY)


def default_search_sources() -> list[type[BaseSource]]:
    """统一搜索默认扇出集合(search_default=True 的源,注册序)。

    Returns:
        源类列表。
    """
    return [cls for cls in _REGISTRY.values() if cls.search_default]


def catalogue() -> list[SourceInfo]:
    """全部源的元信息目录(人类查阅与 agent 发现工具共用)。

    Returns:
        SourceInfo 列表(注册序)。
    """
    return [cls.info() for cls in _REGISTRY.values()]
