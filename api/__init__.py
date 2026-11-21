"""資料 API：FastAPI 讀 app_* 表和 VIEW，回 JSON。網頁（dashboard/）只打這個 API，不直接連資料庫。

  main.py      建 FastAPI app、掛上路由（uvicorn api.main:app）
  routes.py    端點：/health、/dates、/cards、/stocks/{stock_id}
  queries.py   讀資料庫（只讀 app_*）
"""
