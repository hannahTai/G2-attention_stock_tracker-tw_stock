-- raw 層（表名開頭 raw_）：API 回應原文，一個來源一張表，一列＝抓一次的回應
-- 只新增、不修改：同一個 raw_key 每抓一次就多一列，成功、失敗都留；主鍵（raw_key, fetched_at）
-- 讀的時候只拿每個 raw_key 最新一份成功的（common.repository.read_raws）；失敗的列不會蓋掉之前成功的
-- 由 common.repository.create_tables() 執行；已存在就跳過

CREATE TABLE IF NOT EXISTS raw_notice (
    raw_key      VARCHAR(30) NOT NULL,
    fetched_at   TIMESTAMP(6) NOT NULL,
    http_status  SMALLINT    NOT NULL,
    ok           BOOLEAN     NOT NULL,
    problem      TEXT,
    url          TEXT        NOT NULL,
    request_body TEXT,
    body         TEXT    NOT NULL,
    loaded_at    TIMESTAMP(0) NOT NULL DEFAULT LOCALTIMESTAMP(0),
    PRIMARY KEY (raw_key, fetched_at)
);
COMMENT ON TABLE raw_notice IS '證交所 注意股';
COMMENT ON COLUMN raw_notice.raw_key IS '查詢日期，例如 20261113';
COMMENT ON COLUMN raw_notice.fetched_at IS '抓取時間（台灣時間）';
COMMENT ON COLUMN raw_notice.http_status IS 'HTTP 狀態碼';
COMMENT ON COLUMN raw_notice.ok IS '成功且內容檢查通過；transform 只讀 ok 的列';
COMMENT ON COLUMN raw_notice.problem IS '失敗原因；成功為 NULL';
COMMENT ON COLUMN raw_notice.url IS '請求網址（含查詢參數）';
COMMENT ON COLUMN raw_notice.request_body IS 'POST 的參數（JSON 字串）；GET 為 NULL';
COMMENT ON COLUMN raw_notice.body IS 'API 回應原文（JSON 字串；被擋時可能是 HTML）';
COMMENT ON COLUMN raw_notice.loaded_at IS '寫入時間（台灣時間）';

CREATE TABLE IF NOT EXISTS raw_punish (
    raw_key      VARCHAR(30) NOT NULL,
    fetched_at   TIMESTAMP(6) NOT NULL,
    http_status  SMALLINT    NOT NULL,
    ok           BOOLEAN     NOT NULL,
    problem      TEXT,
    url          TEXT        NOT NULL,
    request_body TEXT,
    body         TEXT    NOT NULL,
    loaded_at    TIMESTAMP(0) NOT NULL DEFAULT LOCALTIMESTAMP(0),
    PRIMARY KEY (raw_key, fetched_at)
);
COMMENT ON TABLE raw_punish IS '證交所 處置股';
COMMENT ON COLUMN raw_punish.raw_key IS '查詢日期，例如 20261113（查當天起 14 天內處置中的）';
COMMENT ON COLUMN raw_punish.fetched_at IS '抓取時間（台灣時間）';
COMMENT ON COLUMN raw_punish.http_status IS 'HTTP 狀態碼';
COMMENT ON COLUMN raw_punish.ok IS '成功且內容檢查通過；transform 只讀 ok 的列';
COMMENT ON COLUMN raw_punish.problem IS '失敗原因；成功為 NULL';
COMMENT ON COLUMN raw_punish.url IS '請求網址（含查詢參數）';
COMMENT ON COLUMN raw_punish.request_body IS 'POST 的參數（JSON 字串）；GET 為 NULL';
COMMENT ON COLUMN raw_punish.body IS 'API 回應原文（JSON 字串；被擋時可能是 HTML）';
COMMENT ON COLUMN raw_punish.loaded_at IS '寫入時間（台灣時間）';

