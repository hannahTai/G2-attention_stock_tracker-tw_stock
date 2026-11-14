# 任務追蹤

> 每完成一項就打勾。個人製作，負責人都是 @hannahTai。

---

## 前置期

- [x] GitHub repo 建好（G2-attention_stock_tracker-tw_stock）
- [x] 題目選定
- [x] 架構圖畫好（[docs/images/](docs/images/)）
- [x] 7 個資料來源實測

---

## 實作期

### 第一階段：跑通最小可行版

- [x] 證交所資料抓進 raw 表（注意股、處置股、注意累計名單、開休市、市場成交、上市公司）
- [x] 觀測站重大訊息抓進 raw 表
- [x] raw → stage 清理（款次解析、民國轉西元、去重）
- [x] 回補 2026/07/01 起的資料
- [x] 規則引擎：要點第六條四條路徑、處置後重新計數（stage → app）
- [x] 回測：上市公司 66/66 一致（[docs/rules.md](docs/rules.md)）
- [x] 每日排程：crawler.producer → transform.producer → analyze.producer

### 第二階段：擴充 + 自動化

- [x] 若再列注意：從哪天處置到哪天（5／7 個營業日、休市日）
- [ ] 每日和官方「只差一次」名單比對（官方一致／推算／不一致）
- [ ] Docker Compose（MySQL、RabbitMQ、Flower、worker）
- [ ] Celery：觀測站逐家查詢限速、失敗重試
- [ ] Airflow DAG 取代 Windows 排程
- [ ] 失敗通知：每日排程失敗（結束代碼 1）時通知，附上 log 裡 `✗` 的那幾行（Windows 通知／Discord／Telegram／Email 擇一；上 Airflow 後改用 on_failure_callback）
- [ ] FastAPI 卡片網站：個股卡片、卡片牆

### 第三階段：打磨 + 收尾

- [ ] 上 GCP（VM、Cloud SQL、BigQuery、Looker Studio）
- [ ] Metabase 統計儀表板
- [ ] 功能凍結、修 bug

---

## Demo 準備

- [ ] 投影片
- [ ] 講稿 / demo 流程
- [ ] 練習至少 1 次
- [ ] README 更新到最新狀態
