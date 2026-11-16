"""producer 層（轉換）：決定這一輪要整理哪些 raw，依序執行（跟 crawler/producer.py 同樣的寫法）。raw → stage。

    uv run python -m transform.producer                        # 只處理今天（daily）
    uv run python -m transform.producer backfill --start 20260701  # 處理一段期間（可加 --end）

任務清單的每一項是 (任務函式, 參數 dict)，例如 (task.transform_notice, {"date_yyyymmdd": "20261113"})。
範圍跟 crawler 抓的一樣。任務之間互不依賴：一個任務失敗不影響其他任務，最後列出失敗清單。
raw 沒抓到（crawler 失敗）的任務會自己跳過，不算失敗；要不要算進度由 analyze 檢查。
"""

from common import cli, repository
from common.date_utils import dates_between, months_between, prev_month
from transform import task


def build_jobs(start: str, end: str) -> list[tuple]:
    """產生任務清單：[(任務函式, 參數 dict), ...]。"""
    days = dates_between(start, end)
    months = months_between(start, end)
    years = sorted({int(m[:4]) for m in months})
    announcement_months = set(months) | {prev_month(m) for m in months}   # 重大訊息查當月和上個月

    jobs = [(task.transform_holiday, {"year": y}) for y in years]
    jobs += [(task.transform_market_daily, {"month_yyyymm": m}) for m in months]
    jobs += [(task.transform_notice, {"date_yyyymmdd": d}) for d in days]
    jobs += [(task.transform_punish, {"date_yyyymmdd": d}) for d in days]
    jobs += [(task.transform_punish_watchlist, {"date_yyyymmdd": d}) for d in days]
    jobs += [(task.transform_companies, {"date_yyyymmdd": end})]                    # 只用最新一份
    for key in repository.list_raw_keys("announcements"):                    # 例如 1303_202611
        stock_id, month_yyyymm = key.split("_")
        if month_yyyymm in announcement_months:
            jobs.append((task.transform_announcements, {"stock_id": stock_id, "year": int(month_yyyymm[:4]), "month": int(month_yyyymm[4:])}))
    for key in repository.list_raw_keys("announcement_detail"):              # 例如 9908_20261102_1
        stock_id, enter_yyyymmdd, serial_number = key.split("_")
        if enter_yyyymmdd[:6] in announcement_months:                        # 輸入日期在處理範圍的月份
            jobs.append((task.transform_announcement_detail,
                         {"stock_id": stock_id, "enter_yyyymmdd": enter_yyyymmdd, "serial_number": int(serial_number)}))
    return jobs


def run_jobs(jobs: list[tuple]) -> list[str]:
    """依序執行，回傳失敗的任務。單一任務失敗不會中斷整批。"""
    failed = []
    for func, kwargs in jobs:
        name = f"{func.__name__}({', '.join(f'{k}={v!r}' for k, v in kwargs.items())})"   # log 用
        try:
            print(name, "→", func(**kwargs))
        except Exception as e:
            print(name, "✗", e)
            failed.append(name)
    return failed


def main() -> None:
    start, end = cli.date_range()   # daily：今天；backfill：--start～--end

    repository.create_tables()   # 確認資料庫和表都在：不存在就建立（不會刪表）

    failed = run_jobs(build_jobs(start, end))
    print("\n失敗：", failed or "無")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
