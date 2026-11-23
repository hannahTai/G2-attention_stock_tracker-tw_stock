"""task 層（AI）：一個任務處理一筆資料、問 LLM、結果寫進 ai_* 表，回傳一行結果。

    classify_announcement：公告分類，一則重大訊息 → ai_announcement_label
    summarize_news：新聞摘要，一則新聞 → ai_news_summary
    embed_news：新聞 RAG，一天的新聞（標題＋新聞摘要）轉成向量 → ai_news_embedding（本機的 bge-m3，不呼叫 API）
    group_news_stories：同一件事，一段區間的新聞歸類 → ai_news_story（用新聞向量，不呼叫 API）
    summarize_card：卡片 AI 摘要，一檔一天 → ai_card_summary（輸入和前一天一樣就沿用，不呼叫 API）

問 LLM 和 crawler 打 API 同樣三步（asker 做好的 ask）：
    1. response = client.fetch_xxx(...)：呼叫 Claude API
    2. problem = client.json_answer_problem(response)：檢查回應
    3. repository.save_llm_call(紀錄, problem)：每次呼叫都記一列 ai_llm_call（token、花費）；有問題的存完丟 FetchError
       ref_key 一律記結果表的代號（公告 announcement_id、新聞 news_id、卡片 card_id），JOIN 結果表都是 ref_key ＝ 代號
同一個 prompt 版本已經有結果的就跳過（不重複花錢）；還沒有全文的公告也跳過，下次再分類。
摘要超過上限（公告 60 字；新聞依文章長度，見 summarizer.summary_limit）：帶著上次的回答請 LLM 縮短，只重問一次；
還是太長、或縮短失敗就用手上的回答照存，summary_ok＝否。
只讀 stage 表和前面的 AI 結果，不讀規則引擎的結果（app），所以和 analyze 同時跑。只寫自己的 ai_* 表，失敗不影響資料線和其他任務。
例外：summarize_news 要新聞全文，全文不存進資料庫（著作權），只能當場再抓一次網頁（用 crawler/client.py 的函式，不另寫爬蟲）。
每個函式都用 @app.task 註冊成 Celery 任務（見 common/worker.py）；要用本機 embedding 模型的（embed_news、summarize_card）
在 embed 佇列上跑，其他在 ai 佇列；
shorten_once、fix_numbers_once、fix_answer、asker、card_news 是任務用的小函式，不是任務。
"""
import requests

from ai import card_summarizer, classifier, client, embedder, retriever, summarizer, verifier
from common import date_utils, repository
from common.config import EMBED_MODEL, NEWS_LOOKBACK_DAYS
from common.date_utils import days_before, parse_yyyymmdd
from common.hash_utils import key_md5, text_md5
from common.worker import Job, app
from crawler import client as crawler_client


def shorten_once(ask, key: str, user_message: str, answer: dict, model: str, limit: int, shorten_messages) -> tuple[dict, str, str]:
    """摘要超過 limit 字：帶著上次的回答請 LLM 縮短，只重問一次。

    ask：asker 做好的 ask。limit、shorten_messages：classifier、summarizer 或 card_summarizer 的上限和縮短的說法。
    回傳 (回答, 模型, 附註)。縮短失敗（拒答、API 錯誤）就用手上的回答（已經付過錢），附註寫給結果那一行看。
    """
    if not verifier.too_long(answer["summary"], limit):
        return answer, model, ""
    try:
        return (*ask(key, shorten_messages(user_message, answer, limit)), "")
    except Exception as e:
        return answer, model, f"；縮短失敗，用第一次的回答（{type(e).__name__}）"


