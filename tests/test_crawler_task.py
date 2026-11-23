"""crawler/task.py：抓完存進 raw 表前的檢查（用假的回應和記憶體裡的假 raw 表）。"""
import datetime
import json
import types

import pytest
import requests

from common import date_utils, repository, runner
from common.hash_utils import url_md5
from crawler import client, task


def fake_response(body: dict) -> requests.Response:
    """做一個像 requests.get 回來的回應。"""
    url = "https://www.twse.com.tw/rwd/zh/holidaySchedule/holidaySchedule?response=json&date=20260101"
    resp = requests.Response()
    resp.status_code = 200
    resp._content = json.dumps(body).encode("utf-8")
    resp.encoding = "utf-8"
    resp.url = url
    resp.request = requests.Request("GET", url).prepare()
    return resp


def test_holiday_saved_when_year_matches(raw_store, monkeypatch):
    monkeypatch.setattr(client, "fetch_holiday", lambda year: fake_response({"queryYear": 2026, "data": []}))

    where = task.crawl_holiday(2026)

    assert where == "raw_holiday/2026"
    assert raw_store.last("holiday", "2026").ok


def test_holiday_wrong_year_saved_as_failure(raw_store, monkeypatch):
    """API 回了別年（11 月起不帶 date 會回下一年）→ 存成失敗的列，不能當成 2026 年的資料。"""
    monkeypatch.setattr(client, "fetch_holiday", lambda year: fake_response({"queryYear": 2027, "data": []}))

    with pytest.raises(repository.FetchError, match="要 2026 年，回傳的是 2027 年") as error:
        task.crawl_holiday(2026)

    row = raw_store.last("holiday", "2026")
    assert not row.ok and "2027" in row.problem
    assert not error.value.retryable   # 內容不對，重試也一樣


def test_wrong_year_does_not_overwrite_good_data(raw_store, monkeypatch):
    """已經有正確的 2026 年資料，之後抓到別年 → 兩列都留著（只新增），讀到的還是好的那份。"""
    raw_store.put("holiday", "2026", {"queryYear": 2026, "data": [["2026-01-01", "元旦", ""]]})
    monkeypatch.setattr(client, "fetch_holiday", lambda year: fake_response({"queryYear": 2027, "data": []}))

    with pytest.raises(RuntimeError):
        task.crawl_holiday(2026)

    assert [r.ok for r in raw_store.history("holiday", "2026")] == [True, False]
    assert repository.read_raw("holiday", "2026")["body"]["queryYear"] == 2026


def test_refetch_keeps_old_rows_and_reads_newest(raw_store, monkeypatch):
    """同一個 raw_key 抓兩次都成功 → 兩列都留，讀到的是後抓的。"""
    raw_store.put("holiday", "2026", {"queryYear": 2026, "data": []}, fetched_at="2026-01-02T21:00:00+08:00")
    monkeypatch.setattr(client, "fetch_holiday",
                        lambda year: fake_response({"queryYear": 2026, "data": [["2026-01-01", "元旦", ""]]}))

    task.crawl_holiday(2026)

    assert len(raw_store.history("holiday", "2026")) == 2
    assert repository.read_raw("holiday", "2026")["body"]["data"] == [["2026-01-01", "元旦", ""]]


@pytest.mark.parametrize(("status", "retryable"), [(503, True), (429, True), (404, False)])
def test_http_error_retryable(raw_store, monkeypatch, status, retryable):
    """伺服器錯誤、請求太多 → 可以重試；404 這類 → 不重試。"""
    resp = fake_response({"queryYear": 2026, "data": []})
    resp.status_code = status
    monkeypatch.setattr(client, "fetch_holiday", lambda year: resp)

    with pytest.raises(repository.FetchError) as error:
        task.crawl_holiday(2026)
    assert error.value.retryable is retryable


def test_announcement_detail_empty_data_saved_as_failure(raw_store, monkeypatch):
    """全文查不到那則公告（data 空的）→ 存成失敗，下次還會再抓。"""
    monkeypatch.setattr(client, "fetch_announcement_detail",
                        lambda *args: fake_response({"code": 200, "message": "查無資料", "result": {"data": []}}))

    with pytest.raises(RuntimeError, match="查無這則公告"):
        task.crawl_announcement_detail("9908", "20261102", 1, "t05st01_detail")

    assert not raw_store.last("announcement_detail", "9908_20261102_1").ok


def test_announcement_detail_saved(raw_store, monkeypatch):
    monkeypatch.setattr(client, "fetch_announcement_detail",
                        lambda *args: fake_response({"code": 200, "result": {"data": [["1", "115/11/05"]]}}))

    assert task.crawl_announcement_detail("9908", "20261102", 1, "t05st01_detail") == "raw_announcement_detail/9908_20261102_1"


