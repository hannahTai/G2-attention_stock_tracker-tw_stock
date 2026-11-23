"""verifier：檢查 AI 的回答，公告分類（classifier.py）、新聞摘要（summarizer.py）、卡片 AI 摘要（card_summarizer.py）共用。
不呼叫 API、不連資料庫。"""
import json
import re


def too_long(summary: str, limit: int) -> bool:
    """摘要超過 limit 字（前後空白不算）。

    >>> too_long("一" * 60, 60), too_long("一" * 61, 60)
    (False, True)
    """
    return len(summary.strip()) > limit


def shorten_messages(user_message: str, answer: dict, limit: int, request: str) -> list[dict]:
    """摘要太長時重問的對話：原本的資料、LLM 上次的回答、說明超過幾個字，再接 request（各項目自己的縮短說法）。

    >>> shorten_messages("公告", {"summary": "一" * 70}, 60, "請縮短。")[-1]["content"]
    'summary 有 70 個字，超過 60 個字。請縮短。'
    """
    return [
        {"role": "user", "content": user_message},
        {"role": "assistant", "content": json.dumps(answer, ensure_ascii=False)},
        {"role": "user", "content": f"summary 有 {len(answer['summary'].strip())} 個字，超過 {limit} 個字。{request}"},
    ]


def numbers_in(text: str) -> set[str]:
    """文字裡的數字（去掉千分位逗號；小數點後面多的 0 去掉，350.00、350.0、350 算同一個數）。

    >>> sorted(numbers_in("10月營收1,520萬元，年增31.5%"))
    ['10', '1520', '31.5']
    >>> sorted(numbers_in("價差達350.00元、漲幅31.50%、0.80倍"))
    ['0.8', '31.5', '350']
    """
    numbers = re.findall(r"\d+(?:\.\d+)?", text.replace(",", ""))
    return {n.rstrip("0").rstrip(".") if "." in n else n for n in numbers}


def numbers_ok(summary: str, source: str) -> bool:
    """摘要裡的每個數字都出現在原文裡 → True。LLM 自己換算、編出來的數字會被抓到。

    >>> numbers_ok("10月營收1520萬元", "本公司10月營收為1,520萬元")
    True
    >>> numbers_ok("10月營收0.15億元", "本公司10月營收為1,520萬元")
    False
    """
    return numbers_in(summary) <= numbers_in(source)
