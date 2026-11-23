"""client 層（AI）：只負責呼叫 Claude API，不存檔（存進 ai_llm_call 的是 task.py → repository.save_llm_call）。

  create_message()：送出一次請求，回傳原始回應（和 crawler/client.py 的 http_get 一樣，是共用的）
  fetch_xxx()：一個 AI 項目的請求：fetch_announcement_label()（公告分類）、fetch_news_summary()（新聞摘要）、
             fetch_card_summary()（卡片 AI 摘要），一則問一次
  xxx_problem()：檢查回應，有問題回傳原因（task 存成失敗），沒問題回傳 None
  parse_answer()、call_record()：從回應取值（回答、要記進 ai_llm_call 的 token 和花費）

- 結構化輸出（output_config.format）：API 保證回傳的文字是符合 schema 的 JSON，enum 以外的值不會出現。
- system prompt 標 cache_control：每則都一樣的部分可以快取（夠長才會真的快取，見 ai_llm_call.cache_read_tokens）。
- API 錯誤（429、5xx、連線失敗）照樣丟出 anthropic 的錯誤，由 runner.is_retryable 判斷要不要重做；anthropic 套件自己也會先重試 2 次。
金鑰從環境變數 ANTHROPIC_API_KEY 讀（.env），anthropic 套件自己讀。
"""
import datetime
import functools
import json

import anthropic

from ai import card_summarizer, classifier, summarizer
from common import date_utils
from common.config import AI_CARD_MODEL, AI_CLASSIFY_MODEL, AI_SUMMARY_MODEL

# 每百萬 token 的價格（美元）：(輸入, 輸出)。輸出含思考的 token。快取讀約 0.1 倍、寫約 1.25 倍輸入價
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}
CACHE_READ_RATE = 0.1
CACHE_WRITE_RATE = 1.25


# ---------- 呼叫 API：回傳原始回應 ----------
@functools.cache   # 整個行程共用一個 client（自己管理連線）
def get_anthropic() -> anthropic.Anthropic:
    """Claude API 的 client；金鑰從環境變數 ANTHROPIC_API_KEY 讀。"""
    return anthropic.Anthropic()


def create_message(model: str, system: str, messages: list[dict], schema: dict,
                   max_tokens: int = 2048, effort: str = "low") -> anthropic.types.Message:
    """送出一次請求，要求照 schema 回答 JSON。

    effort：思考的程度。分類、摘要這種簡單的工作用 low（思考的 token 照輸出價計費）。
    max_tokens：含思考的上限；被截斷（stop_reason＝max_tokens）由 json_answer_problem 抓出來。
    """
    return get_anthropic().messages.create(
        model=model,
        max_tokens=max_tokens,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=messages,
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
    )


def fetch_announcement_label(messages: list[dict]) -> anthropic.types.Message:
    """公告分類：messages 是一則公告（classifier.build_user_message），或摘要太長時重問的整段對話（classifier.shorten_messages）。"""
    return create_message(AI_CLASSIFY_MODEL, classifier.SYSTEM_PROMPT, messages, classifier.SCHEMA)


def fetch_news_summary(messages: list[dict]) -> anthropic.types.Message:
    """新聞摘要：messages 是一則新聞（summarizer.build_user_message，含當場抓的全文）。"""
    return create_message(AI_SUMMARY_MODEL, summarizer.SYSTEM_PROMPT, messages, summarizer.SCHEMA)


def fetch_card_summary(messages: list[dict]) -> anthropic.types.Message:
    """卡片 AI 摘要：messages 是一張卡片的資料（card_summarizer.build_user_message）。"""
    return create_message(AI_CARD_MODEL, card_summarizer.SYSTEM_PROMPT, messages, card_summarizer.SCHEMA)


# ---------- 檢查回應：有問題回傳原因（task 存成失敗），沒問題回傳 None ----------
def json_answer_problem(response) -> str | None:
    """沒有正常結束（refusal＝拒答、max_tokens＝被截斷，JSON 不完整）或回傳的不是 JSON → 回傳原因。重問也一樣，不重試。"""
    if response.stop_reason != "end_turn":
        return f"沒有正常結束（stop_reason={response.stop_reason}）"
    try:
        parse_answer(response)
    except ValueError:
        return f"回傳的不是 JSON：{answer_text(response)[:200]}"
    return None


# ---------- 從回應取值 ----------
def answer_text(response) -> str:
    return next((block.text for block in response.content if block.type == "text"), "")


def parse_answer(response) -> dict:
    """照 schema 回答的 JSON → dict。"""
    return json.loads(answer_text(response))


def cost_usd(model: str, input_tokens: int, output_tokens: int, cache_read: int = 0, cache_write: int = 0) -> float | None:
    """估計一次呼叫的花費（美元）；PRICES 裡沒有的模型回傳 None。

    >>> cost_usd("claude-haiku-5-5", 1_000_000, 100_000)
    0.15
    >>> cost_usd("unknown-model", 1, 1) is None
    True
    """
    if model not in PRICES:
        return None
    input_price, output_price = PRICES[model]
    total = (input_tokens + cache_read * CACHE_READ_RATE + cache_write * CACHE_WRITE_RATE) * input_price + output_tokens * output_price
    return round(total / 1_000_000, 6)


def call_record(task: str, ref_key: str, prompt_version: str, response, started: datetime.datetime) -> dict:
    """一次呼叫 → ai_llm_call 的一列（status、error 由 repository.save_llm_call 填）。"""
    usage = response.usage
    cache_read = usage.cache_read_input_tokens or 0
    cache_write = usage.cache_creation_input_tokens or 0
    return {
        "task": task,
        "ref_key": ref_key,
        "model": response.model,
        "prompt_version": prompt_version,
        "stop_reason": response.stop_reason,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "cost_usd": cost_usd(response.model, usage.input_tokens, usage.output_tokens, cache_read, cache_write),
        "seconds": round((date_utils.now() - started).total_seconds(), 3),
        "request_id": response._request_id,
        "called_at": started.replace(tzinfo=None),
    }
