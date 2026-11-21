"""client 層：只負責跟 API 拿資料，不整理、不存檔（存進 raw 表的是 task.py）。API 的格式只有這裡知道。

  fetch_xxx()：打 API，回傳原始回應
  xxx_stock_ids()、watchlist_date()、company_ids()、announcement_ids()：從回應取出 producer、task 要用的值
  xxx_problem()：檢查回應，有問題回傳原因（task 存成失敗），沒問題回傳 None

  證交所：注意股、處置股、注意累計名單、每日市場成交、每日收盤行情、開休市日期、上市公司清單
  公開資訊觀測站：公司重大訊息（一家公司一個月一個請求）、重大訊息全文（一則一個請求）
"""
import calendar
import datetime

import pandas as pd
import requests

from common import date_utils
from common.config import MOPS_BASE, TWSE_BASE, TWSE_OPENAPI_BASE
from common.date_utils import DATE_FMT, roc_compact_to_yyyymmdd, roc_date_in, yyyymmdd_to_roc_compact

# ---------- HTTP ----------
HEADERS = {   # 不像瀏覽器的請求會被擋
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"),
    "Accept": "application/json",
}
TIMEOUT = 30   # 秒


def http_get(url: str, params: dict) -> requests.Response:
    return requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)


def http_post(url: str, body: dict) -> requests.Response:
    return requests.post(url, json=body, headers=HEADERS, timeout=TIMEOUT)


# ---------- 打 API：回傳原始回應 ----------
def fetch_notice(date_yyyymmdd: str) -> requests.Response:
    """注意股，某一天（YYYYMMDD）。沒開市也回 stat="OK"，只是 data 為空。"""
    return http_get(f"{TWSE_BASE}/announcement/notice", {
        "response": "json", "querytype": "1", "startDate": date_yyyymmdd, "endDate": date_yyyymmdd, "sortKind": "DATE",
    })


def fetch_punish(date_yyyymmdd: str) -> requests.Response:
    """處置股，某一天（YYYYMMDD）處置中＋當天公布的處置。

    篩的是處置期間；當天公布的下一個交易日才開始，連假可能隔 10 天左右，所以結束日多帶 14 天。
    """
    return http_get(f"{TWSE_BASE}/announcement/punish", {
        "response": "json", "startDate": date_yyyymmdd, "endDate": (pd.Timestamp(date_yyyymmdd) + pd.Timedelta(days=14)).strftime(DATE_FMT),
        "querytype": "3", "stockNo": "", "selectType": "", "proceType": "", "remarkType": "",
        "sortKind": "DATE",
    })


def fetch_punish_watchlist() -> requests.Response:
    """注意累計可能達處置標準名單，只有最新一天，不接受日期參數。"""
    return http_get(f"{TWSE_BASE}/announcement/notetrans", {"response": "json"})


def fetch_market_daily(month_yyyymm: str) -> requests.Response:
    """每日市場成交，某個月（YYYYMM）。每一列是一個有交易的日子。"""
    return http_get(f"{TWSE_BASE}/afterTrading/FMTQIK", {"response": "json", "date": f"{month_yyyymm}01"})


def fetch_stock_price(date_yyyymmdd: str) -> requests.Response:
    """每日收盤行情，某一天（YYYYMMDD）：全部上市股票、ETF（ALLBUT0999＝不含權證、牛熊證）。

    回應裡有好幾張表（指數、大盤統計…），個股收盤在 fields 有「證券代號」「收盤價」的那張。沒開市的日子沒有 tables。
    """
    return http_get(f"{TWSE_BASE}/afterTrading/MI_INDEX", {"response": "json", "date": date_yyyymmdd, "type": "ALLBUT0999"})


def fetch_holiday(year: int) -> requests.Response:
    """開休市日期，某一年（西元）。不含颱風等臨時休市。

    用 date（那年任一天）指定年份；queryYear 會被忽略，不帶 date 會回傳最新公布的年份。
    """
    return http_get(f"{TWSE_BASE}/holidaySchedule/holidaySchedule",
                    {"response": "json", "date": f"{year}0101"})


def fetch_companies() -> requests.Response:
    """上市公司清單（OpenAPI），只有當下這一份。"""
    return http_get(f"{TWSE_OPENAPI_BASE}/opendata/t187ap03_L", {})


def fetch_announcements(stock_id: str, year: int, month: int) -> requests.Response:
    """公司重大訊息，一家公司一個月（西元年月）。本月只查到今天。"""
    today = date_utils.today()
    if (year, month) == (today.year, today.month):
        last_day = today.day                              # 本月：查到今天
    else:
        last_day = calendar.monthrange(year, month)[1]    # 其他月：查到月底（monthrange 回傳 (星期幾, 天數)）

    return http_post(f"{MOPS_BASE}/t05st01", {
        "companyId": stock_id, "year": str(year - 1911), "month": str(month),
        "firstDay": "1", "lastDay": str(last_day),
    })


