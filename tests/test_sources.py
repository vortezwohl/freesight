"""25 个数据源移植验证:fetch 归一化与 to_hits 映射(全部离线桩)。"""

from __future__ import annotations

import pytest

from freesignt.core.models import FetchResult
from tests.conftest import RouterTransport


async def test_itunes_search(client, router) -> None:
    """itunes_search:元数据搜索与 Hit 归一化。"""
    router.add_json(
        "itunes.apple.com/search",
        {"resultCount": 1, "results": [{
            "trackName": "Notion", "trackViewUrl": "https://apps.apple.com/app/id1",
            "trackId": 1, "artistName": "Notion Labs", "averageUserRating": 4.5,
            "genres": ["Productivity"],
        }]},
    )
    result = await client.fetch("itunes_search", term="notion")
    assert result.ok and result.data["resultCount"] == 1
    client.describe("itunes_search")
    source = client._source_instance("itunes_search")
    hit_list = source.to_hits(result.data, {"term": "notion"})
    assert hit_list[0].title == "Notion"
    assert hit_list[0].url.endswith("id1")
    assert hit_list[0].extra["app_id"] == 1


async def test_itunes_reviews_single_entry_dict(client, router) -> None:
    """itunes_reviews:单条 entry 为 dict 的坑被归一化。"""
    router.add_json(
        "customerreviews",
        {"feed": {
            "updated": {"label": "2024-01-01"},
            "entry": {"im:rating": {"label": "1"}, "title": {"label": "Crashes"},
                      "content": {"label": "App won't open"},
                      "author": {"name": {"label": "bob"}}},
        }},
    )
    result = await client.fetch("itunes_reviews", app_id=123)
    assert result.ok
    assert result.data == {
        "updated": "2024-01-01",
        "reviews": [
            {"rating": "1", "title": "Crashes", "content": "App won't open", "author": "bob"}
        ],
    }


async def test_itunes_charts_id_extraction(client, router) -> None:
    """itunes_charts:数字 app id 从 id.attributes.im:id 取。"""
    router.add_json(
        "rss/topfreeapplications",
        {"feed": {"updated": {"label": "2024-01-01"}, "entry": [
            {"im:name": {"label": "App A"}, "id": {"attributes": {"im:id": "42"}, "label": "https://x"},
             "im:artist": {"label": "Dev A"}, "category": {"label": "Games"}},
        ]}},
    )
    result = await client.fetch("itunes_charts", country="us")
    assert result.ok
    app = result.data["apps"][0]
    assert app["id"] == "42" and app["name"] == "App A"
    hits = client._source_instance("itunes_charts").to_hits(result.data, {})
    assert hits[0].extra["rank"] == 1
    assert "id42" in hits[0].url


async def test_hn_algolia(client, router) -> None:
    """hn_algolia:命中归一化,无 url 时回退 HN 站内链接。"""
    router.add_json(
        "hn.algolia.com",
        {"nbHits": 2, "hits": [
            {"objectID": "77", "title": "Show HN: thing", "url": "https://example.com",
             "points": 5, "num_comments": 2},
            {"objectID": "88", "title": "No link", "points": 1},
        ]},
    )
    result = await client.fetch("hn_algolia", query="thing")
    assert result.ok and result.data["nbHits"] == 2
    hits = client._source_instance("hn_algolia").to_hits(result.data, {"query": "thing"})
    assert hits[0].url == "https://example.com"
    assert hits[1].url == "https://news.ycombinator.com/item?id=88"


async def test_hn_firebase_item_and_list(client, router) -> None:
    """hn_firebase:单条 item 明细与列表两种路径。"""
    router.add_json("item/42.json", {"id": 42, "title": "Big news", "score": 99, "by": "carol"})
    router.add_json("topstories.json", [1, 2, 3])
    detail = await client.fetch("hn_firebase", item_id=42)
    assert detail.ok and detail.data["title"] == "Big news"
    listing = await client.fetch("hn_firebase")
    assert listing.ok and listing.data == [1, 2, 3]


async def test_github_public_repo_and_search(client, router) -> None:
    """github_public:单仓详情/搜索两分支与参数校验。"""
    router.add_json("repos/o/r", {"full_name": "o/r", "html_url": "u", "description": "d",
                                  "stargazers_count": 10})
    router.add_json("search/repositories", {"total_count": 1, "items": [
        {"full_name": "a/b", "html_url": "u2", "description": "d2", "stargazers_count": 3}]})
    repo = await client.fetch("github_public", repo="o/r")
    assert repo.ok and repo.data["full_name"] == "o/r"
    search = await client.fetch("github_public", query="stars:>1")
    assert search.ok and search.data["total_count"] == 1
    with pytest.raises(ValueError):
        await client.fetch("github_public")


