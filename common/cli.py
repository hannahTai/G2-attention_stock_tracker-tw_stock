"""三個 producer（crawler、transform、analyze）共用的指令參數。

    python -m xxx.producer                                        # daily：只處理今天（預設）
    python -m xxx.producer backfill --start 20261001              # 回補 20261001 到今天
    python -m xxx.producer backfill --start 20261001 --end 20261031
    python -m crawler.producer backfill --start 20261001 --refetch  # crawler：已經抓完整的也重抓
"""
import argparse

from common.date_utils import today_yyyymmdd


def parse_args() -> argparse.Namespace:
    """讀指令參數，回傳 start、end（都是 YYYYMMDD）和 refetch（要不要全部重抓）。"""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="mode")              # 模式（子指令）存到 args.mode；沒給就是 None（＝daily）
    sub.add_parser("daily", help="只處理今天（預設）")
    backfill = sub.add_parser("backfill", help="回補一段期間")
    backfill.add_argument("--start", required=True, help="YYYYMMDD")
    backfill.add_argument("--end", default=today_yyyymmdd(), help="YYYYMMDD，預設今天")
    backfill.add_argument("--refetch", action="store_true",
                          help="只有 crawler 用：已經抓完整的也重抓（預設跳過）")
    args = parser.parse_args()

    if args.mode != "backfill":
        args.start = args.end = today_yyyymmdd()
        args.refetch = False
    return args


def date_range() -> tuple[str, str]:
    """要處理的期間 (start, end)，都是 YYYYMMDD。"""
    args = parse_args()
    return args.start, args.end
