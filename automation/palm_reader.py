"""
palm_reader.py — 手相自動鑑定スクリプト
========================================
仕組み:
  1. Gmail (IMAP) で未読メールを監視
  2. 画像添付ファイルを検出してダウンロード
  3. Claude API (claude-opus-4-6) に画像を送信して鑑定文を生成
  4. 鑑定文を drafts/ フォルダに保存（または返信メールとして下書き保存）

使い方:
  python palm_reader.py --once       # 1回チェックして終了
  python palm_reader.py --watch      # 定期監視モード（60秒ごと）
  python palm_reader.py --test       # テスト画像で鑑定文を生成

環境変数 (.env):
  ANTHROPIC_API_KEY=...
  GMAIL_ADDRESS=...
  GMAIL_APP_PASSWORD=...   # Googleアカウント → 2段階認証 → アプリパスワード
  FORTUNE_EMAIL=...        # 鑑定士のメールアドレス（送信元）
"""

import os
import sys
import time
import imaplib
import email
import base64
import json
import argparse
import textwrap
from datetime import datetime
from email.message import Message
from pathlib import Path

import anthropic
from dotenv import load_dotenv

load_dotenv()

# ── 設定 ──────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GMAIL_ADDRESS     = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
FORTUNE_EMAIL     = os.environ.get("FORTUNE_EMAIL", GMAIL_ADDRESS)

DRAFTS_DIR = Path("drafts")
DRAFTS_DIR.mkdir(exist_ok=True)

SUPPORTED_IMAGE_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif"}

# ── 神矢 瞬 システムプロンプト ────────────────────────
SYSTEM_PROMPT = """あなたは神職（神主）兼 手相鑑定士「神矢 瞬（かみや しゅん）」です。
二十余年の神社奉仕で培った霊的感受性と、東西の手相学の知識を融合した独自の鑑定を行います。

【鑑定スタイル】
- 文体は格調高く、神秘的でありながら温かみがある
- 専門用語（生命線・感情線・頭脳線・運命線）を使いつつ、わかりやすく解説する
- 否定的なことも「転機」「課題」として前向きに伝える
- 神主らしい言葉の重みと品位を保つ
- 鑑定書は以下の構成で書く：
  1. 冒頭の挨拶（神主らしい格調ある文章）
  2. 生命線の鑑定
  3. 感情線の鑑定
  4. 頭脳線の鑑定
  5. 運命線の鑑定
  6. 総合鑑定・メッセージ
  7. 結びの言葉

【注意事項】
- 手相が不鮮明な場合は「より鮮明な写真でのご確認をお勧めします」と添える
- 見えない線については「現時点では確認が難しい」と正直に伝える
- 全体で800〜1200字程度の鑑定文を作成する
"""

READING_PROMPT = """この手のひらの写真を手相鑑定してください。

鑑定書として神矢 瞬の名義で、以下の4線を中心に詳しく鑑定してください：
① 生命線（いのちせん）
② 感情線（かんじょうせん）
③ 頭脳線（ずのうせん）
④ 運命線（うんめいせん）

鑑定書の最後に「神矢 瞬」の署名を入れてください。"""


# ── Claude API 鑑定生成 ───────────────────────────────
def generate_reading(image_data: bytes, media_type: str, client_name: str = "ご依頼者様") -> str:
    """画像データからClaude APIで手相鑑定文を生成する"""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    image_b64 = base64.standard_b64encode(image_data).decode("utf-8")

    # media_typeの正規化
    if media_type == "image/jpg":
        media_type = "image/jpeg"

    print(f"  → Claude API に送信中... ({media_type}, {len(image_data)//1024}KB)")

    with client.messages.stream(
        model="claude-opus-4-6",
        max_tokens=4096,
        thinking={"type": "adaptive"},
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": READING_PROMPT.replace("ご依頼者様", client_name),
                    },
                ],
            }
        ],
    ) as stream:
        reading = ""
        for event in stream:
            if (event.type == "content_block_delta"
                    and event.delta.type == "text_delta"):
                print(event.delta.text, end="", flush=True)
                reading += event.delta.text

    print()  # newline after streaming
    return reading


