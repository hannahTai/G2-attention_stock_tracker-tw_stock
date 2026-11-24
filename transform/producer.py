"""producer 層（轉換）：決定這一輪要整理哪些 raw。raw → stage。

    uv run python -m transform.producer                        # 只處理今天（daily）
    uv run python -m transform.producer backfill --start 20260701  # 處理一段期間（可加 --end）

任務清單的每一項是 (任務函式, 參數 dict)，例如 (task.transform_notice, {"date_yyyymmdd": "20261113"})，
交給 common/runner.py 執行。範圍跟 crawler 抓的一樣；任務之間互不依賴，一個失敗不影響其他任務。
raw 沒抓到（crawler 失敗）的任務會自己跳過，不算失敗；要不要算進度由 analyze 檢查。
"""

from common import cli, repository, runner
from common.date_utils import dates_between, holiday_years, months_between, prev_month
from crawler import producer as crawler_producer
from transform import task, transformer


def build_jobs(start: str, end: str, part: str | None = None) -> list[tuple]:
    """產生任務清單：[(任務函式, 參數 dict), ...]。part：twse＝股價線的（證交所），news＝新聞線的（重大訊息、新聞），
    None＝兩個都做。"""
    jobs = []
    if part in (None, "twse"):
        jobs += build_twse_jobs(start, end)
    if part in (None, "news"):
        jobs += build_news_jobs(start, end)
    return jobs


def build_twse_jobs(start: str, end: str) -> list[tuple]:
    """股價線：證交所的（注意股、處置股、注意累計名單、收盤行情、市場成交、開休市日期、公司清單）。"""
    days = dates_between(start, end)
    months = months_between(start, end)
    jobs = [(task.transform_notice, {"date_yyyymmdd": d}) for d in days]
    jobs += [(task.transform_punish, {"date_yyyymmdd": d}) for d in days]
    jobs += [(task.transform_punish_watchlist, {"date_yyyymmdd": d}) for d in days]
    jobs += [(task.transform_stock_price, {"date_yyyymmdd": d}) for d in days]
    jobs += [(task.transform_market_daily, {"month_yyyymm": m}) for m in months]
    jobs += [(task.transform_holiday, {"year": y}) for y in holiday_years(start, end)]   # 11 月起也整理下一年
    jobs += [(task.transform_company, {"date_yyyymmdd": end})]                    # 只用最新一份
    return jobs


def build_news_jobs(start: str, end: str) -> list[tuple]:
    """新聞線：重大訊息（清單、全文）、新聞（清單、網頁）。"""
    months = months_between(start, end)
    announcement_months = set(months) | {prev_month(m) for m in months}   # 重大訊息查當月和上個月
    jobs = []
    for key in repository.list_raw_keys("announcement"):                    # 例如 1303_202611
        stock_id, month_yyyymm = key.split("_")
        if month_yyyymm in announcement_months:
            jobs.append((task.transform_announcement,
                         {"stock_id": stock_id, "year": int(month_yyyymm[:4]), "month": int(month_yyyymm[4:])}))
    # 全文用輸入日期找：輸入日期可能比發言日期早（5/29 輸入、6/1 發言），所以再往前多看一個月
    detail_months = announcement_months | {prev_month(m) for m in announcement_months}
    for key in repository.list_raw_keys("announcement_detail"):              # 例如 9908_20261102_1
        stock_id, enter_yyyymmdd, serial_number = key.split("_")
        if enter_yyyymmdd[:6] in detail_months:
            jobs.append((task.transform_announcement_detail,
                         {"stock_id": stock_id, "enter_yyyymmdd": enter_yyyymmdd, "serial_number": int(serial_number)}))
    # 新聞：和 crawler 同一個範圍（每天被關注的上市公司，那天和往前 NEWS_LOOKBACK_DAYS 天）、有抓到的。
    # 不是區間內所有的 raw：一次性的回補（例如全部上市公司的新聞）自己整理，不要每天的資料線跟著重做好幾千份
    fetched_news = set(repository.list_raw_keys("stock_news"))
    news_keys = [key for key in (f"{s}_{d}" for s, d in crawler_producer.stock_news_targets(start, end))   # 例如 2330_20261120
                 if key in fetched_news]
    for key in news_keys:
        stock_id, date_yyyymmdd = key.split("_")
        jobs.append((task.transform_stock_news, {"stock_id": stock_id, "date_yyyymmdd": date_yyyymmdd}))
    # 新聞網頁：這些新聞裡有抓到網頁的（raw_news_detail 的 key 是 news_id）
    pages = set(repository.list_raw_keys("news_detail"))
    news_ids = {i for raw in repository.read_raws("stock_news", news_keys) for i in transformer.stock_news_ids(raw)}
    jobs += [(task.transform_news_detail, {"news_id": i}) for i in sorted(news_ids & pages)]
    return jobs


def main() -> None:
    args = cli.parse_args()   # daily：今天；backfill：--start～--end
    repository.ensure_tables()   # 全新的資料庫才建表；建表、更新 VIEW 在 main.py 開始時做（schema/*.sql 改過才做）

    # 不給 --part：先整理證交所，再算重大訊息、新聞的範圍（要讀剛整理好的注意股，stage_watched_stock）
    failed = []
    for part in [args.part] if args.part else ["twse", "news"]:
        failed += runner.run_jobs(build_jobs(args.start, args.end, part), queue="transform")
    runner.finish(failed)


if __name__ == "__main__":
    main()
