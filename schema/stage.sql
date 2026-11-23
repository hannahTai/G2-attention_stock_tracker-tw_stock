-- stage 層（表名開頭 stage_）：raw 解析後的乾淨資料，一列 = 官方公告的一個事實，不做業務判斷
-- 由 common.repository.create_tables() 執行；已存在就跳過

CREATE TABLE IF NOT EXISTS stage_notice (
    trade_date        DATE           NOT NULL,
    stock_id          VARCHAR(10)    NOT NULL,
    stock_name        VARCHAR(50)    NOT NULL,
    reason_text       TEXT           NOT NULL,
    close_price       DECIMAL(12, 4),
    pe_ratio          DECIMAL(12, 4),
    loaded_at         TIMESTAMP(0) NOT NULL DEFAULT LOCALTIMESTAMP(0),
    source_fetched_at TIMESTAMP(6),
    PRIMARY KEY (trade_date, stock_id)
);
COMMENT ON TABLE stage_notice IS '注意股（每日）';
CREATE INDEX IF NOT EXISTS idx_stock_trade_date ON stage_notice (stock_id, trade_date);   -- 用代號查某檔的紀錄（app_card 找最近一次的名稱）；主鍵是日期在前，用代號查不到
COMMENT ON COLUMN stage_notice.trade_date IS '交易日';
COMMENT ON COLUMN stage_notice.stock_id IS '證券代號（含權證、ETF、DR）';
COMMENT ON COLUMN stage_notice.stock_name IS '證券名稱';
COMMENT ON COLUMN stage_notice.reason_text IS '注意交易資訊全文（已去 HTML）';
COMMENT ON COLUMN stage_notice.close_price IS '收盤價';
COMMENT ON COLUMN stage_notice.pe_ratio IS '本益比；無本益比為 NULL';
COMMENT ON COLUMN stage_notice.loaded_at IS '寫入時間（台灣時間）';
COMMENT ON COLUMN stage_notice.source_fetched_at IS '來自哪次抓取（raw 的 fetched_at）；寫入時只讓比較新的覆蓋';

CREATE TABLE IF NOT EXISTS stage_notice_day (
    query_date        DATE        NOT NULL,
    notice_count      INT         NOT NULL,
    loaded_at         TIMESTAMP(0) NOT NULL DEFAULT LOCALTIMESTAMP(0),
    source_fetched_at TIMESTAMP(6),
    PRIMARY KEY (query_date)
);
COMMENT ON TABLE stage_notice_day IS '注意股：每個查詢日一列（分辨「沒人被列」和「還沒抓到」）';
COMMENT ON COLUMN stage_notice_day.query_date IS '查詢日期（有這列＝那天的注意股查過也整理好了；休市日也會查、也有一列）';
COMMENT ON COLUMN stage_notice_day.notice_count IS '那天幾檔被列注意；0＝沒人被列（休市日一定是 0）';
COMMENT ON COLUMN stage_notice_day.loaded_at IS '寫入時間（台灣時間）';
COMMENT ON COLUMN stage_notice_day.source_fetched_at IS '來自哪次抓取（raw 的 fetched_at）；寫入時只讓比較新的覆蓋';

CREATE TABLE IF NOT EXISTS stage_notice_clause (
    trade_date        DATE        NOT NULL,
    stock_id          VARCHAR(10) NOT NULL,
    clause            SMALLINT     NOT NULL,
    loaded_at         TIMESTAMP(0) NOT NULL DEFAULT LOCALTIMESTAMP(0),
    source_fetched_at TIMESTAMP(6),
    PRIMARY KEY (trade_date, stock_id, clause)
);
COMMENT ON TABLE stage_notice_clause IS '注意股觸發的款次';
COMMENT ON COLUMN stage_notice_clause.clause IS '款次 1～14';
COMMENT ON COLUMN stage_notice_clause.loaded_at IS '寫入時間（台灣時間）';
COMMENT ON COLUMN stage_notice_clause.source_fetched_at IS '來自哪次抓取（raw 的 fetched_at）；寫入時只讓比較新的覆蓋';

