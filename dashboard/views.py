"""把 API 給的資料（app_card 等表的列）整理成畫面要顯示的東西。不碰網路，可以單獨測試（uv run pytest dashboard）。

讀這個檔案的順序：
  1. status()                一張卡片現在是什麼狀態（全部畫面判斷的起點）
  2. rule_for()              處置的措施（撮合間隔、預收、延長天數），8/10 前後規則不同
  3. wall_card()             總覽一檔：大字、標籤、資訊列
  4. overview()              總覽：分區、分頁數字、「只看」篩選、搜尋
  5. timeline()              個股頁「處置紀錄」：一列一個日期
  6. notice_cells()          個股頁近 30 個營業日的注意紀錄（30 格、注意紀錄列表）
  7. announcement_groups()   個股頁公開資訊
  8. stock_page()            個股頁要的全部東西

命名：畫面要的東西用名詞（status、headline、timeline、announcement_groups…），回傳 dict 或 dataclass；
判斷用的小函式也用名詞（section_of、filters_of、rule_for）；只有布林值用 is_、has_ 開頭。
kind（seal、amber、ok…）對應 static/style.css 的 class（.big.seal、.pill.amber…）。

欄位意思見 docs/tables.md 的 app_progress；處置規則見 docs/rules.md。
"""
import dataclasses
import datetime
import decimal
import re

# ---------- 固定資料 ----------
# 四條路徑：(畫面上的名稱, 門檻)
PATHS = {
    "streak3": ("連 3 日列第 1 款", 3),     # 連續 3 個營業日被列第 1 款
    "streak5": ("連 5 日被列注意", 5),      # 連續 5 個營業日被列注意（第 1～8 款）
    "in10": ("10 日內被列 6 日", 6),        # 最近 10 個營業日內有 6 日被列注意
    "in30": ("30 日內被列 12 日", 12),      # 最近 30 個營業日內有 12 日被列注意
}

# 注意股款次的簡短名稱（要點第四條）；第 1～8 款會算進處置次數
CLAUSE_NAMES = {
    1: "累積漲跌幅異常", 2: "起迄漲跌幅異常", 3: "漲跌＋成交量放大", 4: "漲跌＋週轉率過高",
    5: "漲跌＋券商占比過高", 6: "本益比淨值比＋週轉率", 7: "漲跌＋券資比放大", 8: "存託憑證溢折價",
    9: "日均量放大", 10: "累積週轉率過高", 11: "起迄價差異常", 12: "借券賣出占比高",
    13: "當沖占比過高", 14: "監視會報決議",
}

# 總覽的分區：(代號, 標題, 說明＝分頁 ⓘ 裡這一區是什麼)。每檔只會在一區（section_of() 決定），所以各區加起來＝全部
# 順序照一檔股票會經過的階段：聽牌 → 即將處置 → 處置中 → 明日出關；「其他」（差 2 次以上）檔數多，放最後
SECTIONS = [
    ("one", "聽牌", "下一個營業日再被列注意就處置"),
    ("soon", "即將處置", "已達處置標準，處置還沒開始"),
    ("punish", "處置中", "正在處置期間：撮合變慢、大額委託要先預收"),
    ("leave_tomorrow", "明日出關", "今天是處置的最後一天，下一個營業日恢復正常交易"),
    ("rest", "其他", "離處置還差 2 次以上"),
]

# 總覽的分頁＝全部＋各分區，數字加起來一樣
TABS = [("all", "全部")] + [(key, title) for key, title, _ in SECTIONS]
TAB_KEYS = [key for key, _ in TABS]
TAB_NOTES = {key: note for key, _, note in SECTIONS}   # 分頁旁 ⓘ 的說明（「全部」沒有）

# 分頁裡沒有股票時的說明：講清楚是「這天沒有這種股票」（正常），不是資料出問題
EMPTY_TEXT = {
    "all": "沒有需要注意的股票",
    "one": "沒有聽牌的股票",
    "soon": "沒有即將處置的股票",
    "punish": "沒有處置中的股票",
    "leave_tomorrow": "沒有明日出關的股票",
    "rest": "沒有差 2 次以上的股票",
}

# 「只看」篩選：和分頁不同，一檔可以同時符合好幾個，所以做成可以開關的篩選，不當分頁
FILTERS = [
    ("listed", "今日注意股"),
    ("leave_today", "今日出關"),
]
FILTER_KEYS = [key for key, _ in FILTERS]
FILTER_LABELS = dict(FILTERS)

RECENT_DAYS = 7   # 個股頁的注意紀錄、處置紀錄（以前的處置）、公開資訊先顯示最近幾天（日曆天，含當天），更早的收起來

# 公開資訊的類別標籤分 5 種顏色，看顏色就知道是哪一類公告（條款意思見 schema/stage.sql 的 stage_announcement_clause）。
# 只看條款分「事件種類」，不判斷利多利空。沒列在這裡的（第51款其他重大事項等）是 other，灰色
CLAUSE_GROUPS = {
    # alert 需要留意（紅）：訴訟、退票、停工、重整、舞弊、災難…
    "alert": [1, 2, 3, 5, 19, 25, 26, 28, 30, 34, 37, 42],
    # finance 財務（藍）：財務報告、財務預測、股利、庫藏股、會計變更、內控、海外財報差異
    "finance": [9, 13, 14, 29, 31, 35, 47],
    # deal 交易、投資、籌資（琥珀）：營業讓與、重要契約、減資合併增資、投資計畫、取得處分資產、背書保證、資金貸與、私募、公開收購…
    "deal": [4, 10, 11, 15, 16, 20, 22, 23, 24, 36, 38, 41, 45],
    # governance 公司治理（紫）：董事長總經理、會計師、發言人異動、法說會、股東會、競業許可、股權變動、委員會意見
    "governance": [6, 7, 8, 12, 17, 18, 21, 33, 44],
}
CLAUSE_GROUP = {f"第{n}款": group for group, numbers in CLAUSE_GROUPS.items() for n in numbers}

