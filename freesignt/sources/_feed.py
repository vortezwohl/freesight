"""RSS/Atom 轻量解析工具(sources 层内部共享,不属于数据源)。

YouTube/Google News/RSSHub/Reddit RSS/arXiv Atom 等多个"内容流"源的
共用底座:用标准库 xml.etree 解析两类主流 feed 形态:
- RSS 2.0(rss/channel/item): Google News、YouTube、RSSHub、Reddit;
- Atom(feed/entry): arXiv API 等。

设计取向与 SDK 整体一致——只做必要的结构压平(title/link/published/
description 四字段),description 内嵌的 HTML 原样保留由调用方处理;
解析失败返回 None,由调用方决定是否视为源失败。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any


def parse_feed(text: str) -> dict[str, Any] | None:
    """把 RSS 2.0 / Atom 文档解析为统一条目结构。

    Args:
        text: feed 原始 XML 文本。

    Returns:
        成功时返回 {"title", "link", "updated", "items", "count"};
        items 元素为 {"title", "link", "published", "description"};
        输入不是合法 XML 或不是可识别的 feed 结构时返回 None。
    """
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None

    # 去命名空间后的根标签名,用于区分 RSS 2.0 与 Atom;
    # 子元素查找用 {*} 通配,兼容带默认命名空间的两类文档。
    tag = root.tag.rsplit("}", 1)[-1]
    if tag == "rss":
        channel = root.find("{*}channel")
        if channel is None:
            return None
        items = []
        for it in channel.findall("{*}item"):
            items.append({
                "title": (it.findtext("{*}title") or "").strip(),
                "link": (it.findtext("{*}link") or "").strip(),
                "published": (it.findtext("{*}pubDate") or "").strip(),
                "description": (it.findtext("{*}description") or "").strip(),
            })
        return {
            "title": (channel.findtext("{*}title") or "").strip(),
            "link": (channel.findtext("{*}link") or "").strip(),
            "updated": (channel.findtext("{*}lastBuildDate")
                        or channel.findtext("{*}pubDate") or "").strip(),
            "items": items,
            "count": len(items),
        }
    if tag == "feed":
        # Atom:entry 的 link 是带 rel/href 属性的空元素。
        def _atom_link(node: ET.Element) -> str:
            best = ""
            for link in node.findall("{*}link"):
                href = link.get("href", "")
                if href and (link.get("rel") in (None, "alternate") or not best):
                    best = href
            return best.strip()

        items = []
        for entry in root.findall("{*}entry"):
            items.append({
                "title": (entry.findtext("{*}title") or "").strip(),
                "link": _atom_link(entry),
                "published": (entry.findtext("{*}published")
                              or entry.findtext("{*}updated") or "").strip(),
                "description": (entry.findtext("{*}summary")
                                or entry.findtext("{*}content") or "").strip(),
            })
        return {
            "title": (root.findtext("{*}title") or "").strip(),
            "link": _atom_link(root),
            "updated": (root.findtext("{*}updated") or "").strip(),
            "items": items,
            "count": len(items),
        }
    return None
