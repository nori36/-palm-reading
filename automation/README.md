# 手相自動鑑定スクリプト

## セットアップ

```bash
cd automation
pip install -r requirements.txt
cp .env.example .env
# .env を編集して API キーとメール設定を入力
```

## 使い方

### ローカル画像でテスト
```bash
python palm_reader.py --test /path/to/palm_photo.jpg
```

### Gmail を1回チェック
```bash
python palm_reader.py --once
```

### 定期監視（60秒ごと）
```bash
python palm_reader.py --watch
```

### 監視間隔を変更（例: 5分ごと）
```bash
python palm_reader.py --watch --interval 300
```

## 生成ファイル

鑑定文は `drafts/` フォルダに Markdown 形式で保存されます。  
送信前に内容を確認・編集してください。

## Gmail アプリパスワードの取得方法

1. Google アカウントにログイン
2. セキュリティ → 2段階認証を有効化
3. セキュリティ → アプリパスワード → 新しいアプリパスワードを作成
4. 生成された16文字のパスワードを `GMAIL_APP_PASSWORD` に設定