# 30 格注意紀錄：深藍＝計入本次累計，淺藍＝已計入前次處置（算過、用掉了）；第 9～14 款不列入累計，和沒被列一樣留白
CELL_SHADE = {"counted": "on", "not_counted": "", "reset": "reset", "none": ""}   # 格子的 class
CELL_NOTE = {"counted": "計入本次累計", "not_counted": "第 9～14 款，不列入累計", "reset": "已計入前次處置", "none": "未列注意"}
MONTH_LABEL_GAP = 3   # 月份標籤至少隔幾格才不會疊在一起（「10月」大約佔 3 格寬）

# 公告全文的排版（reflow()）
TABLE_LINE = re.compile(r"\S {2,}\S")    # 字中間有 2 個以上空白：用空白對齊的表格
NEW_ITEM = re.compile(r"\s*(\d+[.、．]|[(（][一二三四五六七八九十\d]+[)）]|[一二三四五六七八九十]+、|[A-Za-z][.、)）])")
LINE_END = ("。", "：", ":", "！", "？")

NEW_RULE_START = datetime.date(2026, 8, 10)   # 處置新制開始的日子（期間縮短、撮合改 2 分鐘）
MARKET_OPEN = datetime.time(9, 0)
MARKET_CLOSE = datetime.time(14, 30)          # 含盤後定價交易


# ---------- 日期、數字的寫法 ----------
def md(day: datetime.date | None) -> str:
    """日期 → 月/日。

    >>> md(datetime.date(2026, 11, 3))
    '11/03'
    >>> md(None)
    '—'
    """
    return day.strftime("%m/%d") if day else "—"


def md_range(start: datetime.date | None, end: datetime.date | None) -> str:
    """起迄日 → 11/24–11/30。

    >>> md_range(datetime.date(2026, 11, 24), datetime.date(2026, 11, 30))
    '11/24–11/30'
    """
    return f"{md(start)}–{md(end)}"


def weekday(day: datetime.date) -> str:
    """星期幾（一～日）。

    >>> weekday(datetime.date(2026, 11, 20))
    '五'
    """
    return "一二三四五六日"[day.weekday()]


def roc(day: datetime.date) -> str:
    """西元日期 → 民國年，例如 115/11/20。

    >>> roc(datetime.date(2026, 11, 20))
    '115/11/20'
    """
    return f"{day.year - 1911}/{day:%m/%d}"


def price(value: decimal.Decimal | float | None) -> str | None:
    """收盤價取兩位小數；沒有為 None（畫面就不顯示）。

    >>> price(decimal.Decimal("101.5000"))
    '101.50'
    >>> price(None) is None
    True
    """
    return None if value is None else f"{value:.2f}"


# ---------- 1. 狀態 ----------
def in_punish(card: dict) -> bool:
    """這一天在處置期間內。"""
    start, end = card.get("period_start"), card.get("period_end")
    return bool(start and end and start <= card["trade_date"] <= end)


def status(card: dict) -> str:
    """一張卡片現在最重要的狀態，依序判斷：

      soon        已達處置標準：今天達標，或處置已公布、還沒開始
      punish      處置中
      leave_today 今天出關
      one         聽牌（差 1 次）
      steps       差 2 次以上

    >>> day = datetime.date(2026, 11, 20)
    >>> status({"trade_date": day, "punished_by": "streak3", "period_start": datetime.date(2026, 11, 23)})
    'soon'
    >>> status({"trade_date": day, "period_start": datetime.date(2026, 11, 18), "period_end": datetime.date(2026, 11, 24)})
    'punish'
    >>> status({"trade_date": day, "steps_left": 1})
    'one'
    """
    day, start = card["trade_date"], card.get("period_start")
    if card.get("punished_by") or (start and start > day):
        return "soon"
    if in_punish(card):
        return "punish"
    if card.get("resume_date") == day:
        return "leave_today"
    if card.get("steps_left") == 1:
        return "one"
    return "steps"


def is_quiet(card: dict) -> bool:
    """總覽不放的卡片：今天沒被列、近 30 個營業日沒有會累計的紀錄，而且沒有任何處置的事。

    這種股票在 app_progress 裡（近 30 個營業日被列過注意，例如只被列第 9～14 款），
    但四條路徑都是 0，「差 3 次」只是門檻，放上去會誤導。

    >>> is_quiet({"trade_date": datetime.date(2026, 11, 20), "is_listed_today": 0, "in30": 0, "steps_left": 3})
    True
    """
    if card.get("is_listed_today") or card.get("in30"):
        return False
    return status(card) == "steps"


def filters_of(card: dict) -> set[str]:
    """這張卡片符合哪幾個「只看」篩選（FILTERS 的代號）。

    >>> sorted(filters_of({"trade_date": datetime.date(2026, 11, 20), "is_listed_today": 1,
    ...                    "resume_date": datetime.date(2026, 11, 20)}))
    ['leave_today', 'listed']
    """
    day, result = card["trade_date"], set()
    if card.get("is_listed_today"):
        result.add("listed")
    if card.get("resume_date") == day:
        result.add("leave_today")
    return result


