"""client 層：只負責跟 API 拿資料，不整理、不存檔（存進 raw 表的是 task.py）。API 的格式只有這裡知道。

  fetch_xxx()：打 API，回傳原始回應
  xxx_stock_ids()、watchlist_date()、company_ids()、announcement_ids()、stock_news_links()：從回應取出 producer、task 要用的值
  news_detail_head()、news_detail_text()：從新聞網頁取 <head>（存進 raw）、抽全文（全文只在記憶體裡，不存）
  xxx_problem()：檢查回應，有問題回傳原因（task 存成失敗），沒問題回傳 None

  證交所：注意股、處置股、注意累計名單、每日市場成交、每日收盤行情、開休市日期、上市公司清單
  公開資訊觀測站：公司重大訊息（一家公司一個月一個請求）、重大訊息全文（一則一個請求）
  FinMind：個股新聞（一檔一天一個請求）
  新聞網站：新聞網頁（一則一個請求；只存 <head>）
"""
import calendar
import datetime
import re
import urllib.parse

import pandas as pd
import requests
import trafilatura
import trafilatura.utils

from common import date_utils
from common.config import FINMIND_BASE, FINMIND_TOKEN, MOPS_BASE, TWSE_BASE, TWSE_OPENAPI_BASE
from common.date_utils import DATE_FMT, roc_compact_to_yyyymmdd, roc_date_in, yyyymmdd_to_roc_compact

# ---------- HTTP ----------
HEADERS = {   # 不像瀏覽器的請求會被擋
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"),
    "Accept": "application/json",
}
TIMEOUT = 30   # 秒


def http_get(url: str, params: dict, headers: dict | None = None) -> requests.Response:
    return requests.get(url, params=params, headers={**HEADERS, **(headers or {})}, timeout=TIMEOUT)


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


def fetch_stock_news(stock_id: str, date_yyyymmdd: str) -> requests.Response:
    """個股新聞（FinMind TaiwanStockNews），一檔一天（UTC 的一天）。一次只能查一天（給 end_date 會被拒絕）。

    token 放在 header（不放網址），存進 raw 表的網址才不會帶著 token；沒設 FINMIND_TOKEN 也抓得到，只是次數上限比較低。
    """
    headers = {"Authorization": f"Bearer {FINMIND_TOKEN}"} if FINMIND_TOKEN else None
    day = date_utils.parse_yyyymmdd(date_yyyymmdd)
    return http_get(f"{FINMIND_BASE}/data", {
        "dataset": "TaiwanStockNews", "data_id": stock_id, "start_date": day.isoformat(),
    }, headers)


def fetch_news_detail(link: str) -> requests.Response:
    """一則新聞的網頁（HTML）。"""
    return http_get(link, {}, {"Accept": "text/html,application/xhtml+xml"})


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


# 論壇貼文的連結（不是新聞，不抓網頁）
FORUM_PATH = "cmoney.tw/forum/"


def stock_news_links(body) -> list[str]:
    """個股新聞回應裡每則新聞的連結（查網頁要用）；CMoney 股市爆料同學會是論壇貼文，不是新聞，不算。

    >>> stock_news_links({"data": [{"link": " https://udn.com/1 "}, {"link": "https://www.cmoney.tw/forum/article/2"}]})
    ['https://udn.com/1']
    """
    return [row["link"].strip() for row in body.get("data") or [] if FORUM_PATH not in row["link"]]   # 和 transform 一樣去空白，md5 才對得上


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


def news_detail_head(page: bytes) -> str:
    """新聞網頁的 <head>：標題、meta 摘要（og:description）、發布時間，不含正文。存進 raw_news_detail（transform 從這裡取導言）。

    給 bytes 不給 str：trafilatura 自己判斷編碼（Big5、沒標編碼的 UTF-8 網頁才不會變亂碼）。

    >>> news_detail_head("<html><head><title>健策</title></head><body>正文</body></html>".encode("big5"))
    '<head><title>健策</title></head>'
    """
    match = re.search(r"<head\b.*?</head>", trafilatura.utils.decode_file(page), re.S | re.I)
    return match.group(0) if match else ""


def news_detail_text(page: bytes) -> str | None:
    """新聞網頁的正文（去掉選單、廣告、推薦文章）。只在記憶體裡用，不存進資料庫（著作權）：
    crawl_news_detail 只記有幾個字，ai 的 summarize_news 當場交給 LLM 摘要。"""
    return trafilatura.extract(page, include_comments=False, include_tables=False)


