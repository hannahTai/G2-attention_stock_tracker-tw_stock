"""回測：規則引擎預測的處置（app_progress.punished_by）和真實處置公告（stage_punish）比對。

    uv run python -m analyze.backtest                          # 預設 2026/08/11 起（新制、30 日資料完整）
    uv run python -m analyze.backtest --start 20260811 --end 20261113

一致：同一天、同一檔，引擎說會處置、真的也處置了。
漏判：真的處置了，引擎沒算到。誤判：引擎說會處置，但沒有處置公告。
"""
import argparse

import pandas as pd
import sqlalchemy

from common import repository
from common.date_utils import today_yyyymmdd

# 處置公告的條件文字 → 引擎的條件代號（「及當日沖銷標準」只影響處置天數，不影響是哪一條）
CONDITION_PATHS = {
    "連續三次": "streak3",
    "連續五次": "streak5",
    "最近十個營業日已有六次": "in10",
    "最近三十個營業日已有十二次": "in30",
}


def condition_path(trigger_condition: str) -> str | None:
    """'連續三次及當日沖銷標準' → 'streak3'；認不得就回傳 None。"""
    for text, path in CONDITION_PATHS.items():
        if trigger_condition.startswith(text):
            return path
    return None


def read_punish_and_progress(start: str, end: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """讀真實處置和引擎的預測（同一段期間）。"""
    engine = repository.get_engine()
    params = {"start": start, "end": end}
    actual = pd.read_sql(sqlalchemy.text(
        "SELECT announce_date AS trade_date, stock_id, stock_name, trigger_condition "
        "FROM stage_punish WHERE announce_date BETWEEN :start AND :end"), engine, params=params)
    predicted = pd.read_sql(sqlalchemy.text(
        "SELECT trade_date, stock_id, punished_by, streak3, streak5, in10, in30, count_start, full_history "
        "FROM app_progress WHERE trade_date BETWEEN :start AND :end"), engine, params=params)
    for df in (actual, predicted):
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    return actual, predicted


def compare(actual: pd.DataFrame, predicted: pd.DataFrame) -> pd.DataFrame:
    """每一筆真實處置或引擎預測一列，標出 一致／漏判／誤判，以及條件有沒有對上。"""
    actual = actual.assign(actual_path=actual["trigger_condition"].map(condition_path))
    merged = actual.merge(predicted, on=["trade_date", "stock_id"], how="outer")

    def result(row):
        has_actual = pd.notna(row["trigger_condition"])
        has_predicted = pd.notna(row["punished_by"])
        if has_actual and has_predicted:
            return "一致"
        if has_actual:
            return "漏判"
        if has_predicted:
            return "誤判"
        return None   # 沒被處置、引擎也沒預測：不用列出來

    merged["result"] = merged.apply(result, axis=1)
    merged = merged[merged["result"].notna()].copy()
    merged["same_condition"] = [
        (path in str(by).split(",")) if pd.notna(path) and pd.notna(by) else None
        for path, by in zip(merged["actual_path"], merged["punished_by"])
    ]
    return merged.sort_values(["trade_date", "stock_id"]).reset_index(drop=True)


def report(result: pd.DataFrame) -> None:
    counts = result["result"].value_counts()
    hit, miss, false_alarm = counts.get("一致", 0), counts.get("漏判", 0), counts.get("誤判", 0)
    total = hit + miss + false_alarm
    print(f"真實處置 {hit + miss} 筆；引擎預測 {hit + false_alarm} 筆")
    print(f"一致 {hit}、漏判 {miss}、誤判 {false_alarm} → 一致率 {hit / total:.1%}（一致 ÷ 全部）" if total else "沒有資料")

    hits = result[result["result"] == "一致"]
    if len(hits):
        print(f"一致的處置裡，條件也對上的：{int(hits['same_condition'].sum())}／{len(hits)}")

    cols = ["trade_date", "stock_id", "stock_name", "trigger_condition", "punished_by",
            "streak3", "streak5", "in10", "in30", "count_start", "full_history"]
    for label in ("漏判", "誤判"):
        rows = result[result["result"] == label]
        if len(rows):
            print(f"\n== {label} ==")
            print(rows[cols].to_string(index=False))
    wrong_condition = hits[hits["same_condition"] == False]   # noqa: E712（要排除 None）
    if len(wrong_condition):
        print("\n== 一致但條件不同 ==")
        print(wrong_condition[cols].to_string(index=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="20260811", help="YYYYMMDD，預設 20260811（新制且有 30 日資料）")
    parser.add_argument("--end", default=today_yyyymmdd(), help="YYYYMMDD，預設今天")
    args = parser.parse_args()

    actual, predicted = read_punish_and_progress(args.start, args.end)
    report(compare(actual, predicted))


if __name__ == "__main__":
    main()
