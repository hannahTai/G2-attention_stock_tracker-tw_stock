"""producer 層（AI）：決定這一輪要分類哪些公告、摘要哪些新聞。要在 transform 之後執行（要用 stage 的資料）。

    uv run python -m ai.producer                            # 只處理今天（daily）
    uv run python -m ai.producer backfill --start 20261101  # 處理一段期間（可加 --end）

任務清單的格式和其他 producer 一樣，交給 common/runner.py：
- ① 公告分類：區間內發言、有全文、這個 prompt 版本還沒分類的公告 → ai_announcement_label（佇列 ai）
- 新聞摘要：區間內（往前多 NEWS_LOOKBACK_DAYS 天）有抓到網頁、全文夠長、這一版還沒摘要的新聞 → ai_news_summary（佇列 ai）
沒設 ANTHROPIC_API_KEY 就整批跳過，不算失敗。失敗只影響 AI 的結果，資料線（crawler、transform、analyze）已經跑完了。
新聞（FinMind 清單、新聞網頁的導言）由 crawler、transform 抓和整理；摘要要的全文由 task 當場再抓（見 ai/task.py）。
"""
from ai import classifier, summarizer, task
from common import cli, repository, runner
from common.config import ANTHROPIC_API_KEY, NEWS_LOOKBACK_DAYS
from common.date_utils import days_before, parse_yyyymmdd, to_yyyymmdd


def build_classify_jobs(start: str, end: str) -> list[tuple]:
    """① 公告分類：區間內發言、有全文、這一版還沒分類的公告各一個任務。"""
    rows = repository.list_unlabeled_announcements(parse_yyyymmdd(start), parse_yyyymmdd(end), classifier.PROMPT_VERSION)
    return [(task.classify_announcement,
             {"company_id": company_id, "enter_yyyymmdd": to_yyyymmdd(enter_date), "serial_number": serial_number})
            for company_id, enter_date, serial_number in rows]


def build_summary_jobs(start: str, end: str) -> list[tuple]:
    """新聞摘要：區間內（crawler 往前多抓 NEWS_LOOKBACK_DAYS 天，這裡也往前多看）、有抓到網頁、
    全文至少 summarizer.MIN_TEXT_CHARS 字、這一版還沒摘要的新聞各一個任務。"""
    news_ids = repository.list_unsummarized_news(parse_yyyymmdd(days_before(start, NEWS_LOOKBACK_DAYS)), parse_yyyymmdd(end),
                                                 summarizer.PROMPT_VERSION, summarizer.MIN_TEXT_CHARS)
    return [(task.summarize_news, {"news_id": news_id}) for news_id in news_ids]


def main() -> None:
    args = cli.parse_args()   # daily：今天；backfill：--start～--end
    repository.create_tables()   # 建表（已經有就跳過）

    if not ANTHROPIC_API_KEY:
        print("沒設 ANTHROPIC_API_KEY，跳過 AI 項目")
        return
    failed = runner.run_jobs(build_classify_jobs(args.start, args.end), queue="ai")
    failed += runner.run_jobs(build_summary_jobs(args.start, args.end), queue="ai")
    runner.finish(failed)


if __name__ == "__main__":
    main()
