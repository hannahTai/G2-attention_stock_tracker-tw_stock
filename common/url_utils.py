"""網址的小工具：crawler 打開網頁前（crawler/client.py）、網頁顯示外部連結前（dashboard/views.py）都用同一個規則。"""
import urllib.parse


def is_http_url(url: str | None) -> bool:
    """網址是不是 http、https（不是 javascript:、file:、data: 這類點了會執行程式、或讀本機檔案的）。

    >>> is_http_url("https://udn.com/1"), is_http_url(" HTTP://a.com "), is_http_url("javascript:alert(1)"), is_http_url(None)
    (True, True, False, False)
    """
    return urllib.parse.urlsplit((url or "").strip()).scheme.lower() in ("http", "https")