def section_of(card: dict) -> str:
    """「全部」分頁放哪一區（SECTIONS 的代號）。處置最後一天的放「明日出關」；
    今天出關的放「其他」，但如果又只差 1 次，放「聽牌」。

    >>> section_of({"trade_date": datetime.date(2026, 5, 11), "resume_date": datetime.date(2026, 5, 11), "steps_left": 1})
    'one'
    >>> section_of({"trade_date": datetime.date(2026, 11, 20), "period_start": datetime.date(2026, 11, 13),
    ...             "period_end": datetime.date(2026, 11, 20)})
    'leave_tomorrow'
    """
    state = status(card)
    if state == "punish" and card.get("period_end") == card["trade_date"]:
        return "leave_tomorrow"
    if state in ("soon", "punish"):
        return state
    return "one" if card.get("steps_left") == 1 else "rest"


def sort_key(card: dict) -> tuple:
    """總覽的順序：分區照 SECTIONS → 處置中快出關的在前 → 差幾次少的在前 → 進度條填得多的在前 → 代號。

    「填得多」＝卡片上那條進度條（card_progress）填滿的格子÷總格數，和眼睛看到的一樣；沒有進度條的排在後面。
    """
    order = [key for key, _, _ in SECTIONS].index(section_of(card))
    end = card.get("period_end")
    bar = card_progress(card)
    filled = sum(bar["kept_cells"]) / bar["threshold"] if bar else 0
    return order, end.toordinal() if end else 0, card.get("steps_left") or 99, -filled, card["stock_id"]


# ---------- 2. 處置的措施 ----------
def round_of(measure: str | None) -> int:
    """處置措施的文字 → 第幾次處置。

    >>> round_of("第二次處置"), round_of("第一次處置"), round_of(None)
    (2, 1, 1)
    """
    return 1 if not measure or "第一次" in measure else 2


def rule_for(round_no: int, start: datetime.date | None, minutes: int | None = None) -> dict:
    """處置的措施（要點第六條，見 docs/rules.md）：撮合間隔、預收、遇第 13 款延長成幾天。8/10 起是新制。

    minutes：公告上寫的撮合間隔；有寫就用公告的，沒寫才用規則推算的。

    >>> rule_for(1, datetime.date(2026, 11, 24))
    {'minutes': 2, 'preorder': '單筆 10 張或當日累積 30 張以上要預收', 'extended_days': 7}
    >>> rule_for(2, datetime.date(2026, 7, 28))["minutes"], rule_for(2, datetime.date(2026, 7, 28), minutes=10)["minutes"]
    (20, 10)
    """
    new_rule = start is None or start >= NEW_RULE_START
    default_minutes = 2 if new_rule else (5 if round_no == 1 else 20)
    return {
        "minutes": minutes or default_minutes,
        "preorder": "單筆 10 張或當日累積 30 張以上要預收" if round_no == 1 else "全部委託都要預收",
        "extended_days": 7 if new_rule else 12,
    }


def measures_text(rule: dict) -> str:
    """處置措施一句話：撮合間隔＋預收。處置紀錄（現在、以前的處置）都用這一句。

    卡片的資訊列（facts）另外分成「撮合」「預收」兩列，所以那邊不用這句。

    >>> measures_text({"minutes": 2, "preorder": "全部委託都要預收"})
    '約每 2 分鐘撮合一次・全部委託都要預收'
    """
    return f"約每 {rule['minutes']} 分鐘撮合一次・{rule['preorder']}"


def met_paths(punished_by: str | None) -> str:
    """今天達到的路徑（例如 'streak3,in10'）→ 畫面上的名稱。

    >>> met_paths("streak3,in10")
    '連 3 日列第 1 款、10 日內被列 6 日'
    """
    keys = [k for k in (punished_by or "").split(",") if k in PATHS]
    return "、".join(PATHS[k][0] for k in keys)


# ---------- 3. 總覽的卡片 ----------
def clause_list(clauses: str | None) -> list[dict]:
    """款次（例如 '1,13'）→ 每款：款次、名稱、會不會算進處置次數。

    >>> clause_list("1,13")
    [{'no': 1, 'name': '累積漲跌幅異常', 'counted': True}, {'no': 13, 'name': '當沖占比過高', 'counted': False}]
    """
    if not clauses:
        return []
    numbers = [int(x) for x in str(clauses).split(",") if x.strip()]
    return [{"no": n, "name": CLAUSE_NAMES.get(n, f"第{n}款"), "counted": n <= 8} for n in numbers]


def plain_reason(text: str | None) -> str:
    """被列原因全文去掉「﹝第一款﹞」這種標記（款次另外用標籤顯示），多句之間換成空格。

    >>> plain_reason("最近六個營業日累積收盤價跌幅達41.15%﹝第一款﹞。")
    '最近六個營業日累積收盤價跌幅達41.15%。'
    """
    if not text:
        return ""
    text = re.sub(r"﹝第[一二三四五六七八九十]+款﹞", "", text)
    return re.sub(r"。(?=\S)", "。 ", text).strip()


def headline(card: dict) -> dict:
    """卡片上的大字和顏色（kind 對應 CSS 的 class）。

    >>> headline({"trade_date": datetime.date(2026, 11, 20), "punished_by": "streak3",
    ...           "period_start": datetime.date(2026, 11, 23)})
    {'text': '11/23 起處置', 'kind': 'orange'}
    >>> headline({"trade_date": datetime.date(2026, 11, 20), "steps_left": 2})
    {'text': '差 2 次', 'kind': 'quiet'}
    """
    state = status(card)
    if state == "soon":
        start = card.get("period_start")
        return {"text": f"{md(start)} 起處置" if start else "達標", "kind": "orange"}
    if state == "punish":   # 處置最後一天（明日出關）用紫色，和標籤、卡片顏色一樣
        return {"text": f"{md(card['resume_date'])} 出關", "kind": "violet" if card.get("period_end") == card["trade_date"] else "seal"}
    steps = card.get("steps_left")   # 今日出關也一樣寫差幾次（處置結束了，看的是下一次），「今日出關」放小標籤
    return {"text": f"差 {steps} 次", "kind": "amber" if steps == 1 else "quiet"}


