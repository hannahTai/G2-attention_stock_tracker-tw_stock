"""設定：從環境變數讀取（可寫在專案根目錄的 .env），讀不到就用預設值。"""
import os
import pathlib

import dotenv

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent   # common/ 的上一層（專案根目錄）
dotenv.load_dotenv(PROJECT_ROOT / ".env")                       # 已有的環境變數不會被覆蓋


def secret(name: str) -> str:
    """金鑰類的設定：沒設，或還是 .env.example 的範例值（your-…）都當成沒設，回傳空字串。"""
    value = os.environ.get(name, "").strip()
    return "" if value.startswith("your-") else value


# 資料來源
TWSE_BASE = os.environ.get("TWSE_BASE", "https://www.twse.com.tw/rwd/zh")
TWSE_OPENAPI_BASE = os.environ.get("TWSE_OPENAPI_BASE", "https://openapi.twse.com.tw/v1")
MOPS_BASE = os.environ.get("MOPS_BASE", "https://mops.twse.com.tw/mops/api")
FINMIND_BASE = os.environ.get("FINMIND_BASE", "https://api.finmindtrade.com/api/v4")
FINMIND_TOKEN = secret("FINMIND_TOKEN")   # 選填：不帶也抓得到，帶了每小時的次數上限比較高
# 新聞：被關注的股票往前抓幾天；網站、卡片 AI 摘要看最近這幾天（含當天）
NEWS_LOOKBACK_DAYS = int(os.environ.get("NEWS_LOOKBACK_DAYS", "7"))

# 請求間隔（秒），太快會被擋
SLEEP_SECONDS = float(os.environ.get("SLEEP_SECONDS", "1.0"))
SLEEP_SECONDS_MOPS = float(os.environ.get("SLEEP_SECONDS_MOPS", "0.6"))
# FinMind（帶 token）每小時 600 次：6 秒一個剛好。所有打 FinMind 的任務都走 crawl_finmind 佇列（只有一個 worker），
# 每天的資料線和回補一起排隊，加起來也不會超過；不要另外在本機直接打（會和佇列的加在一起，超過就回 402）
SLEEP_SECONDS_FINMIND = float(os.environ.get("SLEEP_SECONDS_FINMIND", "6.0"))
SLEEP_SECONDS_NEWS_DETAIL = float(os.environ.get("SLEEP_SECONDS_NEWS_DETAIL", "0.5"))   # 新聞網頁：分散在十幾個網站
# 打開一則新聞網頁：從開始到讀完最多幾秒（crawler/client.py；TIMEOUT 只管每次讀取的等待，慢慢送的網站可以一直拖）；
# crawl_news_detail 最多做幾秒，超過被 Celery 停掉（最後一道保險）。summarize_news 不設：還要問 LLM，硬停會丟掉付過錢的回答
NEWS_DEADLINE_SECONDS = 60
NEWS_TASK_TIME_LIMIT_SECONDS = 3 * NEWS_DEADLINE_SECONDS

# 暫時性的失敗（逾時、連線失敗、5xx、429）重試幾次、每次等幾秒
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "3"))
RETRY_DELAY_SECONDS = float(os.environ.get("RETRY_DELAY_SECONDS", "5"))

# 同一時間只跑一輪（main.py 的檔案鎖 data/pipeline.lock）：上一輪還沒跑完，最多等幾秒（預設 2 小時，回補可能跑很久）
PIPELINE_LOCK_WAIT_SECONDS = float(os.environ.get("PIPELINE_LOCK_WAIT_SECONDS", str(2 * 60 * 60)))

# PostgreSQL（資料庫 DB_NAME 裡的 schema 也叫 DB_NAME，SQL 寫 tw_stock.<表名> 或直接寫表名都可以）
DB_HOST = os.environ.get("DB_HOST", "127.0.0.1")
DB_PORT = int(os.environ.get("DB_PORT", 5432))
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
DB_NAME = os.environ.get("DB_NAME", "tw_stock")

# 網頁（dashboard/）呼叫的資料 API（api/，FastAPI）
# 寫 127.0.0.1 不寫 localhost：Windows 會先試 IPv6（::1），uvicorn 只開在 IPv4，每次要多等約 2 秒
API_URL = os.environ.get("API_URL", "http://127.0.0.1:8001")

# Celery 任務佇列：1（預設）＝producer 把任務送進 RabbitMQ，由 worker 執行（common/worker.py，要先 docker compose up）；
# 0＝在這台電腦依序執行，不需要 RabbitMQ（沒開 Docker 時臨時用）
USE_CELERY = os.environ.get("USE_CELERY", "1") == "1"
RABBITMQ_HOST = os.environ.get("RABBITMQ_HOST", "127.0.0.1")
RABBITMQ_PORT = int(os.environ.get("RABBITMQ_PORT", 5672))
RABBITMQ_USER = os.environ.get("RABBITMQ_USER", "rabbitmq")           # 預設值和 docker-compose.yml 一樣（本機開發用）
RABBITMQ_PASSWORD = os.environ.get("RABBITMQ_PASSWORD", "rabbitmq")

# 任務歷史：每個任務跑完寫一列 tw_stock.job_run（common/runner.py 的 record()）；測試裡關掉
JOB_LOG = os.environ.get("JOB_LOG", "1") == "1"

# 文字 log：每天一個檔 <LOG_DIR>/<名稱>-YYYY-MM-DD.log，只留最近 LOG_KEEP_DAYS 天（common/logs.py）
LOG_DIR = pathlib.Path(os.environ.get("LOG_DIR", str(PROJECT_ROOT / "data" / "logs")))
LOG_KEEP_DAYS = int(os.environ.get("LOG_KEEP_DAYS", "30"))

# AI（ai/）：Claude API。金鑰的環境變數 ANTHROPIC_API_KEY 由 anthropic 套件自己讀；沒設就跳過 AI 任務，資料線照常
ANTHROPIC_API_KEY = secret("ANTHROPIC_API_KEY")
AI_CLASSIFY_MODEL = os.environ.get("AI_CLASSIFY_MODEL", "claude-haiku-5-5")   # 公告分類：便宜的小型模型
AI_SUMMARY_MODEL = os.environ.get("AI_SUMMARY_MODEL", "claude-haiku-5-5")     # 新聞摘要
AI_CARD_MODEL = os.environ.get("AI_CARD_MODEL", "claude-haiku-5-5")           # 卡片 AI 摘要
# 新聞 RAG：本機的 embedding 模型（sentence-transformers；第一次用時下載，存在 HF_HOME）。換模型要改 schema/ai.sql 的 vector(維度)
EMBED_MODEL = os.environ.get("EMBED_MODEL", "BAAI/bge-m3")
