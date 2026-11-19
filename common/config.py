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

# MySQL
MYSQL_HOST = os.environ.get("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.environ.get("MYSQL_PORT", 3306))
MYSQL_ACCOUNT = os.environ.get("MYSQL_ACCOUNT", "root")
MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.environ.get("MYSQL_DATABASE", "tw_stock")