def tags(card: dict) -> list[dict]:
    """狀態標籤（kind 對應 CSS 的 class）。main＝主要狀態，和分頁名稱同一個詞（即將處置、處置中、聽牌），最多一個、放第一個；
    處置最後一天的主要標籤是「明日出關」（和分頁同名），不另外標處置中。
    其他是次要標籤（第二次處置、今日出關、聽牌、今日注意股）。
    今日注意股＝今天被列注意（任何一款，不管算不算次數），每種卡片都標。今日出關的卡片和一般卡片一樣（大字寫差幾次），
    只多一個「今日出關」小標籤；又只差 1 次的，主要標籤是聽牌。
    即將處置是推算還是已公布，看大字（「達標」或「11/23 起處置」）。總覽和個股頁都全部顯示。

    >>> [t["text"] for t in tags({"trade_date": datetime.date(2026, 11, 20), "punished_by": "streak3"})]
    ['即將處置']
    >>> [t["text"] for t in tags({"trade_date": datetime.date(2026, 11, 20), "steps_left": 1,
    ...                           "period_start": datetime.date(2026, 11, 13), "period_end": datetime.date(2026, 11, 20),
    ...                           "measure_type": "第二次處置"})]
    ['明日出關', '第二次處置', '聽牌']
    """
    day, state = card["trade_date"], status(card)
    main = {
        "soon": {"text": "即將處置", "kind": "orange", "main": True},
        "punish": {"text": "處置中", "kind": "seal", "main": True},
        "one": {"text": "聽牌", "kind": "amber", "main": True},
    }
    one_step = card.get("steps_left") == 1
    if state == "leave_today" and one_step:
        state = "one"   # 今天出關、又只差 1 次：主要是聽牌
    if state == "punish" and card.get("period_end") == day:
        main["punish"] = {"text": "明日出關", "kind": "violet", "main": True}   # 明日出關分頁的卡片；紫色，和今日出關（綠）分開
    result = [main[state]] if state in main else []
    if state == "punish" and round_of(card.get("measure_type")) == 2:
        result.append({"text": "第二次處置", "kind": "mut", "main": False})
    if card.get("resume_date") == day:
        result.append({"text": "今日出關", "kind": "ok", "main": False})
    if state != "one" and one_step:   # 處置中又差 1 次就會再處置
        result.append({"text": "聽牌", "kind": "amber", "main": False})
    if card.get("is_listed_today"):
        result.append({"text": "今日注意股", "kind": "acc", "main": False})
    return result


def facts(card: dict) -> list[tuple[str, str]]:
    """卡片上的資訊列：(標籤, 內容)。只放已經確定的事（處置日期、撮合、預收），不放推算。

    >>> facts({"trade_date": datetime.date(2026, 11, 20), "period_start": datetime.date(2026, 11, 18),
    ...        "period_end": datetime.date(2026, 11, 24), "measure_type": "第一次處置", "match_interval_minutes": 2})
    [('撮合', '約每 2 分鐘一次'), ('預收', '單筆 10 張或當日累積 30 張以上要預收')]
    """
    state, rows = status(card), []
    if state == "soon" and card.get("period_start"):
        rows.append(("處置", f"{md_range(card['period_start'], card['period_end'])} {card.get('measure_type') or ''}".strip()))
        rows.append(("恢復交易", md(card.get("resume_date"))))
    elif state == "soon":
        rows.append(("達標", f"{met_paths(card.get('punished_by'))}，等官方公布處置日期"))
    elif state == "punish":
        rule = rule_for(round_of(card.get("measure_type")), card.get("period_start"), card.get("match_interval_minutes"))
        rows.append(("撮合", f"約每 {rule['minutes']} 分鐘一次"))
        rows.append(("預收", rule["preorder"]))
    return rows


def path_progress(card: dict, path: str) -> dict:
    """一條路徑的進度：名稱、已達幾次、門檻、還差幾次、進度條格子（True＝已計入）。

    10 日內、30 日內是往回數的窗口：最早計入的日子會滑出去，所以「還差幾次」可能比「門檻 − 已達」多。
    expiring＝已達的次數裡，達標前會滑出窗口的有幾次；kept_cells＝扣掉這些、留得住的格子（卡片用，和「還差幾次」對得起來）。

    >>> p = path_progress({"streak5": 4, "streak5_left": 1}, "streak5")
    >>> p["label"], p["count"], p["threshold"], p["cells"], p["expiring"]
    ('連 5 日被列注意', 4, 5, [True, True, True, True, False], 0)
    >>> p = path_progress({"in10": 4, "in10_left": 3}, "in10")   # 4/6，但最早的 1 次會滑出 10 日 → 還差 3 次
    >>> p["expiring"], p["kept_cells"]
    (1, [True, True, True, False, False, False])
    """
    label, threshold = PATHS[path]
    count = card.get(path) or 0
    left = card.get(f"{path}_left")
    kept = threshold - left if left is not None else count
    return {"key": path, "label": label, "count": count, "threshold": threshold, "left": left,
            "cells": [i < count for i in range(threshold)],
            "expiring": max(0, count - kept), "kept_cells": [i < kept for i in range(threshold)]}


