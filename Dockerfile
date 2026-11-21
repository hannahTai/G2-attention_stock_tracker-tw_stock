# 專案的 Python 環境：setup（建表）、api（FastAPI）、web（Streamlit）、producer、三個 Celery worker 共用這一個 image，只是啟動的指令不同
# 見 docker-compose.yml

FROM python:3.12-slim

# uv：跟本機一樣用 uv.lock 裝套件，版本完全一致
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv

WORKDIR /app

# UV_COMPILE_BYTECODE：先編譯成 .pyc，啟動比較快
# UV_LINK_MODE=copy：Docker 的檔案系統不能用 hardlink，避免警告
# UV_PROJECT_ENVIRONMENT：套件裝進 /opt/venv，不放在 /app，掛載程式碼時不會被蓋掉
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    TZ=Asia/Taipei

# 先只複製套件清單裝套件：程式碼改了、套件沒改時，這層用快取，不用重裝
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 再複製程式碼，裝專案本身
COPY . .
RUN uv sync --frozen --no-dev
