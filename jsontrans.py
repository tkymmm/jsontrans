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
CACHE_FILE = "translate_cache.pkl"
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
        root.title("JSON Translator (Argos + Lingva)")
        
        # 状態・設定
        self.lingva_url = tk.StringVar()
        self.engine = tk.StringVar(value="argos")
        self.source_lang = tk.StringVar(value="en")
        self.target_lang = tk.StringVar(value="ja")
        self.cancel_flag = False
        
        # キャッシュとHTTPクライアント
        self.argos_cache = OrderedDict()
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

        tk.Label(lang_inner, text=" → 翻訳先:").pack(side=tk.LEFT)
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

        inner = tk.Frame(button_frame)
        inner.pack(anchor="center")

        tk.Button(inner, text="ファイルを開く", command=self.load_file).pack(side=tk.LEFT, padx=5)
        tk.Button(inner, text="翻訳する", command=self.translate).pack(side=tk.LEFT, padx=5)

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
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, "rb") as f:
                self.argos_cache = pickle.load(f)
    
    def save_settings(self):
        data = {
            "lingva_url": self.lingva_url.get(),
            "window_width": self.root.winfo_width(),
            "window_height": self.root.winfo_height()
        }
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def save_cache(self):
        with open(CACHE_FILE, "wb") as f:
            pickle.dump(self.argos_cache, f)

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
        self.cancel_button.config(state="normal")
        thread = threading.Thread(target=self._translate_worker, daemon=True)
        thread.start()

    def cancel(self):
        self.cancel_flag = True
    
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
        base = base_url.rstrip("/")
        encoded = urllib.parse.quote(text)
        url = f"{base}/api/v1/{source}/{target}/{encoded}"
        
        for _ in range(3):  # リトライ回数を削減
            try:
                res = self.client.get(url)
                if res.status_code == 200:
                    return res.json().get("translation", text)
                time.sleep(0.5)
            except:
                time.sleep(0.5)
        return text
    
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
                messagebox.showinfo("中断", "翻訳をキャンセルしました。途中までの結果を表示します。")

        except Exception as e:
            messagebox.showerror("エラー", f"翻訳中にエラーが発生しました:\n{e}")

        finally:
            self.cancel_button.config(state="disabled")
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
