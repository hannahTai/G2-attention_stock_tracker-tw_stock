"""task 層：一個任務抓一份資料、存進 raw 表，回傳存到哪裡（例如 raw_notice/20261113）（課程補充 J）。

每個任務串接 client（client.py 的 fetch_xxx）→ repository（save_raw）。
任務的三個條件：輸入都是參數（字串、數字）、不依賴其他任務、結果直接寫進資料庫。
producer.py 決定要跑哪些任務；Step 5 接 Celery 時，這裡的函式加上 @app.task()。

和補充 J 的 crawl() 不同：這裡不呼叫 transformer。原文先存進 raw 表，整理在 transform/task.py 做（ELT）。
"""
from common import repository
from crawler import client
from common.date_utils import today_yyyymmdd


# ---------- 證交所 ----------
def crawl_notice(date_yyyymmdd: str) -> str:
    """注意股：抓一天、存進 raw_notice（key 20261113）。"""
    return repository.save_raw("notice", date_yyyymmdd, client.fetch_notice(date_yyyymmdd))


def crawl_punish(date_yyyymmdd: str) -> str:
    """處置股：抓一天、存進 raw_punish（key 20261113）。"""
    return repository.save_raw("punish", date_yyyymmdd, client.fetch_punish(date_yyyymmdd))


def crawl_punish_watchlist() -> str:
    """注意累計名單：抓最新一份，key 用名單上的資料日期（週末抓到的是週五的名單）。"""
    resp = client.fetch_punish_watchlist()
    key = client.watchlist_date(repository.parse_body(resp.text))
    if key is None:   # 找不到日期：用今天，至少把回應存下來查原因
        key = today_yyyymmdd()
    return repository.save_raw("punish_watchlist", key, resp)


def crawl_holiday(year: int) -> str:
    """開休市日期：抓一年、存進 raw_holiday（key 2026）。

    回應裡的 queryYear 和要的年份不同就當失敗，避免把別年的資料存成這一年。
    """
    resp = client.fetch_holiday(year)
    body = repository.parse_body(resp.text)

    problem = None
    if isinstance(body, dict) and body.get("queryYear") != year:
        problem = f"要 {year} 年，回傳的是 {body.get('queryYear')} 年"

    return repository.save_raw("holiday", str(year), resp, problem)


def crawl_market_daily(month_yyyymm: str) -> str:
    """每日市場成交：抓一個月、存進 raw_market_daily（key 202611）。"""
    return repository.save_raw("market_daily", month_yyyymm, client.fetch_market_daily(month_yyyymm))


def crawl_companies() -> str:
    """上市公司清單：抓最新一份，key 用抓取日期（每天一份快照）。"""
    return repository.save_raw("companies", today_yyyymmdd(), client.fetch_companies())


# ---------- 觀測站 ----------
def crawl_announcements(stock_id: str, year: int, month: int) -> str:
    """重大訊息：抓一家公司一個月、存進 raw_announcements（key 1303_202611）。"""
    key = f"{stock_id}_{year:04d}{month:02d}"
    return repository.save_raw("announcements", key, client.fetch_announcements(stock_id, year, month))


def crawl_announcement_detail(stock_id: str, enter_yyyymmdd: str, serial_number: int,
                              api_name: str = "t05st01_detail") -> str:
    """重大訊息全文：抓一則、存進 raw_announcement_detail（key 9908_20261102_1＝公司_輸入日期_序號）。

    api_name：一般公司 t05st01_detail、存託憑證（DR）t59sb01_detail，照重大訊息清單上寫的。
    回應裡沒有那則公告（data 是空的）就當失敗，下次再抓。
    """
    key = f"{stock_id}_{enter_yyyymmdd}_{serial_number}"
    resp = client.fetch_announcement_detail(stock_id, enter_yyyymmdd, serial_number, api_name)
    body = repository.parse_body(resp.text)

    problem = None
    if isinstance(body, dict) and not (body.get("result") or {}).get("data"):
        problem = f"查無這則公告（{body.get('message')}）"

    return repository.save_raw("announcement_detail", key, resp, problem)
