"""producer 層：決定這一輪要做哪些任務，依序執行（課程補充 J Step 4）。API → raw 表。

    uv run python -m crawler.producer                        # 抓今天（daily ＝ 只有今天的 backfill）
    uv run python -m crawler.producer backfill --start 20261001  # 回補到今天（可加 --end）

任務清單的每一項是 (任務函式, 參數 dict)，例如 (task.crawl_notice, {"date_yyyymmdd": "20261113"})。
現在用 func(**kwargs) 當場執行；Step 5 接 Celery 時改成 func.delay(**kwargs) 送進 RabbitMQ。
分兩批：證交所先跑完，觀測站才能依當天的注意股、處置股決定要查哪些公司。
"""
import argparse
import time

from common import repository
from common.config import SLEEP_SECONDS, SLEEP_SECONDS_MOPS
from common.date_utils import dates_between, months_between, prev_month, today_yyyymmdd
from crawler import client, task


# ---------- 第一批：證交所 ----------
def build_twse_jobs(start: str, end: str) -> list[tuple]:
    """產生證交所的任務清單：[(任務函式, 參數 dict), ...]。"""
    days = dates_between(start, end)
    months = months_between(start, end)
    years = sorted({int(m[:4]) for m in months})

    # 開休市日期年初就公布整年，抓過就不再抓（颱風假不在這份裡，是從市場成交看出來的）
    jobs = [(task.crawl_holiday, {"year": y}) for y in years if not repository.raw_exists("holiday", str(y))]
    jobs += [(task.crawl_market_daily, {"month_yyyymm": m}) for m in months]
    jobs += [(task.crawl_notice, {"date_yyyymmdd": d}) for d in days]
    jobs += [(task.crawl_punish, {"date_yyyymmdd": d}) for d in days]
    jobs += [(task.crawl_companies, {})]              # 查重大訊息前要先有公司清單
    if end == today_yyyymmdd():
        jobs += [(task.crawl_punish_watchlist, {})]   # 只有最新一天，回補時抓不到舊的
    return jobs


# ---------- 第二批：觀測站重大訊息（要先有第一批的注意股、處置股、公司清單）----------
def watched_stocks(date_yyyymmdd: str) -> set[str]:
    """某一天的關注名單：當天被列注意的股票＋處置中的股票（讀第一批剛存進 raw 表的回應）。"""
    notice = repository.read_raw("notice", date_yyyymmdd)
    punish = repository.read_raw("punish", date_yyyymmdd)   # 含當天起 14 天內處置中的公告
    ids = client.notice_stock_ids(notice["body"]) if notice else set()
    ids |= client.punish_stock_ids(punish["body"]) if punish else set()
    return ids


def build_mops_jobs(start: str, end: str) -> list[tuple]:
    """區間內每天被關注的上市公司，查那天的當月和上個月重大訊息（重複的只查一次）。"""
    latest = repository.list_raw_keys("companies")[-1]
    companies = client.company_ids(repository.read_raw("companies", latest)["body"])
    targets = set()
    for date_yyyymmdd in dates_between(start, end):
        for stock_id in watched_stocks(date_yyyymmdd) & companies:   # 權證、ETF 不是上市公司，沒有重大訊息
            targets |= {(stock_id, date_yyyymmdd[:6]), (stock_id, prev_month(date_yyyymmdd))}
    return [(task.crawl_announcements, {"stock_id": s, "year": int(m[:4]), "month": int(m[4:])})
            for s, m in sorted(targets)]


# ---------- 執行 ----------
def run_jobs(jobs: list[tuple], sleep: float) -> list[str]:
    """依序執行，回傳失敗的任務。單一任務失敗不會中斷整批。

    jobs 例如 [(crawl_notice, {"date_yyyymmdd": "20261113"}), (crawl_announcements, {"stock_id": "2330", ...})]
    """
    failed = []
    for func, kwargs in jobs:
        name = f"{func.__name__}({', '.join(f'{k}={v!r}' for k, v in kwargs.items())})"   # log 用
        try:
            where = func(**kwargs)               # 執行任務：打 API、存進 raw 表；Step 5 改成 func.delay(**kwargs)
            print(name, "→", where)              # 印到 log，例如 raw_notice/20261113
        except Exception as e:                   # 被擋、逾時等：記下來，繼續下一個
            print(name, "✗", e)
            failed.append(name)
        time.sleep(sleep)                        # 間隔一下，避免被擋
    return failed


def parse_args() -> argparse.Namespace:
    """讀指令參數，例如 `backfill --start 20261001` → args.mode="backfill"、args.start="20261001"。"""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="mode")                # 模式（子指令）存到 args.mode；沒給就是 None
    sub.add_parser("daily", help="只抓今天（預設）")
    backfill = sub.add_parser("backfill", help="回補一段期間")
    backfill.add_argument("--start", required=True, help="YYYYMMDD")
    backfill.add_argument("--end", default=today_yyyymmdd(), help="YYYYMMDD，預設今天")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.mode == "backfill":
        start, end = args.start, args.end
    else:
        start = end = today_yyyymmdd()

    failed = run_jobs(build_twse_jobs(start, end), SLEEP_SECONDS)
    failed += run_jobs(build_mops_jobs(start, end), SLEEP_SECONDS_MOPS)   # 要先有第一批的注意股、處置股、公司清單

    print("\n失敗：", failed or "無")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
