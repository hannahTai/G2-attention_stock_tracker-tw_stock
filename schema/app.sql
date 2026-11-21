-- app 層（表名開頭 app_）：規則引擎算出的結果，給網頁讀
-- 規則見 docs/rules.md；由 analyze/task.py 的 analyze_progress 寫入，每天只處理當天
-- 每個交易日每檔一列；重跑某天只換掉那天的列，process_date 記最後一次是哪天算的
-- 由 common.repository.create_tables() 執行；已存在就跳過

CREATE TABLE IF NOT EXISTS app_progress (
    trade_date        DATE        NOT NULL,
    stock_id          VARCHAR(10) NOT NULL,
    count_start       DATE        NOT NULL,
    streak3           SMALLINT     NOT NULL,
    streak5           SMALLINT     NOT NULL,
    in10              SMALLINT     NOT NULL,
    in30              SMALLINT     NOT NULL,
    streak3_left      SMALLINT,
    streak5_left      SMALLINT,
    in10_left         SMALLINT,
    in30_left         SMALLINT,
    steps_left        SMALLINT,
    closest_path      VARCHAR(10),
    punished_by       VARCHAR(40),
    has_full_history  BOOLEAN     NOT NULL,
    period_start      DATE,
    period_end        DATE,
    resume_date       DATE,
    next_listed_date  DATE,
    next_punish_round SMALLINT,
    next_period_start DATE,
    next_period_end   DATE,
    next_resume_date  DATE,
    process_date      DATE        NOT NULL,
    loaded_at         TIMESTAMP(0) NOT NULL DEFAULT LOCALTIMESTAMP(0),
    PRIMARY KEY (trade_date, stock_id)
);
COMMENT ON TABLE app_progress IS '每檔注意股離處置還差幾次、處置中幾號出關（每個交易日每檔一列）';
COMMENT ON COLUMN app_progress.trade_date IS '交易日';
COMMENT ON COLUMN app_progress.stock_id IS '證券代號（最近 30 個營業日被列過注意的）';
COMMENT ON COLUMN app_progress.count_start IS '從哪天開始算（上次處置公告的隔一個營業日；之前沒處置過就是資料第一天）';
COMMENT ON COLUMN app_progress.streak3 IS '連續幾個營業日被列第 1 款（門檻 3）';
COMMENT ON COLUMN app_progress.streak5 IS '連續幾個營業日被列第 1～8 款（門檻 5）';
COMMENT ON COLUMN app_progress.in10 IS '最近 10 個營業日內有幾天被列第 1～8 款（門檻 6）';
COMMENT ON COLUMN app_progress.in30 IS '最近 30 個營業日內有幾天被列第 1～8 款（門檻 12）';
COMMENT ON COLUMN app_progress.streak3_left IS '連 3 日還差幾次：之後每個營業日都被列第 1 款，再幾天達標；今天達標為 NULL';
COMMENT ON COLUMN app_progress.streak5_left IS '連 5 日還差幾次：之後每個營業日都被列第 1～8 款，再幾天達標；今天達標為 NULL';
COMMENT ON COLUMN app_progress.in10_left IS '10 日 6 次還差幾次（已算進會滑出窗口的日子）；今天達標為 NULL';
COMMENT ON COLUMN app_progress.in30_left IS '30 日 12 次還差幾次（已算進會滑出窗口的日子）；今天達標為 NULL';
COMMENT ON COLUMN app_progress.steps_left IS '還差幾次：四條裡最少的；今天達標為 NULL';
COMMENT ON COLUMN app_progress.closest_path IS '最快達標的那條（同分依 streak3、streak5、in10、in30 的順序）';
COMMENT ON COLUMN app_progress.punished_by IS '今天達到哪幾條，例如 streak3,in10；沒有為 NULL';
COMMENT ON COLUMN app_progress.has_full_history IS '前面有沒有足夠的資料（30 個營業日）；沒有的話 in30 可能偏少';
COMMENT ON COLUMN app_progress.period_start IS '處置起日：公布當天到出關當天都有值；沒有為 NULL，下同';
COMMENT ON COLUMN app_progress.period_end IS '處置迄日';
COMMENT ON COLUMN app_progress.resume_date IS '出關：迄日的下一個營業日，恢復正常交易';
COMMENT ON COLUMN app_progress.next_listed_date IS '只差一次時：假設再被列注意的那天（下一個營業日）；不是只差一次為 NULL，下同';
COMMENT ON COLUMN app_progress.next_punish_round IS '那次會是第幾次處置（最近 30 個營業日內已處置過 → 2）';
COMMENT ON COLUMN app_progress.next_period_start IS '處置起日（再列那天的下一個營業日）';
COMMENT ON COLUMN app_progress.next_period_end IS '處置迄日（5 個營業日；連 3／連 5 日含第 13 款 7 個；8/10 前舊制 10／12 個）';
COMMENT ON COLUMN app_progress.next_resume_date IS '恢復正常交易的日子；未來的營業日依開休市日期推算，臨時休市要隔天重算才會反映';
COMMENT ON COLUMN app_progress.process_date IS '哪一天算的（台灣日期；重跑會更新）';
COMMENT ON COLUMN app_progress.loaded_at IS '寫入時間（台灣時間）';

CREATE TABLE IF NOT EXISTS app_progress_date (
    trade_date   DATE        NOT NULL,
    stock_id     VARCHAR(10) NOT NULL,
    path         VARCHAR(10) NOT NULL,
    counted_date DATE        NOT NULL,
    process_date DATE        NOT NULL,
    loaded_at    TIMESTAMP(0) NOT NULL DEFAULT LOCALTIMESTAMP(0),
    PRIMARY KEY (trade_date, stock_id, path, counted_date)
);
COMMENT ON TABLE app_progress_date IS '每一條（連 3 日、連 5 日…）計入了哪幾天';
COMMENT ON COLUMN app_progress_date.trade_date IS '交易日（哪一天的進度）';
COMMENT ON COLUMN app_progress_date.stock_id IS '證券代號';
COMMENT ON COLUMN app_progress_date.path IS 'streak3／streak5／in10／in30';
COMMENT ON COLUMN app_progress_date.counted_date IS '這一條計入的一天';
COMMENT ON COLUMN app_progress_date.process_date IS '哪一天算的（台灣日期；重跑會更新）';
COMMENT ON COLUMN app_progress_date.loaded_at IS '寫入時間（台灣時間）';
