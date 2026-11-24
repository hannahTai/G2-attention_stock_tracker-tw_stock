-- VIEW：app 層的（表名開頭 app_）給網頁讀，網頁只讀 app_*，需要的 stage（和 ai）欄位從這裡拿；
-- 最後面的 stage_watched_stock 是 stage 層的（關注的股票），規則引擎、AI、crawler 都靠它決定要做哪些股票
-- VIEW 是存起來的查詢，不複製資料，查的時候資料庫才去讀 stage（和 ai）表，所以永遠是最新的
-- 檔名排在 stage.sql 後面：create_tables() 依檔名順序執行，建 VIEW 時 stage 表已經在了
-- 先 DROP 再 CREATE：重跑會用最新的定義蓋掉舊的，不影響資料
-- （PostgreSQL 的 CREATE OR REPLACE VIEW 不能拿掉欄位、改欄位順序或型別，所以整個重建）

DROP VIEW IF EXISTS app_card, app_trading_day, app_notice_history, app_punish_history, app_announcement, app_stock_news,
    stage_watched_stock;


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

    -- 漲跌和漲跌幅（%，和參考價比）：只有收盤行情有；還沒抓到、不比價的為 NULL
    sp.price_change,
    ROUND(sp.price_change / NULLIF(sp.close_price - sp.price_change, 0) * 100, 2) AS change_pct,

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
    ) AS announcement_count,

    -- 卡片 AI 摘要（最新一版、通過檢查的；還沒寫、沒通過的為 NULL）和第三句用到的新聞
    cs.summary AS ai_summary,
    cs.news_ids AS ai_summary_news_ids,
    cs.summarized_at AS ai_summary_at,   -- 寫摘要的時間（沿用前一天的，是原本寫的時間）
    cs.reused AS ai_summary_reused       -- 輸入和前一天一樣，沿用前一天的摘要

FROM app_progress p

LEFT JOIN LATERAL (
    SELECT x.summary, x.news_ids, x.summarized_at, x.reused
    FROM ai_card_summary x
    WHERE x.stock_id = p.stock_id AND x.card_date = p.trade_date AND x.summary_ok
    ORDER BY NULLIF(regexp_replace(x.prompt_version, '\D', '', 'g'), '')::INT DESC NULLS LAST, x.summarized_at DESC   -- v8 比 v7 新（沿用的摘要時間是原本寫的）
    LIMIT 1
) cs ON TRUE

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
-- 更正、說明的標記和類型用公告分類（ai_announcement_label 最新一版）；還沒分類的（剛發的公告）先用主旨的關鍵字判斷
CREATE VIEW app_announcement AS
SELECT
    a.company_id AS stock_id,
    a.announcement_id,              -- 公告代號（API、ai_announcement_label 用它對應）
    a.announce_date,
    a.announce_time,
    a.company_name,
    a.subject,

    -- true＝交易時段內（14:30 前，含盤後定價交易）
    a.announce_time <= '14:30:00' AS is_during_session,

    -- 更正公告：公告分類的判斷；還沒分類就看主旨有沒有「更正」
    COALESCE(l.is_correction, a.subject LIKE '%更正%') AS is_correction,

    -- 說明、澄清公告：公告分類的類型是「澄清／回應媒體」；還沒分類就看主旨（排除「法人說明會」）
    COALESCE(
        l.category = '澄清／回應媒體',
        a.subject LIKE '%澄清%' OR (a.subject LIKE '%說明%' AND a.subject NOT LIKE '%說明會%')
    ) AS is_explanation,

    l.category,                     -- 公告分類的類型（13 類）；還沒分類為 NULL
    l.has_news,                     -- 公告分類：有新的資訊（例行、依規定才公布的為否）；還沒分類為 NULL
    l.is_negative,                  -- 公告分類：對公司是壞消息（衰退、虧損、訴訟、裁罰…）；還沒分類為 NULL
    CASE WHEN l.summary_ok THEN l.summary END AS ai_summary,   -- 公告分類的白話摘要（沒通過檢查的不顯示）

    d.announce_clause,              -- 符合條款，例如 第31款
    c.short_name AS clause_name,    -- 條款的簡短名稱，例如 財務報告
    d.fact_date,                    -- 事實發生日
    d.content                       -- 說明全文
FROM stage_announcement a
LEFT JOIN stage_announcement_detail d
    ON d.announcement_id = a.announcement_id
