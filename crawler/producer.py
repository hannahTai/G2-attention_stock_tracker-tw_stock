"""producer 層（抓取）：決定這一輪要抓哪些資料。API → raw。

    uv run python -m crawler.producer                        # 抓今天（daily ＝ 只有今天的 backfill）
    uv run python -m crawler.producer backfill --start 20261001  # 回補到今天（可加 --end）
    uv run python -m crawler.producer backfill --start 20261001 --refetch  # 已經抓完整的也重抓

任務清單的每一項是 (任務函式, 參數 dict)，例如 (task.crawl_notice, {"date_yyyymmdd": "20261113"})，
交給 common/runner.py 執行（每個任務間隔一下、暫時性的失敗重試）。
分三批：證交所先跑完，觀測站才能依當天的注意股、處置股決定要查哪些公司；
有了公告清單，才知道要查哪幾則公告的全文。

已經抓完整的不再抓（加 --refetch 就全部重抓）。「完整」＝在那份資料不會再變之後抓的，見 is_complete()。
注意累計名單、上市公司清單只有當下一份，每次都抓。
"""
import calendar
import datetime

from common import cli, repository, runner
from common.config import SLEEP_SECONDS, SLEEP_SECONDS_MOPS
from common.date_utils import dates_between, holiday_years, months_between, parse_yyyymmdd, prev_month, today_yyyymmdd
from crawler import client, task


# ---------- 抓過、而且完整了嗎 ----------
def day_ready_at(date_yyyymmdd: str) -> datetime.datetime:
    """一天的資料：那天公布完整（client.COMPLETE_BY）以後就不會再變。"""
    return datetime.datetime.combine(parse_yyyymmdd(date_yyyymmdd), client.COMPLETE_BY)


def month_ready_at(month_yyyymm: str, at: datetime.time = client.COMPLETE_BY) -> datetime.datetime:
    """一個月的資料：那個月最後一天的 at 以後就不會再變。"""
    year, month = int(month_yyyymm[:4]), int(month_yyyymm[4:])
    last_day = calendar.monthrange(year, month)[1]          # monthrange 回傳 (星期幾, 天數)
    return datetime.datetime.combine(datetime.date(year, month, last_day), at)


def is_complete(fetched_times: dict, key: str, ready_at: datetime.datetime) -> bool:
    """抓過、而且是在 ready_at 以後抓的 → 資料不會再變，不用重抓。

    >>> is_complete({"20261113": datetime.datetime(2026, 11, 13, 21, 0)}, "20261113", day_ready_at("20261113"))
    True
    >>> is_complete({"20261113": datetime.datetime(2026, 11, 13, 16, 0)}, "20261113", day_ready_at("20261113"))
    False
    """
    fetched_at = fetched_times.get(key)
    return fetched_at is not None and fetched_at >= ready_at


# ---------- 第一批：證交所 ----------
def build_twse_jobs(start: str, end: str, refetch: bool = False) -> list[tuple]:
    """產生證交所的任務清單：[(任務函式, 參數 dict), ...]。refetch＝True 就不跳過已經完整的。"""
    days = dates_between(start, end)
    months = months_between(start, end)
    fetched = {s: {} if refetch else repository.raw_fetched_times(s) for s in ("notice", "punish", "market_daily", "holiday")}

    # 開休市日期一年一份（11 月起也抓下一年），抓過就不再抓；颱風假不在這份裡，是從市場成交看出來的
    jobs = [(task.crawl_notice, {"date_yyyymmdd": d}) for d in days
            if not is_complete(fetched["notice"], d, day_ready_at(d))]
    jobs += [(task.crawl_punish, {"date_yyyymmdd": d}) for d in days
             if not is_complete(fetched["punish"], d, day_ready_at(d))]
    if end == today_yyyymmdd():
        jobs += [(task.crawl_punish_watchlist, {})]   # 只有最新一天，回補時抓不到舊的
    jobs += [(task.crawl_market_daily, {"month_yyyymm": m}) for m in months
             if not is_complete(fetched["market_daily"], m, month_ready_at(m))]
    jobs += [(task.crawl_holiday, {"year": y}) for y in holiday_years(start, end) if str(y) not in fetched["holiday"]]
    jobs += [(task.crawl_companies, {})]              # 查重大訊息前要先有公司清單
    return jobs


