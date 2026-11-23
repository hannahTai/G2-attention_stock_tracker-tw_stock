"""API 端點：只回 JSON，給 dashboard/（網頁）呼叫。

  GET /health                  網站活著、資料庫連得上
  GET /dates                   最近 30 個有算的交易日（日期選單用）
  GET /cards?date=             這一天每檔上市公司一列（卡片牆）
  GET /stocks/{stock_id}?date= 個股頁要的全部資料

函式名稱用動詞（list_、get_），和 FastAPI 文件、課程一樣；Swagger 上會顯示成 List Cards、Get Stock。
回傳的是資料庫的原始值（日期在 JSON 裡是 2026-11-20 這種字串），怎麼顯示由 dashboard/ 決定。
路由用一般的 def（不是 async def）：查資料庫是同步的，FastAPI 會把 def 放到執行緒裡跑，不會卡住其他請求。
互動式文件：http://localhost:8001/docs
"""
import datetime

import fastapi
import sqlalchemy
import sqlalchemy.exc

from api import queries
from common.config import NEWS_LOOKBACK_DAYS
from common.date_utils import parse_yyyymmdd_strict
from common.repository import get_engine

router = fastapi.APIRouter()

# Swagger（/docs）上的分組
TAG_SYSTEM = "系統"
TAG_STOCK = "注意股"

DATE_HELP = "交易日，只接受 8 位數字，例如 20261120；不給就是最新一天。沒有資料的日子改用那天以前最近的交易日"


def parse_date(text: str | None) -> datetime.date | None:
    """網址上的日期：只接受 8 位數字的 20261120（和專案其他地方一樣）。空的為 None；其他寫法回 422。

    >>> parse_date("20261120"), parse_date("")
    (datetime.date(2026, 11, 20), None)
    """
    if not text:
        return None
    try:
        return parse_yyyymmdd_strict(text)
    except ValueError:
        raise fastapi.HTTPException(status_code=422, detail=f"看不懂日期「{text}」；請寫成 8 位數字，例如 20261120。") from None


def pick_day(conn: sqlalchemy.Connection, requested: datetime.date | None) -> datetime.date:
    """要回哪一天：沒給日期 → 最新一天；給的日期沒有資料（假日、還沒算到）→ 那天以前最近的交易日。"""
    day = queries.latest_trade_date(conn, requested)
    if day is None:
        if requested is None:
            raise fastapi.HTTPException(status_code=404, detail="還沒有任何一天的資料，先跑 main.py")
        raise fastapi.HTTPException(status_code=404, detail=f"{requested} 以前沒有資料")
    return day


@router.get("/health", tags=[TAG_SYSTEM])
def get_health() -> dict:
    """健康檢查：網站活著、資料庫連得上。錯誤細節不回給外面（可能有主機、帳號）。"""
    try:
        with get_engine().connect() as conn:
            conn.execute(sqlalchemy.text("SELECT 1"))
        return {"status": "ok", "database": "connected"}
    except sqlalchemy.exc.SQLAlchemyError:
        return {"status": "degraded", "database": "error"}


@router.get("/dates", tags=[TAG_STOCK])
def list_dates() -> dict:
    """最近 30 個有算的交易日，新的在前。"""
    with get_engine().connect() as conn:
        return {"dates": queries.recent_trade_dates(conn)}


@router.get("/cards", tags=[TAG_STOCK])
def list_cards(date: str | None = fastapi.Query(None, description=DATE_HELP)) -> dict:
    """這一天每檔上市公司一列（app_card）。trade_date 是實際回的日子，可能和要求的不同。"""
    with get_engine().connect() as conn:
        day = pick_day(conn, parse_date(date))
        return {"trade_date": day, "cards": queries.cards(conn, day)}


@router.get("/stocks/{stock_id}", tags=[TAG_STOCK])
def get_stock(stock_id: str, date: str | None = fastapi.Query(None, description=DATE_HELP)) -> dict:
    """個股：卡片、四條路徑計入的日子、近 30 個營業日的注意紀錄、處置紀錄、公開資訊（重大訊息）、
    卡片 AI 摘要（和第三句的出處）、近 NEWS_LOOKBACK_DAYS 天的新聞。"""
    with get_engine().connect() as conn:
        day = pick_day(conn, parse_date(date))
        card = queries.card(conn, stock_id, day)
        if card is None:
            raise fastapi.HTTPException(status_code=404, detail=f"{day:%m/%d} 沒有 {stock_id} 的資料")
        days = queries.trading_days(conn, day)
        start = days[0] if days else day
        return {
            "trade_date": day,
            "card": card,
            "counted_dates": queries.counted_dates(conn, stock_id, day),
            "trading_days": days,
            "notice_history": queries.notice_history(conn, stock_id, start, day),
            "punish_history": queries.punish_history(conn, stock_id, start, day),
            "announcements": queries.announcements(conn, stock_id, start, day),
            "ai_summary": ({"summary": card["ai_summary"], "summarized_at": card.get("ai_summary_at"),
                            "sources": queries.news_by_ids(conn, card["ai_summary_news_ids"] or [])}
                           if card.get("ai_summary") else None),
            "news": queries.stock_news(conn, stock_id, day - datetime.timedelta(days=NEWS_LOOKBACK_DAYS - 1), day),   # 含當天
        }
