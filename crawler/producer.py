"""producer 層（抓取）：決定這一輪要抓哪些資料。API → raw。

    uv run python -m crawler.producer                        # 抓今天（daily ＝ 只有今天的 backfill）
    uv run python -m crawler.producer backfill --start 20261001  # 回補到今天（可加 --end）
    uv run python -m crawler.producer backfill --start 20261001 --refetch  # 已經抓完整的也重抓

任務清單的每一項是 (任務函式, 參數 dict)，例如 (task.crawl_notice, {"date_yyyymmdd": "20261113"})，
交給 common/runner.py 執行（每個任務間隔一下、暫時性的失敗重試）。
分五批：證交所先跑完，觀測站、FinMind 才能依當天的注意股、處置股決定要查哪些公司。之後分成兩條鏈同時進行：
- 觀測站：重大訊息清單 → 公告全文（有了清單，才知道要查哪幾則公告的全文）
- 新聞：FinMind 新聞清單（同一批公司）→ 打開每則新聞的網頁
兩條鏈打的是不同的網站、不同的佇列，不會互相影響；同一條鏈裡上一批做完才送下一批。

已經抓完整的不再抓（加 --refetch 就全部重抓）。「完整」＝在那份資料不會再變之後抓的，見 is_complete()。
注意累計名單、上市公司清單只有當下一份，每次都抓。
"""
import bisect
import calendar
import datetime
import functools
import urllib.parse

from common import cli, repository, rules, runner
from common.config import (
    NEWS_LOOKBACK_DAYS, SLEEP_SECONDS, SLEEP_SECONDS_FINMIND, SLEEP_SECONDS_MOPS, SLEEP_SECONDS_NEWS_DETAIL,
)
from common.date_utils import (
    dates_between, days_before, holiday_years, months_between, parse_yyyymmdd, prev_month, today_yyyymmdd,
)
from common.hash_utils import url_md5
from crawler import client, task


# ---------- 抓過、而且完整了嗎 ----------
# 注意股、處置股、收盤行情：有成功的就完整了（還沒公布就抓的存成失敗，見 client.not_published_problem）
# 一個月的市場成交：最後一天收盤後還會補上那天，那天晚上以後抓的才不會再變
MONTH_DATA_READY = datetime.time(20, 0)


def month_ready_at(month_yyyymm: str, at: datetime.time = MONTH_DATA_READY) -> datetime.datetime:
    """一個月的資料：那個月最後一天的 at 以後就不會再變。"""
    year, month = int(month_yyyymm[:4]), int(month_yyyymm[4:])
    last_day = calendar.monthrange(year, month)[1]          # monthrange 回傳 (星期幾, 天數)
    return datetime.datetime.combine(datetime.date(year, month, last_day), at)


def is_complete(fetched_times: dict, key: str, ready_at: datetime.datetime) -> bool:
    """抓過、而且是在 ready_at 以後抓的 → 資料不會再變，不用重抓。

    >>> is_complete({"202610": datetime.datetime(2026, 11, 2, 9, 0)}, "202610", month_ready_at("202610"))
    True
    >>> is_complete({"202610": datetime.datetime(2026, 10, 20, 21, 0)}, "202610", month_ready_at("202610"))
    False
    """
    fetched_at = fetched_times.get(key)
    return fetched_at is not None and fetched_at >= ready_at


