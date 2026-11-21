-- app 層的 VIEW（表名開頭 app_）：網頁只讀 app_*，需要的 stage 欄位從這裡拿
-- VIEW 是存起來的查詢，不複製資料，查的時候資料庫才去讀 stage 表，所以永遠是最新的
-- 檔名排在 stage.sql 後面：create_tables() 依檔名順序執行，建 VIEW 時 stage 表已經在了
-- 先 DROP 再 CREATE：重跑會用最新的定義蓋掉舊的，不影響資料
-- （PostgreSQL 的 CREATE OR REPLACE VIEW 不能拿掉欄位、改欄位順序或型別，所以整個重建）

DROP VIEW IF EXISTS app_card, app_trading_day, app_notice_history, app_punish_history, app_announcement;


-- ---------- 卡片牆、個股頁最上面 ----------
-- app_progress 每一列，加上名稱、收盤價、今天被列的款次、處置中那次的措施、公告則數
CREATE VIEW app_card AS
SELECT
    p.*,

    -- 最近一次被列注意時的名稱
    (
        SELECT n2.stock_name
        FROM stage_notice n2
        WHERE n2.stock_id = p.stock_id
          AND n2.trade_date <= p.trade_date
        ORDER BY n2.trade_date DESC
        LIMIT 1
    ) AS stock_name,

    -- 今天有沒有被列注意（今日注意股）
    n.stock_id IS NOT NULL AS is_listed_today,

    -- 是不是上市公司（卡片牆只放上市公司，不放權證、ETF）
    EXISTS (
        SELECT 1
        FROM stage_company co
        WHERE co.company_id = p.stock_id
    ) AS is_company,

    -- 當天收盤行情；還沒抓到就用注意股公告上的；那天沒成交為 NULL
    COALESCE(sp.close_price, n.close_price) AS close_price,

    -- 今天被列的原因全文
    n.reason_text,

    -- 今天被列第幾款，例如 1,2
    (
        SELECT string_agg(c.clause::text, ',' ORDER BY c.clause)
        FROM stage_notice_clause c
        WHERE c.stock_id = p.stock_id
          AND c.trade_date = p.trade_date
    ) AS clauses,

    -- 今天會不會累計（有第 1～8 款）
    (
        SELECT COUNT(*) > 0
        FROM stage_notice_clause c
        WHERE c.stock_id = p.stock_id
          AND c.trade_date = p.trade_date
          AND c.clause BETWEEN 1 AND 8
    ) AS is_counted_today,

    -- 處置中那次的措施（欄位名稱和 app_punish_history 一樣）
    s.measure_type,              -- 第一次處置、第二次處置…
    s.trigger_condition,         -- 處置條件，例如 連續三次
    s.match_interval_minutes,    -- 撮合間隔（分鐘）

    -- 近 30 個營業日的公告則數
    (
        SELECT COUNT(*)
        FROM stage_announcement a
        WHERE a.company_id = p.stock_id
          AND a.announce_date <= p.trade_date
          AND a.announce_date >= w.window_start
    ) AS announcement_count

FROM app_progress p

-- 每個交易日的「往前第 30 個營業日」（含當天）先算好一次再接回來；不夠 30 天就用第一個交易日。
-- 不要改回每列各算一次：3 萬列 × 每列一個子查詢，整張排序要 40 秒
LEFT JOIN (
    SELECT
        trade_date,
        COALESCE(LAG(trade_date, 29) OVER (ORDER BY trade_date), MIN(trade_date) OVER ()) AS window_start
    FROM stage_trading_day
    WHERE is_trading
) w
    ON w.trade_date = p.trade_date

LEFT JOIN stage_notice n
    ON n.trade_date = p.trade_date
   AND n.stock_id = p.stock_id

LEFT JOIN stage_stock_price sp
    ON sp.trade_date = p.trade_date
   AND sp.stock_id = p.stock_id

LEFT JOIN stage_punish s
    ON s.stock_id = p.stock_id
   AND s.period_start = p.period_start
   AND s.period_end = p.period_end;


-- ---------- 交易日 ----------
-- 網頁畫注意紀錄格子（近 30 個營業日）、算公告和處置紀錄的起日用
CREATE VIEW app_trading_day AS
SELECT trade_date
FROM stage_trading_day
WHERE is_trading;


-- ---------- 注意紀錄 ----------
-- 每檔每個被列注意的交易日，被列哪幾款、會不會累計
-- 沒被列的日子沒有列（畫成「未列注意」）。「已計入前次處置」＝早於 app_card.count_start 的日子
CREATE VIEW app_notice_history AS
SELECT
    c.trade_date,
    c.stock_id,
    string_agg(c.clause::text, ',' ORDER BY c.clause) AS clauses,
    bool_or(c.clause BETWEEN 1 AND 8) AS is_counted,   -- true＝累計（有第 1～8 款），false＝不累計（只有第 9～14 款）
    MAX(n.reason_text) AS reason_text                  -- 那天被列的原因全文（一天一檔只有一列，MAX 只是為了配合 GROUP BY）
FROM stage_notice_clause c
LEFT JOIN stage_notice n
    ON n.trade_date = c.trade_date
   AND n.stock_id = c.stock_id
GROUP BY c.trade_date, c.stock_id;


-- ---------- 處置紀錄 ----------
-- 處置公告（個股頁列近 30 個營業日內的）
CREATE VIEW app_punish_history AS
SELECT
    announce_date,
    stock_id,
    stock_name,
    trigger_condition,
    period_start,
    period_end,
    measure_type,
    match_interval_minutes,
    content
FROM stage_punish;


-- ---------- 公開資訊（重大訊息）----------
-- 加上交易時段內／後、更正、說明公告的標記，和全文（還沒抓到全文的為 NULL）
CREATE VIEW app_announcement AS
SELECT
    a.company_id AS stock_id,
    a.announce_date,
    a.announce_time,
    a.company_name,
    a.subject,

    -- true＝交易時段內（14:30 前，含盤後定價交易）
    a.announce_time <= '14:30:00' AS is_during_session,

    -- 更正公告
    a.subject LIKE '%更正%' AS is_correction,

    -- 說明、澄清公告（排除「法人說明會」）
    (
        a.subject LIKE '%澄清%'
        OR (a.subject LIKE '%說明%' AND a.subject NOT LIKE '%說明會%')
    ) AS is_explanation,

    d.announce_clause,              -- 符合條款，例如 第31款
    c.short_name AS clause_name,    -- 條款的簡短名稱，例如 財務報告
    d.fact_date,                    -- 事實發生日
    d.content                       -- 說明全文
FROM stage_announcement a
LEFT JOIN stage_announcement_detail d
    ON d.company_id = a.company_id
   AND d.enter_date = a.enter_date
   AND d.serial_number = a.serial_number
LEFT JOIN stage_announcement_clause c
    ON c.announce_clause = d.announce_clause;