# ---------- 第二批：觀測站重大訊息（要先有第一批的注意股、處置股、公司清單）----------
def watched_stocks(date_yyyymmdd: str) -> set[str]:
    """某一天的關注名單：當天被列注意的股票＋處置中的股票（讀第一批剛存進 raw 表的回應）。"""
    notice = repository.read_raw("notice", date_yyyymmdd)
    punish = repository.read_raw("punish", date_yyyymmdd)   # 含當天起 14 天內處置中的公告
    ids = client.notice_stock_ids(notice["body"]) if notice else set()
    ids |= client.punish_stock_ids(punish["body"]) if punish else set()
    return ids


def announcement_targets(start: str, end: str) -> list[tuple[str, str]]:
    """區間內每天被關注的上市公司，要查哪些 (公司, 月份)：那天的當月和上個月（重複的只算一次）。"""
    company_keys = repository.list_raw_keys("companies")
    if not company_keys:   # 還沒抓到過公司清單（例如全新的資料庫、證交所擋掉）：這輪先不查
        return []
    companies = client.company_ids(repository.read_raw("companies", company_keys[-1])["body"])
    targets = set()
    for date_yyyymmdd in dates_between(start, end):
        for stock_id in watched_stocks(date_yyyymmdd) & companies:   # 權證、ETF 不是上市公司，沒有重大訊息
            targets |= {(stock_id, date_yyyymmdd[:6]), (stock_id, prev_month(date_yyyymmdd))}
    return sorted(targets)


def build_mops_jobs(start: str, end: str, refetch: bool = False) -> list[tuple]:
    """重大訊息清單：每個 (公司, 月份) 一個任務；那個月過完以後抓的就不再抓（當月會有新公告，照樣抓）。"""
    fetched = {} if refetch else repository.raw_fetched_times("announcements")
    end_of_day = datetime.time(23, 59, 59)                  # 公告晚上也會發，月底當天整天都可能有
    return [(task.crawl_announcements, {"stock_id": s, "year": int(m[:4]), "month": int(m[4:])})
            for s, m in announcement_targets(start, end)
            if not is_complete(fetched, f"{s}_{m}", month_ready_at(m, end_of_day))]


# ---------- 第三批：重大訊息全文（要先有第二批的公告清單）----------
def build_detail_jobs(start: str, end: str, refetch: bool = False) -> list[tuple]:
    """第二批清單裡的每則公告查全文；全文發出後不會變，抓過的就不再抓。"""
    keys = [f"{s}_{m}" for s, m in announcement_targets(start, end)]   # 例如 1303_202611
    fetched = {} if refetch else repository.raw_fetched_times("announcement_detail")
    jobs = []
    for raw in repository.read_raws("announcements", keys):
        for stock_id, enter_yyyymmdd, serial_number, api_name in client.announcement_ids(raw["body"]):
            if f"{stock_id}_{enter_yyyymmdd}_{serial_number}" not in fetched:
                jobs.append((task.crawl_announcement_detail,
                             {"stock_id": stock_id, "enter_yyyymmdd": enter_yyyymmdd, "serial_number": serial_number,
                              "api_name": api_name}))
    return jobs


# ---------- 執行 ----------
def main() -> None:
    args = cli.parse_args()   # daily：今天；backfill：--start～--end，--refetch 全部重抓
    repository.create_tables()   # 建表（已經有就跳過）；全新的資料庫第一步就是 crawler

    failed = runner.run_jobs(build_twse_jobs(args.start, args.end, args.refetch), sleep=SLEEP_SECONDS)
    failed += runner.run_jobs(build_mops_jobs(args.start, args.end, args.refetch), sleep=SLEEP_SECONDS_MOPS)
    failed += runner.run_jobs(build_detail_jobs(args.start, args.end, args.refetch), sleep=SLEEP_SECONDS_MOPS)
    runner.finish(failed)


if __name__ == "__main__":
    main()
