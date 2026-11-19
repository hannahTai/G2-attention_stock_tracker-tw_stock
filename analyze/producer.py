"""producer 層（分析）：決定這一輪要算哪幾天的進度。stage → app。

    uv run python -m analyze.producer                        # 只算今天（daily）
    uv run python -m analyze.producer backfill --start 20260701  # 算一段期間（可加 --end）

任務清單的每一項是 (任務函式, 參數 dict)，例如 (task.analyze_progress, {"date_yyyymmdd": "20261113"})，
交給 common/runner.py 執行。每個日期一個任務；每天只讀 stage，天和天之間互不依賴，一個失敗不影響其他天。
要在 transform 之後執行（要用到前幾天的 stage）。
"""
from analyze import task
from common import cli, repository, runner
from common.date_utils import dates_between


def build_jobs(start: str, end: str) -> list[tuple]:
    """產生任務清單：每個日期一個 analyze_progress 任務。"""
    return [(task.analyze_progress, {"date_yyyymmdd": d}) for d in dates_between(start, end)]


def main() -> None:
    args = cli.parse_args()   # daily：今天；backfill：--start～--end
    repository.create_tables()   # 建表（已經有就跳過）

    failed = runner.run_jobs(build_jobs(args.start, args.end))
    runner.finish(failed)


if __name__ == "__main__":
    main()