def card_progress(card: dict) -> dict | None:
    """卡片上的進度條：放大字「差 N 次」算出來的那條路徑，兩個數字才對得起來。
    好幾條一樣差 N 次的，放進度最多的那條。即將處置、處置中不放（今日出關照樣放：出關後又被列的話看得到進度）；
    四條都是 0 也照樣放（空的進度條），每張卡片長得一樣。

    >>> base = {"trade_date": datetime.date(2026, 11, 20), "streak3": 0, "streak5": 2, "in10": 2, "in30": 2,
    ...         "streak3_left": 3, "streak5_left": 3, "in10_left": 4, "in30_left": 10, "steps_left": 3, "closest_path": "streak3"}
    >>> card_progress(base)["key"]   # 連 3 日、連 5 日都差 3 次：放有進度的連 5 日（2/5），不放空的 0/3
    'streak5'
    >>> card_progress(dict(base, streak5=0, streak5_left=5, in10=0, in30=0))["key"]   # 四條都是 0：空的連 3 日
    'streak3'
    """
    if status(card) not in ("one", "steps", "leave_today"):
        return None
    steps = card.get("steps_left")
    tied = [path for path in PATHS if card.get(f"{path}_left") == steps] or [card.get("closest_path")]
    best = max(tied, key=lambda path: (card.get(path) or 0) / PATHS[path][1])   # 一樣多時 max 取前面的（PATHS 的順序）
    return path_progress(card, best) if best in PATHS else None


def wall_card(card: dict) -> dict:
    """總覽一檔要顯示的東西（個股頁上半部也用這個）。

    tone：卡片的顏色（上緣色條、滑鼠移上去的底色），跟著主要標籤的顏色走，標籤和卡片才會同色；沒有主要標籤為 ""。
    """
    card_tags = tags(card)
    tone = next((t["kind"] for t in card_tags if t["main"]), "")
    return {
        "stock_id": card["stock_id"],
        "name": card.get("stock_name") or card["stock_id"],
        "close": price(card.get("close_price")),
        "status": status(card),
        "section": section_of(card),
        "headline": headline(card),
        "tags": card_tags,
        "tone": tone,
        "facts": facts(card),
        "progress": card_progress(card),
        "announcement_count": card.get("announcement_count") or 0,
        # 今天被列注意、但只有第 9～14 款（不算次數）：進度條旁邊註明，免得以為被列了次數卻沒變
        "uncounted_today": bool(card.get("is_listed_today")) and not card.get("is_counted_today"),
    }


# ---------- 4. 總覽 ----------
def matches(card: dict, search: str) -> bool:
    """搜尋框：代號或名稱裡有這幾個字（不分大小寫）。

    >>> matches({"stock_id": "2465", "stock_name": "麗臺"}, "246"), matches({"stock_id": "6933", "stock_name": "AMAX-KY"}, "amax")
    (True, True)
    """
    text = search.casefold()
    return text in card["stock_id"].casefold() or text in (card.get("stock_name") or "").casefold()


def overview(rows: list[dict], tab: str = "all", only: str = "", search: str = "") -> dict:
    """總覽：一天所有上市公司分成五區（SECTIONS）。分頁＝分區（加起來等於全部），「只看」是另外疊上去的篩選。

    分頁上的數字固定是這一天的總數，不跟著「只看」改變：數字本身就是資訊（今天處置中 3 檔），
    跟著篩選跳反而會讓人誤會（開「今日注意股」時處置中變 0，像是沒有人在處置）。
    「只看」的數字跟著分頁：這個分頁裡有幾檔符合，也就是點了會看到幾檔。

    回傳 tab、only、search（看不懂的值換成預設）、only_label（「只看」的名稱）、counts（各分頁幾檔）、
    filter_counts（這個分頁裡各篩選幾檔）、shown（篩選、搜尋後顯示幾檔）、
    sections（要顯示的區塊：一般卡片 cards、「其他」的小卡片 rest；「全部」不分區，卡片照聽牌→即將處置→處置中→明日出關排在一起）。
    沒東西可看的（四條路徑都是 0）不列出，也不算進檔數。
    """
    if tab in FILTER_KEYS:   # 舊網址 tab=listed 之類：當成篩選
        tab, only = "all", tab
    if only == "leave_tomorrow":   # 明日出關以前是篩選，現在是分頁
        tab, only = "leave_tomorrow", ""
    if tab not in TAB_KEYS:
        tab = "all"
    if only not in FILTER_KEYS:
        only = ""

    # 1. 拿掉沒東西可看的卡片（四條路徑都是 0）
    rows = sorted((row for row in rows if not is_quiet(row)), key=sort_key)

    # 2. 分頁的數字：這一天的總數（各區加起來＝全部）；「只看」的數字：這個分頁裡有幾檔
    counts = {key: sum(section_of(row) == key for row in rows) for key, _, _ in SECTIONS}
    counts["all"] = len(rows)
    in_tab = [row for row in rows if tab == "all" or section_of(row) == tab]
    filter_counts = {key: sum(key in filters_of(row) for row in in_tab) for key in FILTER_KEYS}

    # 3. 套上篩選、搜尋（搜尋只篩卡片，分頁和「只看」的數字不變）
    if only:
        rows = [row for row in rows if only in filters_of(row)]
    search = search.strip()
    if search:
        rows = [row for row in rows if matches(row, search)]

    # 4. 要顯示的：「其他」用小卡片，其餘用一般卡片；「全部」不分區，卡片照 sort_key 排在一起
    cards = [wall_card(row) for row in rows if tab == "all" or section_of(row) == tab]
    sections = [{"key": "cards", "cards": [c for c in cards if c["section"] != "rest"]},
                {"key": "rest", "cards": [c for c in cards if c["section"] == "rest"]}]
    return {"tab": tab, "only": only, "only_label": FILTER_LABELS.get(only, ""), "search": search,
            "counts": counts, "filter_counts": filter_counts, "shown": len(cards),
            "sections": [s for s in sections if s["cards"]]}


