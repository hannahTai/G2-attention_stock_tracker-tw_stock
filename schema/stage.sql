-- stage 層（表名開頭 stage_）：raw 解析後的乾淨資料，一列 = 官方公告的一個事實，不做業務判斷
-- 由 common.repository.create_tables() 執行；已存在就跳過

CREATE TABLE IF NOT EXISTS stage_notice (
    trade_date  DATE           NOT NULL COMMENT '交易日',
    stock_id    VARCHAR(10)    NOT NULL COMMENT '證券代號（含權證、ETF、DR）',
    stock_name  VARCHAR(50)    NOT NULL COMMENT '證券名稱',
    reason_text TEXT           NOT NULL COMMENT '注意交易資訊全文（已去 HTML）',
    close_price DECIMAL(12, 4)          COMMENT '收盤價',
    pe_ratio    DECIMAL(12, 4)          COMMENT '本益比；無本益比為 NULL',
    loaded_at   DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '寫入時間（台灣時間）',
    PRIMARY KEY (trade_date, stock_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='注意股（每日）';

CREATE TABLE IF NOT EXISTS stage_notice_day (
    trade_date    DATE      NOT NULL COMMENT '日期（有這列＝那天的注意股抓到也整理好了）',
    notice_count  INT       NOT NULL COMMENT '那天幾檔被列注意；0＝沒人被列',
    loaded_at     DATETIME  NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '寫入時間（台灣時間）',
    PRIMARY KEY (trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='注意股：每天一列（分辨「沒人被列」和「還沒抓到」）';

CREATE TABLE IF NOT EXISTS stage_notice_clause (
    trade_date  DATE           NOT NULL,
    stock_id    VARCHAR(10)    NOT NULL,
    clause      TINYINT        NOT NULL COMMENT '款次 1～14',
    loaded_at   DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '寫入時間（台灣時間）',
    PRIMARY KEY (trade_date, stock_id, clause)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='注意股觸發的款次';

CREATE TABLE IF NOT EXISTS stage_punish (
    announce_date  DATE         NOT NULL COMMENT '公布日期',
    stock_id       VARCHAR(10)  NOT NULL COMMENT '證券代號',
    stock_name     VARCHAR(50)  NOT NULL COMMENT '證券名稱',
    trigger_condition VARCHAR(50) NOT NULL COMMENT '處置條件，例如 連續三次（不用 condition：MySQL 保留字）',
    period_start   DATE         NOT NULL COMMENT '處置起日',
    period_end     DATE         NOT NULL COMMENT '處置迄日',
    measure_type   VARCHAR(20)  NOT NULL COMMENT '處置措施，例如 第一次處置',
    match_interval_minutes TINYINT               COMMENT '撮合間隔（分鐘）；內容沒寫為 NULL',
    content        TEXT         NOT NULL COMMENT '處置內容全文',
    has_star_mark  BOOLEAN      NOT NULL COMMENT '公布日期帶 * 號（意義未確認）',
    loaded_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '寫入時間（台灣時間）',
    PRIMARY KEY (announce_date, stock_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='處置股公告';

CREATE TABLE IF NOT EXISTS stage_punish_day (
    query_date    DATE      NOT NULL COMMENT '查詢日期（有這列＝那天的處置股抓到也整理好了）',
    punish_count  INT       NOT NULL COMMENT '查到幾則處置公告（這天起 14 天內處置中的）；0＝沒有',
    loaded_at     DATETIME  NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '寫入時間（台灣時間）',
    PRIMARY KEY (query_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='處置股：每天一列（分辨「沒有處置」和「還沒抓到」）';

CREATE TABLE IF NOT EXISTS stage_punish_watchlist (
    data_date   DATE         NOT NULL COMMENT '名單的資料日期',
    stock_id    VARCHAR(10)  NOT NULL,
    stock_name  VARCHAR(50)  NOT NULL,
    progress_text TEXT         NOT NULL COMMENT '近期達標準之情形；多行以換行分隔',
    loaded_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '寫入時間（台灣時間）',
    PRIMARY KEY (data_date, stock_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='注意累計可能達處置標準名單（官方答案）';

CREATE TABLE IF NOT EXISTS stage_trading_day (
    trade_date    DATE            NOT NULL COMMENT '日期：市場成交涵蓋到的每一天（沒有這列＝還沒抓到）',
    is_trading    BOOLEAN         NOT NULL COMMENT '1＝有交易；0＝沒交易（週末、休市、颱風假）',
    volume_shares BIGINT                   COMMENT '成交股數；沒交易的日子為 NULL，下同',
    amount_twd    BIGINT                   COMMENT '成交金額（元）',
    trade_count   BIGINT                   COMMENT '成交筆數',
    taiex_index   DECIMAL(12, 2)           COMMENT '發行量加權股價指數',
    taiex_change_points DECIMAL(12, 2)           COMMENT '漲跌點數',
    loaded_at     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '寫入時間（台灣時間）',
    PRIMARY KEY (trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='交易日曆：每天一列，以實際成交為準';

CREATE TABLE IF NOT EXISTS stage_holiday (
    holiday_date  DATE          NOT NULL,
    holiday_name  VARCHAR(100)  NOT NULL,
    description   TEXT,
    loaded_at     DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '寫入時間（台灣時間）',
    PRIMARY KEY (holiday_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='官方開休市日期（不含臨時休市）';

CREATE TABLE IF NOT EXISTS stage_company (
    company_id     VARCHAR(10)   NOT NULL,
    short_name     VARCHAR(50)   NOT NULL,
    full_name      VARCHAR(200)  NOT NULL COMMENT '公司全名',
    industry_code  VARCHAR(10)   NOT NULL COMMENT '產業別代碼',
    listed_date    DATE                   COMMENT '上市日期',
    snapshot_date  DATE          NOT NULL COMMENT '取自哪一天的快照',
    loaded_at      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '寫入時間（台灣時間）',
    PRIMARY KEY (company_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='上市公司（依最新快照更新；下市的留著）';

CREATE TABLE IF NOT EXISTS stage_announcement (
    company_id     VARCHAR(10)   NOT NULL,
    announce_date  DATE          NOT NULL COMMENT '發言日期',
    enter_date     DATE          NOT NULL COMMENT '輸入日期（序號依這天編，可能早於發言日期）',
    serial_number  INT           NOT NULL COMMENT '輸入日期當天的序號',
    announce_time  TIME          NOT NULL COMMENT '發言時間（判斷收盤前／後用）',
    company_name   VARCHAR(50)   NOT NULL,
    subject        TEXT          NOT NULL COMMENT '主旨',
    loaded_at      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '寫入時間（台灣時間）',
    PRIMARY KEY (company_id, enter_date, serial_number)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='公司重大訊息';

CREATE TABLE IF NOT EXISTS stage_announcement_detail (
    company_id     VARCHAR(10)   NOT NULL,
    enter_date     DATE          NOT NULL COMMENT '輸入日期（和 stage_announcement 同一個主鍵）',
    serial_number  INT           NOT NULL COMMENT '輸入日期當天的序號',
    clause         VARCHAR(20)   NOT NULL COMMENT '符合條款，例如 第31款（觀測站的公告分類）',
    fact_date      DATE                   COMMENT '事實發生日',
    content        MEDIUMTEXT    NOT NULL COMMENT '說明全文（不含發言人姓名、電話）',
    loaded_at      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '寫入時間（台灣時間）',
    PRIMARY KEY (company_id, enter_date, serial_number)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='公司重大訊息全文';
