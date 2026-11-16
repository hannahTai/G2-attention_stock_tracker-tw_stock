-- raw 層（表名開頭 raw_）：API 回應原文，一個來源一張表，一列＝一份回應
-- 同一個 raw_key 重抓：成功一律覆蓋；失敗只在還沒有成功的那一列時才寫入（不會蓋掉好的資料）
-- 由 common.repository.create_tables() 執行；已存在就跳過

CREATE TABLE IF NOT EXISTS raw_notice (
    raw_key       VARCHAR(30)  NOT NULL COMMENT '查詢日期，例如 20261113',
    fetched_at    DATETIME     NOT NULL COMMENT '抓取時間（台灣時間）',
    http_status   SMALLINT     NOT NULL COMMENT 'HTTP 狀態碼',
    ok            BOOLEAN      NOT NULL COMMENT '成功且內容檢查通過；transform 只讀 ok 的列',
    problem       TEXT                  COMMENT '失敗原因；成功為 NULL',
    url           TEXT         NOT NULL COMMENT '請求網址（含查詢參數）',
    request_body  TEXT                  COMMENT 'POST 的參數（JSON 字串）；GET 為 NULL',
    body          LONGTEXT     NOT NULL COMMENT 'API 回應原文（JSON 字串；被擋時可能是 HTML）',
    loaded_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '寫入時間（台灣時間）',
    PRIMARY KEY (raw_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='證交所 注意股';

CREATE TABLE IF NOT EXISTS raw_punish (
    raw_key       VARCHAR(30)  NOT NULL COMMENT '查詢日期，例如 20261113（查當天起 14 天內處置中的）',
    fetched_at    DATETIME     NOT NULL COMMENT '抓取時間（台灣時間）',
    http_status   SMALLINT     NOT NULL COMMENT 'HTTP 狀態碼',
    ok            BOOLEAN      NOT NULL COMMENT '成功且內容檢查通過；transform 只讀 ok 的列',
    problem       TEXT                  COMMENT '失敗原因；成功為 NULL',
    url           TEXT         NOT NULL COMMENT '請求網址（含查詢參數）',
    request_body  TEXT                  COMMENT 'POST 的參數（JSON 字串）；GET 為 NULL',
    body          LONGTEXT     NOT NULL COMMENT 'API 回應原文（JSON 字串；被擋時可能是 HTML）',
    loaded_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '寫入時間（台灣時間）',
    PRIMARY KEY (raw_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='證交所 處置股';

CREATE TABLE IF NOT EXISTS raw_punish_watchlist (
    raw_key       VARCHAR(30)  NOT NULL COMMENT '名單上的資料日期，例如 20261113',
    fetched_at    DATETIME     NOT NULL COMMENT '抓取時間（台灣時間）',
    http_status   SMALLINT     NOT NULL COMMENT 'HTTP 狀態碼',
    ok            BOOLEAN      NOT NULL COMMENT '成功且內容檢查通過；transform 只讀 ok 的列',
    problem       TEXT                  COMMENT '失敗原因；成功為 NULL',
    url           TEXT         NOT NULL COMMENT '請求網址（含查詢參數）',
    request_body  TEXT                  COMMENT 'POST 的參數（JSON 字串）；GET 為 NULL',
    body          LONGTEXT     NOT NULL COMMENT 'API 回應原文（JSON 字串；被擋時可能是 HTML）',
    loaded_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '寫入時間（台灣時間）',
    PRIMARY KEY (raw_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='證交所 注意累計可能達處置標準名單';

CREATE TABLE IF NOT EXISTS raw_market_daily (
    raw_key       VARCHAR(30)  NOT NULL COMMENT '月份，例如 202611',
    fetched_at    DATETIME     NOT NULL COMMENT '抓取時間（台灣時間）',
    http_status   SMALLINT     NOT NULL COMMENT 'HTTP 狀態碼',
    ok            BOOLEAN      NOT NULL COMMENT '成功且內容檢查通過；transform 只讀 ok 的列',
    problem       TEXT                  COMMENT '失敗原因；成功為 NULL',
    url           TEXT         NOT NULL COMMENT '請求網址（含查詢參數）',
    request_body  TEXT                  COMMENT 'POST 的參數（JSON 字串）；GET 為 NULL',
    body          LONGTEXT     NOT NULL COMMENT 'API 回應原文（JSON 字串；被擋時可能是 HTML）',
    loaded_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '寫入時間（台灣時間）',
    PRIMARY KEY (raw_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='證交所 每日市場成交（一次一個月）';

CREATE TABLE IF NOT EXISTS raw_holiday (
    raw_key       VARCHAR(30)  NOT NULL COMMENT '西元年，例如 2026',
    fetched_at    DATETIME     NOT NULL COMMENT '抓取時間（台灣時間）',
    http_status   SMALLINT     NOT NULL COMMENT 'HTTP 狀態碼',
    ok            BOOLEAN      NOT NULL COMMENT '成功且內容檢查通過；transform 只讀 ok 的列',
    problem       TEXT                  COMMENT '失敗原因；成功為 NULL',
    url           TEXT         NOT NULL COMMENT '請求網址（含查詢參數）',
    request_body  TEXT                  COMMENT 'POST 的參數（JSON 字串）；GET 為 NULL',
    body          LONGTEXT     NOT NULL COMMENT 'API 回應原文（JSON 字串；被擋時可能是 HTML）',
    loaded_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '寫入時間（台灣時間）',
    PRIMARY KEY (raw_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='證交所 開休市日期（一次一年）';

CREATE TABLE IF NOT EXISTS raw_companies (
    raw_key       VARCHAR(30)  NOT NULL COMMENT '抓取日期，例如 20261113（每天一份快照）',
    fetched_at    DATETIME     NOT NULL COMMENT '抓取時間（台灣時間）',
    http_status   SMALLINT     NOT NULL COMMENT 'HTTP 狀態碼',
    ok            BOOLEAN      NOT NULL COMMENT '成功且內容檢查通過；transform 只讀 ok 的列',
    problem       TEXT                  COMMENT '失敗原因；成功為 NULL',
    url           TEXT         NOT NULL COMMENT '請求網址（含查詢參數）',
    request_body  TEXT                  COMMENT 'POST 的參數（JSON 字串）；GET 為 NULL',
    body          LONGTEXT     NOT NULL COMMENT 'API 回應原文（JSON 字串；被擋時可能是 HTML）',
    loaded_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '寫入時間（台灣時間）',
    PRIMARY KEY (raw_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='證交所 OpenAPI 上市公司清單';

CREATE TABLE IF NOT EXISTS raw_announcements (
    raw_key       VARCHAR(30)  NOT NULL COMMENT '公司代號_月份，例如 1303_202611',
    fetched_at    DATETIME     NOT NULL COMMENT '抓取時間（台灣時間）',
    http_status   SMALLINT     NOT NULL COMMENT 'HTTP 狀態碼',
    ok            BOOLEAN      NOT NULL COMMENT '成功且內容檢查通過；transform 只讀 ok 的列',
    problem       TEXT                  COMMENT '失敗原因；成功為 NULL',
    url           TEXT         NOT NULL COMMENT '請求網址（含查詢參數）',
    request_body  TEXT                  COMMENT 'POST 的參數（JSON 字串）；GET 為 NULL',
    body          LONGTEXT     NOT NULL COMMENT 'API 回應原文（JSON 字串；被擋時可能是 HTML）',
    loaded_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '寫入時間（台灣時間）',
    PRIMARY KEY (raw_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='觀測站 重大訊息（一家公司一個月）';

CREATE TABLE IF NOT EXISTS raw_announcement_detail (
    raw_key       VARCHAR(30)  NOT NULL COMMENT '公司代號_輸入日期_序號，例如 9908_20261102_1',
    fetched_at    DATETIME     NOT NULL COMMENT '抓取時間（台灣時間）',
    http_status   SMALLINT     NOT NULL COMMENT 'HTTP 狀態碼',
    ok            BOOLEAN      NOT NULL COMMENT '成功且內容檢查通過；transform 只讀 ok 的列',
    problem       TEXT                  COMMENT '失敗原因；成功為 NULL',
    url           TEXT         NOT NULL COMMENT '請求網址（含查詢參數）',
    request_body  TEXT                  COMMENT 'POST 的參數（JSON 字串）；GET 為 NULL',
    body          LONGTEXT     NOT NULL COMMENT 'API 回應原文（JSON 字串；被擋時可能是 HTML）',
    loaded_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '寫入時間（台灣時間）',
    PRIMARY KEY (raw_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='觀測站 重大訊息全文（一則公告）';