# ---------- 5. 個股頁「處置紀錄」 ----------
@dataclasses.dataclass
class Step:
    """時程的一列。

    kind：plain 一般／seal 處置期間／ok 恢復交易／past 以前的處置（灰色）。
    resume_when：處置後出關的日子，寫在同一列說明下面（「11/30 出關，恢復正常交易」），不另外一列。
    note：說明後面的灰字附註，例如以前的處置「11/02 公布」。
    """
    when: str
    text: str
    kind: str = "plain"
    resume_when: str = ""
    note: str = ""


def timeline(card: dict) -> list[Step]:
    """現在的處置（或已公布的處置，出關日寫在同一列）。今天出關是今天的重點，照樣自己一列。
    只放已經發生、已經公布的事，不放「若再被列注意就處置」這種推算。

    >>> steps = timeline({"trade_date": datetime.date(2026, 11, 2), "steps_left": 1,
    ...     "period_start": datetime.date(2026, 10, 30), "period_end": datetime.date(2026, 11, 9),
    ...     "resume_date": datetime.date(2026, 11, 10), "measure_type": "第一次處置",
    ...     "next_listed_date": datetime.date(2026, 11, 3), "next_punish_round": 2})
    >>> [(s.when, s.resume_when) for s in steps]
    [('10/30–11/09', '11/10')]
    """
    day, state, steps = card["trade_date"], status(card), []

    if state in ("soon", "punish", "leave_today") and card.get("period_start"):
        measure = card.get("measure_type") or "處置"
        rule = rule_for(round_of(measure), card["period_start"], card.get("match_interval_minutes"))
        condition = f"（{card['trigger_condition']}）" if card.get("trigger_condition") else ""
        resume = "" if state == "leave_today" else md(card.get("resume_date"))
        steps.append(Step(md_range(card["period_start"], card["period_end"]),
                          f"{measure}{condition}・{measures_text(rule)}", "seal", resume_when=resume))
        if state == "leave_today":
            steps.append(Step(md(card.get("resume_date")), "今日出關，恢復正常交易", "ok"))
    elif state == "soon":
        steps.append(Step(md(day), f"{met_paths(card.get('punished_by'))}達標，等官方公布處置日期", "seal"))
    elif state == "leave_today":
        steps.append(Step(md(day), "今日出關，恢復正常交易", "ok"))
    return steps


def past_step(punishment: dict) -> Step:
    """以前的一次處置（app_punish_history 一列）→ 時程的一列（灰色），措施的寫法和現在的處置一樣。

    >>> s = past_step({"announce_date": datetime.date(2026, 11, 2), "period_start": datetime.date(2026, 11, 3),
    ...                "period_end": datetime.date(2026, 11, 9), "measure_type": "第一次處置",
    ...                "trigger_condition": "連續三次", "match_interval_minutes": 5})
    >>> s.when, s.text, s.note
    ('11/03–11/09', '第一次處置（連續三次）・約每 5 分鐘撮合一次・單筆 10 張或當日累積 30 張以上要預收', '11/02 公布')
    """
    measure = punishment.get("measure_type") or "處置"
    rule = rule_for(round_of(measure), punishment["period_start"], punishment.get("match_interval_minutes"))
    condition = f"（{punishment['trigger_condition']}）" if punishment.get("trigger_condition") else ""
    return Step(md_range(punishment["period_start"], punishment["period_end"]),
                f"{measure}{condition}・{measures_text(rule)}", "past", note=f"{md(punishment['announce_date'])} 公布")


# ---------- 6. 個股頁近 30 個營業日的注意紀錄 ----------
@dataclasses.dataclass
class Cell:
    """注意紀錄的一天。kind：counted 會累計／not_counted 不累計／reset 已計入前次處置／none 沒被列注意。"""
    day: datetime.date
    kind: str
    clauses: str
    in_punish: bool
    month_label: str   # 這個月的第一格寫「11月」，其他空白

    @property
    def shade(self) -> str:
        """格子的 class：深藍 on、淺藍 reset，其他留白。"""
        return CELL_SHADE[self.kind]

    @property
    def title(self) -> str:
        """滑鼠移上去的說明，用的字和格子下面的圖例一樣：計入本次累計／已計入前次處置／處置期間。
        白色的格子（沒被列、只被列第 9～14 款）不算次數，只寫日期，跟圖例一樣不另外說明。

        >>> d = datetime.date(2026, 11, 20)
        >>> Cell(d, "counted", "1,5", True, "").title
        '11/20・計入本次累計（第 1、5 款）・處置期間'
        >>> Cell(d, "reset", "6", False, "").title, Cell(d, "not_counted", "13", False, "").title
        ('11/20・已計入前次處置（第 6 款）', '11/20')
        """
        parts = [md(self.day)]
        if self.kind in ("counted", "reset"):
            numbers = "、".join(str(c["no"]) for c in clause_list(self.clauses))
            parts.append(f"{CELL_NOTE[self.kind]}（第 {numbers} 款）")
        if self.in_punish:
            parts.append("處置期間")
        return "・".join(parts)


