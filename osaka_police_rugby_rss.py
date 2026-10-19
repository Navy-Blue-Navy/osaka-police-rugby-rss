import requests
import re
import json
import time
import hashlib
import html
import os
import xml.etree.ElementTree as ET

from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
from urllib.parse import urlencode


# ============================================================
# 設定
# ============================================================

SEARCH_WORD = "ラグビー"

CX = "008697376049680232758:v-f6hw84hiu"

SEARCH_PAGE = (
    "https://www.police.pref.osaka.lg.jp/result.html"
    "?q=%E3%83%A9%E3%82%B0%E3%83%93%E3%83%BC"
)

CSE_JS_URL = "https://cse.google.com/cse.js"
CSE_API_URL = "https://cse.google.com/cse/element/v1"

OUTPUT_FILE = "osaka_police_rugby.xml"

FEED_TITLE = "大阪府警「ラグビー」検索結果"
FEED_DESCRIPTION = (
    "大阪府警察ウェブサイト内で「ラグビー」にヒットするページの更新監視RSS"
)

JST = timezone(timedelta(hours=9))

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/154.0.0.0 Safari/537.36"
)


# ============================================================
# HTTP
# ============================================================

session = requests.Session()

HEADERS = {
    "User-Agent": USER_AGENT,
    "Referer": SEARCH_PAGE,
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}


def get_with_retry(url, params=None, retries=3, timeout=30):
    last_error = None

    for attempt in range(1, retries + 1):
        try:
            response = session.get(
                url,
                params=params,
                headers=HEADERS,
                timeout=timeout,
            )

            print(
                f"GET {response.url[:180]}"
                f"{'...' if len(response.url) > 180 else ''}"
            )
            print(
                f"HTTP: {response.status_code} / "
                f"{len(response.content)} bytes"
            )

            if response.status_code == 200:
                return response

            last_error = RuntimeError(
                f"HTTP {response.status_code}: {response.url}"
            )

        except Exception as e:
            last_error = e
            print(
                f"取得失敗 "
                f"({attempt}/{retries}): "
                f"{type(e).__name__}: {e}"
            )

        if attempt < retries:
            time.sleep(10)

    raise RuntimeError(
        f"取得に失敗しました: {last_error}"
    )


# ============================================================
# cse.js の設定取得
# ============================================================

def extract_cse_config():
    response = get_with_retry(
        CSE_JS_URL,
        params={"cx": CX},
    )

    text = response.text

    token_match = re.search(
        r'"cse_token"\s*:\s*"([^"]+)"',
        text,
    )

    if not token_match:
        raise RuntimeError(
            "cse.js から cse_token を取得できませんでした"
        )

    token = token_match.group(1)

    # cse_token の近辺にある設定ブロックを使用する。
    token_pos = token_match.start()

    block_start = max(0, token_pos - 5000)
    block_end = min(len(text), token_pos + 5000)

    block = text[block_start:block_end]

    version_match = re.search(
        r'"cselibVersion"\s*:\s*"([^"]+)"',
        block,
    )

    exp_match = re.search(
        r'"exp"\s*:\s*(\[[^\]]*\])',
        block,
    )

    fexp_match = re.search(
        r'"fexp"\s*:\s*(\[[^\]]*\])',
        block,
    )

    if not version_match:
        raise RuntimeError(
            "cse.js から cselibVersion を取得できませんでした"
        )

    if not exp_match:
        raise RuntimeError(
            "cse.js から exp を取得できませんでした"
        )

    if not fexp_match:
        raise RuntimeError(
            "cse.js から fexp を取得できませんでした"
        )

    version = version_match.group(1)

    exp = json.loads(exp_match.group(1))
    fexp = json.loads(fexp_match.group(1))

    print("CSE設定取得成功")
    print("cselibVersion:", version)
    print("exp:", exp)
    print("fexp:", fexp)

    return {
        "token": token,
        "version": version,
        "exp": exp,
        "fexp": fexp,
    }