CREATE TABLE IF NOT EXISTS stage_punish (
    announce_date          DATE        NOT NULL,
    stock_id               VARCHAR(10) NOT NULL,
    stock_name             VARCHAR(50) NOT NULL,
    trigger_condition      VARCHAR(50) NOT NULL,
    period_start           DATE        NOT NULL,
    period_end             DATE        NOT NULL,
    measure_type           VARCHAR(20) NOT NULL,
    match_interval_minutes SMALLINT,
    content                TEXT        NOT NULL,
    has_star_mark          BOOLEAN     NOT NULL,
    loaded_at              TIMESTAMP(0) NOT NULL DEFAULT LOCALTIMESTAMP(0),
    source_fetched_at      TIMESTAMP(6),
    PRIMARY KEY (announce_date, stock_id)
);
COMMENT ON TABLE stage_punish IS '處置股公告';
COMMENT ON COLUMN stage_punish.announce_date IS '公布日期';
COMMENT ON COLUMN stage_punish.stock_id IS '證券代號';
COMMENT ON COLUMN stage_punish.stock_name IS '證券名稱';
COMMENT ON COLUMN stage_punish.trigger_condition IS '處置條件，例如 連續三次（不用 condition：SQL 保留字）';
COMMENT ON COLUMN stage_punish.period_start IS '處置起日';
COMMENT ON COLUMN stage_punish.period_end IS '處置迄日';
COMMENT ON COLUMN stage_punish.measure_type IS '處置措施，例如 第一次處置';
COMMENT ON COLUMN stage_punish.match_interval_minutes IS '撮合間隔（分鐘）；內容沒寫為 NULL';
COMMENT ON COLUMN stage_punish.content IS '處置內容全文';
COMMENT ON COLUMN stage_punish.has_star_mark IS '公布日期帶 * 號（意義未確認）';
COMMENT ON COLUMN stage_punish.loaded_at IS '寫入時間（台灣時間）';
COMMENT ON COLUMN stage_punish.source_fetched_at IS '來自哪次抓取（raw 的 fetched_at）；寫入時只讓比較新的覆蓋';

CREATE TABLE IF NOT EXISTS stage_punish_day (
    query_date        DATE        NOT NULL,
    punish_count      INT         NOT NULL,
    loaded_at         TIMESTAMP(0) NOT NULL DEFAULT LOCALTIMESTAMP(0),
    source_fetched_at TIMESTAMP(6),
    PRIMARY KEY (query_date)
);
COMMENT ON TABLE stage_punish_day IS '處置股：每天一列（分辨「沒有處置」和「還沒抓到」）';
COMMENT ON COLUMN stage_punish_day.query_date IS '查詢日期（有這列＝那天的處置股抓到也整理好了）';
COMMENT ON COLUMN stage_punish_day.punish_count IS '查到幾則處置公告（這天起 14 天內處置中的）；0＝沒有';
COMMENT ON COLUMN stage_punish_day.loaded_at IS '寫入時間（台灣時間）';
COMMENT ON COLUMN stage_punish_day.source_fetched_at IS '來自哪次抓取（raw 的 fetched_at）；寫入時只讓比較新的覆蓋';

CREATE TABLE IF NOT EXISTS stage_punish_watchlist (
    data_date         DATE        NOT NULL,
    stock_id          VARCHAR(10) NOT NULL,
    stock_name        VARCHAR(50) NOT NULL,
    progress_text     TEXT        NOT NULL,
    loaded_at         TIMESTAMP(0) NOT NULL DEFAULT LOCALTIMESTAMP(0),
    source_fetched_at TIMESTAMP(6),
    PRIMARY KEY (data_date, stock_id)
);
COMMENT ON TABLE stage_punish_watchlist IS '注意累計可能達處置標準名單（官方答案）';
COMMENT ON COLUMN stage_punish_watchlist.data_date IS '名單的資料日期';
COMMENT ON COLUMN stage_punish_watchlist.progress_text IS '近期達標準之情形；多行以換行分隔';
COMMENT ON COLUMN stage_punish_watchlist.loaded_at IS '寫入時間（台灣時間）';
COMMENT ON COLUMN stage_punish_watchlist.source_fetched_at IS '來自哪次抓取（raw 的 fetched_at）；寫入時只讓比較新的覆蓋';