def notice_cells(days: list[datetime.date], history: dict[datetime.date, dict], count_start: datetime.date | None,
                 punish_periods: list[tuple[datetime.date, datetime.date]]) -> list[Cell]:
    """近 30 個營業日每天一格。會累計、但早於 count_start（上次處置公布）的，記為「已計入前次處置」。

    月份寫在每個月的第一格；兩個月份離不到 MONTH_LABEL_GAP 格會疊在一起（「9月10月」變成「9月0月」），
    這時只留後面那個月。

    >>> d = [datetime.date(2026, 10, 30), datetime.date(2026, 11, 2), datetime.date(2026, 11, 3)]
    >>> cells = notice_cells(d, {d[0]: {"clauses": "1", "is_counted": 1}, d[1]: {"clauses": "9", "is_counted": 0}},
    ...                      datetime.date(2026, 11, 3), [])
    >>> [(c.kind, c.month_label) for c in cells]
    [('reset', ''), ('not_counted', '11月'), ('none', '')]
    """
    cells, last_month = [], None
    for day in days:
        row = history.get(day)
        if row is None:
            kind = "none"
        elif not row.get("is_counted"):
            kind = "not_counted"
        elif count_start and day < count_start:
            kind = "reset"
        else:
            kind = "counted"
        label = ""
        if day.month != last_month:
            label = f"{day.month}月"
            labeled = [i for i, c in enumerate(cells) if c.month_label]
            if labeled and len(cells) - labeled[-1] < MONTH_LABEL_GAP:   # 和前一個月份太近：前一個拿掉
                cells[labeled[-1]].month_label = ""
        cells.append(Cell(day=day, kind=kind, clauses=row["clauses"] if row else "",
                          in_punish=any(start <= day <= end for start, end in punish_periods),
                          month_label=label))
        last_month = day.month
    return cells


def cell_legend(cells: list[Cell]) -> list[dict]:
    """30 格下面的圖例：只列這 30 格裡有出現的；一格都沒有計入就寫一句說明（cls 空的＝沒有色塊）。

    >>> d = datetime.date(2026, 11, 20)
    >>> [x["text"] for x in cell_legend([Cell(d, "counted", "1", True, "")])]
    ['計入本次累計', '處置期間']
    >>> cell_legend([Cell(d, "none", "", False, "")])
    [{'cls': '', 'text': '近 30 個營業日沒有計入累計的注意'}]
    """
    kinds = {c.kind for c in cells}
    legend = [{"cls": CELL_SHADE[kind], "text": CELL_NOTE[kind]} for kind in ("counted", "reset") if kind in kinds]
    if any(c.in_punish for c in cells):
        legend.append({"cls": "punish", "text": "處置期間"})
    if not kinds & {"counted", "reset"}:
        legend.append({"cls": "", "text": "近 30 個營業日沒有計入累計的注意"})
    return legend


def notice_log(cells: list[Cell], history: dict[datetime.date, dict]) -> list[dict]:
    """注意紀錄：近 30 個營業日每次被列注意的那天（新的在前）——款次、算不算進累計、原因全文。
    算不算進累計和 30 格注意紀錄同一套（cells 的 kind），兩邊說法才一致。

    >>> d = datetime.date(2026, 11, 20)
    >>> log = notice_log([Cell(d, "counted", "1", False, "")], {d: {"reason_text": "跌幅達41%﹝第一款﹞。"}})
    >>> log[0]["date"], log[0]["weekday"], log[0]["note"], log[0]["reason"]
    ('11/20', '五', '計入本次累計', '跌幅達41%。')
    """
    return [{"day": cell.day, "date": md(cell.day), "weekday": weekday(cell.day), "kind": cell.kind,
             "note": CELL_NOTE[cell.kind], "clauses": clause_list(cell.clauses),
             "reason": plain_reason(history[cell.day].get("reason_text"))}
            for cell in reversed(cells) if cell.kind != "none"]


def counted_text(days: list[datetime.date]) -> str:
    """一條路徑計入了哪幾個交易日，全部列出（最多 12 天：30 日內 12 日的門檻）。

    >>> counted_text([datetime.date(2026, 11, 19), datetime.date(2026, 11, 20)])
    '計入 11/19、11/20'
    >>> counted_text([])
    '還沒有計入的日子'
    """
    if not days:
        return "還沒有計入的日子"
    return "計入 " + "、".join(md(d) for d in days)


# ---------- 7. 個股頁公開資訊 ----------
def reflow(text: str) -> tuple[str, bool]:
    r"""觀測站的重大訊息全文每 35 字左右就硬換行（常斷在句子中間，甚至用逗號開頭）。
    把被切斷的行接回來，讓網頁依框的寬度自己換行；回傳 (整理後的文字, 是否有表格)。

    不接：下一行是新項目（1.、(1)、一、）、這一行以。：結尾、空行。
    整則裡有用空白對齊的表格時完全不動，表格才會對齊。

    >>> reflow("(2)為避免投資人因以高於面額\n價格買進\n，而有投資損失。\n(3)本公司")
    ('(2)為避免投資人因以高於面額價格買進，而有投資損失。\n(3)本公司', False)
    >>> reflow("7.其他應敘明事項(若事件\n  符合規定之事項):\n無。")
    ('7.其他應敘明事項(若事件符合規定之事項):\n無。', False)
    >>> reflow("科目    最近一月\n營收    50.72")[1]
    True
    """
    lines = text.split("\n")
    if any(TABLE_LINE.search(line.strip()) for line in lines):
        return text, True
    out: list[str] = []
    for line in lines:
        joinable = (out and out[-1].strip() and line.strip() and not NEW_ITEM.match(line)
                    and not out[-1].rstrip().endswith(LINE_END))
        if joinable:
            prev, nxt = out[-1].rstrip(), line.strip()
            ascii_pair = prev[-1:].isascii() and prev[-1:].isalnum() and nxt[:1].isascii() and nxt[:1].isalnum()
            out[-1] = prev + (" " if ascii_pair else "") + nxt   # 英文、數字接起來中間留空白，中文不留
        else:
            out.append(line)
    return "\n".join(out), False