# ── 下書き保存 ─────────────────────────────────────────
def save_draft(
    from_email: str,
    subject: str,
    reading: str,
    image_filename: str,
    output_dir: Path = DRAFTS_DIR,
) -> Path:
    """鑑定文をMarkdown形式で下書き保存"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_from = from_email.replace("@", "_at_").replace(".", "_")
    filename = output_dir / f"reading_{timestamp}_{safe_from}.md"

    content = textwrap.dedent(f"""\
        # 手相鑑定書 下書き

        **依頼者:** {from_email}
        **件名:** {subject}
        **画像:** {image_filename}
        **生成日時:** {datetime.now().strftime('%Y年%m月%d日 %H:%M:%S')}

        ---

        ## 鑑定文

        {reading}

        ---

        *この鑑定文は AI による下書きです。送信前に内容をご確認ください。*
    """)

    filename.write_text(content, encoding="utf-8")
    print(f"  → 下書き保存: {filename}")
    return filename


# ── メールから画像を抽出 ──────────────────────────────
def extract_images_from_email(msg: Message) -> list[tuple[str, bytes, str]]:
    """メールから画像添付ファイルを抽出する。返り値: [(filename, data, mime_type)]"""
    images = []

    for part in msg.walk():
        content_type = part.get_content_type()
        if content_type not in SUPPORTED_IMAGE_TYPES:
            continue

        filename = part.get_filename() or f"palm_photo.{content_type.split('/')[1]}"
        payload = part.get_payload(decode=True)
        if payload:
            images.append((filename, payload, content_type))

    return images


# ── Gmail IMAP 監視 ───────────────────────────────────
def check_new_emails() -> int:
    """Gmailの未読メールを確認して手相画像が添付されていれば鑑定を実行。処理件数を返す。"""
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        print("⚠ Gmail認証情報が設定されていません (.env を確認してください)")
        return 0

    processed = 0

    try:
        print(f"📧 Gmail ({GMAIL_ADDRESS}) に接続中...")
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        mail.select("inbox")

        # 未読メールを検索
        _, msg_ids = mail.search(None, "UNSEEN")
        ids = msg_ids[0].split()

        if not ids:
            print("  新規メールなし")
            mail.logout()
            return 0

        print(f"  未読メール: {len(ids)}件")

        for msg_id in ids:
            _, data = mail.fetch(msg_id, "(RFC822)")
            msg = email.message_from_bytes(data[0][1])

            from_addr = email.utils.parseaddr(msg.get("From", ""))[1]
            subject   = msg.get("Subject", "（件名なし）")
            print(f"\n📨 From: {from_addr} / 件名: {subject}")

            images = extract_images_from_email(msg)
            if not images:
                print("  → 画像添付なし、スキップ")
                continue

            print(f"  → 画像 {len(images)}枚を検出")

            for filename, img_data, mime_type in images:
                print(f"  処理中: {filename}")
                try:
                    reading = generate_reading(img_data, mime_type, from_addr)
                    draft_path = save_draft(from_addr, subject, reading, filename)
                    processed += 1
                    print(f"  ✅ 鑑定完了: {draft_path.name}")
                except anthropic.BadRequestError as e:
                    print(f"  ⚠ 画像処理エラー（不鮮明または非対応形式）: {e}")
                except Exception as e:
                    print(f"  ❌ エラー: {e}")

        mail.logout()

    except imaplib.IMAP4.error as e:
        print(f"❌ Gmail接続エラー: {e}")

    return processed


# ── テスト用: ローカル画像ファイルで鑑定 ──────────────
def test_with_file(image_path: str) -> None:
    """ローカルの画像ファイルを使って鑑定文を生成するテスト"""
    path = Path(image_path)
    if not path.exists():
        print(f"ファイルが見つかりません: {image_path}")
        sys.exit(1)

    suffix_to_mime = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".webp": "image/webp",
        ".gif": "image/gif",
    }
    mime_type = suffix_to_mime.get(path.suffix.lower(), "image/jpeg")

    print(f"🔮 テスト鑑定: {path.name}")
    image_data = path.read_bytes()
    reading = generate_reading(image_data, mime_type, "テストユーザー")

    draft_path = save_draft(
        from_email="test@example.com",
        subject="テスト鑑定",
        reading=reading,
        image_filename=path.name,
    )

    print(f"\n✅ 鑑定完了 → {draft_path}")


# ── メイン ────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="手相自動鑑定スクリプト")
    parser.add_argument("--once",  action="store_true", help="1回チェックして終了")
    parser.add_argument("--watch", action="store_true", help="定期監視モード（60秒ごと）")
    parser.add_argument("--test",  type=str, metavar="IMAGE_PATH", help="ローカル画像でテスト")
    parser.add_argument("--interval", type=int, default=60, help="監視間隔（秒、デフォルト60）")
    args = parser.parse_args()

    if not ANTHROPIC_API_KEY:
        print("❌ ANTHROPIC_API_KEY が設定されていません")
        sys.exit(1)

    if args.test:
        test_with_file(args.test)
        return

    if args.once:
        n = check_new_emails()
        print(f"\n完了: {n}件処理しました")
        return

    if args.watch:
        print(f"👁 監視モード開始（{args.interval}秒ごとに確認）")
        print("  終了するには Ctrl+C を押してください\n")
        try:
            while True:
                check_new_emails()
                print(f"  次回チェック: {args.interval}秒後...")
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n監視を終了しました")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
