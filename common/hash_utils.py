"""雜湊工具：好幾層（crawler、transform、ai、資料庫）都要算、而且要算得一模一樣的代號，例如新聞的 news_id、重大訊息的 announcement_id。"""
import hashlib


def url_md5(url: str) -> str:
    """網址（去掉前後空白）的 md5，32 個字。新聞的 news_id 就是新聞連結的 url_md5：
    連結太長（最長好幾百字）不好當主鍵；同一個網址永遠是同一個值，資料庫裡也能算（SQL 的 md5(link)，stage_news 的 CHECK 用它檢查）。

    >>> url_md5("https://udn.com/news/1"), url_md5(" https://udn.com/news/1 ") == url_md5("https://udn.com/news/1")
    ('5583cdf4c2064913b0e08ee9e90f450e', True)
    """
    return hashlib.md5(url.strip().encode("utf-8")).hexdigest()


def text_md5(text: str) -> str:
    """一段文字的 md5（不去空白），用來比對內容有沒有變，例如新聞向量的 input_hash（文字變了才重轉）。

    >>> text_md5("健策營收創高") == text_md5("健策營收創高"), text_md5("健策營收創高") == text_md5("健策營收創高 ")
    (True, False)
    """
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def key_md5(*parts) -> str:
    """好幾欄的主鍵 → 一個代號：用「_」接起來再取 md5，32 個字。重大訊息的 announcement_id 就是這個：
    （公司, 輸入日期, 序號）＝ 9908、20261102、1 → md5("9908_20261102_1")，和 raw_announcement_detail 的 raw_key 同一個字串。

    >>> key_md5("9908", "20261102", 1) == url_md5("9908_20261102_1")
    True
    """
    return url_md5("_".join(str(part) for part in parts))