# ============================================================
# JSONP解析
# ============================================================

def parse_jsonp(text):
    start = text.find("(")
    end = text.rfind(")")

    if start == -1 or end == -1 or end <= start:
        raise RuntimeError(
            "Google検索結果のJSONPを解析できませんでした"
        )

    payload = text[start + 1:end].strip()

    try:
        return json.loads(payload)

    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"Google検索結果JSONの解析に失敗しました: {e}"
        )


# ============================================================
# Google Custom Search
# ============================================================

def fetch_search_page(config, start):
    params = {
        "rsz": "filtered_cse",
        "num": "10",
        "hl": "ja",
        "source": "gcsc",
        "cselibv": config["version"],
        "cx": CX,
        "q": SEARCH_WORD,
        "safe": "off",
        "cse_tok": config["token"],
        "sort": "",
        "exp": ",".join(config["exp"]),
        "fexp": ",".join(
            str(x) for x in config["fexp"]
        ),
        "callback": "google.search.cse.api10051",
        "rurl": SEARCH_PAGE,
        "start": str(start),
    }

    response = get_with_retry(
        CSE_API_URL,
        params=params,
    )

    return parse_jsonp(response.text)


def fetch_all_results():
    config = extract_cse_config()

    all_items = {}
    estimated_count = None

    # Google CSE の通常検索結果は最大100件程度まで
    # ページングできるため start=0～90 を確認する。
    for start in range(0, 100, 10):
        print()
        print(
            f"検索結果取得: start={start}"
        )

        data = fetch_search_page(
            config,
            start,
        )

        cursor = data.get("cursor", {})

        if estimated_count is None:
            estimated_count = (
                cursor.get("resultCount")
                or cursor.get(
                    "estimatedResultCount"
                )
            )

            print(
                "Google表示上の検索結果数:",
                estimated_count,
            )

        results = data.get("results") or []

        print(
            f"このページの取得件数: "
            f"{len(results)}件"
        )

        if not results:
            break

        new_on_page = 0

        for item in results:
            url = (
                item.get("unescapedUrl")
                or item.get("url")
                or ""
            ).strip()

            title = (
                item.get("titleNoFormatting")
                or item.get("title")
                or ""
            ).strip()

            description = (
                item.get("contentNoFormatting")
                or ""
            ).strip()

            if not url or not title:
                continue

            # 大阪府警ドメインだけを採用
            if not url.startswith(
                "https://www.police.pref.osaka.lg.jp/"
            ):
                continue

            title = html.unescape(title)
            description = html.unescape(
                description
            )

            if url not in all_items:
                all_items[url] = {
                    "title": title,
                    "url": url,
                    "description": description,
                }
                new_on_page += 1

        print(
            f"有効URL追加: {new_on_page}件"
        )

        # 10件未満なら最終ページと判断
        if len(results) < 10:
            break

        # Googleへの連続アクセスを少し空ける
        time.sleep(1)

    print()
    print(
        f"今回取得できたユニークURL: "
        f"{len(all_items)}件"
    )

    return list(all_items.values())


# ============================================================
# 既存RSS読込
# ============================================================

def load_existing_feed():
    existing = {}

    if not os.path.exists(OUTPUT_FILE):
        print("既存RSS件数: 0")
        return existing

    try:
        tree = ET.parse(OUTPUT_FILE)
        root = tree.getroot()

        channel = root.find("channel")

        if channel is None:
            print("既存RSS件数: 0")
            return existing

        for item in channel.findall("item"):
            title = item.findtext(
                "title",
                default="",
            ).strip()

            link = item.findtext(
                "link",
                default="",
            ).strip()

            description = item.findtext(
                "description",
                default="",
            ).strip()

            pub_date = item.findtext(
                "pubDate",
                default="",
            ).strip()

            guid = item.findtext(
                "guid",
                default="",
            ).strip()

            if not link:
                continue

            existing[link] = {
                "title": title,
                "url": link,
                "description": description,
                "pubDate": pub_date,
                "guid": guid,
            }

    except Exception as e:
        raise RuntimeError(
            f"既存RSSの読み込みに失敗しました: {e}"
        )

    print(
        f"既存RSS件数: {len(existing)}"
    )

    return existing


