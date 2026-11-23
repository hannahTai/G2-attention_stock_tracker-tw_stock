"""dashboard/render.py：總覽、個股頁、錯誤頁的 HTML。資料用假的，不連網路。"""
import datetime

from dashboard import render, views
from tests.web_fixtures import CARDS, DAY, NEXT, card_of, make_card, stock_detail


def overview(tab: str = "all", only: str = "", requested: datetime.date | None = None, cards=None) -> str:
    data = {"trade_date": DAY, "cards": [dict(c) for c in (cards or CARDS)]}
    return render.overview_html(data, [DAY], requested, tab, only)


def tab_count(page: str, label: str) -> int:
    """分頁上「標籤 <span class="count">N</span>」的 N。"""
    marker = f'{label} <span class="count">'
    start = page.index(marker) + len(marker)
    return int(page[start:page.index("<", start)])


# ---------- 總覽 ----------
def test_overview_tabs_add_up_to_all():
    page = overview()
    sections = [tab_count(page, label) for label in ("聽牌", "即將處置", "處置中", "明日出關", "其他")]
    assert sections == [1, 1, 0, 1, 1]   # 丙公司今天處置最後一天：在明日出關
    assert tab_count(page, "全部") == sum(sections) == 4


def test_all_tab_has_no_section_titles_and_cards_follow_the_stages():
    page = overview()
    assert "sec-title" not in page
    order = [page.index(name) for name in ("乙公司", "丁公司", "丙公司", "甲公司")]   # 聽牌 → 即將處置 → 處置中 → 其他
    assert order == sorted(order)


def test_tab_notes_are_in_the_tabs():
    page = overview()
    assert '<span class="tip">下一個營業日再被列注意就處置</span>' in page
    tabs = page[page.index('<nav class="tabs"'):page.index("</nav>", page.index('<nav class="tabs"'))]
    assert tabs.count('class="info"') == 5   # 「全部」沒有


def test_only_filter_counts_and_filtering():
    page = overview()
    assert tab_count(page, "11/20 注意股") == 3 and tab_count(page, "明日出關") == 1
    filtered = overview(only="leave_tomorrow")
    assert "丙公司" in filtered and "甲公司" not in filtered
    assert tab_count(filtered, "全部") == 4 and tab_count(filtered, "聽牌") == 1    # 分頁數字是當天總數，不跟著篩選變
    assert "丙公司" in filtered and "乙公司" not in filtered


def test_links_keep_the_other_parameters():
    page = overview(tab="leave_tomorrow", only="listed")
    assert 'href="?date=20261120&amp;tab=one&amp;only=listed"' in page                    # 換分頁，篩選留著
    assert 'href="?date=20261120&amp;tab=leave_tomorrow"' in page                         # 取消篩選，分頁留著
    page = render.overview_html({"trade_date": DAY, "cards": [dict(c) for c in CARDS]}, [DAY], None, "leave_tomorrow", "", "33")
    assert 'href="?stock=3333&amp;date=20261120&amp;tab=leave_tomorrow&amp;q=33"' in page   # 點卡片到個股頁，帶著分頁和搜尋


def test_back_link_returns_to_the_same_tab():
    stock = render.stock_html(stock_detail(card_of("3333")), back_params={"tab": "punish", "only": "", "q": "33"})
    assert 'href="?date=20261120&amp;tab=punish&amp;q=33">‹ 回總覽' in stock
    assert 'href="?date=20261120">‹ 回總覽' in render.stock_html(stock_detail(card_of("3333")))   # 直接開個股頁：回「全部」


def test_old_tab_links_become_filters():
    page = overview(tab="listed")
    assert "甲公司" in page and "丙公司" not in page


def test_announced_disposition_shows_its_dates():
    page = overview()
    assert "11/23 起處置" in page and "11/23–11/27 第一次處置" in page
    assert "等官方公布" not in page


def test_no_if_listed_again_prediction():
    page = overview(tab="one")
    assert "乙公司" in page and "甲公司" not in page
    assert "若 11/23" not in page and "11/24–11/30" not in page
    stock = render.stock_html(stock_detail(card_of("2222")))   # 個股頁也不放推算
    assert "若 11/23" not in stock and "11/24–11/30" not in stock


