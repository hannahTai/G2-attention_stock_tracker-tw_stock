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
def day_status(date_yyyymmdd: str, with_price: bool = True) -> str:
    """這天是不是交易日（client.day_status）：先看日曆（週末、raw_holiday 開休市日期），再用收盤行情（raw_stock_price）確認，
    收盤行情沒有的再看市場成交（raw_market_daily）事後確認是不是臨時休市。
    證交所的任務分好幾步依序做（crawler.producer.build_twse_steps）：開休市日期、市場成交 → 收盤行情 → 處置股 → 其他，
    所以要用的時候已經抓好了。with_price＝False：不看收盤行情（收盤行情自己用）。"""
    holiday = repository.read_raw("holiday", date_yyyymmdd[:4])
    closed, open_ = client.calendar_days(holiday["body"]) if holiday else (set(), set())
    price = repository.read_raw("stock_price", date_yyyymmdd) if with_price else None
    market = repository.read_raw("market_daily", date_yyyymmdd[:6])
    return client.day_status(date_yyyymmdd, price["body"] if price else None, closed, open_, market["body"] if market else None)


@app.task(base=TwseJob)
def crawl_notice(date_yyyymmdd: str) -> str:
    """注意股：一天 → raw_notice（key 20261113）。還沒公布完整就抓的當失敗（看內容，見 client.not_published_problem）。
    和處置股一起公布：那天的處置股已經公布了，注意股是空的就是真的沒人被列（處置股排在前一步）。"""
    resp = client.fetch_notice(date_yyyymmdd)
    key = date_yyyymmdd
    punish = repository.read_raw("punish", date_yyyymmdd)
    punish_published = punish is not None and client.is_published("punish", punish["body"], date_yyyymmdd)
    problem = client.not_published_problem("notice", repository.parse_body(resp.text), date_yyyymmdd,
                                           day_status(date_yyyymmdd), published_with=punish_published)
    return repository.save_raw("notice", key, resp, problem)


@app.task(base=TwseJob)
def crawl_punish(date_yyyymmdd: str) -> str:
    """處置股：一天 → raw_punish（key 20261113）。還沒公布完整就抓的當失敗（看內容，見 client.not_published_problem）。"""
    resp = client.fetch_punish(date_yyyymmdd)
    key = date_yyyymmdd
    problem = client.not_published_problem("punish", repository.parse_body(resp.text), date_yyyymmdd, day_status(date_yyyymmdd))
    return repository.save_raw("punish", key, resp, problem)


@app.task(base=TwseJob)
def crawl_punish_watchlist(date_yyyymmdd: str | None = None) -> str:
    """注意累計名單：只有最新一份 → raw_punish_watchlist（key 名單上的資料日期；週末抓到的是週五的）。
    名單上不是這一輪那天的就存成失敗，下一輪再抓；那天休市就照存前一天的（看 day_status，見 client.watchlist_problem）。
    date_yyyymmdd：這一輪資料線的那天（producer 給，不在這裡查「今天」：跑過午夜也還是同一天）；
    沒給（改版前送進佇列、還沒做的任務）就用今天。"""
    date_yyyymmdd = date_yyyymmdd or today_yyyymmdd()
    resp = client.fetch_punish_watchlist()
    list_date = client.watchlist_date(repository.parse_body(resp.text))
    key = list_date or date_yyyymmdd   # 找不到日期：用這一輪的那天，至少把回應存下來
    problem = client.watchlist_problem(list_date, date_yyyymmdd, day_status(date_yyyymmdd))
    return repository.save_raw("punish_watchlist", key, resp, problem)


@app.task(base=TwseJob)
def crawl_market_daily(month_yyyymm: str) -> str:
    """每日市場成交：一個月 → raw_market_daily（key 202611）。"""
    resp = client.fetch_market_daily(month_yyyymm)
    key = month_yyyymm
    return repository.save_raw("market_daily", key, resp)


@app.task(base=TwseJob)
def crawl_stock_price(date_yyyymmdd: str) -> str:
    """每日收盤行情：一天 → raw_stock_price（key 20261120）。還沒公布完整就抓的當失敗（看內容，見 client.not_published_problem）。
    它是判斷交易日的依據，自己不看收盤行情：日曆上休市、或市場成交事後確認休市就收；
    其他沒有資料的存成失敗（還沒公布，或臨時休市，下一個交易日會自動確認）。"""
    resp = client.fetch_stock_price(date_yyyymmdd)
    key = date_yyyymmdd
    problem = client.not_published_problem("stock_price", repository.parse_body(resp.text), date_yyyymmdd,
                                           day_status(date_yyyymmdd, with_price=False))
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
    全文只在記憶體裡抽。
    永久的失敗（轉址抓不到、被擋 4xx、不是網頁、解析不出來）也存一列（ok＝否），之後不再抓；
    不算任務失敗：新聞網站常擋，不該讓每天的資料線失敗。
    暫時性的失敗：HTTP 5xx、429 存完丟 FetchError 重試；逾時、連線失敗沒有回應可存，requests 的錯誤直接丟出去（會重試）。
    """
    key = url_md5(link)   # news_id
    problem = client.unreachable_link_problem(link)
    if problem:
        return f"{repository.save_raw_record('news_detail', key, url=link, http_status=None, body={}, problem=problem)}：{problem}"

    page = client.fetch_news_detail(link)
    body = {"final_url": page.url, "content_type": page.content_type}
    problem = page.problem
    if problem is None:
        try:
            body["head"] = client.news_detail_head(page.content)
            text = client.news_detail_text(page.content)   # 全文用完就丟
            body["text_chars"] = len(text) if text else None
        except Exception as e:   # 網頁太怪、解析器出錯：記下來，不要每一輪都重抓
            problem = f"解析失敗：{type(e).__name__}"
    where = repository.save_raw_record("news_detail", key, url=link, http_status=page.status_code, body=body, problem=problem)
    if problem and page.status_code and repository.is_temporary_status(page.status_code):
        raise repository.FetchError(f"news_detail {key} 抓取失敗（{problem}），回應已存到 {where}", retryable=True)
    return f"{where}：{problem}（不再抓）" if problem else f"{where}：全文 {body['text_chars'] or 0} 字"
