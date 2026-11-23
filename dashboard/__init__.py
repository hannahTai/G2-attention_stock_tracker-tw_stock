"""網頁（FastAPI＋Jinja2）：打 api/（資料 API）拿資料，畫出卡片牆和個股頁。不直接連資料庫。

  app.py      進入點（uvicorn dashboard.app:app --port 8502）：讀網址參數、打 API、回傳整頁 HTML
  client.py   呼叫 API（requests），把回應裡的日期、時間字串換回 date、time
  views.py    把 API 給的資料整理成畫面要顯示的東西（不碰網路，可以單獨測試）
  render.py   把 views 的結果套進 templates/ 的 Jinja2 範本，變成 HTML
  templates/  版面（_macros.html 是共用的小元件）；static/style.css 是全部樣式
"""