CREATE TABLE IF NOT EXISTS raw_punish_watchlist (
    raw_key      VARCHAR(30) NOT NULL,
    fetched_at   TIMESTAMP(6) NOT NULL,
    http_status  SMALLINT    NOT NULL,
    ok           BOOLEAN     NOT NULL,
    problem      TEXT,
    url          TEXT        NOT NULL,
    request_body TEXT,
    body         TEXT    NOT NULL,
    loaded_at    TIMESTAMP(0) NOT NULL DEFAULT LOCALTIMESTAMP(0),
    PRIMARY KEY (raw_key, fetched_at)
);
COMMENT ON TABLE raw_punish_watchlist IS '證交所 注意累計可能達處置標準名單';
COMMENT ON COLUMN raw_punish_watchlist.raw_key IS '名單上的資料日期，例如 20261113';
COMMENT ON COLUMN raw_punish_watchlist.fetched_at IS '抓取時間（台灣時間）';
COMMENT ON COLUMN raw_punish_watchlist.http_status IS 'HTTP 狀態碼';
COMMENT ON COLUMN raw_punish_watchlist.ok IS '成功且內容檢查通過；transform 只讀 ok 的列';
COMMENT ON COLUMN raw_punish_watchlist.problem IS '失敗原因；成功為 NULL';
COMMENT ON COLUMN raw_punish_watchlist.url IS '請求網址（含查詢參數）';
COMMENT ON COLUMN raw_punish_watchlist.request_body IS 'POST 的參數（JSON 字串）；GET 為 NULL';
COMMENT ON COLUMN raw_punish_watchlist.body IS 'API 回應原文（JSON 字串；被擋時可能是 HTML）';
COMMENT ON COLUMN raw_punish_watchlist.loaded_at IS '寫入時間（台灣時間）';

CREATE TABLE IF NOT EXISTS raw_market_daily (
    raw_key      VARCHAR(30) NOT NULL,
    fetched_at   TIMESTAMP(6) NOT NULL,
    http_status  SMALLINT    NOT NULL,
    ok           BOOLEAN     NOT NULL,
    problem      TEXT,
    url          TEXT        NOT NULL,
    request_body TEXT,
    body         TEXT    NOT NULL,
    loaded_at    TIMESTAMP(0) NOT NULL DEFAULT LOCALTIMESTAMP(0),
    PRIMARY KEY (raw_key, fetched_at)
);
COMMENT ON TABLE raw_market_daily IS '證交所 每日市場成交（一次一個月）';
COMMENT ON COLUMN raw_market_daily.raw_key IS '月份，例如 202611';
COMMENT ON COLUMN raw_market_daily.fetched_at IS '抓取時間（台灣時間）';
COMMENT ON COLUMN raw_market_daily.http_status IS 'HTTP 狀態碼';
COMMENT ON COLUMN raw_market_daily.ok IS '成功且內容檢查通過；transform 只讀 ok 的列';
COMMENT ON COLUMN raw_market_daily.problem IS '失敗原因；成功為 NULL';
COMMENT ON COLUMN raw_market_daily.url IS '請求網址（含查詢參數）';
COMMENT ON COLUMN raw_market_daily.request_body IS 'POST 的參數（JSON 字串）；GET 為 NULL';
COMMENT ON COLUMN raw_market_daily.body IS 'API 回應原文（JSON 字串；被擋時可能是 HTML）';
COMMENT ON COLUMN raw_market_daily.loaded_at IS '寫入時間（台灣時間）';

