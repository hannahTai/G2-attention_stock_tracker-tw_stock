"""api/ 和 dashboard/ 測試共用的假資料：app_card 的列、個股頁的 API 回應。

每張假卡片代表一種狀態，寫在 CARDS 旁邊。
"""
import datetime

DAY = datetime.date(2026, 11, 20)


def make_card(stock_id: str, name: str, **overrides) -> dict:
    """app_card 的一列；沒給的欄位用「今天被列注意、差 2 次」的預設值。"""
    row = {
        "trade_date": DAY, "stock_id": stock_id, "stock_name": name, "count_start": datetime.date(2026, 10, 1),
        "streak3": 1, "streak5": 3, "in10": 3, "in30": 3,
        "streak3_left": 2, "streak5_left": 2, "in10_left": 3, "in30_left": 9,
        "steps_left": 2, "closest_path": "streak5", "punished_by": None, "has_full_history": 1,
        "period_start": None, "period_end": None, "resume_date": None,
        "next_listed_date": None, "next_punish_round": None, "next_period_start": None,
        "next_period_end": None, "next_resume_date": None,
        "is_listed_today": 1, "is_company": 1, "close_price": 31.5, "reason_text": "最近六個營業日累積漲幅達 32%﹝第一款﹞。",
        "clauses": "1", "is_counted_today": 1,
        "measure_type": None, "trigger_condition": None, "match_interval_minutes": None, "announcement_count": 1,
    }
    row.update(overrides)
    return row


NEXT = {"next_listed_date": datetime.date(2026, 11, 23), "next_punish_round": 2,
        "next_period_start": datetime.date(2026, 11, 24), "next_period_end": datetime.date(2026, 11, 30),
        "next_resume_date": datetime.date(2026, 12, 1)}

CARDS = [
    make_card("1111", "甲公司"),                                                     # 差 2 次
    make_card("2222", "乙公司", steps_left=1, streak3=2, streak3_left=1, closest_path="streak3", **NEXT),   # 聽牌
    make_card("3333", "丙公司", is_listed_today=0, clauses=None, is_counted_today=0, steps_left=None,       # 處置中
              close_price=None, period_start=datetime.date(2026, 11, 16), period_end=datetime.date(2026, 11, 20),
              resume_date=datetime.date(2026, 11, 23), measure_type="第二次處置", match_interval_minutes=2),
    make_card("4444", "丁公司", steps_left=None, streak3=3, streak3_left=None, punished_by="streak3",       # 今日達標、已公布
              period_start=datetime.date(2026, 11, 23), period_end=datetime.date(2026, 11, 27),
              resume_date=datetime.date(2026, 11, 30), measure_type="第一次處置"),
    make_card("5555", "戊公司", is_listed_today=0, clauses=None, is_counted_today=0,                       # 沒東西可看
              streak3=0, streak5=0, in10=0, in30=0, steps_left=3, closest_path="streak3"),
]

TRADING_DAYS = [datetime.date(2026, 11, 18), datetime.date(2026, 11, 19), DAY]

ANNOUNCEMENTS = [
    {"announce_date": DAY, "announce_time": datetime.time(15, 26), "subject": "更正本公司營收公告 US$ 1,000",
     "is_correction": 1, "is_explanation": 0, "clause_name": "其他重大事項", "content": "說明全文"},
    {"announce_date": DAY, "announce_time": datetime.time(8, 30), "subject": "盤前公告",
     "is_correction": 0, "is_explanation": 0, "clause_name": None, "content": None},
]


def card_of(stock_id: str) -> dict | None:
    return next((dict(c) for c in CARDS if c["stock_id"] == stock_id), None)


def stock_detail(card: dict) -> dict:
    """API /stocks/{stock_id} 的回應（日期、時間已換回 date、time，和 dashboard.client 給的一樣）。"""
    return {
        "trade_date": DAY,
        "card": card,
        "counted_dates": {"streak3": [datetime.date(2026, 11, 19), DAY]},
        "trading_days": list(TRADING_DAYS),
        "notice_history": [{"trade_date": DAY, "clauses": "1", "is_counted": 1}],
        "punish_history": [],
        "announcements": [dict(a) for a in ANNOUNCEMENTS],
    }