# ============================================================
# RSS生成
# ============================================================

def make_guid(url):
    return hashlib.sha256(
        url.encode("utf-8")
    ).hexdigest()


def build_rss(items):
    now = datetime.now(JST)

    rss = ET.Element(
        "rss",
        version="2.0",
    )

    channel = ET.SubElement(
        rss,
        "channel",
    )

    ET.SubElement(
        channel,
        "title",
    ).text = FEED_TITLE

    ET.SubElement(
        channel,
        "link",
    ).text = SEARCH_PAGE

    ET.SubElement(
        channel,
        "description",
    ).text = FEED_DESCRIPTION

    ET.SubElement(
        channel,
        "language",
    ).text = "ja"

    ET.SubElement(
        channel,
        "lastBuildDate",
    ).text = format_datetime(now)

    for data in items:
        item = ET.SubElement(
            channel,
            "item",
        )

        ET.SubElement(
            item,
            "title",
        ).text = data["title"]

        ET.SubElement(
            item,
            "link",
        ).text = data["url"]

        ET.SubElement(
            item,
            "description",
        ).text = data.get(
            "description",
            "",
        )

        ET.SubElement(
            item,
            "pubDate",
        ).text = data["pubDate"]

        guid = ET.SubElement(
            item,
            "guid",
            isPermaLink="false",
        )

        guid.text = data["guid"]

    tree = ET.ElementTree(rss)

    try:
        ET.indent(
            tree,
            space="  ",
        )
    except AttributeError:
        pass

    tree.write(
        OUTPUT_FILE,
        encoding="utf-8",
        xml_declaration=True,
    )


# ============================================================
# メイン
# ============================================================

def main():
    existing = load_existing_feed()

    try:
        current = fetch_all_results()

    except Exception:
        print()
        print(
            "検索結果取得に失敗したため、"
            "既存RSSは変更しません。"
        )
        raise

    if not current:
        raise RuntimeError(
            "検索結果が0件だったため、"
            "異常と判断してRSSを変更しません"
        )

    now = datetime.now(JST)

    merged = dict(existing)

    new_count = 0

    for data in current:
        url = data["url"]

        if url in existing:
            # タイトルや説明文は現在値へ更新するが、
            # 初回検出日時は維持する。
            old = existing[url]

            merged[url] = {
                "title": data["title"],
                "url": url,
                "description": data[
                    "description"
                ],
                "pubDate": old["pubDate"],
                "guid": (
                    old["guid"]
                    or make_guid(url)
                ),
            }

        else:
            merged[url] = {
                "title": data["title"],
                "url": url,
                "description": data[
                    "description"
                ],
                "pubDate": format_datetime(now),
                "guid": make_guid(url),
            }

            new_count += 1

    def sort_key(item):
        pub_date = item.get(
            "pubDate",
            "",
        )

        try:
            from email.utils import (
                parsedate_to_datetime,
            )

            return parsedate_to_datetime(
                pub_date
            )

        except Exception:
            return datetime.min.replace(
                tzinfo=timezone.utc
            )

    items = sorted(
        merged.values(),
        key=sort_key,
        reverse=True,
    )

    print()
    print(
        f"今回の検索結果: {len(current)}件"
    )
    print(
        f"新規URL: {new_count}件"
    )
    print(
        f"累積RSS件数: {len(items)}件"
    )

    print()
    print("--- RSS上位30件 ---")

    for i, item in enumerate(
        items[:30],
        start=1,
    ):
        print(
            f"{i}. {item['title']}"
        )
        print(
            f"   {item['url']}"
        )

    build_rss(items)

    print()
    print(
        f"保存完了: "
        f"{OUTPUT_FILE} "
        f"({len(items)}件)"
    )


if __name__ == "__main__":
    main()