def fix_numbers_once(ask, key: str, user_message: str, answer: dict, model: str, source: str,
                     clean=lambda summary: summary) -> tuple[dict, str, str]:
    """摘要裡有原文沒有的數字（LLM 自己換算，例如 374,246仟元 寫成 3.74 億元）：帶著上次的回答、
    說哪幾個數字找不到，請 LLM 照原文的寫法改，只重問一次。還是不對就照存（summary_ok＝否，網站不顯示）。
    source：和 summary_ok 檢查同一份原文；clean：summary_ok 檢查前先怎麼整理摘要（卡片摘要拿掉（新聞 N）編號）。
    只重問「LLM 看到的訊息裡也沒有」的數字（真的編出來的）：日期、條款這些訊息裡有的，不叫 LLM 拿掉（不然會刪掉對的資訊）。
    在 shorten_once 之前做（改完數字再檢查長度）。
    回傳 (回答, 模型, 附註)。重問失敗（拒答、API 錯誤）就用手上的回答（已經付過錢）。
    """
    seen = verifier.numbers_in(user_message)
    missing = [n for n in verifier.missing_numbers(clean(answer["summary"]), source) if n not in seen]
    if not missing:
        return answer, model, ""
    try:
        return (*ask(key, verifier.fix_numbers_messages(user_message, answer, missing)), f"；{'、'.join(missing)} 不在資料裡，重問一次")
    except Exception as e:
        return answer, model, f"；數字重問失敗，用第一次的回答（{type(e).__name__}）"


def fix_answer(ask, key: str, user_message: str, answer: dict, model: str, source: str, limit: int, shorten_messages,
               clean=lambda summary: summary) -> tuple[dict, str, str]:
    """LLM 回答後的檢查和重問，三個 AI 項目共用：先改數字（fix_numbers_once），再檢查長度（shorten_once）。
    回傳 (回答, 模型, 附註)。"""
    answer, model, numbers_note = fix_numbers_once(ask, key, user_message, answer, model, source, clean)
    answer, model, shorten_note = shorten_once(ask, key, user_message, answer, model, limit, shorten_messages)
    return answer, model, numbers_note + shorten_note


def asker(task_name: str, fetch, prompt_version: str):
    """→ ask(ref_key, messages)：問一次 LLM（fetch，例如 client.fetch_card_summary），記一列 ai_llm_call
    （task＝task_name、ref_key＝結果表的代號），回傳 (回答, 實際回答的模型)。任務執行時才做，測試換掉的 fetch 才會用到。"""
    def ask(ref_key: str, messages: list[dict]) -> tuple[dict, str]:
        started = date_utils.now()
        response = fetch(messages)
        problem = client.json_answer_problem(response)
        repository.save_llm_call(client.call_record(task_name, ref_key, prompt_version, response, started), problem)
        return client.parse_answer(response), response.model
    return ask


@app.task(base=Job)
def classify_announcement(company_id: str, enter_yyyymmdd: str, serial_number: int) -> str:
    """一則重大訊息 → ai_announcement_label（類型、更正、有新消息、白話摘要）。"""
    enter_date = parse_yyyymmdd(enter_yyyymmdd)
    key = classifier.announcement_key(company_id, enter_date, serial_number)   # 9908_20261102_1：結果那一行看得懂
    announcement_id = key_md5(company_id, enter_yyyymmdd, serial_number)          # ai_llm_call.ref_key 記這個
    if repository.has_announcement_label(announcement_id, classifier.PROMPT_VERSION):
        return f"{key} 已經分類過（{classifier.PROMPT_VERSION}），跳過"
    row = repository.read_announcement(company_id, enter_date, serial_number)
    if row is None:
        return f"{key} 還沒有全文，跳過"

    user_message = classifier.build_user_message(row)
    ask = asker("classify_announcement", client.fetch_announcement_label, classifier.PROMPT_VERSION)
    answer, model = ask(announcement_id, [{"role": "user", "content": user_message}])
    answer, model, note = fix_answer(ask, announcement_id, user_message, answer, model, classifier.source_text(row),
                                     classifier.SUMMARY_MAX_CHARS, classifier.shorten_messages)
    label = classifier.to_label_row(row, answer, model)
    repository.insert_announcement_label(label)
    flags = "、".join(name for name, on in (("更正", label["is_correction"]), ("有新消息", label["has_news"])) if on)
    return f"ai_announcement_label/{key}：{label['category']}{f'（{flags}）' if flags else ''}{note}"


