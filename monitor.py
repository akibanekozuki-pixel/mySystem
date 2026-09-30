"""
汎用 在庫監視 → LINE通知スクリプト

監視対象URL・キーワードはコードに埋め込まず、環境変数(GitHub Secrets)経由で受け取る。
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


def is_in_stock(stock_text: str) -> bool:
    return stock_text != "在庫なし"


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
    seen_any_item = False

    for i, url in enumerate(MONITOR_URLS, start=1):
        try:
            html = fetch(url)
        except Exception:
            print(f"[警告] ページ{i}: 取得失敗", file=sys.stderr)
            continue

        print(f"[info] ページ{i}: 取得成功 (文字数: {len(html)})")

        items = extract_items(html, url)
        if not items:
            print(f"[debug] ページ{i}: 対象該当なし")

        for key, item in items.items():
            seen_any_item = True
            prev_stock = new_state.get(key)
            prev_in_stock = is_in_stock(prev_stock) if prev_stock else False
            now_in_stock = is_in_stock(item["stock"])

            print(f"[item] 対象{key[:6]}: {'在庫あり' if now_in_stock else '在庫なし'}")

            if now_in_stock and not prev_in_stock:
                notifications.append(
                    f"🎉 入荷通知\n{item['name']}\n{item['stock']}\n{item['url']}"
                )

            new_state[key] = item["stock"]

    if not seen_any_item:
        print("[警告] 対象商品が1件も見つかりませんでした。")

    for msg in notifications:
        send_line_broadcast(msg)
        print("[info] 通知を送信しました")

    if not notifications:
        print("変化なし")

    save_state(new_state)


if __name__ == "__main__":
    main()