CREATE TABLE IF NOT EXISTS raw_stock_price (
    raw_key      VARCHAR(30) NOT NULL,
    fetched_at   TIMESTAMP(6) NOT NULL,
    http_status  SMALLINT    NOT NULL,
    ok           BOOLEAN     NOT NULL,
    problem      TEXT,
    url          TEXT        NOT NULL,
    request_body TEXT,
    body         TEXT    NOT NULL,
    loaded_at    TIMESTAMP(0) NOT NULL DEFAULT LOCALTIMESTAMP(0),
    PRIMARY KEY (raw_key, fetched_at)
);
COMMENT ON TABLE raw_stock_price IS '證交所 每日收盤行情（一次一天，全部上市股票、ETF，不含權證）';
COMMENT ON COLUMN raw_stock_price.raw_key IS '日期，例如 20261120';
COMMENT ON COLUMN raw_stock_price.fetched_at IS '抓取時間（台灣時間）';
COMMENT ON COLUMN raw_stock_price.http_status IS 'HTTP 狀態碼';
COMMENT ON COLUMN raw_stock_price.ok IS '成功且內容檢查通過；transform 只讀 ok 的列';
COMMENT ON COLUMN raw_stock_price.problem IS '失敗原因；成功為 NULL';
COMMENT ON COLUMN raw_stock_price.url IS '請求網址（含查詢參數）';
COMMENT ON COLUMN raw_stock_price.request_body IS 'POST 的參數（JSON 字串）；GET 為 NULL';
COMMENT ON COLUMN raw_stock_price.body IS 'API 回應原文（JSON 字串；被擋時可能是 HTML）';
COMMENT ON COLUMN raw_stock_price.loaded_at IS '寫入時間（台灣時間）';

CREATE TABLE IF NOT EXISTS raw_holiday (
    raw_key      VARCHAR(30) NOT NULL,
    fetched_at   TIMESTAMP(6) NOT NULL,
    http_status  SMALLINT    NOT NULL,
    ok           BOOLEAN     NOT NULL,
    problem      TEXT,
    url          TEXT        NOT NULL,
    request_body TEXT,
    body         TEXT    NOT NULL,
    loaded_at    TIMESTAMP(0) NOT NULL DEFAULT LOCALTIMESTAMP(0),
    PRIMARY KEY (raw_key, fetched_at)
);
COMMENT ON TABLE raw_holiday IS '證交所 開休市日期（一次一年）';
COMMENT ON COLUMN raw_holiday.raw_key IS '西元年，例如 2026';
COMMENT ON COLUMN raw_holiday.fetched_at IS '抓取時間（台灣時間）';
COMMENT ON COLUMN raw_holiday.http_status IS 'HTTP 狀態碼';
COMMENT ON COLUMN raw_holiday.ok IS '成功且內容檢查通過；transform 只讀 ok 的列';
COMMENT ON COLUMN raw_holiday.problem IS '失敗原因；成功為 NULL';
COMMENT ON COLUMN raw_holiday.url IS '請求網址（含查詢參數）';
COMMENT ON COLUMN raw_holiday.request_body IS 'POST 的參數（JSON 字串）；GET 為 NULL';
COMMENT ON COLUMN raw_holiday.body IS 'API 回應原文（JSON 字串；被擋時可能是 HTML）';
COMMENT ON COLUMN raw_holiday.loaded_at IS '寫入時間（台灣時間）';

CREATE TABLE IF NOT EXISTS raw_company (
    raw_key      VARCHAR(30) NOT NULL,
    fetched_at   TIMESTAMP(6) NOT NULL,
    http_status  SMALLINT    NOT NULL,
    ok           BOOLEAN     NOT NULL,
    problem      TEXT,
    url          TEXT        NOT NULL,
    request_body TEXT,
    body         TEXT    NOT NULL,
    loaded_at    TIMESTAMP(0) NOT NULL DEFAULT LOCALTIMESTAMP(0),
    PRIMARY KEY (raw_key, fetched_at)
);
COMMENT ON TABLE raw_company IS '證交所 OpenAPI 上市公司清單';
COMMENT ON COLUMN raw_company.raw_key IS '抓取日期，例如 20261113（每天一份快照）';
COMMENT ON COLUMN raw_company.fetched_at IS '抓取時間（台灣時間）';
COMMENT ON COLUMN raw_company.http_status IS 'HTTP 狀態碼';
COMMENT ON COLUMN raw_company.ok IS '成功且內容檢查通過；transform 只讀 ok 的列';
COMMENT ON COLUMN raw_company.problem IS '失敗原因；成功為 NULL';
COMMENT ON COLUMN raw_company.url IS '請求網址（含查詢參數）';
COMMENT ON COLUMN raw_company.request_body IS 'POST 的參數（JSON 字串）；GET 為 NULL';
COMMENT ON COLUMN raw_company.body IS 'API 回應原文（JSON 字串；被擋時可能是 HTML）';
COMMENT ON COLUMN raw_company.loaded_at IS '寫入時間（台灣時間）';