CREATE TABLE IF NOT EXISTS stage_trading_day (
    trade_date          DATE           NOT NULL,
    is_trading          BOOLEAN        NOT NULL,
    volume_shares       BIGINT,
    amount_twd          BIGINT,
    trade_count         BIGINT,
    taiex_index         DECIMAL(12, 2),
    taiex_change_points DECIMAL(12, 2),
    loaded_at           TIMESTAMP(0) NOT NULL DEFAULT LOCALTIMESTAMP(0),
    source_fetched_at   TIMESTAMP(6),
    PRIMARY KEY (trade_date)
);
COMMENT ON TABLE stage_trading_day IS '交易日曆：每天一列，以實際成交為準';
COMMENT ON COLUMN stage_trading_day.trade_date IS '日期：市場成交涵蓋到的每一天（沒有這列＝還沒抓到）';
COMMENT ON COLUMN stage_trading_day.is_trading IS '1＝有交易；0＝沒交易（週末、休市、颱風假）';
COMMENT ON COLUMN stage_trading_day.volume_shares IS '成交股數；沒交易的日子為 NULL，下同';
COMMENT ON COLUMN stage_trading_day.amount_twd IS '成交金額（元）';
COMMENT ON COLUMN stage_trading_day.trade_count IS '成交筆數';
COMMENT ON COLUMN stage_trading_day.taiex_index IS '發行量加權股價指數';
COMMENT ON COLUMN stage_trading_day.taiex_change_points IS '漲跌點數';
COMMENT ON COLUMN stage_trading_day.loaded_at IS '寫入時間（台灣時間）';
COMMENT ON COLUMN stage_trading_day.source_fetched_at IS '來自哪次抓取（raw 的 fetched_at）；寫入時只讓比較新的覆蓋';

CREATE TABLE IF NOT EXISTS stage_stock_price (
    trade_date        DATE           NOT NULL,
    stock_id          VARCHAR(10)    NOT NULL,
    stock_name        VARCHAR(50),
    open_price        DECIMAL(10, 2),
    high_price        DECIMAL(10, 2),
    low_price         DECIMAL(10, 2),
    close_price       DECIMAL(10, 2),
    price_change      DECIMAL(10, 2),
    volume_shares     BIGINT,
    loaded_at         TIMESTAMP(0) NOT NULL DEFAULT LOCALTIMESTAMP(0),
    source_fetched_at TIMESTAMP(6),
    PRIMARY KEY (trade_date, stock_id)
);
COMMENT ON TABLE stage_stock_price IS '每日收盤行情：一天一檔一列（那天沒成交的不放）';
COMMENT ON COLUMN stage_stock_price.trade_date IS '交易日';
COMMENT ON COLUMN stage_stock_price.stock_id IS '證券代號';
COMMENT ON COLUMN stage_stock_price.stock_name IS '證券名稱';
COMMENT ON COLUMN stage_stock_price.open_price IS '開盤價';
COMMENT ON COLUMN stage_stock_price.high_price IS '最高價';
COMMENT ON COLUMN stage_stock_price.low_price IS '最低價';
COMMENT ON COLUMN stage_stock_price.close_price IS '收盤價';
COMMENT ON COLUMN stage_stock_price.price_change IS '漲跌（和參考價比，除權息日已經調整；正的漲、負的跌、0 平盤）；不比價的為 NULL';
COMMENT ON COLUMN stage_stock_price.volume_shares IS '成交股數';
COMMENT ON COLUMN stage_stock_price.loaded_at IS '寫入時間（台灣時間）';
COMMENT ON COLUMN stage_stock_price.source_fetched_at IS '來自哪次抓取（raw 的 fetched_at）；寫入時只讓比較新的覆蓋';