@app.task(base=Job)
def summarize_news(news_id: str) -> str:
    """一則新聞 → ai_news_summary：當場打開網頁抽全文，請 LLM 用自己的話寫摘要。

    全文只在記憶體裡，用完就丟，不存進資料庫（著作權）；存的只有摘要。
    網頁這次打不開（被擋、不是網頁）或全文太短就跳過，不問 LLM；5xx、429、逾時丟錯誤重試（requests 的錯誤）。
    摘要上限依文章長度（summarizer.summary_limit：文章的三成，80～1200 字）；
    超過就請 LLM 縮短，只重問一次（和公告分類一樣），還是太長就照存，summary_ok＝否。
    在 ai 佇列上跑（同時 2 個），沒有 NewsDetailJob 的間隔：要摘要的網頁分散在十幾個網站，同一個網站不會被連續打。
    """
    if repository.has_news_summary(news_id, summarizer.PROMPT_VERSION):
        return f"{news_id} 已經摘要過（{summarizer.PROMPT_VERSION}），跳過"
    news = repository.read_news(news_id)
    if news is None:
        return f"{news_id} 不在 stage_news，跳過"

    unreachable = crawler_client.unreachable_link_problem(news["link"])   # Google 轉址：不打
    if unreachable:
        return f"{news_id} {unreachable}，跳過"
    page = crawler_client.fetch_news_detail(news["link"])   # 逾時、連線失敗：requests 的錯誤直接丟出去（會重試）
    if page.problem:
        if page.status_code and repository.is_temporary_status(page.status_code):
            raise requests.HTTPError(page.problem)   # 5xx、429：丟 requests 的 HTTPError，runner／Celery 會重試
        return f"{news_id} 網頁打不開（{page.problem}），跳過"
    text = crawler_client.news_detail_text(page.content) or ""   # 全文只在記憶體裡
    if len(text) < summarizer.MIN_TEXT_CHARS:
        return f"{news_id} 全文太短（{len(text)} 字），跳過"

    limit = summarizer.summary_limit(text)   # 文章的三成，80～1200 字
    user_message = summarizer.build_user_message(news, text, limit)
    ask = asker("summarize_news", client.fetch_news_summary, summarizer.PROMPT_VERSION)
    answer, model = ask(news_id, [{"role": "user", "content": user_message}])
    source = f"{news['title']}\n{text}"   # 檢查摘要數字用的原文（數字重問、summary_ok 同一份）
    answer, model, note = fix_answer(ask, news_id, user_message, answer, model, source, limit, summarizer.shorten_messages)
    row = summarizer.to_summary_row(news_id, answer, source, limit, model)
    repository.insert_news_summary(row)
    return f"ai_news_summary/{news_id}：{row['summary'][:30]}{'' if row['summary_ok'] else '（summary_ok＝否）'}{note}"


# ---------- 新聞 RAG ----------
@app.task(base=Job)
def embed_news(date_yyyymmdd: str) -> str:
    """一天的新聞 → ai_news_embedding（EMBED_MODEL 的向量）。轉的是標題＋新聞摘要（retriever.news_text）；
    文字的雜湊值和上次一樣的跳過，摘要後來才寫好的會重轉。本機跑模型，不花錢。"""
    day = parse_yyyymmdd(date_yyyymmdd)
    todo = []   # (news_id, 文字, 文字的 md5)
    for row in repository.list_news_to_embed(day, day, EMBED_MODEL):
        text = retriever.news_text(row["title"], row["ai_summary"], row["lead"])
        text_hash = text_md5(text)
        if text_hash != row["input_hash"]:
            todo.append((row["news_id"], text, text_hash))
    if not todo:
        return f"{date_yyyymmdd} 沒有要轉的新聞"
    vectors = embedder.embed([text for _, text, _ in todo])
    repository.save_news_embeddings([{"news_id": news_id, "model": EMBED_MODEL, "embedding": embedder.to_pgvector(vector),
                                      "input_hash": text_hash} for (news_id, _, text_hash), vector in zip(todo, vectors, strict=True)])
    return f"ai_news_embedding/{date_yyyymmdd}：{len(todo)} 則"


