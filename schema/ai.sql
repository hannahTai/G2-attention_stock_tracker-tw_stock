-- ai 層（表名開頭 ai_）：AI 項目的結果，由 ai/task.py 寫入
-- 和 app_* 分開：app_* 隨時可以從 stage 免費重算；AI 結果重跑要花錢、同樣的輸入每次答案可能不同，所以拿到就留著
-- 重算 stage、app（run_rebuild.bat）不碰 ai_* 表
-- 由 common.repository.create_tables() 執行；已存在就跳過

-- 每次呼叫 LLM 一列：token、花費、延遲（看每天花多少、哪個任務最貴）；由 repository.save_llm_call 寫入
-- API 錯誤（429、5xx、連線失敗）沒有回應，不在這裡，記在任務歷史 job_run
CREATE TABLE IF NOT EXISTS ai_llm_call (
    call_id               BIGINT        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    task                  VARCHAR(80)   NOT NULL,
    ref_key               VARCHAR(80)   NOT NULL,
    model                 VARCHAR(50)   NOT NULL,
    prompt_version        VARCHAR(20)   NOT NULL,
    status                VARCHAR(10)   NOT NULL,
    stop_reason           VARCHAR(20),
    input_tokens          INT,
    output_tokens         INT,
    cache_read_tokens     INT,
    cache_write_tokens    INT,
    cost_usd              DECIMAL(12, 6),
    seconds               DECIMAL(10, 3) NOT NULL,
    request_id            VARCHAR(80),
    error                 TEXT,
    called_at             TIMESTAMP(3)  NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ai_llm_call_called ON ai_llm_call (called_at);   -- 每天花多少
-- 失敗幾次就不再問（repository.MAX_FAILED_CALLS）：只放失敗的列，很小
CREATE INDEX IF NOT EXISTS idx_ai_llm_call_failed ON ai_llm_call (ref_key, prompt_version) WHERE status = 'failure';
COMMENT ON TABLE ai_llm_call IS '每次呼叫 LLM 一列（token、花費、延遲）';
COMMENT ON COLUMN ai_llm_call.call_id IS '流水號';
COMMENT ON COLUMN ai_llm_call.task IS '哪個任務，例如 classify_announcement、summarize_news、summarize_card';
COMMENT ON COLUMN ai_llm_call.ref_key IS '處理哪一筆：結果表的代號（公告 announcement_id、新聞 news_id、卡片 card_id），JOIN 結果表就是 ref_key ＝ 代號';
COMMENT ON COLUMN ai_llm_call.model IS '模型，例如 claude-haiku-5-5';
COMMENT ON COLUMN ai_llm_call.prompt_version IS 'prompt 版本，例如 v2';
COMMENT ON COLUMN ai_llm_call.status IS 'success＝拿到可用的結果；failure＝拒答、被截斷、格式不對';
COMMENT ON COLUMN ai_llm_call.stop_reason IS 'API 的 stop_reason，例如 end_turn、max_tokens、refusal';
COMMENT ON COLUMN ai_llm_call.input_tokens IS '輸入 token（不含快取）';
COMMENT ON COLUMN ai_llm_call.output_tokens IS '輸出 token（含思考）';
COMMENT ON COLUMN ai_llm_call.cache_read_tokens IS '從快取讀的輸入 token（約 0.1 倍價）';
COMMENT ON COLUMN ai_llm_call.cache_write_tokens IS '寫進快取的輸入 token（約 1.25 倍價）';
COMMENT ON COLUMN ai_llm_call.cost_usd IS '估計花費（美元），用 ai/client.py 的 PRICES 算';
COMMENT ON COLUMN ai_llm_call.seconds IS '花了幾秒';
COMMENT ON COLUMN ai_llm_call.request_id IS 'API 的 request-id（回報問題用）';
COMMENT ON COLUMN ai_llm_call.error IS '失敗原因（ai/client.py 的 xxx_problem）';
COMMENT ON COLUMN ai_llm_call.called_at IS '呼叫時間（台灣時間）';

-- 公告分類：每則公告每個 prompt 版本一列；同一版已經有就不再問
CREATE TABLE IF NOT EXISTS ai_announcement_label (
    announcement_id CHAR(32)     NOT NULL,
    prompt_version  VARCHAR(20)  NOT NULL,
    category        VARCHAR(20)  NOT NULL,
    is_correction   BOOLEAN      NOT NULL,
    has_news        BOOLEAN      NOT NULL,
    is_negative     BOOLEAN,
    summary         VARCHAR(100) NOT NULL,
    reason          TEXT         NOT NULL,
    summary_ok      BOOLEAN      NOT NULL,
    model           VARCHAR(50)  NOT NULL,
    labeled_at      TIMESTAMP(0) NOT NULL DEFAULT LOCALTIMESTAMP(0),
    PRIMARY KEY (announcement_id, prompt_version)
);
COMMENT ON TABLE ai_announcement_label IS '公告分類：每則重大訊息的類型、標記、白話摘要（LLM 結構化輸出）';
COMMENT ON COLUMN ai_announcement_label.announcement_id IS '公告代號（stage_announcement.announcement_id）';
COMMENT ON COLUMN ai_announcement_label.prompt_version IS 'prompt 版本（ai/classifier.py 的 PROMPT_VERSION）；改 prompt 換版本才重跑';
COMMENT ON COLUMN ai_announcement_label.category IS '類型（13 類選一，見 ai/classifier.py 的 CATEGORIES）';
COMMENT ON COLUMN ai_announcement_label.is_correction IS '更正、補充、修正先前的公告';
COMMENT ON COLUMN ai_announcement_label.has_news IS '有新的資訊；例行、依規定才公布的為否';
COMMENT ON COLUMN ai_announcement_label.is_negative IS '對公司是壞消息（衰退、虧損、訴訟、裁罰、災害…；照事實判斷，不是預測股價）；v3 起才有，舊版為 NULL';
COMMENT ON COLUMN ai_announcement_label.summary IS '一句白話摘要（程式限制 60 字；超過會請 LLM 縮短一次）';
COMMENT ON COLUMN ai_announcement_label.reason IS 'LLM 給的一句理由（評估、除錯用）';
COMMENT ON COLUMN ai_announcement_label.summary_ok IS '摘要通過程式檢查：不超過 60 字、數字都出現在公告裡；否的話網站不顯示摘要（app_announcement.ai_summary 為 NULL）';
COMMENT ON COLUMN ai_announcement_label.model IS '模型';
COMMENT ON COLUMN ai_announcement_label.labeled_at IS '分類時間（台灣時間）';

-- 新聞摘要：每則新聞每個 prompt 版本一列；同一版已經有就不再問。全文當場抓、不存，只存摘要
CREATE TABLE IF NOT EXISTS ai_news_summary (
    news_id         CHAR(32)     NOT NULL,
    prompt_version  VARCHAR(20)  NOT NULL,
    summary         VARCHAR(1200) NOT NULL,
    summary_ok      BOOLEAN      NOT NULL,
    model           VARCHAR(50)  NOT NULL,
    summarized_at   TIMESTAMP(0) NOT NULL DEFAULT LOCALTIMESTAMP(0),
    PRIMARY KEY (news_id, prompt_version)
);
COMMENT ON TABLE ai_news_summary IS '新聞摘要：LLM 讀新聞全文（當場抓，不存）用自己的話寫的摘要';
COMMENT ON COLUMN ai_news_summary.news_id IS '新聞代號（stage_news.news_id）';
COMMENT ON COLUMN ai_news_summary.prompt_version IS 'prompt 版本（ai/summarizer.py 的 PROMPT_VERSION）；改 prompt 換版本才重跑';
COMMENT ON COLUMN ai_news_summary.summary IS '摘要（上限依文章長度：文章的三成，80～1200 字，超過會請 LLM 縮短一次；不是新聞內容的為空字串）';
COMMENT ON COLUMN ai_news_summary.summary_ok IS '摘要通過程式檢查：不是空的、不超過這篇的上限、數字都出現在原文裡；否的話不顯示';
COMMENT ON COLUMN ai_news_summary.model IS '模型；skipped＝網頁打不開、全文太短，沒有問 LLM（summary 是空字串、summary_ok 為否；1 天後再試）';
COMMENT ON COLUMN ai_news_summary.summarized_at IS '摘要時間（台灣時間）';

-- 新聞 RAG：新聞（標題＋新聞摘要）的向量（pgvector）。每則新聞每個模型一列；換模型另外存一份，比較完再換
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS ai_news_embedding (
    news_id     CHAR(32)     NOT NULL,
    model       VARCHAR(80)  NOT NULL,
    embedding   vector(1024) NOT NULL,
    input_hash  CHAR(32)     NOT NULL,
    embedded_at TIMESTAMP(0) NOT NULL DEFAULT LOCALTIMESTAMP(0),
    PRIMARY KEY (news_id, model)
);
COMMENT ON TABLE ai_news_embedding IS '新聞 RAG：新聞的向量（標題＋新聞摘要；本機的 embedding 模型，不呼叫 API）';
COMMENT ON COLUMN ai_news_embedding.news_id IS '新聞代號（stage_news.news_id）';
COMMENT ON COLUMN ai_news_embedding.model IS 'embedding 模型，例如 BAAI/bge-m3；不同模型的向量不能互相比';
COMMENT ON COLUMN ai_news_embedding.embedding IS '標題＋新聞摘要的向量（沒有摘要用導言，都沒有就只有標題；1024 維，長度 1：cosine 相似度＝內積；用 <=> 算 cosine 距離）';
COMMENT ON COLUMN ai_news_embedding.input_hash IS '轉成向量的文字的 md5；摘要後來才寫好、文字變了就重轉';
COMMENT ON COLUMN ai_news_embedding.embedded_at IS '轉成向量的時間（台灣時間）';

-- 同一件事：好幾家媒體報同一件事，歸成一個 story（story_id＝這件事最早的那則新聞）。每則新聞一列（換模型就換成新模型的歸類）
CREATE TABLE IF NOT EXISTS ai_news_story (
    news_id    CHAR(32)     NOT NULL,
    model      VARCHAR(80)  NOT NULL,
    story_id   CHAR(32)     NOT NULL,
    grouped_at TIMESTAMP(0) NOT NULL DEFAULT LOCALTIMESTAMP(0),
    PRIMARY KEY (news_id)
);
CREATE INDEX IF NOT EXISTS idx_ai_news_story_story ON ai_news_story (story_id);
COMMENT ON TABLE ai_news_story IS '同一件事的新聞歸在一起（新聞 RAG 的向量判斷，不呼叫 API）';
COMMENT ON COLUMN ai_news_story.news_id IS '新聞代號（stage_news.news_id）';
COMMENT ON COLUMN ai_news_story.model IS '用哪個 embedding 模型的向量判斷';
COMMENT ON COLUMN ai_news_story.story_id IS '這件事最早的那則新聞的 news_id；自己就是最早的（或沒有別家報）等於自己的 news_id';
COMMENT ON COLUMN ai_news_story.grouped_at IS '歸類的時間（台灣時間）';

-- 卡片 AI 摘要：每張卡片（一檔一天）每個 prompt 版本一列；輸入和前一天一樣就沿用（reused），不呼叫 API
CREATE TABLE IF NOT EXISTS ai_card_summary (
    card_id             CHAR(32)     NOT NULL,
    prompt_version      VARCHAR(20)  NOT NULL,
    stock_id            VARCHAR(10)  NOT NULL,
    card_date           DATE         NOT NULL,
    summary             VARCHAR(300) NOT NULL,
    news_ids            TEXT[]       NOT NULL DEFAULT '{}',
    input_hash          CHAR(32)     NOT NULL,
    summary_ok          BOOLEAN      NOT NULL,
    reused              BOOLEAN      NOT NULL DEFAULT FALSE,
    model               VARCHAR(50)  NOT NULL,
    summarized_at       TIMESTAMP(0) NOT NULL DEFAULT LOCALTIMESTAMP(0),
    announcement_count  INT,
    PRIMARY KEY (card_id, prompt_version)
);
-- 舊的資料庫（這一欄加進來以前建的表）：補上這一欄，舊的列是 NULL（不會因為這一欄重寫）
ALTER TABLE ai_card_summary ADD COLUMN IF NOT EXISTS announcement_count INT;
CREATE INDEX IF NOT EXISTS idx_ai_card_summary_stock ON ai_card_summary (stock_id, card_date);   -- app_card 用它接
COMMENT ON TABLE ai_card_summary IS '卡片 AI 摘要：卡片牆上的每一張（stage_watched_stock）每天 2～3 句白話（最近為什麼漲跌、公告和新聞裡的原因）';
COMMENT ON COLUMN ai_card_summary.card_id IS '卡片代號：md5(證券代號_日期)，例如 md5(''2330_20261120'')；ai_llm_call.ref_key 記這個';
COMMENT ON COLUMN ai_card_summary.prompt_version IS 'prompt 版本（ai/card_summarizer.py 的 PROMPT_VERSION）';
COMMENT ON COLUMN ai_card_summary.stock_id IS '證券代號';
COMMENT ON COLUMN ai_card_summary.card_date IS '卡片的日期（交易日）';
COMMENT ON COLUMN ai_card_summary.summary IS '2～3 句白話摘要（程式限制 150 字，超過會請 LLM 縮短一次）';
COMMENT ON COLUMN ai_card_summary.news_ids IS '第三句用到的新聞（stage_news.news_id）；網站從 app_stock_news 查標題、連結，不用 LLM 打的字';
COMMENT ON COLUMN ai_card_summary.input_hash IS '輸入的雜湊值（prompt 版本＋整理好的輸入）；和前一天一樣就沿用前一天的摘要';
COMMENT ON COLUMN ai_card_summary.summary_ok IS '摘要通過程式檢查：不超過 150 字、數字都出現在輸入裡、引用的新聞編號是這次給的；否的話不顯示';
COMMENT ON COLUMN ai_card_summary.reused IS '沿用前一天的摘要（輸入一樣，沒有呼叫 API）';
COMMENT ON COLUMN ai_card_summary.model IS '模型';
COMMENT ON COLUMN ai_card_summary.summarized_at IS '這一列寫入的時間（台灣時間；沿用前一天的摘要也是寫入的時間）';
COMMENT ON COLUMN ai_card_summary.announcement_count IS '寫的時候給了幾則公告（那天往前 30 天）；之後又出現新的公告（例如傍晚才發），下一輪新聞線重寫。NULL＝舊的列，不重寫';