CREATE TABLE IF NOT EXISTS stage_holiday (
    holiday_date      DATE         NOT NULL,
    holiday_name      VARCHAR(100) NOT NULL,
    description       TEXT,
    loaded_at         TIMESTAMP(0) NOT NULL DEFAULT LOCALTIMESTAMP(0),
    source_fetched_at TIMESTAMP(6),
    PRIMARY KEY (holiday_date)
);
COMMENT ON TABLE stage_holiday IS '官方開休市日期（不含臨時休市）';
COMMENT ON COLUMN stage_holiday.loaded_at IS '寫入時間（台灣時間）';
COMMENT ON COLUMN stage_holiday.source_fetched_at IS '來自哪次抓取（raw 的 fetched_at）；寫入時只讓比較新的覆蓋';

CREATE TABLE IF NOT EXISTS stage_company (
    company_id        VARCHAR(10)  NOT NULL,
    short_name        VARCHAR(50)  NOT NULL,
    full_name         VARCHAR(200) NOT NULL,
    industry_code     VARCHAR(10)  NOT NULL,
    listed_date       DATE,
    snapshot_date     DATE         NOT NULL,
    loaded_at         TIMESTAMP(0) NOT NULL DEFAULT LOCALTIMESTAMP(0),
    source_fetched_at TIMESTAMP(6),
    PRIMARY KEY (company_id)
);
COMMENT ON TABLE stage_company IS '上市公司（依最新快照更新；下市的留著）';
COMMENT ON COLUMN stage_company.full_name IS '公司全名';
COMMENT ON COLUMN stage_company.industry_code IS '產業別代碼';
COMMENT ON COLUMN stage_company.listed_date IS '上市日期';
COMMENT ON COLUMN stage_company.snapshot_date IS '取自哪一天的快照';
COMMENT ON COLUMN stage_company.loaded_at IS '寫入時間（台灣時間）';
COMMENT ON COLUMN stage_company.source_fetched_at IS '來自哪次抓取（raw 的 fetched_at）；寫入時只讓比較新的覆蓋';

CREATE TABLE IF NOT EXISTS stage_announcement (
    company_id        VARCHAR(10) NOT NULL,
    announce_date     DATE        NOT NULL,
    enter_date        DATE        NOT NULL,
    serial_number     INT         NOT NULL,
    announcement_id   CHAR(32)    NOT NULL,
    announce_time     TIME        NOT NULL,
    company_name      VARCHAR(50) NOT NULL,
    subject           TEXT        NOT NULL,
    loaded_at         TIMESTAMP(0) NOT NULL DEFAULT LOCALTIMESTAMP(0),
    source_fetched_at TIMESTAMP(6),
    PRIMARY KEY (company_id, enter_date, serial_number),
    UNIQUE (announcement_id),
    CONSTRAINT stage_announcement_id_is_md5
        CHECK (announcement_id = md5(company_id || '_' || to_char(enter_date, 'YYYYMMDD') || '_' || serial_number))
);
COMMENT ON TABLE stage_announcement IS '公司重大訊息';
COMMENT ON COLUMN stage_announcement.announce_date IS '發言日期';
COMMENT ON COLUMN stage_announcement.enter_date IS '輸入日期（序號依這天編，可能早於發言日期）';
COMMENT ON COLUMN stage_announcement.serial_number IS '輸入日期當天的序號';
COMMENT ON COLUMN stage_announcement.announcement_id IS '公告代號：主鍵三欄合成一欄，md5(公司_輸入日期_序號)，例如 md5(''9908_20261102_1'')；重建也不變，其他表、API 用它對應';
COMMENT ON COLUMN stage_announcement.announce_time IS '發言時間（判斷收盤前／後用）';
COMMENT ON COLUMN stage_announcement.subject IS '主旨';
COMMENT ON COLUMN stage_announcement.loaded_at IS '寫入時間（台灣時間）';
COMMENT ON COLUMN stage_announcement.source_fetched_at IS '來自哪次抓取（raw 的 fetched_at）；寫入時只讓比較新的覆蓋';

