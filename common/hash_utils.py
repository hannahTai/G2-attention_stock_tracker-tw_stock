"""雜湊工具：好幾層（crawler、transform、資料庫）都要算、而且要算得一模一樣的代號，例如新聞的 news_id。"""
import hashlib


def url_md5(url: str) -> str:
    """網址（去掉前後空白）的 md5，32 個字。新聞的 news_id 就是新聞連結的 url_md5：
    連結太長（最長好幾百字）不好當主鍵；同一個網址永遠是同一個值，資料庫裡也能算（SQL 的 md5(link)，stage_news 的 CHECK 用它檢查）。

    >>> url_md5("https://udn.com/news/1"), url_md5(" https://udn.com/news/1 ") == url_md5("https://udn.com/news/1")
    ('5583cdf4c2064913b0e08ee9e90f450e', True)
    """
    return hashlib.md5(url.strip().encode("utf-8")).hexdigest()
