"""統計頁的圖：只算座標（SVG 的 viewBox 單位），不碰 HTML；畫法在 templates/_charts.html。不碰網路，可以單獨測試。

  line_chart()    折線圖：每日家數、第 13 款（每天一個點，可以好幾條線）
  column_chart()  直條圖：新增處置（每天或每週一根，一條平均線）

網頁沒有 JavaScript：數字直接標在圖上（線的尾端、平均線）；點一下（或滑鼠移上去）顯示那天的說明卡片（tip，CSS 做的）。
顏色用 kind（accent、amber、seal…），範本把它換成 style.css 的顏色變數，深色模式自動跟著換。
"""
import dataclasses
import datetime

WIDTH = 1000                     # viewBox 寬；實際顯示的寬度由 CSS 決定（撐滿框）
PAD_LEFT, PAD_RIGHT = 34, 120    # 左邊放 y 軸數字、右邊放線尾端的名稱和數字（名稱最長約 4 個字＋數字）
PAD_TOP, PAD_BOTTOM = 18, 30     # 上面留給說明卡片、下面放月份
END_LABEL_GAP = 15               # 線尾端的標籤至少隔這麼多，不重疊
TIP_WIDTH = 150                  # 點一下顯示的說明卡片：寬、第一行（日期）和每一行的高度
TIP_LINE = 20
TIP_GAP = 10                     # 卡片和那天的直線隔多遠