@app.task(base=Job)
def group_news_stories(start_yyyymmdd: str, end_yyyymmdd: str) -> str:
    """區間內的新聞歸成同一件事 → ai_news_story（要在 embed_news 之後）。一天一天有先後（後面的歸到前面的），
    所以整段區間一個任務，不拆成每天一個（同時做會互相等不到）。不呼叫 API。"""
    news_ids, links, known = repository.news_story_links(parse_yyyymmdd(start_yyyymmdd), parse_yyyymmdd(end_yyyymmdd), EMBED_MODEL,
                                                         retriever.DUPLICATE_SIMILARITY, retriever.STORY_DAYS,
                                                         retriever.SAME_TITLE_MIN_CHARS)
    if not news_ids:
        return f"{start_yyyymmdd}～{end_yyyymmdd} 沒有要歸類的新聞"
    stories = retriever.assign_stories(news_ids, links, known)
    repository.save_news_stories([{"news_id": news_id, "model": EMBED_MODEL, "story_id": story_id}
                                  for news_id, story_id in stories.items()])
    grouped = sum(1 for news_id, story_id in stories.items() if news_id != story_id)
    return f"ai_news_story/{start_yyyymmdd}～{end_yyyymmdd}：{len(stories)} 則，{grouped} 則和別則是同一件事"


# ---------- 卡片 AI 摘要 ----------
def card_news(card: dict, announcements: list[dict]) -> list[dict]:
    """新聞 RAG：這檔最近 NEWS_LOOKBACK_DAYS 天（含當天）的新聞，挑出跟股票名稱、公告最相關的幾則（見 ai/retriever.py）。"""
    query = embedder.embed([retriever.query_text(card, announcements)])[0]
    day = card["trade_date"]
    start = parse_yyyymmdd(days_before(date_utils.to_yyyymmdd(day), NEWS_LOOKBACK_DAYS - 1))
    candidates = repository.stock_news_candidates(card["stock_id"], start, day, EMBED_MODEL, embedder.to_pgvector(query))
    return retriever.related_news(candidates)


@app.task(base=Job)
def summarize_card(stock_id: str, date_yyyymmdd: str) -> str:
    """一張卡片（一檔一天）→ ai_card_summary：2～3 句白話（股價最近怎麼了、公告和新聞裡可能的原因）。

    輸入（只讀 stage 和其他 AI 結果）：那天的注意股、處置、漲跌幅（repository.card_facts）、近 30 天公告（含公告分類）、
    新聞 RAG 挑出的新聞（有就帶，沒有或出錯照寫）。
    輸入的雜湊值和前一天一樣（例如處置中、沒有新公告、新聞）就沿用前一天的摘要，不呼叫 API。
    """
    key = f"{stock_id}_{date_yyyymmdd}"                       # 結果那一行看得懂
    card_id = key_md5(stock_id, date_yyyymmdd)                # ai_card_summary 的主鍵、ai_llm_call.ref_key
    if repository.has_card_summary(card_id, card_summarizer.PROMPT_VERSION):
        return f"{key} 已經寫過（{card_summarizer.PROMPT_VERSION}），跳過"
    day = parse_yyyymmdd(date_yyyymmdd)
    card = repository.card_facts(stock_id, day)
    if card is None:
        return f"{key} 那天不在卡片牆上（stage_watched_stock），跳過"
    announcements = repository.recent_announcements(stock_id, parse_yyyymmdd(days_before(date_yyyymmdd, 30)), day)
    note = ""
    try:
        news = card_news(card, announcements)
    except Exception as e:   # 新聞是選擇性的輸入：轉不了向量、查不到，照樣只用自己的資料寫
        news, note = [], f"；沒有帶新聞（{type(e).__name__}）"

    user_message = card_summarizer.build_user_message(card, announcements, news)
    previous = repository.previous_card_summary(stock_id, day, card_summarizer.PROMPT_VERSION)
    if previous and previous["input_hash"] == card_summarizer.input_hash(user_message):
        repository.insert_card_summary({**previous, "card_id": card_id, "card_date": day, "reused": True})
        return f"ai_card_summary/{key}：輸入和 {previous['card_date']} 一樣，沿用"

    ask = asker("summarize_card", client.fetch_card_summary, card_summarizer.PROMPT_VERSION)
    answer, model = ask(card_id, [{"role": "user", "content": user_message}])
    answer, model, fix_note = fix_answer(ask, card_id, user_message, answer, model, user_message,
                                         card_summarizer.SUMMARY_MAX_CHARS, card_summarizer.shorten_messages,
                                         card_summarizer.without_news_numbers)
    row = card_summarizer.to_card_row(card, card_id, answer, news, user_message, model)
    repository.insert_card_summary(row)
    return f"ai_card_summary/{key}：{row['summary'][:30]}{'' if row['summary_ok'] else '（summary_ok＝否）'}{note}{fix_note}"