TODAY_NOTICE = {"stat": "OK", "fields": ["證券代號", "日期"], "data": [["2330", "115.11.13"]]}


@pytest.mark.parametrize(("now", "body", "saved_ok"), [
    ("2026-11-13T16:00:00", {"stat": "OK", "data": []}, False),   # 還沒公布：空名單不是「沒人被列」
    ("2026-11-13T18:00:00", TODAY_NOTICE, True),                  # 看得到當天的資料：公布了，不用等 20:00
    ("2026-11-13T22:00:00", {"stat": "OK", "data": []}, False),   # 不知道是不是交易日：幾點都不猜，存成失敗
])
def test_notice_before_published_saved_as_failure(raw_store, monkeypatch, now, body, saved_ok):
    """注意股：看得到當天的資料就算公布了；看不到、又不是已知的休市，幾點都存成失敗（不用時間猜）。"""
    monkeypatch.setattr(date_utils, "now", lambda: datetime.datetime.fromisoformat(now).replace(tzinfo=date_utils.TAIPEI))
    monkeypatch.setattr(client, "fetch_notice", lambda day: fake_response(body))

    if saved_ok:
        task.crawl_notice("20261113")
    else:
        with pytest.raises(repository.FetchError, match="還沒公布完整"):
            task.crawl_notice("20261113")
    assert raw_store.last("notice", "20261113").ok is saved_ok


def test_stock_news_problem_reads_status_in_body():
    """FinMind 次數用完時 HTTP 可能還是 200，要看內容的 status。"""
    assert client.stock_news_problem({"msg": "success", "status": 200, "data": []}) is None
    assert client.stock_news_problem({"msg": "Requests reach the upper limit", "status": 402}) == "FinMind 回傳 402：Requests reach the upper limit"
    assert client.stock_news_problem({"msg": "success", "status": 200}) == "FinMind 回應沒有 data（格式可能改了）"


def test_stock_news_over_limit_saved_as_failure(raw_store, monkeypatch):
    """FinMind 次數用完（HTTP 200、內容 status 402）→ 存成失敗的列，不能當成那天沒有新聞。"""
    monkeypatch.setattr(client, "fetch_stock_news",
                        lambda stock_id, day: fake_response({"msg": "Requests reach the upper limit", "status": 402}))

    with pytest.raises(repository.FetchError, match="FinMind 回傳 402") as error:
        task.crawl_stock_news("2330", "20261120")

    row = raw_store.last("stock_news", "2330_20261120")
    assert not row.ok and "402" in row.problem
    assert not error.value.retryable   # 內容不對，重試也一樣


# ---------- 新聞網頁：raw_news_detail 只存 <head>，全文不存 ----------
ARTICLE = ("<html><head><meta charset='big5'><title>健策前十月獲利靚</title>"
           "<meta property='og:description' content='健策10月營收30.73億元，年增81％。'></head>"
           "<body><nav>首頁 財經</nav><article><h1>健策前十月獲利靚</h1>"
           + "<p>健策公告10月自結財務資訊，單月營收30.73億元，年增81％，稅後純益9.84億元。</p>" * 5
           + "</article></body></html>").encode("big5")
LINK = "https://www.ctee.com.tw/news/1"


def test_news_detail_head_and_text_handle_big5():
    """給 bytes：Big5 網頁也不會變亂碼；<head> 不含正文，正文去掉選單。"""
    head = client.news_detail_head(ARTICLE)
    assert "健策10月營收30.73億元" in head and "稅後純益" not in head
    text = client.news_detail_text(ARTICLE)
    assert "稅後純益9.84億元" in text and "首頁" not in text


def page_response(status=200, content=ARTICLE, content_type="text/html; charset=big5"):
    return types.SimpleNamespace(url=LINK, status_code=status, ok=status < 400,
                                 content=content, headers={"Content-Type": content_type})


def test_crawl_news_detail_saves_head_not_text(raw_store, monkeypatch):
    monkeypatch.setattr(client, "fetch_news_detail", lambda link: page_response())
    assert task.crawl_news_detail(LINK).startswith(f"raw_news_detail/{url_md5(LINK)}：全文")
    row = raw_store.last("news_detail", url_md5(LINK))
    body = json.loads(row.body)
    assert row.ok and row.url == LINK and body["final_url"] == LINK
    assert "og:description" in body["head"] and body["text_chars"] > 100
    assert "稅後純益" not in row.body   # 全文不存，只記有幾個字


