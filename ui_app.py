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
import customtkinter as ctk
from tkinter import messagebox, filedialog
from datetime import datetime

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

COLORS = {
    "bg": "#1a1d21",
    "card": "#25282e",
    "card_hover": "#2d3038",
    "accent": "#3b82f6",
    "accent_hover": "#2563eb",
    "success": "#22c55e",
    "warning": "#f59e0b",
    "danger": "#ef4444",
    "text": "#e6e8eb",
    "text_muted": "#8b9099",
    "border": "#2c3038",
}


class WeChatAIApp(ctk.CTk):
    """主应用窗口"""

    def __init__(self, config_path="config.yaml"):
        super().__init__()
        self.config_path = config_path
        self.engine = None
        self.config_data = None

        self.title("微信 AI 助手 v2.0")
        self.geometry("900x650")
        self.minsize(800, 550)
        self.configure(fg_color=COLORS["bg"])

        self._load_config()
        self._build_ui()

    def _load_config(self):
        import yaml
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), self.config_path)
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                self.config_data = yaml.safe_load(f) or {}
        except Exception:
            self.config_data = {}

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # 顶部标题栏
        header = ctk.CTkFrame(self, fg_color=COLORS["card"], height=56, corner_radius=0)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(1, weight=1)

        title_label = ctk.CTkLabel(
            header, text="微信 AI 助手",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=COLORS["text"],
        )
        title_label.grid(row=0, column=0, padx=20, pady=15, sticky="w")

        self.status_label = ctk.CTkLabel(
            header, text="● 待机",
            font=ctk.CTkFont(size=13),
            text_color=COLORS["text_muted"],
        )
        self.status_label.grid(row=0, column=2, padx=20, pady=15, sticky="e")

        # 标签页
        self.tabs = ctk.CTkTabview(self, fg_color=COLORS["card"])
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)

        self.tab_monitor = self.tabs.add("信息提取")
        self.tab_reply = self.tabs.add("自动回复")
        self.tab_data = self.tabs.add("数据查看")
        self.tab_settings = self.tabs.add("设置")

        self._build_monitor_tab()
        self._build_reply_tab()
        self._build_data_tab()
        self._build_settings_tab()

    def _build_monitor_tab(self):
        tab = self.tab_monitor
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(2, weight=1)

        # 控制按钮区
        ctrl = ctk.CTkFrame(tab, fg_color="transparent")
        ctrl.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 5))
        ctrl.grid_columnconfigure(3, weight=1)

        self.btn_start = ctk.CTkButton(
            ctrl, text="开始监控", width=120, height=36,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=COLORS["success"], hover_color="#16a34a",
            command=self.start_monitoring,
        )
        self.btn_start.grid(row=0, column=0, padx=5)

        self.btn_stop = ctk.CTkButton(
            ctrl, text="停止监控", width=120, height=36,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=COLORS["danger"], hover_color="#dc2626",
            command=self.stop_monitoring, state="disabled",
        )
        self.btn_stop.grid(row=0, column=1, padx=5)

        self.btn_test = ctk.CTkButton(
            ctrl, text="识别当前信息", width=100, height=36,
            font=ctk.CTkFont(size=13),
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            command=self.test_ocr,
        )
        self.btn_test.grid(row=0, column=2, padx=5)

        # 统计信息
        stats_frame = ctk.CTkFrame(tab, fg_color=COLORS["card"], corner_radius=8)
        stats_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=5)
        for i in range(6):
            stats_frame.grid_columnconfigure(i, weight=1)

        self.stat_labels = {}
        stat_items = [
            ("frames", "截图帧"), ("ocr", "OCR调用"),
            ("messages", "检测消息"), ("extracted", "已提取"),
            ("important", "重要消息"), ("replies", "已回复"),
        ]
        for i, (key, label) in enumerate(stat_items):
            ctk.CTkLabel(stats_frame, text=label,
                        font=ctk.CTkFont(size=11), text_color=COLORS["text_muted"]).grid(
                row=0, column=i, padx=5, pady=(8, 2))
            val_label = ctk.CTkLabel(stats_frame, text="0",
                                     font=ctk.CTkFont(size=18, weight="bold"),
                                     text_color=COLORS["text"])
            val_label.grid(row=1, column=i, padx=5, pady=(2, 8))
            self.stat_labels[key] = val_label

        # 创建标签页切换：实时消息 / 运行日志
        self.view_tabview = ctk.CTkTabview(tab, fg_color=COLORS["card"])
        self.view_tabview.grid(row=2, column=0, sticky="nsew", padx=10, pady=5)

        tab_msg = self.view_tabview.add("实时消息")
        tab_log = self.view_tabview.add("运行日志")

        # 实时消息面板
        self._create_message_panel(tab_msg)

        # 运行日志面板
        self.log_text = ctk.CTkTextbox(
            tab_log, font=ctk.CTkFont(size=12),
            fg_color=COLORS["bg"], text_color=COLORS["text"],
            wrap="word", state="disabled",
        )
        self.log_text.pack(fill="both", expand=True, padx=8, pady=8)

        # 提取结果
        result_frame = ctk.CTkFrame(tab_log, fg_color=COLORS["bg"], corner_radius=8)
        result_frame.pack(fill="x", padx=8, pady=(0, 8))

        ctk.CTkLabel(result_frame, text="最新提取结果",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=COLORS["text"]).pack(anchor="w", padx=10, pady=(8, 4))

        self.result_text = ctk.CTkTextbox(
            result_frame, font=ctk.CTkFont(size=12),
            fg_color=COLORS["card"], text_color=COLORS["text"],
            wrap="word", state="disabled", height=120,
        )
        self.result_text.pack(fill="x", padx=10, pady=(0, 8))

    def _build_reply_tab(self):
        tab = self.tab_reply
        tab.grid_columnconfigure(0, weight=1)

        # 自动回复开关
        switch_frame = ctk.CTkFrame(tab, fg_color=COLORS["card"], corner_radius=8)
        switch_frame.grid(row=0, column=0, sticky="ew", padx=20, pady=20)
        switch_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(switch_frame, text="自动回复功能",
                     font=ctk.CTkFont(size=16, weight="bold"),
                     text_color=COLORS["text"]).grid(row=0, column=0, padx=15, pady=(15, 5), sticky="w")

        ctk.CTkLabel(switch_frame, text="开启后将自动回复对方消息（默认关闭，仅提取信息）",
                     font=ctk.CTkFont(size=12),
                     text_color=COLORS["text_muted"]).grid(row=1, column=0, padx=15, pady=(0, 10), sticky="w")

        self.reply_switch = ctk.CTkSwitch(
            switch_frame, text="启用自动回复",
            font=ctk.CTkFont(size=14),
            command=self.on_reply_switch,
        )
        auto_reply_cfg = self.config_data.get("auto_reply", {})
        self.reply_switch.select() if auto_reply_cfg.get("enabled") else self.reply_switch.deselect()
        self.reply_switch.grid(row=2, column=0, padx=15, pady=(0, 15), sticky="w")

        # 角色说明
        roles_frame = ctk.CTkFrame(tab, fg_color=COLORS["card"], corner_radius=8)
        roles_frame.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0, 20))
        roles_frame.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(roles_frame, text="角色配置",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=COLORS["text"]).grid(row=0, column=0, padx=15, pady=(15, 10), sticky="w")

        roles = self.config_data.get("roles", {})
        for i, (role_key, role) in enumerate(roles.items()):
            role_card = ctk.CTkFrame(roles_frame, fg_color=COLORS["bg"], corner_radius=6)
            role_card.grid(row=i + 1, column=0, sticky="ew", padx=15, pady=3)
            role_card.grid_columnconfigure(1, weight=1)

            ctk.CTkLabel(role_card, text=role.get("name", role_key),
                         font=ctk.CTkFont(size=13, weight="bold"),
                         text_color=COLORS["accent"]).grid(row=0, column=0, padx=10, pady=8, sticky="w")
            ctk.CTkLabel(role_card, text=role.get("reply_style", ""),
                         font=ctk.CTkFont(size=11),
                         text_color=COLORS["text_muted"]).grid(row=0, column=1, padx=10, pady=8, sticky="w")

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
                      fg_color=COLORS["success"], hover_color="#16a34a",
                      command=self.export_csv).grid(row=0, column=1, padx=5)

        ctk.CTkButton(btn_frame, text="导出JSON", width=100, height=32,
                      font=ctk.CTkFont(size=13),
                      fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
                      command=self.export_json).grid(row=0, column=2, padx=5)

        self.data_stats_label = ctk.CTkLabel(
            btn_frame, text="暂无数据",
            font=ctk.CTkFont(size=12), text_color=COLORS["text_muted"])
        self.data_stats_label.grid(row=0, column=3, padx=10, sticky="e")

        # 搜索区域
        search_frame = ctk.CTkFrame(tab, fg_color=COLORS["card"], corner_radius=8)
        search_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=5)

        ctk.CTkLabel(search_frame, text="消息搜索",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=COLORS["text"]).pack(anchor="w", padx=10, pady=5)

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
        table_frame = ctk.CTkFrame(tab, fg_color=COLORS["card"], corner_radius=8)
        table_frame.grid(row=4, column=0, sticky="nsew", padx=10, pady=(0, 10))
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)

        self.data_text = ctk.CTkTextbox(
            table_frame, font=ctk.CTkFont(size=12),
            fg_color=COLORS["bg"], text_color=COLORS["text"],
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
        monitor_frame = ctk.CTkFrame(scroll, fg_color=COLORS["card"], corner_radius=8)
        monitor_frame.pack(fill="x", padx=20, pady=20)
        monitor_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(monitor_frame, text="消息监控模式",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=COLORS["text"]).grid(row=0, column=0, columnspan=2, padx=15, pady=(15, 10), sticky="w")

        ctk.CTkLabel(monitor_frame, text="选择如何监控新消息",
                     font=ctk.CTkFont(size=12),
                     text_color=COLORS["text_muted"]).grid(row=1, column=0, columnspan=2, padx=15, pady=(0, 10), sticky="w")

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
                           text_color=COLORS["text"]).grid(row=2, column=0, columnspan=2, padx=25, pady=3, sticky="w")
        ctk.CTkRadioButton(monitor_frame, text="红点监控 — 自动检测左侧未读红点并切换",
                           variable=self.monitor_var, value="red_dot",
                           font=ctk.CTkFont(size=13),
                           text_color=COLORS["text"]).grid(row=3, column=0, columnspan=2, padx=25, pady=3, sticky="w")
        ctk.CTkRadioButton(monitor_frame, text="轮询扫描 — 定时遍历联系人列表",
                           variable=self.monitor_var, value="scan",
                           font=ctk.CTkFont(size=13),
                           text_color=COLORS["text"]).grid(row=4, column=0, columnspan=2, padx=25, pady=(3, 15), sticky="w")

        # 联系人过滤
        filter_frame = ctk.CTkFrame(scroll, fg_color=COLORS["card"], corner_radius=8)
        filter_frame.pack(fill="x", padx=20, pady=5)
        ctk.CTkLabel(filter_frame, text="👥 联系人过滤",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=COLORS["text"]).pack(anchor="w", padx=15, pady=(10, 5))

        ctk.CTkLabel(filter_frame, text="白名单（逗号分隔，留空=全部监控）:",
                     font=ctk.CTkFont(size=12), text_color=COLORS["text_muted"]).pack(anchor="w", padx=15, pady=(5, 0))
        self.whitelist_entry = ctk.CTkEntry(filter_frame, width=300, placeholder_text="如: 张三,李四,*群*")
        self.whitelist_entry.pack(fill="x", padx=15, pady=2)
        # 回填当前配置中的白名单（强制转str，防止yaml把10086解析成int）
        _filter_cfg = self.config_data.get("contacts_filter", {})
        _wl = _filter_cfg.get("whitelist", []) or []
        if _wl:
            self.whitelist_entry.insert(0, ",".join(str(x) for x in _wl))

        ctk.CTkLabel(filter_frame, text="黑名单（逗号分隔，已默认包含公众号/服务号/订阅号等）:",
                     font=ctk.CTkFont(size=12), text_color=COLORS["text_muted"]).pack(anchor="w", padx=15, pady=(5, 0))
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
        calib_frame = ctk.CTkFrame(scroll, fg_color=COLORS["card"], corner_radius=8)
        calib_frame.pack(fill="x", padx=20, pady=10)

        ctk.CTkLabel(calib_frame, text="窗口校准",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=COLORS["text"]).pack(anchor="w", padx=15, pady=5)
        ctk.CTkLabel(calib_frame, text="手动框选微信聊天区域，解决DPI缩放导致的坐标偏移",
                     font=ctk.CTkFont(size=11), text_color="gray").pack(anchor="w", padx=15)

        ctk.CTkButton(calib_frame, text="启动校准向导",
                      command=self._run_calibration).pack(padx=15, pady=10, anchor="w")

        # 模式开关
        mode_frame = ctk.CTkFrame(scroll, fg_color=COLORS["card"], corner_radius=8)
        mode_frame.pack(fill="x", padx=20, pady=10)

        ctk.CTkLabel(mode_frame, text="智能模式",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=COLORS["text"]).pack(anchor="w", padx=15, pady=5)

        self.fast_mode_switch = ctk.CTkSwitch(mode_frame, text="快速模式（窗口标题检测，省CPU）",
                                              onvalue=True, offvalue=False)
        self.fast_mode_switch.pack(anchor="w", padx=15, pady=3)

        self.dnd_switch = ctk.CTkSwitch(mode_frame, text="勿扰模式（检测到用户操作时暂停）",
                                        onvalue=True, offvalue=False)
        self.dnd_switch.pack(anchor="w", padx=15, pady=3)

        # 报告设置
        report_frame = ctk.CTkFrame(scroll, fg_color=COLORS["card"], corner_radius=8)
        report_frame.pack(fill="x", padx=20, pady=10)

        ctk.CTkLabel(report_frame, text="定时报告",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=COLORS["text"]).pack(anchor="w", padx=15, pady=5)

        report_btn_frame = ctk.CTkFrame(report_frame, fg_color="transparent")
        report_btn_frame.pack(fill="x", padx=15, pady=5)

        ctk.CTkButton(report_btn_frame, text="生成今日报告",
                      command=self._generate_today_report).pack(side="left", padx=5)

        ctk.CTkButton(report_btn_frame, text="生成本周报告",
                      command=self._generate_weekly_report).pack(side="left", padx=5)

        ctk.CTkButton(report_btn_frame, text="打开报告目录",
                      command=self._open_report_dir).pack(side="left", padx=5)

        # LLM设置
        llm_frame = ctk.CTkFrame(scroll, fg_color=COLORS["card"], corner_radius=8)
        llm_frame.pack(fill="x", padx=20, pady=20)
        llm_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(llm_frame, text="LLM 配置",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=COLORS["text"]).grid(row=0, column=0, columnspan=2, padx=15, pady=(15, 10), sticky="w")

        llm_cfg = self.config_data.get("llm", {})
        ctk.CTkLabel(llm_frame, text="API地址", font=ctk.CTkFont(size=12),
                     text_color=COLORS["text_muted"]).grid(row=1, column=0, padx=15, pady=5, sticky="w")
        self.entry_url = ctk.CTkEntry(llm_frame, font=ctk.CTkFont(size=12))
        self.entry_url.insert(0, llm_cfg.get("base_url", ""))
        self.entry_url.grid(row=1, column=1, padx=15, pady=5, sticky="ew")

        ctk.CTkLabel(llm_frame, text="API Key", font=ctk.CTkFont(size=12),
                     text_color=COLORS["text_muted"]).grid(row=2, column=0, padx=15, pady=5, sticky="w")
        self.entry_key = ctk.CTkEntry(llm_frame, font=ctk.CTkFont(size=12), show="*")
        self.entry_key.insert(0, llm_cfg.get("api_key", ""))
        self.entry_key.grid(row=2, column=1, padx=15, pady=5, sticky="ew")

        ctk.CTkLabel(llm_frame, text="模型", font=ctk.CTkFont(size=12),
                     text_color=COLORS["text_muted"]).grid(row=3, column=0, padx=15, pady=5, sticky="w")
        self.entry_model = ctk.CTkEntry(llm_frame, font=ctk.CTkFont(size=12))
        self.entry_model.insert(0, llm_cfg.get("model", ""))
        self.entry_model.grid(row=3, column=1, padx=15, pady=5, sticky="ew")

        # 高级设置（默认隐藏）
        self.advanced_frame = ctk.CTkFrame(scroll, fg_color="transparent")

        # OCR设置
        ocr_frame = ctk.CTkFrame(self.advanced_frame, fg_color=COLORS["card"], corner_radius=8)
        ocr_frame.pack(fill="x", padx=20, pady=(0, 20))
        ocr_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(ocr_frame, text="OCR 识别优化",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=COLORS["text"]).grid(row=0, column=0, columnspan=2, padx=15, pady=(15, 10), sticky="w")

        wechat_cfg = self.config_data.get("wechat", {})
        ctk.CTkLabel(ocr_frame, text="识别精度 (0.5-1.0)", font=ctk.CTkFont(size=12),
                     text_color=COLORS["text_muted"]).grid(row=1, column=0, padx=15, pady=5, sticky="w")
        self.slider_scale = ctk.CTkSlider(ocr_frame, from_=0.5, to=1.0, number_of_steps=10)
        self.slider_scale.set(wechat_cfg.get("ocr_scale", 0.85))
        self.slider_scale.grid(row=1, column=1, padx=15, pady=5, sticky="ew")

        ctk.CTkLabel(ocr_frame, text="最低置信度 (0.3-0.9)", font=ctk.CTkFont(size=12),
                     text_color=COLORS["text_muted"]).grid(row=2, column=0, padx=15, pady=5, sticky="w")
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
        ai_frame = ctk.CTkFrame(scroll, fg_color=COLORS["card"], corner_radius=8)
        ai_frame.pack(fill="x", padx=20, pady=10)
        ai_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(ai_frame, text="AI 智能学习",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=COLORS["text"]).grid(row=0, column=0, columnspan=2, padx=15, pady=(15, 10), sticky="w")

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
                      fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
                      command=self.save_settings).pack(pady=(0, 20))

    def _create_message_panel(self, parent):
        """创建实时消息通知面板"""
        # 消息标题
        header = ctk.CTkLabel(parent, text="📡 实时消息", font=ctk.CTkFont(size=16, weight="bold"))
        header.pack(pady=(5, 5), anchor="w")

        # 消息列表（可滚动）
        self.msg_list_frame = ctk.CTkScrollableFrame(parent, height=300)
        self.msg_list_frame.pack(fill="both", expand=True, padx=5, pady=5)

        # 空状态提示
        self.msg_empty_label = ctk.CTkLabel(
            self.msg_list_frame,
            text="暂无消息，启动监控后自动显示",
            text_color="gray"
        )
        self.msg_empty_label.pack(pady=20)

        # 统计信息
        self.msg_stats_label = ctk.CTkLabel(parent, text="今日: 0 条消息 | 0 条重要", text_color="gray")
        self.msg_stats_label.pack(pady=(5, 5))

    def _on_new_message(self, msg_data):
        """新消息回调（线程安全）"""
        self.after(0, lambda: self._add_message_card(msg_data))

    def _add_message_card(self, msg_data):
        """添加消息卡片到面板"""
        # 隐藏空状态
        if hasattr(self, 'msg_empty_label') and self.msg_empty_label.winfo_exists():
            try:
                self.msg_empty_label.pack_forget()
            except:
                pass

        contact = msg_data.get("contact", "未知")
        sender = msg_data.get("sender", "other")
        content = msg_data.get("content", "")
        timestamp = msg_data.get("timestamp", "")
        is_important = msg_data.get("is_important", False)

        # 创建消息卡片（插到最顶部，最新消息在最上方）
        card = ctk.CTkFrame(self.msg_list_frame, corner_radius=8)
        # 获取当前第一个子组件（如果有的话），把新卡片插到它前面
        first_child = self.msg_list_frame.winfo_children()[0] if self.msg_list_frame.winfo_children() else None
        if first_child and str(first_child) != str(card):
            card.pack(fill="x", padx=5, pady=3, before=first_child)
        else:
            card.pack(fill="x", padx=5, pady=3)

        # 根据重要性设置颜色
        if is_important:
            border_color = "#FF6B6B"
            bg_color = ("#FFE0E0", "#3A2020")
        else:
            border_color = "#4ECDC4"
            bg_color = ("#F0F0F0", "#2B2B2B")

        card.configure(fg_color=bg_color, border_width=1, border_color=border_color)

        # 第一行：联系人 + 时间
        top_frame = ctk.CTkFrame(card, fg_color="transparent")
        top_frame.pack(fill="x", padx=8, pady=(4, 2))

        contact_label = ctk.CTkLabel(
            top_frame, text=f"{'🔴' if is_important else '💬'} {contact}",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=("#FF4444" if is_important else "#2196F3")
        )
        contact_label.pack(side="left")

        time_label = ctk.CTkLabel(top_frame, text=timestamp, text_color="gray", font=ctk.CTkFont(size=11))
        time_label.pack(side="right")

        # 第二行：消息内容
        sender_prefix = "我: " if sender == "me" else ""
        content_label = ctk.CTkLabel(
            card, text=f"{sender_prefix}{content[:100]}",
            font=ctk.CTkFont(size=12),
            wraplength=400, justify="left"
        )
        content_label.pack(fill="x", padx=8, pady=(0, 2))

        # 重要原因
        if is_important and msg_data.get("importance_reason"):
            reason_label = ctk.CTkLabel(
                card, text=f"📌 {msg_data['importance_reason']}",
                font=ctk.CTkFont(size=11), text_color="#FF6B6B"
            )
            reason_label.pack(fill="x", padx=8, pady=(0, 4))

        # 限制最多显示100条
        children = self.msg_list_frame.winfo_children()
        if len(children) > 100:
            children[0].destroy()

        # 更新统计
        self._msg_count = getattr(self, '_msg_count', 0) + 1
        self._important_count = getattr(self, '_important_count', 0) + (1 if is_important else 0)
        if hasattr(self, 'msg_stats_label'):
            self.msg_stats_label.configure(
                text=f"今日: {self._msg_count} 条消息 | {self._important_count} 条重要"
            )

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
            "running": ("● 运行中", COLORS["success"]),
            "stopped": ("● 已停止", COLORS["text_muted"]),
            "error": ("● 错误", COLORS["danger"]),
        }
        text, color = status_map.get(status, ("● 未知", COLORS["text_muted"]))
        self.status_label.configure(text=text, text_color=color)

    def _ensure_result_tags(self):
        """确保结果文本框的标签样式已配置"""
        self.result_text.tag_config("header", foreground="#3b82f6")
        self.result_text.tag_config("separator", foreground="#2c3038")
        self.result_text.tag_config("contact", foreground="#22c55e")
        self.result_text.tag_config("msg", foreground="#e6e8eb")
        self.result_text.tag_config("time", foreground="#8b9099")

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
