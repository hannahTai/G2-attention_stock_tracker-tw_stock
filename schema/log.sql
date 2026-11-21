-- 任務歷史：每個任務每跑一次寫一列（成功、失敗、要重試的都寫），不管是本機依序執行還是 Celery worker
-- 由 common/runner.py 的 record() 寫入（worker 上由 common/worker.py 的 Job 呼叫）；寫不進去只印警告，不影響任務
-- 只新增、不修改；和 data/logs/ 的文字 log 不同，這張表永久保留，可以用 SQL 查（哪些任務常失敗、平均幾秒）
-- 由 common.repository.create_tables() 執行；已存在就跳過

CREATE TABLE IF NOT EXISTS job_run (
    run_id      BIGINT       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    task        VARCHAR(80)  NOT NULL,
    args        JSONB        NOT NULL,
    status      VARCHAR(10)  NOT NULL,
    result      TEXT,
    error       TEXT,
    attempt     SMALLINT     NOT NULL,
    mode        VARCHAR(10)  NOT NULL,
    worker      VARCHAR(80),
    started_at  TIMESTAMP(3) NOT NULL,
    finished_at TIMESTAMP(3) NOT NULL,
    seconds     DECIMAL(10, 3) NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_job_run_started ON job_run (started_at);          -- 看某一天、某一輪
CREATE INDEX IF NOT EXISTS idx_job_run_task ON job_run (task, started_at);       -- 看某個任務的歷史

COMMENT ON TABLE job_run IS '任務歷史：每個任務每跑一次一列';
COMMENT ON COLUMN job_run.run_id IS '流水號';
COMMENT ON COLUMN job_run.task IS '任務名稱（模組.函式），例如 crawler.task.crawl_notice';
COMMENT ON COLUMN job_run.args IS '任務參數，例如 {"date_yyyymmdd": "20261120"}';
COMMENT ON COLUMN job_run.status IS 'success＝成功；retry＝暫時性失敗、等一下重做；failure＝失敗（不再重試）';
COMMENT ON COLUMN job_run.result IS '成功時任務回傳的一行結果，例如 raw_notice/20261120';
COMMENT ON COLUMN job_run.error IS '失敗時的錯誤（類型: 訊息）';
COMMENT ON COLUMN job_run.attempt IS '第幾次嘗試（1＝第一次）';
COMMENT ON COLUMN job_run.mode IS 'celery＝在 worker 上跑；local＝本機依序執行（USE_CELERY=0）';
COMMENT ON COLUMN job_run.worker IS '哪個 worker 跑的（例如 crawl_twse@tw_stock）；本機執行為 NULL';
COMMENT ON COLUMN job_run.started_at IS '開始時間（台灣時間）';
COMMENT ON COLUMN job_run.finished_at IS '結束時間（台灣時間）';
COMMENT ON COLUMN job_run.seconds IS '花了幾秒';