CREATE TABLE IF NOT EXISTS raw_announcement (
    raw_key      VARCHAR(30) NOT NULL,
    fetched_at   TIMESTAMP(6) NOT NULL,
    http_status  SMALLINT    NOT NULL,
    ok           BOOLEAN     NOT NULL,
    problem      TEXT,
    url          TEXT        NOT NULL,
    request_body TEXT,
    body         TEXT    NOT NULL,
    loaded_at    TIMESTAMP(0) NOT NULL DEFAULT LOCALTIMESTAMP(0),
    PRIMARY KEY (raw_key, fetched_at)
);
COMMENT ON TABLE raw_announcement IS '觀測站 重大訊息（一家公司一個月）';
COMMENT ON COLUMN raw_announcement.raw_key IS '公司代號_月份，例如 1303_202611';
COMMENT ON COLUMN raw_announcement.fetched_at IS '抓取時間（台灣時間）';
COMMENT ON COLUMN raw_announcement.http_status IS 'HTTP 狀態碼';
COMMENT ON COLUMN raw_announcement.ok IS '成功且內容檢查通過；transform 只讀 ok 的列';
COMMENT ON COLUMN raw_announcement.problem IS '失敗原因；成功為 NULL';
COMMENT ON COLUMN raw_announcement.url IS '請求網址（含查詢參數）';
COMMENT ON COLUMN raw_announcement.request_body IS 'POST 的參數（JSON 字串）；GET 為 NULL';
COMMENT ON COLUMN raw_announcement.body IS 'API 回應原文（JSON 字串；被擋時可能是 HTML）';
COMMENT ON COLUMN raw_announcement.loaded_at IS '寫入時間（台灣時間）';

CREATE TABLE IF NOT EXISTS raw_announcement_detail (
    raw_key      VARCHAR(30) NOT NULL,
    fetched_at   TIMESTAMP(6) NOT NULL,
    http_status  SMALLINT    NOT NULL,
    ok           BOOLEAN     NOT NULL,
    problem      TEXT,
    url          TEXT        NOT NULL,
    request_body TEXT,
    body         TEXT    NOT NULL,
    loaded_at    TIMESTAMP(0) NOT NULL DEFAULT LOCALTIMESTAMP(0),
    PRIMARY KEY (raw_key, fetched_at)
);
COMMENT ON TABLE raw_announcement_detail IS '觀測站 重大訊息全文（一則公告）';
COMMENT ON COLUMN raw_announcement_detail.raw_key IS '公司代號_輸入日期_序號，例如 9908_20261102_1';
COMMENT ON COLUMN raw_announcement_detail.fetched_at IS '抓取時間（台灣時間）';
COMMENT ON COLUMN raw_announcement_detail.http_status IS 'HTTP 狀態碼';
COMMENT ON COLUMN raw_announcement_detail.ok IS '成功且內容檢查通過；transform 只讀 ok 的列';
COMMENT ON COLUMN raw_announcement_detail.problem IS '失敗原因；成功為 NULL';
COMMENT ON COLUMN raw_announcement_detail.url IS '請求網址（含查詢參數）';
COMMENT ON COLUMN raw_announcement_detail.request_body IS 'POST 的參數（JSON 字串）；GET 為 NULL';
COMMENT ON COLUMN raw_announcement_detail.body IS 'API 回應原文（JSON 字串；被擋時可能是 HTML）';
COMMENT ON COLUMN raw_announcement_detail.loaded_at IS '寫入時間（台灣時間）';