CREATE TABLE IF NOT EXISTS stage_announcement_detail (
    company_id        VARCHAR(10) NOT NULL,
    enter_date        DATE        NOT NULL,
    serial_number     INT         NOT NULL,
    announcement_id   CHAR(32)    NOT NULL,
    announce_clause   VARCHAR(20) NOT NULL,
    fact_date         DATE,
    content           TEXT  NOT NULL,
    loaded_at         TIMESTAMP(0) NOT NULL DEFAULT LOCALTIMESTAMP(0),
    source_fetched_at TIMESTAMP(6),
    PRIMARY KEY (company_id, enter_date, serial_number),
    UNIQUE (announcement_id),
    CONSTRAINT stage_announcement_detail_id_is_md5
        CHECK (announcement_id = md5(company_id || '_' || to_char(enter_date, 'YYYYMMDD') || '_' || serial_number))
);
COMMENT ON TABLE stage_announcement_detail IS '公司重大訊息全文';
COMMENT ON COLUMN stage_announcement_detail.enter_date IS '輸入日期（和 stage_announcement 同一個主鍵）';
COMMENT ON COLUMN stage_announcement_detail.serial_number IS '輸入日期當天的序號';
COMMENT ON COLUMN stage_announcement_detail.announcement_id IS '公告代號（和 stage_announcement.announcement_id 一樣）';
COMMENT ON COLUMN stage_announcement_detail.announce_clause IS '符合條款，例如 第31款（觀測站的公告分類；和注意股的款次 stage_notice_clause.clause 是不同東西）';
COMMENT ON COLUMN stage_announcement_detail.fact_date IS '事實發生日';
COMMENT ON COLUMN stage_announcement_detail.content IS '說明全文（不含發言人姓名、電話）';
COMMENT ON COLUMN stage_announcement_detail.loaded_at IS '寫入時間（台灣時間）';
COMMENT ON COLUMN stage_announcement_detail.source_fetched_at IS '來自哪次抓取（raw 的 fetched_at）；寫入時只讓比較新的覆蓋';

-- 重大訊息的「符合條款」對照表（固定資料，不用爬；條文修正時改這裡，建表時會一併更新）
-- 來源：臺灣證券交易所對有價證券上市公司重大訊息之查證暨公開處理程序 第 4 條第 1 項（115.09.30 版）
--       twse-regulation.twse.com.tw/m/LawContent.aspx?FID=FL007111
-- short_name、description 是整理後的摘要，不是條文原文
CREATE TABLE IF NOT EXISTS stage_announcement_clause (
    announce_clause VARCHAR(20) NOT NULL,
    clause_no   SMALLINT      NOT NULL,
    short_name  VARCHAR(20)  NOT NULL,
    description VARCHAR(200) NOT NULL,
    loaded_at   TIMESTAMP(0) NOT NULL DEFAULT LOCALTIMESTAMP(0),
    PRIMARY KEY (announce_clause)
);
COMMENT ON TABLE stage_announcement_clause IS '重大訊息符合條款對照表（固定資料）';
COMMENT ON COLUMN stage_announcement_clause.announce_clause IS '符合條款，例如 第31款（和 stage_announcement_detail.announce_clause 一樣）';
COMMENT ON COLUMN stage_announcement_clause.clause_no IS '款次數字，排序用';
COMMENT ON COLUMN stage_announcement_clause.short_name IS '簡短名稱（卡片上的標籤），例如 財務報告';
COMMENT ON COLUMN stage_announcement_clause.description IS '條文摘要';
COMMENT ON COLUMN stage_announcement_clause.loaded_at IS '寫入時間（台灣時間）';

