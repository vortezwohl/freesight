# freesight

免费竞品调研 SDK:把 25 个免费免 key 公开数据源聚合为一个人体工学的 Python 客户端,
支持多源统一搜索(可插拔语义排序),并可直接封装为 LLM agent 的工具。

源自 Industry-Research 的 datasource 层重构:全异步内核、按 host 自适应限流、
TTL 缓存 + 单飞请求合并,面向 C 端高并发场景"既拿全数据、又尽量不撞限流"。

- Python >= 3.13,仅两个运行时依赖(`httpx` + `h2`),全部数据源免费免 key;
- SDK 不做持久化存储:内置进程内 TTL 缓存,外部存储通过 `CacheProtocol` 外包给调用方。

## 安装

```bash
uv sync
```

## 快速上手

```python
import freesignt

# 一行式:模块级默认客户端(进程内单例)
resp = freesignt.search("notion")
for hit in resp.hits[:10]:
    print(f"[{hit.source}] {hit.title} {hit.url}")

# 推荐用法:显式客户端(同步)
from freesignt import FreeSight

with FreeSight() as client:
    result = client.itunes_search(term="notion", country="us", limit=5)  # 源名即方法
    reviews = client.itunes_reviews(app_id=1239583776, country="us")
    jobs = client.greenhouse_jobs(company="anthropic")
    info = client.describe("crt_sh")          # 查参数 schema/限速/说明
    names = [s.name for s in client.list_sources()]
```

异步场景(服务端 / agent 框架):

```python
from freesignt import AsyncFreeSight

async with AsyncFreeSight() as client:
    hits = (await client.search("notion")).hits
    result = await client.fetch("hn_algolia", query="show hn")
```

## 数据源一览(25 个,全部免费免 key)

| 领域 | 源 | 说明 |
| --- | --- | --- |
| 应用商店 | `itunes_search` / `itunes_reviews` / `itunes_charts` | App Store 元数据/评论/榜单 |
| 社区 | `hn_algolia` / `hn_firebase` | Hacker News 全历史搜索/实时数据 |
| 开源 | `github_public` / `ecosyste_ms` / `npm_registry` / `pypi_metadata` / `pypi_downloads` / `wordpress_plugins` / `huggingface_hub` | 仓库/包/模型生态情报 |
| 招聘 | `greenhouse_jobs` / `lever_jobs` | ATS 公开招聘板(stealth 公司方向信号) |
| 社媒 | `bluesky` / `mastodon_trends` / `v2ex` | 公共检索与趋势 |
| 法定披露 | `sec_edgar` / `uspto_trademark` / `rdap_domain` / `common_crawl` | SEC/商标/域名/全网快照 |
| 游戏 | `steam_store` / `steamspy` / `itchio_feed` | Steam 搜索与估算、独立游戏流 |
| 基础设施 | `crt_sh` | CT 证书日志子域名发现(预发布信号) |

每个源的参数、限速与中文说明:`client.describe(name)` 或 `client.list_sources()`。

## 统一搜索与语义搜索

`search()` 一次查询扇出到多个源,归一化为可比的命中列表:

```python
resp = client.search(
    "notion",
    limit_per_source=5,              # 每源条数上限
    sources=["itunes_search", "hn_algolia", "npm_registry"],  # 缺省为默认 9 源
)
resp.hits          # [Hit(source, title, url, snippet, score, extra, raw), ...]
resp.per_source    # {源名: 成败/条数/是否缓存/耗时},单源失败不中断整体
```

- 默认词法排序:BM25 风格打分(标题加权),中文按单字切分,零依赖;
- 语义排序:注入任何 `texts -> 向量列表` 的可调用对象即可
  (sentence-transformers 的 `model.encode`、OpenAI embedding 封装等):

```python
client = FreeSight(embedder=my_embed_model.encode)
resp = client.search("跨设备笔记协作工具", semantic=True)   # 自动余弦重排
resp.semantic       # True 表示本次为语义排序;嵌入异常自动回退词法
```

域名情报模式(把域名当查询词,显式指定源):

```python
resp = client.search("openai.com", sources=["crt_sh", "rdap_domain", "common_crawl"])
```

## 接入 Agent 系统

工具声明自动生成(参数 JSON Schema 从源签名与 docstring 派生),
兼容 OpenAI function calling 格式,可平移到 Anthropic tool use / MCP:

```python
from freesignt import FreeSight

client = FreeSight()
tools = client.build_agent_tools()          # 2 个复合工具 + 25 个单源工具
payload = client.call_tool("freesight_search", {"query": "notion", "limit_per_source": 3})
payload = client.call_tool("itunes_search", {"term": "notion", "limit": 5})
payload = client.call_tool("freesight_list_sources", {})   # 让 agent 自行发现源与参数
```

- `freesight_search`:聚合搜索复合工具(agent 场景的主力,一个工具覆盖多源);
- `freesight_list_sources`:源目录发现工具(25 源的能力/参数/限速速查);
- 每个源一个细粒度工具:需要精确单源调用时使用;
- 只暴露部分源:`client.build_agent_tools(["hn_algolia", "crt_sh"])`;
- 工具执行失败统一折叠为 `{"ok": False, "error": ...}`,不向框架抛异常。

## C 端高并发设计

三层递进的取数管线(顺序固定):**内存缓存 -> 单飞合并 -> 限流引擎**。

1. **按 host 令牌桶限流**:每个源的实测限速换算为匀速放行(默认突发容量 1,
   纯匀速对免费 API 最友好);同 host 多源共享预算(如 iTunes 三个源共用
   `itunes.apple.com`),与免费 API 的服务级限速语义一致;
2. **自适应冷却**:命中 429/`Retry-After` 或 `X-RateLimit-Remaining: 0` 时,
   该 host 整体冷却到恢复时间,所有等待者自动排队;冷却较长(>30s)时不
   无脑挂起,立即返回携带冷却秒数的失败结果,由调用方决策;
3. **并发席位**:每 host 最大在途请求数(默认 5),避免连接风暴;
4. **TTL 缓存**:每源声明建议 TTL(榜单 1h、评论 15min、招聘 2h 等),
   只有成功结果进缓存;
5. **单飞合并**:并发同参请求合并为一次网络调用——千人同查一个竞品,
   只打一次上游;leader 被取消时等待者自动接管。

C 端大流量部署建议:

```python
client = FreeSight(
    rate_multiplier=0.3,          # 全局限速再收紧(0.2-0.5 常用)
    max_concurrency_per_host=3,   # 每端点在途请求数再收紧
    cache_maxsize=4096,           # 进程内缓存容量
)
snapshot = client.rate_snapshot()  # 各 host 速率/剩余冷却(接监控告警)
```

## 存储外包(SDK 不做持久化)

```python
from freesignt import FreeSight

class MyRedisCache:
    """满足 CacheProtocol(get/set/clear)即可注入,存储由调用方负责。"""
    def get(self, key): ...
    def set(self, key, value, ttl_s): ...
    def clear(self): ...

client = FreeSight(cache=MyRedisCache())   # 或 cache=None 关闭缓存
```

## 线程与事件循环模型

- `AsyncFreeSight` 绑定创建它的事件循环;跨线程/脚本场景请用 `FreeSight`;
- `FreeSight` 内部持有一个专属后台事件循环线程(双重检查锁保证全局唯一),
  所有同步方法线程安全,可在多线程 worker 中并发调用;
- 不要在运行中的事件循环内使用 `FreeSight`(会显式报错),请改用 `AsyncFreeSight`;
- 缓存返回的对象视为只读;需要修改请自行深拷贝。

## 开发

```bash
uv sync                 # 安装依赖(含 dev)
uv run pytest           # 88 个离线单测(httpx MockTransport,不依赖真实网络)
uv run ruff check .     # lint
```

测试覆盖:注册表/Schema 派生/令牌桶与冷却/限速头解析/缓存与单飞/25 源归一化/
统一搜索(含部分失败容错)/agent 工具闭环/同步桥线程安全。

## 说明与边界

- 各源限速值为 2026-09-09 实测快照,平台可能随时调整;自适应冷却机制会
  在真实 429 时自动退避,无需改代码;
- SEC 源要求申明式 UA,请通过 `FreeSight(sec_user_agent="公司名 邮箱")` 覆盖默认占位;
- `itunes_reviews` 为 Apple 老服务,存在服务端间歇性降级(返回空 entry),
  上层应把"空评论"视为可重试信号而非业务结论;
- `crt_sh` 为单机慢源(长超时/串行/多次退避),适合低频后台任务而非在线请求路径。
