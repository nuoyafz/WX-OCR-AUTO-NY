"""
窗口校准向导 — 手动框选聊天区域
===============================
解决DPI缩放和多显示器导致的坐标偏移问题。
让用户在全屏半透明覆盖层上拖拽框选微信聊天区域和侧边栏区域。
"""
import os
import json
import tkinter as tk
from tkinter import messagebox
import pygetwindow as gw
import logging

logger = logging.getLogger(__name__)


class CalibrationWizard:
    """窗口校准向导"""

    def __init__(self, config_path="config.yaml"):
        self.config_path = config_path
        self.results = {}
        self.window = None
        self.canvas = None
        self.start_x = 0
        self.start_y = 0
        self.rect_id = None
        self.current_step = 0
        self.steps = [
            ("聊天区域", "请框选微信的【聊天内容区域】（不含标题栏和输入框）"),
            ("侧边栏区域", "请框选微信的【左侧联系人列表区域】"),
        ]
        self.labels = ["chat_region", "sidebar_region"]

    def find_wechat_window(self):
        """找到微信窗口"""
        windows = gw.getWindowsWithTitle("微信")
        for win in windows:
            title = win.title or ""
            if "AI" in title or "助手" in title:
                continue
            if win.width > 400 and win.height > 300:
                return win
        return None

    def start(self):
        """启动校准向导"""
        wechat = self.find_wechat_window()
        if not wechat:
            messagebox.showerror("错误", "未找到微信窗口，请先打开微信")
            return None

        self.wechat = wechat
        self._run_step()
        return self.results if self.results else None

    def _run_step(self):
        """运行当前步骤"""
        if self.current_step >= len(self.steps):
            self._save_results()
            if self.window:
                self.window.destroy()
            return

        label, instruction = self.steps[self.current_step]

        # 创建全屏覆盖窗口
        if self.window:
            self.window.destroy()

        self.window = tk.Tk()
        self.window.attributes("-fullscreen", True)
        self.window.attributes("-alpha", 0.3)
        self.window.attributes("-topmost", True)
        self.window.configure(bg="black")

        # 顶部提示栏
        tip_frame = tk.Frame(self.window, bg="#1a1a2e", height=80)
        tip_frame.pack(fill="x", side="top")
        tip_frame.pack_propagate(False)

        tk.Label(
            tip_frame, text=f"步骤 {self.current_step + 1}/{len(self.steps)}: {instruction}",
            font=("Microsoft YaHei", 14), bg="#1a1a2e", fg="white", pady=20,
        ).pack()

        btn_frame = tk.Frame(tip_frame, bg="#1a1a2e")
        btn_frame.pack(side="right", padx=20)

        tk.Button(
            btn_frame, text="跳过", command=self._skip_step,
            font=("Microsoft YaHei", 10), bg="#333", fg="white", padx=15,
        ).pack(side="left", padx=5)

        tk.Button(
            btn_frame, text="取消", command=self._cancel,
            font=("Microsoft YaHei", 10), bg="#e74c3c", fg="white", padx=15,
        ).pack(side="left", padx=5)

        # 绘图画布
        self.canvas = tk.Canvas(self.window, bg="black", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        # 微信窗口边框提示
        wx = self.wechat.left
        wy = self.wechat.top
        ww = self.wechat.width
        wh = self.wechat.height
        self.canvas.create_rectangle(wx, wy, wx + ww, wy + wh, outline="#3b82f6", width=2)
        self.canvas.create_text(
            wx + ww // 2, wy + 20,
            text="微信窗口", fill="#3b82f6", font=("Microsoft YaHei", 12),
        )

        # 鼠标事件
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)

        # 显示已校准的区域
        for key, val in self.results.items():
            if val:
                self.canvas.create_rectangle(
                    val["left"], val["top"],
                    val["left"] + val["width"], val["top"] + val["height"],
                    outline="#10b981", width=2, dash=(5, 5),
                )

        self.window.mainloop()

    def _on_press(self, event):
        self.start_x = event.x
        self.start_y = event.y
        if self.rect_id:
            self.canvas.delete(self.rect_id)
        self.rect_id = self.canvas.create_rectangle(
            self.start_x, self.start_y, self.start_x, self.start_y,
            outline="#ef4444", width=3,
        )

    def _on_drag(self, event):
        if self.rect_id:
            self.canvas.coords(self.rect_id, self.start_x, self.start_y, event.x, event.y)
            # 显示尺寸
            w = abs(event.x - self.start_x)
            h = abs(event.y - self.start_y)
            self.canvas.delete("size_text")
            self.canvas.create_text(
                event.x, event.y - 15, text=f"{w}x{h}",
                fill="#ef4444", font=("Microsoft YaHei", 12), tags="size_text",
            )

    def _on_release(self, event):
        x1 = min(self.start_x, event.x)
        y1 = min(self.start_y, event.y)
        x2 = max(self.start_x, event.x)
        y2 = max(self.start_y, event.y)

        w = x2 - x1
        h = y2 - y1

        if w < 50 or h < 50:
            messagebox.showwarning("区域太小", "框选区域太小，请重新框选")
            self.canvas.delete(self.rect_id)
            return

        key = self.labels[self.current_step]
        self.results[key] = {
            "left": x1,
            "top": y1,
            "width": w,
            "height": h,
        }

        # 确认
        result = messagebox.askyesno(
            "确认区域",
            f"区域: ({x1}, {y1}) 尺寸: {w}x{h}\n确认无误？"
        )
        if result:
            self.current_step += 1
            self._run_step()
        else:
            self.canvas.delete(self.rect_id)
            self.results.pop(key, None)

    def _skip_step(self):
        """跳过当前步骤"""
        key = self.labels[self.current_step]
        self.results[key] = None
        self.current_step += 1
        self._run_step()

    def _cancel(self):
        """取消校准"""
        self.results = {}
        if self.window:
            self.window.destroy()

    def _save_results(self):
        """保存校准结果到config"""
        try:
            import yaml
            config = {}
            if os.path.exists(self.config_path):
                with open(self.config_path, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f) or {}

            if "calibration" not in config:
                config["calibration"] = {}

            for key, val in self.results.items():
                if val:
                    config["calibration"][key] = val
                    logger.info(f"校准 {key}: {val}")

            with open(self.config_path, "w", encoding="utf-8") as f:
                yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

            logger.info(f"校准结果已保存到 {self.config_path}")
        except Exception as e:
            logger.error(f"保存校准结果失败: {e}")


def run_calibration(config_path="config.yaml"):
    """运行校准向导"""
    wizard = CalibrationWizard(config_path)
    return wizard.start()