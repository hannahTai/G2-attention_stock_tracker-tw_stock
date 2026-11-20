"""task 層（抓取）：一個任務抓一份資料、原樣存進 raw 表，回傳存到哪裡（例如 raw_notice/20261113）。

每個任務都是同樣三步：
    1. resp = client.fetch_xxx(...)：打 API
    2. key：這份存成哪個 raw_key（要檢查內容的，多一行 problem = client.xxx_problem(...)）
    3. return repository.save_raw(來源, key, resp)：成功、失敗都存；失敗的存完丟 FetchError
這裡不整理資料：原文先存進 raw 表，整理在 transform/task.py 做（ELT）。
任務的輸入都是字串、數字，不依賴其他任務。
"""
from common import repository
from common.date_utils import today_yyyymmdd
from crawler import client


# ---------- 證交所 ----------
def crawl_notice(date_yyyymmdd: str) -> str:
    """注意股：一天 → raw_notice（key 20261113）。還沒公布完整就抓的當失敗。"""
    resp = client.fetch_notice(date_yyyymmdd)
    key = date_yyyymmdd
    problem = client.fetched_too_early_problem(date_yyyymmdd)
    return repository.save_raw("notice", key, resp, problem)


def crawl_punish(date_yyyymmdd: str) -> str:
    """處置股：一天 → raw_punish（key 20261113）。還沒公布完整就抓的當失敗。"""
    resp = client.fetch_punish(date_yyyymmdd)
    key = date_yyyymmdd
    problem = client.fetched_too_early_problem(date_yyyymmdd)
    return repository.save_raw("punish", key, resp, problem)


def crawl_punish_watchlist() -> str:
    """注意累計名單：只有最新一份 → raw_punish_watchlist（key 名單上的資料日期；週末抓到的是週五的）。"""
    resp = client.fetch_punish_watchlist()
    key = client.watchlist_date(repository.parse_body(resp.text)) or today_yyyymmdd()   # 找不到日期：用今天，至少把回應存下來
    return repository.save_raw("punish_watchlist", key, resp)


def crawl_market_daily(month_yyyymm: str) -> str:
    """每日市場成交：一個月 → raw_market_daily（key 202611）。"""
    resp = client.fetch_market_daily(month_yyyymm)
    key = month_yyyymm
    return repository.save_raw("market_daily", key, resp)


def crawl_holiday(year: int) -> str:
    """開休市日期：一年 → raw_holiday（key 2026）。回傳的年份不對就當失敗。"""
    resp = client.fetch_holiday(year)
    key = str(year)
    problem = client.holiday_problem(repository.parse_body(resp.text), year)
    return repository.save_raw("holiday", key, resp, problem)


def crawl_company() -> str:
    """上市公司清單：只有最新一份 → raw_company（key 抓取日期，每天一份快照）。"""
    resp = client.fetch_companies()
    key = today_yyyymmdd()
    return repository.save_raw("company", key, resp)


# ---------- 公開資訊觀測站 ----------
def crawl_announcement(stock_id: str, year: int, month: int) -> str:
    """重大訊息：一家公司一個月 → raw_announcement（key 1303_202611）。"""
    resp = client.fetch_announcements(stock_id, year, month)
    key = f"{stock_id}_{year:04d}{month:02d}"
    return repository.save_raw("announcement", key, resp)


def crawl_announcement_detail(stock_id: str, enter_yyyymmdd: str, serial_number: int, api_name: str) -> str:
    """重大訊息全文：一則 → raw_announcement_detail（key 9908_20261102_1＝公司_輸入日期_序號）。

    api_name：一般公司 t05st01_detail、存託憑證（DR）t59sb01_detail，照重大訊息清單上寫的。
    查無那則公告就當失敗，下次再抓。
    """
    resp = client.fetch_announcement_detail(stock_id, enter_yyyymmdd, serial_number, api_name)
    key = f"{stock_id}_{enter_yyyymmdd}_{serial_number}"
    problem = client.announcement_detail_problem(repository.parse_body(resp.text))
    return repository.save_raw("announcement_detail", key, resp, problem)