INSERT INTO stage_announcement_clause (announce_clause, clause_no, short_name, description) VALUES
    ('第1款', 1, '退票、信用異常', '公司、負責人、母子公司存款不足退票、拒絕往來或其他喪失債信'),
    ('第2款', 2, '訴訟、行政處分', '公司或負責人涉及訴訟、非訟、行政處分、行政爭訟，對財務業務有重大影響'),
    ('第3款', 3, '減產、停工', '嚴重減產、全部或部分停工，廠房或主要設備出租、質押'),
    ('第4款', 4, '重大營業讓與', '有公司法第 185 條第 1 項各款情事（出租全部營業、讓與主要營業或財產等）'),
    ('第5款', 5, '重整、破產', '公司、母公司或子公司進行重整或破產程序'),
    ('第6款', 6, '董事長、總經理、董事異動', '董事長、總經理、法人董監事或其代表人、獨立董事異動'),
    ('第7款', 7, '更換會計師', '非會計師事務所內部調整的更換會計師，或契約期間內更換承銷商'),
    ('第8款', 8, '發言人、主管異動', '發言人、代理發言人、重要營運主管異動'),
    ('第9款', 9, '會計年度、會計變更', '變更會計年度、會計政策或會計估計'),
    ('第10款', 10, '重要契約、合作', '簽訂、變更、終止重要契約、備忘錄、策略聯盟或業務合作計畫'),
    ('第11款', 11, '減資、合併、增資', '董事會決議減資、合併、分割、收購、股份交換、增資發行新股等'),
    ('第12款', 12, '法人說明會', '召開或受邀參加法人說明會，及說明會的財務業務資訊'),
    ('第13款', 13, '財務預測', '董事會決議公開財務預測，或財務預測不適用、更正'),
    ('第14款', 14, '股利', '董事會決議發放或不發放股利，或股利分派有變動'),
    ('第15款', 15, '投資計畫', '董事會或股東會決議投資計畫達實收資本額一定比例'),
    ('第16款', 16, '增資、公司債計畫變更', '現金增資或募集公司債計畫生效後有變更'),
    ('第17款', 17, '股東會召開', '股東常會或臨時會的召開日期、方式、議程'),
    ('第18款', 18, '股東會決議', '股東常會或臨時會的重要決議事項'),
    ('第19款', 19, '舞弊、搜索、羈押', '內部控制舞弊、非常規交易、掏空資產，或遭檢調搜索、負責人遭羈押'),
    ('第20款', 20, '取得或處分資產', '取得或處分資產達應公告申報標準，或衍生性商品損失達一定比例'),
    ('第21款', 21, '競業許可', '董事會或股東會決議許可經理人或董事從事競業行為'),
    ('第22款', 22, '背書保證', '依資金貸與及背書保證處理準則第 25 條應公告的背書保證'),
    ('第23款', 23, '資金貸與', '依資金貸與及背書保證處理準則第 22 條應公告的資金貸與'),
    ('第24款', 24, '私募有價證券', '公司或子公司取得或處分私募有價證券'),
    ('第25款', 25, '主要客戶供應商停止往來', '與銷售或進貨達一成以上的主要買主或供應商停止業務往來'),
    ('第26款', 26, '災難、罷工、資安事件', '發生災難、集體抗議、罷工、環境污染、資通安全事件等'),
    ('第27款', 27, '債權銀行協商', '與債權銀行召開協商會議，協商結果確定'),
    ('第28款', 28, '關係人退票', '關係人、主要債務人或其連帶保證人遭退票或喪失債信'),
    ('第29款', 29, '內控聲明、會計師報告', '內部控制制度聲明書變更重新申報，或會計師專案審查報告'),
    ('第30款', 30, '財報延誤或錯誤', '未依期限公告財務報告、財務報告錯誤或疏漏、會計師非無保留意見'),
    ('第31款', 31, '財務報告', '董事會通過財務報告，或公告年度自結財務資訊'),
    ('第32款', 32, '集保比率不足', '股票集中保管期間內，集保比率低於規定'),
    ('第33款', 33, '股權變動', '有公司法第 369 條之 8 所定股權變動並收到通知'),
    ('第34款', 34, '董監停止職權', '董事或監察人遭假處分停止行使職權或緊急處置'),
    ('第35款', 35, '買回庫藏股', '依上市上櫃公司買回本公司股份辦法應公告申報'),
    ('第36款', 36, '減資、面額變更', '減資或每股面額變更：資本變更登記完成、換發股票等'),
    ('第37款', 37, '無法履行上市承諾', '申請上市時出具承諾書，之後無法履行'),
    ('第38款', 38, '公開收購', '依公開收購管理辦法應公告申報'),
    ('第39款', 39, '（已刪除）', '本款刪除'),
    ('第40款', 40, '暫停、恢復交易', '經證交所公告暫停或恢復交易'),
    ('第41款', 41, '控股家數變動', '投資控股公司持有的被控股公司家數增減'),
    ('第42款', 42, '終止上市', '董事會或股東會決議申請終止上市、轉上櫃等，或有重大變更'),
    ('第43款', 43, '捐贈', '對關係人捐贈，或對非關係人的重大捐贈'),
    ('第44款', 44, '委員會、獨董異議', '審計委員會、薪資報酬委員會成員或獨立董事有反對或保留意見'),
    ('第45款', 45, '董監放棄認購增資', '全體董監事放棄認購現金增資達一半以上，並洽特定人認購'),
    ('第46款', 46, '子公司下市、營收為零', '子公司終止上市達一定標準，或單月營收為零或負數'),
    ('第47款', 47, '海外財報差異', '境外發行有價證券，海外申報的財務資訊與國內的差異'),
    ('第48款', 48, '營業細則第53條之25', '有證交所營業細則第 53 條之 25 的情事'),
    ('第49款', 49, '喪失子公司控制', '對重要子公司喪失控制力，或持股大幅減少'),
    ('第50款', 50, '營業細則第48條之3', '依證交所營業細則第 48 條之 3 召開董事會或股東會，或子公司海外上市'),
    ('第51款', 51, '其他重大事項', '其他董事會決議的重大決策，或對股東權益、證券價格有重大影響的事（概括條款）')
