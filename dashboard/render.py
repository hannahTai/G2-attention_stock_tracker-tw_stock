"""把資料套進 Jinja2 範本（templates/），變成整頁的 HTML（app.py 回傳給瀏覽器）。不碰網路，可以單獨測試。

  overview_html()  總覽（卡片牆）
  stock_html()     個股頁
  error_html()     錯誤頁

連結都是網址參數（?date=20261120&tab=one、?stock=1795&date=20261120），app.py 讀網址決定畫哪一頁。
back_query：總覽目前的分頁、只看、搜尋（tab、only、q）寫成的查詢字串。點卡片進個股頁時帶著，個股頁的「回總覽」才回到同一個地方。
"""
import datetime
import pathlib
import urllib.parse

import jinja2

from common.config import SHOW_DATE_PICKER
from dashboard import views

HERE = pathlib.Path(__file__).parent

# autoescape：資料裡的 < > & 會被跳脫，公告主旨裡就算有 HTML 也不會被當成網頁執行
ENV = jinja2.Environment(loader=jinja2.FileSystemLoader(HERE / "templates"), autoescape=True)

FONTS = ("https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600"
         "&family=Noto+Sans+TC:wght@400;500;700;900&display=swap")


def ymd(day: datetime.date) -> str:
    """網址上的日期寫法，和專案其他地方一樣。

    >>> ymd(datetime.date(2026, 11, 10))
    '20261110'
    """
    return day.strftime("%Y%m%d")


# css_version：style.css 的修改時間，接在 CSS 網址後面（style.css?v=…），改了 CSS 瀏覽器才會重抓。
# 啟動時算一次就好：改 CSS 時 uvicorn --reload 會重啟（--reload-include *.css），雲端只有換 image 才會變
ENV.globals.update(md=views.md, roc=views.roc, ymd=ymd, fonts=FONTS, show_date_picker=SHOW_DATE_PICKER,
                   css_version=int((HERE / "static" / "style.css").stat().st_mtime))


def render(template: str, **context) -> str:
    """套用一個範本，回傳 HTML。"""
    return ENV.get_template(template).render(**context)


def notice_text(requested: datetime.date | None, day: datetime.date) -> str | None:
    """要求的日期沒有資料、改顯示別天時的提醒。

    >>> notice_text(datetime.date(2026, 11, 21), datetime.date(2026, 11, 20))
    '11/21 不是交易日或還沒有資料，改顯示 11/20'
    """
    if requested is None or requested == day:
        return None
    return f"{views.md(requested)} 不是交易日或還沒有資料，改顯示 {views.md(day)}"


def view_query(params: dict) -> str:
    """總覽的網址參數（date、tab、only、q）→ 查詢字串；空的、預設的（tab=all）不寫。

    >>> view_query({"date": "20261120", "tab": "all", "only": "", "q": "246"})
    'date=20261120&q=246'
    """
    return urllib.parse.urlencode({k: v for k, v in params.items() if v and not (k == "tab" and v == "all")})


def overview_html(data: dict, dates: list[datetime.date], requested: datetime.date | None = None,
                  tab: str = "all", only: str = "", search: str = "") -> str:
    """總覽。data 是 API /cards 的回應，dates 是 /dates 的回應。"""
    day = data["trade_date"]
    page = views.overview(data["cards"], tab, only, search)
    current = {"date": ymd(day), "tab": page["tab"], "only": page["only"], "q": page["search"]}

    def url_with(**changes) -> str:
        """目前的網址參數，只換掉指定的；空的、預設的（tab=all）不寫。"""
        return view_query({**current, **changes})

    back_query = view_query({"tab": page["tab"], "only": page["only"], "q": page["search"]})
    filters = views.filter_labels(day)   # 「11/20 注意股」：前面加資料的日期
    return render("overview.html", day=day, notice=notice_text(requested, day), back_query=back_query,
                  dates=sorted(set(dates) | {day}, reverse=True), tabs=views.TABS, filters=filters,
                  only_label=dict(filters).get(page["only"], ""),
                  empty_text=views.EMPTY_TEXT, tab_notes=views.TAB_NOTES, url_with=url_with, **page)


def stock_html(detail: dict, requested: datetime.date | None = None, back_params: dict | None = None) -> str:
    """個股頁。detail 是 API /stocks/{stock_id} 的回應；back_params 是從總覽帶來的 tab、only、q（「回總覽」用）。"""
    page = views.stock_page(detail)
    back_query = view_query({"date": ymd(page["day"]), **(back_params or {})})
    return render("stock.html", notice=notice_text(requested, page["day"]), back_query=back_query, **page)


def error_html(message: str) -> str:
    """錯誤頁：一行說明＋回總覽的連結（API 連不上、找不到資料、網址日期看不懂）。"""
    return render("error.html", day=None, message=message)