# ---------- 第一批：證交所 ----------
def build_twse_steps(start: str, end: str, refetch: bool = False) -> list[list[tuple]]:
    """證交所的任務分好幾步，一步做完才做下一步（後面的要用前面抓到的判斷那天是不是交易日，見 task.day_status）：
    1. 開休市日期（日曆）、市場成交（事後確認臨時休市）
    2. 收盤行情（有個股資料＝交易日）
    3. 處置股（注意股和處置股一起公布，注意股要看處置股公布了沒）
    4. 注意股、注意累計名單、上市公司清單
    同一步裡就算有任務重試、順序被打亂也沒關係。refetch＝True 就不跳過已經完整的。"""
    days = dates_between(start, end)
    months = months_between(start, end)
    fetched = {s: {} if refetch else repository.raw_fetched_times(s)
               for s in ("notice", "punish", "stock_price", "market_daily", "holiday", "company")}

    # 開休市日期一年一份（11 月起也抓下一年），抓過就不再抓；颱風假不在這份裡，是從市場成交看出來的
    calendar = [(task.crawl_holiday, {"year": y}) for y in holiday_years(start, end) if str(y) not in fetched["holiday"]]
    calendar += [(task.crawl_market_daily, {"month_yyyymm": m}) for m in months
                 if not is_complete(fetched["market_daily"], m, month_ready_at(m))]
    prices = [(task.crawl_stock_price, {"date_yyyymmdd": d}) for d in days if d not in fetched["stock_price"]]
    punishes = [(task.crawl_punish, {"date_yyyymmdd": d}) for d in days if d not in fetched["punish"]]
    rest = [(task.crawl_notice, {"date_yyyymmdd": d}) for d in days if d not in fetched["notice"]]
    if end == today_yyyymmdd():
        rest += [(task.crawl_punish_watchlist, {"date_yyyymmdd": end})]   # 只有最新一天，回補時抓不到舊的
    if today_yyyymmdd() not in fetched["company"]:   # 查重大訊息前要先有公司清單；一天一份快照，同一天的下一輪不用再抓
        rest += [(task.crawl_company, {})]
    return [calendar, prices, punishes, rest]


def build_twse_jobs(start: str, end: str, refetch: bool = False) -> list[tuple]:
    """證交所全部的任務（照步驟的順序接起來）：[(任務函式, 參數 dict), ...]。"""
    return [job for step in build_twse_steps(start, end, refetch) for job in step]


# ---------- 第二批：觀測站重大訊息（要先有第一批的注意股、處置股、公司清單）----------
NOTICE_LOOKBACK_CALENDAR_DAYS = 120   # 關注名單往前讀幾個日曆天的注意股（要涵蓋 WATCH_WINDOW_DAYS 個交易日，連假也夠）


@functools.cache   # 同一輪（一個 producer 程序）裡 raw 的注意股不會變，算一次給重大訊息、新聞共用
def watched_by_day(start: str, end: str) -> dict[str, set[str]]:
    """區間內每天的關注名單：往前 WATCH_WINDOW_DAYS 個交易日（含當天）被列注意過的股票，和 stage_watched_stock（卡片牆）同一個規則；
    再加上那天處置中的（raw 處置股）當保險：處置中一定是最近被列注意過的，只有 raw 缺了某幾天時才會多出來，多查幾家不會漏。
    全部從 raw 算，不看 stage：同一輪剛抓的、還沒整理成 stage 的日子也算得到（多天一起補、全新的資料庫都一樣）。
    交易日＝raw 注意股有人被列的日子：沒人被列的交易日很少，算漏只會讓窗口往前多看一天、多查幾家，不會漏掉。
    休市的日子（週末、連假）用之前的交易日的名單：新聞照樣每天查，連假比 NEWS_LOOKBACK_DAYS 長也不會漏。
    範圍內要用的注意股一次讀完，再一天一天往後滑。
    和 stage_watched_stock 比，這裡故意只會多、不會少（多的只是多查幾家重大訊息、新聞）。"""
    # 往前照日期讀：注意股每個日曆天都抓（休市也有一份空的），30 個交易日最多約 45 天（過年連假），讀 120 天很夠
    read_from = days_before(start, NOTICE_LOOKBACK_CALENDAR_DAYS)
    keys = [key for key in repository.list_raw_keys("notice") if read_from <= key <= end]
    listed = {}   # 有人被列注意的日子 → 那天被列的股票
    for raw in repository.read_raws("notice", keys):
        ids = client.notice_stock_ids(raw["body"])
        if ids:
            listed[raw["meta"]["key"]] = ids
    days = sorted(listed)
    watched = {}
    for date_yyyymmdd in dates_between(start, end):
        before = bisect.bisect_left(days, date_yyyymmdd)   # days 裡比這天早的有幾個
        if date_yyyymmdd in listed:   # 當天有人被列：當天＋往前 29 個
            window = days[max(0, before - (rules.WATCH_WINDOW_DAYS - 1)):before] + [date_yyyymmdd]
        else:                         # 休市、或還沒公布：之前的交易日的完整 30 個
            window = days[max(0, before - rules.WATCH_WINDOW_DAYS):before]
        punish = repository.read_raw("punish", date_yyyymmdd)   # 含當天起 14 天內處置中的公告
        watched[date_yyyymmdd] = set().union(*(listed[day] for day in window),
                                             client.punish_stock_ids(punish["body"]) if punish else set())
    return watched