def test_punish_card_shows_matching_and_preorder():
    page = overview(tab="leave_tomorrow")   # 丙公司：處置最後一天，卡片照樣是處置中
    assert "約每 2 分鐘一次" in page and "全部委託都要預收" in page
    assert "收盤 —" not in page   # 沒有收盤價就不顯示


def test_quiet_card_is_hidden_without_a_note():
    assert "戊公司" not in overview()
    assert "沒有列出" not in overview()


def test_unknown_tab_falls_back_to_all():
    assert "甲公司" in overview(tab="nope")


def test_non_trading_day_shows_a_notice():
    assert "11/21 不是交易日或還沒有資料，改顯示 11/20" in overview(requested=datetime.date(2026, 11, 21))


def test_date_menu_links_use_yyyymmdd():
    assert '<a href="?date=20261120" aria-current="date">' in overview()


def test_resume_day_with_one_step_is_in_the_one_section():
    resumed = make_card("6666", "己公司", is_listed_today=0, steps_left=1, streak3=2, streak3_left=1,
                        closest_path="streak3", period_start=datetime.date(2026, 11, 13),
                        period_end=datetime.date(2026, 11, 19), resume_date=DAY, measure_type="第一次處置", **NEXT)
    page = overview(cards=[resumed])
    assert '<article class="card tone-amber">' in page and 'class="rows"' not in page   # 聽牌：卡片，不在「其他」清單
    stock = render.stock_html(stock_detail(resumed))
    assert "11/13–11/19" in stock and "出關，恢復正常交易" in stock


# ---------- 個股頁 ----------
def test_stock_page_sections_in_order():
    page = render.stock_html(stock_detail(card_of("2222")))
    order = [page.index(f"<h3>{h}") for h in ("AI 摘要", "公開資訊", "新聞資訊", "處置進度", "注意紀錄", "處置紀錄")]
    assert order == sorted(order)


def test_one_step_stock_has_no_predicted_timeline():
    page = render.stock_html(stock_detail(card_of("2222")))
    section = page.split("<h3>處置紀錄")[1]
    assert '<p class="muted">無</p>' in section and "若再被列" not in section


def test_stock_page_met_today_with_announced_dates():
    page = render.stock_html(stock_detail(card_of("4444")))
    assert "即將處置" in page and "11/23 起處置" in page and "11/30" in page
    assert "等官方公布" not in page


def test_stock_page_announcements_are_escaped():
    page = render.stock_html(stock_detail(card_of("2222")))
    assert "更正本公司營收公告 US$ 1,000" in page and "盤前公告" in page
    assert "盤中" not in page   # 15:26 盤後、08:30 盤前都不標


def test_back_link_keeps_the_date():
    assert '<a class="back" href="?date=20261120">' in render.stock_html(stock_detail(card_of("2222")))


def test_error_page():
    page = render.error_html("11/20 沒有 9999 的資料")
    assert "11/20 沒有 9999 的資料" in page and 'href="?"' in page


def test_page_links_fonts_and_css():
    page = overview()
    assert page.lstrip().startswith("<!doctype html>") and "</html>" in page
    assert f'<link rel="stylesheet" href="{render.FONTS}">' in page.replace("&amp;", "&")
    assert '<link rel="stylesheet" href="/static/style.css?v=' in page


def test_zero_filter_is_not_a_link():
    page = overview()
    assert '<span class="chip zero" aria-disabled="true">11/20 出關 <span class="count">0</span></span>' in page
    assert "only=leave_today" not in page


def test_empty_filter_result_explains_and_offers_to_clear():
    page = overview(only="leave_today")   # 網址自己打 only=leave_today，但這天沒有
    assert "11/20 沒有符合「11/20 出關」的股票" in page and '<a href="?date=20261120">清除篩選</a>' in page


def test_empty_tab_says_which_kind_is_missing():
    page = overview(tab="soon", cards=[card_of("1111")])   # 只有一檔差 2 次
    assert "11/20 沒有即將處置的股票" in page
    assert "這個分頁沒有股票" not in page


