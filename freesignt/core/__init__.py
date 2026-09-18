"""核心层出口:模型、引擎、缓存、搜索、客户端与 agent 封装。

分层结构(依赖自上而下,无环):
- models / errors: 纯数据契约与异常;
- ratelimit / cache / http: 传输治理设施;
- schema / base / registry: 源抽象与注册;
- search / client / agent: 面向使用方的高层能力。
"""

from freesignt.core import (  # noqa: F401
    agent,
    base,
    cache,
    client,
    errors,
    http,
    models,
    ratelimit,
    registry,
    schema,
    search,
)
