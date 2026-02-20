# JSON Translator

JSONファイル内のテキストを翻訳するGUIツール。

## 特徴

- Argos Translate（オフライン）とLingva（オンライン）の2つの翻訳エンジンに対応
- 翻訳結果をJSON形式で保存
- 翻訳キャッシュ機能で高速化
- 進捗バーとキャンセル機能

## 動作環境

**重要**: Python 3.11が必要です。Python 3.14では動作しません。

## インストール

```bash
pip install httpx argostranslate
```

## 実行

```bash
python jsontrans.py
```

## 使い方

1. 翻訳元・翻訳先の言語を選択
2. 翻訳エンジンを選択（Argos/Lingva）
3. JSONファイルを開くか、テキストエリアにJSONを貼り付け
4. 「翻訳する」をクリック
5. 翻訳結果を保存

## 設定

- `settings.json`: ウィンドウサイズとLingva URLを保存
- `translate_cache.pkl`: 翻訳キャッシュを保存
