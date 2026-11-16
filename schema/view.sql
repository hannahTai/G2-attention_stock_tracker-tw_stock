-- app 層的 VIEW（表名開頭 app_）：網頁只讀 app_*，需要的 stage 欄位從這裡拿
-- VIEW 是存起來的查詢，不複製資料，查的時候 MySQL 才去讀 stage 表，所以永遠是最新的
-- 檔名排在 stage.sql 後面：create_tables() 依檔名順序執行，建 VIEW 時 stage 表已經在了
-- CREATE OR REPLACE：重跑會用最新的定義蓋掉舊的，不影響資料

-- 卡片牆、個股卡片上半部：app_progress 每一列，加上名稱、收盤價、今天被列的款次、公告則數
CREATE OR REPLACE VIEW app_card AS
SELECT
    p.*,
    (SELECT n2.stock_name FROM stage_notice n2
      WHERE n2.stock_id = p.stock_id AND n2.trade_date <= p.trade_date
      ORDER BY n2.trade_date DESC LIMIT 1)                      AS stock_name,      -- 最近一次被列注意時的名稱
    n.stock_id IS NOT NULL                                      AS is_listed_today, -- 今天有沒有被列注意（注意股分頁）
    EXISTS (SELECT 1 FROM stage_company co WHERE co.company_id = p.stock_id)
                                                                AS is_company,      -- 是不是上市公司（卡片牆只放上市公司，不放權證、ETF）
    n.close_price,                                                                  -- 今天沒被列為 NULL
    n.reason_text,                                                                  -- 今天被列的原因全文
    (SELECT GROUP_CONCAT(c.clause ORDER BY c.clause) FROM stage_notice_clause c
      WHERE c.stock_id = p.stock_id AND c.trade_date = p.trade_date)  AS clauses,    -- 今天被列第幾款，例如 1,2
    (SELECT COUNT(*) > 0 FROM stage_notice_clause c
      WHERE c.stock_id = p.stock_id AND c.trade_date = p.trade_date
        AND c.clause BETWEEN 1 AND 8)                           AS is_counted_today, -- 今天會不會累計（第 1～8 款）
    s.measure_type    AS punish_measure,                                            -- 處置中那次：第一次處置、第二次處置…
    s.trigger_condition AS punish_condition,                                        -- 處置條件，例如 連續三次
    s.match_interval_minutes AS punish_match_minutes,                               -- 撮合間隔（分鐘）
    (SELECT COUNT(*) FROM stage_announcement a
      WHERE a.company_id = p.stock_id
        AND a.announce_date <= p.trade_date
        AND a.announce_date >= (SELECT MIN(t.trade_date) FROM
              (SELECT trade_date FROM stage_trading_day d
                WHERE d.is_trading AND d.trade_date <= p.trade_date ORDER BY d.trade_date DESC LIMIT 30) t)
    )                                                           AS announcement_count   -- 近 30 個營業日的公告則數
FROM app_progress p
LEFT JOIN stage_notice n
       ON n.trade_date = p.trade_date AND n.stock_id = p.stock_id
LEFT JOIN stage_punish s
       ON s.stock_id = p.stock_id AND s.period_start = p.punish_start AND s.period_end = p.punish_end;

-- 注意紀錄格子：每檔每個被列注意的交易日，被列哪幾款、會不會累計
-- 沒被列的日子沒有列（畫成「未列注意」）。「已計入前次處置」＝早於 app_card.count_start 的日子
CREATE OR REPLACE VIEW app_notice_history AS
SELECT
    c.trade_date,
    c.stock_id,
    GROUP_CONCAT(c.clause ORDER BY c.clause) AS clauses,
    MAX(c.clause BETWEEN 1 AND 8)            AS is_counted  -- 1＝累計（有第 1～8 款），0＝不累計（只有第 9～14 款）
FROM stage_notice_clause c
GROUP BY c.trade_date, c.stock_id;

-- 處置紀錄：處置公告（卡片上列近 30 個營業日內的）
CREATE OR REPLACE VIEW app_punish_history AS
SELECT announce_date, stock_id, stock_name, trigger_condition, period_start, period_end,
       measure_type, match_interval_minutes, content
FROM stage_punish;

-- 公司公告：加上交易時段內／後、更正、說明公告的標記，和全文（還沒抓到全文的為 NULL）
CREATE OR REPLACE VIEW app_announcement AS
SELECT
    a.company_id AS stock_id,
    a.announce_date,
    a.announce_time,
    a.company_name,
    a.subject,
    a.announce_time <= '14:30:00' AS is_during_session,  -- 1＝交易時段內（14:30 前，含盤後定價交易）
    a.subject LIKE '%更正%'        AS is_correction,      -- 更正公告
    (a.subject LIKE '%澄清%' OR (a.subject LIKE '%說明%' AND a.subject NOT LIKE '%說明會%'))
                                  AS is_explanation,     -- 說明、澄清公告（排除「法人說明會」）
    d.clause,                                            -- 符合條款，例如 第31款
    c.short_name AS clause_name,                         -- 條款的簡短名稱，例如 財務報告
    d.fact_date,                                         -- 事實發生日
    d.content                                            -- 說明全文
FROM stage_announcement a
LEFT JOIN stage_announcement_detail d
       ON d.company_id = a.company_id AND d.enter_date = a.enter_date AND d.serial_number = a.serial_number
LEFT JOIN stage_announcement_clause c
       ON c.clause = d.clause;
