"""verifier：檢查 AI 的回答，公告分類（classifier.py）和新聞摘要（summarizer.py）共用。不呼叫 API、不連資料庫。"""
import re


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
