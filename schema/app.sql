-- app 層（表名開頭 app_）：規則引擎算出的結果，給網頁讀
-- 規則見 docs/rules.md；由 analyze/task.py 的 analyze_progress 寫入，每天只處理當天
-- 每個交易日每檔一列；重跑某天只換掉那天的列，process_date 記最後一次是哪天算的
-- 由 common.repository.create_tables() 執行；已存在就跳過

CREATE TABLE IF NOT EXISTS app_progress (
    trade_date    DATE         NOT NULL COMMENT '交易日',
    stock_id      VARCHAR(10)  NOT NULL COMMENT '證券代號（最近 30 個營業日被列過注意的）',
    count_start   DATE         NOT NULL COMMENT '從哪天開始算（上次處置公告的隔一個營業日；之前沒處置過就是資料第一天）',
    streak3       TINYINT      NOT NULL COMMENT '連續幾個營業日被列第 1 款（門檻 3）',
    streak5       TINYINT      NOT NULL COMMENT '連續幾個營業日被列第 1～8 款（門檻 5）',
    in10          TINYINT      NOT NULL COMMENT '最近 10 個營業日內有幾天被列第 1～8 款（門檻 6）',
    in30          TINYINT      NOT NULL COMMENT '最近 30 個營業日內有幾天被列第 1～8 款（門檻 12）',
    streak3_left  TINYINT               COMMENT '連 3 日還差幾次：之後每個營業日都被列第 1 款，再幾天達標；今天達標為 NULL',
    streak5_left  TINYINT               COMMENT '連 5 日還差幾次：之後每個營業日都被列第 1～8 款，再幾天達標；今天達標為 NULL',
    in10_left     TINYINT               COMMENT '10 日 6 次還差幾次（已算進會滑出窗口的日子）；今天達標為 NULL',
    in30_left     TINYINT               COMMENT '30 日 12 次還差幾次（已算進會滑出窗口的日子）；今天達標為 NULL',
    steps_left    TINYINT               COMMENT '還差幾次：四條裡最少的；今天達標為 NULL',
    closest_path  VARCHAR(10)           COMMENT '最快達標的那條（同分依 streak3、streak5、in10、in30 的順序）',
    punished_by   VARCHAR(40)           COMMENT '今天達到哪幾條，例如 streak3,in10；沒有為 NULL',
    has_full_history  BOOLEAN      NOT NULL COMMENT '前面有沒有足夠的資料（30 個營業日）；沒有的話 in30 可能偏少',
    punish_start  DATE                  COMMENT '處置起日：公布當天到出關當天都有值；沒有為 NULL，下同',
    punish_end    DATE                  COMMENT '處置迄日',
    resume_date   DATE                  COMMENT '出關：迄日的下一個營業日，恢復正常交易',
    next_listed_date  DATE              COMMENT '只差一次時：假設再被列注意的那天（下一個營業日）；不是只差一次為 NULL，下同',
    next_punish_round TINYINT           COMMENT '那次會是第幾次處置（最近 30 個營業日內已處置過 → 2）',
    next_punish_start DATE              COMMENT '處置起日（再列那天的下一個營業日）',
    next_punish_end   DATE              COMMENT '處置迄日（5 個營業日；連 3／連 5 日含第 13 款 7 個；8/10 前舊制 10／12 個）',
    next_resume_date  DATE              COMMENT '恢復正常交易的日子；未來的營業日依開休市日期推算，臨時休市要隔天重算才會反映',
    process_date  DATE         NOT NULL COMMENT '哪一天算的（台灣日期；重跑會更新）',
    loaded_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '寫入時間（台灣時間）',
    PRIMARY KEY (trade_date, stock_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='每檔注意股離處置還差幾次、處置中幾號出關（每個交易日每檔一列）';

CREATE TABLE IF NOT EXISTS app_progress_date (
    trade_date    DATE         NOT NULL COMMENT '交易日（哪一天的進度）',
    stock_id      VARCHAR(10)  NOT NULL COMMENT '證券代號',
    path          VARCHAR(10)  NOT NULL COMMENT 'streak3／streak5／in10／in30',
    counted_date  DATE         NOT NULL COMMENT '這一條計入的一天',
    process_date  DATE         NOT NULL COMMENT '哪一天算的（台灣日期；重跑會更新）',
    loaded_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '寫入時間（台灣時間）',
    PRIMARY KEY (trade_date, stock_id, path, counted_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='每一條（連 3 日、連 5 日…）計入了哪幾天';
