"""資料 API 的進入點。

啟動（專案根目錄）：
    uv run uvicorn api.main:app --reload --port 8001
    # uvicorn 是 ASGI 伺服器：FastAPI 只「定義」API，開 port、收請求的是 uvicorn
    # api.main:app ＝ api/main.py 裡叫 app 的物件；--reload：存檔自動重啟（開發用）
互動式文件 http://localhost:8001/docs ；健康檢查 http://localhost:8001/health
"""
import logging

import fastapi
import fastapi.responses

from api import routes

app = fastapi.FastAPI(
    title="注意股，還差幾步？ API",
    description="上市注意股的處置進度：交易日、卡片牆、個股",
    version="0.1.0",
)
app.include_router(routes.router)


@app.exception_handler(Exception)
def unexpected_error(request: fastapi.Request, exc: Exception):
    """沒料到的錯誤（例如資料庫連不上）：記在 log，回應只說發生錯誤，不露出細節。"""
    logging.getLogger(__name__).exception("API 發生錯誤：%s", request.url)
    return fastapi.responses.JSONResponse({"detail": "系統發生錯誤，請稍後再試。"}, status_code=500)