async def test_dev_ecosystem_sources(client, router) -> None:
    """npm/pypi/pypistats/ecosyste.ms/wordpress/hf 六源。"""
    router.add_json("registry.npmjs.org", {"objects": [
        {"package": {"name": "pkg", "description": "desc", "version": "1.0.0"},
         "score": {"final": 0.5}}]})
    npm = await client.fetch("npm_registry", text="pkg")
    assert npm.ok and npm.data["objects"][0]["package"]["name"] == "pkg"

    router.add_json(
        "pypi.org/pypi/pkg/json",
        {"info": {"name": "pkg", "summary": "s", "version": "1"}, "releases": {"1": []}},
    )
    pypi = await client.fetch("pypi_metadata", package="pkg")
    assert pypi.ok and pypi.data["info"]["name"] == "pkg"

    router.add_json("pypistats.org", {"data": {"last_month": 12345, "last_week": 1000}})
    stats = await client.fetch("pypi_downloads", package="pkg")
    assert stats.ok and stats.data["data"]["last_month"] == 12345
    hit = client._source_instance("pypi_downloads").to_hits(stats.data, {"package": "pkg"})
    assert "12,345" in hit[0].snippet

    router.add_json("repos.ecosyste.ms", {"full_name": "o/r", "description": "d",
                                          "stargazers_count": 7, "html_url": "https://github.com/o/r"})
    eco = await client.fetch("ecosyste_ms", owner="o", repo="r")
    assert eco.ok and eco.data["stargazers_count"] == 7
    with pytest.raises(ValueError):
        await client.fetch("ecosyste_ms", owner="o")

    router.add_json("api.wordpress.org", {"info": {"results": 1, "pages": 1}, "plugins": [
        {"name": "P", "slug": "p", "short_description": "sd", "active_installs": 10}]})
    wp = await client.fetch("wordpress_plugins")
    assert wp.ok and wp.data["plugins"][0]["slug"] == "p"

    router.add_json("huggingface.co/api/models", [{"id": "m1", "downloads": 5, "likes": 2}])
    hf = await client.fetch("huggingface_hub", kind="models", search="m")
    assert hf.ok and hf.data[0]["id"] == "m1"
    with pytest.raises(ValueError):
        await client.fetch("huggingface_hub", kind="spaceships")


async def test_jobs_sources(client, router) -> None:
    """greenhouse/lever 招聘板。"""
    router.add_json("boards/acme/jobs", {"jobs": [
        {"title": "Eng", "absolute_url": "u", "location": {"name": "SF"},
         "departments": [{"name": "Eng"}], "updated_at": "t"}]})
    gh = await client.fetch("greenhouse_jobs", company="acme")
    assert gh.ok and gh.data["jobs"][0]["title"] == "Eng"

    router.add_json("postings/acme", [{"text": "Eng", "hostedUrl": "u2",
                                       "categories": {"location": "Remote", "team": "Core"}}])
    lv = await client.fetch("lever_jobs", company="acme")
    assert lv.ok and lv.data[0]["text"] == "Eng"
    hits = client._source_instance("lever_jobs").to_hits(lv.data, {})
    assert hits[0].snippet == "Remote · Core"


async def test_bluesky_actor_and_post(client, router) -> None:
    """bluesky:账号检索、帖子检索与 at:// 链接转换。"""
    router.add_json("searchActors", {"actors": [{"handle": "a.bsky.social", "displayName": "A",
                                                 "description": "bio", "followersCount": 3}]})
    router.add_json("searchPosts", {"posts": [
        {"uri": "at://did:plc:abc/app.bsky.feed.post/rk1", "record": {"text": "hello notion"},
         "author": {"handle": "b.bsky.social"}, "likeCount": 2}]})
    actors = await client.fetch("bluesky", q="a", method="actor_search")
    assert actors.ok and actors.data["actors"][0]["handle"] == "a.bsky.social"
    posts = await client.fetch("bluesky", q="notion", method="post_search")
    hits = client._source_instance("bluesky").to_hits(posts.data, {"method": "post_search"})
    assert hits[0].url == "https://bsky.app/profile/did:plc:abc/post/rk1"
    with pytest.raises(ValueError):
        await client.fetch("bluesky", q="x", method="nope")


