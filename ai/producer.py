"""producer 層（AI）：決定這一輪要分類哪些公告、摘要哪些新聞、轉哪些向量、寫哪些卡片摘要。

    uv run python -m ai.producer                            # 只處理今天（daily）
    uv run python -m ai.producer backfill --start 20261101  # 處理一段期間（可加 --end）

只讀 stage 和前面的 AI 結果，不讀規則引擎的結果（app），所以 main.py 讓它和 analyze 同時跑（要在 transform 之後）。

任務清單的格式和其他 producer 一樣，交給 common/runner.py：
- 公告分類：區間內發言、有全文、這個 prompt 版本還沒分類的公告 → ai_announcement_label（佇列 ai）
- 新聞摘要：區間內（往前多 NEWS_LOOKBACK_DAYS 天）被關注的股票、有抓到網頁、全文夠長、這一版還沒摘要的新聞 → ai_news_summary（佇列 ai）
- 新聞 RAG：區間內（往前多 NEWS_LOOKBACK_DAYS 天）每天一個任務，標題＋新聞摘要轉成向量 → ai_news_embedding
  （佇列 embed，不用 API key；要在新聞摘要之後）
- 同一件事：整段區間（往前多 NEWS_LOOKBACK_DAYS 天）一個任務，好幾家報同一件事的新聞歸在一起 → ai_news_story（佇列 ai，不用 API key）
- 卡片 AI 摘要：區間內每天被列注意、在注意累計名單上的上市公司，一檔一天一個任務 → ai_card_summary
  （佇列 embed：查詢也要轉向量；要在公告分類、新聞 RAG 之後）
順序：公告分類 → 新聞摘要 → 新聞 RAG → 同一件事 → 卡片 AI 摘要。
沒設 ANTHROPIC_API_KEY 就跳過要呼叫 Claude 的項目（新聞 RAG 的向量照樣轉，只用標題和導言），不算失敗。失敗只影響 AI 的結果，規則引擎算的還差幾次不受影響。
新聞（FinMind 清單、新聞網頁的導言）由 crawler、transform 抓和整理；摘要要的全文由 task 當場再抓（見 ai/task.py）。
"""
from ai import card_summarizer, classifier, summarizer, task
from common import cli, repository, runner
from common.config import ANTHROPIC_API_KEY, NEWS_LOOKBACK_DAYS
from common.date_utils import dates_between, days_before, parse_yyyymmdd, to_yyyymmdd


def build_classify_jobs(start: str, end: str) -> list[tuple]:
    """公告分類：區間內發言、有全文、這一版還沒分類的公告各一個任務。"""
    rows = repository.list_unlabeled_announcements(parse_yyyymmdd(start), parse_yyyymmdd(end), classifier.PROMPT_VERSION)
    return [(task.classify_announcement,
             {"company_id": company_id, "enter_yyyymmdd": to_yyyymmdd(enter_date), "serial_number": serial_number})
            for company_id, enter_date, serial_number in rows]


def build_summary_jobs(start: str, end: str) -> list[tuple]:
    """新聞摘要：區間內（crawler 往前多抓 NEWS_LOOKBACK_DAYS 天，這裡也往前多看）被關注的股票（被列注意、處置中）、有抓到網頁、
    全文至少 summarizer.MIN_TEXT_CHARS 字、這一版還沒摘要的新聞各一個任務。"""
    news_ids = repository.list_unsummarized_news(parse_yyyymmdd(days_before(start, NEWS_LOOKBACK_DAYS)), parse_yyyymmdd(end),
                                                 summarizer.PROMPT_VERSION, summarizer.MIN_TEXT_CHARS)
    return [(task.summarize_news, {"news_id": news_id}) for news_id in news_ids]


def build_embed_jobs(start: str, end: str) -> list[tuple]:
    """新聞 RAG：區間內（往前多 NEWS_LOOKBACK_DAYS 天，卡片要看最近幾天的新聞）每天一個任務。"""
    return [(task.embed_news, {"date_yyyymmdd": d}) for d in dates_between(days_before(start, NEWS_LOOKBACK_DAYS), end)]


def build_story_jobs(start: str, end: str) -> list[tuple]:
    """同一件事：整段區間（往前多 NEWS_LOOKBACK_DAYS 天）一個任務（天和天有先後，不拆開）。"""
    return [(task.group_news_stories, {"start_yyyymmdd": days_before(start, NEWS_LOOKBACK_DAYS), "end_yyyymmdd": end})]


def build_card_jobs(start: str, end: str) -> list[tuple]:
    """卡片 AI 摘要：區間內每天的注意股、聽牌股（上市公司），這一版還沒寫的各一個任務。
    照日期排：輸入和前一天一樣就沿用前一天的摘要，要前一天先寫好（embed worker 一次做一個，照送出的順序做）。"""
    rows = repository.list_unsummarized_cards(parse_yyyymmdd(start), parse_yyyymmdd(end), card_summarizer.PROMPT_VERSION)
    return [(task.summarize_card, {"stock_id": stock_id, "date_yyyymmdd": to_yyyymmdd(day)}) for stock_id, day in rows]


def run_all(start: str, end: str) -> list[str]:
    """公告分類 → 新聞摘要 → 新聞 RAG → 同一件事 → 卡片 AI 摘要，回傳失敗的任務。

    用到本機 embedding 模型的任務送 embed 佇列（只有 celery-embed 載入模型），其他送 ai。
    要呼叫 Claude 的項目沒有 API key 就跳過；向量、同一件事不用 key，照樣跑（夾在中間，順序不能換）。
    """
    failed = []
    if ANTHROPIC_API_KEY:
        failed += runner.run_jobs(build_classify_jobs(start, end), queue="ai")
        failed += runner.run_jobs(build_summary_jobs(start, end), queue="ai")
    else:
        print("沒設 ANTHROPIC_API_KEY，跳過公告分類、新聞摘要、卡片 AI 摘要")
    failed += runner.run_jobs(build_embed_jobs(start, end), queue="embed")   # 轉標題＋新聞摘要，排在新聞摘要後面
    failed += runner.run_jobs(build_story_jobs(start, end), queue="ai")      # 用新聞向量，不用 API key
    if ANTHROPIC_API_KEY:
        failed += runner.run_jobs(build_card_jobs(start, end), queue="embed")   # 用到公告分類、新聞 RAG 的結果，排最後
    return failed


def main() -> None:
    args = cli.parse_args()   # daily：今天；backfill：--start～--end
    repository.create_tables()   # 建表（已經有就跳過）
    runner.finish(run_all(args.start, args.end))


if __name__ == "__main__":
    main()