CREATE TABLE IF NOT EXISTS raw_stock_news (
    raw_key      VARCHAR(30) NOT NULL,
    fetched_at   TIMESTAMP(6) NOT NULL,
    http_status  SMALLINT    NOT NULL,
    ok           BOOLEAN     NOT NULL,
    problem      TEXT,
    url          TEXT        NOT NULL,
    request_body TEXT,
    body         TEXT    NOT NULL,
    loaded_at    TIMESTAMP(0) NOT NULL DEFAULT LOCALTIMESTAMP(0),
    PRIMARY KEY (raw_key, fetched_at)
);
COMMENT ON TABLE raw_stock_news IS 'FinMind 個股新聞 TaiwanStockNews（一檔一天；只有標題、連結、來源、時間，沒有內文）';
COMMENT ON COLUMN raw_stock_news.raw_key IS '證券代號_日期，例如 2330_20261120';
COMMENT ON COLUMN raw_stock_news.fetched_at IS '抓取時間（台灣時間）';
COMMENT ON COLUMN raw_stock_news.http_status IS 'HTTP 狀態碼';
COMMENT ON COLUMN raw_stock_news.ok IS '成功且內容檢查通過；transform 只讀 ok 的列';
COMMENT ON COLUMN raw_stock_news.problem IS '失敗原因；成功為 NULL';
COMMENT ON COLUMN raw_stock_news.url IS '請求網址（含查詢參數；token 放在 header，不會出現在這裡）';
COMMENT ON COLUMN raw_stock_news.request_body IS 'POST 的參數（JSON 字串）；GET 為 NULL';
COMMENT ON COLUMN raw_stock_news.body IS 'API 回應原文（JSON 字串）';
COMMENT ON COLUMN raw_stock_news.loaded_at IS '寫入時間（台灣時間）';

-- 新聞網頁（crawl_news_detail）：不是整份回應原樣存——<body> 就是新聞全文，不存進資料庫（著作權）；
-- body 是 crawl_news_detail 組的 JSON：<head>（標題、meta 摘要）、轉址後的網址、Content-Type、全文有幾個字
CREATE TABLE IF NOT EXISTS raw_news_detail (
    raw_key      VARCHAR(32) NOT NULL,
    fetched_at   TIMESTAMP(6) NOT NULL,
    http_status  SMALLINT,
    ok           BOOLEAN     NOT NULL,
    problem      TEXT,
    url          TEXT        NOT NULL,
    request_body TEXT,
    body         TEXT    NOT NULL,
    loaded_at    TIMESTAMP(0) NOT NULL DEFAULT LOCALTIMESTAMP(0),
    PRIMARY KEY (raw_key, fetched_at)
);
COMMENT ON TABLE raw_news_detail IS '新聞網頁（資料來源：新聞網站本身，打開每則新聞的連結；只存 <head>，全文不存）';
COMMENT ON COLUMN raw_news_detail.raw_key IS 'news_id：md5(link)，和 stage_news.news_id 一樣';
COMMENT ON COLUMN raw_news_detail.fetched_at IS '抓取時間（台灣時間）';
COMMENT ON COLUMN raw_news_detail.http_status IS 'HTTP 狀態碼；沒打（轉址連結）為 NULL';
COMMENT ON COLUMN raw_news_detail.ok IS '成功且內容檢查通過；transform 只讀 ok 的列';
COMMENT ON COLUMN raw_news_detail.problem IS '失敗原因（HTTP 403、不是網頁、轉址連結）；成功為 NULL。5xx、429 下次再抓，其他的不再抓';
COMMENT ON COLUMN raw_news_detail.url IS '新聞連結';
COMMENT ON COLUMN raw_news_detail.request_body IS '都是 GET，為 NULL';
COMMENT ON COLUMN raw_news_detail.body IS 'JSON：head（<head> 原文）、final_url、content_type、text_chars（全文有幾個字，全文本身不存）';
COMMENT ON COLUMN raw_news_detail.loaded_at IS '寫入時間（台灣時間）';