LEFT JOIN stage_announcement_clause c
    ON c.announce_clause = d.announce_clause
LEFT JOIN LATERAL (   -- 最新一版的公告分類
    SELECT x.category, x.is_correction, x.has_news, x.is_negative, x.summary, x.summary_ok
    FROM ai_announcement_label x
    WHERE x.announcement_id = a.announcement_id
    ORDER BY x.labeled_at DESC
    LIMIT 1
) l ON TRUE;


-- ---------- 新聞資訊 ----------
-- 每檔每則新聞（stage_stock_news → stage_news），加上網頁的導言（stage_news_detail；還沒抓、抓不到的為 NULL）、
-- AI 摘要（ai_news_summary；還沒摘要、沒通過檢查的為 NULL）
-- 好幾則新聞共用同一句導言（富聯網的網站介紹、CMoney 個股概覽頁）不是這則新聞的內容，當成沒有
CREATE VIEW app_stock_news AS
SELECT
    s.stock_id,
    n.news_id,
    n.news_time,
    n.news_date,
    n.publisher,
    n.title,
    n.link,
    CASE WHEN shared.news_count >= 3 THEN NULL ELSE d.lead END AS lead,
    summary.summary AS ai_summary,
    COALESCE(st.story_id, n.news_id) AS story_id   -- 同一件事（ai_news_story）；還沒歸類就是自己
FROM stage_stock_news s
JOIN stage_news n
    ON n.news_id = s.news_id
LEFT JOIN stage_news_detail d
    ON d.news_id = n.news_id
LEFT JOIN (
    SELECT lead, COUNT(*) AS news_count
    FROM stage_news_detail
    WHERE lead IS NOT NULL
    GROUP BY lead
) shared
    ON shared.lead = d.lead
LEFT JOIN LATERAL (   -- 最新一版、通過檢查的 AI 摘要
    SELECT a.summary
    FROM ai_news_summary a
    WHERE a.news_id = n.news_id AND a.summary_ok
    ORDER BY a.summarized_at DESC
    LIMIT 1
) summary ON TRUE
LEFT JOIN ai_news_story st   -- 每則新聞一列
    ON st.news_id = n.news_id;

-- ---------- 關注的股票（stage 層）----------
-- 每個交易日關注哪些股票：往前 30 個交易日（含當天）被列注意過的（要點的「30 個營業日內」）。
-- 只讀 stage。規則引擎（analyze，寫 app_progress＝卡片牆）、新聞摘要、卡片 AI 摘要都用這個決定要算、要寫哪些，規則只寫在這裡；
-- 新聞線的 crawler 抓重大訊息、新聞也讀這裡（股價線整理好當天的注意股以後；還沒有那天就用之前最近一個交易日，見 repository.watched_companies）。
-- 處置中、在注意累計名單上的股票一定是最近被列注意過的，所以不用另外加。
-- is_company：上市公司（權證、ETF 為否：規則引擎照樣算，但沒有重大訊息、不查新聞、不寫 AI 摘要）
-- 29＝30 個交易日（含當天）－1：和 common/rules.py 的 WATCH_WINDOW_DAYS 要一樣（tests/test_main.py 會檢查）。
-- 資料最前面的 29 個交易日不夠 30 天，從第一個交易日算起（和 app_card 一樣）
CREATE VIEW stage_watched_stock AS
WITH trading AS (   -- 交易日：照 stage_trading_day（和規則引擎一樣）；那天在 stage_trading_day 整個沒有列（市場成交那個月還沒抓到）、
                    -- 但有人被列注意，也算交易日，不然當天的卡片 AI 摘要會被跳過
    SELECT trade_date FROM stage_trading_day WHERE is_trading
    UNION
    SELECT DISTINCT c.trade_date FROM stage_notice_clause c
    WHERE NOT EXISTS (SELECT 1 FROM stage_trading_day t WHERE t.trade_date = c.trade_date)
), days AS (
    SELECT
        trade_date AS day,
        COALESCE(LAG(trade_date, 29) OVER (ORDER BY trade_date), MIN(trade_date) OVER ()) AS window_start
    FROM trading
)
SELECT DISTINCT
    d.day,
    n.stock_id,
    EXISTS (SELECT 1 FROM stage_company c WHERE c.company_id = n.stock_id) AS is_company
FROM days d
JOIN stage_notice_clause n ON n.trade_date BETWEEN d.window_start AND d.day;   -- 和規則引擎一樣看款次表