def watched_companies(start: str, end: str) -> dict[str, set[str]]:
    """區間內每天被關注的上市公司：{日期: {公司}}。權證、ETF 不是上市公司，沒有重大訊息，也不查新聞。

    還沒抓到過公司清單（例如全新的資料庫、證交所擋掉）：回傳空的，這輪先不查。
    """
    company_keys = repository.list_raw_keys("company")
    if not company_keys:
        return {}
    companies = client.company_ids(repository.read_raw("company", company_keys[-1])["body"])
    return {date_yyyymmdd: stocks & companies for date_yyyymmdd, stocks in watched_by_day(start, end).items()}


def announcement_targets(start: str, end: str) -> list[tuple[str, str]]:
    """區間內每天被關注的上市公司，要查哪些 (公司, 月份)：那天的當月和上個月（重複的只算一次）。"""
    targets = set()
    for date_yyyymmdd, stock_ids in watched_companies(start, end).items():
        for stock_id in stock_ids:
            targets |= {(stock_id, date_yyyymmdd[:6]), (stock_id, prev_month(date_yyyymmdd))}
    return sorted(targets)


def build_mops_jobs(start: str, end: str, refetch: bool = False) -> list[tuple]:
    """重大訊息清單：每個 (公司, 月份) 一個任務；那個月過完以後抓的就不再抓（當月會有新公告，照樣抓）。"""
    fetched = {} if refetch else repository.raw_fetched_times("announcement")
    end_of_day = datetime.time(23, 59, 59)                  # 公告晚上也會發，月底當天整天都可能有
    return [(task.crawl_announcement, {"stock_id": s, "year": int(m[:4]), "month": int(m[4:])})
            for s, m in announcement_targets(start, end)
            if not is_complete(fetched, f"{s}_{m}", month_ready_at(m, end_of_day))]


# ---------- 第三批：重大訊息全文（要先有第二批的公告清單）----------
def build_detail_jobs(start: str, end: str, refetch: bool = False) -> list[tuple]:
    """第二批清單裡的每則公告查全文；全文發出後不會變，抓過的就不再抓。"""
    keys = [f"{s}_{m}" for s, m in announcement_targets(start, end)]   # 例如 1303_202611
    fetched = {} if refetch else repository.raw_fetched_times("announcement_detail")
    jobs = []
    for raw in repository.read_raws("announcement", keys):
        for stock_id, enter_yyyymmdd, serial_number, api_name in client.announcement_ids(raw["body"]):
            if f"{stock_id}_{enter_yyyymmdd}_{serial_number}" not in fetched:
                jobs.append((task.crawl_announcement_detail,
                             {"stock_id": stock_id, "enter_yyyymmdd": enter_yyyymmdd, "serial_number": serial_number,
                              "api_name": api_name}))
    return jobs


# ---------- 第四批：新聞清單（FinMind；和重大訊息同一批公司）----------
def stock_news_ready_at(date_yyyymmdd: str) -> datetime.datetime:
    """一天的新聞清單：FinMind 的「一天」是 UTC 的一天（台灣時間那天 08:00 到隔天 08:00），隔天 08:00 以後抓的就不會再變。"""
    return datetime.datetime.combine(parse_yyyymmdd(date_yyyymmdd) + datetime.timedelta(days=1), datetime.time(8, 0))


