"""task 層（抓取）：一個任務抓一份資料、原樣存進 raw 表，回傳存到哪裡（例如 raw_notice/20261113）。

每個任務都是同樣三步：
    1. resp = client.fetch_xxx(...)：打 API
    2. key：這份存成哪個 raw_key（要檢查內容的，多一行 problem = client.xxx_problem(...)）
    3. return repository.save_raw(來源, key, resp)：成功、失敗都存；失敗的存完丟 FetchError
這裡不整理資料：原文先存進 raw 表，整理在 transform/task.py 做（ELT）。
例外：crawl_news_detail 不存整份回應（<body> 就是新聞全文，不存進資料庫），只存 <head> 和全文有幾個字。
任務的輸入都是字串、數字，不依賴其他任務。
每個函式都用 @app.task 註冊成 Celery 任務（見 common/worker.py）：證交所的用 TwseJob、觀測站的用 MopsJob、FinMind 的用 FinmindJob、
新聞網站的用 NewsDetailJob，做完等一下再拿下一個。
"""
from common import repository
from common.date_utils import today_yyyymmdd
from common.hash_utils import url_md5
from common.worker import FinmindJob, MopsJob, NewsDetailJob, TwseJob, app
from crawler import client


# ---------- 證交所 ----------
@app.task(base=TwseJob)
def crawl_notice(date_yyyymmdd: str) -> str:
    """注意股：一天 → raw_notice（key 20261113）。還沒公布完整就抓的當失敗。"""
    resp = client.fetch_notice(date_yyyymmdd)
    key = date_yyyymmdd
    problem = client.fetched_too_early_problem(date_yyyymmdd)
    return repository.save_raw("notice", key, resp, problem)


@app.task(base=TwseJob)
def crawl_punish(date_yyyymmdd: str) -> str:
    """處置股：一天 → raw_punish（key 20261113）。還沒公布完整就抓的當失敗。"""
    resp = client.fetch_punish(date_yyyymmdd)
    key = date_yyyymmdd
    problem = client.fetched_too_early_problem(date_yyyymmdd)
    return repository.save_raw("punish", key, resp, problem)


@app.task(base=TwseJob)
def crawl_punish_watchlist() -> str:
    """注意累計名單：只有最新一份 → raw_punish_watchlist（key 名單上的資料日期；週末抓到的是週五的）。"""
    resp = client.fetch_punish_watchlist()
    key = client.watchlist_date(repository.parse_body(resp.text)) or today_yyyymmdd()   # 找不到日期：用今天，至少把回應存下來
    return repository.save_raw("punish_watchlist", key, resp)


@app.task(base=TwseJob)
def crawl_market_daily(month_yyyymm: str) -> str:
    """每日市場成交：一個月 → raw_market_daily（key 202611）。"""
    resp = client.fetch_market_daily(month_yyyymm)
    key = month_yyyymm
    return repository.save_raw("market_daily", key, resp)


@app.task(base=TwseJob)
def crawl_stock_price(date_yyyymmdd: str) -> str:
    """每日收盤行情：一天 → raw_stock_price（key 20261120）。還沒公布完整就抓的當失敗。"""
    resp = client.fetch_stock_price(date_yyyymmdd)
    key = date_yyyymmdd
    problem = client.fetched_too_early_problem(date_yyyymmdd)
    return repository.save_raw("stock_price", key, resp, problem)


@app.task(base=TwseJob)
def crawl_holiday(year: int) -> str:
    """開休市日期：一年 → raw_holiday（key 2026）。回傳的年份不對就當失敗。"""
    resp = client.fetch_holiday(year)
    key = str(year)
    problem = client.holiday_problem(repository.parse_body(resp.text), year)
    return repository.save_raw("holiday", key, resp, problem)


@app.task(base=TwseJob)
def crawl_company() -> str:
    """上市公司清單：只有最新一份 → raw_company（key 抓取日期，每天一份快照）。"""
    resp = client.fetch_companies()
    key = today_yyyymmdd()
    return repository.save_raw("company", key, resp)


