"""producer 層（分析）：決定這一輪要算哪幾天的進度，依序執行（跟 crawler、transform 的 producer 同樣的寫法）。stage → app。

    uv run python -m analyze.producer                        # 只算今天（daily）
    uv run python -m analyze.producer backfill --start 20260701  # 算一段期間（可加 --end）

任務清單的每一項是 (任務函式, 參數 dict)，例如 (task.analyze_progress, {"date_yyyymmdd": "20261113"})。
每個日期一個任務，照日期順序執行，其中一個任務失敗就停。要在 transform 之後執行（要用到前幾天的 stage）。
算之前先檢查必要的 stage 資料（注意股、處置股、交易日）那天都有；缺了就不算，避免算出錯的進度。
其他資料（重大訊息、公司清單…）只給卡片顯示，缺了不影響進度。
"""
import argparse
import datetime

from analyze import task
from common import repository
from common.date_utils import dates_between, today_yyyymmdd


def build_jobs(start: str, end: str) -> list[tuple]:
    """產生任務清單：每個日期一個 analyze_progress 任務。"""
    jobs = []
    for date_yyyymmdd in dates_between(start, end):
        jobs.append((task.analyze_progress, {"date_yyyymmdd": date_yyyymmdd}))
    return jobs


# 算進度一定要有的 stage 資料：每張表每天一列，有這列＝那天抓到也整理好了
#   stage_trading_day：那天是不是交易日（市場成交）
#   stage_notice_day：那天的注意股（0 檔也有一列）
#   stage_punish_day：那天的處置股（0 則也有一列）
REQUIRED_DAYS = [("stage_trading_day", "trade_date"), ("stage_notice_day", "trade_date"), ("stage_punish_day", "query_date")]


def missing_inputs(date_yyyymmdd: str) -> list[str]:
    """那天算進度需要、但還沒有的 stage 資料，例如 ["stage_notice_day/20261113"]；都有就是空的。"""
    day = datetime.datetime.strptime(date_yyyymmdd, "%Y%m%d").date()
    missing = []
    for table, column in REQUIRED_DAYS:
        if not repository.stage_has_day(table, column, day):
            missing.append(f"{table}/{date_yyyymmdd}")
    return missing


def run_jobs(jobs: list[tuple]) -> None:
    """照順序執行；一個任務失敗就停。"""
    for func, kwargs in jobs:
        name = f"{func.__name__}({kwargs})"   # log 用，例如 analyze_progress({'date_yyyymmdd': '20261113'})
        try:
            print(name, "→", func(**kwargs))
        except Exception as e:
            print(name, "✗", e)
            raise SystemExit(1)


def parse_args() -> argparse.Namespace:
    """跟 crawler/producer.py 一樣：daily（預設，只算今天）或 backfill --start（算一段期間）。"""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="mode")
    sub.add_parser("daily", help="只算今天（預設）")
    backfill = sub.add_parser("backfill", help="算一段期間")
    backfill.add_argument("--start", required=True, help="YYYYMMDD")
    backfill.add_argument("--end", default=today_yyyymmdd(), help="YYYYMMDD，預設今天")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.mode == "backfill":
        start, end = args.start, args.end
    else:
        start = end = today_yyyymmdd()

    repository.create_tables()   # 確認資料庫和表都在：不存在就建立（不會刪表）

    # 1. 先檢查每一天必要的 stage 資料都有；缺任何一份就整段都不算
    missing = []
    for date_yyyymmdd in dates_between(start, end):
        missing += missing_inputs(date_yyyymmdd)
    if missing:
        print("缺少必要的資料，不算進度：", missing)
        raise SystemExit(1)

    # 2. 一天一個任務，照日期順序算
    run_jobs(build_jobs(start, end))


if __name__ == "__main__":
    main()
