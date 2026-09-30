"""
汎用 在庫監視 → LINE通知スクリプト

2つの監視方式を持つ。
1. 一覧ページ方式: MONITOR_URLS_JSON のページを取得し、KEYWORDS_JSON に一致する
   商品名を自動検出して監視する(一覧ページがHTMLで静的に商品を出力するサイト向け)。
2. 個別ページ方式: SINGLE_PAGE_TARGETS_JSON に列挙した個別の商品ページを直接取得し、
   ページ内に「売り切れマーカー文言」が含まれるかどうかで在庫を判定する
   (一覧ページがJavaScriptで動的描画されるなど、方式1が使えないサイト向け)。

監視対象URL・キーワード・マーカー文言はコードに埋め込まず、環境変数(GitHub Secrets)経由で受け取る。
ログにも監視対象の実データ(URL・商品名・キーワード)を出力しない。
"""

import os
import re
import sys
import json
import hashlib
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

STATE_FILE = Path(__file__).parent / "state.json"

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")

try:
    MONITOR_URLS = json.loads(os.environ.get("MONITOR_URLS_JSON", "[]"))
    KEYWORDS = json.loads(os.environ.get("KEYWORDS_JSON", "[]"))
    SINGLE_PAGE_TARGETS = json.loads(os.environ.get("SINGLE_PAGE_TARGETS_JSON", "[]"))
except json.JSONDecodeError:
    print("[エラー] 設定用Secretsの形式が不正です。", file=sys.stderr)
    sys.exit(1)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}

ITEM_PATTERN = re.compile(
    r"(?P<name>\S.{0,40}?)\s*[\d,]+\s*円\s*\(税込\)\s*(?P<stock>在庫なし|在庫数\s*\d+\s*点)"
)


def fetch(url: str) -> str:
    res = requests.get(url, headers=HEADERS, timeout=20)
    res.raise_for_status()
    res.encoding = "utf-8"
    return res.text


def hash_key(raw: str) -> str:
    """state.jsonやログに実データを残さないよう、識別子はハッシュ化する"""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# ---- 方式1: 一覧ページ方式 ----

def extract_items(html: str, base_url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    items = {}
    for a in soup.find_all("a"):
        text = a.get_text(separator=" ", strip=True)
        if not text:
            continue
        m = ITEM_PATTERN.search(text)
        if not m:
            continue
        name = m.group("name").strip()
        stock = re.sub(r"\s+", "", m.group("stock"))
        if not any(k in name for k in KEYWORDS):
            continue
        href = a.get("href", "")
        key = hash_key(href or name)
        full_url = urljoin(base_url, href) if href else base_url
        items[key] = {"name": name, "stock": stock, "url": full_url}
    return items


def is_in_stock_list_style(stock_text: str) -> bool:
    return stock_text != "在庫なし"


def run_list_style(new_state: dict, notifications: list) -> bool:
    """一覧ページ方式のチェックを実行。何か対象を見つけたら True を返す"""
    seen_any = False
    for i, url in enumerate(MONITOR_URLS, start=1):
        try:
            html = fetch(url)
        except Exception:
            print(f"[警告] 一覧{i}: 取得失敗", file=sys.stderr)
            continue

        print(f"[info] 一覧{i}: 取得成功 (文字数: {len(html)})")

        items = extract_items(html, url)
        if not items:
            print(f"[debug] 一覧{i}: 対象該当なし")

        for key, item in items.items():
            seen_any = True
            prev_stock = new_state.get(key)
            prev_in_stock = is_in_stock_list_style(prev_stock) if prev_stock else False
            now_in_stock = is_in_stock_list_style(item["stock"])

            print(f"[item] 対象{key[:6]}: {'在庫あり' if now_in_stock else '在庫なし'}")

            if now_in_stock and not prev_in_stock:
                notifications.append(
                    f"🎉 入荷通知\n{item['name']}\n{item['stock']}\n{item['url']}"
                )

            new_state[key] = item["stock"]

    return seen_any


# ---- 方式2: 個別ページ方式(売り切れマーカー判定) ----

def run_single_page_style(new_state: dict, notifications: list) -> bool:
    """個別ページ方式のチェックを実行。何か対象を見つけたら True を返す"""
    seen_any = False
    for i, target in enumerate(SINGLE_PAGE_TARGETS, start=1):
        url = target.get("url")
        markers = target.get("out_of_stock_markers", [])
        name = target.get("name", f"対象{i}")
        if not url:
            continue

        try:
            html = fetch(url)
        except Exception:
            print(f"[警告] 個別ページ{i}: 取得失敗", file=sys.stderr)
            continue

        seen_any = True
        print(f"[info] 個別ページ{i}: 取得成功 (文字数: {len(html)})")

        now_out_of_stock = any(marker in html for marker in markers)
        now_in_stock = not now_out_of_stock

        key = hash_key(url)
        prev_status = new_state.get(key)  # "in" / "out"
        prev_in_stock = prev_status == "in"

        print(f"[item] 個別{key[:6]}: {'在庫あり' if now_in_stock else '在庫なし'}")

        if now_in_stock and not prev_in_stock:
            notifications.append(f"🎉 入荷通知\n{name}\n{url}")

        new_state[key] = "in" if now_in_stock else "out"

    return seen_any


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def send_line_broadcast(message: str) -> None:
    if not LINE_CHANNEL_ACCESS_TOKEN:
        print("[警告] LINEトークンが未設定のため通知をスキップします。")
        return
    res = requests.post(
        "https://api.line.me/v2/bot/message/broadcast",
        headers={
            "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
            "Content-Type": "application/json",
        },
        json={"messages": [{"type": "text", "text": message}]},
        timeout=15,
    )
    if res.status_code != 200:
        print(f"[エラー] 通知送信に失敗しました: {res.status_code}", file=sys.stderr)


def main() -> None:
    state = load_state()
    new_state = dict(state)
    notifications = []

    seen_list = run_list_style(new_state, notifications)
    seen_single = run_single_page_style(new_state, notifications)

    if not seen_list and not seen_single:
        print("[警告] どちらの方式でも対象が1件も見つかりませんでした。")

    for msg in notifications:
        send_line_broadcast(msg)
        print("[info] 通知を送信しました")

    if not notifications:
        print("変化なし")

    save_state(new_state)


if __name__ == "__main__":
    main()