def announcement_item(row: dict, trading_days: set[datetime.date]) -> dict:
    """一則公告。盤中＝交易日的 09:00～14:30（含盤後定價），其他時間（盤前、盤後、假日）不標。"""
    time = row.get("announce_time")   # dashboard.client 已經換成 datetime.time
    during = bool(time and MARKET_OPEN <= time <= MARKET_CLOSE and row["announce_date"] in trading_days)
    content, is_table = reflow((row.get("content") or "").strip())
    return {
        "time": time.strftime("%H:%M") if time else "",
        "during_session": during,
        "subject": row["subject"],
        "is_correction": bool(row.get("is_correction")),
        "is_explanation": bool(row.get("is_explanation")),
        "clause_name": row.get("clause_name"),
        "clause_group": CLAUSE_GROUP.get(row.get("announce_clause") or "", "other"),   # 標籤顏色，見 CLAUSE_GROUPS
        "content": content,
        "is_table": is_table,
    }


def announcement_groups(rows: list[dict], trading_days: set[datetime.date]) -> list[dict]:
    """依日期分組，新的在前：新的日期在前，同一天也是晚的時間在前（整份從上到下由新到舊）。

    >>> rows = [{"announce_date": datetime.date(2026, 11, 11), "announce_time": datetime.time(17), "subject": "甲"},
    ...         {"announce_date": datetime.date(2026, 11, 20), "announce_time": datetime.time(9), "subject": "乙"},
    ...         {"announce_date": datetime.date(2026, 11, 20), "announce_time": datetime.time(16, 30), "subject": "丙"}]
    >>> [(g["date"], g["weekday"], [a["subject"] for a in g["items"]]) for g in announcement_groups(rows, set())]
    [('11/20', '五', ['丙', '乙']), ('11/11', '三', ['甲'])]
    """
    groups: dict[datetime.date, list[dict]] = {}
    for row in rows:
        groups.setdefault(row["announce_date"], []).append(announcement_item(row, trading_days))
    return [{"day": day, "date": md(day), "weekday": weekday(day),
             "items": sorted(groups[day], key=lambda a: a["time"], reverse=True)}
            for day in sorted(groups, reverse=True)]


# ---------- 8. 個股頁 ----------
def stock_page(detail: dict) -> dict:
    """個股頁要的全部東西。detail 是 API /stocks/{stock_id} 的回應（日期、時間已換回 date、time）。

    版面（一欄）：狀態 → AI 摘要 → 公開資訊 → 新聞資訊 → 處置進度 → 注意紀錄 → 處置紀錄。
    注意紀錄、以前的處置、公開資訊都先顯示最近 RECENT_DAYS 天的，更早的收起來（*_recent、*_older）。
    """
    day, row, days = detail["trade_date"], detail["card"], detail["trading_days"]
    history = {h["trade_date"]: h for h in detail["notice_history"]}
    punishments = detail["punish_history"]
    since = day - datetime.timedelta(days=RECENT_DAYS - 1)

    # 四條路徑：進度、計入的日期、今天達標或差 1 次
    paths = []
    for key in PATHS:
        path = path_progress(row, key)
        counted = detail["counted_dates"].get(key, [])
        path["dates"] = counted_text(counted)
        # 寫在進度條每一格上的日期：第 i 個填滿的格子＝第 i 個計入的日子
        path["labels"] = [md(counted[i]) if i < len(counted) else "" for i in range(path["threshold"])]
        path["met"] = key in (row.get("punished_by") or "").split(",")
        paths.append(path)

    # 處置紀錄：現在這次（或已公布、還沒開始的這次）在 timeline 裡；下面列以前的處置，不重複
    steps = timeline(row)
    past = [p for p in punishments if p["period_start"] != row.get("period_start")]

    # 注意紀錄：30 格和列表同一套 kind
    cells = notice_cells(days, history, row.get("count_start"),
                         [(p["period_start"], p["period_end"]) for p in punishments])
    log = notice_log(cells, history)   # 新的在前

    groups = announcement_groups(detail["announcements"], set(days))
    older_groups = [g for g in groups if g["day"] < since]
    return {
        "day": day,
        "card": row,
        "wall": wall_card(row),
        "recent_days": RECENT_DAYS,
        "ai_summary": detail.get("ai_summary"),   # 目前 API 還沒有這個欄位，網頁先顯示「還沒產生」的框
        "news": detail.get("news") or [],          # 同上：之後 API 給 FinMind 新聞（title、link、source、date），現在是空的
        # 公開資訊
        "announcement_total": len(detail["announcements"]),
        "announcements_recent": [g for g in groups if g["day"] >= since],
        "announcements_older": older_groups,
        "announcements_older_count": sum(len(g["items"]) for g in older_groups),
        # 注意紀錄
        "notice_total": len(log),
        "notices_recent": [n for n in log if n["day"] >= since],
        "notices_older": [n for n in log if n["day"] < since],
        # 處置紀錄
        "timeline": steps,
        "has_punishment": bool(steps or past),
        "show_glossary": any(s.kind == "seal" for s in steps) or bool(past),   # 有處置內容才需要解釋預收、撮合
        "past_recent": [past_step(p) for p in past if p["period_end"] >= since],   # 處置期間碰到最近 RECENT_DAYS 天的
        "past_older": [past_step(p) for p in past if p["period_end"] < since],
        # 處置進度
        "paths": paths,
        "cells": cells,
        "legend": cell_legend(cells),
    }