def test_crawl_news_detail_stores_permanent_failures_without_failing(raw_store, monkeypatch):
    """永久的失敗存一列（ok＝否，之後不再抓），任務不算失敗：Google 轉址不打、被擋 403、不是網頁。"""
    def no_fetch(link):
        raise AssertionError("轉址連結不該打")
    monkeypatch.setattr(client, "fetch_news_detail", no_fetch)
    task.crawl_news_detail("https://news.google.com/rss/articles/abc")
    monkeypatch.setattr(client, "fetch_news_detail", lambda link: page_response(status=403))
    task.crawl_news_detail("https://example.com/news/1")
    monkeypatch.setattr(client, "fetch_news_detail", lambda link: page_response(content=b"%PDF", content_type="application/pdf"))
    task.crawl_news_detail("https://example.com/report.pdf")
    rows = [r for s, r in raw_store.rows if s == "news_detail"]
    assert [(r.ok, r.http_status, r.problem) for r in rows] == [
        (False, None, "news.google.com 的轉址連結，抓不到原文"), (False, 403, "HTTP 403"), (False, 200, "不是網頁（application/pdf）")]


def test_crawl_news_detail_temporary_failures_raise_for_retry(raw_store, monkeypatch):
    """逾時：沒有回應可存，直接丟錯誤；5xx：存完丟 FetchError 讓 runner／Celery 重試。"""
    def timeout(link):
        raise requests.Timeout("read timed out")
    monkeypatch.setattr(client, "fetch_news_detail", timeout)
    with pytest.raises(requests.Timeout):
        task.crawl_news_detail(LINK)
    assert raw_store.rows == []
    monkeypatch.setattr(client, "fetch_news_detail", lambda link: page_response(status=503))
    with pytest.raises(repository.FetchError) as error:
        task.crawl_news_detail(LINK)
    assert runner.is_retryable(error.value) and raw_store.last("news_detail", url_md5(LINK)).http_status == 503


OLD_LIST = "115年11月20日 公布注意累計次數可能達處置標準之有價證券一覽表"
NEW_LIST = "115年11月23日 公布注意累計次數可能達處置標準之有價證券一覽表"
PRICE_TODAY = {"tables": [{"fields": ["證券代號", "收盤價"], "data": [["2330", "1,000"]]}]}
PRICE_HOLIDAY = {"stat": "很抱歉，沒有符合條件的資料!"}


@pytest.mark.parametrize("source, crawl", [("notice", "crawl_notice"), ("punish", "crawl_punish"), ("stock_price", "crawl_stock_price")])
def test_known_holiday_accepted_before_20(raw_store, monkeypatch, source, crawl):
    """開休市日期上寫休市的日子：沒有資料是正常的，18:00 就算數（不會留下失敗）。"""
    monkeypatch.setattr(date_utils, "now", lambda: datetime.datetime(2026, 12, 25, 18, 0, tzinfo=date_utils.TAIPEI))
    raw_store.put("holiday", "2026", {"fields": ["日期", "名稱", "說明"], "data": [["2026-12-25", "行憲紀念日", ""]]})
    monkeypatch.setattr(client, f"fetch_{source}", lambda day: fake_response({"stat": "OK", "data": []}))
    getattr(task, crawl)("20261225")
    assert raw_store.last(source, "20261225").ok


def test_unknown_closure_is_not_guessed(raw_store, monkeypatch):
    """開休市日期上沒有的（颱風假）：幾點都存成失敗，不猜是休市（要人確認）。"""
    monkeypatch.setattr(date_utils, "now", lambda: datetime.datetime(2026, 12, 24, 22, 0, tzinfo=date_utils.TAIPEI))
    raw_store.put("holiday", "2026", {"fields": ["日期", "名稱", "說明"], "data": [["2026-12-25", "行憲紀念日", ""]]})
    monkeypatch.setattr(client, "fetch_notice", lambda day: fake_response({"stat": "OK", "data": []}))
    with pytest.raises(repository.FetchError, match="還沒公布完整"):
        task.crawl_notice("20261224")


def test_watchlist_on_known_holiday_accepted_before_20(raw_store, monkeypatch):
    monkeypatch.setattr(date_utils, "now", lambda: datetime.datetime(2026, 12, 25, 18, 0, tzinfo=date_utils.TAIPEI))
    raw_store.put("holiday", "2026", {"fields": ["日期", "名稱", "說明"], "data": [["2026-12-25", "行憲紀念日", ""]]})
    monkeypatch.setattr(client, "fetch_punish_watchlist",
                        lambda: fake_response({"stat": "OK", "title": "115年12月24日 公布注意累計次數…", "data": []}))
    task.crawl_punish_watchlist("20261225")
    assert raw_store.last("punish_watchlist", "20261224").ok


