"""好幾個檔案共用的固定值（只有一個檔案用的，直接寫在那個檔案裡；會隨環境改變的放 config.py）。"""

# 每張 stage 表的主鍵：transform 寫入時用主鍵找出要換掉的舊列（transform/task.py）；
# repository 只接受這裡列的表名（common/repository.py）
STAGE_KEYS = {
    "stage_notice": ["trade_date", "stock_id"],
    "stage_notice_clause": ["trade_date", "stock_id", "clause"],
    "stage_punish": ["announce_date", "stock_id"],
    "stage_punish_watchlist": ["data_date", "stock_id"],
    "stage_notice_day": ["trade_date"],
    "stage_punish_day": ["query_date"],
    "stage_trading_day": ["trade_date"],
    "stage_holiday": ["holiday_date"],
    "stage_company": ["company_id"],
    "stage_announcement": ["company_id", "enter_date", "serial_number"],
    "stage_announcement_detail": ["company_id", "enter_date", "serial_number"],
}
