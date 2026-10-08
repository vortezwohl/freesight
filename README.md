# freesight

> 仓库/发布名为 `freesight`,导入包名为 `freesignt`(`import freesignt`)。

免费竞品调研**多渠道数据访问** SDK:封装 75 个免费免 key 公开数据源,
每个渠道一层薄的独立访问封装,只负责一件事——

**单渠道取数**:每个源独立 fetch,按 host 自适应限流 + 429 冷却 +
TTL 缓存 + 单飞请求合并,返回该源独立的 `FetchResult`。

各渠道的参数与结果形态完全不同,SDK **不做跨源聚合检索**——
结果的合并、筛选、排序、语义判断全部交给调用方。

**明确不做的**(全部交给调用方):跨源聚合检索、信息筛选、排序、
语义/相关性判断、agent 工具封装、持久化存储。SDK 因此很轻:
Python >= 3.13,运行时依赖仅 `httpx` + `h2` + `ddgs`(DuckDuckGo
检索的浏览器指纹库);缓存协议(CacheProtocol)可外接,调用方可
借此实现自己的持久化缓存。

## 安装

```bash
uv sync
```

## 两层 API

**第一层:根包开箱即用(最小 API)**

```python
import freesignt

# 一行式单渠道取数(进程级默认客户端)
result = freesignt.fetch("hn_algolia", query="show hn")
print(result.ok, result.data)

# 同步客户端(推荐,脚本与人类):源名即方法
with freesignt.FreeSight() as client:
    result = client.itunes_search(term="notion", limit=5)   # -> FetchResult
    reviews = client.itunes_reviews(app_id=1239583776)
    info = client.describe("crt_sh")          # 参数 schema/限速/中文说明
    names = [s.name for s in client.list_sources()]

# 异步客户端(服务端/agent 宿主):与 FreeSight 同层级、同一套方法面
from freesignt import AsyncFreeSight

async with AsyncFreeSight() as client:
    result = await client.fetch("hn_algolia", query="show hn")
    domain = await client.rdap_domain(domain="openai.com")
```

根包只导出:`FreeSight` / `AsyncFreeSight`(双客户端同层级)、
`FetchResult` / `CacheProtocol`,以及快捷函数 `fetch` / `list_sources`。

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

## 数据源一览(75 个,全部免费免 key)

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
| 基础设施足迹 | `rapiddns` / `subdomain_center` / `otx_passive_dns` / `hackertarget` / `shodan_internetdb` / `certspotter` / `wayback_cdx` | 子域聚合/被动 DNS/子域+IP 映射/IP 端口画像/CT 冗余源/存档 URL 索引 |
| 威胁情报 | `urlscan` / `hudsonrock` | 公开页面扫描记录/IP-ASN;infostealer 域名泄漏画像(涉敏感数据,调用方自负合规) |
| 通用搜索 | `ddg_search` / `searxng` / `baidu` / `yahoo` / `mojeek` / `wikipedia` / `wikidata` / `gdelt` | DuckDuckGo(ddgs 库)/元搜索实例/三 SERP/维基检索/SPARQL/全球新闻索引 |
| 网页获取 | `jina_reader` / `allorigins` / `codetabs` / `corsproxy` | 无头渲染转 Markdown(r.jina.ai)/三个免 key 内容代理 |
| 公司注册 | `jp_houjin_bangou` / `fr_sirene` / `fr_bodacc` / `no_brreg` / `fdic_banks` | 日本法人番号/法国企业检索(Sirene 同源)/法国法定公告/挪威注册局/美国银行库 |
| 风投创业 | `yc_companies` / `sec_form_d` / `signal_nfx` | YC 目录(yc-oss 静态 JSON)/SEC Form D 融资披露/NFX 投资人列表 |
| 宏观统计 | `worldbank` / `eurostat` / `oecd` / `imf` / `cn_stats` | 世行/欧盟统计局/OECD/IMF SDMX/中国国家统计局(反爬,尽力而为) |
| 产品社区 | `discourse` / `fdroid` | 任意 Discourse 论坛 JSON/F-Droid 应用详情 |
| 内容流 | `reddit` / `youtube_rss` / `google_news` / `rsshub` / `lobsters` | Reddit(json/rss,匿名限速严格)/YouTube RSS/Google News RSS/RSSHub 路由/Lobsters |
| 学术文献 | `crossref` / `openalex` / `arxiv` | 1.5 亿 DOI/2.5 亿实体/预印本(均免 key,mailto 可进礼貌池) |
| 包生态 | `rubygems` / `crates` / `packagist` / `nuget` / `dockerhub` / `repology` | Ruby/Rust/PHP/.NET 包与 Docker 镜像/跨发行版版本聚合 |

