"""producer 層（轉換）：決定這一輪要整理哪些 raw。raw → stage。

    uv run python -m transform.producer                        # 只處理今天（daily）
    uv run python -m transform.producer backfill --start 20260701  # 處理一段期間（可加 --end）

任務清單的每一項是 (任務函式, 參數 dict)，例如 (task.transform_notice, {"date_yyyymmdd": "20261113"})，
交給 common/runner.py 執行。範圍跟 crawler 抓的一樣；任務之間互不依賴，一個失敗不影響其他任務。
raw 沒抓到（crawler 失敗）的任務會自己跳過，不算失敗；要不要算進度由 analyze 檢查。
"""

from common import cli, repository, runner
from common.config import NEWS_LOOKBACK_DAYS
from common.date_utils import dates_between, days_before, holiday_years, months_between, prev_month
from transform import task, transformer


def build_jobs(start: str, end: str) -> list[tuple]:
    """產生任務清單：[(任務函式, 參數 dict), ...]。"""
    days = dates_between(start, end)
    months = months_between(start, end)
    announcement_months = set(months) | {prev_month(m) for m in months}   # 重大訊息查當月和上個月

    jobs = [(task.transform_notice, {"date_yyyymmdd": d}) for d in days]
    jobs += [(task.transform_punish, {"date_yyyymmdd": d}) for d in days]
    jobs += [(task.transform_punish_watchlist, {"date_yyyymmdd": d}) for d in days]
    jobs += [(task.transform_stock_price, {"date_yyyymmdd": d}) for d in days]
    jobs += [(task.transform_market_daily, {"month_yyyymm": m}) for m in months]
    jobs += [(task.transform_holiday, {"year": y}) for y in holiday_years(start, end)]   # 11 月起也整理下一年
    jobs += [(task.transform_company, {"date_yyyymmdd": end})]                    # 只用最新一份
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
    # 新聞：crawler 往前多抓 NEWS_LOOKBACK_DAYS 天，這裡也往前多整理那幾天
    news_from = days_before(start, NEWS_LOOKBACK_DAYS)
    news_keys = [key for key in repository.list_raw_keys("stock_news")      # 例如 2330_20261120
                 if news_from <= key.split("_")[1] <= end]
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
    repository.create_tables()   # 建表（已經有就跳過）

    failed = runner.run_jobs(build_jobs(args.start, args.end), queue="transform")
    runner.finish(failed)


if __name__ == "__main__":
    main()
