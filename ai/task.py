"""task 層（AI）：一個任務處理一筆資料、問 LLM、結果寫進 ai_* 表，回傳一行結果。

    classify_announcement：公告分類，一則重大訊息 → ai_announcement_label
    summarize_news：新聞摘要，一則新聞 → ai_news_summary

問 LLM 和 crawler 打 API 同樣三步（label_once、summary_once）：
    1. response = client.fetch_xxx(...)：呼叫 Claude API
    2. problem = client.xxx_problem(response)：檢查回應
    3. repository.save_llm_call(紀錄, problem)：每次呼叫都記一列 ai_llm_call（token、花費）；有問題的存完丟 FetchError
       ref_key 一律記結果表的代號（公告 announcement_id、新聞 news_id），JOIN 結果表都是 ref_key ＝ 代號
同一個 prompt 版本已經有結果的就跳過（不重複花錢）；還沒有全文的公告也跳過，下次再分類。
摘要超過上限（公告 60 字；新聞依文章長度，見 summarizer.summary_limit）：帶著上次的回答請 LLM 縮短，只重問一次；
還是太長、或縮短失敗就用手上的回答照存，summary_ok＝否。
只讀 stage 表、只寫自己的 ai_* 表，失敗不影響資料線和其他任務。
例外：summarize_news 要新聞全文，全文不存進資料庫（著作權），只能當場再抓一次網頁（用 crawler/client.py 的函式，不另寫爬蟲）。
每個函式都用 @app.task 註冊成 Celery 任務（見 common/worker.py），在 ai 佇列上跑；shorten_once、label_once、summary_once 是任務用的小函式，不是任務。
"""
from ai import classifier, client, summarizer
from common import date_utils, repository
from common.date_utils import parse_yyyymmdd
from common.hash_utils import key_md5
from common.worker import Job, app
from crawler import client as crawler_client


def shorten_once(ask, key: str, user_message: str, answer: dict, model: str, limit: int, shorten_messages) -> tuple[dict, str, str]:
    """摘要超過 limit 字：帶著上次的回答請 LLM 縮短，只重問一次。

    ask：label_once 或 summary_once。limit、shorten_messages：classifier 或 summarizer 的上限和縮短的說法。
    回傳 (回答, 模型, 附註)。縮短失敗（拒答、API 錯誤）就用手上的回答（已經付過錢），附註寫給結果那一行看。
    """
    if len(answer["summary"].strip()) <= limit:
        return answer, model, ""
    try:
        return (*ask(key, shorten_messages(user_message, answer, limit)), "")
    except Exception as e:
        return answer, model, f"；縮短失敗，用第一次的回答（{type(e).__name__}）"


def label_once(announcement_id: str, messages: list[dict]) -> tuple[dict, str]:
    """問一次公告分類，記一列 ai_llm_call（ref_key＝announcement_id），回傳 (回答, 實際回答的模型)。"""
    started = date_utils.now()
    response = client.fetch_announcement_label(messages)
    problem = client.announcement_label_problem(response)
    repository.save_llm_call(client.call_record("classify_announcement", announcement_id, classifier.PROMPT_VERSION, response, started), problem)
    return client.parse_answer(response), response.model


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
    answer, model = label_once(announcement_id, [{"role": "user", "content": user_message}])
    answer, model, note = shorten_once(label_once, announcement_id, user_message, answer, model,
                                       classifier.SUMMARY_MAX_CHARS, classifier.shorten_messages)
    label = classifier.to_label_row(row, answer, model)
    repository.insert_announcement_label(label)
    flags = "、".join(name for name, on in (("更正", label["is_correction"]), ("有新消息", label["has_news"])) if on)
    return f"ai_announcement_label/{key}：{label['category']}{f'（{flags}）' if flags else ''}{note}"


def summary_once(news_id: str, messages: list[dict]) -> tuple[dict, str]:
    """問一次新聞摘要，記一列 ai_llm_call（ref_key＝news_id），回傳 (回答, 實際回答的模型)。"""
    started = date_utils.now()
    response = client.fetch_news_summary(messages)
    problem = client.news_summary_problem(response)
    repository.save_llm_call(client.call_record("summarize_news", news_id, summarizer.PROMPT_VERSION, response, started), problem)
    return client.parse_answer(response), response.model


@app.task(base=Job)
def summarize_news(news_id: str) -> str:
    """一則新聞 → ai_news_summary：當場打開網頁抽全文，請 LLM 用自己的話寫摘要。

    全文只在記憶體裡，用完就丟，不存進資料庫（著作權）；存的只有摘要。
    網頁這次打不開（被擋、不是網頁）或全文太短就跳過，不問 LLM；5xx、429、逾時丟錯誤重試（requests 的錯誤）。
    摘要上限依文章長度（summarizer.summary_limit：文章的三成，80～1200 字）；超過就請 LLM 縮短，只重問一次（和公告分類一樣），還是太長就照存，summary_ok＝否。
    在 ai 佇列上跑（同時 2 個），沒有 NewsDetailJob 的間隔：要摘要的網頁分散在十幾個網站，同一個網站不會被連續打。
    """
    if repository.has_news_summary(news_id, summarizer.PROMPT_VERSION):
        return f"{news_id} 已經摘要過（{summarizer.PROMPT_VERSION}），跳過"
    news = repository.read_news(news_id)
    if news is None:
        return f"{news_id} 不在 stage_news，跳過"

    resp = crawler_client.fetch_news_detail(news["link"])   # 逾時、連線失敗：requests 的錯誤直接丟出去（會重試）
    problem = crawler_client.news_detail_problem(resp)
    if problem:
        if repository.is_temporary_status(resp.status_code):
            resp.raise_for_status()   # 5xx、429：丟 requests 的 HTTPError，runner／Celery 會重試
        return f"{news_id} 網頁打不開（{problem}），跳過"
    text = crawler_client.news_detail_text(resp.content) or ""   # 全文只在記憶體裡
    if len(text) < summarizer.MIN_TEXT_CHARS:
        return f"{news_id} 全文太短（{len(text)} 字），跳過"

    limit = summarizer.summary_limit(text)   # 文章的三成，80～1200 字
    user_message = summarizer.build_user_message(news, text, limit)
    answer, model = summary_once(news_id, [{"role": "user", "content": user_message}])
    answer, model, note = shorten_once(summary_once, news_id, user_message, answer, model, limit, summarizer.shorten_messages)
    row = summarizer.to_summary_row(news_id, answer, f"{news['title']}\n{text}", limit, model)
    repository.insert_news_summary(row)
    return f"ai_news_summary/{news_id}：{row['summary'][:30]}{'' if row['summary_ok'] else '（summary_ok＝否）'}{note}"
