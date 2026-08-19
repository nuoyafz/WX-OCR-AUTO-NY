import ctypes
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass
"""
微信 AI 助手 — UI主界面
========================
基于 customtkinter 的现代化桌面界面，适合小白使用。
功能：信息提取监控 / 自动回复开关 / 数据查看 / 设置
"""
import os
import threading
import time
import customtkinter as ctk
from tkinter import messagebox, filedialog
from datetime import datetime

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

# 微信PC风格配色
WC_COLORS = {
    "bg": "#EDEDED",
    "sidebar": "#2E2E2E",
    "sidebar_hover": "#3A3A3A",
    "sidebar_active": "#07C160",
    "card": "#FFFFFF",
    "card_hover": "#F5F5F5",
    "accent": "#07C160",
    "accent_hover": "#06AE56",
    "danger": "#FA5151",
    "text": "#191919",
    "text_muted": "#888888",
    "text_dark": "#FFFFFF",
    "border": "#E5E5E5",
    "bubble_self": "#95EC69",
    "bubble_other": "#FFFFFF",
    "header": "#F7F7F7",
    "online_dot": "#07C160",
}


class WeChatAIApp(ctk.CTk):
    """主应用窗口 — 微信PC风格"""

    def __init__(self, config_path="config.yaml"):
        super().__init__()
        self.config_path = config_path
        self.engine = None
        self.config_data = None
        # 跨线程预览队列（监控线程→UI主线程），避免 after_idle 线程安全问题
        import queue
        self._capture_queue = queue.Queue(maxsize=2)

        self.title("微信 AI 助手")
        self.geometry("1100x720")
        self.minsize(1000, 650)
        self.configure(fg_color=WC_COLORS["bg"])

        self._load_config()
        self._build_ui()

        # 启动预览队列轮询（必须在主线程，跨线程安全）
        self.after(100, self._poll_capture)

    def _load_config(self):
        import yaml
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), self.config_path)
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                self.config_data = yaml.safe_load(f) or {}
        except Exception:
            self.config_data = {}

    def _build_ui(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # ====== 左侧导航栏（微信风格图标导航） ======
        self.sidebar = ctk.CTkFrame(self, fg_color=WC_COLORS["sidebar"], width=64, corner_radius=0)
        self.sidebar.grid(row=0, column=0, sticky="ns")
        self.sidebar.grid_propagate(False)

        # 顶部Logo
        logo_label = ctk.CTkLabel(self.sidebar, text="🤖", font=ctk.CTkFont(size=24))
        logo_label.grid(row=0, column=0, pady=(20, 30))

        # 导航按钮
        nav_items = [
            ("📡", "monitor", "监控", True),
            ("💬", "reply", "回复", False),
            ("📊", "data", "数据", False),
            ("⚙️", "settings", "设置", False),
        ]
        self._nav_buttons = {}
        for i, (icon, key, label, active) in enumerate(nav_items):
            btn = ctk.CTkButton(
                self.sidebar, text=icon, width=44, height=44,
                fg_color="transparent", hover_color=WC_COLORS["sidebar_hover"],
                corner_radius=8, font=ctk.CTkFont(size=18),
                command=lambda k=key: self._switch_nav(k),
            )
            btn.grid(row=i + 1, column=0, pady=5, padx=10)
            if active:
                btn.configure(fg_color=WC_COLORS["sidebar_active"])
            self._nav_buttons[key] = btn

        # 底部状态
        self.sidebar_status = ctk.CTkLabel(
            self.sidebar, text="● 待机",
            font=ctk.CTkFont(size=11), text_color="#888888",
        )
        self.sidebar_status.grid(row=10, column=0, pady=(30, 15))

        # ====== 主内容区 ======
        self.main_content = ctk.CTkFrame(self, fg_color=WC_COLORS["bg"], corner_radius=0)
        self.main_content.grid(row=0, column=1, sticky="nsew")
        self.main_content.grid_columnconfigure(0, weight=1)
        self.main_content.grid_rowconfigure(0, weight=1)

        # 创建Tabview放在主内容区
        self.tabs = ctk.CTkTabview(self.main_content, fg_color="transparent")
        self.tabs.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)

        self.tab_monitor = self.tabs.add("📡 监控")
        self.tab_reply = self.tabs.add("💬 自动回复")
        self.tab_data = self.tabs.add("📊 数据")
        self.tab_settings = self.tabs.add("⚙️ 设置")

        # 隐藏默认tab切换器，用侧边栏导航
        self.tabs._segmented_button.grid_remove()

        self._build_monitor_tab()
        self._build_reply_tab()
        self._build_data_tab()
        self._build_settings_tab()

    def _switch_nav(self, key):
        """切换导航"""
        tab_map = {
            "monitor": "📡 监控",
            "reply": "💬 自动回复",
            "data": "📊 数据",
            "settings": "⚙️ 设置",
        }
        for k, btn in self._nav_buttons.items():
            if k == key:
                btn.configure(fg_color=WC_COLORS["sidebar_active"])
            else:
                btn.configure(fg_color="transparent")
        if key in tab_map:
            self.tabs.set(tab_map[key])

    def _build_monitor_tab(self):
        tab = self.tab_monitor
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(2, weight=1)

        # 顶部控制条 — 微信风格
        ctrl = ctk.CTkFrame(tab, fg_color=WC_COLORS["header"], corner_radius=10, height=60)
        ctrl.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 5))
        ctrl.grid_columnconfigure(6, weight=1)

        self.btn_start = ctk.CTkButton(
            ctrl, text="▶ 开始监控", width=120, height=36,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color=WC_COLORS["accent"], hover_color=WC_COLORS["accent_hover"],
            command=self.start_monitoring,
        )
        self.btn_start.grid(row=0, column=0, padx=(15, 5), pady=12)

        self.btn_stop = ctk.CTkButton(
            ctrl, text="■ 停止", width=90, height=36,
            font=ctk.CTkFont(size=13),
            fg_color=WC_COLORS["danger"], hover_color="#E04848",
            command=self.stop_monitoring, state="disabled",
        )
        self.btn_stop.grid(row=0, column=1, padx=5, pady=12)

        self.btn_test = ctk.CTkButton(
            ctrl, text="🔍 识别", width=80, height=36,
            font=ctk.CTkFont(size=13),
            fg_color="#8A8A8A", hover_color="#7A7A7A",
            command=self.test_ocr,
        )
        self.btn_test.grid(row=0, column=2, padx=5, pady=12)

        self.btn_preview = ctk.CTkButton(
            ctrl, text="🖼 预览", command=self._open_preview_window,
            fg_color="#6B5CE7", hover_color="#5B4CD0", width=80, height=36,
            font=ctk.CTkFont(size=13))
        self.btn_preview.grid(row=0, column=3, padx=5, pady=12)

        # 一键显示微信（窗口被移到屏幕外后，找不到时点击恢复）
        self.btn_show_wechat = ctk.CTkButton(
            ctrl, text="👁 显示微信", width=90, height=36,
            font=ctk.CTkFont(size=13),
            fg_color="#27AE60", hover_color="#229954",
            command=self._show_wechat_window,
        )
        self.btn_show_wechat.grid(row=0, column=4, padx=5, pady=12)

        # 统计信息 — 紧凑横排
        stats_frame = ctk.CTkFrame(tab, fg_color=WC_COLORS["card"], corner_radius=10)
        stats_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=5)
        for i in range(6):
            stats_frame.grid_columnconfigure(i, weight=1)

        self.stat_labels = {}
        stat_items = [
            ("frames", "📷 截图帧"), ("ocr", "🔤 OCR"),
            ("messages", "💬 消息"), ("extracted", "📋 提取"),
            ("important", "⭐ 重要"), ("replies", "✉️ 回复"),
        ]
        for i, (key, label) in enumerate(stat_items):
            ctk.CTkLabel(stats_frame, text=label,
                        font=ctk.CTkFont(size=11), text_color=WC_COLORS["text_muted"]).grid(
                row=0, column=i, padx=5, pady=(8, 2))
            val_label = ctk.CTkLabel(stats_frame, text="0",
                                     font=ctk.CTkFont(size=20, weight="bold"),
                                     text_color=WC_COLORS["accent"])
            val_label.grid(row=1, column=i, padx=5, pady=(2, 8))
            self.stat_labels[key] = val_label

        # 标签页：实时消息 / 预览 / 运行日志
        self.view_tabview = ctk.CTkTabview(tab, fg_color=WC_COLORS["card"], corner_radius=10)
        self.view_tabview.grid(row=2, column=0, sticky="nsew", padx=10, pady=(5, 10))

        tab_msg = self.view_tabview.add("💬 消息")
        tab_preview = self.view_tabview.add("🖥️ 预览")
        tab_log = self.view_tabview.add("📝 日志")

        # 实时消息面板
        self._create_message_panel(tab_msg)

        # 主界面截图预览区
        self._create_main_preview(tab_preview)

        # 运行日志
        self.log_text = ctk.CTkTextbox(
            tab_log, font=ctk.CTkFont(size=12),
            fg_color=WC_COLORS["bg"], text_color=WC_COLORS["text"],
            wrap="word", state="disabled",
        )
        self.log_text.pack(fill="both", expand=True, padx=8, pady=8)

        # 提取结果
        result_frame = ctk.CTkFrame(tab_log, fg_color=WC_COLORS["bg"], corner_radius=8)
        result_frame.pack(fill="x", padx=8, pady=(0, 8))

        ctk.CTkLabel(result_frame, text="📋 最新提取结果",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=WC_COLORS["text"]).pack(anchor="w", padx=10, pady=(8, 4))

        self.result_text = ctk.CTkTextbox(
            result_frame, font=ctk.CTkFont(size=12),
            fg_color=WC_COLORS["card"], text_color=WC_COLORS["text"],
            wrap="word", state="disabled", height=120,
        )
        self.result_text.pack(fill="x", padx=10, pady=(0, 8))

    def _create_main_preview(self, parent):
        """主界面截图预览区"""
        import tkinter as tk
        from PIL import Image, ImageTk

        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)

        # 顶部状态栏
        header = tk.Frame(parent, bg="#2E2E2E", height=36)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)

        self.main_preview_title = tk.Label(
            header, text="🖥️ 实时画面", bg="#2E2E2E", fg="#FFFFFF",
            font=("Microsoft YaHei", 11, "bold"))
        self.main_preview_title.pack(side="left", padx=12, pady=8)

        self.main_preview_status = tk.Label(
            header, text="等待截图...", bg="#2E2E2E", fg="#888888",
            font=("Microsoft YaHei", 9))
        self.main_preview_status.pack(side="right", padx=12, pady=8)

        # 截图显示区
        self.main_preview_label = tk.Label(
            parent, bg="#000000",
            highlightbackground="#444444", highlightthickness=1)
        self.main_preview_label.grid(row=1, column=0, sticky="nsew", padx=8, pady=8)

        # 占位图
        try:
            placeholder = Image.new("RGB", (400, 300), color="#1A1A1A")
            self._main_placeholder_photo = ImageTk.PhotoImage(placeholder)
            self.main_preview_label.configure(image=self._main_placeholder_photo)
            self.main_preview_label.image = self._main_placeholder_photo
        except Exception:
            pass

    def _build_reply_tab(self):
        tab = self.tab_reply
        tab.grid_columnconfigure(0, weight=1)

        # 自动回复开关
        switch_frame = ctk.CTkFrame(tab, fg_color=WC_COLORS["card"], corner_radius=10)
        switch_frame.grid(row=0, column=0, sticky="ew", padx=20, pady=20)
        switch_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(switch_frame, text="💬 自动回复",
                     font=ctk.CTkFont(size=16, weight="bold"),
                     text_color=WC_COLORS["text"]).grid(row=0, column=0, padx=15, pady=(15, 5), sticky="w")

        ctk.CTkLabel(switch_frame, text="开启后将自动回复对方消息（默认关闭，仅提取信息）",
                     font=ctk.CTkFont(size=12),
                     text_color=WC_COLORS["text_muted"]).grid(row=1, column=0, padx=15, pady=(0, 10), sticky="w")

        self.reply_switch = ctk.CTkSwitch(
            switch_frame, text="启用自动回复",
            font=ctk.CTkFont(size=14),
            command=self.on_reply_switch,
            fg_color=WC_COLORS["accent"],
        )
        auto_reply_cfg = self.config_data.get("auto_reply", {})
        self.reply_switch.select() if auto_reply_cfg.get("enabled") else self.reply_switch.deselect()
        self.reply_switch.grid(row=2, column=0, padx=15, pady=(0, 15), sticky="w")

        # 角色说明
        roles_frame = ctk.CTkFrame(tab, fg_color=WC_COLORS["card"], corner_radius=10)
        roles_frame.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0, 20))
        roles_frame.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(roles_frame, text="👤 角色配置",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=WC_COLORS["text"]).grid(row=0, column=0, padx=15, pady=(15, 10), sticky="w")

        roles = self.config_data.get("roles", {})
        for i, (role_key, role) in enumerate(roles.items()):
            role_card = ctk.CTkFrame(roles_frame, fg_color=WC_COLORS["bg"], corner_radius=8)
            role_card.grid(row=i + 1, column=0, sticky="ew", padx=15, pady=3)
            role_card.grid_columnconfigure(1, weight=1)

            ctk.CTkLabel(role_card, text=role.get("name", role_key),
                         font=ctk.CTkFont(size=13, weight="bold"),
                         text_color=WC_COLORS["accent"]).grid(row=0, column=0, padx=10, pady=8, sticky="w")
            ctk.CTkLabel(role_card, text=role.get("reply_style", ""),
                         font=ctk.CTkFont(size=11),
                         text_color=WC_COLORS["text_muted"]).grid(row=0, column=1, padx=10, pady=8, sticky="w")

    def _build_data_tab(self):
        tab = self.tab_data
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(4, weight=1)

        # 按钮区
        btn_frame = ctk.CTkFrame(tab, fg_color="transparent")
        btn_frame.grid(row=0, column=0, sticky="ew", padx=10, pady=10)
        btn_frame.grid_columnconfigure(3, weight=1)

        ctk.CTkButton(btn_frame, text="刷新数据", width=100, height=32,
                      font=ctk.CTkFont(size=13),
                      command=self.refresh_data).grid(row=0, column=0, padx=5)

        ctk.CTkButton(btn_frame, text="导出CSV", width=100, height=32,
                      font=ctk.CTkFont(size=13),
                      fg_color=WC_COLORS["accent"], hover_color=WC_COLORS["accent_hover"],
                      command=self.export_csv).grid(row=0, column=1, padx=5)

        ctk.CTkButton(btn_frame, text="导出JSON", width=100, height=32,
                      font=ctk.CTkFont(size=13),
                      fg_color=WC_COLORS["accent"], hover_color=WC_COLORS["accent_hover"],
                      command=self.export_json).grid(row=0, column=2, padx=5)

        self.data_stats_label = ctk.CTkLabel(
            btn_frame, text="暂无数据",
            font=ctk.CTkFont(size=12), text_color=WC_COLORS["text_muted"])
        self.data_stats_label.grid(row=0, column=3, padx=10, sticky="e")

        # 搜索区域
        search_frame = ctk.CTkFrame(tab, fg_color=WC_COLORS["card"], corner_radius=8)
        search_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=5)

        ctk.CTkLabel(search_frame, text="消息搜索",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=WC_COLORS["text"]).pack(anchor="w", padx=10, pady=5)

        search_input_frame = ctk.CTkFrame(search_frame, fg_color="transparent")
        search_input_frame.pack(fill="x", padx=10, pady=5)

        self.search_entry = ctk.CTkEntry(search_input_frame, placeholder_text="输入关键词搜索消息内容...")
        self.search_entry.pack(side="left", fill="x", expand=True, padx=(0, 5))

        ctk.CTkButton(search_input_frame, text="搜索", width=80,
                      command=self._search_messages).pack(side="left")

        ctk.CTkButton(search_input_frame, text="重要消息", width=80,
                      command=self._show_important_messages).pack(side="left", padx=5)

        ctk.CTkButton(search_input_frame, text="今日报告", width=80,
                      command=self._generate_today_report).pack(side="left")

        # 统计面板
        stats_container = ctk.CTkFrame(tab, fg_color="transparent")
        stats_container.grid(row=2, column=0, sticky="ew", padx=10, pady=5)
        self._create_stats_panel(stats_container)

        # 搜索面板
        search_container = ctk.CTkFrame(tab, fg_color="transparent")
        search_container.grid(row=3, column=0, sticky="ew", padx=10, pady=5)
        self._create_search_panel(search_container)

        # 数据表格
        table_frame = ctk.CTkFrame(tab, fg_color=WC_COLORS["card"], corner_radius=8)
        table_frame.grid(row=4, column=0, sticky="nsew", padx=10, pady=(0, 10))
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)

        self.data_text = ctk.CTkTextbox(
            table_frame, font=ctk.CTkFont(size=12),
            fg_color=WC_COLORS["bg"], text_color=WC_COLORS["text"],
            wrap="word", state="disabled",
        )
        self.data_text.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)

    def _build_settings_tab(self):
        tab = self.tab_settings
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)

        # 使用可滚动容器，解决设置项过多无法查看的问题
        scroll = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        scroll.grid(row=0, column=0, sticky="nsew")

        # 消息监控模式
        monitor_frame = ctk.CTkFrame(scroll, fg_color=WC_COLORS["card"], corner_radius=8)
        monitor_frame.pack(fill="x", padx=20, pady=20)
        monitor_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(monitor_frame, text="消息监控模式",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=WC_COLORS["text"]).grid(row=0, column=0, columnspan=2, padx=15, pady=(15, 10), sticky="w")

        ctk.CTkLabel(monitor_frame, text="选择如何监控新消息",
                     font=ctk.CTkFont(size=12),
                     text_color=WC_COLORS["text_muted"]).grid(row=1, column=0, columnspan=2, padx=15, pady=(0, 10), sticky="w")

        rd_cfg = self.config_data.get("red_dot_monitor", {})
        cs_cfg = self.config_data.get("contact_scanner", {})

        self.monitor_var = ctk.StringVar(value="current")
        if rd_cfg.get("enabled"):
            self.monitor_var.set("red_dot")
        elif cs_cfg.get("enabled"):
            self.monitor_var.set("scan")

        ctk.CTkRadioButton(monitor_frame, text="仅当前窗口（默认）",
                           variable=self.monitor_var, value="current",
                           font=ctk.CTkFont(size=13),
                           text_color=WC_COLORS["text"]).grid(row=2, column=0, columnspan=2, padx=25, pady=3, sticky="w")
        ctk.CTkRadioButton(monitor_frame, text="红点监控 — 自动检测左侧未读红点并切换",
                           variable=self.monitor_var, value="red_dot",
                           font=ctk.CTkFont(size=13),
                           text_color=WC_COLORS["text"]).grid(row=3, column=0, columnspan=2, padx=25, pady=3, sticky="w")
        ctk.CTkRadioButton(monitor_frame, text="轮询扫描 — 定时遍历联系人列表",
                           variable=self.monitor_var, value="scan",
                           font=ctk.CTkFont(size=13),
                           text_color=WC_COLORS["text"]).grid(row=4, column=0, columnspan=2, padx=25, pady=(3, 15), sticky="w")

        # 联系人过滤
        filter_frame = ctk.CTkFrame(scroll, fg_color=WC_COLORS["card"], corner_radius=8)
        filter_frame.pack(fill="x", padx=20, pady=5)
        ctk.CTkLabel(filter_frame, text="👥 联系人过滤",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=WC_COLORS["text"]).pack(anchor="w", padx=15, pady=(10, 5))

        ctk.CTkLabel(filter_frame, text="白名单（逗号分隔，留空=全部监控）:",
                     font=ctk.CTkFont(size=12), text_color=WC_COLORS["text_muted"]).pack(anchor="w", padx=15, pady=(5, 0))
        self.whitelist_entry = ctk.CTkEntry(filter_frame, width=300, placeholder_text="如: 张三,李四,*群*")
        self.whitelist_entry.pack(fill="x", padx=15, pady=2)
        # 回填当前配置中的白名单（强制转str，防止yaml把10086解析成int）
        _filter_cfg = self.config_data.get("contacts_filter", {})
        _wl = _filter_cfg.get("whitelist", []) or []
        if _wl:
            self.whitelist_entry.insert(0, ",".join(str(x) for x in _wl))

        ctk.CTkLabel(filter_frame, text="黑名单（逗号分隔，已默认包含公众号/服务号/订阅号等）:",
                     font=ctk.CTkFont(size=12), text_color=WC_COLORS["text_muted"]).pack(anchor="w", padx=15, pady=(5, 0))
        self.blacklist_entry = ctk.CTkEntry(filter_frame, width=300, placeholder_text="如: 拼多多,瑞幸,*服务号*")
        self.blacklist_entry.pack(fill="x", padx=15, pady=(0, 10))
        # 回填当前配置中的黑名单（强制转str，防止yaml把10086解析成int）
        _bl = _filter_cfg.get("blacklist", []) or []
        if _bl:
            self.blacklist_entry.insert(0, ",".join(str(x) for x in _bl))

        # 高级设置开关
        self.advanced_settings_var = ctk.BooleanVar(value=False)
        advanced_switch = ctk.CTkSwitch(
            scroll,
            text="显示高级设置",
            variable=self.advanced_settings_var,
            command=self._toggle_advanced_settings
        )
        advanced_switch.pack(pady=5, anchor="w", padx=20)

        # 校准向导
        calib_frame = ctk.CTkFrame(scroll, fg_color=WC_COLORS["card"], corner_radius=8)
        calib_frame.pack(fill="x", padx=20, pady=10)

        ctk.CTkLabel(calib_frame, text="窗口校准",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=WC_COLORS["text"]).pack(anchor="w", padx=15, pady=5)
        ctk.CTkLabel(calib_frame, text="手动框选微信聊天区域，解决DPI缩放导致的坐标偏移",
                     font=ctk.CTkFont(size=11), text_color="gray").pack(anchor="w", padx=15)

        ctk.CTkButton(calib_frame, text="启动校准向导",
                      command=self._run_calibration).pack(padx=15, pady=10, anchor="w")

        # 模式开关
        mode_frame = ctk.CTkFrame(scroll, fg_color=WC_COLORS["card"], corner_radius=8)
        mode_frame.pack(fill="x", padx=20, pady=10)

        ctk.CTkLabel(mode_frame, text="智能模式",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=WC_COLORS["text"]).pack(anchor="w", padx=15, pady=5)

        self.fast_mode_switch = ctk.CTkSwitch(mode_frame, text="快速模式（窗口标题检测，省CPU）",
                                              onvalue=True, offvalue=False)
        self.fast_mode_switch.pack(anchor="w", padx=15, pady=3)

        self.dnd_switch = ctk.CTkSwitch(mode_frame, text="勿扰模式（检测到用户操作时暂停）",
                                        onvalue=True, offvalue=False)
        self.dnd_switch.pack(anchor="w", padx=15, pady=3)

        # 报告设置
        report_frame = ctk.CTkFrame(scroll, fg_color=WC_COLORS["card"], corner_radius=8)
        report_frame.pack(fill="x", padx=20, pady=10)

        ctk.CTkLabel(report_frame, text="定时报告",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=WC_COLORS["text"]).pack(anchor="w", padx=15, pady=5)

        report_btn_frame = ctk.CTkFrame(report_frame, fg_color="transparent")
        report_btn_frame.pack(fill="x", padx=15, pady=5)

        ctk.CTkButton(report_btn_frame, text="生成今日报告",
                      command=self._generate_today_report).pack(side="left", padx=5)

        ctk.CTkButton(report_btn_frame, text="生成本周报告",
                      command=self._generate_weekly_report).pack(side="left", padx=5)

        ctk.CTkButton(report_btn_frame, text="打开报告目录",
                      command=self._open_report_dir).pack(side="left", padx=5)

        # LLM设置
        llm_frame = ctk.CTkFrame(scroll, fg_color=WC_COLORS["card"], corner_radius=8)
        llm_frame.pack(fill="x", padx=20, pady=20)
        llm_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(llm_frame, text="LLM 配置",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=WC_COLORS["text"]).grid(row=0, column=0, columnspan=2, padx=15, pady=(15, 10), sticky="w")

        llm_cfg = self.config_data.get("llm", {})
        ctk.CTkLabel(llm_frame, text="API地址", font=ctk.CTkFont(size=12),
                     text_color=WC_COLORS["text_muted"]).grid(row=1, column=0, padx=15, pady=5, sticky="w")
        self.entry_url = ctk.CTkEntry(llm_frame, font=ctk.CTkFont(size=12))
        self.entry_url.insert(0, llm_cfg.get("base_url", ""))
        self.entry_url.grid(row=1, column=1, padx=15, pady=5, sticky="ew")

        ctk.CTkLabel(llm_frame, text="API Key", font=ctk.CTkFont(size=12),
                     text_color=WC_COLORS["text_muted"]).grid(row=2, column=0, padx=15, pady=5, sticky="w")
        self.entry_key = ctk.CTkEntry(llm_frame, font=ctk.CTkFont(size=12), show="*")
        self.entry_key.insert(0, llm_cfg.get("api_key", ""))
        self.entry_key.grid(row=2, column=1, padx=15, pady=5, sticky="ew")

        ctk.CTkLabel(llm_frame, text="模型", font=ctk.CTkFont(size=12),
                     text_color=WC_COLORS["text_muted"]).grid(row=3, column=0, padx=15, pady=5, sticky="w")
        self.entry_model = ctk.CTkEntry(llm_frame, font=ctk.CTkFont(size=12))
        self.entry_model.insert(0, llm_cfg.get("model", ""))
        self.entry_model.grid(row=3, column=1, padx=15, pady=5, sticky="ew")

        # 高级设置（默认隐藏）
        self.advanced_frame = ctk.CTkFrame(scroll, fg_color="transparent")

        # OCR设置
        ocr_frame = ctk.CTkFrame(self.advanced_frame, fg_color=WC_COLORS["card"], corner_radius=8)
        ocr_frame.pack(fill="x", padx=20, pady=(0, 20))
        ocr_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(ocr_frame, text="OCR 识别优化",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=WC_COLORS["text"]).grid(row=0, column=0, columnspan=2, padx=15, pady=(15, 10), sticky="w")

        wechat_cfg = self.config_data.get("wechat", {})
        ctk.CTkLabel(ocr_frame, text="识别精度 (0.5-1.0)", font=ctk.CTkFont(size=12),
                     text_color=WC_COLORS["text_muted"]).grid(row=1, column=0, padx=15, pady=5, sticky="w")
        self.slider_scale = ctk.CTkSlider(ocr_frame, from_=0.5, to=1.0, number_of_steps=10)
        self.slider_scale.set(wechat_cfg.get("ocr_scale", 0.85))
        self.slider_scale.grid(row=1, column=1, padx=15, pady=5, sticky="ew")

        ctk.CTkLabel(ocr_frame, text="最低置信度 (0.3-0.9)", font=ctk.CTkFont(size=12),
                     text_color=WC_COLORS["text_muted"]).grid(row=2, column=0, padx=15, pady=5, sticky="w")
        self.slider_conf = ctk.CTkSlider(ocr_frame, from_=0.3, to=0.9, number_of_steps=12)
        self.slider_conf.set(wechat_cfg.get("ocr_min_confidence", 0.60))
        self.slider_conf.grid(row=2, column=1, padx=15, pady=5, sticky="ew")

        self.switch_merge = ctk.CTkSwitch(ocr_frame, text="合并同气泡多行文本",
                                          font=ctk.CTkFont(size=12))
        if wechat_cfg.get("ocr_merge_bubble", True):
            self.switch_merge.select()
        self.switch_merge.grid(row=3, column=0, columnspan=2, padx=15, pady=5, sticky="w")

        self.switch_denoise = ctk.CTkSwitch(ocr_frame, text="图片预处理（降噪+锐化）",
                                            font=ctk.CTkFont(size=12))
        if wechat_cfg.get("ocr_denoise", True):
            self.switch_denoise.select()
        self.switch_denoise.grid(row=4, column=0, columnspan=2, padx=15, pady=5, sticky="w")

        # AI训练设置
        ai_frame = ctk.CTkFrame(scroll, fg_color=WC_COLORS["card"], corner_radius=8)
        ai_frame.pack(fill="x", padx=20, pady=10)
        ai_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(ai_frame, text="AI 智能学习",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=WC_COLORS["text"]).grid(row=0, column=0, columnspan=2, padx=15, pady=(15, 10), sticky="w")

        ctk.CTkLabel(ai_frame, text="前10次运行AI辅助学习识别规则，之后自动切换纯规则模式",
                     font=ctk.CTkFont(size=11), text_color="gray").grid(row=1, column=0, columnspan=2, padx=15, pady=(0, 10), sticky="w")

        ai_btn_frame = ctk.CTkFrame(ai_frame, fg_color="transparent")
        ai_btn_frame.grid(row=2, column=0, columnspan=2, padx=15, pady=(0, 15), sticky="w")

        ctk.CTkButton(ai_btn_frame, text="查看/编辑规则库",
                      command=self._open_rules_file).pack(side="left", padx=5)
        ctk.CTkButton(ai_btn_frame, text="重置训练",
                      command=self._reset_ai_training).pack(side="left", padx=5)

        # ==================== Obsidian 同步设置 ====================
        obsidian_frame = ctk.CTkFrame(scroll)
        obsidian_frame.pack(fill="x", padx=10, pady=10)

        ctk.CTkLabel(obsidian_frame, text="Obsidian 同步",
                    font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", padx=15, pady=(10, 5))

        ctk.CTkLabel(obsidian_frame, text="将提取的消息同步到Obsidian vault，支持文件直写和REST API双模式",
                    font=ctk.CTkFont(size=12)).pack(anchor="w", padx=15, pady=(0, 10))

        # 启用同步
        self.obsidian_enabled_var = ctk.BooleanVar(value=self.config_data.get("obsidian", {}).get("auto_sync", False))
        ctk.CTkSwitch(obsidian_frame, text="启用自动同步",
                     variable=self.obsidian_enabled_var).pack(anchor="w", padx=15, pady=5)

        # Vault路径
        ctk.CTkLabel(obsidian_frame, text="Vault 路径:").pack(anchor="w", padx=15, pady=(5, 0))
        self.obsidian_vault_entry = ctk.CTkEntry(obsidian_frame, width=400,
                        placeholder_text="C:\\Users\\xxx\\Documents\\MyVault")
        self.obsidian_vault_entry.pack(fill="x", padx=15, pady=2)
        vault_path = self.config_data.get("obsidian", {}).get("vault_path", "")
        if vault_path:
            self.obsidian_vault_entry.insert(0, vault_path)

        # 同步模式
        ctk.CTkLabel(obsidian_frame, text="同步模式:").pack(anchor="w", padx=15, pady=(10, 0))
        self.obsidian_mode_var = ctk.StringVar(value=self.config_data.get("obsidian", {}).get("mode", "file"))
        ctk.CTkSegmentedButton(obsidian_frame, variable=self.obsidian_mode_var,
                              values=["file", "api", "both"]).pack(anchor="w", padx=15, pady=2)

        # API URL
        ctk.CTkLabel(obsidian_frame, text="REST API 地址:").pack(anchor="w", padx=15, pady=(10, 0))
        self.obsidian_api_url_entry = ctk.CTkEntry(obsidian_frame, width=300,
                        placeholder_text="http://127.0.0.1:27124")
        self.obsidian_api_url_entry.pack(fill="x", padx=15, pady=2)
        api_url = self.config_data.get("obsidian", {}).get("api_url", "http://127.0.0.1:27124")
        if api_url:
            self.obsidian_api_url_entry.insert(0, api_url)

        # API Key
        ctk.CTkLabel(obsidian_frame, text="REST API 密钥:").pack(anchor="w", padx=15, pady=(10, 0))
        self.obsidian_api_key_entry = ctk.CTkEntry(obsidian_frame, width=300, show="*",
                        placeholder_text="在Obsidian Local REST API插件设置中获取")
        self.obsidian_api_key_entry.pack(fill="x", padx=15, pady=2)
        api_key = self.config_data.get("obsidian", {}).get("api_key", "")
        if api_key:
            self.obsidian_api_key_entry.insert(0, api_key)

        # 按钮行
        btn_frame = ctk.CTkFrame(obsidian_frame, fg_color="transparent")
        btn_frame.pack(fill="x", padx=15, pady=10)

        ctk.CTkButton(btn_frame, text="测试API连接", width=120,
                     command=self._test_obsidian_api).pack(side="left", padx=(0, 10))

        ctk.CTkButton(btn_frame, text="重建Vault", width=120,
                     command=self._rebuild_obsidian_vault).pack(side="left", padx=(0, 10))

        ctk.CTkLabel(obsidian_frame, text="提示: 文件模式无需安装插件，直接写入Vault目录。\n"
                    "API模式需要在Obsidian中安装'Local REST API'插件。",
                    font=ctk.CTkFont(size=11)).pack(anchor="w", padx=15, pady=(0, 10))

        # 保存按钮
        ctk.CTkButton(scroll, text="保存设置", width=120, height=36,
                      font=ctk.CTkFont(size=14, weight="bold"),
                      fg_color=WC_COLORS["accent"], hover_color=WC_COLORS["accent_hover"],
                      command=self.save_settings).pack(pady=(0, 20))

    def _create_message_panel(self, parent):
        """创建微信风格消息面板"""
        # 消息头部：当前会话信息
        chat_header = ctk.CTkFrame(parent, fg_color=WC_COLORS["header"], height=48, corner_radius=8)
        chat_header.pack(fill="x", padx=5, pady=(5, 0))
        chat_header.pack_propagate(False)

        self.chat_title = ctk.CTkLabel(
            chat_header, text="💬 实时消息监控",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=WC_COLORS["text"],
        )
        self.chat_title.pack(side="left", padx=15, pady=10)

        self.chat_subtitle = ctk.CTkLabel(
            chat_header, text="等待新消息...",
            font=ctk.CTkFont(size=11),
            text_color=WC_COLORS["online_dot"],
        )
        self.chat_subtitle.pack(side="right", padx=15, pady=10)

        # 消息列表
        self.msg_list_frame = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        self.msg_list_frame.pack(fill="both", expand=True, padx=5, pady=5)

        # 空状态
        self.msg_empty_label = ctk.CTkLabel(
            self.msg_list_frame,
            text="还没有消息\n启动监控后，新消息将以气泡形式显示",
            text_color=WC_COLORS["text_muted"],
            font=ctk.CTkFont(size=13),
            justify="center",
        )
        self.msg_empty_label.pack(pady=60)

        # 底部统计
        bottom_bar = ctk.CTkFrame(parent, fg_color=WC_COLORS["header"], height=36, corner_radius=8)
        bottom_bar.pack(fill="x", padx=5, pady=(0, 5))
        bottom_bar.pack_propagate(False)

        self.msg_stats_label = ctk.CTkLabel(
            bottom_bar, text="今日: 0 条消息 | 0 条重要",
            text_color=WC_COLORS["text_muted"],
            font=ctk.CTkFont(size=12),
        )
        self.msg_stats_label.pack(pady=6)

    def _on_new_message(self, msg_data):
        """新消息回调（线程安全）"""
        try:
            contact = msg_data.get("contact", "?")
            content = str(msg_data.get("content", ""))[:30]
            self._on_log("info", f"[UI] 收到新消息回调: {contact}: {content}")
            self.after(0, lambda: self._add_message_card(msg_data))
        except Exception as e:
            print(f"[实时消息] 回调调度失败: {e}")

    def _add_message_card(self, msg_data):
        """添加消息卡片到面板（异常输出到UI日志，不再静默）"""
        try:
            self._add_message_card_impl(msg_data)
        except Exception as e:
            import traceback
            try:
                self._on_log("error", f"[实时消息] 卡片创建失败: {e}")
                self._on_log("error", traceback.format_exc()[:200])
            except Exception:
                print(f"[实时消息] 卡片创建失败: {e}")
                traceback.print_exc()

    def _add_message_card_impl(self, msg_data):
        """微信风格消息气泡"""
        mel = getattr(self, 'msg_empty_label', None)
        if mel is not None:
            try:
                if mel.winfo_exists():
                    mel.destroy()
            except Exception:
                pass
            self.msg_empty_label = None

        contact = msg_data.get("contact", "未知")
        sender = msg_data.get("sender", "other")
        content = msg_data.get("content", "")
        timestamp = msg_data.get("timestamp", "")
        is_important = msg_data.get("is_important", False)

        is_self = (sender == "me")

        # 行容器 — 左对齐(对方)或右对齐(自己)
        row = ctk.CTkFrame(self.msg_list_frame, fg_color="transparent")
        row.pack(side="top", fill="x", padx=5, pady=4)

        if is_self:
            row.grid_columnconfigure(0, weight=1)
            # 自己的消息：右对齐，绿色气泡
            spacer = ctk.CTkFrame(row, fg_color="transparent", width=40)
            spacer.grid(row=0, column=0, sticky="w")

            bubble_frame = ctk.CTkFrame(row, fg_color=WC_COLORS["bubble_self"],
                                        corner_radius=12, border_width=0)
            bubble_frame.grid(row=0, column=1, sticky="e", padx=(5, 0))

            meta_frame = ctk.CTkFrame(bubble_frame, fg_color="transparent")
            meta_frame.pack(fill="x", padx=12, pady=(6, 2))

            ctk.CTkLabel(meta_frame, text=f"{contact} {timestamp}",
                         font=ctk.CTkFont(size=10),
                         text_color="#555555").pack(side="right")

            content_label = ctk.CTkLabel(
                bubble_frame, text=content[:200],
                font=ctk.CTkFont(size=13),
                text_color="#191919",
                wraplength=300, justify="left",
            )
            content_label.pack(padx=12, pady=(2, 6), anchor="e")

            ctk.CTkLabel(row, text="🤖", font=ctk.CTkFont(size=20),
                         width=36, height=36, fg_color=WC_COLORS["accent"],
                         corner_radius=18).grid(row=0, column=2, padx=(5, 0))
        else:
            row.grid_columnconfigure(1, weight=1)
            # 对方消息：左对齐，白色气泡
            avatar = ctk.CTkLabel(row, text="👤", font=ctk.CTkFont(size=18),
                                   width=36, height=36, fg_color="#E0E0E0",
                                   corner_radius=18)
            avatar.grid(row=0, column=0, padx=(0, 5))

            bubble_frame = ctk.CTkFrame(row, fg_color=WC_COLORS["bubble_other"],
                                        corner_radius=12, border_width=1,
                                        border_color=WC_COLORS["border"])
            bubble_frame.grid(row=0, column=1, sticky="w", padx=(5, 0))

            meta_frame = ctk.CTkFrame(bubble_frame, fg_color="transparent")
            meta_frame.pack(fill="x", padx=12, pady=(6, 2))

            prefix = "🔴 " if is_important else ""
            ctk.CTkLabel(meta_frame, text=f"{prefix}{contact} {timestamp}",
                         font=ctk.CTkFont(size=10),
                         text_color=WC_COLORS["text_muted"]).pack(side="left")

            content_label = ctk.CTkLabel(
                bubble_frame, text=content[:200],
                font=ctk.CTkFont(size=13),
                text_color=WC_COLORS["text"],
                wraplength=300, justify="left",
            )
            content_label.pack(padx=12, pady=(2, 6), anchor="w")

            if is_important and msg_data.get("importance_reason"):
                reason = ctk.CTkLabel(
                    bubble_frame, text=f"📌 {msg_data['importance_reason']}",
                    font=ctk.CTkFont(size=10), text_color=WC_COLORS["danger"],
                )
                reason.pack(padx=12, pady=(0, 6), anchor="w")

        # 限制最多显示100条
        children = self.msg_list_frame.winfo_children()
        if len(children) > 100:
            children[0].destroy()

        # 更新统计和标题
        self._msg_count = getattr(self, '_msg_count', 0) + 1
        self._important_count = getattr(self, '_important_count', 0) + (1 if is_important else 0)
        if hasattr(self, 'msg_stats_label'):
            self.msg_stats_label.configure(
                text=f"今日: {self._msg_count} 条消息 | {self._important_count} 条重要"
            )
        if hasattr(self, 'chat_subtitle'):
            self.chat_subtitle.configure(text=f"最新: {contact} · {timestamp}")

    def _create_search_panel(self, parent):
        """创建搜索面板"""
        search_frame = ctk.CTkFrame(parent)
        search_frame.pack(fill="x", padx=10, pady=5)

        ctk.CTkLabel(search_frame, text="🔍 搜索:").pack(side="left", padx=5)
        self.search_entry = ctk.CTkEntry(search_frame, width=200, placeholder_text="搜索联系人或消息内容...")
        self.search_entry.pack(side="left", padx=5)

        search_btn = ctk.CTkButton(search_frame, text="搜索", width=60, command=self._do_search)
        search_btn.pack(side="left", padx=5)

        self.search_results_frame = ctk.CTkScrollableFrame(parent, height=200)
        self.search_results_frame.pack(fill="both", expand=True, padx=10, pady=5)

    def _do_search(self):
        keyword = self.search_entry.get().strip()
        if not keyword:
            return
        # 清空结果
        for w in self.search_results_frame.winfo_children():
            w.destroy()
        # 搜索数据库
        if hasattr(self, 'engine') and self.engine and self.engine.get_storage():
            results = self.engine.get_storage().search(keyword)
            for msg in results[:50]:
                card = ctk.CTkFrame(self.search_results_frame, corner_radius=6)
                card.pack(fill="x", padx=5, pady=2)
                contact = msg.get("contact", "")
                content = msg.get("raw_text", "")
                is_important = "🔴" if msg.get("is_important") else "💬"
                ctk.CTkLabel(card, text=f"{is_important} {contact}: {content[:80]}",
                            font=ctk.CTkFont(size=12)).pack(anchor="w", padx=8, pady=3)
            if not results:
                ctk.CTkLabel(self.search_results_frame, text="未找到匹配消息").pack(pady=10)

    def _create_stats_panel(self, parent):
        """创建统计面板"""
        stats_frame = ctk.CTkFrame(parent)
        stats_frame.pack(fill="x", padx=10, pady=5)

        ctk.CTkLabel(stats_frame, text="📊 统计", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=5)

        self.stats_labels = {}
        for label in ["总消息", "重要消息", "联系人数", "提取信息", "今日消息"]:
            row = ctk.CTkFrame(stats_frame, fg_color="transparent")
            row.pack(fill="x", padx=10, pady=2)
            ctk.CTkLabel(row, text=label+":", width=80, anchor="w").pack(side="left")
            val_label = ctk.CTkLabel(row, text="0", font=ctk.CTkFont(size=14, weight="bold"), text_color="#2196F3")
            val_label.pack(side="right")
            self.stats_labels[label] = val_label

        refresh_btn = ctk.CTkButton(stats_frame, text="刷新统计", width=80, command=self._refresh_stats)
        refresh_btn.pack(pady=5)

    def _refresh_stats(self):
        if hasattr(self, 'engine') and self.engine and self.engine.get_storage():
            stats = self.engine.get_storage().get_stats()
            self.stats_labels["总消息"].configure(text=str(stats.get("total", 0)))
            self.stats_labels["重要消息"].configure(text=str(stats.get("important", 0)))
            self.stats_labels["联系人数"].configure(text=str(stats.get("contacts", 0)))

    def _toggle_advanced_settings(self):
        if self.advanced_settings_var.get():
            self.advanced_frame.pack(fill="x", padx=10, pady=5)
        else:
            self.advanced_frame.pack_forget()

    # ========== 事件处理 ==========

    def start_monitoring(self):
        from main import WeChatEngine

        self.engine = WeChatEngine(self.config_path, callbacks={
            "on_log": self._on_log,
            "on_extract": self._on_extract,
            "on_reply": self._on_reply,
            "on_stats": self._on_stats,
            "on_status": self._on_status,
            "on_new_message": self._on_new_message,
            "on_capture": self._on_capture,
        })

        auto_reply = self.config_data.get("auto_reply", {}).get("enabled", False)
        self.engine.auto_reply_enabled = auto_reply

        # 后台线程执行启动流程，分步加载避免UI卡顿
        self.btn_start.configure(state="disabled")
        self._on_log("info", "━━━ 正在启动监控，请稍候 ━━━")
        threading.Thread(target=self._start_engine_thread, daemon=True).start()

    def _start_engine_thread(self):
        """后台执行engine.start()，分步加载避免UI卡顿"""
        try:
            self.engine.start()
        except Exception as e:
            self._on_log("error", f"启动失败: {e}")
            self.after(0, lambda: self.btn_start.configure(state="normal"))
            return
        # start()内部失败会设置status=error但不抛异常，用is_running判断
        if self.engine.is_running():
            self.after(0, lambda: self.btn_stop.configure(state="normal"))
        else:
            self._on_log("error", "启动未成功，请检查日志中的错误信息")
            self.after(0, lambda: self.btn_start.configure(state="normal"))

    def stop_monitoring(self):
        if self.engine:
            self.engine.stop()
            self.engine = None
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")

    def _show_wechat_window(self):
        """把屏幕外的微信恢复到桌面（窗口被隐藏后的一键找回）"""
        try:
            from window_manager import find_wechat_window, is_window_offscreen, bring_window_back
            win = find_wechat_window()
            if not win:
                self._on_log("warning", "未找到微信窗口")
                return
            if is_window_offscreen(win):
                bring_window_back(win)
                self._on_log("info", "👁 微信已恢复到桌面")
            else:
                self._on_log("info", "微信窗口本来就在桌面，无需恢复")
        except Exception as e:
            self._on_log("error", f"恢复微信失败: {e}")

    def test_ocr(self):
        self._on_log("info", "正在测试OCR识别...")
        threading.Thread(target=self._test_ocr_thread, daemon=True).start()

    def _test_ocr_thread(self):
        try:
            from window_manager import find_wechat_window, get_contact_name
            from screenshot import capture_chat_area, capture_full_window, is_image_blank, get_monitor_info
            from ocr_engine import recognize, identify_senders

            window = find_wechat_window()
            if not window:
                self._on_log("error", "未找到微信窗口，请确保微信已打开且未最小化")
                return

            self._on_log("info", f"找到窗口: {window.title}")
            self._on_log("info", f"窗口位置: left={window.left}, top={window.top}, {window.width}x{window.height}")

            # 显示显示器信息（调试用）
            monitors = get_monitor_info()
            for m in monitors:
                self._on_log("info", f"  显示器{m['index']}: {m['left']},{m['top']} {m['width']}x{m['height']}")

            # 截取完整聊天区域（不是只截底部）
            image = capture_chat_area(window)
            if image is None:
                self._on_log("error", "截图失败，返回None")
                return

            if is_image_blank(image):
                self._on_log("warning", "截图为空或全黑，可能窗口被遮挡或坐标不对")
                self._on_log("info", "尝试截取整个窗口...")
                image = capture_full_window(window)
                if is_image_blank(image):
                    self._on_log("error", "整个窗口截图也为空，请检查微信窗口是否可见")
                    return

            self._on_log("info", f"截图尺寸: {image.shape[1]}x{image.shape[0]}")
            # 保存调试截图，方便定位坐标问题
            try:
                import os
                import numpy as np
                from PIL import Image
                debug_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "debug")
                os.makedirs(debug_dir, exist_ok=True)
                debug_path = os.path.join(debug_dir, "screenshot_debug.png")

                # BGR -> RGB（OpenCV和PIL颜色通道顺序相反）
                img_rgb = image[:, :, ::-1].copy()
                pil_img = Image.fromarray(img_rgb)
                pil_img.save(debug_path)

                # 同时输出图像统计，就算保存失败也能判断是否是全黑/全白
                mean_val = float(np.mean(image))
                std_val = float(np.std(image))
                self._on_log("info", f"调试截图已保存: {debug_path}")
                self._on_log("info", f"图像统计: 均值={mean_val:.1f}, 标准差={std_val:.1f}")
                if mean_val < 5:
                    self._on_log("warning", "图像均值极低(<5)，基本全黑 = 坐标错误截到屏幕外了！")
                elif mean_val > 250:
                    self._on_log("warning", "图像均值极高(>250)，基本全白 = 可能截到微信的空白聊天区")
                elif std_val < 10:
                    self._on_log("warning", "图像标准差极低(<10)，大面积纯色 = 截到了空白区域或图片消息而非文字")

                # 保存一份整窗口截图作为对比
                try:
                    from screenshot import capture_full_window
                    full_img = capture_full_window(window)
                    if full_img is not None:
                        full_path = os.path.join(debug_dir, "screenshot_full_window.png")
                        full_rgb = full_img[:, :, ::-1].copy()
                        Image.fromarray(full_rgb).save(full_path)
                        self._on_log("info", f"整窗口对比截图: {full_path}")
                except Exception as e3:
                    self._on_log("warning", f"整窗口截图保存失败: {e3}")

            except Exception as e2:
                self._on_log("error", f"调试截图保存失败: {type(e2).__name__}: {e2}")
                import traceback
                self._on_log("error", traceback.format_exc()[-300:])

            results = recognize(image, scale=0.85, min_confidence=0.50,
                               merge_bubble=True, denoise=True)
            self._on_log("info", f"OCR识别到 {len(results)} 条文本")

            if len(results) == 0:
                self._on_log("warning", "未识别到文字，可能原因: 1)聊天区域无文字 2)窗口被遮挡 3)DPI缩放导致坐标偏移")
                return

            results = identify_senders(results, image)

            me_count = 0
            other_count = 0
            for r in results:
                sender = "我" if r.get("sender") == "me" else "对方"
                conf = r.get("confidence", 0)
                lines = r["text"].split("\n")
                first_line = lines[0][:50] if lines else r["text"][:50]
                self._on_log("info", f"  [{sender}] (置信度:{conf:.0%}) {first_line}")
                if len(lines) > 1:
                    self._on_log("info", f"    (共{len(lines)}行)")
                if r.get("sender") == "me":
                    me_count += 1
                else:
                    other_count += 1

            self._on_log("info", f"统计: 我={me_count} 对方={other_count} 总计={len(results)}")

            # 同时在提取结果区域显示
            self.after(0, lambda: self._show_test_results(results))

        except Exception as e:
            import traceback
            self._on_log("error", f"识别失败: {e}")
            self._on_log("error", traceback.format_exc()[-200:])

    def _show_test_results(self, results):
        """在提取结果区域显示测试识别结果（卡片式UI）"""
        self.result_text.configure(state="normal")
        self.result_text.delete("1.0", "end")

        # 配置文本标签样式
        self.result_text.tag_config("header", foreground="#3b82f6")
        self.result_text.tag_config("me_label", foreground="#22c55e")
        self.result_text.tag_config("other_label", foreground="#e6e8eb")
        self.result_text.tag_config("me_text", foreground="#86efac")
        self.result_text.tag_config("other_text", foreground="#e6e8eb")
        self.result_text.tag_config("conf_high", foreground="#22c55e")
        self.result_text.tag_config("conf_mid", foreground="#f59e0b")
        self.result_text.tag_config("conf_low", foreground="#ef4444")
        self.result_text.tag_config("separator", foreground="#2c3038")
        self.result_text.tag_config("line_count", foreground="#8b9099")

        me_count = sum(1 for r in results if r.get("sender") == "me")
        other_count = len(results) - me_count

        self.result_text.insert("end", f"  识别结果\n", "header")
        self.result_text.insert("end", f"  共 {len(results)} 条  |  我 {me_count}  对方 {other_count}\n\n", "line_count")

        for i, r in enumerate(results):
            sender = r.get("sender", "other")
            conf = r.get("confidence", 0)
            text = r["text"]
            lines = text.split("\n")
            line_count = len(lines)

            if sender == "me":
                label_tag = "me_label"
                text_tag = "me_text"
                icon = ">>"
                label_text = " 我"
            else:
                label_tag = "other_label"
                text_tag = "other_text"
                icon = "<<"
                label_text = "对方"

            if conf >= 0.90:
                conf_tag = "conf_high"
                conf_icon = "●"
            elif conf >= 0.70:
                conf_tag = "conf_mid"
                conf_icon = "●"
            else:
                conf_tag = "conf_low"
                conf_icon = "○"

            self.result_text.insert("end", f" {icon} ", label_tag)
            self.result_text.insert("end", label_text, label_tag)
            self.result_text.insert("end", f"   {conf_icon} {conf:.0%}", conf_tag)
            if line_count > 1:
                self.result_text.insert("end", f"  ({line_count}行)", "line_count")
            self.result_text.insert("end", "\n")

            display_text = lines[0][:80] if lines else text[:80]
            self.result_text.insert("end", f"  {display_text}\n", text_tag)
            if line_count > 1:
                for extra_line in lines[1:3]:
                    self.result_text.insert("end", f"  {extra_line[:80]}\n", text_tag)
                if line_count > 3:
                    self.result_text.insert("end", f"  ... ({line_count - 3}行更多)\n", "line_count")

            self.result_text.insert("end", "  ──────────────────────────────────\n", "separator")

        self.result_text.configure(state="disabled")

    def on_reply_switch(self):
        enabled = self.reply_switch.get() == 1
        self.config_data.setdefault("auto_reply", {})["enabled"] = enabled
        if self.engine:
            self.engine.set_auto_reply(enabled)
        self._on_log("info", f"自动回复已{'开启' if enabled else '关闭'}")

    def refresh_data(self):
        try:
            from storage import MessageStorage
            storage_cfg = self.config_data.get("storage", {})
            storage = MessageStorage(storage_cfg)

            stats = storage.get_stats()
            self.data_stats_label.configure(
                text=f"共 {stats.get('total_messages', 0)} 条 | "
                     f"重要 {stats.get('important_messages', 0)} 条 | "
                     f"联系人 {stats.get('total_contacts', 0)} 个"
            )

            data = storage.query(limit=200)
            self.data_text.configure(state="normal")
            self.data_text.delete("1.0", "end")

            if not data:
                self.data_text.insert("end", "暂无数据。启动监控后，提取的消息会显示在这里。\n")
            else:
                for row in data:
                    important = "★" if row["is_important"] else " "
                    contact = row["contact"]
                    ts = row["timestamp"]
                    text = row["raw_text"][:80]
                    cats = ", ".join(row.get("keyword_categories", []))
                    llm = row.get("llm_analysis", {})
                    summary = llm.get("summary", "") if llm else ""

                    line = f"{important} [{ts}] {contact}: {text}"
                    if cats:
                        line += f"  | 标签: {cats}"
                    if summary:
                        line += f"  | 摘要: {summary}"
                    self.data_text.insert("end", line + "\n")

            self.data_text.configure(state="disabled")

        except Exception as e:
            self._on_log("error", f"刷新数据失败: {e}")

    def export_csv(self):
        try:
            from storage import MessageStorage
            storage = MessageStorage(self.config_data.get("storage", {}))
            filepath = filedialog.asksaveasfilename(
                defaultextension=".csv",
                filetypes=[("CSV文件", "*.csv")],
                initialfile=f"messages_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            )
            if filepath:
                storage.export_csv(filepath)
                messagebox.showinfo("成功", f"已导出CSV到:\n{filepath}")
        except Exception as e:
            messagebox.showerror("错误", f"导出失败: {e}")

    def export_json(self):
        try:
            from storage import MessageStorage
            storage = MessageStorage(self.config_data.get("storage", {}))
            filepath = filedialog.asksaveasfilename(
                defaultextension=".json",
                filetypes=[("JSON文件", "*.json")],
                initialfile=f"messages_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            )
            if filepath:
                storage.export_json(filepath)
                messagebox.showinfo("成功", f"已导出JSON到:\n{filepath}")
        except Exception as e:
            messagebox.showerror("错误", f"导出失败: {e}")

    def _open_rules_file(self):
        """打开规则库文件进行编辑"""
        import subprocess, os
        rules_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "learned_rules.json")
        if os.path.exists(rules_path):
            subprocess.Popen(["notepad", rules_path])
        else:
            self._on_log("warning", "规则库文件不存在")

    def _reset_ai_training(self):
        """重置AI训练"""
        try:
            import json, os
            rules_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "learned_rules.json")
            if os.path.exists(rules_path):
                with open(rules_path, "r", encoding="utf-8") as f:
                    rules = json.load(f)
                rules["_meta"]["training_count"] = 0
                with open(rules_path, "w", encoding="utf-8") as f:
                    json.dump(rules, f, ensure_ascii=False, indent=2)
                self._on_log("info", "AI训练已重置，重新进入学习期")
            else:
                self._on_log("warning", "规则库文件不存在")
        except Exception as e:
            self._on_log("error", f"重置失败: {e}")

    def _test_obsidian_api(self):
        """测试Obsidian REST API连接"""
        from obsidian_sync import ObsidianSync
        cfg = {
            "mode": "api",
            "api_url": self.obsidian_api_url_entry.get().strip(),
            "api_key": self.obsidian_api_key_entry.get().strip(),
        }
        sync = ObsidianSync(cfg)
        ok, msg = sync.test_api_connection()
        if ok:
            self._on_log("info", f"[Obsidian] {msg}")
        else:
            self._on_log("warning", f"[Obsidian] {msg}")

    def _rebuild_obsidian_vault(self):
        """重建Obsidian vault笔记"""
        if not self.engine or not self.engine.storage:
            self._on_log("warning", "[Obsidian] 请先启动监控再重建")
            return
        vault_path = self.obsidian_vault_entry.get().strip()
        if not vault_path:
            self._on_log("warning", "[Obsidian] 请先填写Vault路径")
            return
        self._on_log("info", "[Obsidian] 正在重建Vault笔记...")
        try:
            from obsidian_sync import ObsidianSync
            cfg = {"vault_path": vault_path, "mode": "file", "folder": "微信消息"}
            sync = ObsidianSync(cfg)
            all_data = self.engine.storage.query(limit=999999)
            messages = []
            for row in all_data:
                llm_data = row.get("llm_analysis")
                summary = ""
                if isinstance(llm_data, str):
                    try:
                        import json
                        llm_data = json.loads(llm_data)
                    except Exception:
                        llm_data = {}
                if isinstance(llm_data, dict):
                    summary = llm_data.get("summary", "") or llm_data.get("摘要", "")
                messages.append({
                    "contact": row.get("contact", ""),
                    "sender": row.get("sender", "other"),
                    "content": row.get("raw_text", ""),
                    "timestamp": row.get("timestamp", ""),
                    "is_important": row.get("is_important", False),
                    "importance_reason": row.get("importance_reason", ""),
                    "keywords": row.get("matched_keywords", []),
                    "summary": summary,
                })
            if sync.rebuild_vault(messages):
                self._on_log("info", f"[Obsidian] Vault重建完成: {len(messages)}条消息")
            else:
                self._on_log("warning", "[Obsidian] Vault重建失败")
        except Exception as e:
            self._on_log("error", f"[Obsidian] 重建失败: {e}")

    def save_settings(self):
        try:
            import yaml
            # 保存监控模式
            monitor_mode = self.monitor_var.get()
            self.config_data.setdefault("red_dot_monitor", {})["enabled"] = (monitor_mode == "red_dot")
            self.config_data.setdefault("contact_scanner", {})["enabled"] = (monitor_mode == "scan")
            self.config_data.setdefault("llm", {})["base_url"] = self.entry_url.get()
            self.config_data["llm"]["api_key"] = self.entry_key.get()
            self.config_data["llm"]["model"] = self.entry_model.get()
            self.config_data.setdefault("wechat", {})["ocr_scale"] = round(self.slider_scale.get(), 2)
            self.config_data["wechat"]["ocr_min_confidence"] = round(self.slider_conf.get(), 2)
            self.config_data["wechat"]["ocr_merge_bubble"] = self.switch_merge.get() == 1
            self.config_data["wechat"]["ocr_denoise"] = self.switch_denoise.get() == 1

            # 联系人过滤黑白名单
            _filter_cfg = self.config_data.setdefault("contacts_filter", {})
            _wl_text = self.whitelist_entry.get().strip()
            _bl_text = self.blacklist_entry.get().strip()
            _filter_cfg["whitelist"] = [s.strip() for s in _wl_text.split(",") if s.strip()] if _wl_text else []
            _filter_cfg["blacklist"] = [s.strip() for s in _bl_text.split(",") if s.strip()] if _bl_text else []
            self.config_data["contacts_filter"] = _filter_cfg

            # Obsidian设置
            obsidian_cfg = self.config_data.get("obsidian", {})
            obsidian_cfg["vault_path"] = self.obsidian_vault_entry.get().strip()
            obsidian_cfg["mode"] = self.obsidian_mode_var.get()
            obsidian_cfg["auto_sync"] = self.obsidian_enabled_var.get()
            obsidian_cfg["api_url"] = self.obsidian_api_url_entry.get().strip()
            obsidian_cfg["api_key"] = self.obsidian_api_key_entry.get().strip()
            self.config_data["obsidian"] = obsidian_cfg

            config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), self.config_path)
            with open(config_path, "w", encoding="utf-8") as f:
                yaml.dump(self.config_data, f, allow_unicode=True, default_flow_style=False)

            messagebox.showinfo("成功", "设置已保存！")
            self._on_log("info", "设置已保存到配置文件")
        except Exception as e:
            messagebox.showerror("错误", f"保存失败: {e}")

    # ========== 回调（从引擎线程调用，需要线程安全） ==========

    def _on_capture(self, image):
        """截图预览回调（监控线程跨线程调用）：
        放入队列，由主线程 _poll_capture 轮询更新。
        ★ 不能用 after_idle 直接更新：Tkinter 的 after 系列非线程安全，
          从监控线程调用会导致回调不执行（预览一直"等待截图"）。
        """
        try:
            if self._capture_queue.full():
                try:
                    self._capture_queue.get_nowait()  # 丢弃旧帧，只留最新
                except Exception:
                    pass
            self._capture_queue.put_nowait(image)
        except Exception:
            pass

    def _poll_capture(self):
        """主线程轮询预览队列（跨线程安全，~12fps）"""
        try:
            while True:
                try:
                    image = self._capture_queue.get_nowait()
                except Exception:
                    break
                self._update_preview(image)
        except Exception:
            pass
        try:
            self.after(80, self._poll_capture)
        except Exception:
            pass

    def _update_preview(self, image):
        """更新预览窗口图像 + 同步联系人列表"""
        import numpy as np
        from PIL import Image, ImageTk
        try:
            if image is None:
                return

            # numpy BGR -> PIL RGB（避免.copy()开销，用swap直接转换）
            if isinstance(image, np.ndarray) and len(image.shape) == 3:
                # 直接在numpy层完成颜色转换（比PIL.fromarray+copy快）
                pil_img = Image.fromarray(image[:, :, ::-1])
            else:
                pil_img = image

            # 先缩放到预览尺寸（减少后续计算量）
            max_w, max_h = 720, 480
            # Image.BILINEAR 在 Pillow 10 已移除，用 Resampling 枚举
            pil_img.thumbnail((max_w, max_h), Image.Resampling.BILINEAR)

            # 计算均值（在缩小后的图上计算，快10倍）
            mean_val = float(np.array(pil_img).mean())

            photo = ImageTk.PhotoImage(pil_img)

            # 更新主界面预览（用after_idle调度，比after(0)更快）
            if hasattr(self, 'main_preview_label'):
                self.main_preview_label.configure(image=photo)
                self.main_preview_label.image = photo
                if hasattr(self, 'main_preview_status'):
                    self.main_preview_status.configure(
                        text=f"{pil_img.size[0]}x{pil_img.size[1]} | {mean_val:.0f}")
            else:
                print(f"[预览] main_preview_label不存在, type={type(self).__name__}")

            # 更新独立预览窗口（如果打开了）
            if hasattr(self, "preview_label"):
                self.preview_label.configure(image=photo)
                self.preview_label.image = photo
            if hasattr(self, 'preview_status'):
                self.preview_status.configure(
                    text=f"● {pil_img.size[0]}x{pil_img.size[1]} | {mean_val:.0f}")
            if hasattr(self, 'preview_info'):
                self.preview_info.configure(
                    text=f"{pil_img.size[0]}x{pil_img.size[1]} | {mean_val:.1f} | 正常")
        except Exception as e:
            import traceback
            now_ts = time.time()
            if now_ts - getattr(self, "_last_preview_err", 0) > 10:
                self._last_preview_err = now_ts
                try:
                    self._on_log("warning", f"[预览] 更新失败: {e}")
                    print(f"[预览] 更新失败: {e}")
                    traceback.print_exc()
                except Exception:
                    print(f"[预览] 更新失败(无法记录): {e}")
                    traceback.print_exc()

    def _sync_preview_contacts(self):
        """同步监控中的联系人到预览窗口联系人列表"""
        try:
            if not hasattr(self, '_preview_contacts'):
                self._preview_contacts = {}
            if not hasattr(self, '_msg_count'):
                return

            # 从实时消息列表中提取最近联系人
            if hasattr(self, 'msg_list_frame'):
                children = self.msg_list_frame.winfo_children()
                seen = set()
                for card in children[:20]:
                    try:
                        # 从已渲染的气泡中提取联系人信息
                        for widget in card.winfo_children():
                            if hasattr(widget, 'cget') and widget.winfo_exists():
                                text = widget.cget('text')
                                if text and isinstance(text, str) and len(text) > 2:
                                    # 尝试提取联系人名
                                    for prefix in ['🔴 ', '']:
                                        cleaned = text.replace(prefix, '').strip()
                                        if cleaned and len(cleaned) < 20 and cleaned not in seen:
                                            seen.add(cleaned)
                                            self._preview_contacts[cleaned] = {
                                                "time": datetime.now().strftime("%H:%M"),
                                                "preview": text[:40],
                                                "unread": 0,
                                            }
                                            break
                    except Exception:
                        pass

                # 限制数量
                if len(self._preview_contacts) > 30:
                    keys = list(self._preview_contacts.keys())
                    for k in keys[:-30]:
                        del self._preview_contacts[k]

                self._update_preview_contacts()
        except Exception:
            pass

    def _open_preview_window(self):
        """打开微信风格预览窗口"""
        import tkinter as tk
        from PIL import Image, ImageTk, ImageDraw

        if hasattr(self, "preview_win") and self.preview_win.winfo_exists():
            self.preview_win.focus_force()
            return

        self.preview_win = tk.Toplevel(self)
        self.preview_win.title("微信 AI 助手 - 监控预览")
        self.preview_win.geometry("960x680")
        self.preview_win.configure(bg="#EDEDED")
        self.preview_win.minsize(860, 580)

        # ===== 顶部标题栏（微信风格）=====
        title_bar = tk.Frame(self.preview_win, bg="#2E2E2E", height=36)
        title_bar.pack(fill="x", side="top")
        title_bar.pack_propagate(False)

        tk.Label(title_bar, text="🤖 微信 AI 助手",
                 bg="#2E2E2E", fg="#07C160",
                 font=("Microsoft YaHei", 12, "bold")).pack(side="left", padx=15, pady=6)

        self.preview_status = tk.Label(title_bar, text="● 等待截图...",
                                        bg="#2E2E2E", fg="#888888",
                                        font=("Consolas", 10))
        self.preview_status.pack(side="right", padx=15, pady=6)

        # ===== 主体：左侧联系人 + 右侧预览 =====
        body = tk.Frame(self.preview_win, bg="#EDEDED")
        body.pack(fill="both", expand=True)

        # ===== 左侧联系人面板 =====
        left_panel = tk.Frame(body, bg="#2E2E2E", width=200)
        left_panel.pack(side="left", fill="y")
        left_panel.pack_propagate(False)

        # 搜索框
        search_frame = tk.Frame(left_panel, bg="#2E2E2E")
        search_frame.pack(fill="x", padx=10, pady=10)
        tk.Entry(search_frame, font=("Microsoft YaHei", 10),
                 bg="#3A3A3A", fg="#FFFFFF", insertbackground="#FFFFFF",
                 relief="flat", highlightthickness=0).pack(fill="x", ipady=4)

        # 标签
        tk.Label(left_panel, text="最近监控",
                 bg="#2E2E2E", fg="#888888",
                 font=("Microsoft YaHei", 9)).pack(anchor="w", padx=15, pady=(5, 8))

        # 联系人列表（滚动）
        list_container = tk.Frame(left_panel, bg="#2E2E2E")
        list_container.pack(fill="both", expand=True, padx=5)

        canvas = tk.Canvas(list_container, bg="#2E2E2E", highlightthickness=0)
        scrollbar = tk.Scrollbar(list_container, orient="vertical", command=canvas.yview)
        self.contact_list_frame = tk.Frame(canvas, bg="#2E2E2E")

        self.contact_list_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=self.contact_list_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # 默认联系人占位
        self._preview_contacts = {}
        self._update_preview_contacts()

        # ===== 右侧预览区 =====
        right_panel = tk.Frame(body, bg="#EDEDED")
        right_panel.pack(side="left", fill="both", expand=True)

        # 聊天头部
        chat_header = tk.Frame(right_panel, bg="#F7F7F7", height=48)
        chat_header.pack(fill="x", side="top")
        chat_header.pack_propagate(False)

        self.chat_preview_title = tk.Label(chat_header, text="📡 实时监控画面",
                                            bg="#F7F7F7", fg="#191919",
                                            font=("Microsoft YaHei", 13, "bold"))
        self.chat_preview_title.pack(side="left", padx=15, pady=12)

        self.chat_preview_sub = tk.Label(chat_header, text="屏幕外监控中",
                                          bg="#F7F7F7", fg="#07C160",
                                          font=("Microsoft YaHei", 10))
        self.chat_preview_sub.pack(side="right", padx=15, pady=12)

        # 截图预览区
        preview_container = tk.Frame(right_panel, bg="#EDEDED")
        preview_container.pack(fill="both", expand=True, padx=10, pady=10)

        # 模拟微信窗口边框
        self.preview_label = tk.Label(
            preview_container, bg="black",
            highlightbackground="#D0D0D0", highlightthickness=1,
        )
        self.preview_label.pack(fill="both", expand=True)

        # 底部信息栏
        bottom_bar = tk.Frame(right_panel, bg="#F7F7F7", height=32)
        bottom_bar.pack(fill="x", side="bottom")
        bottom_bar.pack_propagate(False)

        self.preview_info = tk.Label(bottom_bar,
                                      text="分辨率: -- | 状态: 等待截图",
                                      bg="#F7F7F7", fg="#888888",
                                      font=("Microsoft YaHei", 9))
        self.preview_info.pack(pady=6)

        # 默认占位图
        placeholder = Image.new("RGB", (720, 480), (240, 240, 240))
        draw = ImageDraw.Draw(placeholder)
        draw.rectangle([0, 0, 719, 479], outline="#E0E0E0", width=1)
        draw.text((260, 200), "等待监控截图...", fill="#999999")
        draw.text((230, 240), "启动监控后将实时显示微信画面", fill="#BBBBBB")
        self._placeholder_photo = ImageTk.PhotoImage(placeholder)
        self.preview_label.configure(image=self._placeholder_photo)

        self.preview_win.protocol("WM_DELETE_WINDOW", self._close_preview)
        self._on_log("info", "[预览窗口] 微信风格预览已打开")

    def _update_preview_contacts(self):
        """更新预览窗口联系人列表"""
        if not hasattr(self, '_preview_contacts'):
            return
        if not hasattr(self, 'contact_list_frame'):
            return
        try:
            if not self.contact_list_frame.winfo_exists():
                return
        except Exception:
            return

        for widget in self.contact_list_frame.winfo_children():
            widget.destroy()

        items = list(self._preview_contacts.items())
        if not items:
            items = [
                ("等待中...", {"time": "--:--", "unread": 0}),
            ]

        for name, info in items:
            contact_row = tk.Frame(self.contact_list_frame, bg="#2E2E2E", cursor="hand2")
            contact_row.pack(fill="x", padx=5, pady=2)

            avatar_frame = tk.Frame(contact_row, bg="#07C160", width=36, height=36)
            avatar_frame.pack(side="left", padx=(10, 8), pady=6)
            avatar_frame.pack_propagate(False)
            tk.Label(avatar_frame, text=name[0] if name else "?",
                     bg="#07C160", fg="white",
                     font=("Microsoft YaHei", 11, "bold")).pack(expand=True)

            info_frame = tk.Frame(contact_row, bg="#2E2E2E")
            info_frame.pack(side="left", fill="x", expand=True, pady=6)

            name_row = tk.Frame(info_frame, bg="#2E2E2E")
            name_row.pack(fill="x")
            tk.Label(name_row, text=name, bg="#2E2E2E", fg="#FFFFFF",
                     font=("Microsoft YaHei", 10, "bold")).pack(side="left")
            tk.Label(name_row, text=info.get("time", ""), bg="#2E2E2E", fg="#888888",
                     font=("Consolas", 8)).pack(side="right")

            msg_row = tk.Frame(info_frame, bg="#2E2E2E")
            msg_row.pack(fill="x")
            preview = info.get("preview", "")[:20]
            tk.Label(msg_row, text=preview or "暂无新消息", bg="#2E2E2E", fg="#888888",
                     font=("Microsoft YaHei", 9)).pack(anchor="w")

            unread = info.get("unread", 0)
            if unread > 0:
                badge = tk.Label(contact_row, text=str(unread),
                                 bg="#FA5151", fg="white", width=2,
                                 font=("Microsoft YaHei", 8, "bold"))
                badge.place(relx=1.0, y=8, x=-12, anchor="ne")

    def _close_preview(self):
        """关闭预览窗口"""
        try:
            self.preview_win.destroy()
        except Exception:
            pass
        self._on_log("info", "[预览窗口] 已关闭")

    def _on_log(self, level, message=None):
        if message is None:
            message = level
            level = "info"
        self.after(0, lambda: self._append_log(level, message))

    def _append_log(self, level, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        prefix = {"info": "", "warning": "⚠ ", "error": "✖ "}.get(level, "")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{timestamp}] {prefix}{message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _on_extract(self, result):
        self.after(0, lambda: self._append_result(result))

    def _append_result(self, result):
        contact = result.get("contact", "")
        text = result.get("raw_text", "")[:100]
        important = result.get("is_important", False)
        kws = result.get("matched_keywords", [])
        regex = result.get("regex_extracts", {})
        llm = result.get("llm_analysis", {})
        sender = result.get("sender", "other")

        self.result_text.tag_config("header", foreground="#3b82f6")
        self.result_text.tag_config("me_label", foreground="#22c55e")
        self.result_text.tag_config("other_label", foreground="#e6e8eb")
        self.result_text.tag_config("me_text", foreground="#86efac")
        self.result_text.tag_config("other_text", foreground="#e6e8eb")
        self.result_text.tag_config("important_mark", foreground="#ef4444")
        self.result_text.tag_config("keyword_tag", foreground="#f59e0b")
        self.result_text.tag_config("extract_tag", foreground="#3b82f6")
        self.result_text.tag_config("llm_tag", foreground="#a78bfa")
        self.result_text.tag_config("separator", foreground="#2c3038")

        self.result_text.configure(state="normal")

        if important:
            self.result_text.insert("end", " ★ 重要 ", "important_mark")

        if sender == "me":
            self.result_text.insert("end", " >>  我 ", "me_label")
            text_tag = "me_text"
        else:
            self.result_text.insert("end", " << 对方 ", "other_label")
            text_tag = "other_text"

        self.result_text.insert("end", f"  {contact}\n", "other_label")
        self.result_text.insert("end", f"  {text}\n", text_tag)

        if kws:
            self.result_text.insert("end", f"  关键词: {', '.join(kws)}\n", "keyword_tag")
        if regex:
            extracts = []
            for group, items in regex.items():
                for item in items:
                    extracts.append(f"{item['type']}={item['value']}")
            self.result_text.insert("end", f"  提取: {' | '.join(extracts)}\n", "extract_tag")
        if llm and llm.get("summary"):
            urgency = llm.get("urgency", "?")
            category = llm.get("category", "?")
            self.result_text.insert("end", f"  摘要: {llm['summary']} | 紧急度:{urgency} | 分类:{category}\n", "llm_tag")

        self.result_text.insert("end", "  ──────────────────────────────────\n", "separator")
        self.result_text.see("end")
        self.result_text.configure(state="disabled")

    def _on_reply(self, contact, reply):
        self.after(0, lambda: self._append_log("info", f"[回复] {contact}: {reply[:50]}"))

    def _on_stats(self, stats):
        self.after(0, lambda: self._update_stats(stats))

    def _update_stats(self, stats):
        self.stat_labels["frames"].configure(text=str(stats.get("frames_captured", 0)))
        self.stat_labels["ocr"].configure(text=str(stats.get("ocr_calls", 0)))
        self.stat_labels["messages"].configure(text=str(stats.get("messages_detected", 0)))
        self.stat_labels["extracted"].configure(text=str(stats.get("extracted", 0)))
        self.stat_labels["important"].configure(text=str(stats.get("important", 0)))
        self.stat_labels["replies"].configure(text=str(stats.get("replies_sent", 0)))

    def _on_status(self, status):
        self.after(0, lambda: self._update_status(status))

    def _update_status(self, status):
        status_map = {
            "running": ("● 运行中", WC_COLORS["online_dot"]),
            "stopped": ("● 已停止", WC_COLORS["text_muted"]),
            "error": ("● 错误", WC_COLORS["danger"]),
        }
        text, color = status_map.get(status, ("● 未知", WC_COLORS["text_muted"]))
        if hasattr(self, 'sidebar_status'):
            self.sidebar_status.configure(text=text, text_color=color)
        # 同步更新按钮状态
        if status == "running":
            self.btn_start.configure(state="disabled")
            self.btn_stop.configure(state="normal")
        elif status == "stopped":
            self.btn_start.configure(state="normal")
            self.btn_stop.configure(state="disabled")

    def _ensure_result_tags(self):
        """确保结果文本框的标签样式已配置"""
        self.result_text.tag_config("header", foreground=WC_COLORS["accent"])
        self.result_text.tag_config("separator", foreground=WC_COLORS["border"])
        self.result_text.tag_config("contact", foreground=WC_COLORS["online_dot"])
        self.result_text.tag_config("msg", foreground=WC_COLORS["text"])
        self.result_text.tag_config("time", foreground=WC_COLORS["text_muted"])

    def _search_messages(self):
        """搜索消息"""
        keyword = self.search_entry.get().strip()
        if not keyword:
            self._on_log("warning", "[搜索] 请输入关键词")
            return

        self._on_log("info", f"[搜索] 正在搜索: {keyword}")
        try:
            if self.engine and self.engine.storage:
                results = self.engine.storage.query(keyword=keyword, limit=200)
                self._on_log("info", f"[搜索] 找到 {len(results)} 条匹配消息")

                self._ensure_result_tags()
                self.result_text.configure(state="normal")
                self.result_text.delete("1.0", "end")

                if not results:
                    self.result_text.insert("end", "未找到匹配消息\n")
                else:
                    contacts = {}
                    for r in results:
                        name = r["contact"]
                        if name not in contacts:
                            contacts[name] = []
                        contacts[name].append(r)

                    self.result_text.insert("end", f"搜索: \"{keyword}\" — 共 {len(results)} 条\n", "header")
                    self.result_text.insert("end", "-" * 50 + "\n\n", "separator")

                    for name in sorted(contacts.keys()):
                        msgs = contacts[name]
                        self.result_text.insert("end", f"  {name} ({len(msgs)}条)\n", "contact")
                        for msg in msgs:
                            if msg["sender"] == "me":
                                sender = "我"
                            else:
                                sender = msg.get("contact", "对方")
                            important = " ★" if msg["is_important"] else ""
                            self.result_text.insert("end", f"    [{sender}]{important} {msg['raw_text'][:60]}\n", "msg")
                            self.result_text.insert("end", f"       {msg['timestamp']}\n", "time")
                        self.result_text.insert("end", "\n")

                self.result_text.configure(state="disabled")
        except Exception as e:
            self._on_log("error", f"[搜索] 搜索失败: {e}")

    def _show_important_messages(self):
        """显示所有重要消息"""
        if not self.engine or not self.engine.storage:
            self._on_log("warning", "[重要] 请先启动监控再查看重要消息")
            return
        self._on_log("info", "[搜索] 正在加载重要消息...")
        try:
            results = self.engine.storage.query(important_only=True, limit=200)
            self._on_log("info", f"[搜索] 找到 {len(results)} 条重要消息")

            self._ensure_result_tags()
            self.result_text.configure(state="normal")
            self.result_text.delete("1.0", "end")

            if not results:
                self.result_text.insert("end", "暂无重要消息\n")
            else:
                self.result_text.insert("end", f"重要消息 — 共 {len(results)} 条\n", "header")
                self.result_text.insert("end", "-" * 50 + "\n\n", "separator")

                for msg in results:
                    # 显示联系人名而非"对方"
                    if msg["sender"] == "me":
                        sender = "我"
                    else:
                        sender = msg.get("contact", "对方")
                    self.result_text.insert("end", f"  [{msg.get('contact', '?')}] ", "contact")
                    self.result_text.insert("end", f"{sender}: {msg.get('raw_text', '')[:60]}\n", "msg")
                    if msg.get("importance_reason"):
                        self.result_text.insert("end", f"    原因: {msg['importance_reason']}\n", "time")
                    self.result_text.insert("end", f"    {msg.get('timestamp', '')}\n\n", "time")

            self.result_text.configure(state="disabled")
        except Exception as e:
            self._on_log("error", f"[搜索] 加载失败: {e}")
            import traceback
            self._on_log("error", traceback.format_exc()[-200:])

    def _generate_today_report(self):
        """生成今日报告"""
        if not self.engine or not self.engine.storage:
            self._on_log("warning", "[报告] 请先启动监控再生成报告")
            return
        self._on_log("info", "[报告] 正在生成今日报告...")
        try:
            from report_generator import ReportGenerator
            report_cfg = self.config_data.get("report", {})
            gen = ReportGenerator(self.engine.storage, report_cfg)
            report = gen.generate_daily_report()

            fmt = report_cfg.get("format", "html")
            if fmt == "html":
                filepath = gen.export_html(report)
            else:
                filepath = gen.export_text(report)

            self._on_log("info", f"[报告] 报告已生成: {filepath}")
            self._on_log("info", f"[报告] 消息{report['total_messages']}条, 联系人{report['total_contacts']}个, 重要{report['total_important']}条")

            self._ensure_result_tags()
            self.result_text.configure(state="normal")
            self.result_text.delete("1.0", "end")
            self.result_text.insert("end", f"{report['title']}\n", "header")
            self.result_text.insert("end", f"生成时间: {report['generated_at']}\n\n", "time")
            self.result_text.insert("end", f"总消息: {report['total_messages']} 条\n", "msg")
            self.result_text.insert("end", f"联系人: {report['total_contacts']} 个\n", "msg")
            self.result_text.insert("end", f"重要消息: {report['total_important']} 条\n", "msg")
            self.result_text.insert("end", f"待办事项: {report['total_actions']} 条\n\n", "msg")

            if report.get("contacts"):
                self.result_text.insert("end", "联系人统计:\n", "contact")
                for c in report["contacts"][:10]:
                    self.result_text.insert("end", f"  {c['name']}: {c['total']}条 (重要:{c['important']})\n", "msg")

            if report.get("important_messages"):
                self.result_text.insert("end", "\n重要消息:\n", "contact")
                for m in report["important_messages"][:5]:
                    self.result_text.insert("end", f"  [{m['contact']}] {m['text'][:40]}\n", "msg")

            if report.get("action_items"):
                self.result_text.insert("end", "\n待办事项:\n", "contact")
                for i, a in enumerate(report["action_items"][:5], 1):
                    self.result_text.insert("end", f"  {i}. [{a['contact']}] {a['task']}\n", "msg")

            self.result_text.insert("end", f"\n完整报告: {filepath}\n", "time")
            self.result_text.configure(state="disabled")
        except ImportError:
            self._on_log("error", "[报告] 报告生成模块未找到(report_generator.py)")
        except Exception as e:
            self._on_log("error", f"[报告] 生成失败: {e}")
            import traceback
            self._on_log("error", traceback.format_exc()[-200:])

    def _generate_weekly_report(self):
        """生成本周报告"""
        self._on_log("info", "[报告] 正在生成本周报告...")
        try:
            from report_generator import ReportGenerator
            if self.engine and self.engine.storage:
                report_cfg = self.engine.config.get("report", {}) if hasattr(self.engine, 'config') else {}
                gen = ReportGenerator(self.engine.storage, report_cfg)
                report = gen.generate_weekly_report()

                fmt = report_cfg.get("format", "html")
                if fmt == "html":
                    filepath = gen.export_html(report)
                else:
                    filepath = gen.export_text(report)

                self._on_log("info", f"[报告] 周报已生成: {filepath}")
        except Exception as e:
            self._on_log("error", f"[报告] 生成失败: {e}")

    def _open_report_dir(self):
        """打开报告目录"""
        import subprocess
        import os
        report_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "reports")
        os.makedirs(report_dir, exist_ok=True)
        try:
            subprocess.Popen(f'explorer "{report_dir}"')
        except Exception as e:
            self._on_log("error", f"[报告] 打开目录失败: {e}")

    def _run_calibration(self):
        """运行窗口校准向导"""
        self._on_log("info", "[校准] 启动窗口校准向导...")
        try:
            from calibration import run_calibration
            import threading
            def _do_calib():
                result = run_calibration(self.config_path)
                if result:
                    self._on_log("info", "[校准] 校准完成，已保存到配置文件")
                    self._on_log("info", "[校准] 请重启程序使校准结果生效")
                else:
                    self._on_log("warning", "[校准] 校准已取消")
            t = threading.Thread(target=_do_calib, daemon=True)
            t.start()
        except Exception as e:
            self._on_log("error", f"[校准] 启动失败: {e}")


def run_ui(config_path="config.yaml"):
    """启动UI应用"""
    app = WeChatAIApp(config_path)
    app.mainloop()


if __name__ == "__main__":
    run_ui()