def test_rest_is_small_cards_with_steps_left():
    page = overview(tab="rest")   # 甲公司：差 2 次
    assert 'class="cards cards-sm"' in page and "差 2 次" in page


def test_rest_lists_every_stock_without_more_button():
    many = [make_card(f"{2000 + i}", f"公司{i}") for i in range(14)]
    page = overview(tab="rest", cards=many)
    assert all(f"公司{i}" in page for i in range(14)) and "顯示其餘" not in page


def test_path_bars_show_counted_dates_in_the_cells():
    page = render.stock_html(stock_detail(card_of("2222")))   # 連 3 日列第 1 款：計入 11/19、11/20
    assert '<i class="on">11/19</i><i class="on">11/20</i><i class=""></i>' in page
    assert '">18</span>' in page and '">20</span>' in page   # 注意紀錄頭尾兩格：格子裡寫幾號（月份在上面）


def test_no_disposition_says_none():
    page = render.stock_html(stock_detail(card_of("1111")))   # 差 2 次，沒有處置
    section = page.split("<h3>處置紀錄")[1].split("<h3>")[0]
    assert '<p class="muted">無</p>' in section and "還沒有處置日期" not in page


def test_filter_counts_follow_the_tab():
    assert tab_count(overview(), "11/20 注意股") == 3           # 全部：甲、乙、丁
    assert tab_count(overview(tab="punish"), "11/20 注意股") == 0   # 處置中只有丙，今天沒被列
    assert tab_count(overview(tab="punish"), "明日出關") == 1
    assert tab_count(overview(tab="punish"), "全部") == 4          # 分頁數字照樣是當天總數


def test_card_tags_are_the_same_on_every_tab():
    tag = '<span class="pill violet">明日出關</span>'
    assert tag in overview() and tag in overview(tab="leave_tomorrow")


def test_notice_log_lists_each_listed_day():
    page = render.stock_html(stock_detail(card_of("2222")))   # 測試資料：11/20 被列第 1 款
    log = page.split("<h3>注意紀錄")[1].split("<h3>")[0]
    assert "近 30 個營業日 1 次" in log and "11/20" in log and "第 1 款・累積漲跌幅異常" in log and "計入本次累計" in log


def test_notice_log_shows_recent_days_and_folds_older(monkeypatch):
    """注意紀錄只先列最近幾天（views.RECENT_DAYS）的，更早的收在「顯示更早的 N 次」裡；原因都收著，點了才展開。"""
    monkeypatch.setattr(views, "RECENT_DAYS", 2)   # 11/19～11/20 算最近；11/18 收起來
    detail = stock_detail(card_of("2222"))
    detail["notice_history"] = [{"trade_date": d, "clauses": "1", "is_counted": 1, "reason_text": f"原因{d:%m%d}"}
                                for d in detail["trading_days"]]
    page = render.stock_html(detail)
    log = page[page.index("<h3>注意紀錄"):page.index("<h3>處置紀錄")]
    assert "近 30 個營業日 3 次" in log
    assert "顯示更早的 1 次" in log
    assert log.index("顯示更早的 1 次") > log.index("原因1119")      # 最近的在收起來的前面
    assert log.index("顯示更早的 1 次") < log.index("原因1118")
    assert '<details class="log" open' not in log                    # 原因全文預設收著


def test_announcements_show_recent_days_and_fold_older():
    """公開資訊只先列最近 7 天的，更早的收在「顯示更早的 N 則」裡。"""
    detail = stock_detail(card_of("2222"))
    detail["announcements"].append({"stock_id": "2222", "announce_date": DAY - datetime.timedelta(days=10),
                                    "announce_time": datetime.time(17, 0), "company_name": "乙公司", "subject": "十天前的公告"})
    page = render.stock_html(detail)
    anns = page[page.index('id="announcements"'):]
    assert "顯示更早的 1 則" in anns
    assert anns.index("顯示更早的 1 則") < anns.index("十天前的公告") and anns.index("盤前公告") < anns.index("顯示更早的 1 則")