def nice_max(value: float, step: int) -> int:
    """y 軸最上面：最大值往上取到 step 的倍數；全是 0 時也留一格。

    >>> nice_max(54, 10), nice_max(11, 5), nice_max(0, 10)
    (60, 15, 10)
    """
    return max(step, -(-int(value) // step) * step)


@dataclasses.dataclass
class Axis:
    """x、y 換算：x 是第幾個點，y 是數值。"""
    count: int
    y_max: int
    height: int
    width: int = WIDTH

    def x(self, i: float) -> float:
        span = self.width - PAD_LEFT - PAD_RIGHT
        return PAD_LEFT + (span * i / (self.count - 1) if self.count > 1 else span / 2)

    def y(self, value: float) -> float:
        return PAD_TOP + (1 - value / self.y_max) * (self.height - PAD_TOP - PAD_BOTTOM)

    @property
    def bottom(self) -> float:
        return self.height - PAD_BOTTOM


def y_ticks(axis: Axis, step: int) -> list[dict]:
    """y 軸的格線：每 step 一條。

    >>> [t["label"] for t in y_ticks(Axis(10, 20, 200), 10)]
    [0, 10, 20]
    """
    return [{"label": v, "y": round(axis.y(v), 1)} for v in range(0, axis.y_max + 1, step)]


def date_ticks(days: list[datetime.date], x, count: int = 7) -> list[dict]:
    """x 軸：平均標大約 count 個日期（月/日），第一天、最後一天一定標；x 是「第幾個 → x 座標」的函式。
    anchor：第一個靠左對齊、最後一個靠右對齊，字才不會超出圖；其他置中。最後一個和前一個太近就拿掉前一個。

    >>> days = [datetime.date(2026, 10, 14) + datetime.timedelta(days=i) for i in range(30)]
    >>> [t["label"] for t in date_ticks(days, lambda i: i * 10)]
    ['10/14', '10/19', '10/24', '10/29', '11/03', '11/08', '11/12']
    >>> [t["anchor"] for t in date_ticks(days[:2], lambda i: i)]
    ['start', 'end']
    """
    if not days:
        return []
    step = max(1, -(-(len(days) - 1) // (count - 1)))   # 無條件進位，標的數量不超過 count
    picks = list(range(0, len(days), step))
    last = len(days) - 1
    if picks[-1] != last:
        if last - picks[-1] < step / 2 and len(picks) > 1:
            picks.pop()   # 最後一個標籤太近，讓給最後一天
        picks.append(last)
    return [{"label": f"{days[i]:%m/%d}", "x": round(x(i), 1),
             "anchor": "start" if i == 0 else "end" if i == last else "middle"} for i in picks]


def spread_labels(ys: list[float], gap: float, bottom: float, top: float = PAD_TOP) -> list[float]:
    """線尾端的標籤：照 y 排好，太近的往下推，至少隔 gap；最下面的超過 bottom 就整組往上移，但不高過 top（照原本的順序回傳）。

    >>> spread_labels([100, 104, 50], 15, 250)
    [100, 115, 50]
    >>> spread_labels([249, 250], 15, 250)   # 都在 0 附近：往上移，不超出圖
    [235, 250]
    >>> spread_labels([30, 30, 30], 15, 40, top=18)   # 往上移到超過 top 的停在 top
    [18, 25, 40]
    """
    order = sorted(range(len(ys)), key=lambda i: ys[i])
    result = list(ys)
    for prev, cur in zip(order, order[1:], strict=False):
        result[cur] = max(result[cur], result[prev] + gap)
    overflow = max(0, max(result, default=0) - bottom)
    return [max(top, y - overflow) for y in result]


def tip(center: float, top: float, title: str, rows: list[dict], width: int = WIDTH) -> dict:
    """點一下（或滑鼠移上去）顯示的說明卡片：放在那天直線的右邊，太靠右就放左邊。rows：[{label, value, kind}]。

    >>> card = tip(900, 18, "11/26", [{"label": "注意股", "value": 19, "kind": "accent"}])
    >>> card["x"] < 900, card["height"], card["rows"][0]["y"]
    (True, 52, 66)
    """
    x, side = center + TIP_GAP, "right"
    if x + TIP_WIDTH > width - 4:
        x, side = center - TIP_GAP - TIP_WIDTH, "left"
    y = top + 4
    return {"x": round(x, 1), "y": round(y, 1), "width": TIP_WIDTH, "height": 12 + TIP_LINE * (len(rows) + 1), "side": side,
            "title": title, "title_y": round(y + 24, 1),
            "rows": [dict(r, y=round(y + 24 + TIP_LINE * (i + 1), 1)) for i, r in enumerate(rows)]}


def line_chart(days: list[datetime.date], series: list[dict], height: int = 280, step: int = 10,
               width: int = WIDTH) -> dict:
    """折線圖。series：[{name, kind, values}]，values 和 days 一樣長。
    width：viewBox 寬；並排的小圖用窄一點的（例如 560），畫出來的字才不會縮得太小。

    回傳範本要的東西：viewBox 寬高、y 格線、月份、每條線的 points、尾端標籤、
    每天一條看不見的直條（hits）：點一下或滑鼠移上去，畫那天的直線、每條線上的點，和說明卡片（tip）。

    >>> chart = line_chart([datetime.date(2026, 11, 25), datetime.date(2026, 11, 26)],
    ...                    [{"name": "注意股", "kind": "accent", "values": [14, 19]}])
    >>> chart["lines"][0]["end"]["text"], chart["hits"][1]["label"], chart["hits"][1]["tip"]["rows"][0]["value"]
    ('19', '11/26：注意股 19 檔', 19)
    """
    axis = Axis(len(days), nice_max(max((max(s["values"], default=0) for s in series), default=0), step), height, width)
    lines = []
    for s in series:
        points = " ".join(f"{axis.x(i):.1f},{axis.y(v):.1f}" for i, v in enumerate(s["values"]))
        last = s["values"][-1] if s["values"] else 0
        lines.append({"name": s["name"], "kind": s["kind"], "points": points,
                      "end": {"x": round(axis.x(len(days) - 1), 1), "y": round(axis.y(last), 1), "text": str(last)}})
    for line, label_y in zip(lines, spread_labels([line["end"]["y"] for line in lines], END_LABEL_GAP, axis.bottom), strict=True):
        line["end"]["label_y"] = round(label_y, 1)
    step_x = axis.x(1) - axis.x(0) if len(days) > 1 else width - PAD_LEFT - PAD_RIGHT
    hits = [{"x": round(axis.x(i) - step_x / 2, 1), "width": round(step_x, 1), "center": round(axis.x(i), 1),
             "label": f"{d:%m/%d}：" + "、".join(f"{s['name']} {s['values'][i]} 檔" for s in series),
             "points": [{"y": round(axis.y(s["values"][i]), 1), "kind": s["kind"]} for s in series],
             "tip": tip(axis.x(i), PAD_TOP, f"{d:%m/%d}（週{'一二三四五六日'[d.weekday()]}）",
                        [{"label": s["name"], "value": s["values"][i], "kind": s["kind"]} for s in series], width)}
            for i, d in enumerate(days)]
    return {
        "width": width, "height": height, "top": PAD_TOP, "bottom": axis.bottom,
        "left": PAD_LEFT, "right": width - PAD_RIGHT, "y_ticks": y_ticks(axis, step),
        "x_ticks": date_ticks(days, axis.x, 7 if width >= WIDTH else 4), "lines": lines, "hits": hits,
    }


def column_chart(weeks: list[datetime.date], counts: list[int], height: int = 200, step: int = 10,
                 complete: int | None = None, period: str = "week", first: int = 0) -> dict:
    """直條圖，一條平均線標「平均 N」。period＝week：一週一根（weeks 是每週的星期一）；
    day：一天一根（weeks 是交易日）。橫軸平均標幾個日期（每週標星期一）。
    只有第 first～complete 根是完整的：前面的（期間從週中開始，第一週不滿一週）、
    後面的（這週還沒過完、已公布還沒開始的）照樣畫（淡色），但不算進平均。

    >>> chart = column_chart([datetime.date(2026, 8, 10), datetime.date(2026, 8, 17)], [6, 1], complete=1)
    >>> [b["partial"] for b in chart["bars"]], chart["average"]["text"], chart["bars"][1]["tip"]["title"]
    ([False, True], '平均 6.0', '08/17～08/21（未滿一週）')
    >>> column_chart([datetime.date(2026, 11, 27)], [1], period="day")["bars"][0]["label"]
    '11/27：新增處置 1 檔'
    >>> chart = column_chart([datetime.date(2026, 10, 12), datetime.date(2026, 10, 19)], [1, 4], first=1)
    >>> [b["partial"] for b in chart["bars"]], chart["average"]["text"]
    ([True, False], '平均 4.0')
    """
    week = period == "week"

    def when(w: datetime.date) -> str:
        return f"{w:%m/%d}～{w + datetime.timedelta(days=4):%m/%d}" if week else f"{w:%m/%d}（週{'一二三四五六日'[w.weekday()]}）"

    axis = Axis(len(weeks), nice_max(max(counts, default=0), step), height)
    slot = (WIDTH - PAD_LEFT - PAD_RIGHT) / max(len(weeks), 1)
    full = len(weeks) if complete is None else complete
    partial = [i < first or i >= full for i in range(len(weeks))]
    bars = [{"x": round(PAD_LEFT + i * slot + 2, 1), "y": round(axis.y(c), 1), "width": round(slot - 4, 1),
             "height": round(axis.y(0) - axis.y(c), 1), "partial": partial[i],
             "slot_x": round(PAD_LEFT + i * slot, 1), "slot_width": round(slot, 1),
             "label": f"{w:%m/%d}{'那週' if week else ''}：新增處置 {c} 檔" + ("（未滿一週，不計入平均）" if week and partial[i] else ""),
             "tip": tip(PAD_LEFT + (i + 0.5) * slot, PAD_TOP,
                        when(w) + (("（未滿一週）" if week else "（尚未開始）") if partial[i] else ""),
                        [{"label": "新增處置", "value": c, "kind": "seal"}])}
            for i, (w, c) in enumerate(zip(weeks, counts, strict=True))]
    part = counts[first:full]
    avg = sum(part) / len(part) if part else None
    average = ({"x1": round(PAD_LEFT + first * slot, 1), "x2": round(PAD_LEFT + full * slot, 1), "y": round(axis.y(avg), 1),
                "value": avg, "text": f"平均 {avg:.1f}"} if avg is not None else None)
    x_ticks = date_ticks(weeks, lambda i: PAD_LEFT + (i + 0.5) * slot)   # 標在直條中間；每週標那週的星期一
    return {
        "width": WIDTH, "height": height, "top": PAD_TOP, "bottom": axis.bottom,
        "left": PAD_LEFT, "right": WIDTH - PAD_RIGHT, "y_ticks": y_ticks(axis, step), "x_ticks": x_ticks,
        "bars": bars, "average": average,
    }
