import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import json
import urllib.parse
import time
import threading
import httpx
import os
import pickle
from collections import OrderedDict

# Argos Translate
import argostranslate.package
import argostranslate.translate

# ==============================
# 定数（最小ウィンドウサイズ計算用）
# ==============================
TOP_UI_HEIGHT = 90        # 言語選択 + URL 行
BOTTOM_UI_HEIGHT = 120    # ボタン + 進捗バー + 件数表示
TEXTAREA_MIN_LINES = 1    # テキストエリア最小行数
MIN_WIDTH = 600           # 最小幅

# ==============================
# 設定ファイル
# ==============================
SETTINGS_FILE = "settings.json"
ARGOS_CACHE_FILE = "argos_cache.pkl"
LINGVA_CACHE_FILE = "lingva_cache.pkl"
MAX_CACHE_SIZE = 1000  # キャッシュ最大サイズ

def ensure_argos_model(source, target):
    installed = argostranslate.translate.get_installed_languages()
    installed_codes = [l.code for l in installed]

    if source in installed_codes and target in installed_codes:
        return

    packages = argostranslate.package.get_available_packages()
    for p in packages:
        if p.from_code == source and p.to_code == target:
            path = p.download()
            argostranslate.package.install_from_path(path)
            return

class JsonTranslatorApp:
    def __init__(self, root):
        self.root = root
        root.title("JSON Translator (Argos + Lingva) - Merge版")
        
        # 状態・設定
        self.lingva_url = tk.StringVar()
        self.engine = tk.StringVar(value="argos")
        self.source_lang = tk.StringVar(value="en")
        self.target_lang = tk.StringVar(value="ja")
        self.cancel_flag = False
        self.target_data = None  # 下書きのJSONデータを保持
        self.is_cancelled = False  # キャンセルされたかどうか
        self.paused_translation = None  # 中断された翻訳データを保持
        self.paused_engine = None  # 中断された時のエンジンを保持
        
        # キャッシュとHTTPクライアント
        self.argos_cache = OrderedDict()
        self.lingva_cache = OrderedDict()
        self.client = httpx.Client(timeout=10)
        self.last_progress_update = 0
        self.progress_update_interval = 10  # 10件毎に進捗更新
        
        self.load_settings()
        self.load_cache()

        # root レイアウト（grid）
        root.rowconfigure(0, weight=0)
        root.rowconfigure(1, weight=0)
        root.rowconfigure(2, weight=1)
        root.rowconfigure(3, weight=0)
        root.rowconfigure(4, weight=0)
        root.rowconfigure(5, weight=0)
        root.columnconfigure(0, weight=1)

        # 言語選択行
        lang_frame = tk.Frame(root)
        lang_frame.grid(row=0, column=0, sticky="ew", padx=5, pady=5)

        lang_inner = tk.Frame(lang_frame)
        lang_inner.pack(anchor="center")

        tk.Label(lang_inner, text="翻訳元:").pack(side=tk.LEFT)
        tk.OptionMenu(lang_inner, self.source_lang,
                      "auto", "en", "ja", "zh", "fr", "de", "es", "ko").pack(side=tk.LEFT)

        tk.Label(lang_inner, text=" → 下書き:").pack(side=tk.LEFT)
        tk.OptionMenu(lang_inner, self.target_lang,
                      "ja", "en", "zh", "fr", "de", "es", "ko").pack(side=tk.LEFT)

        tk.Label(lang_inner, text="  エンジン:").pack(side=tk.LEFT)
        tk.OptionMenu(lang_inner, self.engine, "argos", "lingva").pack(side=tk.LEFT)

        # Lingva URL 行
        url_frame = tk.Frame(root)
        url_frame.grid(row=1, column=0, sticky="ew", padx=5, pady=2)

        tk.Label(url_frame, text="Lingva URL:").pack(side=tk.LEFT)
        tk.Entry(url_frame, textvariable=self.lingva_url, width=40).pack(side=tk.LEFT, fill="x", expand=True)

        # テキストエリア行
        text_frame = tk.Frame(root)
        text_frame.grid(row=2, column=0, sticky="nsew", padx=5, pady=5)

        text_frame.rowconfigure(0, weight=1)
        text_frame.columnconfigure(1, weight=1)
        text_frame.columnconfigure(2, weight=1)

        self.linenumbers = tk.Canvas(text_frame, width=40)
        self.linenumbers.grid(row=0, column=0, sticky="ns")

        self.input_text = tk.Text(
            text_frame,
            wrap="none",
            yscrollcommand=lambda *args: (self._sync_scroll(*args), self._update_linenumbers())
        )
        self.input_text.grid(row=0, column=1, sticky="nsew")

        self.output_text = tk.Text(
            text_frame,
            wrap="none",
            yscrollcommand=lambda *args: self._sync_scroll(*args)
        )
        self.output_text.grid(row=0, column=2, sticky="nsew")

        scrollbar = tk.Scrollbar(text_frame, orient="vertical", command=self._scroll_both)
        scrollbar.grid(row=0, column=3, sticky="ns")

        # 下部ボタン行
        button_frame = tk.Frame(root)
        button_frame.grid(row=3, column=0, sticky="ew", padx=5, pady=5)

        # 下書きファイルパス表示
        path_frame = tk.Frame(button_frame)
        path_frame.pack(fill="x", pady=(0, 5))
        
        self.target_path_label = tk.Label(path_frame, text="下書き: 未選択", fg="gray")
        self.target_path_label.pack(side=tk.LEFT, fill="x", expand=True, padx=5)
        
        # クリアボタンをパスラベルのすぐ後ろに配置
        self.clear_button = tk.Button(path_frame, text="下書きをクリア", command=self.close_target_file)
        self.clear_button.pack(side=tk.LEFT, padx=5)

        # GitHubアップロードボタン
        self.github_button = tk.Button(path_frame, text="README作成", command=self.create_readme)
        self.github_button.pack(side=tk.LEFT, padx=5)

        inner = tk.Frame(button_frame)
        inner.pack(anchor="center")

        tk.Button(inner, text="翻訳元を開く", command=self.load_file).pack(side=tk.LEFT, padx=5)
        tk.Button(inner, text="下書きを開く", command=self.load_target_file).pack(side=tk.LEFT, padx=5)
        self.translate_button = tk.Button(inner, text="翻訳する", command=self.smart_translate)
        self.translate_button.pack(side=tk.LEFT, padx=5)

        self.cancel_button = tk.Button(inner, text="キャンセル", command=self.cancel)
        self.cancel_button.pack(side=tk.LEFT, padx=5)
        self.cancel_button.config(state="disabled")

        tk.Button(inner, text="保存", command=self.save_file).pack(side=tk.LEFT, padx=5)

        # 進捗バー
        self.progress = ttk.Progressbar(root, orient="horizontal", length=400, mode="determinate")
        self.progress.grid(row=4, column=0, sticky="ew", padx=20, pady=(0, 2))

        self.progress_label = tk.Label(root, text="0 / 0")
        self.progress_label.grid(row=5, column=0, sticky="e", padx=20, pady=(0, 5))

        # 最小ウィンドウサイズ設定
        line_height = self.input_text.winfo_reqheight() // 20  # 推定行高
        textarea_min_height = line_height * TEXTAREA_MIN_LINES
        min_height = TOP_UI_HEIGHT + textarea_min_height + BOTTOM_UI_HEIGHT
        self.root.minsize(MIN_WIDTH, min_height)

    # 設定保存・読み込み
    def load_settings(self):
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.lingva_url.set(data.get("lingva_url", ""))
                w = data.get("window_width")
                h = data.get("window_height")
                if w and h:
                    self.root.geometry(f"{w}x{h}")

    def load_cache(self):
        """エンジンごとのキャッシュを読み込む"""
        # Argosキャッシュ
        if os.path.exists(ARGOS_CACHE_FILE):
            with open(ARGOS_CACHE_FILE, "rb") as f:
                self.argos_cache = pickle.load(f)
        
        # Lingvaキャッシュ
        if os.path.exists(LINGVA_CACHE_FILE):
            with open(LINGVA_CACHE_FILE, "rb") as f:
                self.lingva_cache = pickle.load(f)
    
    def save_settings(self):
        data = {
            "lingva_url": self.lingva_url.get(),
            "window_width": self.root.winfo_width(),
            "window_height": self.root.winfo_height()
        }
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def save_cache(self):
        """エンジンごとのキャッシュを保存する"""
        # Argosキャッシュ保存
        with open(ARGOS_CACHE_FILE, "wb") as f:
            pickle.dump(self.argos_cache, f)
        
        # Lingvaキャッシュ保存
        with open(LINGVA_CACHE_FILE, "wb") as f:
            pickle.dump(self.lingva_cache, f)

    # ファイル読み込み・保存
    def load_file(self):
        path = filedialog.askopenfilename(filetypes=[("JSON files", "*.json")])
        if not path:
            return
        with open(path, "r", encoding="utf-8") as f:
            self.input_text.delete("1.0", tk.END)
            self.input_text.insert(tk.END, f.read())
            self._update_linenumbers()

    def save_file(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON files", "*.json")]
        )
        if not path:
            return
        text = self.output_text.get("1.0", tk.END).strip()
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        messagebox.showinfo("保存完了", "翻訳結果を保存しました。")
    
    def load_target_file(self):
        """下書きのJSONファイルを読み込む"""
        path = filedialog.askopenfilename(filetypes=[("JSON files", "*.json")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                self.target_data = json.load(f)
            # パスラベルを更新
            self.target_path_label.config(text=f"下書き: {path}", fg="black")
            # ボタンテキストを「マージ翻訳」に変更
            self.translate_button.config(text="マージ翻訳")
        except Exception as e:
            messagebox.showerror("エラー", f"下書きファイルの読み込みに失敗しました:\n{e}")
            self.target_data = None

    def close_target_file(self):
        """下書きファイルを閉じる"""
        if self.target_data is None:
            messagebox.showinfo("情報", "下書きファイルが開かれていません。")
            return
        
        self.target_data = None
        # パスラベルをクリア
        self.target_path_label.config(text="下書き: 未選択", fg="gray")
        # ボタンテキストを「翻訳する」に変更
        self.translate_button.config(text="翻訳する")
        messagebox.showinfo("完了", "下書きファイルを閉じました。")

    def smart_translate(self):
        """下書きファイルの有無に応じて適切な翻訳を実行"""
        if self.target_data is not None:
            # 下書きがある場合はマージ翻訳
            self.merge_translate()
        else:
            # 下書きがない場合は通常翻訳
            self.translate()

    # スクロール・行番号
    def _scroll_both(self, *args):
        self.input_text.yview(*args)
        self.output_text.yview(*args)
        self._update_linenumbers()

    def _sync_scroll(self, *args):
        self.input_text.yview_moveto(args[0])
        self.output_text.yview_moveto(args[0])
        self._update_linenumbers()

    def _update_linenumbers(self):
        # 行番号のキャッシュを利用して更新頻度を削減
        self.linenumbers.delete("all")
        line_count = int(self.input_text.index('end-1c').split('.')[0])
        for i in range(1, line_count + 1):
            dline = self.input_text.dlineinfo(f"{i}.0")
            if dline:
                self.linenumbers.create_text(2, dline[1], anchor="nw", text=str(i))

    # 翻訳制御
    def translate(self):
        self.cancel_flag = False
        self.is_cancelled = False
        self.paused_translation = None  # 新規翻訳開始時にクリア
        self.paused_engine = None  # エンジン情報もクリア
        # ボタンをキャンセルに設定
        self.cancel_button.config(text="キャンセル", command=self.cancel, state="normal")
        self.translate_button.config(state="disabled")  # 翻訳実行中は無効化
        thread = threading.Thread(target=self._translate_worker, daemon=True)
        thread.start()

    def cancel(self):
        """キャンセル処理"""
        self.cancel_flag = True
        self.is_cancelled = True
        self.cancel_button.config(text="続きから再開", command=self.resume_translation)

    def resume_translation(self):
        """続きから翻訳を再開"""
        # エンジンが変更されているかチェック
        current_engine = self.engine.get()
        if self.paused_engine and self.paused_engine != current_engine:
            messagebox.showwarning(
                "エンジン変更", 
                f"中断時のエンジン: {self.paused_engine}\n"
                f"現在のエンジン: {current_engine}\n\n"
                f"エンジンが切り替わったため、はじめから翻訳します"
            )
            # 進捗をリセットして新規翻訳として扱う
            self.paused_translation = None
            self.paused_engine = None
            # 下書きテキストエリアを空欄にする
            self.output_text.delete("1.0", tk.END)
            # ボタンをキャンセルに設定
            self.cancel_button.config(text="キャンセル", command=self.cancel, state="normal")
            # 新規翻訳を開始
            self.merge_translate()
            return
        
        # 続きから再開可能な場合
        self.is_cancelled = False
        self.cancel_flag = False
        # ボタンをキャンセルに設定
        self.cancel_button.config(text="キャンセル", command=self.cancel, state="normal")
        
        if self.paused_translation:
            # 続きから再開の場合、未完部分を削除
            self.output_text.delete("1.0", tk.END)
            thread = threading.Thread(target=self._resume_worker, daemon=True)
            thread.start()
        else:
            # 再開不可な場合はグレーアウト
            self.cancel_button.config(text="キャンセル", state="disabled")

    def _resume_worker(self):
        """再開用のワーカー関数"""
        try:
            source_data, target_data, current_count, total_count = self.paused_translation
            
            # 進捗バーを復元
            self.progress["maximum"] = total_count
            self.progress["value"] = current_count
            self.progress_label.config(text=f"{current_count} / {total_count}")

            current = {"count": current_count}

            def update_progress():
                current["count"] += 1
                if current["count"] % self.progress_update_interval == 0 or current["count"] == total_count:
                    self.progress["value"] = current["count"]
                    self.progress_label.config(text=f"{current['count']} / {total_count}")
                    self.root.update_idletasks()

            def cancel_check():
                return self.cancel_flag

            source = self.source_lang.get()
            target = self.target_lang.get()

            try:
                # 続きからマージ翻訳実行
                merged_result = self.merge_translate_json(
                    source_data, target_data, source, target, update_progress, cancel_check
                )
                
                # 結果をJSON形式で出力
                result = json.dumps(merged_result, ensure_ascii=False, indent=2)

                self.output_text.delete("1.0", tk.END)
                self.output_text.insert(tk.END, result)

                if self.cancel_flag:
                    messagebox.showinfo("中断", "マージ翻訳をキャンセルしました。途中までの結果を表示します。")
                else:
                    messagebox.showinfo("完了", "マージ翻訳が完了しました。")
                    self.paused_translation = None  # 完了したらクリア

            except Exception as e:
                messagebox.showerror("エラー", f"マージ翻訳中にエラーが発生しました:\n{e}")

        finally:
            self.cancel_button.config(state="disabled")
            self.save_cache()
    
    # 翻訳関連のヘルパー関数
    def argos_translate(self, text, source, target):
        key = (text, source, target)
        if key in self.argos_cache:
            return self.argos_cache[key]
        
        try:
            translated = argostranslate.translate.translate(text, source, target)
        except Exception:
            translated = text
        
        # LRUキャッシュ管理
        if len(self.argos_cache) >= MAX_CACHE_SIZE:
            self.argos_cache.popitem(last=False)
        self.argos_cache[key] = translated
        return translated
    
    def lingva_translate(self, base_url, text, source, target):
        key = (text, source, target)
        if key in self.lingva_cache:
            return self.lingva_cache[key]
        
        base = base_url.rstrip("/")
        encoded = urllib.parse.quote(text)
        url = f"{base}/api/v1/{source}/{target}/{encoded}"
        
        translated = text
        for _ in range(3):  # リトライ回数を削減
            try:
                res = self.client.get(url)
                if res.status_code == 200:
                    translated = res.json().get("translation", text)
                    break
                time.sleep(0.5)
            except:
                time.sleep(0.5)
        
        # LRUキャッシュ管理
        if len(self.lingva_cache) >= MAX_CACHE_SIZE:
            self.lingva_cache.popitem(last=False)
        self.lingva_cache[key] = translated
        return translated
    
    def count_strings(self, obj):
        if isinstance(obj, str):
            return 1
        if isinstance(obj, list):
            return sum(self.count_strings(i) for i in obj)
        if isinstance(obj, dict):
            return sum(self.count_strings(v) for v in obj.values())
        return 0

    def translate_text(self, text, source, target):
        if self.engine.get() == "argos":
            ensure_argos_model(source, target)
            return self.argos_translate(text, source, target)
        else:
            return self.lingva_translate(self.lingva_url.get(), text, source, target)

    def merge_translate_json(self, source_obj, target_template, source_lang, target_lang, progress_callback, cancel_check):
        """
        下書きの構造を維持しつつ、翻訳元の内容でマージ翻訳する
        target_template: 下書きのJSON構造（テンプレート）
        source_obj: 翻訳元のJSONデータ
        """
        if cancel_check():
            return target_template

        # 下書きと翻訳元が両方とも文字列の場合
        if isinstance(target_template, str) and isinstance(source_obj, str):
            # 下書きが空欄""の場合、翻訳元のテキストを翻訳して入れる
            if target_template == "":
                translated = self.translate_text(source_obj, source_lang, target_lang)
                progress_callback()
                return translated
            # 翻訳元のテキストを翻訳して、下書きの形式で返す
            translated = self.translate_text(source_obj, source_lang, target_lang)
            progress_callback()
            return translated

        # 下書きが文字列で、翻訳元がオブジェクトの場合
        # 翻訳元の内容を文字列化して翻訳
        if isinstance(target_template, str) and not isinstance(source_obj, str):
            # 下書きが空欄""の場合、翻訳元の内容を文字列化して翻訳して入れる
            if target_template == "":
                source_text = json.dumps(source_obj, ensure_ascii=False)
                translated = self.translate_text(source_text, source_lang, target_lang)
                progress_callback()
                return translated
            source_text = json.dumps(source_obj, ensure_ascii=False)
            translated = self.translate_text(source_text, source_lang, target_lang)
            progress_callback()
            return translated

        # 下書きがリストの場合
        if isinstance(target_template, list):
            if isinstance(source_obj, list):
                # 両方ともリストの場合、対応する要素をマージ
                result = []
                min_len = min(len(target_template), len(source_obj))
                for i in range(min_len):
                    merged = self.merge_translate_json(
                        source_obj[i], target_template[i], 
                        source_lang, target_lang, progress_callback, cancel_check
                    )
                    result.append(merged)
                # 翻訳元が長い場合、残りを翻訳して追加
                for i in range(min_len, len(source_obj)):
                    if cancel_check():
                        break
                    translated_item = self.translate_json(
                        source_obj[i], source_lang, target_lang, 
                        progress_callback, cancel_check
                    )
                    result.append(translated_item)
                # 下書きが長い場合、残りをそのまま保持
                for i in range(min_len, len(target_template)):
                    result.append(target_template[i])
                return result
            else:
                # 翻訳元がリストでない場合、下書きの構造を維持
                return target_template

        # 下書きが辞書の場合
        if isinstance(target_template, dict):
            if isinstance(source_obj, dict):
                # 両方とも辞書の場合、翻訳元のキー順を優先してマージ
                result = {}
                # 翻訳元のキー順で処理
                for key in source_obj.keys():
                    if key in target_template:
                        # 下書きに同じキーがある場合、マージ
                        merged = self.merge_translate_json(
                            source_obj[key], target_template[key],
                            source_lang, target_lang, progress_callback, cancel_check
                        )
                        result[key] = merged
                    else:
                        # 下書きにキーがない場合、翻訳元の値を翻訳
                        translated_value = self.translate_json(
                            source_obj[key], source_lang, target_lang,
                            progress_callback, cancel_check
                        )
                        result[key] = translated_value
                
                # 下書きにのみ存在するキーを追加（翻訳元のデータの後ろに追加）
                for key in target_template.keys():
                    if key not in source_obj:
                        if cancel_check():
                            break
                        result[key] = target_template[key]
                return result
            else:
                # 翻訳元が辞書でない場合、下書きの構造を維持
                return target_template

        # その他の場合、下書きの値を維持
        return target_template

    def translate_json(self, obj, source, target, progress_callback, cancel_check):
        if cancel_check():
            return obj

        if isinstance(obj, str):
            translated = self.translate_text(obj, source, target)
            progress_callback()
            return translated

        if isinstance(obj, list):
            return [self.translate_json(i, source, target, progress_callback, cancel_check)
                    for i in obj]

        if isinstance(obj, dict):
            return {k: self.translate_json(v, source, target, progress_callback, cancel_check)
                    for k, v in obj.items()}

        return obj

    def merge_translate(self):
        """マージ翻訳を実行"""
        if self.target_data is None:
            messagebox.showerror("エラー", "下書きファイルが読み込まれていません。\n「下書きを開く」ボタンからファイルを読み込んでください。")
            return
        
        self.cancel_flag = False
        self.is_cancelled = False
        self.paused_translation = None  # 新規翻訳開始時にクリア
        self.paused_engine = None  # エンジン情報もクリア
        # 下書きテキストエリアを空欄にする
        self.output_text.delete("1.0", tk.END)
        # ボタンをキャンセルに設定
        self.cancel_button.config(text="キャンセル", command=self.cancel, state="normal")
        self.translate_button.config(state="disabled")  # 翻訳実行中は無効化
        thread = threading.Thread(target=self._merge_translate_worker, daemon=True)
        thread.start()

    def _merge_translate_worker(self):
        """マージ翻訳のワーカー関数"""
        try:
            # 翻訳元データの読み込み
            raw = self.input_text.get("1.0", tk.END).strip()
            source_data = json.loads(raw)
        except Exception as e:
            messagebox.showerror("エラー", f"翻訳元JSONの読み込みに失敗しました:\n{e}")
            return

        source = self.source_lang.get()
        target = self.target_lang.get()

        # 翻訳対象の文字列数をカウント（翻訳元ベース）
        total = self.count_strings(source_data)
        if total == 0:
            messagebox.showinfo("情報", "翻訳対象の文字列がありません。")
            return

        self.progress["maximum"] = total
        self.progress["value"] = 0
        self.progress_label.config(text=f"0 / {total}")

        current = {"count": 0}

        def update_progress():
            current["count"] += 1
            if current["count"] % self.progress_update_interval == 0 or current["count"] == total:
                self.progress["value"] = current["count"]
                self.progress_label.config(text=f"{current['count']} / {total}")
                self.root.update_idletasks()

        def cancel_check():
            if self.cancel_flag:
                # キャンセルされたら現在の状態を保存
                self.paused_translation = (source_data, self.target_data, current["count"], total)
                self.paused_engine = self.engine.get()  # 現在のエンジンを保存
            return self.cancel_flag

        try:
            # マージ翻訳実行
            merged_result = self.merge_translate_json(
                source_data, self.target_data, source, target, update_progress, cancel_check
            )
            
            # 結果をJSON形式で出力
            result = json.dumps(merged_result, ensure_ascii=False, indent=2)

            self.output_text.delete("1.0", tk.END)
            self.output_text.insert(tk.END, result)

            if self.cancel_flag:
                messagebox.showinfo("中断", "マージ翻訳をキャンセルしました。途中までの下書きを表示します。")
            else:
                messagebox.showinfo("完了", "マージ翻訳が完了しました。")
                self.paused_translation = None  # 完了したらクリア

        except Exception as e:
            messagebox.showerror("エラー", f"マージ翻訳中にエラーが発生しました:\n{e}")

        finally:
            if not self.is_cancelled:  # キャンセルされていない場合のみボタンを無効化
                self.cancel_button.config(text="キャンセル", state="disabled")
                self.translate_button.config(state="normal")  # 翻訳ボタンを再有効化
            self.save_cache()

    def _translate_worker(self):
        try:
            raw = self.input_text.get("1.0", tk.END).strip()
            data = json.loads(raw)
        except Exception as e:
            messagebox.showerror("エラー", f"JSON の読み込みに失敗しました:\n{e}")
            return

        source = self.source_lang.get()
        target = self.target_lang.get()

        total = self.count_strings(data)
        if total == 0:
            messagebox.showinfo("情報", "翻訳対象の文字列がありません。")
            return

        self.progress["maximum"] = total
        self.progress["value"] = 0
        self.progress_label.config(text=f"0 / {total}")

        current = {"count": 0}

        def update_progress():
            current["count"] += 1
            # 進捗更新の頻度を削減してパフォーマンス向上
            if current["count"] % self.progress_update_interval == 0 or current["count"] == total:
                self.progress["value"] = current["count"]
                self.progress_label.config(text=f"{current['count']} / {total}")
                self.root.update_idletasks()

        def cancel_check():
            return self.cancel_flag

        try:
            translated = self.translate_json(data, source, target, update_progress, cancel_check)
            result = json.dumps(translated, ensure_ascii=False, indent=2)

            self.output_text.delete("1.0", tk.END)
            self.output_text.insert(tk.END, result)

            if self.cancel_flag:
                messagebox.showinfo("中断", "翻訳をキャンセルしました。途中までの下書きを表示します。")

        except Exception as e:
            messagebox.showerror("エラー", f"翻訳中にエラーが発生しました:\n{e}")

        finally:
            if not self.is_cancelled:  # キャンセルされていない場合のみボタンを無効化
                self.cancel_button.config(text="キャンセル", state="disabled")
                self.translate_button.config(state="normal")  # 翻訳ボタンを再有効化
            self.save_cache()

if __name__ == "__main__":
    root = tk.Tk()
    app = JsonTranslatorApp(root)

    def on_close():
        app.save_settings()
        app.save_cache()
        app.client.close()  # HTTPクライアントをクローズ
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()
