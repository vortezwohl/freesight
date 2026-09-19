# freesight

免费竞品调研**数据聚合** SDK:聚合 25 个免费免 key 公开数据源,只负责三件事——

1. **聚合抓取**:按 host 自适应限流 + 429 冷却 + TTL 缓存 + 单飞请求合并,
   下游无感获取信息;
2. **聚合检索**:一次查询扇出到多个源(`search("notion")`);
3. **结果聚合**:各源完整结果装进一个容器(`AggregateResult`)。

**明确不做的**(全部交给调用方):信息筛选、排序、语义/相关性判断、
agent 工具封装、持久化存储。SDK 因此非常轻:Python >= 3.13,
运行时依赖仅 `httpx` + `h2`;缓存协议(CacheProtocol)可外接,
调用方可借此实现自己的持久化缓存。

## 安装

```bash
uv sync
```

## 两层 API

**第一层:根包开箱即用(最小 API)**

```python
import freesignt

# 一行式聚合检索(进程级默认客户端)
agg = freesignt.search("notion")
for name, r in agg.results.items():
    print(name, r.ok, r.error or "")

# 同步客户端(推荐,脚本与人类):源名即方法
with freesignt.FreeSight() as client:
    result = client.itunes_search(term="notion", limit=5)   # -> FetchResult
    reviews = client.itunes_reviews(app_id=1239583776)
    agg = client.search("notion", limit_per_source=5)
    info = client.describe("crt_sh")          # 参数 schema/限速/中文说明
    names = [s.name for s in client.list_sources()]

# 异步客户端(服务端/agent 宿主):与 FreeSight 同层级、同一套方法面
from freesignt import AsyncFreeSight

async with AsyncFreeSight() as client:
    agg = await client.search("notion")
    result = await client.fetch("hn_algolia", query="show hn")
```

根包只导出:`FreeSight` / `AsyncFreeSight`(双客户端同层级)、
`FetchResult` / `AggregateResult` / `CacheProtocol`,以及快捷函数
`search` / `fetch` / `list_sources`。

**第二层:内核层(扩展与二次封装,不在根包导出)**

```python
from freesignt.core.base import BaseSource             # 自定义源基类(定义即注册)
from freesignt.core import registry                    # 源注册表(get/catalogue/...)
from freesignt.core.cache import TTLCache, SingleFlight, CacheProtocol
from freesignt.core.ratelimit import TokenBucket, RateGovernor
from freesignt.core.http import HttpEngine, HttpConfig
```

高级调用方(如 agent 框架接入方)自行基于内核层做工具封装、语义排序、
结果筛选——SDK 通过 `describe()` / `SourceInfo.input_schema` 提供每个源
的参数 JSON Schema 与元信息,供二次封装取用,但封装本身不属于 SDK。

## 数据源一览(25 个,全部免费免 key)

| 领域 | 源 | 说明 |
| --- | --- | --- |
| 应用商店 | `itunes_search` / `itunes_reviews` / `itunes_charts` | App Store 元数据/评论/榜单 |
| 社区 | `hn_algolia` / `hn_firebase` | Hacker News 全历史搜索/实时数据 |
| 开源 | `github_public` / `ecosyste_ms` / `npm_registry` / `pypi_metadata` / `pypi_downloads` / `wordpress_plugins` / `huggingface_hub` | 仓库/包/模型生态情报 |
| 招聘 | `greenhouse_jobs` / `lever_jobs` | ATS 公开招聘板(stealth 公司方向信号) |
| 社媒 | `bluesky` / `mastodon_trends` / `v2ex` | 公共检索与趋势 |
| 法定披露 | `sec_edgar` / `uspto_trademark` / `rdap_domain` / `common_crawl` | SEC/商标/域名/全网快照 |
| 游戏 | `steam_store` / `steamspy` / `itchio_feed` | Steam 搜索与估算、独立游戏 RSS |
| 基础设施 | `crt_sh` | CT 证书日志子域名发现(预发布信号) |

## 聚合检索的语义

`search()` = "把查询词投给多个源,把各源的完整结果原样收回来":

