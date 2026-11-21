"""設定：從環境變數讀取（可寫在專案根目錄的 .env），讀不到就用預設值。"""
import os
import pathlib

import dotenv

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent   # common/ 的上一層（專案根目錄）
dotenv.load_dotenv(PROJECT_ROOT / ".env")                       # 已有的環境變數不會被覆蓋

# 資料來源
TWSE_BASE = os.environ.get("TWSE_BASE", "https://www.twse.com.tw/rwd/zh")
TWSE_OPENAPI_BASE = os.environ.get("TWSE_OPENAPI_BASE", "https://openapi.twse.com.tw/v1")
MOPS_BASE = os.environ.get("MOPS_BASE", "https://mops.twse.com.tw/mops/api")

# 請求間隔（秒），太快會被擋
SLEEP_SECONDS = float(os.environ.get("SLEEP_SECONDS", "1.0"))
SLEEP_SECONDS_MOPS = float(os.environ.get("SLEEP_SECONDS_MOPS", "0.6"))

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

# 網頁（dashboard/，Streamlit）呼叫的資料 API（api/，FastAPI）
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
