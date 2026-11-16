"""schema/*.sql 裡的固定資料：不連資料庫，直接讀 SQL 檔檢查。執行：uv run pytest"""
import re

from common.config import PROJECT_ROOT


def test_clause_table_has_every_clause_once():
    """重大訊息條款對照表：第 1～51 款各一列，款次文字和數字對得上（第31款 ↔ 31）。"""
    sql = (PROJECT_ROOT / "schema" / "stage.sql").read_text(encoding="utf-8")
    rows = re.findall(r"\('第(\d+)款', (\d+), '([^']+)', '([^']+)'\)", sql)
    assert [int(text) for text, _, _, _ in rows] == list(range(1, 52))
    assert all(text == number for text, number, _, _ in rows)
    assert all(len(name) <= 20 for _, _, name, _ in rows)   # short_name VARCHAR(20)