```python
agg = client.search(
    "notion",
    limit_per_source=5,                                       # 每源获取条数
    sources=["itunes_search", "hn_algolia", "npm_registry"],  # 缺省为默认 9 源
)
agg.results        # {源名: FetchResult}:完整数据 + ok/error/耗时/缓存标记
agg.ok_sources     # 成功的源名列表
agg.failed_sources # 失败的源名列表(错误详情在对应 FetchResult.error)
agg.to_dict()      # 整体导出 JSON
```

- 结果**不做任何筛选、排序、去重或相关性判断**——怎么消费是调用方的事;
- 单源失败不中断整体,失败详情保留在该源的 `FetchResult` 里;
- 域名情报模式(把域名当查询词,显式指定源):

```python
agg = client.search("openai.com", sources=["crt_sh", "rdap_domain", "common_crawl"])
```

## C 端高并发设计

取数管线固定三层递进:**内存缓存 -> 单飞合并 -> 限流引擎**。

1. **按 host 令牌桶限流**:每个源的实测限速换算为匀速放行(默认突发容量
   1,纯匀速对免费 API 最友好);同 host 多源共享预算(如 iTunes 三个源
   共用 `itunes.apple.com`),与免费 API 的服务级限速语义一致;
2. **自适应冷却**:命中 429/`Retry-After` 或 `X-RateLimit-Remaining: 0` 时,
   该 host 整体冷却到恢复时间,所有等待者自动排队;冷却较长(>30s)时不
   无脑挂起,立即返回携带冷却秒数的失败结果,由调用方决策;
3. **每 host 并发席位**(默认 5),避免连接风暴;
4. **TTL 缓存**:每源声明建议 TTL(榜单 1h、评论 15min、招聘 2h 等),
   只有成功结果进缓存;
5. **单飞合并**:并发同参请求合并为一次网络调用——千人同查一个竞品,
   只打一次上游;leader 被取消时等待者自动接管。

C 端大流量部署建议:

```python
client = freesignt.FreeSight(
    rate_multiplier=0.3,          # 全局限速再收紧(0.2-0.5 常用)
    max_concurrency_per_host=3,   # 每端点在途请求数再收紧
    cache_maxsize=4096,           # 进程内缓存容量
)
snapshot = client.rate_snapshot()  # 各 host 速率/剩余冷却(接监控告警)
```

## 缓存与存储外包(SDK 不做持久化)

默认进程内 TTL+LRU 内存缓存;调用方实现三个方法即可外接自有存储:

```python
class MyRedisCache:
    """满足 CacheProtocol(get/set/clear)即可注入,持久化由调用方负责。"""
    def get(self, key): ...
    def set(self, key, value, ttl_s): ...
    def clear(self): ...

client = freesignt.FreeSight(cache=MyRedisCache())   # 或 cache=None 关闭缓存
```

## 线程与事件循环模型

- `AsyncFreeSight` 绑定创建它的事件循环;跨线程/脚本场景用根包的
  `FreeSight`;
- `FreeSight` 内部持有一个专属后台事件循环线程(双重检查锁保证全局唯一),
  所有同步方法线程安全,可在多线程 worker 中并发调用;
- 不要在运行中的事件循环内使用 `FreeSight`(会显式报错),请改用
  `AsyncFreeSight`;
- 缓存返回的对象视为只读;需要修改请自行深拷贝。

## 开发

```bash
uv sync                 # 安装依赖(含 dev)
uv run pytest           # 74 个离线单测(httpx MockTransport,不依赖真实网络)
uv run ruff check .     # lint
```

测试覆盖:注册表/Schema 派生/令牌桶与冷却/限速头解析/缓存与单飞/
25 源抓取与源内归一化/聚合检索(含部分失败容错)/同步桥线程安全。

## 说明与边界

- 各源限速值为 2026-09-09 实测快照,平台可能随时调整;自适应冷却机制会
  在真实 429 时自动退避,无需改代码;
- SEC 源要求申明式 UA,请通过 `FreeSight(sec_user_agent="公司名 邮箱")`
  覆盖默认占位;
- `itunes_reviews` 为 Apple 老服务,存在服务端间歇性降级(返回空 entry),
  上层应把"空评论"视为可重试信号而非业务结论;
- `crt_sh` 为单机慢源(长超时/串行/多次退避),适合低频后台任务而非
  在线请求路径;
- 源内仅做必要的结构归一化(RSS 压平/JSONL 逐行解析等),跨源的字段
  映射、筛选与排序均不在 SDK 职责内。
