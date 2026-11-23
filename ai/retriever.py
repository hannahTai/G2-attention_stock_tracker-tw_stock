"""retriever：新聞 RAG 的檢索，挑出一張卡片要用的新聞。不呼叫 API、不連資料庫（向量查詢在 repository）。

每則新聞轉成向量的文字（news_text）：標題＋新聞摘要；沒有摘要用導言，都沒有就只有標題。

一張卡片要寫摘要時（ai/task.py 的 card_news）：
  1. query_text()：查詢＝股票名稱＋近 30 天公告的主旨和摘要，轉成一個向量。
     不放被注意的原因（「當日沖銷成交量占…比率61.27%」這種條文字句）：放了會去比對「股價大漲」的快訊，找不到「為什麼」
  2. repository.stock_news_candidates()：這檔最近 NEWS_LOOKBACK_DAYS 天、有向量的新聞，用 pgvector 的 cosine 距離（<=>）排好
  3. related_news()：先拿掉沒有內容的（is_noise：論壇貼文、個股概覽、法人買賣超表、重複注意股名單的快訊）；
     相似度低於 MIN_SIMILARITY 的不要；同一件事好幾家報（同一個 story_id、same_title，或兩則向量相似度 ≥ DUPLICATE_SIMILARITY）
     只留最相關的一則，其他家記在 also_reported_by（卡片 AI 摘要的輸入會寫「X、Y 也有報導」）；最多 MAX_NEWS 則

同一件事（assign_stories，寫進 ai_news_story）：一則新聞和 STORY_DAYS 天內、同一檔股票、比它早的新聞
same_title 或相似度 ≥ DUPLICATE_SIMILARITY，就歸到那則的 story；網站的新聞列表一件事只列一則，旁邊寫「也有 X、Y 報導」。
"""
from ai import embedder

MAX_NEWS = 5
MIN_SIMILARITY = 0.45
DUPLICATE_SIMILARITY = 0.84   # 標題＋摘要的向量：0.84～0.88 幾乎都是同一件事別家寫的，0.82 以下多半是不同的事
STORY_DAYS = 3   # 同一件事：往前看幾天內的新聞
SAME_TITLE_MIN_CHARS = 10   # 一則標題是另一則的開頭也算同一件事：短的那則至少要這麼長（太短的「台積電」不算）

# 標題有這些字的新聞沒有「為什麼」的內容：論壇貼文、個股概覽頁、法人買賣超表、操作日報、
# 重複注意股／警示股／處置股名單的快訊（卡片上已經有）
NOISE_TITLE_WORDS = ("股市爆料同學會", "個股概覽", "三大法人買賣超", "操作日報", "注意股：", "警示股", "處置股")


def news_text(title: str, summary: str | None = None, lead: str | None = None) -> str:
    """一則新聞轉成向量的文字：標題＋新聞摘要；沒有摘要用導言，都沒有就只有標題。

    >>> news_text("健策營收創高", "10月營收年增81％", "導言")
    '健策營收創高\\n10月營收年增81％'
    >>> news_text("健策營收創高", None, "導言"), news_text("健策營收創高")
    ('健策營收創高\\n導言', '健策營收創高')
    """
    body = summary or lead
    return f"{title}\n{body}" if body else title


def same_title(a: str, b: str) -> bool:
    """兩則標題是同一篇：一樣，或一則是另一則的開頭（同一篇轉貼時後面加了欄目名稱，例如「…-起漲K線」）。
    和 repository.news_story_links 的 SQL 同一個規則。

    >>> same_title("冠西電週五跌近一成仍留3清單", "冠西電週五跌近一成仍留3清單-起漲K線"), same_title("台積電", "台積電法說會")
    (True, False)
    """
    short, long = sorted((a, b), key=len)
    return a == b or (len(short) >= SAME_TITLE_MIN_CHARS and long.startswith(short))


def is_noise(title: str) -> bool:
    """
    >>> is_noise("2330 台積電 - 下週反彈低點位置- 股市爆料同學會"), is_noise("《電零組》注意股：敬鵬 近5日漲20.12%")
    (True, True)
    >>> is_noise("新代法說會／記憶體供貨到2027年底無虞")
    False
    """
    return any(word in title for word in NOISE_TITLE_WORDS)


def query_text(card: dict, announcements: list[dict]) -> str:
    """股票名稱＋公告的主旨和摘要（不放被注意的原因，見最上面）。

    >>> query_text({"stock_name": "健策", "reason_text": "最近六個營業日累積漲幅32%"},
    ...            [{"subject": "公告10月營收", "ai_summary": "10月營收年增81％"}, {"subject": "澄清媒體報導"}])
    '健策\\n公告10月營收：10月營收年增81％\\n澄清媒體報導'
    """
    lines = [f"{a['subject']}：{a['ai_summary']}" if a.get("ai_summary") else a["subject"] for a in announcements]
    return "\n".join(part for part in [card.get("stock_name")] + lines if part)


def similarity(a: list[float], b: list[float]) -> float:
    """兩個長度 1 的向量的 cosine 相似度（＝內積）。

    >>> similarity([1.0, 0.0], [0.6, 0.8])
    0.6
    """
    return sum(x * y for x, y in zip(a, b))


def related_news(candidates: list[dict]) -> list[dict]:
    """candidates：已經照相關程度排好（最相關的在前），每則有 similarity、embedding（pgvector 的字串）。

    >>> news = [{"news_id": "a", "title": "A", "publisher": "UDN", "similarity": 0.8, "embedding": "[1.0,0.0]"},
    ...         {"news_id": "b", "title": "B", "publisher": "自由時報", "similarity": 0.7, "embedding": "[0.99,0.141]"},
    ...         {"news_id": "x", "title": "X - 股市爆料同學會", "publisher": "CMoney", "similarity": 0.65, "embedding": "[0.0,1.0]"},
    ...         {"news_id": "c", "title": "C", "publisher": "CMoney", "similarity": 0.6, "embedding": "[0.0,1.0]"},
    ...         {"news_id": "e", "title": "C", "publisher": "聚財網", "similarity": 0.5, "embedding": "[0.6,0.8]"},
    ...         {"news_id": "d", "title": "D", "publisher": "鉅亨網", "similarity": 0.2, "embedding": "[0.6,0.8]"}]
    >>> [(n["news_id"], n["also_reported_by"]) for n in related_news(news)]
    [('a', ['自由時報']), ('c', ['聚財網'])]
    """
    kept = []
    for news in candidates:
        if news["similarity"] < MIN_SIMILARITY:
            break
        if is_noise(news["title"]):
            continue
        vector = embedder.from_pgvector(news["embedding"])
        same = next((k for k in kept if same_title(k["title"], news["title"])
                     or (news.get("story_id") and k.get("story_id") == news["story_id"])
                     or similarity(k["vector"], vector) >= DUPLICATE_SIMILARITY), None)
        if same is not None:
            if news["publisher"] not in same["also_reported_by"] and news["publisher"] != same["publisher"]:
                same["also_reported_by"].append(news["publisher"])   # 同一件事：只留最相關的那則，記下別家
        elif len(kept) < MAX_NEWS:   # 滿 5 則以後還是看下去：後面的可能是已經留下的那幾件事的別家報導
            kept.append({**news, "vector": vector, "also_reported_by": []})
    return [{key: value for key, value in news.items() if key not in ("vector", "embedding")} for news in kept]


def assign_stories(news_ids: list[str], links: list[tuple[str, str, float]], known: dict[str, str]) -> dict[str, str]:
    """同一件事歸成一個 story。news_ids：要歸類的新聞，照時間排（早的在前）；links：(新聞, 比它早的同一件事的新聞, 相似度)；
    known：區間之前已經歸好的 {news_id: story_id}。回傳 {news_id: story_id}，story_id＝這件事最早的那則。

    >>> assign_stories(["b", "c", "d"], [("b", "a", 0.9), ("c", "b", 0.95), ("c", "a", 0.89)], {"a": "a0"})
    {'b': 'a0', 'c': 'a0', 'd': 'd'}
    """
    best: dict[str, tuple[float, str]] = {}
    for news_id, earlier_id, sim in links:
        if news_id not in best or sim > best[news_id][0]:
            best[news_id] = (sim, earlier_id)
    story = dict(known)
    for news_id in news_ids:
        earlier_id = best.get(news_id, (0.0, None))[1]
        story[news_id] = story.get(earlier_id, earlier_id) if earlier_id else news_id
    return {news_id: story[news_id] for news_id in news_ids}
