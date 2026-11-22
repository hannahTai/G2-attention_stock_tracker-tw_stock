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
COMMENT ON TABLE ai_llm_call IS '每次呼叫 LLM 一列（token、花費、延遲）';
COMMENT ON COLUMN ai_llm_call.call_id IS '流水號';
COMMENT ON COLUMN ai_llm_call.task IS '哪個任務，例如 classify_announcement、summarize_news';
COMMENT ON COLUMN ai_llm_call.ref_key IS '處理哪一筆，例如 9908_20261102_1（公司_輸入日期_序號）、新聞的 news_id';
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

-- ① 公告分類：每則公告每個 prompt 版本一列；同一版已經有就不再問
CREATE TABLE IF NOT EXISTS ai_announcement_label (
    announcement_id CHAR(32)     NOT NULL,
    prompt_version  VARCHAR(20)  NOT NULL,
    category        VARCHAR(20)  NOT NULL,
    is_correction   BOOLEAN      NOT NULL,
    has_news        BOOLEAN      NOT NULL,
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
COMMENT ON COLUMN ai_announcement_label.summary IS '一句白話摘要（程式限制 40 字；超過會請 LLM 縮短一次）';
COMMENT ON COLUMN ai_announcement_label.reason IS 'LLM 給的一句理由（評估、除錯用）';
COMMENT ON COLUMN ai_announcement_label.summary_ok IS '摘要通過程式檢查：不超過 40 字、數字都出現在公告裡；否的話網站不該顯示摘要（網站還沒接上 ai_announcement_label）';
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
COMMENT ON COLUMN ai_news_summary.model IS '模型';
COMMENT ON COLUMN ai_news_summary.summarized_at IS '摘要時間（台灣時間）';