@pytest.mark.parametrize(("now", "price", "list_title", "saved_key", "saved_ok"), [
    ("2026-11-23T18:00:00", PRICE_TODAY, OLD_LIST, "20261120", False),    # 交易日、今天的還沒公布
    ("2026-11-23T18:30:00", PRICE_TODAY, NEW_LIST, "20261123", True),     # 公布了
    ("2026-11-23T21:00:00", PRICE_TODAY, OLD_LIST, "20261120", False),    # 交易日，20:00 以後還是舊的：證交所晚了，照樣失敗
    ("2026-11-23T22:00:00", PRICE_HOLIDAY, OLD_LIST, "20261120", False),  # 平日沒有收盤行情、日曆上沒寫：不猜，存成失敗
])
def test_watchlist_from_before_today_saved_as_failure(raw_store, monkeypatch, now, price, list_title, saved_key, saved_ok):
    """注意累計名單只有最新一份：不是今天的就存成失敗（不然這一輪算成功、放了記號，今天的名單就沒抓到）；
    只有確定休市的日子（週末、開休市日期）才照存前一天的。"""
    moment = datetime.datetime.fromisoformat(now).replace(tzinfo=date_utils.TAIPEI)
    monkeypatch.setattr(date_utils, "now", lambda: moment)
    raw_store.put("stock_price", "20261123", price, fetched_at=moment.isoformat())
    monkeypatch.setattr(client, "fetch_punish_watchlist", lambda: fake_response({"stat": "OK", "title": list_title, "data": []}))
    if saved_ok:
        task.crawl_punish_watchlist("20261123")
    else:
        with pytest.raises(repository.FetchError, match="還沒公布"):
            task.crawl_punish_watchlist("20261123")
    assert raw_store.last("punish_watchlist", saved_key).ok is saved_ok


PRICE_1123 = {"tables": [{"fields": ["證券代號", "收盤價"], "data": [["2330", "1,000"]]}]}


@pytest.mark.parametrize("now", ["2026-11-23T18:00:00", "2026-11-23T21:00:00"])
def test_trading_day_never_accepts_empty_notice(raw_store, monkeypatch, now):
    """交易日（收盤行情有個股資料）：注意股一定有資料，看不到就是證交所還沒公布，20:00 以後也存成失敗（不會把空名單當成沒人被列）。"""
    monkeypatch.setattr(date_utils, "now", lambda: datetime.datetime.fromisoformat(now).replace(tzinfo=date_utils.TAIPEI))
    raw_store.put("stock_price", "20261123", PRICE_1123)
    monkeypatch.setattr(client, "fetch_notice", lambda day: fake_response({"stat": "OK", "data": []}))
    with pytest.raises(repository.FetchError, match="是交易日，還沒公布完整"):
        task.crawl_notice("20261123")


def test_weekend_accepted_before_20(raw_store, monkeypatch):
    """週末：沒有資料是正常的，馬上算數。"""
    monkeypatch.setattr(date_utils, "now", lambda: datetime.datetime(2026, 11, 28, 18, 0, tzinfo=date_utils.TAIPEI))
    monkeypatch.setattr(client, "fetch_notice", lambda day: fake_response({"stat": "OK", "data": []}))
    task.crawl_notice("20261128")
    assert raw_store.last("notice", "20261128").ok


def test_saturday_with_trading_is_a_trading_day(raw_store, monkeypatch):
    """週六補行交易（開休市日期上寫「…交易日」）、有收盤行情：當交易日，注意股看不到資料就存成失敗。"""
    monkeypatch.setattr(date_utils, "now", lambda: datetime.datetime(2026, 11, 28, 18, 0, tzinfo=date_utils.TAIPEI))
    raw_store.put("holiday", "2026", {"fields": ["日期", "名稱", "說明"], "data": [["2026-11-28", "補行交易日", ""]]})
    raw_store.put("stock_price", "20261128", {"tables": [{"fields": ["證券代號"], "data": [["2330"]]}]})
    monkeypatch.setattr(client, "fetch_notice", lambda day: fake_response({"stat": "OK", "data": []}))
    with pytest.raises(repository.FetchError, match="是交易日"):
        task.crawl_notice("20261128")


def test_watchlist_uses_the_run_date_not_the_clock(raw_store, monkeypatch):
    """跑過午夜：用這一輪的那天（20261123）判斷，不會拿 11/24 的日期去比，把 11/23 的名單當成舊的。"""
    monkeypatch.setattr(date_utils, "now", lambda: datetime.datetime(2026, 11, 24, 0, 10, tzinfo=date_utils.TAIPEI))
    raw_store.put("stock_price", "20261123", PRICE_TODAY)
    monkeypatch.setattr(client, "fetch_punish_watchlist", lambda: fake_response({"stat": "OK", "title": NEW_LIST, "data": []}))
    task.crawl_punish_watchlist("20261123")
    assert raw_store.last("punish_watchlist", "20261123").ok