def test_older_dispositions_are_folded():
    """以前的處置：最近 7 天沒碰到的收在「顯示更早的 N 次」裡。"""
    detail = stock_detail(card_of("2222"))
    detail["punish_history"] = [{
        "announce_date": datetime.date(2026, 11, 2), "stock_id": "2222", "stock_name": "乙公司",
        "trigger_condition": "連續三次", "period_start": datetime.date(2026, 11, 3),
        "period_end": datetime.date(2026, 11, 9), "measure_type": "第一次處置", "match_interval_minutes": 5, "content": ""}]
    page = render.stock_html(detail)
    timeline = page[page.index("<h3>處置紀錄"):]
    assert "顯示更早的 1 次" in timeline                      # 11/03～11/09 早於最近 7 天（11/14 起）
    assert timeline.index("顯示更早的 1 次") < timeline.index("11/03–11/09")


def test_clause_labels_are_colored_by_group():
    """類別標籤依條款分顏色：訴訟是 alert（紅）、財報是 finance（藍）、沒分類的是 other（灰）。"""
    detail = stock_detail(card_of("2222"))
    detail["announcements"] = [
        {"stock_id": "2222", "announce_date": DAY, "announce_time": datetime.time(17, 0), "subject": "收到法院執行命令",
         "announce_clause": "第2款", "clause_name": "訴訟、行政處分"},
        {"stock_id": "2222", "announce_date": DAY, "announce_time": datetime.time(16, 0), "subject": "董事會通過財報",
         "announce_clause": "第31款", "clause_name": "財務報告"}]
    page = render.stock_html(detail)
    assert '<span class="ann-cat alert">訴訟、行政處分</span>' in page
    assert '<span class="ann-cat finance">財務報告</span>' in page


def test_glossary_sits_next_to_the_timeline_heading():
    """有處置時，「預收、撮合是什麼？」在處置紀錄標題旁邊（時程的框前面）；沒有處置就不出現。"""
    page = render.stock_html(stock_detail(card_of("3333")))   # 處置中
    timeline = page[page.index("<h3>處置紀錄"):]
    assert "預收、撮合是什麼？" in timeline
    assert timeline.index("預收、撮合是什麼？") < timeline.index('class="box"')
    assert "預收、撮合是什麼？" not in render.stock_html(stock_detail(card_of("5555")))   # 沒東西可看


def test_recent_disposition_shows_without_folding():
    """以前的處置如果處置期間碰到最近 7 天，直接列出來，不收起來。"""
    detail = stock_detail(card_of("2222"))
    detail["punish_history"] = [{
        "announce_date": datetime.date(2026, 11, 9), "stock_id": "2222", "stock_name": "乙公司",
        "trigger_condition": "連續三次", "period_start": datetime.date(2026, 11, 10),
        "period_end": datetime.date(2026, 11, 16), "measure_type": "第一次處置", "match_interval_minutes": 5, "content": ""}]
    timeline = render.stock_html(detail)
    timeline = timeline[timeline.index("<h3>處置紀錄"):]
    assert "11/10–11/16" in timeline
    assert "顯示更早的" not in timeline


def test_ai_summary_block_above_notice_log():
    """AI 摘要在注意紀錄上面；還沒產生就寫一行說明。"""
    page = render.stock_html(stock_detail(card_of("2222")))
    assert page.index("AI 摘要") < page.index("注意紀錄")
    assert "今天的 AI 摘要還沒產生" in page


def test_ai_summary_shows_text_and_sources():
    """有摘要時顯示全文和新聞來源連結（標題要跳脫）。"""
    detail = stock_detail(card_of("2222"))
    detail["ai_summary"] = {"summary": "今天因第一款被列注意。",   # API 的格式：出處是 stage_news 的欄位
                            "sources": [{"title": "訂單<增加>", "link": "https://example.com/n1", "publisher": "經濟日報",
                                         "news_date": datetime.date(2026, 11, 19)}]}
    page = render.stock_html(detail)
    assert "今天因第一款被列注意。" in page and "還沒產生" not in page
    assert 'href="https://example.com/n1"' in page and "訂單&lt;增加&gt;" in page


def test_news_block_below_announcements():
    """新聞資訊在公開資訊下面；沒有新聞就寫一行說明。"""
    page = render.stock_html(stock_detail(card_of("2222")))
    assert page.index('id="announcements"') < page.index('id="news"')
    assert "11/20 和前幾天都沒有新聞" in page


