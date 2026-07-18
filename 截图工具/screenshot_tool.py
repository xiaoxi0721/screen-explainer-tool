# -*- coding: utf-8 -*-
"""
Windows 截图工具 —— 后台待机 + F9 快捷键截图 + 豆包 AI 弹窗解释。
零额外依赖：全局快捷键用 ctypes 调 Windows API，弹窗用 Tkinter。

使用方法：
    python screenshot_tool.py                      # 后台模式：按 F9 截图
    python screenshot_tool.py --once               # 单次模式：截图后退出
    python screenshot_tool.py -p "这里有什么？"     # 自定义提示词
    python screenshot_tool.py -o screenshot.jpg    # 保存截图
    python screenshot_tool.py -k Ctrl+Shift+F9     # 自定义快捷键（默认 F9）
"""

import base64
import ctypes
from ctypes import wintypes
import io
import os
import sys
import time
import json
import argparse
import threading
import tkinter as tk
from tkinter import scrolledtext

import mss
import requests
from PIL import Image

# ------------------------------------------------
# 配置
# ------------------------------------------------

API_KEY_PATH = os.path.join(os.path.dirname(__file__), "API KEY.txt")
DOUBAO_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
MAX_IMAGE_SIZE = (1920, 1920)
JPEG_QUALITY = 85
DEFAULT_PROMPT = "请用中文详细描述这张截图的内容，包括画面中的文字、UI 元素、图表、以及任何值得注意的细节。"
DEFAULT_HOTKEY = "F9"

VK_MAP = {
    "F1":0x70,"F2":0x71,"F3":0x72,"F4":0x73,"F5":0x74,"F6":0x75,
    "F7":0x76,"F8":0x77,"F9":0x78,"F10":0x79,"F11":0x7A,"F12":0x7B,
}
MOD_CONTROL, MOD_ALT, MOD_SHIFT = 0x0002, 0x0001, 0x0004
WM_HOTKEY = 0x0312


# ------------------------------------------------
# 全局快捷键（ctypes → Windows RegisterHotKey）
# ------------------------------------------------

class GlobalHotkey:
    def __init__(self, hotkey_str: str, callback):
        self.user32 = ctypes.windll.user32
        self.callback = callback
        self._running = False
        self._id = 1
        parts = hotkey_str.upper().replace(" ", "").split("+")
        self._mod = 0
        self._vk = None
        for p in parts:
            if p == "CTRL":   self._mod |= MOD_CONTROL
            elif p == "ALT":  self._mod |= MOD_ALT
            elif p == "SHIFT": self._mod |= MOD_SHIFT
            elif p in VK_MAP: self._vk = VK_MAP[p]
            else: raise ValueError(f"不支持的快捷键: {hotkey_str}")
        if self._vk is None:
            raise ValueError(f"快捷键缺少功能键 F1-F12: {hotkey_str}")

    def _loop(self):
        msg = wintypes.MSG()
        if not self.user32.RegisterHotKey(None, self._id, self._mod, self._vk):
            raise RuntimeError(f"注册快捷键失败 (可能被占用): {self._vk}")
        while self._running:
            if self.user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                if msg.message == WM_HOTKEY:
                    self.callback()
                self.user32.TranslateMessage(ctypes.byref(msg))
                self.user32.DispatchMessageW(ctypes.byref(msg))
            else:
                time.sleep(0.05)
        self.user32.UnregisterHotKey(None, self._id)

    def start(self):
        self._running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self._running = False


# ------------------------------------------------
# 配置读取
# ------------------------------------------------

def _read_config_line(path: str, keyword: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if keyword in line:
                return line.split("：")[-1].strip()
    raise ValueError(f"API KEY.txt 中未找到包含 {keyword} 的行")

def load_api_key(path):     return _read_config_line(path, "DouBao")
def load_endpoint_id(path): return _read_config_line(path, "接入点")


# ------------------------------------------------
# 截图
# ------------------------------------------------

def capture_region(region) -> bytes:
    with mss.MSS() as sct:
        if region is None:
            monitor = sct.monitors[1]
            img = sct.grab(monitor)
        else:
            x, y, w, h = region
            img = sct.grab({"left": x, "top": y, "width": w, "height": h})

    pil_img = Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")
    if pil_img.width > MAX_IMAGE_SIZE[0] or pil_img.height > MAX_IMAGE_SIZE[1]:
        pil_img.thumbnail(MAX_IMAGE_SIZE, Image.LANCZOS)
    buf = io.BytesIO()
    pil_img.save(buf, format="JPEG", quality=JPEG_QUALITY)
    return buf.getvalue()


# ------------------------------------------------
# 豆包 API 调用（流式）
# ------------------------------------------------

def call_doubao(api_key: str, image_bytes: bytes, prompt: str, endpoint_id: str,
                on_chunk=None) -> str:
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    payload = {
        "model": endpoint_id,
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
            {"type": "text", "text": prompt},
        ]}],
        "stream": True, "max_tokens": 4096,
    }
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    resp = requests.post(
        f"{DOUBAO_BASE_URL}/chat/completions",
        json=payload, headers=headers, timeout=300, stream=True,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"API [{resp.status_code}]: {resp.text}")

    parts = []
    for line in resp.iter_lines():
        if not line: continue
        line = line.decode("utf-8")
        if not line.startswith("data: ") or line == "data: [DONE]": continue
        chunk = json.loads(line[6:])
        c = chunk["choices"][0].get("delta", {}).get("content", "")
        if c:
            parts.append(c)
            if on_chunk: on_chunk(c)
    return "".join(parts)


