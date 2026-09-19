"""内核层:模型、引擎、缓存、限流、源抽象与客户端。

本包是面向高级调用方的内核层,不(全部)经由根包出口;高级用户按需
从具体子模块导入,例如:
    from freesignt.core.client import AsyncFreeSight
    from freesignt.core.base import BaseSource          # 自定义源(定义即注册)
    from freesignt.core.cache import TTLCache, CacheProtocol
    from freesignt.core.ratelimit import TokenBucket, RateGovernor

分层结构(依赖自上而下,无环):
- models / errors: 纯数据契约与异常;
- ratelimit / cache / http: 传输治理设施;
- schema / base / registry: 源抽象与注册;
- client: 面向使用方的门面客户端。
"""

from freesignt.core import (  # noqa: F401
    base,
    cache,
    client,
    errors,
    http,
    models,
    ratelimit,
    registry,
    schema,
)