async def test_mastodon_and_v2ex(client, router) -> None:
    """mastodon 趋势(含 HTML 剥离)与 v2ex。"""
    router.add_json("trends/tags", [{"name": "ai", "url": "https://x/tags/ai",
                                     "history": [{"uses": "123"}]}])
    tags = await client.fetch("mastodon_trends", kind="tags")
    assert tags.ok and tags.data[0]["name"] == "ai"

    router.add_json("trends/statuses", [{"url": "https://x/@a/1", "account": {"acct": "a"},
                                         "content": "<p>hello <b>world</b></p>",
                                         "reblogs_count": 1}])
    statuses = await client.fetch("mastodon_trends", kind="statuses")
    hits = client._source_instance("mastodon_trends").to_hits(statuses.data, {"kind": "statuses"})
    assert hits[0].snippet == "hello world"

    router.add_json("topics/hot.json", [{"title": "T", "url": "https://v2ex.com/t/1",
                                         "content": "c", "replies": 3, "node": {"name": "create"}}])
    v2 = await client.fetch("v2ex")
    assert v2.ok and v2.data[0]["title"] == "T"
    with pytest.raises(ValueError):
        await client.fetch("v2ex", kind="cold")


async def test_sec_edgar_both_modes(client, router) -> None:
    """sec_edgar:CIK 提交历史(并行数组压平)与全文检索。"""
    router.add_json(
        "submissions/CIK0000320193.json",
        {"names": ["Apple Inc."], "filings": {"recent": {
            "cik": ["320193"], "form": ["10-K", "8-K"], "filingDate": ["2024-11-01", "2024-08-02"],
            "accessionNumber": ["a1", "a2"],
        }}},
    )
    subs = await client.fetch("sec_edgar", cik="0000320193")
    assert subs.ok
    hits = client._source_instance("sec_edgar").to_hits(subs.data, {"cik": "0000320193"})
    assert hits[0].title.startswith("[10-K] Apple Inc.")
    assert hits[1].extra["accession"] == "a2"

    router.add_json("search-index", {"hits": {"total": {"value": 1}, "hits": [
        {"_source": {"display_names": ["Acme Inc."], "form": "D", "file_date": "2024-01-01"}}]}})
    full_text = await client.fetch("sec_edgar", query="acme", forms="D")
    assert full_text.ok and full_text.data["hits"]["total"]["value"] == 1
    with pytest.raises(ValueError):
        await client.fetch("sec_edgar")


async def test_sec_headers_override(client, router) -> None:
    """sec_edgar 使用申明式 UA(公司名 邮箱格式)。"""
    router.add_json("submissions/CIK1.json", {"names": []})
    await client.fetch("sec_edgar", cik="1")
    sec_req = [r for r in router.calls if "data.sec.gov" in str(r.url)][-1]
    assert sec_req.headers["user-agent"] == client.engine.config.sec_user_agent


async def test_uspto_trademark(client, router) -> None:
    """uspto:Solr 风格 docs 提取。"""
    router.add_json("developer.uspto.gov", {"response": {"docs": [
        {"trademarkName": "ACME", "statusLabel": "Registered", "serialNumber": "1"}]}})
    result = await client.fetch("uspto_trademark", search_text="acme")
    assert result.ok
    hits = client._source_instance("uspto_trademark").to_hits(result.data, {})
    assert hits[0].title == "ACME" and hits[0].extra["serialNumber"] == "1"


async def test_rdap_domain(client, router) -> None:
    """rdap:事件/注册商提取与后缀校验。"""
    router.add_json("rdap.verisign.com", {
        "ldhName": "example.com",
        "events": [{"eventAction": "registration", "eventDate": "2020-01-01T00:00:00Z"},
                   {"eventAction": "expiration", "eventDate": "2027-01-01T00:00:00Z"}],
        "entities": [{"roles": ["registrar"],
                      "vcardArray": ["vcard", [["fn", {}, "text", "Example Registrar"]]]}],
        "status": ["active"],
    })
    result = await client.fetch("rdap_domain", domain="example.com")
    assert result.ok
    hits = client._source_instance("rdap_domain").to_hits(result.data, {"domain": "example.com"})
    assert hits[0].snippet == "注册于 2020-01-01 · 到期 2027-01-01 · 注册商 Example Registrar"
    with pytest.raises(ValueError):
        await client.fetch("rdap_domain", domain="example.io")


async def test_common_crawl_jsonl(client, router) -> None:
    """common_crawl:JSONL 逐行解析。"""
    router.add_text(
        "CC-MAIN",
        '{"url": "https://example.com/a", "timestamp": "20240101", "status": "200"}\n'
        '{"url": "https://example.com/b", "timestamp": "20240201", "status": "301"}\n',
        content_type="application/json",
    )
    result = await client.fetch("common_crawl", url="example.com/*")
    assert result.ok and isinstance(result.data, list) and len(result.data) == 2
    assert result.data[0]["url"].endswith("/a")


async def test_steam_store_both_modes(client, router) -> None:
    """steam_store:目录搜索与 appdetails 两形态。"""
    router.add_json("storesearch", {"total": 1, "items": [
        {"id": 7, "name": "Game", "price": {"final": 1999, "currency": "USD"}}]})
    search = await client.fetch("steam_store", term="game")
    assert search.ok
    hits = client._source_instance("steam_store").to_hits(search.data, {"term": "game"})
    assert hits[0].title == "Game" and hits[0].url.endswith("/7")

    router.add_json("appdetails", {"7": {"success": True, "data": {
        "steam_appid": 7, "name": "Game", "short_description": "fun",
        "genres": [{"description": "Action"}]}}})
    detail = await client.fetch("steam_store", appid=7)
    hits2 = client._source_instance("steam_store").to_hits(detail.data, {"appid": 7})
    assert hits2[0].extra["genres"] == ["Action"]
    with pytest.raises(ValueError):
        await client.fetch("steam_store")


async def test_steamspy_top100_and_detail(client, router) -> None:
    """steamspy:榜单映射与单游戏详情。"""
    # 先注册更具体的 appdetails 路由,避免被宽泛的榜单路由抢先匹配。
    router.add_json("request=appdetails", {"appid": 440, "name": "TF2", "owner": "x"})
    router.add_json(
        "steamspy.com",
        {"570": {"name": "Dota 2", "owner": "100,000,000 .. 200,000,000",
                 "players_2weeks": "1,000,000 .. 2,000,000"}},
    )
    top = await client.fetch("steamspy")
    assert top.ok
    hits = client._source_instance("steamspy").to_hits(top.data, {})
    assert hits[0].title == "Dota 2" and hits[0].url.endswith("/570")

    detail = await client.fetch("steamspy", request="appdetails", appid=440)
    hits2 = client._source_instance("steamspy").to_hits(detail.data, {"appid": 440})
    assert hits2[0].title == "TF2"


async def test_itchio_feed_parse(client, router) -> None:
    """itchio:RSS XML 解析为 Hit。"""
    router.add_text(
        "itch.io",
        '<?xml version="1.0"?><rss version="2.0"><channel>'
        "<item><title>Indie Game</title><link>https://x.itch.io/g</link>"
        "<pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate></item>"
        "</channel></rss>",
        content_type="application/rss+xml",
    )
    result = await client.fetch("itchio_feed")
    assert result.ok and isinstance(result.data, str)
    hits = client._source_instance("itchio_feed").to_hits(result.data, {})
    assert hits[0].title == "Indie Game" and hits[0].url.endswith("/g")


async def test_crt_sh_subdomains(client, router) -> None:
    """crt_sh:多行 name_value 提取唯一子域名,过滤通配符。"""
    router.add_json("crt.sh", [
        {"name_value": "a.example.com\nb.example.com"},
        {"name_value": "*.wild.example.com\na.example.com"},
    ])
    result = await client.fetch("crt_sh", domain="example.com")
    assert result.ok
    assert result.data == {"subdomains": ["a.example.com", "b.example.com"], "records": 2}
    hits = client._source_instance("crt_sh").to_hits(result.data, {"domain": "example.com"})
    assert len(hits) == 2 and hits[0].url == "https://a.example.com"


async def test_crt_sh_non_json_falls_back_to_error() -> None:
    """crt_sh 返回非 JSON(如临时错误页)时折叠为 ok=False。"""
    from freesignt.core.client import AsyncFreeSight

    router = RouterTransport()
    router.add_text("crt.sh", "<html>under maintenance</html>", content_type="text/html")
    async with AsyncFreeSight(transport=router, rate_multiplier=1000.0) as client:
        result = await client.fetch("crt_sh", domain="example.com")
    assert result.ok is False
    assert "非 JSON" in (result.error or "")


async def test_fetch_result_shape(client, router) -> None:
    """FetchResult 统一形态:source/status/latency/to_dict 可序列化。"""
    router.add_json("v2ex.com/api/topics", [{"title": "t"}])
    result: FetchResult = await client.fetch("v2ex", kind="latest")
    assert result.source == "v2ex"
    assert result.status == 200
    assert result.latency_s >= 0
    payload = result.to_dict()
    assert payload["ok"] is True and payload["source"] == "v2ex"