def stock_news_targets(start: str, end: str) -> list[tuple[str, str]]:
    """要查哪些 (公司, 日期)：區間內每天被關注的上市公司，那天和往前 NEWS_LOOKBACK_DAYS 天（重複的只算一次）。

    往前抓：第一次被關注的股票也有最近幾天的新聞；之前抓過的日子 build_stock_news_jobs 會跳過。
    """
    targets = set()
    for date_yyyymmdd, stock_ids in watched_companies(start, end).items():
        first = days_before(date_yyyymmdd, NEWS_LOOKBACK_DAYS)
        targets |= {(stock_id, day) for stock_id in stock_ids for day in dates_between(first, date_yyyymmdd)}
    return sorted(targets)


def build_stock_news_jobs(start: str, end: str, refetch: bool = False) -> list[tuple]:
    """新聞清單：每個 (公司, 日期) 一個任務；那天過完（隔天 08:00）以後抓的就不再抓。"""
    fetched = {} if refetch else repository.raw_fetched_times("stock_news")
    return [(task.crawl_stock_news, {"stock_id": s, "date_yyyymmdd": d}) for s, d in stock_news_targets(start, end)
            if not is_complete(fetched, f"{s}_{d}", stock_news_ready_at(d))]


# ---------- 第五批：新聞網頁（要先有第四批的新聞清單）----------
def build_news_detail_jobs(start: str, end: str, refetch: bool = False) -> list[tuple]:
    """第四批清單裡的每則新聞打開網頁一次（存 <head>）；成功或永久失敗（被擋、轉址）的不再抓，5xx、429 下次再試。"""
    keys = [f"{s}_{d}" for s, d in stock_news_targets(start, end)]   # 例如 2330_20261120
    done = set() if refetch else repository.raw_keys_done("news_detail")
    links = {link for raw in repository.read_raws("stock_news", keys) for link in client.stock_news_links(raw["body"])}
    return [(task.crawl_news_detail, {"link": link}) for link in spread_by_site(sorted(links)) if url_md5(link) not in done]


def spread_by_site(links: list[str]) -> list[str]:
    """網站輪流排：A 站一則、B 站一則、C 站一則…，worker 同時做好幾個時，打的大多是不同網站（不會同時連打同一站）。

    >>> spread_by_site(["https://a.com/1", "https://a.com/2", "https://a.com/3", "https://b.com/1", "https://c.com/1"])
    ['https://a.com/1', 'https://b.com/1', 'https://c.com/1', 'https://a.com/2', 'https://a.com/3']
    """
    by_site: dict[str, list[str]] = {}
    for link in links:
        by_site.setdefault(urllib.parse.urlsplit(link).netloc, []).append(link)
    queues = list(by_site.values())
    spread = []
    for i in range(max((len(q) for q in queues), default=0)):
        spread += [q[i] for q in queues if i < len(q)]
    return spread


# ---------- 執行 ----------
def main() -> None:
    args = cli.parse_args()   # daily：今天；backfill：--start～--end，--refetch 全部重抓
    repository.ensure_tables()   # 全新的資料庫才建表；建表、更新 VIEW 在 main.py 一輪開始時做

    span = (args.start, args.end, args.refetch)
    # queue：開 Celery 時，每個來源各自一個佇列、一個 worker（見 common/worker.py）
    # 證交所先做完（要知道哪些股票被關注），分好幾步依序做（見 build_twse_steps）；
    # 之後觀測站、新聞兩條鏈同時進行，各自上一批做完才送下一批
    failed = []
    for step in build_twse_steps(*span):
        if step:
            failed += runner.run_jobs(step, sleep=SLEEP_SECONDS, queue="crawl_twse")
    failed += runner.run_chains([
        [(lambda: build_mops_jobs(*span), SLEEP_SECONDS_MOPS, "crawl_mops"),
         (lambda: build_detail_jobs(*span), SLEEP_SECONDS_MOPS, "crawl_mops")],
        [(lambda: build_stock_news_jobs(*span), SLEEP_SECONDS_FINMIND, "crawl_finmind"),
         (lambda: build_news_detail_jobs(*span), SLEEP_SECONDS_NEWS_DETAIL, "crawl_news")],
    ])
    runner.finish(failed)


if __name__ == "__main__":
    main()