# ---------- 公開資訊觀測站 ----------
@app.task(base=MopsJob)
def crawl_announcement(stock_id: str, year: int, month: int) -> str:
    """重大訊息：一家公司一個月 → raw_announcement（key 1303_202611）。"""
    resp = client.fetch_announcements(stock_id, year, month)
    key = f"{stock_id}_{year:04d}{month:02d}"
    return repository.save_raw("announcement", key, resp)


@app.task(base=MopsJob)
def crawl_announcement_detail(stock_id: str, enter_yyyymmdd: str, serial_number: int, api_name: str) -> str:
    """重大訊息全文：一則 → raw_announcement_detail（key 9908_20261102_1＝公司_輸入日期_序號）。

    api_name：一般公司 t05st01_detail、存託憑證（DR）t59sb01_detail，照重大訊息清單上寫的。
    查無那則公告就當失敗，下次再抓。
    """
    resp = client.fetch_announcement_detail(stock_id, enter_yyyymmdd, serial_number, api_name)
    key = f"{stock_id}_{enter_yyyymmdd}_{serial_number}"
    problem = client.announcement_detail_problem(repository.parse_body(resp.text))
    return repository.save_raw("announcement_detail", key, resp, problem)


# ---------- FinMind ----------
@app.task(base=FinmindJob)
def crawl_stock_news(stock_id: str, date_yyyymmdd: str) -> str:
    """個股新聞：一檔一天 → raw_stock_news（key 2330_20261120）。FinMind 回傳的 status 不是 200 就當失敗。"""
    resp = client.fetch_stock_news(stock_id, date_yyyymmdd)
    key = f"{stock_id}_{date_yyyymmdd}"
    problem = client.stock_news_problem(repository.parse_body(resp.text))
    return repository.save_raw("stock_news", key, resp, problem)


# ---------- 新聞網站 ----------
@app.task(base=NewsDetailJob)
def crawl_news_detail(link: str) -> str:
    """一則新聞的網頁 → raw_news_detail（key＝news_id，也就是 md5(link)）。

    只存 <head>（標題、meta 摘要）、轉址後的網址、全文有幾個字；<body> 就是新聞全文，不存資料庫（著作權），
    全文只在記憶體裡抽（之後 ② 在這裡轉向量）。
    永久的失敗（轉址抓不到、被擋 4xx、不是網頁、解析不出來）也存一列（ok＝否），之後不再抓；
    不算任務失敗：新聞網站常擋，不該讓每天的資料線失敗。
    暫時性的失敗：HTTP 5xx、429 存完丟 FetchError 重試；逾時、連線失敗沒有回應可存，requests 的錯誤直接丟出去（會重試）。
    """
    key = url_md5(link)   # news_id
    problem = client.unreachable_link_problem(link)
    if problem:
        return f"{repository.save_raw_record('news_detail', key, url=link, http_status=None, body={}, problem=problem)}：{problem}"

    resp = client.fetch_news_detail(link)
    body = {"final_url": resp.url, "content_type": resp.headers.get("Content-Type", "")}
    problem = client.news_detail_problem(resp)
    if problem is None:
        try:
            body["head"] = client.news_detail_head(resp.content)
            text = client.news_detail_text(resp.content)   # 全文用完就丟
            body["text_chars"] = len(text) if text else None
        except Exception as e:   # 網頁太怪、解析器出錯：記下來，不要每一輪都重抓
            problem = f"解析失敗：{type(e).__name__}"
    where = repository.save_raw_record("news_detail", key, url=link, http_status=resp.status_code, body=body, problem=problem)
    if problem and repository.is_temporary_status(resp.status_code):
        raise repository.FetchError(f"news_detail {key} 抓取失敗（{problem}），回應已存到 {where}", retryable=True)
    return f"{where}：{problem}（不再抓）" if problem else f"{where}：全文 {body['text_chars'] or 0} 字"
