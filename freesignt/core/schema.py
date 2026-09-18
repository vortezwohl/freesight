"""从源类自动派生 fetch 参数的 JSON Schema。

人体工学与 agent 接入的共用底座:源的 fetch() 签名(类型注解 + 默认值)
与 Google 风格 docstring 的 Args 段(中文说明)是唯一事实源,本模块把
两者合成为标准 JSON Schema,用于:
- agent 工具的 parameters 声明(OpenAI function calling 等直接可用);
- 人类开发者的参数自省(client.describe(name))。

docstring 解析采取宽容策略:解析失败只是缺少 description,绝不影响
schema 结构本身的正确性。
"""

from __future__ import annotations

import inspect
import re
import typing
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

# docstring Args 段中 "参数名:" 行的匹配(参数名允许字母数字下划线)。
_ARG_LINE = re.compile(r"^(\w+)\s*\*{0,2}:\s*(.*)$")
# Google 风格段落标题行(如 Returns: / Raises:),Args 段到此处截止。
_SECTION_HEADER = re.compile(
    r"^\s*(Args|Arguments|Returns|Raises|Yields|Examples?|Note|Notes)\s*:\s*$"
)
# JSON Schema 关键字集合,用于 overrides 合并时的合法提示与清理。
_RESERVED_SCHEMA_KEYS = frozenset(
    {"type", "enum", "default", "description", "items", "properties", "required"}
)


def parse_google_args(doc: str | None) -> dict[str, str]:
    """解析 Google 风格 docstring 的 Args 段为 {参数名: 说明}。

    宽容处理多行续写(缩进延续行并入上一参数)与无 Args 段的情况。

    Args:
        doc: 函数 docstring 原文;None 或空返回空 dict。

    Returns:
        {参数名: 合并后的说明文本}。
    """
    args: dict[str, str] = {}
    if not doc:
        return args
    lines = doc.splitlines()
    in_args = False
    current: str | None = None
    for line in lines:
        if _SECTION_HEADER.match(line):
            heading = line.strip().rstrip(":").lower()
            in_args = heading in ("args", "arguments")
            current = None
            continue
        if not in_args:
            continue
        stripped = line.strip()
        if not stripped:
            continue
        match = _ARG_LINE.match(stripped)
        if match:
            current = match.group(1)
            args[current] = match.group(2).strip()
        elif current is not None and line.startswith((" ", "\t")):
            # 缩进续行:并入上一参数的说明。
            args[current] = (args[current] + " " + stripped).strip()
    return args


def _annotation_to_type(annotation: Any) -> tuple[str | None, list[Any] | None]:
    """把 Python 类型注解映射为 (JSON 类型, 枚举值列表)。

    Args:
        annotation: 已解析的类型对象。

    Returns:
        JSON Schema type 字符串与可选 enum 值;无法映射时为 (None, None)。
    """
    if annotation is inspect.Parameter.empty or annotation is None:
        return None, None

    origin = get_origin(annotation)
    if origin is Literal:
        values = list(get_args(annotation))
        inferred = {type(v).__name__ for v in values if v is not None}
        json_type = {
            "str": "string",
            "int": "integer",
            "float": "number",
            "bool": "boolean",
        }.get(next(iter(inferred)) if len(inferred) == 1 else "", None)
        return json_type, values

    if origin is Union:  # 含 Optional[X] 与 X | None
        non_none = [a for a in get_args(annotation) if a is not type(None)]
        if len(non_none) == 1:
            return _annotation_to_type(non_none[0])
        return None, None

    mapping: dict[Any, str] = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        list: "array",
        dict: "object",
        typing.Sequence: "array",
        typing.Mapping: "object",
    }
    for py_type, json_type in mapping.items():
        if annotation is py_type:
            return json_type, None
    if isinstance(annotation, type) and annotation in mapping:
        return mapping[annotation], None
    return None, None


def derive_input_schema(
    func: Any,
    *,
    overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """从函数签名与 docstring 派生 JSON Schema(object 类型)。

    Args:
        func: 目标函数(通常为 BaseSource.fetch)。
        overrides: {参数名: schema 片段} 覆盖;仅接受合法关键字,
            用于补充 enum/示例等签名表达不了的信息。

    Returns:
        {"type": "object", "properties": {...}, "required": [...]}。
        无参函数返回空 properties 结构。

    Raises:
        ValueError: overrides 中出现保留字冲突的非法用法。
    """
    signature = inspect.signature(func)
    try:
        hints = get_type_hints(func)
    except Exception:  # noqa: BLE001 - 注解不可解析(前向引用等)时降级为裸签名
        hints = {}
    descriptions = parse_google_args(inspect.getdoc(func))
    overrides = overrides or {}

    properties: dict[str, Any] = {}
    required: list[str] = []
    for param_name, param in signature.parameters.items():
        if param_name == "self" or param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        annotation = hints.get(param_name, param.annotation)
        json_type, enum_values = _annotation_to_type(annotation)
        prop: dict[str, Any] = {}
        if json_type:
            prop["type"] = json_type
        if enum_values:
            prop["enum"] = enum_values
        description = descriptions.get(param_name, "")
        if description:
            prop["description"] = description
        if param.default is not inspect.Parameter.empty:
            if param.default is not None:
                prop["default"] = param.default
        else:
            required.append(param_name)
        # overrides 片段整体并入同名属性(浅合并,覆盖同名字段)。
        extra = overrides.get(param_name)
        if extra is not None:
            unknown = set(extra) - _RESERVED_SCHEMA_KEYS
            if unknown:
                raise ValueError(
                    f"schema overrides 含非法关键字 {sorted(unknown)}: {param_name}"
                )
            prop.update(extra)
            # override 提供了 enum 时,必填状态与其保持一致。
            if "enum" in extra and param.default is inspect.Parameter.empty:
                required.append(param_name)
        if prop:
            properties[param_name] = prop

    # required 去重保序(overrides 分支可能重复追加)。
    required = list(dict.fromkeys(required))
    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }
