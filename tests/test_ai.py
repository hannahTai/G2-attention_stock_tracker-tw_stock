"""ai/：公告分類、新聞摘要（prompt、檢查、任務流程）、LLM 呼叫紀錄、AI producer 的任務清單。不連網路、不連資料庫、不花錢。執行：uv run pytest"""
import datetime
import json
import types

import anthropic
import httpx2
import pytest
import requests

from ai import card_summarizer, classifier, client, embedder, summarizer
from ai import producer as pa
from ai import task as ai_task
from common import date_utils, repository, runner
from common.hash_utils import key_md5
from crawler import client as crawler_client

ANNOUNCEMENT = {
    "announcement_id": key_md5("6108", "20261114", 3), "company_id": "6108", "company_name": "競國", "announce_date": datetime.date(2026, 11, 14),
    "subject": "本公司115年10月營收", "announce_clause": "第51款", "clause_name": "其他重大事項",
    "fact_date": datetime.date(2026, 11, 14), "content": "10月營收為1,520萬元，較去年同期增加31%。",
}
ANSWER = {"category": "營收、自結損益", "is_correction": False, "has_news": True, "is_negative": False,
          "summary": "10月營收1520萬元，年增31%", "reason": "公告每月營收"}


# ---------- classifier ----------
def test_categories_fit_column_and_schema_uses_them():
    assert len(classifier.CATEGORIES) == 13 == len(set(classifier.CATEGORIES))
    assert all(len(c) <= 20 for c in classifier.CATEGORIES)   # category VARCHAR(20)
    assert classifier.SCHEMA["properties"]["category"]["enum"] == classifier.CATEGORIES
    assert set(classifier.SCHEMA["required"]) == set(classifier.SCHEMA["properties"])   # 結構化輸出：每個欄位都要回答


def test_every_category_is_defined_in_prompt():
    assert all(f"- {c}：" in classifier.SYSTEM_PROMPT for c in classifier.CATEGORIES)


def test_user_message_wraps_announcement_as_data():
    message = classifier.build_user_message(ANNOUNCEMENT)
    assert message.startswith("<announcement>") and message.endswith("</announcement>")
    assert "符合條款：第51款（其他重大事項）" in message
    assert ANNOUNCEMENT["content"] in message   # 全文整段給，不截斷


def test_label_row_checks_numbers_against_announcement():
    row = classifier.to_label_row(ANNOUNCEMENT, ANSWER, "claude-haiku-5-5")
    assert row["summary_ok"] and row["prompt_version"] == classifier.PROMPT_VERSION
    assert row["announcement_id"] == ANNOUNCEMENT["announcement_id"]
    made_up = classifier.to_label_row(ANNOUNCEMENT, {**ANSWER, "summary": "10月營收0.15億元"}, "claude-haiku-5-5")
    assert not made_up["summary_ok"]   # 自己換算的數字抓得到


def test_label_row_rejects_summary_over_limit():
    long = {**ANSWER, "summary": "10月營收1520萬元，年增31%" + "，持續成長" * 10}
    assert classifier.too_long(long["summary"])
    assert not classifier.to_label_row(ANNOUNCEMENT, long, "claude-haiku-5-5")["summary_ok"]


# ---------- client（假的 Anthropic，不花錢）----------
class FakeMessages:
    def __init__(self, result):
        self.result = result
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self.result


def fake_response(answer, stop_reason="end_turn"):
    """假的 API 回應：answer 是 dict（轉成 JSON）或文字。"""
    text = json.dumps(answer, ensure_ascii=False) if isinstance(answer, dict) else answer
    usage = types.SimpleNamespace(input_tokens=1000, output_tokens=200, cache_read_input_tokens=0, cache_creation_input_tokens=0)
    return types.SimpleNamespace(stop_reason=stop_reason, usage=usage, _request_id="req_1", model="claude-haiku-5-5",
                                 content=[types.SimpleNamespace(type="text", text=text)])


def test_fetch_announcement_label_asks_for_schema_and_caches_system(monkeypatch):
    messages = FakeMessages(fake_response(ANSWER))
    monkeypatch.setattr(client, "get_anthropic", lambda: types.SimpleNamespace(messages=messages))
    conversation = [{"role": "user", "content": "公告"}]
    client.fetch_announcement_label(conversation)
    assert messages.kwargs["output_config"]["format"] == {"type": "json_schema", "schema": classifier.SCHEMA}
    assert messages.kwargs["system"] == [{"type": "text", "text": classifier.SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]
    assert messages.kwargs["messages"] == conversation


def test_announcement_label_problem_catches_refusal_truncation_and_bad_json():
    assert client.announcement_label_problem(fake_response(ANSWER)) is None
    assert client.announcement_label_problem(fake_response("", stop_reason="refusal")) == "沒有正常結束（stop_reason=refusal）"
    assert client.announcement_label_problem(fake_response("{不完整", stop_reason="max_tokens")).startswith("沒有正常結束")
    assert client.announcement_label_problem(fake_response("不是 JSON")).startswith("回傳的不是 JSON")


def test_call_record_has_tokens_and_cost():
    record = client.call_record("classify_announcement", "6108_20261114_3", "v2", fake_response(ANSWER), date_utils.now())
    assert record["input_tokens"] == 1000 and record["output_tokens"] == 200 and record["cost_usd"] == 0.0002
    assert record["request_id"] == "req_1" and record["called_at"].tzinfo is None


def test_save_llm_call_records_problem_then_raises(monkeypatch):
    """和 save_raw 一樣：有問題的也記，記完丟 FetchError（不重試）。"""
    rows = []
    monkeypatch.setattr(repository, "insert_one", lambda table, row, on_conflict="": rows.append((table, row)))
    record = {"task": "classify_announcement", "ref_key": ANNOUNCEMENT["announcement_id"]}
    assert repository.save_llm_call(record) == f"ai_llm_call/{ANNOUNCEMENT['announcement_id']}"
    with pytest.raises(repository.FetchError) as error:
        repository.save_llm_call(record, "沒有正常結束（stop_reason=refusal）")
    assert not error.value.retryable
    assert [(t, r["status"], r["error"]) for t, r in rows] == [
        ("ai_llm_call", "success", None), ("ai_llm_call", "failure", "沒有正常結束（stop_reason=refusal）")]


def test_claude_api_errors_retry_only_when_temporary():
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    status = lambda code: anthropic.APIStatusError("x", response=httpx2.Response(code, request=request), body=None)
    assert runner.is_retryable(anthropic.APIConnectionError(request=request))
    assert runner.is_retryable(anthropic.RateLimitError("x", response=httpx2.Response(429, request=request), body=None))
    assert runner.is_retryable(status(529)) and not runner.is_retryable(status(400))


# ---------- classify_announcement 任務 ----------
@pytest.fixture
def labels(monkeypatch):
    """假的 ai_announcement_label、ai_llm_call：記下寫入的列；已經有的 announcement_id 放在 existing。"""
    store = types.SimpleNamespace(rows=[], calls=[], existing=set())
    monkeypatch.setattr(repository, "has_announcement_label", lambda announcement_id, version: announcement_id in store.existing)
    monkeypatch.setattr(repository, "insert_announcement_label", store.rows.append)
    monkeypatch.setattr(repository, "save_llm_call", lambda row, problem=None: store.calls.append(row))
    return store


def answer_with(monkeypatch, *answers):
    """client.fetch_announcement_label 依序回這些回答；回傳每次收到的 messages。"""
    asked = []

    def fake_fetch(messages):
        asked.append(messages)
        return fake_response(answers[min(len(asked), len(answers)) - 1])
    monkeypatch.setattr(client, "fetch_announcement_label", fake_fetch)
    return asked


def test_classify_writes_label_and_records_call(monkeypatch, labels):
    monkeypatch.setattr(repository, "read_announcement", lambda *key: ANNOUNCEMENT)
    answer_with(monkeypatch, ANSWER)
    result = ai_task.classify_announcement("6108", "20261114", 3)
    assert result == "ai_announcement_label/6108_20261114_3：營收、自結損益（有新消息）"
    assert labels.rows[0]["category"] == "營收、自結損益" and labels.rows[0]["announcement_id"] == ANNOUNCEMENT["announcement_id"]
    assert labels.calls[0]["ref_key"] == ANNOUNCEMENT["announcement_id"]   # ref_key 記結果表的代號
    assert labels.calls[0]["prompt_version"] == classifier.PROMPT_VERSION


def test_classify_asks_once_more_when_summary_too_long(monkeypatch, labels):
    """摘要超過上限：帶著上次的回答請它縮短，只重問一次；第二次的回答才存。"""
    monkeypatch.setattr(repository, "read_announcement", lambda *key: ANNOUNCEMENT)
    asked = answer_with(monkeypatch, {**ANSWER, "summary": "一" * 61}, ANSWER)
    ai_task.classify_announcement("6108", "20261114", 3)
    assert [m["role"] for m in asked[0]] == ["user"] and [m["role"] for m in asked[1]] == ["user", "assistant", "user"]
    assert "61 個字" in asked[1][-1]["content"]
    assert labels.rows[0]["summary"] == ANSWER["summary"] and labels.rows[0]["summary_ok"] and len(labels.calls) == 2


def test_classify_still_too_long_is_saved_but_hidden(monkeypatch, labels):
    monkeypatch.setattr(repository, "read_announcement", lambda *key: ANNOUNCEMENT)
    answer_with(monkeypatch, {**ANSWER, "summary": "一" * 61})
    ai_task.classify_announcement("6108", "20261114", 3)
    assert not labels.rows[0]["summary_ok"]


def test_classify_keeps_first_answer_when_shorten_fails(monkeypatch, labels):
    """縮短那次失敗（拒答、API 錯誤）：用第一次的回答照存（已經付過錢），摘要太長 → summary_ok＝否。"""
    monkeypatch.setattr(repository, "read_announcement", lambda *key: ANNOUNCEMENT)
    calls = []

    def fake_fetch(messages):
        calls.append(messages)
        if len(calls) == 2:
            raise repository.FetchError("沒有正常結束（stop_reason=refusal）", retryable=False)
        return fake_response({**ANSWER, "summary": "一" * 61})
    monkeypatch.setattr(client, "fetch_announcement_label", fake_fetch)
    ai_task.classify_announcement("6108", "20261114", 3)
    assert len(calls) == 2 and labels.rows[0]["summary"] == "一" * 61 and not labels.rows[0]["summary_ok"]


def test_classify_skips_labeled_and_missing_detail(monkeypatch, labels):
    def no_call(messages):
        raise AssertionError("不該呼叫 LLM")
    monkeypatch.setattr(client, "fetch_announcement_label", no_call)
    labels.existing.add(key_md5("6108", "20261114", 3))
    assert "已經分類過" in ai_task.classify_announcement("6108", "20261114", 3)
    monkeypatch.setattr(repository, "read_announcement", lambda *key: None)
    assert "還沒有全文" in ai_task.classify_announcement("6108", "20261114", 4)
    assert labels.rows == []


# ---------- AI producer ----------
def test_classify_jobs_use_yyyymmdd(monkeypatch):
    monkeypatch.setattr(repository, "list_unlabeled_announcements",
                        lambda start, end, version: [("9908", datetime.date(2026, 11, 2), 1)])
    [(func, kwargs)] = pa.build_classify_jobs("20261101", "20261120")
    assert func is ai_task.classify_announcement
    assert kwargs == {"company_id": "9908", "enter_yyyymmdd": "20261102", "serial_number": 1}


# ---------- 新聞摘要：summarizer ----------
NEWS = {"news_id": "5583cdf4c2064913b0e08ee9e90f450e", "link": "https://www.ctee.com.tw/news/1", "title": "健策前十月獲利靚",
        "publisher": "工商時報", "news_date": datetime.date(2026, 11, 20)}
ARTICLE_TEXT = "健策公告10月自結財務資訊，單月營收30.73億元，年增81％，稅後純益9.84億元。" * 5
SHORT_ANSWER = {"summary": "健策10月營收30.73億元，年增81％"}
LONG_ANSWER = {"summary": "健策10月營收30.73億元，" + "年增81％，" * 50}


def test_news_summary_row_checks_numbers_and_never_keeps_text():
    """摘要裡的數字要出現在原文；存的那列只有摘要，沒有全文。"""
    row = summarizer.to_summary_row(NEWS["news_id"], {"summary": " 健策10月營收30.73億元，年增81％ "}, ARTICLE_TEXT, 80, "claude-haiku-5-5")
    assert row["summary"] == "健策10月營收30.73億元，年增81％" and row["summary_ok"] and row["prompt_version"] == summarizer.PROMPT_VERSION
    assert ARTICLE_TEXT not in str(row)
    assert not summarizer.to_summary_row(NEWS["news_id"], {"summary": "營收3073000萬元"}, ARTICLE_TEXT, 80, "m")["summary_ok"]


def test_news_summary_limit_scales_with_article():
    """上限是文章的三成：短文章摘要短、長文章最多 1200 字（欄位大小）；上限寫在給 LLM 的訊息最後。"""
    assert summarizer.summary_limit("字" * 600) == 180 and summarizer.summary_limit("字" * 9000) == summarizer.SUMMARY_MAX_CHARS
    assert summarizer.build_user_message(NEWS, "字" * 600, 180).endswith("summary 最多 153 個字。")   # 上限的八五成


def test_news_summary_message_wraps_article_and_cuts_long_text():
    message = summarizer.build_user_message(NEWS, "甲" * (summarizer.MAX_TEXT_CHARS + 500), 1200)
    assert message.startswith("<article>\n標題：健策前十月獲利靚") and "</article>" in message
    assert message.count("甲") == summarizer.MAX_TEXT_CHARS


# ---------- 新聞摘要：client ----------
def test_fetch_news_summary_uses_summary_schema(monkeypatch):
    messages = FakeMessages(fake_response(SHORT_ANSWER))
    monkeypatch.setattr(client, "get_anthropic", lambda: types.SimpleNamespace(messages=messages))
    client.fetch_news_summary([{"role": "user", "content": "新聞"}])
    assert messages.kwargs["output_config"]["format"] == {"type": "json_schema", "schema": summarizer.SCHEMA}
    assert messages.kwargs["system"][0]["text"] == summarizer.SYSTEM_PROMPT
    assert client.news_summary_problem(fake_response("", stop_reason="refusal")) == "沒有正常結束（stop_reason=refusal）"


# ---------- summarize_news 任務 ----------
def page(status=200, content_type="text/html"):
    """假的新聞網頁回應（像 crawler_client.fetch_news_detail 回來的）。"""
    return types.SimpleNamespace(status_code=status, ok=status < 400, content=b"<html>", headers={"Content-Type": content_type},
                                 raise_for_status=lambda: (_ for _ in ()).throw(requests.HTTPError(f"HTTP {status}")))


@pytest.fixture
def summaries(monkeypatch):
    """假的 stage_news、ai_news_summary、ai_llm_call；網頁回 store.page，LLM 依序回 store.answers。"""
    store = types.SimpleNamespace(rows=[], calls=[], asked=[], existing=set(), page=page(), answers=[SHORT_ANSWER])
    monkeypatch.setattr(repository, "has_news_summary", lambda news_id, version: news_id in store.existing)
    monkeypatch.setattr(repository, "read_news", lambda news_id: NEWS if news_id == NEWS["news_id"] else None)
    monkeypatch.setattr(repository, "insert_news_summary", store.rows.append)
    monkeypatch.setattr(repository, "save_llm_call", lambda row, problem=None: store.calls.append(row))
    monkeypatch.setattr(crawler_client, "fetch_news_detail", lambda link: store.page)
    monkeypatch.setattr(crawler_client, "news_detail_text", lambda content: ARTICLE_TEXT)

    def fake_fetch(messages):
        store.asked.append(messages)
        answer = store.answers[min(len(store.asked), len(store.answers)) - 1]
        if isinstance(answer, Exception):
            raise answer
        return fake_response(answer)
    monkeypatch.setattr(client, "fetch_news_summary", fake_fetch)
    return store


def test_summarize_news_reads_page_now_and_stores_only_summary(summaries):
    result = ai_task.summarize_news(NEWS["news_id"])
    assert result.startswith(f"ai_news_summary/{NEWS['news_id']}：健策10月營收")
    assert ARTICLE_TEXT[:50] in summaries.asked[0][0]["content"]       # 全文有給 LLM
    [row] = summaries.rows
    assert row["summary_ok"] and ARTICLE_TEXT not in str(row)           # 存的只有摘要
    assert summaries.calls[0]["task"] == "summarize_news" and summaries.calls[0]["ref_key"] == NEWS["news_id"]


def test_summarize_news_asks_once_more_when_too_long(summaries):
    """摘要太長：帶著上次的回答請它縮短，只重問一次。"""
    summaries.answers = [LONG_ANSWER, SHORT_ANSWER]
    ai_task.summarize_news(NEWS["news_id"])
    limit = summarizer.summary_limit(ARTICLE_TEXT)
    assert len(summaries.asked) == 2 and f"超過 {limit} 個字" in summaries.asked[1][-1]["content"]
    assert summaries.rows[0]["summary"] == SHORT_ANSWER["summary"] and summaries.rows[0]["summary_ok"]


def test_summarize_news_keeps_first_answer_when_shorten_fails(summaries):
    """縮短那次失敗（拒答、API 錯誤）：用第一次的回答照存（已經付過錢），摘要太長 → summary_ok＝否。"""
    summaries.answers = [LONG_ANSWER, repository.FetchError("沒有正常結束（stop_reason=refusal）", retryable=False)]
    assert "縮短失敗" in ai_task.summarize_news(NEWS["news_id"])
    assert summaries.rows[0]["summary"] == LONG_ANSWER["summary"].strip() and not summaries.rows[0]["summary_ok"]


def test_summarize_news_skips_without_asking(summaries, monkeypatch):
    """摘要過、不在 stage_news、網頁打不開（被擋）、全文太短：跳過，不花錢問 LLM。"""
    summaries.existing.add(NEWS["news_id"])
    assert "已經摘要過" in ai_task.summarize_news(NEWS["news_id"])
    assert "不在 stage_news" in ai_task.summarize_news("0" * 32)
    summaries.existing.clear()
    summaries.page = page(status=403)
    assert "網頁打不開（HTTP 403）" in ai_task.summarize_news(NEWS["news_id"])
    summaries.page = page()
    monkeypatch.setattr(crawler_client, "news_detail_text", lambda content: "太短")
    assert "全文太短" in ai_task.summarize_news(NEWS["news_id"])
    assert summaries.asked == [] and summaries.rows == []


def test_summarize_news_server_error_retries(summaries):
    """網頁 5xx：丟 requests 的錯誤，runner／Celery 會重試；不問 LLM。"""
    summaries.page = page(status=503)
    with pytest.raises(requests.HTTPError) as error:
        ai_task.summarize_news(NEWS["news_id"])
    assert runner.is_retryable(error.value) and summaries.asked == []


# ---------- AI producer：新聞摘要 ----------
def test_summary_jobs_look_back_like_crawler(monkeypatch):
    """crawler 往前多抓 NEWS_LOOKBACK_DAYS 天的新聞，摘要也往前多看。"""
    monkeypatch.setattr(pa, "NEWS_LOOKBACK_DAYS", 7)
    asked = {}

    def fake_list(start, end, version, min_chars):
        asked.update(start=start, end=end, min_chars=min_chars)
        return [NEWS["news_id"]]
    monkeypatch.setattr(repository, "list_unsummarized_news", fake_list)
    [(func, kwargs)] = pa.build_summary_jobs("20261120", "20261120")
    assert func is ai_task.summarize_news and kwargs == {"news_id": NEWS["news_id"]}
    assert asked == {"start": datetime.date(2026, 11, 13), "end": datetime.date(2026, 11, 20), "min_chars": summarizer.MIN_TEXT_CHARS}


# ---------- 新聞 RAG：embed_news ----------
def test_embed_news_turns_title_and_summary_into_vectors(monkeypatch):
    """標題＋新聞摘要（沒有摘要用導言）→ 向量，一批寫進 ai_news_embedding；文字沒變的跳過，摘要後來才寫好的重轉。不載入真的模型。"""
    rows = [{"news_id": "n1", "title": "健策營收創高", "ai_summary": "10月營收年增81％", "lead": "導言", "input_hash": None},
            {"news_id": "n2", "title": "台積電法說", "ai_summary": None, "lead": None, "input_hash": key_md5("台積電法說")}]
    monkeypatch.setattr(repository, "list_news_to_embed", lambda start, end, model: rows)
    asked = []
    monkeypatch.setattr(embedder, "embed", lambda texts: asked.extend(texts) or [[1.0, 0.0] for _ in texts])
    written = []
    monkeypatch.setattr(repository, "save_news_embeddings", written.extend)
    assert ai_task.embed_news("20261120") == "ai_news_embedding/20261120：1 則"   # n2 的文字沒變
    assert asked == ["健策營收創高\n10月營收年增81％"]
    assert [(r["news_id"], r["embedding"], r["input_hash"]) for r in written] == [("n1", "[1.0,0.0]", key_md5(asked[0]))]
    rows[0]["input_hash"] = key_md5(asked[0])
    assert "沒有要轉的新聞" in ai_task.embed_news("20261120")


def test_embed_jobs_look_back_like_crawler(monkeypatch):
    monkeypatch.setattr(pa, "NEWS_LOOKBACK_DAYS", 2)
    assert [kw["date_yyyymmdd"] for _, kw in pa.build_embed_jobs("20261120", "20261120")] == ["20261118", "20261119", "20261120"]


# ---------- 卡片 AI 摘要：card_summarizer ----------
CARD = {"trade_date": datetime.date(2026, 11, 20), "stock_id": "3653", "stock_name": "健策", "is_listed_today": True,
        "on_watchlist": False, "clauses": "1", "reason_text": "最近六個營業日累積收盤價漲幅達32.5%", "change_pct": 9.96,
        "punish": {"period_start": datetime.date(2026, 11, 17), "period_end": datetime.date(2026, 11, 23),
                   "match_interval_minutes": 5}}
CARD_ANNOUNCEMENTS = [{"announce_date": datetime.date(2026, 11, 14), "subject": "公告10月營收", "category": "營收、自結損益",
                       "has_news": True, "is_negative": False, "ai_summary": "10月營收30.73億元，年增81％"}]
CARD_NEWS = [{"news_id": "n1", "news_date": datetime.date(2026, 11, 19), "publisher": "UDN", "title": "健策接獲AI伺服器散熱訂單"}]
CARD_ANSWER = {"summary": "最近六個交易日漲了 32.5%。期間公司公告10月營收年增81％；新聞提到接獲AI伺服器散熱訂單（新聞 1）。",
               "news_used": [1]}


def test_card_message_has_facts_but_no_rules():
    """款次換成白話、官方寫的情況（營業日換成交易日）、處置中的期間和撮合間隔、今天的漲跌幅；
    不寫第幾款、離處置還差幾次（規定），不放收盤價本身，也不放日期本身。"""
    message = card_summarizer.build_user_message(CARD, CARD_ANNOUNCEMENTS, CARD_NEWS)
    assert "今天被列為注意股，原因：最近一段期間股價累積漲（跌）幅太大" in message and "款" not in message
    assert "官方寫的情況：最近六個交易日累積收盤價漲幅達32.5%" in message
    assert "處置中：11/17～11/23，期間每 5 分鐘才撮合一次" in message and "今天漲跌：漲 9.96%" in message
    assert "還差" not in message and "營業日" not in message and "今天收盤" not in message
    assert "營收、自結損益，有新消息：10月營收30.73億元" in message and "[1] 2026-11-19 UDN：健策接獲AI伺服器散熱訂單" in message
    assert "next" not in message and "2026-11-20" not in message   # 輸入一樣的兩天，訊息也一樣，可以沿用


def test_card_row_maps_news_numbers_to_ids_and_checks_numbers():
    message = card_summarizer.build_user_message(CARD, CARD_ANNOUNCEMENTS, CARD_NEWS)
    row = card_summarizer.to_card_row(CARD, "cid", CARD_ANSWER, CARD_NEWS, message, "claude-haiku-5-5")
    assert row["summary_ok"] and row["news_ids"] == ["n1"] and row["input_hash"] == card_summarizer.input_hash(message)
    assert "（新聞 1）" not in row["summary"]   # 編號拿掉，網頁另外列新聞
    made_up = card_summarizer.to_card_row(CARD, "cid", {**CARD_ANSWER, "summary": "最近六個交易日漲了 39%"}, CARD_NEWS, message, "m")
    assert not made_up["summary_ok"]   # 39 是編的


# ---------- summarize_card 任務 ----------
@pytest.fixture
def cards(monkeypatch):
    """假的卡片事實（stage）、公告、新聞 RAG、ai_card_summary、ai_llm_call。"""
    store = types.SimpleNamespace(rows=[], calls=[], asked=[], existing=set(), previous=None, news=list(CARD_NEWS))
    monkeypatch.setattr(repository, "has_card_summary", lambda card_id, version: card_id in store.existing)
    monkeypatch.setattr(repository, "card_facts", lambda stock_id, day: CARD if stock_id == "3653" else None)
    monkeypatch.setattr(repository, "recent_announcements", lambda stock_id, start, end: CARD_ANNOUNCEMENTS)
    monkeypatch.setattr(repository, "previous_card_summary", lambda stock_id, before, version: store.previous)
    monkeypatch.setattr(repository, "insert_card_summary", store.rows.append)
    monkeypatch.setattr(repository, "save_llm_call", lambda row, problem=None: store.calls.append(row))
    monkeypatch.setattr(ai_task, "card_news", lambda card, announcements: store.news)

    def fake_fetch(messages):
        store.asked.append(messages)
        return fake_response(CARD_ANSWER)
    monkeypatch.setattr(client, "fetch_card_summary", fake_fetch)
    return store


def test_summarize_card_writes_summary_with_news(cards):
    assert ai_task.summarize_card("3653", "20261120").startswith("ai_card_summary/3653_20261120：")
    [row] = cards.rows
    assert row["card_id"] == key_md5("3653", "20261120") and row["summary_ok"] and row["news_ids"] == ["n1"]
    assert cards.calls[0]["task"] == "summarize_card" and cards.calls[0]["ref_key"] == row["card_id"]


def test_summarize_card_reuses_yesterday_when_input_is_the_same(cards):
    """輸入的雜湊值和前一天一樣：沿用前一天的摘要，不呼叫 API。"""
    message = card_summarizer.build_user_message(CARD, CARD_ANNOUNCEMENTS, CARD_NEWS)
    cards.previous = {"card_id": "old", "prompt_version": card_summarizer.PROMPT_VERSION, "stock_id": "3653",
                      "card_date": datetime.date(2026, 11, 19), "summary": "昨天的摘要", "news_ids": ["n1"],
                      "input_hash": card_summarizer.input_hash(message), "summary_ok": True, "reused": False, "model": "m"}
    assert "沿用" in ai_task.summarize_card("3653", "20261120")
    [row] = cards.rows
    assert cards.asked == [] and row["summary"] == "昨天的摘要" and row["reused"] and row["card_date"] == datetime.date(2026, 11, 20)


def test_summarize_card_still_writes_when_news_fails(cards, monkeypatch):
    """新聞是選擇性的輸入：新聞 RAG 出錯就不帶新聞，照樣寫。"""
    def broken(card, announcements):
        raise RuntimeError("模型載不起來")
    monkeypatch.setattr(ai_task, "card_news", broken)
    assert "沒有帶新聞" in ai_task.summarize_card("3653", "20261120")
    assert "近幾天沒有相關的新聞" in cards.asked[0][0]["content"]


def test_summarize_card_skips_done_and_missing(cards):
    cards.existing.add(key_md5("3653", "20261120"))
    assert "已經寫過" in ai_task.summarize_card("3653", "20261120")
    assert "不在注意累計名單上，跳過" in ai_task.summarize_card("9999", "20261120")
    assert cards.asked == [] and cards.rows == []


def test_card_jobs_use_yyyymmdd(monkeypatch):
    monkeypatch.setattr(repository, "list_unsummarized_cards", lambda start, end, version: [("3653", datetime.date(2026, 11, 20))])
    [(func, kwargs)] = pa.build_card_jobs("20261120", "20261120")
    assert func is ai_task.summarize_card and kwargs == {"stock_id": "3653", "date_yyyymmdd": "20261120"}