# ------------------------------------------------
# 核心 App：单 Tk root，Toplevel 做遮罩 + 弹窗
# ------------------------------------------------

class ScreenshotApp:
    def __init__(self, api_key, endpoint_id, prompt, output_path, once=False):
        self.api_key = api_key
        self.endpoint_id = endpoint_id
        self.prompt = prompt
        self.output_path = output_path
        self.once = once

        self.root = tk.Tk()
        self.root.withdraw()
        self.triggered = False
        self._lock = threading.Lock()
        self._busy = False
        self._hotkey = None

    # ---- 快捷键回调（热键线程调用）----
    def _on_hotkey(self):
        with self._lock:
            if not self._busy:
                self.triggered = True

    # ---- 主循环轮询 ----
    def _poll(self):
        with self._lock:
            if self.triggered:
                self.triggered = False
                self._start_flow()
        self.root.after(100, self._poll)

    # ---- 截图流程入口 ----
    def _start_flow(self):
        self._busy = True
        self._show_overlay()

    # ---- 1. 选区遮罩 (Toplevel) ----
    def _show_overlay(self):
        top = tk.Toplevel(self.root)
        top.title("")
        top.attributes("-fullscreen", True)
        top.attributes("-topmost", True)
        top.attributes("-alpha", 0.35)
        top.configure(bg="black")
        top.config(cursor="cross")

        canvas = tk.Canvas(top, bg="black", highlightthickness=0)
        canvas.pack(fill=tk.BOTH, expand=True)

        sw, sh = top.winfo_screenwidth(), top.winfo_screenheight()
        canvas.create_text(sw//2, sh//2-40,
            text="拖拽鼠标框选截图区域\nEnter=全屏 | Esc=取消",
            fill="white", font=("Microsoft YaHei", 16, "bold"), justify=tk.CENTER)
        size_txt = canvas.create_text(sw//2, sh//2+50, text="",
            fill="#aaa", font=("Consolas", 13))

        state = {"sx": None, "sy": None, "rect": None, "result": None}

        def on_press(e):
            state["sx"], state["sy"] = e.x, e.y
        def on_drag(e):
            if state["rect"]: canvas.delete(state["rect"])
            state["rect"] = canvas.create_rectangle(
                state["sx"], state["sy"], e.x, e.y,
                outline="#00ff88", width=2, dash=(6,3))
            canvas.itemconfig(size_txt,
                text=f"{abs(e.x-state['sx'])} x {abs(e.y-state['sy'])}")
        def on_release(e):
            if state["sx"] is None: return
            x1 = min(state["sx"], e.x); y1 = min(state["sy"], e.y)
            x2 = max(state["sx"], e.x); y2 = max(state["sy"], e.y)
            if x2-x1 >= 10 and y2-y1 >= 10:
                state["result"] = (x1, y1, x2-x1, y2-y1)
                top.destroy()
            else:
                state["sx"] = None
                if state["rect"]: canvas.delete(state["rect"]); state["rect"] = None
        def on_full(e):
            state["result"] = None; top.destroy()
        def on_cancel(e):
            state["result"] = "cancel"; top.destroy()

        canvas.bind("<ButtonPress-1>", on_press)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_release)
        top.bind("<Escape>", on_cancel)
        top.bind("<Return>", on_full)

        top.protocol("WM_DELETE_WINDOW", on_cancel)
        top.focus_force()

        def after_destroy():
            region = state["result"]
            self.root.after(0, lambda: self._on_overlay_done(region))
        top.bind("<Destroy>", lambda e: after_destroy())

    def _on_overlay_done(self, region):
        if region == "cancel":
            self._busy = False
            return

        img_bytes = capture_region(region)
        size_kb = len(img_bytes) / 1024
        print(f"📸 截图完成 ({size_kb:.0f}KB)")

        if self.output_path:
            with open(self.output_path, "wb") as f:
                f.write(img_bytes)
            print(f"💾 已保存: {self.output_path}")

        self._show_result_popup(img_bytes)

    # ---- 2. 分析结果弹窗 (Toplevel) ----
    def _show_result_popup(self, img_bytes):
        top = tk.Toplevel(self.root)
        top.title("豆包 AI 分析结果")
        top.configure(bg="#f5f5f5")
        top.attributes("-topmost", True)

        sw, sh = top.winfo_screenwidth(), top.winfo_screenheight()
        w, h = min(700, sw-40), min(500, sh-80)
        top.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")
        top.minsize(400, 300)

        # 标题
        f = tk.Frame(top, bg="#f5f5f5")
        f.pack(fill=tk.X, padx=16, pady=(12,4))
        tk.Label(f, text="豆包 AI 分析结果",
            font=("Microsoft YaHei", 14, "bold"), bg="#f5f5f5", fg="#333"
        ).pack(side=tk.LEFT)
        tk.Frame(top, bg="#ddd", height=1).pack(fill=tk.X, padx=16)

        # 文本区
        txt = scrolledtext.ScrolledText(top, wrap=tk.WORD,
            font=("Microsoft YaHei", 11), bg="white", fg="#333",
            relief=tk.FLAT, padx=12, pady=12, borderwidth=0)
        txt.pack(fill=tk.BOTH, expand=True, padx=16, pady=(8,4))
        txt.insert(tk.END, "⏳ 正在分析中，请稍候...")
        txt.config(state=tk.DISABLED)

        # 底部栏
        bf = tk.Frame(top, bg="#f5f5f5")
        bf.pack(fill=tk.X, padx=16, pady=(4,12))
        status = tk.Label(bf, text="分析中...",
            font=("Microsoft YaHei", 9), bg="#f5f5f5", fg="#999")
        status.pack(side=tk.LEFT)

        full_text = []
        start_t = time.time()

        # ---- 流式更新回调（API 线程 → Tk 主线程）----
        def on_chunk(c):
            self.root.after(0, lambda: _append(c))

        def _append(c):
            full_text.append(c)
            txt.config(state=tk.NORMAL)
            if len(full_text) == 1:
                txt.delete(1.0, tk.END)
            txt.insert(tk.END, c)
            txt.see(tk.END)
            txt.config(state=tk.DISABLED)

        def _done(err=None):
            if err:
                txt.config(state=tk.NORMAL)
                txt.insert(tk.END, f"\n\n❌ 错误: {err}")
                txt.config(state=tk.DISABLED)
                status.config(text="出错")
            else:
                status.config(text=f"分析完成 · {time.time()-start_t:.0f}s")

        def _copy():
            top.clipboard_clear()
            top.clipboard_append("".join(full_text))

        tk.Button(bf, text="复制全文", command=_copy,
            font=("Microsoft YaHei", 10), bg="#e8e8e8", fg="#333",
            relief=tk.FLAT, padx=16, pady=4, cursor="hand2"
        ).pack(side=tk.RIGHT, padx=(8,0))

        def _close():
            top.destroy()
            self._busy = False
            if self.once:
                self.root.destroy()

        tk.Button(bf, text="关闭", command=_close,
            font=("Microsoft YaHei", 10), bg="#1677ff", fg="white",
            relief=tk.FLAT, padx=20, pady=4, cursor="hand2"
        ).pack(side=tk.RIGHT)
        top.protocol("WM_DELETE_WINDOW", _close)

        # ---- 后台线程调 API ----
        def api_thread():
            try:
                call_doubao(self.api_key, img_bytes, self.prompt, self.endpoint_id,
                            on_chunk=on_chunk)
                self.root.after(0, _done)
            except Exception as e:
                self.root.after(0, lambda: _done(str(e)))

        threading.Thread(target=api_thread, daemon=True).start()

    # ---- 启动 ----
    def run(self):
        print(f"\n{'=' * 50}")
        print(f"  截图工具已启动 — 后台待机中")
        print(f"  F9: 框选截图 | 拖拽选区域 | Enter: 全屏 | Esc: 取消")
        print(f"  Ctrl+C 退出")
        print(f"{'=' * 50}\n")
        self._poll()
        self.root.mainloop()


# ------------------------------------------------
# 入口
# ------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Windows 截图 + 豆包 AI 解释工具")
    parser.add_argument("--once", action="store_true", help="单次模式")
    parser.add_argument("-k", "--hotkey", default=DEFAULT_HOTKEY, help="快捷键")
    parser.add_argument("-p", "--prompt", default=DEFAULT_PROMPT, help="自定义提示词")
    parser.add_argument("-o", "--output", default=None, help="保存路径")
    args = parser.parse_args()

    if sys.stdout.encoding != "utf-8":
        try: sys.stdout.reconfigure(encoding="utf-8")
        except: pass

    api_key = load_api_key(API_KEY_PATH)
    endpoint_id = load_endpoint_id(API_KEY_PATH)

    app = ScreenshotApp(api_key, endpoint_id, args.prompt, args.output, once=args.once)
    hotkey = GlobalHotkey(args.hotkey, app._on_hotkey)
    hotkey.start()
    try:
        app.run()
    except KeyboardInterrupt:
        pass
    finally:
        hotkey.stop()


if __name__ == "__main__":
    main()
