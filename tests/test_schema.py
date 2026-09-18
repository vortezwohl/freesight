"""Schema 派生测试:docstring 解析、注解映射、overrides 合并。"""

from __future__ import annotations

from typing import Literal, Optional

from freesignt.core.schema import derive_input_schema, parse_google_args


def test_parse_google_args_basic_and_multiline() -> None:
    """Args 段解析:单行、多行续写、段落截止。"""
    doc = """摘要行。

    Args:
        term: 搜索关键词
            (支持中英文)。
        limit: 条数上限。

    Returns:
        结果。
    """
    args = parse_google_args(doc)
    assert args["term"] == "搜索关键词 (支持中英文)。"
    assert args["limit"] == "条数上限。"


def test_parse_google_args_empty() -> None:
    """空 docstring 返回空映射。"""
    assert parse_google_args(None) == {}
    assert parse_google_args("") == {}


def test_derive_schema_types_and_defaults() -> None:
    """类型注解映射与默认值/必填推导。"""

    def sample(
        self,
        term: str,
        country: str = "us",
        limit: int = 20,
        genre: Optional[int] = None,  # noqa: UP045
        kind: Literal["hot", "latest"] = "hot",
    ) -> None:
        """样例。

        Args:
            term: 关键词。
            country: 国家。
            limit: 条数。
            genre: 分类。
            kind: 类型。
        """

    schema = derive_input_schema(sample)
    props = schema["properties"]
    assert schema["required"] == ["term"]
    assert props["term"]["type"] == "string"
    assert props["term"]["description"] == "关键词。"
    assert props["country"]["default"] == "us"
    assert props["limit"]["type"] == "integer"
    # Optional[int] 应解开为 integer 且非必填。
    assert props["genre"]["type"] == "integer"
    assert "genre" not in schema["required"]
    assert props["kind"]["enum"] == ["hot", "latest"]


def test_derive_schema_overrides_merge_and_validate() -> None:
    """overrides 合并 enum/描述;非法关键字报错。"""

    def sample(self, feed: str = "newest") -> None:
        """样例。

        Args:
            feed: 流类型。
        """

    schema = derive_input_schema(
        sample, overrides={"feed": {"enum": ["newest", "popular"], "description": "覆盖描述"}}
    )
    assert schema["properties"]["feed"]["enum"] == ["newest", "popular"]
    assert schema["properties"]["feed"]["description"] == "覆盖描述"

    try:
        derive_input_schema(sample, overrides={"feed": {"bogus_key": 1}})
    except ValueError as exc:
        assert "非法关键字" in str(exc)
    else:
        raise AssertionError("非法 overrides 应当报错")


def test_real_source_schema_itunes() -> None:
    """真实源 schema:itunes_search 必填 term,参数含中文说明。"""
    import freesignt  # noqa: F401
    from freesignt.core import registry

    schema = registry.get("itunes_search").input_schema()
    assert schema["required"] == ["term"]
    assert schema["properties"]["limit"]["type"] == "integer"
    assert "搜索关键词" in schema["properties"]["term"]["description"]


def test_real_source_schema_enum_overrides() -> None:
    """源上声明的 enum 透传到 schema(v2ex/bluesky/steamspy)。"""
    import freesignt  # noqa: F401
    from freesignt.core import registry

    assert registry.get("v2ex").input_schema()["properties"]["kind"]["enum"] == ["hot", "latest"]
    bluesky = registry.get("bluesky").input_schema()
    assert bluesky["properties"]["method"]["enum"] == ["actor_search", "post_search"]