# ---------- 檢查回應：有問題回傳原因（task 存成失敗），沒問題回傳 None ----------
# 注意股、處置股收盤後 17:30～17:45 公布，收盤行情 14:30 左右。看得到當天的資料就算公布了（is_published）；
# 看不到的，看那天是不是交易日（day_status）：休市才收；交易日和不知道的都不收（存成失敗，下一輪再抓），
# 不用時間猜：猜錯會把「還沒公布」的空名單當成「沒人被列」
TRADING, CLOSED, UNKNOWN = "trading", "closed", "unknown"   # day_status 的結果


def update_date(body) -> datetime.date | None:
    """處置股標題上的「資訊更新日期：115/11/23」→ date；沒有就回傳 None。
    查過去的日子，這個日期是抓的那天（最新一次更新），不是查的那天。

    >>> update_date({"title": "公布處置有價證券資訊 (115/07/01 至 115/07/15)\\n資訊更新日期：115/11/16"})
    datetime.date(2026, 11, 16)
    >>> update_date({"title": "公布處置有價證券資訊 (115/11/22 至 115/12/06)"}) is None
    True
    """
    found = re.search(r"資訊更新日期：\s*(\d+/\d+/\d+)", (body.get("title") or "") if isinstance(body, dict) else "")
    return date_utils.roc_to_date(found.group(1)) if found else None


def is_published(source: str, body, date_yyyymmdd: str) -> bool:
    """回應裡看得到這天的資料（證交所公布了）：
    注意股有一列的日期是這天；處置股的「資訊更新日期」在這天或之後（查當天要是當天，查過去的是抓的那天）；
    收盤行情有個股的表、而且有資料。

    >>> is_published("notice", {"fields": ["證券代號", "日期"], "data": [["2330", "115.11.23"]]}, "20261123")
    True
    >>> is_published("notice", {"fields": ["證券代號", "日期"], "data": []}, "20261123")
    False
    >>> is_published("punish", {"title": "公布處置有價證券資訊 (115/11/23 至 115/12/07)\\n資訊更新日期：115/11/23"}, "20261123")
    True
    >>> is_published("punish", {"title": "公布處置有價證券資訊 (115/11/23 至 115/12/07)"}, "20261123")
    False
    >>> is_published("punish", {"title": "(115/07/01 至 115/07/15)\\n資訊更新日期：115/11/16"}, "20260701")   # 回補過去的日子
    True
    >>> is_published("punish", {"title": "(115/11/24 至 115/12/08)\\n資訊更新日期：115/11/23"}, "20261124")   # 今天的還沒更新
    False
    >>> is_published("stock_price", {"tables": [{"fields": ["證券代號", "收盤價"], "data": [["2330", "1,000"]]}]}, "20261123")
    True
    >>> is_published("stock_price", {"stat": "很抱歉，沒有符合條件的資料!"}, "20261123")
    False
    """
    day = date_utils.parse_yyyymmdd(date_yyyymmdd)
    if not isinstance(body, dict):
        return False
    if source == "notice":
        fields = body.get("fields") or []
        if "日期" not in fields:
            return False
        at = fields.index("日期")
        return any(len(row) > at and str(row[at]).strip() == date_utils.to_roc(day, ".") for row in body.get("data") or [])
    if source == "punish":
        updated = update_date(body)
        return updated is not None and updated >= day
    if source == "stock_price":
        return any("證券代號" in (table.get("fields") or []) and table.get("data") for table in body.get("tables") or [])
    raise ValueError(f"不認識的來源：{source}")


def calendar_days(body) -> tuple[set[str], set[str]]:
    """開休市日期的回應 → (休市的日子, 有交易的日子)，都是 YYYYMMDD。
    名稱是「…交易日」的（開始交易日、最後交易日、補行交易日）那天有交易，其他都是休市；
    和 analyze 讀 stage_holiday 的規則一樣（holiday_name NOT LIKE '%交易日'）。不含颱風假這類臨時休市。

    >>> calendar_days({"fields": ["日期", "名稱", "說明"], "data": [
    ...     ["2027-01-01", "中華民國開國紀念日", ""], ["2027-01-04", "國曆新年開始交易日", ""]]})
    ({'20270101'}, {'20270104'})
    """
    if not isinstance(body, dict) or "日期" not in (body.get("fields") or []) or "名稱" not in body["fields"]:
        return set(), set()
    at_date, at_name = body["fields"].index("日期"), body["fields"].index("名稱")
    closed, open_ = set(), set()
    for row in body.get("data") or []:
        if len(row) <= max(at_date, at_name) or not isinstance(row[at_date], str) or not isinstance(row[at_name], str):
            continue   # 壞掉的列（太短、空值）跳過，不要讓整年的證交所任務都失敗
        (open_ if row[at_name].strip().endswith("交易日") else closed).add(row[at_date].replace("-", ""))
    return closed, open_