> 基础设施足迹与威胁情报 9 源的端点行为参照 theHarvester 社区实测
> (2026-09)与各服务公开文档,限速为保守声明待实测复核;`shodan_internetdb`
> 入参为 IP(本 SDK 不做 DNS 解析),`hackertarget` 免 key 每日限量。
> 2026-09-20 新增的 41 源端点形态已逐一直连探测;个别源(`jp_houjin_bangou`
> / `repology` / `lobsters`)在部分网络环境 TLS 握手异常,属网络层可达性
> 问题,接口本身公开免 key。

## 单渠道访问的语义

每个渠道就是一次独立的 `fetch`(或等价的属性糖 `client.源名(...)`):

```python
result = client.hn_algolia(query="notion", hits_per_page=5)
result.ok        # 是否成功(HTTP 200 且解析无致命错误)
result.data      # 该源的完整业务数据(原样,未筛选未排序)
result.error     # 失败时的错误详情
result.cached    # 是否来自缓存
result.to_dict() # 导出 JSON
```

- 各源参数不同,可通过 `client.describe("hn_algolia")` 查看参数
  JSON Schema 与限速说明;
- 失败不抛异常(参数校验 `ValueError` 除外),由调用方按 `ok` 分流;
- 需要查多个渠道时,由调用方自行编排(串行、`asyncio.gather` 或任务队列
  均可)——SDK 不提供扇出与聚合,也不做任何筛选、排序、去重;

```python
# 调用方自行编排多渠道(示例:asyncio.gather)
import asyncio

results = await asyncio.gather(
    client.fetch("crt_sh", domain="openai.com"),
    client.fetch("rdap_domain", domain="openai.com"),
    client.fetch("rapiddns", domain="openai.com"),
    client.fetch("urlscan", domain="openai.com"),
    return_exceptions=False,
)
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
- `FreeSight` 内部持有一个专属后台事件循环线程(双重检查锁保证该客户端
  实例内唯一,多个 `FreeSight` 实例会各起一条后台线程),所有同步方法线程安全,
  可在多线程 worker 中并发调用;
- 不要在运行中的事件循环内使用 `FreeSight`(会显式报错),请改用
  `AsyncFreeSight`;
- 缓存返回的对象视为只读;需要修改请自行深拷贝。

## 开发

```bash
uv sync                 # 安装依赖(含 dev)
uv run pytest           # 131 个离线单测(httpx MockTransport,不依赖真实网络)
uv run ruff check .     # lint
```

测试覆盖:注册表/Schema 派生/令牌桶与冷却/限速头解析/缓存与单飞/
75 源抓取与源内归一化/RSS-Atom 解析/同步桥线程安全。

## 说明与边界

- 各源限速值为 2026-09-09/20 实测快照,平台可能随时调整;自适应冷却机制会
  在真实 429 时自动退避,无需改代码;
- SEC 源要求申明式 UA,请通过 `FreeSight(sec_user_agent="公司名 邮箱")`
  覆盖默认占位;
- `itunes_reviews` 为 Apple 老服务,存在服务端间歇性降级(返回空 entry),
  上层应把"空评论"视为可重试信号而非业务结论;
- `crt_sh` 为单机慢源(长超时/串行/多次退避),适合低频后台任务而非
  在线请求路径;
- 反爬敏感源:`baidu` 匿名高频会命中安全验证页(源内显式识别为失败)、
  `reddit` 匿名限速严格(约 10 qpm,重度使用应注册免费 OAuth)、
  `cn_stats` 实测部分网络 403(需浏览器会话)、`rsshub` 公共实例
  可用性波动大(生产建议 Docker 自建)、`searxng` 公共实例常带浏览器
  验证(如 searx.be,源内显式识别,建议自建)、`mojeek` 实测 httpx
  直连返回空壳页(需浏览器会话)、SERP 类(`baidu`/`yahoo`/`mojeek`)
  解析为尽力而为,页面改版时可能返回原始 HTML;
- 网络可达性个别差异:`jp_houjin_bangou`/`repology`/`lobsters`/`imf`
  在部分出口网络 TLS 握手异常(接口本身公开免 key),部署前建议先行验证;
- `ddg_search` 走 ddgs 库(primp 浏览器指纹)自带重试,不经过本 SDK
  的 HttpEngine 治理,限速由源声明值约束;
- 2026-09-20 调研后明确**不收录**的"免费"源(实测不可达或不满足免 key):
  EDINET v2(需免费订阅键,v1 已关闭)、德国 Handelsregister(JSF 表单站,
  无免 key API,开放镜像 OffeneRegister 亦不可用)、OpenVC(Cloudflare
  浏览器验证墙,可经 `jina_reader` 组合访问)、INPI RNE API(同墙,
  已由 `fr_bodacc` 承担法国披露角色)、Crunchbase API(2025 起免费层取消);
- 源内仅做必要的结构归一化(RSS 压平/JSONL 逐行解析等),跨源的字段
  映射、筛选与排序均不在 SDK 职责内。
