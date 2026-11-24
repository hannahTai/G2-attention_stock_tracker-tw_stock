"""AI 的品質和花費：最近幾天每個 AI 項目、每個 prompt 版本問了幾筆、重問幾次、失敗幾次、多少錢、幾成沒通過檢查。

    uv run python scripts/ai_report.py            # 最近 7 天
    uv run python scripts/ai_report.py --days 30  # 最近 30 天

改了 prompt（換版本）以後跑一次，和舊版比：重問、沒通過檢查變多，就是新版比較差。
- 重問：同一筆問了不只一次（摘要裡有原文沒有的數字、太長，請 LLM 改一次；ai/task.py 的 fix_answer）。
  也包含失敗後下一輪再問、卡片有新公告的重寫，所以公告多的日子卡片的重問會比較高
- 沒通過檢查：summary_ok＝否，網站不顯示這段摘要（數字對不上、太長）
- 卡片的沿用：輸入和前一天一樣，沒有呼叫 API
- 新聞跳過：網頁打不開、全文太短，沒有問 LLM（ai_news_summary 的 model＝skipped，1 天後再試）
只讀資料庫（ai_* 表），不呼叫 API。
"""
import argparse

from common import repository

# 每個 AI 項目的結果表、時間欄位（沒通過檢查的比例從這裡算）
RESULT_TABLES = {
    "classify_announcement": ("ai_announcement_label", "labeled_at"),
    "summarize_news": ("ai_news_summary", "summarized_at"),
    "summarize_card": ("ai_card_summary", "summarized_at"),
}


def llm_calls(days: int) -> list[dict]:
    """每個項目、每個版本：問了幾筆（ref_key）、呼叫幾次、重問幾次、失敗幾次、花多少。"""
    rows = repository.query_rows(
        "SELECT task, prompt_version, count(DISTINCT ref_key) AS items, count(*) AS calls, "
        "       count(*) - count(DISTINCT ref_key) AS re_asks, count(*) FILTER (WHERE status = 'failure') AS failures, "
        "       COALESCE(sum(cost_usd), 0) AS cost "
        "FROM tw_stock.ai_llm_call WHERE called_at >= LOCALTIMESTAMP - make_interval(days => :days) "
        "GROUP BY task, prompt_version ORDER BY task, prompt_version",
        {"days": days})
    return [row._asdict() for row in rows]


def results(days: int) -> dict[tuple[str, str], dict]:
    """{(項目, 版本): {寫了幾筆, 沒通過檢查幾筆, 沿用幾筆}}；只有卡片有沿用。新聞跳過的（沒有問 LLM）不算。"""
    out = {}
    for task, (table, time_column) in RESULT_TABLES.items():
        reused = "count(*) FILTER (WHERE reused)" if table == "ai_card_summary" else "0"
        not_skipped = "AND model <> :skipped" if table == "ai_news_summary" else ""
        rows = repository.query_rows(
            f"SELECT prompt_version, count(*) AS written, count(*) FILTER (WHERE NOT summary_ok) AS not_ok, {reused} AS reused "
            f"FROM tw_stock.{table} WHERE {time_column} >= LOCALTIMESTAMP - make_interval(days => :days) {not_skipped} "
            f"GROUP BY prompt_version",
            {"days": days, "skipped": repository.NEWS_SKIPPED_MODEL})
        for row in rows:
            out[(task, row.prompt_version)] = row._asdict()
    return out


def news_skips(days: int) -> int:
    """目前在「跳過」狀態的新聞幾則（網頁打不開、全文太短，沒有問 LLM）。跳過的那一列會被下一次的結果蓋掉，
    所以算的是「現在還是跳過的」，不是跳過了幾次。"""
    return repository.query_rows(
        "SELECT count(*) FROM tw_stock.ai_news_summary "
        "WHERE model = :skipped AND summarized_at >= LOCALTIMESTAMP - make_interval(days => :days)",
        {"days": days, "skipped": repository.NEWS_SKIPPED_MODEL})[0][0]


def percent(part: int, whole: int) -> str:
    """幾成，例如 12.5%；分母是 0 寫「—」。

    >>> percent(1, 8), percent(0, 0)
    ('12.5%', '—')
    """
    return f"{part / whole * 100:.1f}%" if whole else "—"


def report(days: int) -> str:
    """整份報表（純文字）。"""
    calls = llm_calls(days)
    written = results(days)
    lines = [f"最近 {days} 天的 AI（只算這段期間問的、寫的）",
             f"{'項目':<22}{'版本':<6}{'問幾筆':>8}{'重問':>8}{'失敗':>6}{'花費(USD)':>11}{'寫了':>8}{'沒通過檢查':>10}{'沿用':>8}"]
    keys = sorted({(c["task"], c["prompt_version"]) for c in calls} | set(written))
    by_key = {(c["task"], c["prompt_version"]): c for c in calls}
    for key in keys:
        c = by_key.get(key, {"items": 0, "re_asks": 0, "failures": 0, "cost": 0})
        w = written.get(key, {"written": 0, "not_ok": 0, "reused": 0})
        lines.append(f"{key[0]:<22}{key[1]:<6}{c['items']:>8}{percent(c['re_asks'], c['items']):>8}{c['failures']:>6}"
                     f"{float(c['cost']):>11.4f}{w['written']:>8}{percent(w['not_ok'], w['written']):>10}"
                     f"{percent(w['reused'], w['written']) if key[0] == 'summarize_card' else '':>8}")
    total = sum(float(c["cost"]) for c in calls)
    lines += [f"合計花費 US${total:.4f}（每天約 US${total / days:.4f}）",
              f"目前在跳過狀態的新聞（網頁打不開、全文太短，沒有問 LLM）：{news_skips(days)} 則"]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7, help="最近幾天（預設 7）")
    days = parser.parse_args().days
    if days < 1:
        parser.error("--days 至少 1")
    print(report(days))


if __name__ == "__main__":
    main()