# 全文的 API：一般上市公司 t05st01_detail，存託憑證（DR，例如 9103 美德醫療-DR）t59sb01_detail
DETAIL_APIS = ("t05st01_detail", "t59sb01_detail")


def fetch_announcement_detail(stock_id: str, enter_yyyymmdd: str, serial_number: int, api_name: str) -> requests.Response:
    """一則重大訊息的全文（主旨、符合條款、事實發生日、說明）。

    用輸入日期（民國年月日連寫）＋當天序號指定哪一則；marketKind sii＝上市。
    api_name 照重大訊息清單上寫的（DR 和一般公司不同支），只接受 DETAIL_APIS 裡的。
    """
    if api_name not in DETAIL_APIS:
        raise ValueError(f"不認識的全文 API：{api_name}")
    return http_post(f"{MOPS_BASE}/{api_name}", {
        "marketKind": "sii", "enterDate": yyyymmdd_to_roc_compact(enter_yyyymmdd), "serialNumber": str(serial_number), "companyId": stock_id,
    })


# ---------- 從回應取值：producer、task 要用的值 ----------
def notice_stock_ids(body) -> set[str]:
    """注意股回應裡的證券代號（data 每列第 2 欄）。"""
    return {row[1].strip() for row in body.get("data") or []}


def punish_stock_ids(body) -> set[str]:
    """處置股回應裡的證券代號（data 每列第 3 欄）。"""
    return {row[2].strip() for row in body.get("data") or []}


def watchlist_date(body) -> str | None:
    """從注意累計名單的標題取出資料日期（YYYYMMDD），找不到就回傳 None。

    標題例如「115年11月12日 公布注意累計次數可能達處置標準之有價證券一覽表」
    """
    if not isinstance(body, dict):   # 被擋時回傳的是 HTML 文字，不是 dict
        return None
    data_date = roc_date_in(body.get("title", ""))
    return data_date.strftime(DATE_FMT) if data_date else None


def company_ids(body) -> set[str]:
    """上市公司清單裡的公司代號。"""
    return {company["公司代號"].strip() for company in body}


def announcement_ids(body) -> list[tuple[str, str, int, str]]:
    """重大訊息回應裡每則公告的 (公司代號, 輸入日期 YYYYMMDD, 序號, 全文 API)，查全文要用。

    取自每列最後一欄「詳細資料」，例如
    {"parameters": {"marketKind": "sii", "enterDate": "1151102", "serialNumber": "1", "companyId": "9908"},
     "apiName": "t05st01_detail"}
    """
    ids = []
    for row in (body.get("result") or {}).get("data") or []:
        params = row[-1]["parameters"]
        enter_yyyymmdd = roc_compact_to_yyyymmdd(params["enterDate"])   # "1151102" → "20261102"
        ids.append((params["companyId"].strip(), enter_yyyymmdd, int(params["serialNumber"]), row[-1]["apiName"]))
    return ids


# ---------- 檢查回應：有問題回傳原因（task 存成失敗），沒問題回傳 None ----------
# 注意股、處置股收盤後 17:30～17:45 公布（實測），留點緩衝：當天 20:00 以後抓的才算公布完整
COMPLETE_BY = datetime.time(20, 0)


def complete_at(date_yyyymmdd: str) -> datetime.datetime:
    """這天的注意股、處置股從什麼時候起算公布完整（台灣時間）。"""
    return datetime.datetime.combine(date_utils.parse_yyyymmdd(date_yyyymmdd), COMPLETE_BY, date_utils.TAIPEI)


def fetched_too_early_problem(date_yyyymmdd: str) -> str | None:
    """抓得太早（還沒公布完整）→ 回傳原因：這時回傳的空名單不是「沒人被列」，不能當成那天的資料。"""
    if date_utils.now() < complete_at(date_yyyymmdd):
        return f"{date_yyyymmdd} 還沒公布完整（{COMPLETE_BY:%H:%M} 以後才算）"
    return None


def holiday_problem(body, year: int) -> str | None:
    """開休市日期回傳的年份不對（11 月起不帶日期會回下一年）→ 回傳原因，避免把別年的資料存成這一年。"""
    if isinstance(body, dict) and body.get("queryYear") != year:
        return f"要 {year} 年，回傳的是 {body.get('queryYear')} 年"
    return None


def announcement_detail_problem(body) -> str | None:
    """全文回應裡沒有那則公告（data 是空的）→ 回傳原因。"""
    if isinstance(body, dict) and not (body.get("result") or {}).get("data"):
        return f"查無這則公告（{body.get('message')}）"
    return None
