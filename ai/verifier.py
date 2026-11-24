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
    """摘要太長時重問的對話：原本的資料、LLM 上次的回答、說明超過幾個字，再接 request（各項目自己的縮短說法）；
    最後提醒數字照資料抄（縮短時不要把數字換算掉，不然會被 numbers_ok 擋下）。

    >>> shorten_messages("公告", {"summary": "一" * 70}, 60, "請縮短。")[-1]["content"]
    'summary 有 70 個字，超過 60 個字。請縮短。數字照資料裡的寫法抄，不要換算。'
    """
    return [
        {"role": "user", "content": user_message},
        {"role": "assistant", "content": json.dumps(answer, ensure_ascii=False)},
        {"role": "user", "content": f"summary 有 {len(answer['summary'].strip())} 個字，超過 {limit} 個字。{request}"
                                    "數字照資料裡的寫法抄，不要換算。"},
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


def missing_numbers(summary: str, source: str) -> list[str]:
    """摘要裡、原文沒有的數字（LLM 自己換算、編出來的）。

    >>> missing_numbers("淨利約 3.74 億元", "歸屬母公司淨利374,246仟元"), missing_numbers("淨利 374,246仟元", "淨利374,246仟元")
    (['3.74'], [])
    """
    return sorted(numbers_in(summary) - numbers_in(source))


def fix_numbers_messages(user_message: str, answer: dict, missing: list[str]) -> list[dict]:
    """摘要裡有原文沒有的數字時重問的對話：原本的資料、LLM 上次的回答，再說哪幾個數字找不到、要照原文寫法抄。

    >>> messages = fix_numbers_messages("資料", {"summary": "淨利約 3.74 億元"}, ["3.74"])
    >>> messages[-1]["content"].startswith("summary 裡的 3.74 在原文裡找不到。")
    True
    """
    return [
        {"role": "user", "content": user_message},
        {"role": "assistant", "content": json.dumps(answer, ensure_ascii=False)},
        {"role": "user", "content": f"summary 裡的 {'、'.join(missing)} 在原文裡找不到。數字只能照原文的寫法抄"
                                    "（例如原文寫 374,246仟元，就寫 374,246仟元），不要自己換算、四捨五入或推算；"
                                    "只在日期這類標頭、原文沒有的數字不要寫；不好抄的金額可以不寫金額、只講事情。"
                                    "其他照原本的規則，重新回答。"},
    ]


def numbers_ok(summary: str, source: str) -> bool:
    """摘要裡的每個數字都出現在原文裡 → True。LLM 自己換算、編出來的數字會被抓到。

    >>> numbers_ok("10月營收1520萬元", "本公司10月營收為1,520萬元")
    True
    >>> numbers_ok("10月營收0.15億元", "本公司10月營收為1,520萬元")
    False
    """
    return not missing_numbers(summary, source)