def market_confirms_closed(date_yyyymmdd: str, market_body) -> bool:
    """事後確認那天休市：這個月的市場成交已經有之後的交易日，卻沒有那天（颱風假這類臨時休市）。
    當天還不知道（市場成交最多到前一天），下一個交易日的資料出來後才確認得了。

    >>> market = {"fields": ["日期", "成交股數"], "data": [["115/09/09", "1"], ["115/09/11", "1"]]}
    >>> market_confirms_closed("20260910", market), market_confirms_closed("20260911", market)   # 9/10 颱風假、9/11 有交易
    (True, False)
    >>> market_confirms_closed("20260914", market)   # 之後還沒有資料：還不知道
    False
    """
    if not isinstance(market_body, dict) or "日期" not in (market_body.get("fields") or []):
        return False
    at = market_body["fields"].index("日期")
    dates = set()
    for row in market_body.get("data") or []:
        try:
            dates.add(date_utils.roc_to_date(str(row[at])))
        except (IndexError, ValueError):
            continue
    day = date_utils.parse_yyyymmdd(date_yyyymmdd)
    return day not in dates and any(d > day for d in dates)


def should_trade(date_yyyymmdd: str, closed: set[str], open_: set[str]) -> bool:
    """日曆上這天應該有交易：開休市日期上寫休市的不會；寫有交易的會（週末的補行交易也是）；其他看是不是週末。

    >>> should_trade("20261123", set(), set()), should_trade("20261128", set(), set())   # 平日、週六
    (True, False)
    >>> should_trade("20261225", {"20261225"}, set()), should_trade("20261128", set(), {"20261128"})   # 休市、週六補行交易
    (False, True)
    """
    if date_yyyymmdd in closed:
        return False
    if date_yyyymmdd in open_:
        return True
    return date_utils.parse_yyyymmdd(date_yyyymmdd).weekday() < 5


def day_status(date_yyyymmdd: str, price_body, closed: set[str], open_: set[str], market_body=None) -> str:
    """這天是不是交易日：先看日曆（應不應該有交易），再用交易確認（收盤行情有沒有個股資料）。

    price_body：這天收盤行情的回應（還沒抓到為 None）；closed、open_：開休市日期上休市、有交易的日子（calendar_days）；
    market_body：那個月的市場成交（事後確認臨時休市用，market_confirms_closed）。
    日曆上休市 → CLOSED；應該有交易、收盤行情有資料 → TRADING；
    應該有交易、收盤行情沒有：市場成交已經有之後的交易日卻沒有這天 → CLOSED（颱風假，事後確認），不然 → UNKNOWN。

    >>> price = {"tables": [{"fields": ["證券代號"], "data": [["2330"]]}]}
    >>> day_status("20261123", price, set(), set()), day_status("20261128", price, set(), {"20261128"})   # 平日、週六補行交易
    ('trading', 'trading')
    >>> day_status("20261225", None, {"20261225"}, set()), day_status("20261128", None, set(), set())   # 休市、週末
    ('closed', 'closed')
    >>> day_status("20261124", {"stat": "很抱歉，沒有符合條件的資料!"}, set(), set())   # 平日、收盤行情沒有：不知道
    'unknown'
    """
    if not should_trade(date_yyyymmdd, closed, open_):
        return CLOSED
    if price_body is not None and is_published("stock_price", price_body, date_yyyymmdd):
        return TRADING
    if market_confirms_closed(date_yyyymmdd, market_body):
        return CLOSED
    return UNKNOWN


def accept_missing(status: str) -> bool:
    """看不到這天的資料時，可不可以照存：只有休市可以。交易日一定有資料，只是還沒公布；
    不知道的（平日、收盤行情一直沒有、日曆上沒寫，例如颱風假）不猜，存成失敗讓人看得到。"""
    return status == CLOSED