ON CONFLICT (announce_clause) DO UPDATE SET clause_no = EXCLUDED.clause_no, short_name = EXCLUDED.short_name,
    description = EXCLUDED.description, loaded_at = LOCALTIMESTAMP(0);

-- 新聞（資料來源：FinMind TaiwanStockNews 的新聞清單）：一則一列；好幾檔都有的新聞也只有一列
CREATE TABLE IF NOT EXISTS stage_news (
    news_id           CHAR(32)     NOT NULL,
    link              TEXT         NOT NULL,
    news_time         TIMESTAMP(0) NOT NULL,
    news_date         DATE         NOT NULL,
    publisher         VARCHAR(50),
    title             TEXT         NOT NULL,
    loaded_at         TIMESTAMP(0) NOT NULL DEFAULT LOCALTIMESTAMP(0),
    source_fetched_at TIMESTAMP(6),
    PRIMARY KEY (news_id),
    CONSTRAINT stage_news_id_is_md5 CHECK (news_id = md5(link))   -- transform 用 Python 算，這裡確認和資料庫算的一樣
);
CREATE INDEX IF NOT EXISTS idx_stage_news_date ON stage_news (news_date);
COMMENT ON TABLE stage_news IS '新聞（資料來源：FinMind 新聞清單）：標題、連結、媒體、時間，不存內文';
COMMENT ON COLUMN stage_news.news_id IS '新聞代號：md5(link)，連結太長（最長好幾百字）不好當主鍵';
COMMENT ON COLUMN stage_news.link IS '新聞連結（Google 新聞的轉址連結可能好幾百字）';
COMMENT ON COLUMN stage_news.news_time IS '新聞時間（台灣時間；FinMind 給的是 UTC，整理時轉成台灣時間）';
COMMENT ON COLUMN stage_news.news_date IS '新聞日期（台灣時間）';
COMMENT ON COLUMN stage_news.publisher IS '發布的媒體，例如 自由時報、CMoney（FinMind 的 source 欄）';
COMMENT ON COLUMN stage_news.title IS '標題（去掉結尾的「 - 媒體」）';
COMMENT ON COLUMN stage_news.loaded_at IS '寫入時間（台灣時間）';
COMMENT ON COLUMN stage_news.source_fetched_at IS '來自哪次抓取（raw 的 fetched_at）；寫入時只讓比較新的覆蓋';

