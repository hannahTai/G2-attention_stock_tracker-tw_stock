"""四個 producer（crawler、transform、analyze、ai）和 main.py 共用的指令參數。

    python -m xxx.producer                                        # daily：只處理今天（預設）
    python -m xxx.producer backfill --start 20261001              # 回補 20261001 到今天
    python -m xxx.producer backfill --start 20261001 --end 20261031
    python -m crawler.producer backfill --start 20261001 --part twse --refetch  # crawler：已經抓完整的也重抓
    python -m crawler.producer daily --part twse                  # crawler、transform：只做一條線的部分

--part：twse＝股價線的部分（證交所），news＝新聞線的部分（觀測站、新聞）。
crawler 一定要給；transform 不給就兩個都做（先證交所，再用剛整理好的注意股算新聞的範圍）。
news 模式（main.py news）：新聞線，處理今天。
main.py 也用這裡讀參數：期間在 main.py 算一次，再明確傳給每一步。
"""
import argparse

from common.date_utils import today_yyyymmdd


def parse_args(news_mode: bool = False) -> argparse.Namespace:
    """讀指令參數，回傳 start、end（都是 YYYYMMDD）和 refetch（要不要全部重抓）。
    news_mode（main.py 用）：多一個 news 子指令，沒有 --part（main.py 自己會給每一步）。"""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="mode")              # 模式（子指令）存到 args.mode；沒給就是 None（＝daily）
    daily = sub.add_parser("daily", help="只處理今天（預設）")
    if news_mode:
        sub.add_parser("news", help="新聞線，處理今天")
    backfill = sub.add_parser("backfill", help="回補一段期間")
    backfill.add_argument("--start", required=True, help="YYYYMMDD")
    backfill.add_argument("--end", default=today_yyyymmdd(), help="YYYYMMDD，預設今天")
    backfill.add_argument("--refetch", action="store_true",
                          help="只有 crawler 用：已經抓完整的也重抓（預設跳過）")
    for each in () if news_mode else (daily, backfill):
        each.add_argument("--part", choices=["twse", "news"], help="crawler（一定要給）、transform（不給就兩個都做）：只做一條線的部分")
    parser.set_defaults(part=None)   # 沒給子指令（daily）、main.py 時沒有 --part
    args = parser.parse_args()

    if args.mode != "backfill":
        args.start = args.end = today_yyyymmdd()
        args.refetch = False
    return args

