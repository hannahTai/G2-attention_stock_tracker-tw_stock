"""把資料套進 Jinja2 範本（templates/），變成整頁的 HTML（app.py 回傳給瀏覽器）。不碰網路，可以單獨測試。

  overview_html()  總覽（卡片牆）
  stock_html()     個股頁
  stats_html()     統計頁
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


PICKER_DAYS = 30   # 交易日選單只列最近幾個交易日


def picker_dates(dates: list[datetime.date], day: datetime.date) -> list[datetime.date]:
    """交易日選單：最近 PICKER_DAYS 個交易日，新的在上；正在看的那天比這些舊時也放進去（放在最後）。

    >>> d = [datetime.date(2026, 11, i) for i in range(1, 27)] + [datetime.date(2026, 10, i) for i in range(1, 32)]
    >>> got = picker_dates(d, datetime.date(2026, 10, 1))
    >>> len(got), got[0], got[-2], got[-1]
    (31, datetime.date(2026, 11, 26), datetime.date(2026, 10, 28), datetime.date(2026, 10, 1))
    """
    recent = sorted(set(dates) | {day}, reverse=True)[:PICKER_DAYS]
    return recent if day in recent else [*recent, day]


def ymd(day: datetime.date) -> str:
    """網址上的日期寫法，和專案其他地方一樣。

    >>> ymd(datetime.date(2026, 11, 10))
    '20261110'
    """
    return day.strftime("%Y%m%d")


# css_version／js_version：style.css／stats.js 的修改時間，接在網址後面（?v=…），改了瀏覽器才會重抓。
# 啟動時算一次就好：改 CSS 時 uvicorn --reload 會重啟（--reload-include *.css），雲端只有換 image 才會變
ENV.globals.update(md=views.md, roc=views.roc, ymd=ymd, fonts=FONTS, show_date_picker=SHOW_DATE_PICKER,
                   css_version=int((HERE / "static" / "style.css").stat().st_mtime),
                   js_version=int((HERE / "static" / "stats.js").stat().st_mtime))


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
    return render("overview.html", day=day, notice=notice_text(requested, day), back_query=back_query, current_view="wall",
                  dates=picker_dates(dates, day), tabs=views.TABS, filters=filters,
                  only_label=dict(filters).get(page["only"], ""),
                  empty_text=views.EMPTY_TEXT, tab_notes=views.TAB_NOTES, url_with=url_with, **page)


def stock_html(detail: dict, requested: datetime.date | None = None, back_params: dict | None = None) -> str:
    """個股頁。detail 是 API /stocks/{stock_id} 的回應；back_params 是從總覽帶來的 tab、only、q（「回總覽」用）。"""
    page = views.stock_page(detail)
    back_query = view_query({"date": ymd(page["day"]), **(back_params or {})})
    return render("stock.html", notice=notice_text(requested, page["day"]), back_query=back_query, **page)


def stats_html(cards: dict, stats: dict, dates: list[datetime.date], requested: datetime.date | None = None,
               part: str = "future") -> str:
    """統計頁。cards 是 API /cards 的回應（明日交易限制、未來出關日程用），stats 是 /stats 的回應，dates 是 /dates 的回應。"""
    page = views.stats_page(cards["cards"], stats)
    date = ymd(page["day"])
    current = {"view": "stats", "date": date, "part": part, "history": page["history"], "period": page["period"]}

    def wall_query(tab: str) -> str:
        """卡片牆的某個分頁（明天的交易限制點進去看是哪幾支）。"""
        return view_query({"date": date, "tab": tab})

    def stats_query(**changes) -> str:
        """統計頁目前的參數，只換掉指定的（交易日、分頁、歷史期間、每日／每週）；預設值（未來幾日、近 30 個交易日、每週）不寫。"""
        params = {**current, **changes}
        defaults = {"part": "future", "history": "30", "period": "week"}
        return urllib.parse.urlencode({k: v for k, v in params.items() if defaults.get(k) != v})

    return render("stats.html", notice=notice_text(requested, page["day"]), current_view="stats", part=part, parts=views.PARTS,
                  dates=picker_dates(dates, page["day"]),
                  wall_query=wall_query, stats_query=stats_query, **page)


def error_html(message: str) -> str:
    """錯誤頁：一行說明＋回總覽的連結（API 連不上、找不到資料、網址日期看不懂）。"""
    return render("error.html", day=None, message=message)
