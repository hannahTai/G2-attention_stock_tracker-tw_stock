-- stage 層（表名開頭 stage_）：raw 解析後的乾淨資料，一列 = 官方公告的一個事實，不做業務判斷
-- 由 common.repository.create_tables() 執行；已存在就跳過

CREATE TABLE IF NOT EXISTS stage_notice (
    trade_date        DATE           NOT NULL COMMENT '交易日',
    stock_id          VARCHAR(10)    NOT NULL COMMENT '證券代號（含權證、ETF、DR）',
    stock_name        VARCHAR(50)    NOT NULL COMMENT '證券名稱',
    reason_text       TEXT           NOT NULL COMMENT '注意交易資訊全文（已去 HTML）',
    close_price       DECIMAL(12, 4)          COMMENT '收盤價',
    pe_ratio          DECIMAL(12, 4)          COMMENT '本益比；無本益比為 NULL',
    loaded_at         DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '寫入時間（台灣時間）',
    source_fetched_at DATETIME(6)             COMMENT '來自哪次抓取（raw 的 fetched_at）；寫入時只讓比較新的覆蓋',
    PRIMARY KEY (trade_date, stock_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='注意股（每日）';

CREATE TABLE IF NOT EXISTS stage_notice_day (
    trade_date        DATE        NOT NULL COMMENT '日期（有這列＝那天的注意股抓到也整理好了）',
    notice_count      INT         NOT NULL COMMENT '那天幾檔被列注意；0＝沒人被列',
    loaded_at         DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '寫入時間（台灣時間）',
    source_fetched_at DATETIME(6)          COMMENT '來自哪次抓取（raw 的 fetched_at）；寫入時只讓比較新的覆蓋',
    PRIMARY KEY (trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='注意股：每天一列（分辨「沒人被列」和「還沒抓到」）';

CREATE TABLE IF NOT EXISTS stage_notice_clause (
    trade_date        DATE        NOT NULL,
    stock_id          VARCHAR(10) NOT NULL,
    clause            TINYINT     NOT NULL COMMENT '款次 1～14',
    loaded_at         DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '寫入時間（台灣時間）',
    source_fetched_at DATETIME(6)          COMMENT '來自哪次抓取（raw 的 fetched_at）；寫入時只讓比較新的覆蓋',
    PRIMARY KEY (trade_date, stock_id, clause)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='注意股觸發的款次';

CREATE TABLE IF NOT EXISTS stage_punish (
    announce_date          DATE        NOT NULL COMMENT '公布日期',
    stock_id               VARCHAR(10) NOT NULL COMMENT '證券代號',
    stock_name             VARCHAR(50) NOT NULL COMMENT '證券名稱',
    trigger_condition      VARCHAR(50) NOT NULL COMMENT '處置條件，例如 連續三次（不用 condition：MySQL 保留字）',
    period_start           DATE        NOT NULL COMMENT '處置起日',
    period_end             DATE        NOT NULL COMMENT '處置迄日',
    measure_type           VARCHAR(20) NOT NULL COMMENT '處置措施，例如 第一次處置',
    match_interval_minutes TINYINT              COMMENT '撮合間隔（分鐘）；內容沒寫為 NULL',
    content                TEXT        NOT NULL COMMENT '處置內容全文',
    has_star_mark          BOOLEAN     NOT NULL COMMENT '公布日期帶 * 號（意義未確認）',
    loaded_at              DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '寫入時間（台灣時間）',
    source_fetched_at      DATETIME(6)          COMMENT '來自哪次抓取（raw 的 fetched_at）；寫入時只讓比較新的覆蓋',
    PRIMARY KEY (announce_date, stock_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='處置股公告';

CREATE TABLE IF NOT EXISTS stage_punish_day (
    query_date        DATE        NOT NULL COMMENT '查詢日期（有這列＝那天的處置股抓到也整理好了）',
    punish_count      INT         NOT NULL COMMENT '查到幾則處置公告（這天起 14 天內處置中的）；0＝沒有',
    loaded_at         DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '寫入時間（台灣時間）',
    source_fetched_at DATETIME(6)          COMMENT '來自哪次抓取（raw 的 fetched_at）；寫入時只讓比較新的覆蓋',
    PRIMARY KEY (query_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='處置股：每天一列（分辨「沒有處置」和「還沒抓到」）';

CREATE TABLE IF NOT EXISTS stage_punish_watchlist (
    data_date         DATE        NOT NULL COMMENT '名單的資料日期',
    stock_id          VARCHAR(10) NOT NULL,
    stock_name        VARCHAR(50) NOT NULL,
    progress_text     TEXT        NOT NULL COMMENT '近期達標準之情形；多行以換行分隔',
    loaded_at         DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '寫入時間（台灣時間）',
    source_fetched_at DATETIME(6)          COMMENT '來自哪次抓取（raw 的 fetched_at）；寫入時只讓比較新的覆蓋',
    PRIMARY KEY (data_date, stock_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='注意累計可能達處置標準名單（官方答案）';

CREATE TABLE IF NOT EXISTS stage_trading_day (
    trade_date          DATE           NOT NULL COMMENT '日期：市場成交涵蓋到的每一天（沒有這列＝還沒抓到）',
    is_trading          BOOLEAN        NOT NULL COMMENT '1＝有交易；0＝沒交易（週末、休市、颱風假）',
    volume_shares       BIGINT                  COMMENT '成交股數；沒交易的日子為 NULL，下同',
    amount_twd          BIGINT                  COMMENT '成交金額（元）',
    trade_count         BIGINT                  COMMENT '成交筆數',
    taiex_index         DECIMAL(12, 2)          COMMENT '發行量加權股價指數',
    taiex_change_points DECIMAL(12, 2)          COMMENT '漲跌點數',
    loaded_at           DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '寫入時間（台灣時間）',
    source_fetched_at   DATETIME(6)             COMMENT '來自哪次抓取（raw 的 fetched_at）；寫入時只讓比較新的覆蓋',
    PRIMARY KEY (trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='交易日曆：每天一列，以實際成交為準';

CREATE TABLE IF NOT EXISTS stage_holiday (
    holiday_date      DATE         NOT NULL,
    holiday_name      VARCHAR(100) NOT NULL,
    description       TEXT,
    loaded_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '寫入時間（台灣時間）',
    source_fetched_at DATETIME(6)           COMMENT '來自哪次抓取（raw 的 fetched_at）；寫入時只讓比較新的覆蓋',
    PRIMARY KEY (holiday_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='官方開休市日期（不含臨時休市）';

CREATE TABLE IF NOT EXISTS stage_company (
    company_id        VARCHAR(10)  NOT NULL,
    short_name        VARCHAR(50)  NOT NULL,
    full_name         VARCHAR(200) NOT NULL COMMENT '公司全名',
    industry_code     VARCHAR(10)  NOT NULL COMMENT '產業別代碼',
    listed_date       DATE                  COMMENT '上市日期',
    snapshot_date     DATE         NOT NULL COMMENT '取自哪一天的快照',
    loaded_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '寫入時間（台灣時間）',
    source_fetched_at DATETIME(6)           COMMENT '來自哪次抓取（raw 的 fetched_at）；寫入時只讓比較新的覆蓋',
    PRIMARY KEY (company_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='上市公司（依最新快照更新；下市的留著）';

CREATE TABLE IF NOT EXISTS stage_announcement (
    company_id        VARCHAR(10) NOT NULL,
    announce_date     DATE        NOT NULL COMMENT '發言日期',
    enter_date        DATE        NOT NULL COMMENT '輸入日期（序號依這天編，可能早於發言日期）',
    serial_number     INT         NOT NULL COMMENT '輸入日期當天的序號',
    announce_time     TIME        NOT NULL COMMENT '發言時間（判斷收盤前／後用）',
    company_name      VARCHAR(50) NOT NULL,
    subject           TEXT        NOT NULL COMMENT '主旨',
    loaded_at         DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '寫入時間（台灣時間）',
    source_fetched_at DATETIME(6)          COMMENT '來自哪次抓取（raw 的 fetched_at）；寫入時只讓比較新的覆蓋',
    PRIMARY KEY (company_id, enter_date, serial_number)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='公司重大訊息';

CREATE TABLE IF NOT EXISTS stage_announcement_detail (
    company_id        VARCHAR(10) NOT NULL,
    enter_date        DATE        NOT NULL COMMENT '輸入日期（和 stage_announcement 同一個主鍵）',
    serial_number     INT         NOT NULL COMMENT '輸入日期當天的序號',
    clause            VARCHAR(20) NOT NULL COMMENT '符合條款，例如 第31款（觀測站的公告分類）',
    fact_date         DATE                 COMMENT '事實發生日',
    content           MEDIUMTEXT  NOT NULL COMMENT '說明全文（不含發言人姓名、電話）',
    loaded_at         DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '寫入時間（台灣時間）',
    source_fetched_at DATETIME(6)          COMMENT '來自哪次抓取（raw 的 fetched_at）；寫入時只讓比較新的覆蓋',
    PRIMARY KEY (company_id, enter_date, serial_number)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='公司重大訊息全文';

-- 重大訊息的「符合條款」對照表（固定資料，不用爬；條文修正時改這裡，建表時會一併更新）
-- 來源：臺灣證券交易所對有價證券上市公司重大訊息之查證暨公開處理程序 第 4 條第 1 項（115.09.30 版）
--       twse-regulation.twse.com.tw/m/LawContent.aspx?FID=FL007111
-- short_name、description 是整理後的摘要，不是條文原文
CREATE TABLE IF NOT EXISTS stage_announcement_clause (
    clause      VARCHAR(20)  NOT NULL COMMENT '符合條款，例如 第31款（和 stage_announcement_detail.clause 一樣）',
    clause_no   TINYINT      NOT NULL COMMENT '款次數字，排序用',
    short_name  VARCHAR(20)  NOT NULL COMMENT '簡短名稱（卡片上的標籤），例如 財務報告',
    description VARCHAR(200) NOT NULL COMMENT '條文摘要',
    loaded_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '寫入時間（台灣時間）',
    PRIMARY KEY (clause)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='重大訊息符合條款對照表（固定資料）';

INSERT INTO stage_announcement_clause (clause, clause_no, short_name, description) VALUES
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
AS new ON DUPLICATE KEY UPDATE clause_no = new.clause_no, short_name = new.short_name, description = new.description;
