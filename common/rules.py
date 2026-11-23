"""處置規則的常數（臺灣證券交易所公布注意交易資訊暨處置作業要點第六條，見 docs/rules.md）。

規則引擎（analyze/engine.py）用來算、網頁（dashboard/views.py）用來顯示，都從這裡拿：改規則只改這裡。
（app 的 VIEW 用 SQL 寫「第 1～8 款計入」，在 schema/view.sql，改 COUNTED_CLAUSES 時要一起改。）
"""
import datetime

# 四條路徑的門檻：連 3 日列第 1 款、連 5 日被列注意、10 日內被列 6 日、30 日內被列 12 日
THRESHOLDS = {"streak3": 3, "streak5": 5, "in10": 6, "in30": 12}
COUNTED_CLAUSES = {1, 2, 3, 4, 5, 6, 7, 8}   # 第 1～8 款計入（連 3 日只看第 1 款）；第 9～14 款不計入

# 處置期間（幾個營業日）、撮合間隔（分鐘），見 docs/rules.md「處置期間與措施」
NEW_RULE_DATE = datetime.date(2026, 8, 10)    # 這天起新制（期間縮短、撮合改 2 分鐘）
PUNISH_DAYS = {"new": 5, "old": 10}           # 一般
PUNISH_DAYS_EXTENDED = {"new": 7, "old": 12}  # 連 3 日、連 5 日且計算期間內有第 13 款
EXTEND_CLAUSE = 13                            # 當沖占比過高
MATCH_MINUTES = {"new": {1: 2, 2: 2}, "old": {1: 5, 2: 20}}   # 第幾次處置 → 幾分鐘撮合一次（公告上有寫就照公告）


def rule_era(start: datetime.date | None) -> str:
    """處置開始的日子 → "new"（新制）或 "old"（舊制）；不知道哪天開始的當成新制。

    >>> rule_era(datetime.date(2026, 8, 10)), rule_era(datetime.date(2026, 7, 28)), rule_era(None)
    ('new', 'old', 'new')
    """
    return "new" if start is None or start >= NEW_RULE_DATE else "old"