def watchlist_problem(list_yyyymmdd: str | None, today_yyyymmdd: str, status: str) -> str | None:
    """注意累計名單只有最新一份：上面的日期不是今天 → 今天的還沒公布，回傳原因（存成失敗，下一輪再抓）。
    不擋的話，抓到的是前一天的名單，這一輪照樣算成功、放了跑完的記號，今天的名單就永遠沒抓到。
    status：今天是不是交易日（day_status）；休市就不會有今天的名單，照存前一天的。

    >>> watchlist_problem("20261123", "20261123", TRADING) is None
    True
    >>> watchlist_problem("20261120", "20261123", TRADING)
    '注意累計名單還是 20261120 的，今天（交易日）的還沒公布'
    """
    if list_yyyymmdd == today_yyyymmdd or accept_missing(status):
        return None
    return f"注意累計名單還是 {list_yyyymmdd or '（找不到日期）'} 的，今天{'（交易日）' if status == TRADING else ''}的還沒公布"


def not_published_problem(source: str, body, date_yyyymmdd: str, status: str, published_with: bool = False) -> str | None:
    """還沒公布 → 回傳原因：這時回傳的空名單不是「沒人被列」，不能當成那天的資料。
    看得到這天的資料就算公布了（不管幾點）；published_with：一起公布的資料已經公布了
    （注意股和處置股一起公布：處置股公布了，注意股是空的就是那天真的沒人被列）。
    看不到的看 status（day_status）：休市就收；交易日不收（一定有資料，只是證交所還沒公布）；
    不知道的也不收（還沒公布，或颱風假這類臨時休市：下一個交易日的市場成交出來後會自動確認成休市）。"""
    if is_published(source, body, date_yyyymmdd) or published_with or accept_missing(status):
        return None
    if status == TRADING:
        return f"{date_yyyymmdd} 是交易日，還沒公布完整（看不到這天的資料）"
    if source == "stock_price":
        return (f"{date_yyyymmdd} 看不到收盤行情：還沒公布（平常 14:30 左右），"
                "或颱風假這類臨時休市（下一個交易日的市場成交出來後會自動確認，不用處理）")
    return (f"{date_yyyymmdd} 還沒公布完整：日曆上應該有交易，但收盤行情沒有資料。可能是颱風假這類臨時休市"
            "（那天和週末一樣當成沒交易，資料不會寫錯；下一個交易日的市場成交出來後會自動確認，不用處理）；"
            "如果是交易日，表示證交所還沒公布")


def holiday_problem(body, year: int) -> str | None:
    """開休市日期回傳的年份不對（11 月起不帶日期會回下一年）→ 回傳原因，避免把別年的資料存成這一年。"""
    if isinstance(body, dict) and body.get("queryYear") != year:
        return f"要 {year} 年，回傳的是 {body.get('queryYear')} 年"
    return None


def stock_news_problem(body) -> str | None:
    """FinMind 的回應 status 不是 200（例如次數用完 402）或沒有 data（格式改了）→ 回傳原因。HTTP 狀態碼可能還是 200，所以要看內容。"""
    if isinstance(body, dict) and body.get("status") != 200:
        return f"FinMind 回傳 {body.get('status')}：{body.get('msg')}"
    if isinstance(body, dict) and "data" not in body:
        return "FinMind 回應沒有 data（格式可能改了）"
    return None


def news_detail_problem(resp: requests.Response) -> str | None:
    """新聞網頁打開了但不能用（HTTP 錯誤、不是網頁，例如 PDF、影片）→ 回傳原因。5xx、429 由 task 存完再重試。"""
    if not resp.ok:
        return f"HTTP {resp.status_code}"
    content_type = resp.headers.get("Content-Type", "")
    if "html" not in content_type:
        return f"不是網頁（{content_type or '沒有 Content-Type'}）"
    return None


# 抓不到原文的連結：Google 新聞的轉址要跑 JavaScript 才會跳到原文
UNREACHABLE_HOSTS = ("news.google.com",)


def unreachable_link_problem(link: str) -> str | None:
    """連過去也拿不到原文的連結 → 回傳原因（不打）。

    >>> unreachable_link_problem("https://news.google.com/rss/articles/abc")
    'news.google.com 的轉址連結，抓不到原文'
    >>> unreachable_link_problem("https://udn.com/news/1") is None
    True
    """
    host = urllib.parse.urlparse(link).netloc
    return f"{host} 的轉址連結，抓不到原文" if host in UNREACHABLE_HOSTS else None


def announcement_detail_problem(body) -> str | None:
    """全文回應裡沒有那則公告（data 是空的）→ 回傳原因。"""
    if isinstance(body, dict) and not (body.get("result") or {}).get("data"):
        return f"查無這則公告（{body.get('message')}）"
    return None
