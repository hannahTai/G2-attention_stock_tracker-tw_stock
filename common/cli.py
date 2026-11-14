"""三個 producer（crawler、transform、analyze）共用的指令參數。

    python -m xxx.producer                                        # daily：只處理今天（預設）
    python -m xxx.producer backfill --start 20261001              # 回補 20261001 到今天
    python -m xxx.producer backfill --start 20261001 --end 20261031
"""
import argparse

from common.date_utils import today_yyyymmdd


def date_range() -> tuple[str, str]:
    """讀指令參數，回傳要處理的期間 (start, end)，都是 YYYYMMDD。"""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="mode")              # 模式（子指令）存到 args.mode；沒給就是 None（＝daily）
    sub.add_parser("daily", help="只處理今天（預設）")
    backfill = sub.add_parser("backfill", help="回補一段期間")
    backfill.add_argument("--start", required=True, help="YYYYMMDD")
    backfill.add_argument("--end", default=today_yyyymmdd(), help="YYYYMMDD，預設今天")
    args = parser.parse_args()

    if args.mode == "backfill":
        return args.start, args.end
    today = today_yyyymmdd()
    return today, today