def test_news_block_lists_titles_with_links():
    detail = stock_detail(card_of("2222"))
    """當天的新聞先列，前幾天的收在「顯示前幾天的…」。"""
    detail["news"] = [{"title": "今天<大單>", "link": "https://example.com/b", "publisher": "UDN",
                       "news_date": datetime.date(2026, 11, 20), "also_reported_by": ["自由時報"]},
                      {"title": "接獲<大單>", "link": "https://example.com/a", "publisher": "經濟日報",
                       "news_date": datetime.date(2026, 11, 19)}]
    page = render.stock_html(detail)
    news = page.split('id="news"')[1].split("</section>")[0]
    assert "近 7 天" in news and "今天&lt;大單&gt;" in news and "也有 自由時報 報導" in news
    today, older = news.split("顯示前幾天的 1 則")
    assert "今天&lt;大單&gt;" in today and 'href="https://example.com/a"' in older and "接獲&lt;大單&gt;" in older


def test_rest_card_bar_matches_the_headline():
    """進度條放大字「差 3 次」的那條：30 日內 4/12 雖然進度最多，但還差 8 次，和「差 3 次」對不起來，不放。"""
    rest = make_card("7777", "庚公司", streak3=0, streak5=1, in10=1, in30=4, steps_left=3, closest_path="streak3",
                     streak3_left=3, streak5_left=4, in10_left=5, in30_left=8)
    card = overview(cards=[rest]).split("庚公司")[1].split("</article>")[0]
    assert "差 3 次" in card and '<span class="progress-label">連 3 日列第 1 款</span>' in card
    assert "30 日內" not in card


def test_rest_row_without_counted_days():
    nothing = make_card("7777", "庚公司", streak3=0, streak5=0, in10=0, in30=0, steps_left=3, closest_path="streak3",
                        clauses="9", is_counted_today=0)   # 今天只被列第 9 款
    card = overview(tab="rest", cards=[nothing]).split("庚公司")[1].split("</article>")[0]
    assert 'class="bar"' in card and "今天被列的款不算次數" in card   # 空的進度條照樣放，和其他卡片一樣


def test_card_has_footer_with_close_and_announcements():
    page = overview()
    assert 'class="card-foot"' in page and "公告 1 則 ›" in page


def test_leave_today_is_a_normal_card_with_a_small_tag():
    """11/20 出關和一般卡片一樣：大字寫差幾次、有進度條，只多一個「11/20 出關」小標籤；排序也和一般卡片一樣。"""
    left = make_card("8888", "辛公司", steps_left=3, resume_date=DAY, streak5=2, streak5_left=3, in10=2, in30=2,
                     period_start=datetime.date(2026, 11, 6), period_end=datetime.date(2026, 11, 19))
    page = overview(cards=[left])
    card = page.split("辛公司")[1].split("</article>")[0]
    assert '<span class="sub-tag ok">11/20 出關</span>' in card and "差 3 次" in card and 'class="bar"' in card


def test_rest_is_sorted_by_steps_then_how_full_the_bar_is():
    """「其他」：差 2 次的在前；一樣差幾次，進度條填得多的（看格子比例）在前。11/20 出關不特別排前面。"""
    two = make_card("5001", "差二", steps_left=2, streak5=3, streak5_left=2, streak3_left=3, in10_left=4, in30_left=9)
    full = make_card("5002", "填多", steps_left=3, streak3=0, streak3_left=3, streak5=2, streak5_left=3, in10_left=4, in30_left=9)
    empty = make_card("5003", "空的", steps_left=3, streak3=0, streak3_left=3, streak5=0, streak5_left=5, in10=1, in30=1,
                      in10_left=5, in30_left=11, resume_date=DAY)
    page = overview(cards=[empty, full, two])
    assert page.index("差二") < page.index("填多") < page.index("空的")


def test_secondary_tags_are_sub_tags_in_the_header():
    page = overview(tab="leave_tomorrow")   # 丙公司：處置最後一天、第二次
    head = page[page.index("丙公司"):].split("</div>")[0]
    # 小框在左、色塊在右
    assert '<span class="sub-tag mut">第二次處置</span><span class="pill violet">明日出關</span>' in head and "處置中" not in head