-- 哪檔有哪則新聞（資料來源：FinMind；一檔一檔查，同一則新聞出現在好幾檔就好幾列）
CREATE TABLE IF NOT EXISTS stage_stock_news (
    stock_id          VARCHAR(10)  NOT NULL,
    news_id           CHAR(32)     NOT NULL,
    loaded_at         TIMESTAMP(0) NOT NULL DEFAULT LOCALTIMESTAMP(0),
    source_fetched_at TIMESTAMP(6),
    PRIMARY KEY (stock_id, news_id)
);
COMMENT ON TABLE stage_stock_news IS '哪檔有哪則新聞（資料來源：FinMind；新聞本身在 stage_news）';
COMMENT ON COLUMN stage_stock_news.stock_id IS '證券代號';
COMMENT ON COLUMN stage_stock_news.news_id IS '新聞代號（stage_news.news_id）';
COMMENT ON COLUMN stage_stock_news.loaded_at IS '寫入時間（台灣時間）';
COMMENT ON COLUMN stage_stock_news.source_fetched_at IS '來自哪次抓取（raw 的 fetched_at）；寫入時只讓比較新的覆蓋';

-- 新聞網頁（資料來源：新聞網站本身，打開每則新聞的連結；raw_news_detail 只存 <head>）：導言、全文有幾個字
-- 抓不到的（被擋、轉址）沒有列，原因在 raw_news_detail.problem
CREATE TABLE IF NOT EXISTS stage_news_detail (
    news_id           CHAR(32)     NOT NULL,
    final_url         TEXT,
    lead              TEXT,
    text_chars        INT,
    loaded_at         TIMESTAMP(0) NOT NULL DEFAULT LOCALTIMESTAMP(0),
    source_fetched_at TIMESTAMP(6),
    PRIMARY KEY (news_id)
);
COMMENT ON TABLE stage_news_detail IS '新聞網頁（資料來源：新聞網站本身）：導言（meta 摘要）、全文有幾個字（全文不存）';
COMMENT ON COLUMN stage_news_detail.news_id IS '新聞代號（stage_news.news_id）';
COMMENT ON COLUMN stage_news_detail.final_url IS '轉址後實際打開的網址';
COMMENT ON COLUMN stage_news_detail.lead IS '導言：網頁 <head> 的 meta 摘要（og:description、description），新聞網站自己寫的一兩句；有些網站每則都放同一句網站介紹，app_stock_news 會排除';
COMMENT ON COLUMN stage_news_detail.text_chars IS '抽出來的全文有幾個字（全文本身不存）；抽不到為 NULL';
COMMENT ON COLUMN stage_news_detail.loaded_at IS '寫入時間（台灣時間）';
COMMENT ON COLUMN stage_news_detail.source_fetched_at IS '來自哪次抓取（raw 的 fetched_at）；寫入時只讓比較新的覆蓋';