def test_leave_today_listed_again_shows_sub_tag_and_bar():
    """11/20 出關又被列、差 1 次：主要標籤是聽牌，「11/20 出關」「11/20 注意股」是小標籤；大字差 1 次，進度條放差 1 次的那條。"""
    again = make_card("8888", "辛公司", resume_date=DAY, is_listed_today=1, clauses="1", is_counted_today=1,
                      period_start=datetime.date(2026, 11, 6), period_end=datetime.date(2026, 11, 19),
                      steps_left=1, streak3=2, streak3_left=1, closest_path="streak3")
    card = overview(cards=[again]).split("辛公司")[1].split("</article>")[0]
    assert ('<span class="sub-tag ok">11/20 出關</span><span class="sub-tag acc">11/20 注意股</span>'
            '<span class="pill amber">聽牌</span>') in card
    assert "差 1 次" in card and card.count('<i class="on">') == 2 and "連 3 日列第 1 款" in card


def test_card_bar_counts_what_is_left_when_old_days_slide_out():
    """10 日內 4/6，但最早的 1 次達標前會滑出 10 日 → 還差 3 次：卡片填 3 格、空 3 格，和大字「差 3 次」一樣；個股頁寫原因。"""
    card = make_card("7777", "庚公司", streak3=0, streak5=0, in10=4, in30=4, steps_left=3, closest_path="streak3",
                     streak3_left=3, streak5_left=5, in10_left=3, in30_left=8)
    html = overview(cards=[card]).split("庚公司")[1].split("</article>")[0]
    assert "差 3 次" in html and "10 日內被列 6 日" in html
    assert html.count('<i class="on">') == 3 and html.count("<i class=\"\">") == 3
    stock = render.stock_html(stock_detail(card))
    assert "10 日內被列 6 日：最早計入的 1 次在達標前會超出期間，所以還差 3 次。" in stock


def test_listed_today_cards_get_a_tag():
    page = overview()   # 甲、乙、丁今天被列注意，丙沒有
    for name, listed in (("甲公司", True), ("乙公司", True), ("丁公司", True), ("丙公司", False)):
        card = page.split(name)[1].split("</article>")[0]
        assert ('<span class="sub-tag acc">11/20 注意股</span>' in card) == listed, name


def test_card_color_matches_the_main_tag():
    """卡片上緣色條和主要標籤同色：明日出關紫、聽牌琥珀、即將處置紅框。"""
    page = overview()
    for name, tone, pill in (("丙公司", "violet", "violet"), ("乙公司", "amber", "amber"), ("丁公司", "orange", "orange")):
        start = page.rindex("<article", 0, page.index(name))
        card = page[start:page.index("</article>", start)]
        assert f'<article class="card tone-{tone}">' in card and f'<span class="pill {pill}">' in card, name


def test_stock_hero_uses_the_card_layout():
    """個股頁最上面和總覽卡片同一個版面：標籤在名稱右邊（小框在左、色塊在右）、進度條、底部收盤價｜公告。"""
    page = render.stock_html(stock_detail(card_of("2222")))   # 乙公司：聽牌、今天被列
    hero = page[page.index('<div class="hero'):page.index("AI 摘要")]
    assert '<div class="card-tags"><span class="sub-tag acc">11/20 注意股</span><span class="pill amber">聽牌</span></div>' in hero
    assert 'class="progress"' in hero and 'class="card-foot"' in hero and 'href="#announcements"' in hero


def test_card_shows_change_next_to_close():
    """收盤價旁邊寫漲跌幅：漲紅（up）、跌綠（down）；沒有漲跌幅就只寫收盤價。"""
    up = make_card("7777", "庚公司", close_price=725.0, change_pct=3.5)
    down = make_card("8888", "辛公司", close_price=50.0, change_pct=-1.2)
    page = overview(cards=[up, down, make_card("9999", "壬公司", close_price=10.0)])
    assert '725.00</span> <span class="chg up mono">▲3.50%</span>' in page
    assert '<span class="chg down mono">▼1.20%</span>' in page
    assert page.split("壬公司")[1].split("</article>")[0].count("chg ") == 0
