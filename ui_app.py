import ctypes
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass
"""
NOYA Chat 微信助手 — UI主界面
========================
基于 customtkinter 的现代化桌面界面，适合小白使用。
功能：信息提取监控 / 自动回复开关 / 数据查看 / 设置
"""
import os
import re
import json
import threading
import time
import yaml
import customtkinter as ctk
from tkinter import messagebox, filedialog
from datetime import datetime
from ui_theme import WC_COLORS

# ==========================================================================
# 便携模式基准目录（打包 exe 后关键）
#   开发态：__file__ 所在目录（项目根）。
#   打包态(sys._MEIPASS 存在)：PyInstaller 会把只读资源解压到临时目录
#   _MEIPASS，该目录只读且重启清空，不能写配置/历史。因此可写数据必须
#   重定向到 exe 同目录（APP_BASE），保证 config.yaml / data / debug 等
#   持久化在用户机器上、不被清掉。
# ==========================================================================
import sys as _sys
if getattr(_sys, "frozen", False) and hasattr(_sys, "_MEIPASS"):
    # 打包后：sys.executable 即 dist/微信AI助手.exe，其目录可写
    APP_BASE = os.path.dirname(os.path.abspath(_sys.executable))
else:
    APP_BASE = os.path.dirname(os.path.abspath(__file__))


def _app_path(*parts):
    """拼可写路径（配置/历史/缓存），始终落在 APP_BASE。"""
    return os.path.join(APP_BASE, *parts)


# 全局中文字体：统一为微软雅黑，避免 Windows 默认 Segoe UI 下中文 fallback 不一致
FONT_FAMILY = "Microsoft YaHei"

ctk.set_appearance_mode("light")
# macOS 风格：干净白 + Apple蓝 #007AFF + 大圆角 + 极细分隔
# 不调用 set_default_color_theme 避免污染 accent 色系


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

        # ======================================================================
        # V3 P0-1: 三栏会话切换 + 按会话过滤
        #   _msg_rows_by_contact:   {contact: [(row_CTkFrame, msg_data), ...]}  最新在前
        #   _contact_filter_all:    特殊键 "📋 全部会话"，显示所有
        #   _active_contact:        当前过滤的 contact 或 _contact_filter_all
        # ======================================================================
        self._contact_filter_all = "📋 全部会话"
        self._contact_filter_usage = "📖 使用说明"   # 独立系统卡：点击显示使用说明聊天记录
        self._msg_rows_by_contact = {}           # contact -> list[(row_frame, msg_data)]（最新在前）
        self._all_filtered = False                # 内部保护：避免循环调用

        # === P0 修复：对话上下文开关/轮数必须初始化，否则 _on_new_message 读
        #     self._context_max_turns 抛 AttributeError → 实时消息卡片（含左栏会话卡）
        #     全部建不出来，左栏只剩“全部会话”。缺省关闭，挂载时按配置开启。 ===
        self._context_enabled = False
        self._context_max_turns = 10

        self.title("微信AI助手")
        self.geometry("1280x800")
        self.minsize(1024, 680)
        self.configure(fg_color=WC_COLORS["bg"])

        self._load_config()
        self._build_ui()

        # 启动预览队列轮询（必须在主线程，跨线程安全）
        self.after(100, self._poll_capture)

        # 快捷键 Ctrl+T 切换开始/停止
        self.bind_all("<Control-t>", lambda e: self._toggle_monitoring())
        self.bind_all("<Control-T>", lambda e: self._toggle_monitoring())

        # ===== P0: 4 个全局快捷键 =====
        self.bind_all("<Control-f>", self._hotkey_focus_search)
        self.bind_all("<Control-F>", self._hotkey_focus_search)
        self.bind_all("<Control-d>", self._hotkey_toggle_important)
        self.bind_all("<Control-D>", self._hotkey_toggle_important)
        self.bind_all("<Control-S>", self._hotkey_goto_settings)
        self.bind_all("<Control-s>", lambda e: None)  # 小写 Ctrl+S 不拦截避免与保存冲突
        self.bind_all("<Escape>", self._hotkey_minimize_or_close)

    def _load_config(self):
        import yaml
        config_path = _app_path(self.config_path)
        # 打包态首启：exe 同目录可能还没有 config.yaml，从包内模板复制
        if not os.path.exists(config_path):
            bundled = os.path.join(getattr(_sys, "_MEIPASS", ""), self.config_path)
            if os.path.exists(bundled):
                try:
                    os.makedirs(os.path.dirname(config_path), exist_ok=True)
                    with open(bundled, "r", encoding="utf-8") as src, \
                            open(config_path, "w", encoding="utf-8") as dst:
                        dst.write(src.read())
                except Exception:
                    pass
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                self.config_data = yaml.safe_load(f) or {}
        except Exception:
            self.config_data = {}

    def _build_ui(self):
        print("[DBG-BUILD] start")
        # 2列布局：侧栏(52) | 主内容(满宽)
        self.grid_columnconfigure(0, weight=0, minsize=56)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # ====== 左侧导航栏（暖调编辑风侧栏） ======
        self.sidebar = ctk.CTkFrame(self, fg_color=WC_COLORS["sidebar"], width=56,
                               corner_radius=0, border_width=0)
        self.sidebar.grid(row=0, column=0, sticky="ns")
        self.sidebar.grid_propagate(False)

        # 品牌标记（圆形头像 + 在线绿点）
        avatar_frame = ctk.CTkFrame(self.sidebar, fg_color="transparent", width=44, height=44)
        avatar_frame.grid(row=0, column=0, pady=(22, 8))
        self.sidebar_avatar = ctk.CTkLabel(
            avatar_frame, text="N", width=40, height=40, corner_radius=20,
            fg_color=WC_COLORS["avatar_me"], text_color="#FFFFFF",
            font=ctk.CTkFont(family=FONT_FAMILY, size=18, weight="bold"),
        )
        self.sidebar_avatar.pack()
        self.sidebar_online_dot = ctk.CTkLabel(
            avatar_frame, text="", width=9, height=9, corner_radius=5,
            fg_color=WC_COLORS["online_dot"],
        )
        self.sidebar_online_dot.place(relx=0.76, rely=0.76, anchor="center")

        # 分隔线
        sep = ctk.CTkFrame(self.sidebar, fg_color=WC_COLORS["border"], height=1, width=32)
        sep.grid(row=1, column=0, pady=(0, 6))

        # 导航按钮
        nav_items = [
            ("📡", "monitor", "监控", True),
            ("💬", "reply", "回复", False),
            ("📊", "data", "数据", False),
            ("⚙️", "settings", "设置", False),
        ]
        self._nav_buttons = {}
        self._nav_badges = {}
        for i, (icon, key, label, active) in enumerate(nav_items):
            btn_wrap = ctk.CTkFrame(self.sidebar, fg_color="transparent")
            btn_wrap.grid(row=i + 2, column=0, pady=3, padx=6)
            btn = ctk.CTkButton(
                btn_wrap, text=icon, width=44, height=44,
                fg_color="transparent", hover_color=WC_COLORS["sidebar_hover"],
                corner_radius=10, font=ctk.CTkFont(size=19),
                command=lambda k=key: self._switch_nav(k),
            )
            btn.pack()
            if active:
                btn.configure(fg_color=WC_COLORS["sidebar_active"])
            self._nav_buttons[key] = btn

            badge = ctk.CTkLabel(
                btn_wrap, text="", width=17, height=17, corner_radius=9,
                fg_color=WC_COLORS["danger"], text_color="#FFFFFF",
                font=ctk.CTkFont(family=FONT_FAMILY, size=9, weight="bold"),
            )
            self._nav_badges[key] = badge

        # 底部状态
        self.sidebar_status = ctk.CTkLabel(
            self.sidebar, text="● 待机",
            font=ctk.CTkFont(size=11), text_color=WC_COLORS["text_muted"],
        )
        self.sidebar_status.grid(row=10, column=0, pady=(40, 6))

        # 关于按钮
        about_btn = ctk.CTkButton(
            self.sidebar, text="ⓘ", width=40, height=34,
            fg_color="transparent", hover_color=WC_COLORS["sidebar_hover"],
            corner_radius=10, font=ctk.CTkFont(size=15, weight="bold"),
            text_color=WC_COLORS["text_muted"],
            command=self._show_about_dialog,
        )
        about_btn.grid(row=11, column=0, pady=(0, 16))

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
        """V3: 微信 PC 原版布局 —— 顶栏 + 三栏
              ┌─ 顶部控制条 ────────────────────────────────────┐
              ├─ 统计条(6卡) ────────────────────────────────────┤
              │ [会话] │ [聊天消息]           │ [提取/详情]     │
              │  搜索   │  - 顶部: 群名+状态  │  类型/关键词    │
              │  会话卡 │  - 中部: 气泡消息   │  重要/提取字段  │
              │  (左侧) │  (自己右绿, 对方左白)│  摘要/置信度    │
              │(180px)  │ (中间 expand)       │ (右侧 260px)   │
              └──────── ┴──────────────────── ┴─────────────────┘
        """
        tab = self.tab_monitor
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(2, weight=1)

        # ====== 顶部控制条 ======
        ctrl = ctk.CTkFrame(tab, fg_color=WC_COLORS["card"], corner_radius=14, height=54)
        ctrl.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 4))
        ctrl.grid_columnconfigure(8, weight=1)

        self.btn_start = ctk.CTkButton(
            ctrl, text="▶ 开始监控", width=110, height=34,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=WC_COLORS["accent"], hover_color=WC_COLORS["accent_hover"],
            corner_radius=10, command=self.start_monitoring,
        )
        self.btn_start.grid(row=0, column=0, padx=(14, 4), pady=10)

        self.btn_stop = ctk.CTkButton(
            ctrl, text="■ 停止", width=80, height=34, corner_radius=10,
            font=ctk.CTkFont(size=12),
            fg_color=WC_COLORS["danger"], hover_color="#D0443E",
            command=self.stop_monitoring, state="disabled",
        )
        self.btn_stop.grid(row=0, column=1, padx=4, pady=10)

        self.btn_test = ctk.CTkButton(
            ctrl, text="🔍 识别", width=72, height=34, corner_radius=10,
            font=ctk.CTkFont(size=12),
            fg_color=WC_COLORS["border"], hover_color=WC_COLORS["text_muted2"],
            text_color=WC_COLORS["text"],
            command=self.test_ocr,
        )
        self.btn_test.grid(row=0, column=2, padx=4, pady=10)

        self.btn_preview = ctk.CTkButton(
            ctrl, text="🖼 预览", width=72, height=34, corner_radius=10,
            fg_color=WC_COLORS["accent_light"], hover_color="#F5E0D5",
            text_color=WC_COLORS["accent"],
            font=ctk.CTkFont(size=12), command=self._open_preview_window)
        self.btn_preview.grid(row=0, column=3, padx=4, pady=10)

        self.btn_show_wechat = ctk.CTkButton(
            ctrl, text="👁 显示微信", width=96, height=34, corner_radius=10,
            font=ctk.CTkFont(size=12),
            fg_color=WC_COLORS["accent_light"], hover_color="#F5E0D5",
            text_color=WC_COLORS["accent"],
            command=self._show_wechat_window,
        )
        self.btn_show_wechat.grid(row=0, column=4, padx=4, pady=10)

        self.top_current_label = ctk.CTkLabel(
            ctrl, text="当前会话：未连接",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            text_color=WC_COLORS["text"], anchor="e",
        )
        self.top_current_label.grid(row=0, column=8, padx=(10, 16), sticky="e")

        # ====== 统计卡片 ======
        stats_frame = ctk.CTkFrame(tab, fg_color=WC_COLORS["card"], corner_radius=14,
                                   border_width=0)
        stats_frame.grid(row=1, column=0, sticky="ew", padx=12, pady=(4, 8))
        for i in range(6):
            stats_frame.grid_columnconfigure(i, weight=1)

        self.stat_labels = {}
        stat_items = [
            ("frames",    "📷 截图",   WC_COLORS["info"]),
            ("ocr",       "🔤 OCR",   "#9B7EC4"),
            ("messages",  "💬 消息",   WC_COLORS["accent"]),
            ("extracted", "📋 提取",   WC_COLORS["keyword"]),
            ("important", "⭐ 重要",   WC_COLORS["danger"]),
            ("replies",   "✉️ 回复",   "#6B8E6B"),
        ]
        for i, (key, label, val_color) in enumerate(stat_items):
            col = ctk.CTkFrame(stats_frame, fg_color="transparent")
            col.grid(row=0, column=i, padx=4, pady=8, sticky="nsew")
            col.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(col, text=label,
                         font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                         text_color=WC_COLORS["text_muted"]).grid(
                row=0, column=0, pady=(0, 2), sticky="ew")
            val_label = ctk.CTkLabel(col, text="0",
                                     font=ctk.CTkFont(family=FONT_FAMILY, size=22, weight="bold"),
                                     text_color=val_color)
            val_label.grid(row=1, column=0, sticky="ew")
            self.stat_labels[key] = val_label

        # ====== V3: 两栏主体（砍掉右侧空白详情面板） ======
        two_col = ctk.CTkFrame(tab, fg_color="transparent")
        two_col.grid(row=2, column=0, sticky="nsew", padx=12, pady=(4, 12))
        two_col.grid_columnconfigure(0, weight=0, minsize=200)
        two_col.grid_columnconfigure(1, weight=1)
        two_col.grid_rowconfigure(0, weight=1)

        # 左：会话列表（200px，紧凑）
        contacts_col = ctk.CTkFrame(two_col, fg_color=WC_COLORS["card"], corner_radius=14,
                                     width=200, border_width=0)
        contacts_col.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        contacts_col.grid_propagate(True)
        self._create_contact_list_panel(contacts_col)

        # 中：聊天消息 + 日志 Tabview（满宽）
        center_col = ctk.CTkFrame(two_col, fg_color=WC_COLORS["card"], corner_radius=14,
                                 border_width=0)
        center_col.grid(row=0, column=1, sticky="nsew", padx=(0, 0))
        center_col.grid_rowconfigure(1, weight=1)
        center_col.grid_columnconfigure(0, weight=1)

        # 中栏顶部栏（#EDEDED 模拟微信聊天顶部：会话名 + 在线状态）
        chat_header = ctk.CTkFrame(center_col, fg_color=WC_COLORS["header"], height=44,
                                    corner_radius=0)
        chat_header.grid(row=0, column=0, sticky="ew")
        chat_header.grid_propagate(False)
        chat_header.grid_columnconfigure(0, weight=1)

        self.chat_title = ctk.CTkLabel(
            chat_header, text="💬 实时消息监控",
            font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"),
            text_color=WC_COLORS["text"], anchor="w",
        )
        self.chat_title.grid(row=0, column=0, padx=14, pady=10, sticky="w")

        self.chat_subtitle = ctk.CTkLabel(
            chat_header, text="● 等待新消息",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=WC_COLORS["online_dot"], anchor="e",
        )
        self.chat_subtitle.grid(row=0, column=1, padx=14, pady=10, sticky="e")

        # 在线状态动画（呼吸灯效果：每1.5秒切换透明度）
        def _breathe_online():
            try:
                cur = self.chat_subtitle.cget("text_color")
                if cur == WC_COLORS["online_dot"]:
                    self.chat_subtitle.configure(text_color=WC_COLORS["accent_light"])
                else:
                    self.chat_subtitle.configure(text_color=WC_COLORS["online_dot"])
                self.after(1500, _breathe_online)
            except Exception:
                pass
        self.after(1500, _breathe_online)

        # 中栏 Tabview（消息 / 日志）
        self.view_tabview = ctk.CTkTabview(
            center_col, fg_color=WC_COLORS["bg"], corner_radius=0,
            border_width=0, segmented_button_fg_color=WC_COLORS["header"],
            segmented_button_selected_color=WC_COLORS["card"],
            segmented_button_selected_hover_color=WC_COLORS["card_hover"],
            segmented_button_unselected_color=WC_COLORS["header"],
            text_color=WC_COLORS["text"],
        )
        self.view_tabview.grid(row=1, column=0, sticky="nsew")

        tab_msg = self.view_tabview.add("💬 消息")
        tab_log = self.view_tabview.add("📝 运行日志")
        tab_preview = self.view_tabview.add("🖥️ 预览")

        self._create_message_panel(tab_msg)

        # 运行日志
        log_frame = ctk.CTkFrame(tab_log, fg_color="transparent")
        log_frame.pack(fill="both", expand=True, padx=6, pady=6)
        log_frame.grid_columnconfigure(0, weight=1)
        log_frame.grid_rowconfigure(0, weight=3)
        log_frame.grid_rowconfigure(1, weight=1)

        self.log_text = ctk.CTkTextbox(
            log_frame, font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=WC_COLORS["card"], text_color=WC_COLORS["text"],
            wrap="word", state="disabled",
            border_width=0, corner_radius=14,
        )
        self.log_text.grid(row=0, column=0, sticky="nsew", pady=(0, 4))

        # 日志底部内置"最新提取结果"
        result_frame = ctk.CTkFrame(log_frame, fg_color=WC_COLORS["card"], corner_radius=14,
                                     border_width=0)
        result_frame.grid(row=1, column=0, sticky="nsew")
        result_frame.grid_columnconfigure(0, weight=1)
        result_frame.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(result_frame, text="📋 最新提取结果",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=WC_COLORS["text"]).grid(
            row=0, column=0, sticky="w", padx=10, pady=(8, 4))
        self.result_text = ctk.CTkTextbox(
            result_frame, font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=WC_COLORS["card"], text_color=WC_COLORS["text"],
            wrap="word", state="disabled",
        )
        self.result_text.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 8))

        # 预览：嵌入中栏
        self._create_main_preview(tab_preview)

    # ==============================================================
    # V3: 会话列表 （微信 PC 左栏：搜索框 + 会话卡片）
    # ==============================================================
    def _create_contact_list_panel(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)

        # 搜索框（微信PC会话搜索：#DCDCDC 圆角）
        search_wrap = ctk.CTkFrame(parent, fg_color="transparent")
        search_wrap.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        self.contact_search_entry = ctk.CTkEntry(
            search_wrap, placeholder_text="🔍 搜索联系人", height=32, corner_radius=10,
            fg_color=WC_COLORS["bg"], placeholder_text_color=WC_COLORS["text_muted"],
            text_color=WC_COLORS["text"], border_width=0,
            font=ctk.CTkFont(size=12),
        )
        self.contact_search_entry.pack(fill="x", side="top")

        # 批量操作工具栏（默认隐藏，点击批量模式按钮显示）
        self._batch_bar = ctk.CTkFrame(parent, fg_color="transparent", height=32)
        self._batch_bar.grid_remove()
        self._batch_mode = False
        self._batch_selected = set()

        self._batch_select_all_btn = ctk.CTkButton(
            self._batch_bar, text="全选", width=60, height=26,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            command=self._batch_select_all)
        self._batch_select_all_btn.pack(side="left", padx=(8, 2))

        self._batch_delete_btn = ctk.CTkButton(
            self._batch_bar, text="🗑 删除选中", width=90, height=26,
            font=ctk.CTkFont(size=11),
            fg_color=WC_COLORS["danger"], hover_color="#E53935",
            command=self._batch_delete_selected)
        self._batch_delete_btn.pack(side="left", padx=2)

        self._batch_cancel_btn = ctk.CTkButton(
            self._batch_bar, text="取消", width=50, height=26,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=WC_COLORS["text_muted2"], hover_color=WC_COLORS["text_muted"],
            command=self._batch_cancel)
        self._batch_cancel_btn.pack(side="right", padx=8)

        # 批量模式切换按钮（放在搜索框右侧的小按钮，通过右键菜单也可进入）
        self._batch_toggle_btn = ctk.CTkButton(
            search_wrap, text="📋 批量", width=52, height=28,
            font=ctk.CTkFont(size=10),
            fg_color=WC_COLORS["bg"], hover_color=WC_COLORS["sidebar_hover"],
            text_color=WC_COLORS["text_muted"], corner_radius=8,
            command=self._toggle_batch_mode)
        self._batch_toggle_btn.place(relx=1.0, rely=0.5, anchor="e", x=-4)

        self._refresh_contacts_btn = ctk.CTkButton(
            search_wrap, text="🔄", width=28, height=28,
            font=ctk.CTkFont(size=10),
            fg_color=WC_COLORS["bg"], hover_color=WC_COLORS["sidebar_hover"],
            text_color=WC_COLORS["text_muted"], corner_radius=8,
            command=self._refresh_contacts_btn_click)
        self._refresh_contacts_btn.place(relx=1.0, rely=0.5, anchor="e", x=-60)

        # 会话列表滚动
        self.contact_list_frame = ctk.CTkScrollableFrame(
            parent, fg_color=WC_COLORS["card_hover"], corner_radius=0, border_width=0,
            scrollbar_button_color=WC_COLORS["border"], scrollbar_button_hover_color=WC_COLORS["text_muted2"],
        )
        self.contact_list_frame.grid(row=1, column=0, sticky="nsew", padx=0, pady=(4, 0))

        # 会话卡片集合 {contact: info_dict}
        self._contact_cards = {}
        # V3.3 根治：显式记录 frame 对象，避免依赖 CTkScrollableFrame.winfo_children()
        # 在 CTkScrollableFrame 上 winfo_children() 返回的是内部 _parent_frame，
        # 用 str() 比对会误删系统卡/联系人卡。改用对象身份 is 判断。
        self._system_card_frames = set()   # 系统卡 frame（使用说明 + 全部会话）
        self._contact_card_frames = set()  # 联系人卡 frame（rebuild 时精确销毁）
        self._active_contact = None
        # 按联系人划分的消息池：contact -> [msg_data, ...]，最新在前（索引0）。
        # 点击会话卡时据此重建右侧消息列表，避免 pack_forget 过滤导致的空白/顺序错乱。
        # 持久化到 data/messages_history.json（防抖写盘），重启后保留历史。
        self._history_path = _app_path("data", "messages_history.json")
        self._history_save_job = None      # after() 句柄（防抖）
        self._history_dirty = False
        self._messages_store = {}      # 仅缓存"已打开/实时"的联系人全文（懒加载，不再全量驻留）
        self._conv_index = {}          # 轻量会话索引: contact -> {count,preview,last_sender,last_time,unread,is_group}
        self._msg_seq = 0
        self._msg_index = {}           # 去重索引 contact -> {msg_key: idx}
        self._all_messages_live = []   # "全部会话"视图下的实时新增（尚未落库）
        self._storage_cache = None     # 自建存储对象缓存（避免 engine 未初始化时删除/查询失效）
        self._msg_bubble_cache = {}    # 消息气泡缓存: {contact: {msg_key: CTkFrame}}
        self._last_rendered_key = None  # 上次渲染的会话标识，用于增量更新判断
        self._load_history()
        print(f"[启动诊断] _load_history 完成, _conv_index={len(self._conv_index)} 个联系人")

        # V3.2: 顶部两张系统卡（永远存在，置于最上方）：
        #   「📖 使用说明」 —— 独立卡片，点击显示使用说明聊天记录（最早、最顶）
        #   「📋 全部会话」 —— 默认视图，点击显示所有会话混排（紧随其后）
        #   用 pack(before=...) 保证顺序：usage 卡在最顶，all 卡次之。
        usage_frame, usage_title, usage_preview, usage_avatar = \
            self._create_system_card(
                key=self._contact_filter_usage, title=self._contact_filter_usage,
                preview="点击查看：软件怎么用（聊天式说明）", emoji="📖",
                accent=WC_COLORS["warning"],
                top=True)

        sys_wrap, sys_title, sys_preview, sys_avatar = \
            self._create_system_card(
                key=self._contact_filter_all, title=self._contact_filter_all,
                preview="默认视图：显示所有会话最新消息", emoji="📋",
                accent=WC_COLORS["accent"], top=True)

        # 修正：右侧菜单只在「全部会话」卡上提供「删除全部历史 / 批量模式」
        def _sys_right_click(e):
            import tkinter as _tk
            m = _tk.Menu(self, tearoff=0)
            m.add_command(label="🗑 删除全部会话历史…",
                          command=self._delete_all_contacts)
            m.add_command(label="📋 批量选择模式",
                          command=self._toggle_batch_mode)
            try:
                m.tk_popup(e.x_root, e.y_root)
            finally:
                try:
                    m.grab_release()
                except Exception:
                    pass
        for w in (sys_wrap, sys_avatar, sys_title, sys_preview):
            try:
                w.bind("<Button-3>", _sys_right_click)
                w.bind("<Control-Button-1>", _sys_right_click)
            except Exception:
                pass

    def _create_system_card(self, key, title, preview, emoji, accent, top=False, before=None):
        """V3.2: 通用系统卡工厂（左栏顶部固定卡）。返回 (frame, title, preview, avatar)。
        - top=True：用 pack(side=\"top\") 置于最顶（不依赖 before）。
        - before=某 frame：插入到该 frame 之前（用于「全部会话」卡排在「使用说明」之后）。
        点击 → _set_active_contact(key)；悬停高亮；禁止右键删除菜单。"""
        try:
            # CTkScrollableFrame 标准用法：直接 pack 进 CTkScrollableFrame 本身，
            # CTk 自动重定向到内部滚动内容区。
            if top:
                wrap = ctk.CTkFrame(self.contact_list_frame, fg_color=WC_COLORS["card_hover"], height=52)
                wrap.pack(side="top", fill="x")
            elif before is not None:
                wrap = ctk.CTkFrame(self.contact_list_frame, fg_color=WC_COLORS["card_hover"], height=52)
                wrap.pack(before=before, side="top", fill="x")
            else:
                wrap = ctk.CTkFrame(self.contact_list_frame, fg_color=WC_COLORS["card_hover"], height=52)
                wrap.pack(side="top", fill="x")
            wrap.pack_propagate(False)

            avatar = ctk.CTkLabel(wrap, text=emoji, width=36, height=36, corner_radius=18,
                                  fg_color=accent, text_color="#FFFFFF",
                                  font=ctk.CTkFont(size=15))
            avatar.pack(side="left", padx=(10, 8), pady=10)

            text = ctk.CTkFrame(wrap, fg_color="transparent")
            text.pack(side="left", fill="both", expand=True, pady=9)
            t_label = ctk.CTkLabel(text, text=title,
                                   text_color=WC_COLORS["text"],
                                   font=ctk.CTkFont(family="Microsoft YaHei", size=12, weight="bold"),
                                   anchor="w")
            t_label.pack(fill="x", side="top")
            p_label = ctk.CTkLabel(text, text=preview,
                                   text_color=WC_COLORS["text_muted2"],
                                   font=ctk.CTkFont(family=FONT_FAMILY, size=10), anchor="w")
            p_label.pack(fill="x", side="top")

            # 系统卡信息登记（供 active 切换用）
            self._contact_cards[key] = {
                "frame": wrap, "title": t_label, "preview": p_label,
                "badge": None, "is_group": False, "unread": 0, "avatar": avatar,
                "_is_system": True, "_is_system_all": (key == self._contact_filter_all),
            }
            # V3.3: 显式记录系统卡 frame（根治 CTkScrollableFrame winfo_children 误删）
            self._system_card_frames.add(wrap)

            # 悬停/点击绑定
            def _enter(_e):
                try:
                    if self._active_contact != key:
                        wrap.configure(fg_color=WC_COLORS["card_hover"])
                except Exception:
                    pass
            def _leave(_e):
                try:
                    bg = WC_COLORS["card_active"] if self._active_contact == key else WC_COLORS["card_hover"]
                    wrap.configure(fg_color=bg)
                except Exception:
                    pass
            def _click(_e):
                self._set_active_contact(key)
            for w in (wrap, avatar, t_label, p_label):
                try:
                    w.bind("<Enter>", _enter)
                    w.bind("<Leave>", _leave)
                    w.bind("<Button-1>", _click)
                except Exception:
                    pass
            return wrap, t_label, p_label, avatar
        except Exception as _e:
            try:
                self._debug_log(f"[系统卡] 创建失败 {key!r}: {_e!r}")
            except Exception:
                pass
            return None, None, None, None

        # 启动渲染：按会话索引把每个联系人渲染为独立左栏卡片。
        # 修复回归：此前 _load_history 只建 _conv_index 却漏调 _rebuild_contact_list，
        # 导致启动后左栏只剩“全部会话”，历史联系人的独立卡片永不出现。
        try:
            self._rebuild_contact_list()
        except Exception:
            pass

        # V3 P0-1 + P1-1: 搜索框 实时过滤会话卡（按名字/预览匹配）
        try:
            self.contact_search_entry.bind("<KeyRelease>", self._on_contact_search)
           # self.contact_search_entry.bind("<FocusOut>", self._on_contact_search)
        except Exception:
            pass

        # 默认选中"📋 全部会话" + 延迟重建气泡（确保 frame 已布局）。
        # 性能修复：只触发一次重建，避免 300/450ms 多次全量渲染卡死（渲染 300 条实测 4s+）
        self.after(250, lambda: self._set_active_contact(self._contact_filter_all))
        self.after(450, self._rebuild_message_list)
        self.after(450, self._rebuild_contact_list)

    def _load_history(self):
        """启动加载：只构建轻量会话索引（contact -> count/preview/...），不把全量消息体载入内存。

        数据源：
          - messages.db  —— 引擎持久化库（GROUP BY 量级查询，启动极快）。
          - messages_history.json —— 仅用于恢复未读数等 UI 态（不再承载全量正文）。
        真正点开某会话时才按需从 db 懒加载该会话全文（见 _load_conversation）。
        """
        try:
            # 1) 从 db 构建会话索引（权威、最快）
            self._rebuild_conv_index()

            # 2) 恢复未读数等 UI 态（db 无 unread 概念，从 json overlay）
            try:
                if os.path.exists(self._history_path):
                    with open(self._history_path, "r", encoding="utf-8") as f:
                        _raw = json.load(f)
                    if isinstance(_raw, dict) and "__unread__" in _raw:
                        for _c, _u in (_raw["__unread__"] or {}).items():
                            if _c in self._conv_index:
                                self._conv_index[_c]["unread"] = int(_u or 0)
            except Exception:
                pass
            # V3.4 诊断：启动后明确记录索引结果，避免「左栏空」被静默吞掉
            try:
                self._debug_log(
                    f"[会话索引] 构建完成: {len(self._conv_index)} 个会话 | "
                    f"CWD={os.getcwd()}")
            except Exception:
                pass
        except Exception as _e:
            try:
                self._debug_log(f"[会话索引] _load_history 异常: {_e!r}")
            except Exception:
                pass

    def _get_storage(self):
        """返回存储对象：优先 engine.storage，否则按配置自建（避免未初始化时删除/查询失效）。"""
        try:
            if getattr(self, "engine", None) and getattr(self.engine, "storage", None):
                return self.engine.storage
        except Exception:
            pass
        if getattr(self, "_storage_cache", None) is None:
            try:
                from storage import MessageStorage
                _sc = (self.config_data or {}).get("storage", {}) or {}
                self._storage_cache = MessageStorage(_sc)
            except Exception as _e:
                try:
                    self._on_log("error", f"[存储] 初始化失败: {_e}")
                except Exception:
                    pass
                return None
        return self._storage_cache

    def _rebuild_conv_index(self):
        """从 messages.db 构建轻量会话索引（一条查询，启动/刷新极快，不载入全量正文）。"""
        self._conv_index = {}
        _st = self._get_storage()
        if _st is None:
            return
        try:
            import sqlite3
            # db_path 由 MessageStorage 在初始化时统一绝对化到项目根（storage._resolve），
            # 无论 CWD 在哪、哪个 storage 实例，都指向同一物理文件 → 消除「写入 A / 读 B」。
            _db = getattr(_st, "db_path", _app_path("data", "messages.db"))
            if not os.path.isabs(_db):
                _db = _app_path(_db)
            self._debug_log(f"[会话索引] db_path={_db} exists={os.path.exists(_db)}")
            if not os.path.exists(_db):
                return
            _conn = sqlite3.connect(_db)
            _conn.row_factory = sqlite3.Row
            _rows = _conn.execute(
                "SELECT contact, sender, raw_text, timestamp, is_important "
                "FROM messages ORDER BY timestamp ASC"
            ).fetchall()
            _conn.close()

            _tool = ["助手v2.0", "AI助手", "微信 AI", "信息提取", "自动回复",
                     "数据查看", "设置", "红点", "屏幕外", "保活", "截图",
                     "预览", "诊断", "增量", "窗口坐标"]
            _status = ["消息：", "提取：", "重要：", "回复：", "引擎已初始化"]
            import re as _re
            for _r in _rows:
                _contact = (_r["contact"] or "").strip()
                _content = (_r["raw_text"] or "").strip()
                _sender = _r["sender"] or "other"
                if not _content:
                    continue
                if any(_mk in _contact for _mk in _tool):
                    continue
                if _re.search(r'(昨天|今天|明天|前天|星期[一二三四五六日天]|周[一二三四五六日天])', _contact) \
                        or _re.match(r'^\d{1,2}[:：]\d{2}$', _contact) \
                        or _re.match(r'^\d+$', _contact):
                    _contact = "未命名会话"
                if any(_sm in _content for _sm in _status):
                    continue
                if _sender == "other":
                    if len(_content) < 2:
                        continue
                    if _re.match(r'^\d{1,2}[:：]\d{2}$', _content):
                        continue
                    if _re.match(r'^\d+$', _content):
                        continue
                if not _contact:
                    _contact = "未命名会话"
                _idx = self._conv_index.setdefault(_contact, {
                    "count": 0, "preview": "", "last_sender": _sender,
                    "last_time": _r["timestamp"] or "", "unread": 0, "is_group": False,
                })
                _idx["count"] += 1
                _idx["preview"] = _content
                _idx["last_sender"] = _sender
                _idx["last_time"] = _r["timestamp"] or _idx["last_time"]
            # 诊断日志：记录前5个联系人
            try:
                _sample = list(self._conv_index.keys())[:5]
                self._debug_log(f"[会话索引] 构建完成: {len(self._conv_index)} 个联系人 | 前5: {_sample}")
            except Exception:
                pass
        except Exception as _e:
            try:
                self._on_log("warning", f"[会话索引] 构建失败: {_e}")
            except Exception:
                pass

    def _load_conversation(self, contact):
        """懒加载某联系人完整消息体（精确匹配，从 db），缓存到 _messages_store[contact]。"""
        if contact in self._messages_store:
            return self._messages_store[contact]
        _st = self._get_storage()
        _msgs = []
        if _st is not None:
            try:
                import sqlite3
                import os as _os
                _db = getattr(_st, "db_path", _app_path("data", "messages.db"))
                if not _os.path.isabs(_db):
                    _db = _app_path(_db)
                self._debug_log(f"[懒加载] 联系人 {contact!r}: db_path={_db}")
                _conn = sqlite3.connect(_db)
                _conn.row_factory = sqlite3.Row
                _rows = _conn.execute(
                    "SELECT sender, raw_text, timestamp, is_important FROM messages "
                    "WHERE contact = ? ORDER BY timestamp ASC", (contact,)
                ).fetchall()
                _conn.close()
                for _r in _rows:
                    _msgs.append({
                        "contact": contact,
                        "sender": _r["sender"] or "other",
                        "content": _r["raw_text"] or "",
                        "timestamp": _r["timestamp"] or "",
                        "is_important": bool(_r["is_important"]),
                        "is_group": False,
                        "_seq": 0,
                    })
            except Exception:
                pass
        for i, _m in enumerate(_msgs):
            _m["_seq"] = i + 1
        self._msg_seq = max(self._msg_seq, len(_msgs))
        _msgs.reverse()  # 最新在前（与 _add_message_card_impl 的 insert(0) 一致）
        self._messages_store[contact] = _msgs
        _ci = self._msg_index.setdefault(contact, {})
        for _idx, _m in enumerate(_msgs):
            _k = _m.get("msg_key")
            if _k:
                _ci[_k] = _idx
        return _msgs

    def _get_all_view_messages(self):
        """全部会话视图：从 db 取最近 _MAX 条（一次查询），合并本会话实时新增。"""
        _st = self._get_storage()
        _base = []
        if _st is not None:
            try:
                _rows = _st.query(contact=None, limit=100)
                for _r in _rows:
                    _base.append({
                        "contact": _r.get("contact") or "?",
                        "sender": _r.get("sender", "other"),
                        "content": _r.get("raw_text", ""),
                        "timestamp": _r.get("timestamp", ""),
                        "is_important": bool(_r.get("is_important", False)),
                        "is_group": False,
                        "_seq": 0,
                    })
            except Exception:
                pass
        _live = getattr(self, "_all_messages_live", []) or []
        _merged = _base + _live
        _seen = set()
        _out = []
        for _m in _merged:
            _key = (_m.get("contact"), _m.get("content"), _m.get("timestamp"))
            if _key in _seen:
                continue
            _seen.add(_key)
            _out.append(_m)
        # 按时间倒序（新→旧，最新在最前）；混合格式时间戳退化为插入序
        try:
            _out.sort(key=lambda x: str(x.get("timestamp", "")), reverse=True)
        except Exception:
            pass
        return _out

    def _refresh_contacts_btn_click(self):
        """手动刷新联系人列表：从 DB 重建会话索引，然后重建左栏卡片。"""
        try:
            self._rebuild_conv_index()
            self._rebuild_contact_list()
            self._on_log("info", f"[刷新] 已重建联系人列表 ({len(self._conv_index)} 个会话)")
        except Exception as _e:
            self._on_log("error", f"[刷新] 联系人列表重建失败: {_e}")

    def _rebuild_contact_list(self):
        print("[DBG-RCL] CALLED")
        """重建左侧会话卡片（两张系统卡：📖使用说明 / 📋全部会话 永远保留，且位于最顶）。
        Ctrl+D / 删除历史 / 刷新 等场景调用。

        V3.3 根治：不依赖 CTkScrollableFrame.winfo_children()（它返回的是内部
        _parent_frame，会导致 str() 比对误删）。改用显式 frame 集合 + 对象身份判断：
          - self._system_card_frames: 系统卡 frame（永远保留、不销毁）
          - self._contact_card_frames: 联系人卡 frame（rebuild 时精确销毁重建）
        """
        try:
            if not hasattr(self, "contact_list_frame") or not self.contact_list_frame.winfo_exists():
                print("[DBG-RCL] early return: contact_list_frame not exists")
                return
            print(f"[DBG-RCL] enter, conv_index={len(getattr(self,'_conv_index',{}))}, card_frames={len(getattr(self,'_contact_card_frames',set()))}")

            # 兜底：若会话索引为空（例如 db 尚未就绪 / 上次启动异常），
            # 先重建索引再渲染，避免「重启后左栏只剩系统卡」被静默吞掉。
            if not getattr(self, "_conv_index", None):
                try:
                    self._rebuild_conv_index()
                except Exception:
                    pass

            # 1) 精确销毁「旧的联系人卡」（只动 _contact_card_frames，绝不碰系统卡）
            dead = [f for f in self._contact_card_frames
                    if f is not None and f.winfo_exists()]
            for f in dead:
                try:
                    f.destroy()
                except Exception:
                    pass
            self._contact_card_frames.clear()

            # 2) 清理 _contact_cards，仅保留两张系统卡
            keep = {k: v for k, v in self._contact_cards.items()
                    if v.get("_is_system")}
            self._contact_cards = keep

            # 3) 从会话索引重建所有会话卡（索引里已是“最新消息预览”，无需载入正文）
            active = getattr(self, "_active_contact", None)
            for contact, idx in (self._conv_index or {}).items():
                preview = str(idx.get("preview", ""))[:26]
                is_group = bool(idx.get("is_group", False))
                unread = int(idx.get("unread", 0) or 0)
                try:
                    self._append_contact_card(
                        contact, preview, is_group=is_group, unread=unread,
                        active=(contact == active))
                except Exception as _dbg_e:
                    import traceback as _tb
                    print(f"[DBG-REBUILD] 建卡失败 {contact!r}: {_tb.format_exc()}")

            # 4) 显式重排顺序（根治 CTkScrollableFrame 的 pack 顺序不可靠）：
            #    [使用说明, 全部会话, *联系人卡] 从上到下
            ordered = []
            uf = self._contact_cards.get(self._contact_filter_usage, {}).get("frame")
            af = self._contact_cards.get(self._contact_filter_all, {}).get("frame")
            if uf is not None and uf.winfo_exists():
                ordered.append(uf)
            if af is not None and af.winfo_exists():
                ordered.append(af)
            # 联系人卡：按 _conv_index 顺序（已是最新在前），保证和预览一致
            for contact in (self._conv_index or {}):
                info = self._contact_cards.get(contact)
                if info and info.get("frame") is not None and info["frame"].winfo_exists():
                    ordered.append(info["frame"])
            for f in ordered:
                try:
                    f.pack_forget()
                    f.pack(side="top", fill="x")
                except Exception:
                    pass

            # 强制刷新 CTkScrollableFrame 的 canvas scrollregion，确保新卡进入可视区
            try:
                self.contact_list_frame.update_idletasks()
                _cv = getattr(self.contact_list_frame, "_parent_canvas", None)
                if _cv is not None:
                    _cv.configure(scrollregion=_cv.bbox("all"))
            except Exception:
                pass
        except Exception as e:
            try: self._append_log("warning", f"[会话列表] 重建失败: {e}")
            except Exception: pass

    def _schedule_history_save(self):
        """防抖写盘：800ms 内合并多次写入"""
        self._history_dirty = True
        if self._history_save_job is not None:
            try:
                self.after_cancel(self._history_save_job)
            except Exception:
                pass
        self._history_save_job = self.after(800, self._flush_history)

    def _flush_history(self):
        """写盘：只持久化轻量会话索引 + 未读数（全量正文始终以 messages.db 为权威源）。"""
        self._history_save_job = None
        if not self._history_dirty:
            return
        self._history_dirty = False
        try:
            _payload = {
                "__unread__": {c: int(v.get("unread", 0) or 0)
                               for c, v in (self._conv_index or {}).items()}
            }
            with open(self._history_path, "w", encoding="utf-8") as f:
                json.dump(_payload, f, ensure_ascii=False, indent=1)
        except Exception:
            pass

    def _append_contact_card(self, contact, preview_text, is_group=False,
                              unread=0, active=False):
        """V3: 新建或更新一个"会话卡片"（微信PC会话卡 #EBEBEB 悬停/选中变灰）
        美化：圆形头像首字母 + 时间显示 + 在线状态点
        """
        import tkinter as tk
        import hashlib
        from datetime import datetime

        if contact in self._contact_cards:
            info = self._contact_cards[contact]
            info["preview"].configure(text=preview_text[:26])
            info["is_group"] = is_group
            info["unread"] += unread
            un = info["unread"]
            if un > 0:
                info["badge"].configure(text=str(un) if un < 99 else "99+")
                info["badge"].pack(side="right", padx=(0, 6))
                info["badge"].lift()
            else:
                info["badge"].pack_forget()
            # 更新时间
            if info.get("time_label"):
                info["time_label"].configure(
                    text=datetime.now().strftime("%H:%M"))
            # 更新侧边栏未读角标
            try:
                total_unread = sum(
                    v.get("unread", 0) for v in self._contact_cards.values()
                    if not v.get("_is_system_all"))
                badge = self._nav_badges.get("monitor")
                if badge:
                    if total_unread > 0:
                        badge.configure(
                            text=str(total_unread) if total_unread < 99 else "99+")
                        badge.place(relx=0.78, rely=0.12, anchor="ne")
                        badge.lift()
                    else:
                        badge.place_forget()
            except Exception:
                pass
            if active:
                self._set_active_contact(contact)
            return info["frame"]

        # 新卡片（微信PC：60px 高，悬停浅灰，选中深灰，底部 1px 分割线）
        # CTkScrollableFrame 标准用法：直接 pack 进 CTkScrollableFrame 本身，
        # CTk 会自动把子件重定向到内部滚动内容区（_parent_frame）。
        # 使用 CTkFrame 而非 tk.Frame，确保 CTkScrollableFrame 正确重定向到 _parent_frame，
        # 避免重启后卡片被 canvas 遮挡而不可见。
        card = ctk.CTkFrame(self.contact_list_frame, fg_color=WC_COLORS["card_hover"], height=60,
                            border_width=0, corner_radius=0, width=218)
        card.pack(fill="x", side="top")
        card.pack_propagate(False)
        # V3.3: 登记到联系人卡集合（rebuild 时精确销毁，不依赖 winfo_children）
        self._contact_card_frames.add(card)
        try:
            sep = tk.Frame(card, bg=WC_COLORS["divider"], height=1)
            sep.pack(side="bottom", fill="x")
        except Exception:
            pass

        # 圆形头像（首字母/群图标，固定颜色）
        colors = WC_COLORS.get("avatar_colors",
            ["#007AFF", "#34C759", "#FF3B30", "#FF9500", "#AF52DE"])
        idx = int(hashlib.md5(contact.encode()).hexdigest(), 16) % len(colors)
        avatar_bg = colors[idx]
        first_char = contact[0] if contact else "?"
        avatar_char = "👥" if is_group else first_char
        avatar = ctk.CTkLabel(card, text=avatar_char, width=40, height=40,
                              corner_radius=20,
                              fg_color=avatar_bg, text_color="#FFFFFF",
                              font=ctk.CTkFont(size=16, weight="bold"))
        avatar.pack(side="left", padx=(12, 10), pady=10)

        # 文字区
        text_wrap = tk.Frame(card, bg=WC_COLORS["card_hover"])
        text_wrap.pack(side="left", fill="both", expand=True, pady=8)

        header_row = tk.Frame(text_wrap, bg=WC_COLORS["card_hover"])
        header_row.pack(fill="x")

        title_lbl = tk.Label(header_row, text=contact, bg=WC_COLORS["card_hover"],
                             fg=WC_COLORS["text"],
                             font=ctk.CTkFont(family=FONT_FAMILY, weight="bold"), anchor="w")
        title_lbl.pack(side="left")

        # 时间标签（右侧，灰色小字）
        time_label = tk.Label(header_row, text=datetime.now().strftime("%H:%M"),
                              bg=WC_COLORS["card_hover"], fg=WC_COLORS["time_badge"],
                              font=ctk.CTkFont(family=FONT_FAMILY), anchor="e")
        time_label.pack(side="right", padx=(0, 8))

        # 未读红点 Badge（微信PC 红色小圆标，初始pack_forget）
        badge = tk.Label(header_row, text="1", bg=WC_COLORS["danger"],
                         fg=WC_COLORS["text"],
                         font=ctk.CTkFont(family=FONT_FAMILY, weight="bold"),
                         padx=6, pady=1, borderwidth=0)

        preview = tk.Label(text_wrap, text=preview_text[:26], bg=WC_COLORS["card_hover"],
                           fg=WC_COLORS["text_muted2"],
                           font=ctk.CTkFont(family=FONT_FAMILY), anchor="w")
        preview.pack(fill="x", side="top")

        def _on_enter(_e):
            card.configure(fg_color=WC_COLORS["card_hover"])
            for w in (text_wrap, header_row):
                w.configure(bg=WC_COLORS["card_hover"])
            title_lbl.configure(bg=WC_COLORS["card_hover"])
            preview.configure(bg=WC_COLORS["card_hover"])

        def _on_leave(_e):
            bg_new = WC_COLORS["card_active"] if self._active_contact == contact else WC_COLORS["card_hover"]
            card.configure(fg_color=bg_new)
            for w in (text_wrap, header_row):
                w.configure(bg=bg_new)
            title_lbl.configure(bg=bg_new)
            preview.configure(bg=bg_new)

        def _on_click(_e):
            self._set_active_contact(contact)

        for w in (card, avatar, text_wrap, header_row, title_lbl, preview):
            w.bind("<Enter>", _on_enter)
            w.bind("<Leave>", _on_leave)
            w.bind("<Button-1>", _on_click)

        # V3 P2-2: 会话卡右键菜单（📤 导出会话为 .md + 📌 设为当前会话）
        _ctx_menu = None
        try:
            import tkinter as tk
            _ctx_menu = tk.Menu(self, tearoff=0)
            _ctx_menu.add_command(label="📌 设为当前会话（查看气泡）",
                                   command=lambda c=contact: self._set_active_contact(c))
            _ctx_menu.add_separator()
            _ctx_menu.add_command(label="📤 导出该会话为 Markdown (.md)",
                                   command=lambda c=contact: self._export_contact_md(c))
            _ctx_menu.add_command(label="🗑 删除该会话所有记录…",
                                   command=lambda c=contact: self._delete_contact_records(c))

            def _on_right_click(e, __m=_ctx_menu):
                try:
                    __m.tk_popup(e.x_root, e.y_root)
                finally:
                    try:
                        __m.grab_release()
                    except Exception:
                        pass

            for w in (card, avatar, text_wrap, header_row, title_lbl, preview):
                try:
                    w.bind("<Button-3>", _on_right_click)        # Windows 右键
                    w.bind("<Control-Button-1>", _on_right_click) # macOS 兼容
                except Exception:
                    pass
        except Exception:
            _ctx_menu = None

        self._contact_cards[contact] = {
            "frame": card, "title": title_lbl, "preview": preview,
            "badge": badge, "is_group": is_group, "unread": unread,
            "avatar": avatar, "time_label": time_label,
            "_menu_ref": _ctx_menu,
        }
        info = self._contact_cards[contact]

        if active:
            self._set_active_contact(contact)
        else:
            if unread > 0:
                badge.configure(text=str(unread) if unread < 99 else "99+")
                badge.pack(side="right", padx=(0, 8))
        return card

    def _set_active_contact(self, contact):
        """V3 P0-1: 点击会话卡 → 按选中会话重建中栏气泡（支持系统📋卡=全部会话）"""
        self._active_contact = contact
        # 离开“全部会话”视图时清空实时缓存，下次进入从 db 重载（防重复）
        if contact != self._contact_filter_all:
            self._all_messages_live = []
        # 切换会话 → 重建消息列表（最新在顶）
        try:
            self._rebuild_message_list()
        except Exception as _e:
            try:
                import traceback as _tb
                self._debug_log(f"[切换会话] {contact!r} 重建异常: {_tb.format_exc()}")
            except Exception:
                pass

        # 点击卡片“直接跳到消息”：无论当前在日志/预览哪个 tab，都切回消息页
        try:
            if getattr(self, "view_tabview", None) is not None:
                self.view_tabview.set("💬 消息")
        except Exception:
            pass

        # 点击卡片“直接跳到消息”：强制刷新布局 + 立即滚到最新（底部），
        # 不依赖 after 异步，消除切换时序窗口导致的“点开看不见/空白”。
        try:
            self.msg_list_frame.update_idletasks()
            _cv = getattr(self.msg_list_frame, "_parent_canvas", None)
            if _cv is not None:
                _cv.configure(scrollregion=_cv.bbox("all"))
                _cv.yview_moveto(1.0)
        except Exception:
            pass

        # 刷新每张卡的背景色 + badge清零
        for name, info in self._contact_cards.items():
            is_active = (name == contact)
            bg = WC_COLORS["card_active"] if is_active else WC_COLORS["card_hover"]

            # 1. frame 层（tk.Frame 或 CTkFrame）
            frame_widget = info.get("frame")
            try:
                if hasattr(frame_widget, "configure"):
                    if isinstance(frame_widget, ctk.CTkFrame):
                        frame_widget.configure(fg_color=bg)
                    else:
                        frame_widget.configure(bg=bg)
            except Exception:
                pass

            # 2. title / preview 层
            title_w = info.get("title")
            preview_w = info.get("preview")
            try:
                if isinstance(title_w, ctk.CTkLabel):
                    # 系统卡 CTkLabel：parent 也是 CTkFrame
                    title_w.configure(fg_color=bg if bg != WC_COLORS["card_hover"] else "transparent")
                    # 同步文字区 master fg（CTkFrame）
                    if hasattr(title_w, "master") and isinstance(title_w.master, ctk.CTkFrame):
                        try:
                            title_w.master.configure(fg_color=bg)
                        except Exception:
                            pass
                elif title_w is not None:
                    # tk.Label：改 bg
                    title_w.configure(bg=bg)
                    if hasattr(title_w, "master") and title_w.master is not None:
                        try:
                            title_w.master.configure(bg=bg)
                            if hasattr(title_w.master, "master") and title_w.master.master is not None and title_w.master.master is not frame_widget:
                                title_w.master.master.configure(bg=bg)
                        except Exception:
                            pass
            except Exception:
                pass

            try:
                if isinstance(preview_w, ctk.CTkLabel):
                    preview_w.configure(fg_color=bg if bg != WC_COLORS["card_hover"] else "transparent")
                elif preview_w is not None:
                    preview_w.configure(bg=bg)
            except Exception:
                pass

            if is_active and info.get("badge") is not None:
                info["unread"] = 0
                try:
                    info["badge"].pack_forget()
                except Exception:
                    pass

        # 顶部 chat_title 同步（区分系统卡 / 群聊 / 私聊）
        try:
            is_system_all = (contact == self._contact_filter_all)
            is_usage = (contact == self._contact_filter_usage)
            if is_usage:
                self.chat_title.configure(text="📖 使用说明")
                if hasattr(self, "top_current_label"):
                    self.top_current_label.configure(text="当前：📖 使用说明（聊天式操作指南）")
            elif is_system_all:
                self.chat_title.configure(text="📋 全部会话视图")
                if hasattr(self, "top_current_label"):
                    self.top_current_label.configure(text="当前：📋 全部会话（默认混合视图）")
            else:
                info = self._contact_cards.get(contact, {})
                is_group = bool(info.get("is_group"))
                prefix = "👥" if is_group else "💬"
                self.chat_title.configure(text=f"{prefix} {contact}")
                if hasattr(self, "top_current_label"):
                    self.top_current_label.configure(
                        text=f"当前会话：{contact} {'（群聊）' if is_group else ''}")
        except Exception:
            pass

    # ==============================================================
    # V3 P1-1: 左栏搜索框 — 实时过滤会话卡（按名字/预览匹配）
    # ==============================================================
    def _on_contact_search(self, event=None):
        q = ""
        try:
            q = str(self.contact_search_entry.get() or "").strip().lower()
        except Exception:
            q = ""
        # 系统卡"全部会话"永远显示
        try:
            sys_info = self._contact_cards.get(self._contact_filter_all, {})
            if sys_info:
                sys_info.get("frame").pack(side="top", fill="x")
        except Exception:
            pass
        for name, info in self._contact_cards.items():
            if name == self._contact_filter_all:
                continue
            if not q:
                try:
                    info.get("frame").pack(side="top", fill="x")
                except Exception:
                    pass
                continue
            hit = (q in str(name).lower())
            if not hit:
                try:
                    hit = q in str(info.get("preview").cget("text") or "").lower() if hasattr(info.get("preview"), "cget") else False
                except Exception:
                    hit = False
            try:
                if hit:
                    info.get("frame").pack(side="top", fill="x")
                else:
                    info.get("frame").pack_forget()
            except Exception:
                pass

    # ==============================================================
    # V3 P2-2: 会话卡右键 → 📤 导出该会话为 Markdown（Obsidian 微信风格）
    # ==============================================================
    def _export_contact_md(self, contact):
        """把该会话历史消息（优先 storage.query，回退 _messages_store）写成 Obsidian .md 文件"""
        import tkinter as tk
        from tkinter import filedialog

        try:
            # 1) 拉取该会话的消息列表（最新在前）
            messages = []
            try:
                if self.engine and self.engine.storage:
                    rows = self.engine.storage.query(
                        contact=None if contact == self._contact_filter_all else contact,
                        limit=999999,
                    )
                    # storage 返回的字段名与 UI 消息池字段对齐
                    for r in rows:
                        messages.append({
                            "contact": r.get("contact") or contact,
                            "sender": r.get("sender", "other"),
                            "content": r.get("raw_text", ""),
                            "timestamp": r.get("timestamp", ""),
                            "matched_keywords": r.get("matched_keywords") or [],
                            "keywords": r.get("matched_keywords") or [],
                            "regex_extracts": r.get("regex_extracts") or {},
                            "extracted_fields": {},
                            "is_important": bool(r.get("is_important", False)),
                            "importance_reason": r.get("importance_reason") or "",
                            "llm_analysis": r.get("llm_analysis") or {},
                            "summary": ((r.get("llm_analysis") or {}).get("summary"))
                                       if isinstance(r.get("llm_analysis"), dict) else "",
                            "is_group": False,
                            "group_member": None,
                        })
            except Exception:
                pass

            # 2) UI 内存消息池合并（因为storage可能还没及时保存）
            if contact == self._contact_filter_all:
                extra = []
                for msgs in (self._messages_store or {}).values():
                    extra.extend(msgs)
                messages.extend(extra)
            else:
                msgs_from_cache = (self._messages_store or {}).get(contact, [])
                if msgs_from_cache:
                    messages.extend(msgs_from_cache)

            if not messages:
                messagebox.showinfo("导出", f"「{contact}」无历史可导出")
                return

            # 3) 用 ObsidianSync._format_contact_note 渲染（复用统一格式）
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from obsidian_sync import ObsidianSync
            renderer = ObsidianSync({"mode": "file", "vault_path": "",
                                      "folder": "", "auto_sync": False})
            if contact == self._contact_filter_all:
                # "全部会话" → 用每日笔记格式渲染
                from datetime import datetime as _dt
                md = renderer._format_daily_note(messages,
                                                   date_str=_dt.now().strftime("%Y-%m-%d"))
            else:
                md = renderer._format_contact_note(contact, messages)

            # 4) 文件保存对话框
            safe_name = "".join(c for c in str(contact) if c.isalnum() or c in "_-()[] ").strip() or "导出"
            default_name = f"{safe_name}_聊天记录_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
            default_dir = _app_path("exports")
            os.makedirs(default_dir, exist_ok=True)
            path = filedialog.asksaveasfilename(
                title=f"导出「{contact}」聊天记录",
                initialdir=default_dir,
                initialfile=default_name,
                defaultextension=".md",
                filetypes=[("Markdown 笔记", "*.md"), ("所有文件", "*.*")],
            )
            if not path:
                return
            with open(path, "w", encoding="utf-8") as f:
                f.write(md)

            # 5) 提示 + 可选打开所在文件夹
            try:
                msg = f"导出成功：\n{path}\n共 {len(messages)} 条消息"
                if messagebox.askyesno("导出成功", msg + "\n\n是否打开所在文件夹？"):
                    try:
                        import subprocess
                        subprocess.Popen(f'explorer /select,"{os.path.abspath(path)}"')
                    except Exception:
                        pass
                else:
                    messagebox.showinfo("导出成功", msg)
            except Exception:
                messagebox.showinfo("导出成功", f"已保存 {path}")
        except Exception as e:
            messagebox.showerror("导出失败", str(e))

    def _delete_contact_records(self, contact):
        """删除指定会话的历史消息（增量刷新，不做全量重建）"""
        self._batch_delete_contacts([contact])

    def _batch_delete_contacts(self, contacts):
        """批量删除多个联系人的消息（单次全量重建，只执行一次）"""
        if not contacts:
            return
        contacts = [c for c in contacts
                    if c and c != self._contact_filter_all
                    and c != self._contact_filter_usage]  # V3.4 双保险：绝不许删系统卡
        if not contacts:
            return

        from tkinter import messagebox as _mb
        if len(contacts) == 1:
            msg = f"确定要删除与「{contacts[0]}」的全部聊天记录吗？\n\n此操作不可撤销。"
        else:
            msg = f"确定要删除以下 {len(contacts)} 个会话的全部聊天记录吗？\n\n{', '.join(contacts[:10])}{'...' if len(contacts) > 10 else ''}\n\n此操作不可撤销。"
        if not _mb.askyesno("删除历史记录", msg):
            return

        total_removed = 0
        # 关键修复：未初始化引擎（未点“开始监控”）时 engine.storage 为 None，
        # 改用 _get_storage() 自建存储对象，否则 clear_all/delete_contact 被跳过 → 删了库里还在。
        storage = self._get_storage()

        obsidian_delete = self.config_data.get("obsidian", {}).get("delete_link", False)

        for contact in contacts:
            # V3.4 双保险：绝不允许删除两张系统卡（全部会话 / 使用说明）
            if contact in (self._contact_filter_all, self._contact_filter_usage):
                continue
            removed = 0
            if storage:
                try:
                    n = storage.delete_contact(contact)
                    if n:
                        removed += int(n)
                except Exception as e:
                    self._on_log("error", f"[删除] 数据库删除失败: {contact}: {e}")
                # “未命名会话”是索引里把空联系人/日期伪联系人改名而来，
                # 其原始 db 名（'' / '昨天11' 等）与卡片名不一致，需单独清这些“伪联系人”行
                if contact == "未命名会话":
                    try:
                        on = storage.delete_orphan_contacts()
                        if on:
                            removed += int(on)
                    except Exception as e:
                        self._on_log("error", f"[删除] 伪联系人清理失败: {e}")

            if contact in self._messages_store:
                removed += len(self._messages_store[contact])
                del self._messages_store[contact]
            # 同步清轻量索引 / 实时列表，避免左栏残留与重启复活
            self._conv_index.pop(contact, None)
            if hasattr(self, "_all_messages_live"):
                self._all_messages_live = [
                    m for m in self._all_messages_live
                    if m.get("contact") != contact
                ]

            if obsidian_delete:
                try:
                    from obsidian_sync import ObsidianSync
                    sync = ObsidianSync(self.config_data.get("obsidian", {}))
                    sync.delete_contact_note(contact)
                except Exception:
                    pass

            card = self._contact_cards.pop(contact, None)
            if card:
                frame_w = card.get("frame")
                if frame_w:
                    try:
                        if frame_w.winfo_exists():
                            frame_w.destroy()
                        self._contact_card_frames.discard(frame_w)
                    except Exception:
                        pass
                menu_ref = card.get("_menu_ref")
                if menu_ref:
                    try:
                        menu_ref.destroy()
                    except Exception:
                        pass

            if self._active_contact == contact:
                self._active_contact = None

            total_removed += removed

        # 单次全量重建（批量只重建一次）
        try:
            self._rebuild_message_list()
        except Exception:
            pass
        # 标记脏数据，确保 _flush_history 真正写盘
        self._history_dirty = True
        try:
            self._flush_history()
        except Exception:
            pass

        # ★ 同步清引擎的跨轮去重缓存：否则被删消息的 hash 仍留在 reddot_seen.json，
        # 下次重扫仍判“已见过”→ 永久跨轮去重回不来（用户删历史的目的就是让它们重新出现）。
        eng = getattr(self, "engine", None)
        if eng is not None:
            try:
                eng.clear_reddot_seen(contacts)
            except Exception as e:
                self._on_log("warning", f"[删除] 清空去重缓存失败: {e}")

        # ★ 同步清UI去重索引和自动回复指纹，否则删了聊天记录后重新识别
        #   仍会被判为"已处理/已回复"而跳过
        for contact in contacts:
            self._msg_index.pop(contact, None)
            # 清理 _recent_ui_keys 中属于该联系人的条目（key格式: contact|sender|content）
            _keys_to_del = [k for k in getattr(self, "_recent_ui_keys", {}) or {}
                           if k.startswith(contact + "|")]
            for k in _keys_to_del:
                self._recent_ui_keys.pop(k, None)
        if eng is not None:
            try:
                eng.clear_reply_fingerprints(contacts)
            except Exception as e:
                self._on_log("warning", f"[删除] 清空回复指纹失败: {e}")

        self._on_log("info", f"[删除] 已删除 {len(contacts)} 个会话共 {total_removed} 条消息")

    def _delete_all_contacts(self):
        """删除所有会话历史记录"""
        non_system = [c for c in self._contact_cards
                      if c != self._contact_filter_all
                      and c != self._contact_filter_usage]  # V3.4 绝不许删系统卡（含使用说明）
        if not non_system:
            from tkinter import messagebox as _mb
            _mb.showinfo("提示", "暂无历史记录可删除")
            return
        from tkinter import messagebox as _mb
        if not _mb.askyesno(
                "删除全部历史",
                f"确定要删除全部 {len(non_system)} 个会话的所有聊天记录吗？\n\n此操作不可撤销！"):
            return
        # 一并清空底层 messages.db（含空联系人/日期伪联系人），
        # 否则重启时 _rebuild_conv_index 会从 db 把已删记录“复活”
        storage = self._get_storage()
        if storage:
            try:
                n = storage.clear_all()
                self._on_log("info", f"[删除] 已同步清空 messages.db（{n} 条）")
            except Exception as e:
                self._on_log("error", f"[删除] 清空数据库失败: {e}")
        self._batch_delete_contacts(non_system)
        # 兜底：清空所有内存态，确保“全部会话”视图与统计归零
        self._conv_index = {}
        self._messages_store = {}
        self._all_messages_live = []
        self._msg_index = {}

    def _toggle_batch_mode(self):
        """切换批量选择模式"""
        self._batch_mode = not self._batch_mode
        if self._batch_mode:
            self._batch_bar.grid(row=2, column=0, sticky="ew", padx=8, pady=(4, 0))
            self._batch_toggle_btn.configure(text="✓ 批量")
            self._refresh_batch_checkboxes()
        else:
            self._batch_bar.grid_remove()
            self._batch_toggle_btn.configure(text="📋 批量")
            self._batch_selected.clear()
            self._refresh_batch_checkboxes()

    def _refresh_batch_checkboxes(self):
        """刷新所有联系人卡的复选框状态"""
        for contact, info in self._contact_cards.items():
            if contact == self._contact_filter_all:
                continue
            cb = info.get("_batch_checkbox")
            if self._batch_mode:
                if cb is None:
                    try:
                        import tkinter as _tk
                        var = _tk.BooleanVar(value=False)
                        cb_widget = _tk.Checkbutton(
                            info.get("frame"), variable=var,
                            bg=WC_COLORS["card_hover"], activebackground=WC_COLORS["card_hover"],
                            highlightthickness=0, bd=0)
                        cb_widget.place(relx=0.0, rely=0.5, anchor="w", x=2)
                        info["_batch_checkbox"] = cb_widget
                        info["_batch_var"] = var
                        var.trace_add("write",
                            lambda *_args, c=contact: self._on_batch_check(c))
                    except Exception:
                        pass
                else:
                    try:
                        cb.place(relx=0.0, rely=0.5, anchor="w", x=2)
                    except Exception:
                        pass
            else:
                if cb is not None:
                    try:
                        cb.place_forget()
                    except Exception:
                        pass

    def _on_batch_check(self, contact):
        """复选框勾选变化回调"""
        info = self._contact_cards.get(contact, {})
        var = info.get("_batch_var")
        if var and var.get():
            self._batch_selected.add(contact)
        else:
            self._batch_selected.discard(contact)

    def _batch_select_all(self):
        """全选所有联系人"""
        import tkinter as _tk
        for contact, info in self._contact_cards.items():
            if contact == self._contact_filter_all:
                continue
            var = info.get("_batch_var")
            if var:
                var.set(True)
        self._batch_selected = set(
            c for c in self._contact_cards
            if c != self._contact_filter_all and c != self._contact_filter_usage)

    def _batch_delete_selected(self):
        """删除勾选的会话"""
        selected = list(self._batch_selected)
        if not selected:
            from tkinter import messagebox as _mb
            _mb.showinfo("提示", "请先勾选要删除的会话")
            return
        self._batch_delete_contacts(selected)
        self._batch_cancel()

    def _batch_cancel(self):
        """取消批量模式"""
        self._batch_selected.clear()
        self._toggle_batch_mode()

    def _prepend_message_row(self, msg_data):
        """增量插入单条气泡到顶部（旧版，保留兼容）"""
        try:
            row = self._build_message_row(self.msg_list_frame_inner, msg_data)
            row.pack_forget()
            slaves = self.msg_list_frame_inner.pack_slaves()
            if slaves:
                row.pack(side="top", fill="x", padx=6, pady=4, before=slaves[0])
            else:
                row.pack(side="top", fill="x", padx=6, pady=4)
            try:
                self.msg_list_frame._parent_canvas.yview_moveto(0.0)
            except Exception:
                pass
        except Exception:
            pass

    def _append_message_row(self, msg_data):
        """增量插入单条气泡到底部（微信风格：最新在最下）"""
        try:
            row = self._build_message_row(self.msg_list_frame_inner, msg_data)
            row.pack(side="top", fill="x", padx=6, pady=4)
            try:
                self.msg_list_frame._parent_canvas.yview_moveto(1.0)
            except Exception:
                pass
        except Exception:
            pass

    def _build_message_row(self, parent, m):
        """构建单条消息气泡行（微信PC风格），返回行容器。供 _rebuild_message_list 复用。"""
        contact = m.get("contact", "未知")
        sender = m.get("sender", "other")
        content = str(m.get("content", ""))
        timestamp = m.get("timestamp", "—")
        is_important = bool(m.get("is_important", False))
        is_self = (sender == "me")
        is_group = bool(m.get("is_group", False))
        group_member = m.get("group_member") or None
        cls = m.get("classification", "") or ""
        pri = m.get("priority", 0) or 0

        row = ctk.CTkFrame(parent, fg_color=WC_COLORS["bg"])

        # 头像 40x40（微信PC风格：正方形圆角2；用户偏好也支持圆）
        avatar_bg = WC_COLORS["avatar_me"] if is_self else WC_COLORS["avatar_other"]
        avatar_text = "我" if is_self else (
            str(group_member)[0] if (group_member and is_group)
            else (str(contact)[0] if contact else "?"))
        avatar = ctk.CTkLabel(
            row, text=avatar_text, width=40, height=40, corner_radius=6,
            fg_color=avatar_bg, text_color="#FFFFFF",
            font=ctk.CTkFont(family="Microsoft YaHei", size=14, weight="bold"),
        )

        # 消息气泡 + 昵称/时间
        text_col = ctk.CTkFrame(row, fg_color="transparent")

        if is_self:
            # 右对齐（微信风格）：头像最右，气泡贴右（靠头像），时间/分类在气泡左侧
            avatar.pack(side="right", padx=(6, 10))
            text_col.pack(side="right", fill="x", expand=True)

            bubble_bg = WC_COLORS["bubble_self"]

            wrap = ctk.CTkFrame(text_col, fg_color="transparent")
            wrap.pack(side="right")      # 先 pack → 最右（贴头像）
            outer_bubble = ctk.CTkFrame(wrap, fg_color=WC_COLORS["bubble_self"], corner_radius=18,
                                        border_width=0)
            outer_bubble.pack(side="right", anchor="e")
            content_lbl = ctk.CTkLabel(
                outer_bubble, text=content[:360] if len(content) <= 360 else content[:360] + "…",
                font=ctk.CTkFont(family="Microsoft YaHei", size=12),
                text_color="#FFFFFF", anchor="w", justify="left",
                wraplength=500,
                padx=14, pady=10,
            )
            content_lbl.pack(anchor="e")

            # 元信息在气泡左侧（side="right" 从右往左依次排：气泡、星标、时间、分类chip）
            if is_important:
                star = ctk.CTkLabel(text_col, text="⭐",
                                    font=ctk.CTkFont(size=11),
                                    text_color=WC_COLORS["danger"], fg_color="transparent")
                star.pack(side="right", padx=(4, 0), pady=(18, 0), anchor="s")

            time_lbl = ctk.CTkLabel(
                text_col, text=timestamp,
                font=ctk.CTkFont(family=FONT_FAMILY, size=9),
                text_color=WC_COLORS["text_muted2"])
            time_lbl.pack(side="right", padx=(6, 0), pady=(20, 0), anchor="s")

            if cls:
                cls_chip = ctk.CTkLabel(
                    text_col, text=f"🗂 {cls}{(' P' + str(int(pri))) if pri else ''}",
                    font=ctk.CTkFont(family=FONT_FAMILY, size=10, weight="bold"),
                    text_color="#FFFFFF", fg_color=WC_COLORS["info"])
                cls_chip.pack(side="right", padx=(6, 0), pady=(20, 0), anchor="s")
        else:
            avatar.pack(side="left", padx=(10, 6))
            text_col.pack(side="left", fill="x", expand=True)

            if is_group and group_member:
                member_lbl = ctk.CTkLabel(
                    text_col, text=str(group_member),
                    font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                    text_color=WC_COLORS["member_name"], anchor="w",
                )
                member_lbl.pack(side="top", anchor="w", padx=(2, 0), pady=(0, 2))

            wrap = ctk.CTkFrame(text_col, fg_color="transparent")
            wrap.pack(side="left", fill="x")

            outer_bubble = ctk.CTkFrame(wrap, fg_color=WC_COLORS["bubble_other"], corner_radius=18,
                                        border_width=0)
            outer_bubble.pack(side="left", anchor="w")
            content_lbl = ctk.CTkLabel(
                outer_bubble, text=content[:360] if len(content) <= 360 else content[:360] + "…",
                font=ctk.CTkFont(family="Microsoft YaHei", size=12),
                text_color=WC_COLORS["text"], anchor="w", justify="left",
                wraplength=500,
                padx=14, pady=10,
            )
            content_lbl.pack(anchor="w")

            time_lbl = ctk.CTkLabel(
                text_col, text=timestamp,
                font=ctk.CTkFont(family=FONT_FAMILY, size=9),
                text_color=WC_COLORS["text_muted2"])
            time_lbl.pack(side="left", padx=(6, 0), pady=(20, 0), anchor="s")

            if cls:
                cls_chip = ctk.CTkLabel(
                    text_col, text=f"🗂 {cls}{(' P' + str(int(pri))) if pri else ''}",
                    font=ctk.CTkFont(family=FONT_FAMILY, size=10, weight="bold"),
                    text_color="#FFFFFF", fg_color=WC_COLORS["info"])
                cls_chip.pack(side="left", padx=(6, 0), pady=(20, 0), anchor="s")

            if is_important:
                star = ctk.CTkLabel(text_col, text="⭐",
                                    font=ctk.CTkFont(size=11),
                                    text_color=WC_COLORS["danger"], fg_color="transparent")
                star.pack(side="left", padx=(4, 0), pady=(18, 0), anchor="s")

        # 顶部对齐（调用方按"最新在前"顺序逐条打包，实现最新置顶）
        row.pack(side="top", fill="x", padx=4, pady=2)
        mk = m.get("msg_key")
        if mk:
            _c = m.get("contact", "")
            self._msg_bubble_cache.setdefault(_c, {})[mk] = row
        return row

    def _build_usage_bubbles(self, parent):
        """使用说明：在「全部会话」视图所有气泡之下，用和联系人聊天**一模一样**的
        消息气泡样式呈现（直接复用 _build_message_row），就像和「使用说明」这个联系人的
        一段聊天记录。点击「查看完整指南」弹出完整说明。"""
        # 分隔标题
        sep = ctk.CTkFrame(parent, fg_color=WC_COLORS["divider"], height=1)
        sep.pack(fill="x", padx=10, pady=(12, 6))
        ctk.CTkLabel(parent, text="📖 使用说明（就像一段聊天记录 · 点下方按钮看完整指南）",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=WC_COLORS["text_muted"]).pack(anchor="w", padx=12, pady=(0, 8))

        # 直接用 _build_message_row 渲染真实气泡（contact=使用说明，对方/我交替）
        usage_msgs = [
            {"contact": "📖使用说明", "sender": "other", "content": "这个软件是干嘛的？",
             "timestamp": "指南", "is_important": False, "is_group": False},
            {"contact": "📖使用说明", "sender": "me",
             "content": "NOYA Chat 通过截图+OCR识别微信聊天，AI自动归档/回复，并同步到 Obsidian 知识库。",
             "timestamp": "指南", "is_important": False, "is_group": False},
            {"contact": "📖使用说明", "sender": "other", "content": "怎么开始用？",
             "timestamp": "指南", "is_important": False, "is_group": False},
            {"contact": "📖使用说明", "sender": "me",
             "content": "左侧点「开始监控」→ 软件自动识别未读消息 → 重要消息标⭐并存档。",
             "timestamp": "指南", "is_important": False, "is_group": False},
            {"contact": "📖使用说明", "sender": "other", "content": "会自动回复吗？",
             "timestamp": "指南", "is_important": False, "is_group": False},
            {"contact": "📖使用说明", "sender": "me",
             "content": "开启自动回复后，AI根据角色设定生成回复，预览确认或自动发送，安全可控。",
             "timestamp": "指南", "is_important": False, "is_group": False},
            {"contact": "📖使用说明", "sender": "other", "content": "数据存在哪？隐私安全吗？",
             "timestamp": "指南", "is_important": False, "is_group": False},
            {"contact": "📖使用说明", "sender": "me",
             "content": "本地 SQLite + Obsidian 笔记，纯本地不联网上传，隐私完全可控。",
             "timestamp": "指南", "is_important": False, "is_group": False},
        ]
        for m in usage_msgs:
            try:
                self._build_message_row(parent, m)
            except Exception:
                pass

        # 末尾「查看完整指南」按钮（右侧我的气泡风格）
        more_row = ctk.CTkFrame(parent, fg_color="transparent")
        more_row.pack(side="top", fill="x", padx=6, pady=(6, 2))
        ctk.CTkButton(more_row, text="📘 查看完整使用指南", width=180, height=32,
                      font=ctk.CTkFont(size=11, weight="bold"),
                      fg_color=WC_COLORS["accent"], hover_color=WC_COLORS["accent_hover"],
                      corner_radius=10, command=self._show_usage_dialog).pack(side="right", padx=6)

    def _show_usage_dialog(self):
        """V3.4: 使用说明弹窗（覆盖核心操作流程）。"""
        dlg = ctk.CTkToplevel(self)
        dlg.title("NOYA Chat 使用说明")
        dlg.geometry("560x640")
        dlg.transient(self)
        dlg.grab_set()
        dlg.focus_force()
        try:
            dlg.after(50, lambda: dlg.iconify() or dlg.deiconify())
        except Exception:
            pass

        scroll = ctk.CTkScrollableFrame(dlg, fg_color=WC_COLORS["bg"])
        scroll.pack(fill="both", expand=True, padx=12, pady=12)

        def _title(t):
            ctk.CTkLabel(scroll, text=t, font=ctk.CTkFont(family=FONT_FAMILY, size=15, weight="bold"),
                         text_color=WC_COLORS["accent"]).pack(anchor="w", pady=(10, 4))

        def _line(t):
            ctk.CTkLabel(scroll, text=t, font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                         text_color=WC_COLORS["text"], wraplength=500,
                         justify="left", anchor="w").pack(anchor="w", pady=1)

        ctk.CTkLabel(scroll, text="🤖 NOYA Chat 微信助手 · 使用说明",
                     font=ctk.CTkFont(size=18, weight="bold"),
                     text_color=WC_COLORS["text"]).pack(anchor="w", pady=(4, 10))

        _title("1. 启动监控")
        _line("· 进入「📡 监控」页，点击「▶ 开始监控」按钮。")
        _line("· 微信需登录并保持可见（最小化到托盘时程序会自动把窗口移到屏幕外渲染，不影响使用）。")
        _line("· 支持三种模式：仅当前窗口 / 红点监控（自动切未读）/ 轮询扫描，在「⚙️ 设置」选择。")

        _title("2. 识别与归档")
        _line("· 程序通过截图 + PaddleOCR 实时识别聊天文字，自动区分自己/对方气泡。")
        _line("· 全部聊天数据自动存入本地 SQLite，并同步到 Obsidian 知识库（需配置 vault 路径）。")
        _line("· 重要消息、待办、情绪、分类自动抽取，并打 ⭐ 标记。")

        _title("3. 自动回复")
        _line("· 「💬 自动回复」页开启「启用自动回复」；建议同时开启「预览确认模式」。")
        _line("· 预览模式下 AI 回复会粘贴到微信输入框，需你手动点发送，规避误发风险。")
        _line("· 支持多角色（技术顾问/商务/好友/段子手），可在设置按联系人独立设定。")

        _title("4. AI 赋能（Obsidian）")
        _line("· AI 会话摘要 / 联系人画像 / 关系图谱 / 项目笔记：开启 LLM API Key 后自动生成。")
        _line("· 每日简报：程序退出时自动汇总当日要事，写入「每日简报/<日期>.md」。")
        _line("· 社交图谱 .canvas 文件模式即可用，无需安装插件。")

        _title("5. 数据统计")
        _line("· 「📊 数据」页点「🔄 刷新统计」查看多维度数据 + 可视化图表（无需启动监控也能看历史）。")
        _line("· 支持「今日报告」「周报」一键生成。")

        _title("6. 快捷键")
        _line("· Ctrl+T：全局启停监控（任意窗口可用）。")
        _line("· Ctrl+F：聚焦搜索；Ctrl+I：标记重要；Ctrl+,：打开设置。")

        _title("7. 提示")
        _line("· 全部为本地处理，数据不上传第三方。")
        _line("· 自动发送存在账号风控风险，默认关闭，优先用预览模式。")

        _title("8. v3.4 新功能")
        _line("· 语义向量去重：自动理解消息语义，避免相似内容重复处理。")
        _line("· RAG 知识库检索：AI 回复时自动关联历史相关消息，回答更精准。")
        _line("· 情感分析 + 紧急度分级：自动识别紧急消息，优先处理。")
        _line("· CNN 红点分类器：更准确识别未读红点，减少误触。")
        _line("· UIA 无渲染监控：最小化时通过系统 API 零延迟检测未读。")
        _line("· 自适应点击：像素级验证 + 智能偏移，点击准确率大幅提升。")
        _line("· 更多见 GitHub README 与「关于」页面。")

        ctk.CTkButton(dlg, text="知道了", width=120, height=34,
                      fg_color=WC_COLORS["accent"], hover_color=WC_COLORS["accent_hover"],
                      command=dlg.destroy).pack(pady=10)

    def _show_about_dialog(self):
        """V3.4: 关于页面——软件简介 + 更新内容 + 作者介绍 + 网站。"""
        import webbrowser
        dlg = ctk.CTkToplevel(self)
        dlg.title("关于 NOYA Chat")
        dlg.geometry("480x700")
        dlg.configure(fg_color=WC_COLORS["bg"])
        dlg.transient(self)
        dlg.grab_set()
        dlg.focus_force()

        main = ctk.CTkFrame(dlg, fg_color="transparent")
        main.pack(fill="both", expand=True, padx=24, pady=20)

        ctk.CTkLabel(main, text="🤖", font=ctk.CTkFont(size=56)).pack(pady=(8, 0))
        ctk.CTkLabel(main, text="NOYA Chat 微信助手",
                     font=ctk.CTkFont(family=FONT_FAMILY, size=24, weight="bold"),
                     text_color=WC_COLORS["text"]).pack(pady=(8, 4))
        ctk.CTkLabel(main, text="v3.4 · 本地微信消息智能中枢",
                     font=ctk.CTkFont(family=FONT_FAMILY, size=13),
                     text_color=WC_COLORS["accent"]).pack(pady=(0, 14))

        intro = (
            "NOYA Chat 是一款面向 Windows 的本地微信消息智能管理工具。\n"
            "基于「窗口截图 + PaddleOCR + 大模型」实时解析聊天会话，\n"
            "自动完成语义理解、信息抽取、智能回复，并沉淀到 Obsidian 知识库。\n\n"
            "核心亮点：\n"
            "· 纯视觉方案，不 hook 微信，安全合规\n"
            "· OCR 稳定截图 + 渲染健康度校验\n"
            "· 人格化 AI 回复（贴合关系亲密度）\n"
            "· AI 联系人画像 / 关系图谱 / 项目笔记\n"
            "· 每日简报 + 多维度可视化统计\n"
            "· 全部数据本地存储，绝不上传第三方"
        )
        ctk.CTkLabel(main, text=intro, font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                     text_color=WC_COLORS["text"], wraplength=420,
                     justify="left", anchor="w").pack(anchor="w", pady=4)

        # 分隔
        ctk.CTkFrame(main, fg_color=WC_COLORS["border"], height=1).pack(fill="x", pady=12)

        ctk.CTkLabel(main, text="🆕 v3.4 更新内容",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=WC_COLORS["accent"]).pack(anchor="w", pady=(0, 6))
        updates = (
            "· 语义向量去重 — 本地 Embedding 模型理解语义，\n"
            "  杜绝短文本/语义相似消息的重复处理\n"
            "· RAG 本地知识库 — 历史消息向量检索，\n"
            "  LLM 回复时自动注入相关上下文，回答更精准\n"
            "· 消息情感分析 + 紧急度自动分级 — 三阶段流水线\n"
            "  （规则引擎→统计模型→LLM），紧急消息优先处理\n"
            "· CNN 红点分类器 — 轻量深度学习模型替代传统\n"
            "  HSV+模板匹配，消除红色头像/红包图标误触发\n"
            "· UIA 无渲染监控 — 最小化/屏幕外时通过 Windows\n"
            "  无障碍 API 直接读取未读，零截图延迟\n"
            "· 自适应点击优化 — 像素级验证 + 智能 Y 偏移\n"
            "  + 直方图变化检测，点击准确率大幅提升"
        )
        ctk.CTkLabel(main, text=updates, font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                     text_color=WC_COLORS["text_muted"], wraplength=420,
                     justify="left", anchor="w").pack(anchor="w", pady=4)

        # 分隔
        ctk.CTkFrame(main, fg_color=WC_COLORS["border"], height=1).pack(fill="x", pady=12)

        ctk.CTkLabel(main, text="👤 关于作者",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=WC_COLORS["text"]).pack(anchor="w", pady=(0, 6))
        author = (
            "我是 NOYA（漩涡鸣人），一名独立开发者，\n"
            "热衷于把日常碎片信息变成可检索的知识资产。\n"
            "这个工具是我个人工作流的沉淀，开源免费，欢迎 Star ⭐。"
        )
        ctk.CTkLabel(main, text=author, font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                     text_color=WC_COLORS["text_muted"], wraplength=420,
                     justify="left", anchor="w").pack(anchor="w", pady=4)

        # 网站按钮
        site_btn = ctk.CTkButton(main, text="🌐 访问官网 noya.fangzhoui.cn",
                                 width=240, height=36,
                                 font=ctk.CTkFont(size=13, weight="bold"),
                                 fg_color=WC_COLORS["accent"], hover_color=WC_COLORS["accent_hover"],
                                 corner_radius=8,
                                 command=lambda: webbrowser.open("https://noya.fangzhoui.cn/"))
        site_btn.pack(pady=(14, 8))

        ctk.CTkLabel(main, text="MIT License · 仅供个人学习研究",
                     font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                     text_color=WC_COLORS["text_muted2"]).pack(pady=(0, 6))
        ctk.CTkButton(main, text="关闭", width=100, height=32,
                      fg_color=WC_COLORS["text_muted2"], hover_color=WC_COLORS["text_muted"],
                      command=dlg.destroy).pack(pady=(4, 0))

    def _rebuild_message_list(self):
        """V3: 按当前选中会话重建气泡列表，最新消息在底部（微信风格）。
        - active == _contact_filter_all 或 None → 合并所有会话消息，按 _seq 正序（旧→新）。
        - active == 某个 contact → 只取该会话的消息，倒序排列后 pack。
        """
        try:
            # 清空：先 destroy 所有子节点
            for child in list(self.msg_list_frame_inner.winfo_children()):
                try:
                    if child.winfo_exists():
                        child.destroy()
                except Exception:
                    pass
            self.msg_empty_label = None

            active = self._active_contact
            is_all = (active == self._contact_filter_all)
            is_usage = (active == self._contact_filter_usage)
            if active is None:
                is_all = True

            if is_all:
                # 全部会话：从 db 取最近消息（_get_all_view_messages 已按时间倒序：最新在前）
                items = self._get_all_view_messages()
            elif is_usage:
                # 使用说明：独立卡片，中栏只渲染聊天式说明气泡（不再塞进全部会话流）
                items = []
            else:
                # 单会话：按需懒加载（点开才从 db 取该会话全文），按时间倒序（最新在前）
                items = list(reversed(self._load_conversation(active)))

            # 性能：限制渲染条数（保留最新的 _MAX 条；100 条约 1.4s，300 条实测 4s+ 会卡死 UI）
            _MAX = 100
            total_items = len(items)
            if total_items > _MAX:
                items = items[:_MAX]  # 截取头部（最新部分），保持「最新置顶」

            if not items and not is_usage:
                if is_all:
                    txt = "暂无聊天记录 — 点击「开始监控」识别微信聊天消息，会实时显示在这里。"
                else:
                    txt = f"暂无与「{active}」的聊天记录 — 切换到该会话后，新消息会显示在这里。"
                self.msg_empty_label = ctk.CTkFrame(
                    self.msg_list_frame_inner, fg_color="transparent")
                self.msg_empty_label.pack(pady=36, padx=20, fill="x")
                ctk.CTkLabel(
                    self.msg_empty_label, text="💬",
                    font=ctk.CTkFont(size=22), text_color=WC_COLORS["text_muted"],
                ).pack(pady=(0, 6))
                ctk.CTkLabel(
                    self.msg_empty_label, text=txt,
                    font=ctk.CTkFont(family=FONT_FAMILY, size=12), text_color=WC_COLORS["text_muted"],
                    wraplength=480, justify="center",
                ).pack()
                return

            # 微信风格：最新在顶部，逐条 pack
            _first_err = None
            for m in items:
                try:
                    self._build_message_row(self.msg_list_frame_inner, m)
                except Exception as _e:
                    if _first_err is None:
                        _first_err = _e

            # 首条构建失败的异常记录到调试日志（不再静默吞掉）
            if _first_err is not None:
                try:
                    self._debug_log(f"[重建消息列表] _build_message_row 失败: {_first_err!r}")
                except Exception:
                    pass

            # 使用说明视图：独立渲染聊天式说明气泡（像和「使用说明」这个联系人的一段对话）
            if is_usage:
                try:
                    self._build_usage_bubbles(self.msg_list_frame_inner)
                except Exception as _e:
                    try:
                        self._debug_log(f"[重建消息列表] 使用说明气泡失败: {_e!r}")
                    except Exception:
                        pass

            # 关键修复：强制刷新布局 + 重算 CTkScrollableFrame 的 canvas scrollregion，
            # 否则从“全部会话”切到单卡时，旧内容被销毁但视口不刷新，表现成“空白”。
            try:
                self.msg_list_frame.update_idletasks()
                _canvas = getattr(self.msg_list_frame, "_parent_canvas", None)
                if _canvas is not None:
                    _canvas.configure(scrollregion=_canvas.bbox("all"))
                    _canvas.yview_moveto(1.0)
            except Exception:
                pass

        except Exception:
            import traceback as _tb
            try:
                _msg = _tb.format_exc()
                logger.warning("[重建消息列表] 异常: %s", _msg[-500:])
                self._debug_log("[重建消息列表] 异常:\n" + _msg)
            except Exception:
                pass

    def _refresh_bubble_in_place(self, contact, msg_key, msg_data):
        """更新单个气泡的提取信息（关键词/标签/重要性），不触发全量重建。
        避免 basic→update 两次渲染造成的视觉重复/闪烁。"""
        try:
            cache = self._msg_bubble_cache.setdefault(contact, {})
            bubble = cache.get(msg_key)
            if bubble is None or not bubble.winfo_exists():
                self._rebuild_message_list()
                return
            cache[msg_key] = bubble

            extracted = msg_data.get("extracted") or {}
            is_important = msg_data.get("is_important", False)
            tags = extracted.get("tags", []) or []
            keywords = extracted.get("keywords", []) or []

            for child in list(bubble.winfo_children()):
                try:
                    if isinstance(child, ctk.CTkFrame) and getattr(child, "_bubble_tag_bar", False):
                        child.destroy()
                except Exception:
                    pass

            has_extra = bool(tags or keywords or is_important)
            if not has_extra:
                return

            tag_bar = ctk.CTkFrame(bubble, fg_color="transparent", height=18)
            tag_bar._bubble_tag_bar = True
            tag_bar.pack(fill="x", padx=4, pady=(2, 0), after=bubble.winfo_children()[-1] if bubble.winfo_children() else None)

            for t in tags[:3]:
                ctk.CTkLabel(tag_bar, text=t, font=ctk.CTkFont(size=8),
                             text_color=WC_COLORS["accent"], padx=4).pack(side="left", padx=1)
            for kw in keywords[:2]:
                ctk.CTkLabel(tag_bar, text=f"#{kw}", font=ctk.CTkFont(size=8),
                             text_color=WC_COLORS["text_muted"], padx=4).pack(side="left", padx=1)
            if is_important:
                ctk.CTkLabel(tag_bar, text="⚠重要", font=ctk.CTkFont(size=8),
                             text_color=WC_COLORS["danger"], padx=4).pack(side="left", padx=1)
        except Exception:
            self._rebuild_message_list()

    def _build_reply_tab(self):
        tab = self.tab_reply
        tab.grid_columnconfigure(0, weight=1)

        # 自动回复开关
        switch_frame = ctk.CTkFrame(tab, fg_color=WC_COLORS["card"], corner_radius=14)
        switch_frame.grid(row=0, column=0, sticky="ew", padx=20, pady=20)
        switch_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(switch_frame, text="💬 自动回复",
                     font=ctk.CTkFont(size=16, weight="bold"),
                     text_color=WC_COLORS["text"]).grid(row=0, column=0, padx=15, pady=(15, 5), sticky="w")

        ctk.CTkLabel(switch_frame, text="开启后将自动回复对方消息（默认关闭，仅提取信息）",
                     font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                     text_color=WC_COLORS["text_muted"]).grid(row=1, column=0, padx=15, pady=(0, 10), sticky="w")

        self.reply_switch = ctk.CTkSwitch(
            switch_frame, text="启用自动回复",
            font=ctk.CTkFont(family=FONT_FAMILY, size=14),
            command=self.on_reply_switch,
            fg_color=WC_COLORS["text_muted2"], progress_color=WC_COLORS["accent"],
            button_color=WC_COLORS["card"], button_hover_color=WC_COLORS["accent_light"],
        )
        auto_reply_cfg = self.config_data.get("auto_reply", {})
        self.reply_switch.select() if auto_reply_cfg.get("enabled") else self.reply_switch.deselect()
        self.reply_switch.grid(row=2, column=0, padx=15, pady=(0, 5), sticky="w")

        # 预览确认模式开关
        self.preview_mode_switch = ctk.CTkSwitch(
            switch_frame, text="预览确认模式（生成回复后需手动确认才发送）",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            command=self.on_preview_mode_switch,
            fg_color=WC_COLORS["text_muted2"], progress_color=WC_COLORS["accent"],
            button_color=WC_COLORS["card"], button_hover_color=WC_COLORS["accent_light"],
        )
        self.preview_mode_switch.select() if auto_reply_cfg.get("preview_mode") else self.preview_mode_switch.deselect()
        self.preview_mode_switch.grid(row=3, column=0, padx=15, pady=(0, 15), sticky="w")

        # 角色说明
        roles_frame = ctk.CTkFrame(tab, fg_color=WC_COLORS["card"], corner_radius=14)
        roles_frame.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0, 20))
        roles_frame.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(roles_frame, text="👤 角色配置",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=WC_COLORS["text"]).grid(row=0, column=0, padx=15, pady=(15, 10), sticky="w")

        roles = self.config_data.get("roles", {})
        for i, (role_key, role) in enumerate(roles.items()):
            role_card = ctk.CTkFrame(roles_frame, fg_color=WC_COLORS["card_hover"], corner_radius=14)
            role_card.grid(row=i + 1, column=0, sticky="ew", padx=15, pady=3)
            role_card.grid_columnconfigure(1, weight=1)

            ctk.CTkLabel(role_card, text=role.get("name", role_key),
                         font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
                         text_color=WC_COLORS["accent"]).grid(row=0, column=0, padx=10, pady=8, sticky="w")
            ctk.CTkLabel(role_card, text=role.get("reply_style", ""),
                         font=ctk.CTkFont(family=FONT_FAMILY, size=11),
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
                      font=ctk.CTkFont(family=FONT_FAMILY, size=13),
                      command=self.refresh_data).grid(row=0, column=0, padx=5)

        ctk.CTkButton(btn_frame, text="导出CSV", width=100, height=32,
                      font=ctk.CTkFont(family=FONT_FAMILY, size=13),
                      fg_color=WC_COLORS["accent"], hover_color=WC_COLORS["accent_hover"],
                      command=self.export_csv).grid(row=0, column=1, padx=5)

        ctk.CTkButton(btn_frame, text="导出JSON", width=100, height=32,
                      font=ctk.CTkFont(family=FONT_FAMILY, size=13),
                      fg_color=WC_COLORS["accent"], hover_color=WC_COLORS["accent_hover"],
                      command=self.export_json).grid(row=0, column=2, padx=5)

        self.data_stats_label = ctk.CTkLabel(
            btn_frame, text="暂无数据",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12), text_color=WC_COLORS["text_muted"])
        self.data_stats_label.grid(row=0, column=3, padx=10, sticky="e")

        # 搜索区域
        search_frame = ctk.CTkFrame(tab, fg_color=WC_COLORS["card"], corner_radius=14)
        search_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=5)

        ctk.CTkLabel(search_frame, text="消息搜索",
                     font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
                     text_color=WC_COLORS["text"]).pack(anchor="w", padx=10, pady=5)

        search_input_frame = ctk.CTkFrame(search_frame, fg_color="transparent")
        search_input_frame.pack(fill="x", padx=10, pady=5)

        self.search_entry_msg = ctk.CTkEntry(
            search_input_frame, placeholder_text="输入关键词搜索消息内容...",
            height=34, corner_radius=10, border_width=0,
            fg_color=WC_COLORS["bg"], text_color=WC_COLORS["text"],
            font=ctk.CTkFont(size=12),
        )
        self.search_entry_msg.pack(side="left", fill="x", expand=True, padx=(0, 6))

        ctk.CTkButton(search_input_frame, text="🔍 搜索", width=80, height=34,
                      corner_radius=10, fg_color=WC_COLORS["accent"],
                      hover_color=WC_COLORS["accent_hover"],
                      font=ctk.CTkFont(size=12, weight="bold"),
                      command=self._search_messages).pack(side="left")

        ctk.CTkButton(search_input_frame, text="⭐ 重要", width=80, height=34,
                      corner_radius=10, fg_color=WC_COLORS["bg_dark"],
                      hover_color=WC_COLORS["sidebar_hover"],
                      text_color=WC_COLORS["text"],
                      font=ctk.CTkFont(size=12),
                      command=self._show_important_messages).pack(side="left", padx=5)

        ctk.CTkButton(search_input_frame, text="📊 报告", width=80, height=34,
                      corner_radius=10, fg_color=WC_COLORS["bg_dark"],
                      hover_color=WC_COLORS["sidebar_hover"],
                      text_color=WC_COLORS["text"],
                      font=ctk.CTkFont(size=12),
                      command=self._generate_today_report).pack(side="left")

        # 统计面板（可滚动，撑满整行）
        stats_container = ctk.CTkFrame(tab, fg_color="transparent")
        stats_container.grid(row=2, column=0, sticky="nsew", padx=10, pady=5)
        stats_container.grid_columnconfigure(0, weight=1)
        stats_container.grid_rowconfigure(0, weight=1)
        self._create_stats_panel(stats_container)

        # 搜索面板
        search_container = ctk.CTkFrame(tab, fg_color="transparent")
        search_container.grid(row=3, column=0, sticky="ew", padx=10, pady=5)
        self._create_search_panel(search_container)

        # 数据表格
        table_frame = ctk.CTkFrame(tab, fg_color=WC_COLORS["card"], corner_radius=14)
        table_frame.grid(row=4, column=0, sticky="nsew", padx=10, pady=(0, 10))
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)

        self.data_text = ctk.CTkTextbox(
            table_frame, font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=WC_COLORS["bg"], text_color=WC_COLORS["text"],
            wrap="word", state="disabled",
        )
        self.data_text.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)

    # ==============================================================
    # 分类优先级规则编辑
    # ==============================================================
    def _load_classification_cats(self):
        """读取分类规则（优先引擎实例，否则 config），转为编辑用结构。"""
        cats = []
        src = None
        if getattr(self, "engine", None) and getattr(self.engine, "extractor", None):
            src = self.engine.extractor.classification_categories
        if not src:
            src = (((self.config_data or {}).get("extraction", {}) or {})
                   .get("classification", {}) or {}).get("categories", [])
        for c in src or []:
            kws = c.get("keywords", [])
            if isinstance(kws, list):
                kws = ", ".join(kws)
            cats.append({
                "name": str(c.get("name", "")),
                "priority": int(c.get("priority", 0)),
                "important": bool(c.get("important", False)),
                "keywords": str(kws),
            })
        if not cats:
            cats = [{"name": "工作", "priority": 3, "important": True, "keywords": ""},
                    {"name": "私事", "priority": 2, "important": False, "keywords": ""},
                    {"name": "群聊", "priority": 1, "important": False, "keywords": ""}]
        return cats

    def _render_classification_rows(self):
        for w in self.cls_list_frame.winfo_children():
            try:
                w.destroy()
            except Exception:
                pass
        for idx, cat in enumerate(self._classification_cats):
            row = ctk.CTkFrame(self.cls_list_frame, fg_color="transparent")
            row.pack(fill="x", pady=3)
            name_e = ctk.CTkEntry(row, width=90, font=ctk.CTkFont(family=FONT_FAMILY, size=12))
            name_e.insert(0, cat["name"])
            name_e.pack(side="left", padx=4)
            pri_e = ctk.CTkEntry(row, width=50, font=ctk.CTkFont(family=FONT_FAMILY, size=12))
            pri_e.insert(0, str(cat["priority"]))
            pri_e.pack(side="left", padx=4)
            imp_var = ctk.BooleanVar(value=bool(cat["important"]))
            imp_sw = ctk.CTkSwitch(row, text="重要", variable=imp_var, width=64)
            if cat["important"]:
                imp_sw.select()
            imp_sw.pack(side="left", padx=4)
            kw_e = ctk.CTkEntry(row, width=300, font=ctk.CTkFont(family=FONT_FAMILY, size=12))
            kw_e.insert(0, str(cat["keywords"]))
            kw_e.pack(side="left", padx=4, fill="x", expand=True)
            del_btn = ctk.CTkButton(
                row, text="✕", width=30, fg_color=WC_COLORS["danger"],
                command=lambda i=idx: self._del_classification_cat(i))
            del_btn.pack(side="left", padx=4)
            cat["_widgets"] = (name_e, pri_e, imp_var, imp_sw, kw_e)

    def _add_classification_cat(self):
        self._classification_cats.append(
            {"name": "新分类", "priority": 1, "important": False, "keywords": ""})
        self._render_classification_rows()

    def _del_classification_cat(self, i):
        if 0 <= i < len(self._classification_cats):
            self._classification_cats.pop(i)
            self._render_classification_rows()

    def _collect_classification_cats(self):
        cats = []
        for cat in self._classification_cats:
            w = cat.get("_widgets")
            if not w:
                continue
            name_e, pri_e, imp_var, imp_sw, kw_e = w
            name = name_e.get().strip()
            if not name:
                continue
            try:
                pri = int(pri_e.get().strip() or "0")
            except ValueError:
                pri = 0
            kws_raw = kw_e.get().strip()
            kws = [k.strip() for k in kws_raw.replace("，", ",").split(",") if k.strip()]
            cats.append({
                "name": name, "priority": pri,
                "important": bool(imp_var.get()), "keywords": kws,
            })
        return cats

    def _ensure_config_file(self):
        """UI 内所有写 config.yaml 前共用：确保 config.yaml 存在，不存在则从 example 复制生成。"""
        cfg_path = _app_path("config.yaml")
        if os.path.exists(cfg_path):
            return cfg_path
        ex_path = _app_path("config.example.yaml")
        if os.path.exists(ex_path):
            import shutil as _shutil
            try:
                _shutil.copy2(ex_path, cfg_path)
                self._on_log("info", f"[config] 自动生成配置文件: {cfg_path}")
            except Exception as e:
                self._on_log("warning", f"[config] 复制 example 失败: {e}，新建空配置")
                with open(cfg_path, "w", encoding="utf-8") as f:
                    f.write("# Auto-generated config\n")
        else:
            with open(cfg_path, "w", encoding="utf-8") as f:
                f.write("# Auto-generated config\n")
        return cfg_path

    def _save_classification_rules(self):
        try:
            cats = self._collect_classification_cats()
            cfg_path = self._ensure_config_file()
            with open(cfg_path, "r", encoding="utf-8") as f:
                text = f.read() or ""
            # 保留原有的 enabled 开关；若不存在则默认 true
            current_enabled = True
            cls_re = re.compile(
                r"(?m)^  classification:.*?(?=\n  [a-zA-Z_][a-zA-Z0-9_]*:|\Z)", re.DOTALL)
            cls_m = cls_re.search(text)
            if cls_m:
                em = re.search(r"(?m)^    enabled:\s*(\w+)", cls_m.group(0))
                if em:
                    current_enabled = em.group(1).strip().lower() == "true"
            # 仅替换 categories 列表，保留 classification: 头、enabled 行及注释
            cat_lines = ["    categories:"]
            for c in cats:
                kws = c.get("keywords") or []
                if isinstance(kws, list):
                    kws_repr = "[" + ", ".join(kws) + "]" if kws else "[]"
                else:
                    kws_repr = str(kws)
                cat_lines.append(f"    - name: {c.get('name', '')}")
                cat_lines.append(f"      priority: {int(c.get('priority', 0))}")
                cat_lines.append(f"      important: {'true' if c.get('important') else 'false'}")
                cat_lines.append(f"      keywords: {kws_repr}")
            new_cats = "\n".join(cat_lines)
            cat_re = re.compile(
                r"(?m)^    categories:.*?(?=\n  [a-zA-Z_][a-zA-Z0-9_]*:|\Z)", re.DOTALL)
            m = cat_re.search(text)
            if m:
                text = text[:m.start()] + new_cats + text[m.end():]
            elif cls_m:
                # 无 categories 子键但存在 classification 段：在其后插入
                text = (text[:cls_m.end()].rstrip("\n") + "\n" + new_cats + "\n"
                        + text[cls_m.end():])
            else:
                # classification 段完全缺失：追加到文件末尾
                text = text.rstrip("\n") + "\n  classification:\n    enabled: true\n" + new_cats + "\n"
            with open(cfg_path, "w", encoding="utf-8") as f:
                f.write(text)
            self.config_data.setdefault("extraction", {})["classification"] = {
                "enabled": current_enabled, "categories": cats}
            if getattr(self, "engine", None) and hasattr(self.engine, "update_classification"):
                self.engine.update_classification(cats)
            self._on_log("info", f"[分类] 已保存 {len(cats)} 条分类规则并生效")
            messagebox.showinfo("分类规则", "已保存并生效")
        except Exception as e:
            self._on_log("error", f"[分类] 保存失败: {e}")
            messagebox.showerror("保存失败", str(e))

    def _save_reply_style(self):
        """V3.3: 把设置页的「回复风格」写入 config.yaml 的 reply_style_preset 段（保留注释）。"""
        try:
            tone = self.style_tone.get().strip()
            max_sent_raw = self.style_max_sent.get().strip()
            try:
                max_sent = int(max_sent_raw) if max_sent_raw else None
            except ValueError:
                max_sent = None
            emoji = bool(self.style_emoji.get())
            forbidden = [x.strip() for x in self.style_forbidden.get().split(",") if x.strip()]
            pet_words = [x.strip() for x in self.style_pet.get().split(",") if x.strip()]
            notes = self.style_notes.get().strip()

            preset = {}
            if tone:
                preset["tone"] = tone
            if max_sent is not None:
                preset["max_sentences"] = max_sent
            preset["emoji"] = emoji
            if forbidden:
                preset["forbidden"] = forbidden
            if pet_words:
                preset["pet_words"] = pet_words
            if notes:
                preset["notes"] = notes

            cfg_path = self._ensure_config_file()
            with open(cfg_path, "r", encoding="utf-8") as f:
                text = f.read() or ""

            # 构造 reply_style_preset 段（2 空格缩进，与文件其他段一致）
            lines = ["reply_style_preset:"]
            if "tone" in preset:
                lines.append(f"  tone: \"{preset['tone']}\"")
            if "max_sentences" in preset:
                lines.append(f"  max_sentences: {preset['max_sentences']}")
            lines.append(f"  emoji: {'true' if preset['emoji'] else 'false'}")
            if "forbidden" in preset:
                lines.append(f"  forbidden: {preset['forbidden']}")
            if "pet_words" in preset:
                lines.append(f"  pet_words: {preset['pet_words']}")
            if "notes" in preset:
                lines.append(f"  notes: \"{preset['notes']}\"")
            new_block = "\n".join(lines)

            # 仅替换 reply_style_preset 段（保留其他段和注释）
            re_block = re.compile(
                r"(?m)^reply_style_preset:.*?(?=\n[a-zA-Z_][a-zA-Z0-9_]*:|\Z)", re.DOTALL)
            m = re_block.search(text)
            if m:
                text = text[:m.start()] + new_block + text[m.end():]
            else:
                # 段缺失：追加到文件末尾
                text = text.rstrip("\n") + "\n\n" + new_block + "\n"

            with open(cfg_path, "w", encoding="utf-8") as f:
                f.write(text)

            # 内存更新 + 实时生效（通知 engine 的 llm_client）
            self.config_data["reply_style_preset"] = preset
            eng = getattr(self, "engine", None)
            if eng is not None and hasattr(eng, "llm_client") and eng.llm_client is not None:
                eng.llm_client._style_preset = preset
                self._on_log("info", "[回复风格] 已保存并实时生效")
            else:
                self._on_log("info", "[回复风格] 已保存（重启监控后生效）")
            messagebox.showinfo("回复风格", "已保存并生效")
        except Exception as e:
            self._on_log("error", f"[回复风格] 保存失败: {e}")
            messagebox.showerror("保存失败", str(e))

    def _test_reply_style(self):
        """测试回复风格：用当前设置发一条测试消息给 LLM，预览效果"""
        try:
            # 先保存当前风格设置
            self._save_reply_style()

            llm_cfg = self.config_data.get("llm", {})
            api_key = llm_cfg.get("api_key", "")
            base_url = llm_cfg.get("base_url", "")
            model = llm_cfg.get("model", "")

            if not api_key or not base_url:
                messagebox.showwarning("测试失败", "请先在 LLM 配置中填写 API Key 和 API 地址")
                return

            preset = self.config_data.get("reply_style_preset") or {}
            from llm_client import LLMClient
            client = LLMClient({
                "api_key": api_key,
                "base_url": base_url,
                "model": model,
                "max_tokens": 200,
                "temperature": 0.3,
                "thinking": False,
            }, style_preset=preset if preset else None)

            _role = {"name": "测试助手", "reply_style": "自然", "system_prompt": "你是一个友好的聊天助手。"}
            _test_msgs = [
                "你在干嘛呢？",
                "今天天气真好，要不要出去玩？",
                "哈哈，那个视频太搞笑了你看了吗？",
            ]
            _test_msg = _test_msgs[int(time.time()) % 3]

            self._on_log("info", f"[风格测试] 发送测试消息: {_test_msg}")
            reply = client.generate_reply("测试用户", _test_msg, _role, [])
            if reply:
                self._on_log("info", f"[风格测试] LLM回复: {reply}")
                messagebox.showinfo("风格测试结果",
                    f"测试消息：{_test_msg}\n\nAI回复：{reply}\n\n"
                    f"语气/人设：{preset.get('tone', '未设置')}\n"
                    f"emoji：{'允许' if preset.get('emoji', True) else '禁止'}")
            else:
                messagebox.showerror("测试失败", "LLM 未返回回复，请检查 API 配置")
        except Exception as e:
            self._on_log("error", f"[风格测试] 异常: {e}")
            messagebox.showerror("测试失败", str(e))

    def _import_classification_rules(self):
        filepath = filedialog.askopenfilename(
            title="导入分类规则", filetypes=[("JSON", "*.json"), ("ALL", "*.*")])
        if not filepath:
            return
        try:
            with open(filepath, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                data = data.get("categories", [])
            if not isinstance(data, list):
                raise ValueError("格式应为分类数组")
            cats = []
            for c in data:
                kws = c.get("keywords", [])
                if isinstance(kws, list):
                    kws = ", ".join(kws)
                cats.append({
                    "name": str(c.get("name", "")),
                    "priority": int(c.get("priority", 0)),
                    "important": bool(c.get("important", False)),
                    "keywords": str(kws),
                })
            self._classification_cats = cats
            self._render_classification_rows()
            self._on_log("info", f"[分类] 已导入 {len(cats)} 条规则（点“保存并生效”才会应用）")
        except Exception as e:
            messagebox.showerror("导入失败", str(e))

    def _export_classification_rules(self):
        try:
            cats = self._collect_classification_cats()
            filepath = filedialog.asksaveasfilename(
                title="导出分类规则", defaultextension=".json",
                filetypes=[("JSON", "*.json")])
            if not filepath:
                return
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump({"categories": cats}, f, ensure_ascii=False, indent=2)
            self._on_log("info", f"[分类] 已导出 {len(cats)} 条规则到 {filepath}")
        except Exception as e:
            messagebox.showerror("导出失败", str(e))

    def _build_settings_tab(self):
        tab = self.tab_settings
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        # V3.1: 顶部导航表（固定，点击直达各设置区块）—— 高对比、分组、选中高亮
        nav_bar = ctk.CTkFrame(tab, fg_color=WC_COLORS["card"], corner_radius=14,
                              border_width=0)
        nav_bar.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 4))
        nav_bar.grid_columnconfigure(0, weight=1)

        self._settings_nav_btns = {}   # attr -> button
        self._settings_nav_groups = []

        def _nav_group(title, specs):
            gp = ctk.CTkFrame(nav_bar, fg_color="transparent")
            gp.pack(side="left", fill="y", padx=(10, 4), pady=8)
            ctk.CTkLabel(gp, text=title, font=ctk.CTkFont(family=FONT_FAMILY, size=10, weight="bold"),
                         text_color=WC_COLORS["text_muted"]).pack(anchor="w", padx=2, pady=(0, 3))
            row = ctk.CTkFrame(gp, fg_color="transparent")
            row.pack(fill="x")
            for idx, (label, attr) in enumerate(specs):
                btn = ctk.CTkButton(row, text=f"{idx+1}.{label}", width=96, height=30,
                                   font=ctk.CTkFont(family=FONT_FAMILY, size=11, weight="bold"),
                                   fg_color=WC_COLORS["bg"],
                                   text_color=WC_COLORS["text"],
                                   hover_color=WC_COLORS["accent_light"],
                                   border_width=0,
                                   corner_radius=8,
                                   command=lambda a=attr: self._jump_to_setting(a))
                btn.pack(side="left", padx=3, pady=2)
                self._settings_nav_btns[attr] = btn

        _nav_group("基础", [
            ("监控模式", "monitor_frame"),
            ("联系人", "filter_frame"),
            ("窗口校准", "calib_frame"),
        ])
        _nav_group("AI", [
            ("AI抽取", "ai_frame"),
            ("LLM", "llm_frame"),
            ("回复风格", "style_frame"),
            ("分类", "cls_frame"),
            ("报告", "report_frame"),
        ])
        _nav_group("同步", [
            ("Obsidian", "obsidian_frame"),
            ("高级", "advanced_frame"),
        ])

        # 使用可滚动容器，解决设置项过多无法查看的问题
        scroll = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        scroll.grid(row=1, column=0, sticky="nsew")

        # 消息监控模式
        monitor_frame = ctk.CTkFrame(scroll, fg_color=WC_COLORS["card"], corner_radius=14)
        monitor_frame.pack(fill="x", padx=20, pady=20)
        monitor_frame.grid_columnconfigure(1, weight=1)
        self.monitor_frame = monitor_frame

        ctk.CTkLabel(monitor_frame, text="消息监控模式",
                     font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
                     text_color=WC_COLORS["text"]).grid(row=0, column=0, columnspan=2, padx=15, pady=(15, 10), sticky="w")

        ctk.CTkLabel(monitor_frame, text="选择如何监控新消息",
                     font=ctk.CTkFont(family=FONT_FAMILY, size=12),
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
                           font=ctk.CTkFont(family=FONT_FAMILY, size=13),
                           text_color=WC_COLORS["text"]).grid(row=2, column=0, columnspan=2, padx=25, pady=3, sticky="w")
        ctk.CTkRadioButton(monitor_frame, text="红点监控 — 自动检测左侧未读红点并切换",
                           variable=self.monitor_var, value="red_dot",
                           font=ctk.CTkFont(family=FONT_FAMILY, size=13),
                           text_color=WC_COLORS["text"]).grid(row=3, column=0, columnspan=2, padx=25, pady=3, sticky="w")
        ctk.CTkRadioButton(monitor_frame, text="轮询扫描 — 定时遍历联系人列表",
                           variable=self.monitor_var, value="scan",
                           font=ctk.CTkFont(family=FONT_FAMILY, size=13),
                           text_color=WC_COLORS["text"]).grid(row=4, column=0, columnspan=2, padx=25, pady=(3, 15), sticky="w")

        # 联系人过滤
        filter_frame = ctk.CTkFrame(scroll, fg_color=WC_COLORS["card"], corner_radius=8)
        filter_frame.pack(fill="x", padx=20, pady=5)
        self.filter_frame = filter_frame
        ctk.CTkLabel(filter_frame, text="👥 联系人过滤",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=WC_COLORS["text"]).pack(anchor="w", padx=15, pady=(10, 5))

        ctk.CTkLabel(filter_frame, text="白名单（逗号分隔，留空=全部监控）:",
                     font=ctk.CTkFont(family=FONT_FAMILY, size=12), text_color=WC_COLORS["text_muted"]).pack(anchor="w", padx=15, pady=(5, 0))
        self.whitelist_entry = ctk.CTkEntry(filter_frame, width=300, placeholder_text="如: 张三,李四,*群*")
        self.whitelist_entry.pack(fill="x", padx=15, pady=2)
        # 回填当前配置中的白名单（强制转str，防止yaml把10086解析成int）
        _filter_cfg = self.config_data.get("contacts_filter", {})
        _wl = _filter_cfg.get("whitelist", []) or []
        if _wl:
            self.whitelist_entry.insert(0, ",".join(str(x) for x in _wl))

        ctk.CTkLabel(filter_frame, text="黑名单（逗号分隔，已默认包含公众号/服务号/订阅号等）:",
                     font=ctk.CTkFont(family=FONT_FAMILY, size=12), text_color=WC_COLORS["text_muted"]).pack(anchor="w", padx=15, pady=(5, 0))
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
        calib_frame = ctk.CTkFrame(scroll, fg_color=WC_COLORS["card"], corner_radius=14)
        calib_frame.pack(fill="x", padx=20, pady=10)
        self.calib_frame = calib_frame

        ctk.CTkLabel(calib_frame, text="🎯 窗口校准",
                     font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
                     text_color=WC_COLORS["text"]).pack(anchor="w", padx=15, pady=5)
        ctk.CTkLabel(calib_frame, text="手动框选微信聊天区域，解决DPI缩放导致的坐标偏移",
                     font=ctk.CTkFont(family=FONT_FAMILY, size=11), text_color="gray").pack(anchor="w", padx=15)

        ctk.CTkButton(calib_frame, text="启动校准向导",
                      command=self._run_calibration).pack(padx=15, pady=10, anchor="w")

        # 模式开关
        mode_frame = ctk.CTkFrame(scroll, fg_color=WC_COLORS["card"], corner_radius=14)
        mode_frame.pack(fill="x", padx=20, pady=10)

        ctk.CTkLabel(mode_frame, text="智能模式",
                     font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
                     text_color=WC_COLORS["text"]).pack(anchor="w", padx=15, pady=5)

        self.fast_mode_switch = ctk.CTkSwitch(mode_frame, text="快速模式（窗口标题检测，省CPU）",
                                              onvalue=True, offvalue=False)
        self.fast_mode_switch.pack(anchor="w", padx=15, pady=3)

        self.dnd_switch = ctk.CTkSwitch(mode_frame, text="勿扰模式（检测到用户操作时暂停）",
                                        onvalue=True, offvalue=False)
        self.dnd_switch.pack(anchor="w", padx=15, pady=3)

        # 报告设置
        report_frame = ctk.CTkFrame(scroll, fg_color=WC_COLORS["card"], corner_radius=14)
        report_frame.pack(fill="x", padx=20, pady=10)
        self.report_frame = report_frame

        ctk.CTkLabel(report_frame, text="定时报告",
                     font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
                     text_color=WC_COLORS["text"]).pack(anchor="w", padx=15, pady=5)

        report_btn_frame = ctk.CTkFrame(report_frame, fg_color="transparent")
        report_btn_frame.pack(fill="x", padx=15, pady=5)

        ctk.CTkButton(report_btn_frame, text="生成今日报告",
                      command=self._generate_today_report).pack(side="left", padx=5)

        ctk.CTkButton(report_btn_frame, text="生成本周报告",
                      command=self._generate_weekly_report).pack(side="left", padx=5)

        ctk.CTkButton(report_btn_frame, text="打开报告目录",
                      command=self._open_report_dir).pack(side="left", padx=5)

        # 分类优先级规则
        cls_frame = ctk.CTkFrame(scroll, fg_color=WC_COLORS["card"], corner_radius=14)
        cls_frame.pack(fill="x", padx=20, pady=10)
        self.cls_frame = cls_frame
        ctk.CTkLabel(cls_frame, text="📑 分类优先级规则",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=WC_COLORS["text"]).pack(anchor="w", padx=15, pady=(10, 2))
        ctk.CTkLabel(cls_frame, text="为消息归类并设定优先级（工作/私事/群聊…）。命中 important 的类即标记为重要消息；群聊无需关键词，按会话类型自动归类。",
                     font=ctk.CTkFont(family=FONT_FAMILY, size=11), text_color="gray").pack(anchor="w", padx=15)

        self._classification_cats = self._load_classification_cats()
        self.cls_list_frame = ctk.CTkFrame(cls_frame, fg_color="transparent")
        self.cls_list_frame.pack(fill="x", padx=10, pady=5)
        self._render_classification_rows()

        cls_btn_frame = ctk.CTkFrame(cls_frame, fg_color="transparent")
        cls_btn_frame.pack(fill="x", padx=15, pady=(5, 12))
        ctk.CTkButton(cls_btn_frame, text="＋ 新增分类", width=110,
                      command=self._add_classification_cat).pack(side="left", padx=5)
        ctk.CTkButton(cls_btn_frame, text="保存并生效", width=110,
                      command=self._save_classification_rules).pack(side="left", padx=5)
        ctk.CTkButton(cls_btn_frame, text="导入规则", width=110,
                      command=self._import_classification_rules).pack(side="left", padx=5)
        ctk.CTkButton(cls_btn_frame, text="导出规则", width=110,
                      command=self._export_classification_rules).pack(side="left", padx=5)

        # LLM设置
        llm_frame = ctk.CTkFrame(scroll, fg_color=WC_COLORS["card"], corner_radius=14)
        llm_frame.pack(fill="x", padx=20, pady=20)
        llm_frame.grid_columnconfigure(1, weight=1)
        self.llm_frame = llm_frame

        ctk.CTkLabel(llm_frame, text="LLM 配置",
                     font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
                     text_color=WC_COLORS["text"]).grid(row=0, column=0, columnspan=2, padx=15, pady=(15, 10), sticky="w")

        llm_cfg = self.config_data.get("llm", {})

        # ===== 厂商预设（一键切换国内大模型厂商） =====
        self._llm_providers = {
            "自定义":        {"url": "", "model": ""},
            "阿里云百炼":     {"url": "https://dashscope.aliyuncs.com/compatible-mode", "model": "qwen3.7-flash"},
            "DeepSeek 官方": {"url": "https://api.deepseek.com", "model": "deepseek-chat"},
            "智谱AI GLM":    {"url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4-flash"},
            "硅基流动":       {"url": "https://api.siliconflow.cn/v1/", "model": "Qwen/Qwen2.5-7B-Instruct"},
            "火山引擎豆包":    {"url": "https://ark.cn-beijing.volces.com/api/v3/", "model": "doubao-lite-32k"},
        }
        _current_provider = llm_cfg.get("provider", "自定义")
        if _current_provider not in self._llm_providers:
            _current_provider = "自定义"

        ctk.CTkLabel(llm_frame, text="厂商", font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                     text_color=WC_COLORS["text_muted"]).grid(row=1, column=0, padx=15, pady=5, sticky="w")
        self.provider_var = ctk.StringVar(value=_current_provider)
        self.provider_menu = ctk.CTkOptionMenu(
            llm_frame,
            values=list(self._llm_providers.keys()),
            variable=self.provider_var,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=WC_COLORS["card"],
            button_color=WC_COLORS["accent"],
            button_hover_color=WC_COLORS["accent_hover"],
            dropdown_fg_color=WC_COLORS["card"],
            dropdown_text_color=WC_COLORS["text"],
            dropdown_font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            command=self._on_provider_changed,
        )
        self.provider_menu.grid(row=1, column=1, padx=15, pady=5, sticky="ew")

        ctk.CTkLabel(llm_frame, text="API地址", font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                     text_color=WC_COLORS["text_muted"]).grid(row=2, column=0, padx=15, pady=5, sticky="w")
        self.entry_url = ctk.CTkEntry(llm_frame, font=ctk.CTkFont(family=FONT_FAMILY, size=12))
        self.entry_url.insert(0, llm_cfg.get("base_url", ""))
        self.entry_url.grid(row=2, column=1, padx=15, pady=5, sticky="ew")

        ctk.CTkLabel(llm_frame, text="API Key", font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                     text_color=WC_COLORS["text_muted"]).grid(row=3, column=0, padx=15, pady=5, sticky="w")
        self.entry_key = ctk.CTkEntry(llm_frame, font=ctk.CTkFont(family=FONT_FAMILY, size=12), show="*")
        self.entry_key.insert(0, llm_cfg.get("api_key", ""))
        self.entry_key.grid(row=3, column=1, padx=15, pady=5, sticky="ew")

        # 测试连接按钮
        self.btn_test_llm = ctk.CTkButton(
            llm_frame,
            text="🔗 测试连接",
            font=ctk.CTkFont(size=11),
            fg_color=WC_COLORS["info"],
            hover_color=WC_COLORS["accent_hover"],
            command=self._test_llm_connection,
            width=100,
            height=28
        )
        self.btn_test_llm.grid(row=3, column=2, padx=(5, 15), pady=5, sticky="e")

        # 连接状态标签
        self.llm_status_label = ctk.CTkLabel(
            llm_frame,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            text_color=WC_COLORS["text_muted"]
        )
        self.llm_status_label.grid(row=4, column=0, columnspan=3, padx=15, pady=(0, 5), sticky="w")

        ctk.CTkLabel(llm_frame, text="模型", font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                     text_color=WC_COLORS["text_muted"]).grid(row=5, column=0, padx=15, pady=5, sticky="w")
        self.entry_model = ctk.CTkEntry(llm_frame, font=ctk.CTkFont(family=FONT_FAMILY, size=12))
        self.entry_model.insert(0, llm_cfg.get("model", ""))
        self.entry_model.grid(row=5, column=1, padx=15, pady=5, sticky="ew")

        # ===== 回复风格设定（V3.3：UI 直接编辑，写入 config.yaml 的 reply_style_preset）=====
        style_frame = ctk.CTkFrame(scroll, fg_color=WC_COLORS["card"], corner_radius=14)
        style_frame.pack(fill="x", padx=20, pady=20)
        style_frame.grid_columnconfigure(1, weight=1)
        self.style_frame = style_frame

        ctk.CTkLabel(style_frame, text="💬 回复风格设定",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=WC_COLORS["text"]).grid(row=0, column=0, columnspan=2, padx=15, pady=(15, 4), sticky="w")
        ctk.CTkLabel(style_frame, text="全局生效（叠加在角色模板之上，优先级更高）",
                     font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                     text_color=WC_COLORS["text_muted"]).grid(row=1, column=0, columnspan=2, padx=15, pady=(0, 10), sticky="w")

        sp = self.config_data.get("reply_style_preset") or {}

        def _slabel(r, t):
            ctk.CTkLabel(style_frame, text=t, font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                         text_color=WC_COLORS["text_muted"]).grid(
                row=r, column=0, padx=15, pady=5, sticky="w")

        _slabel(2, "语气/人设")
        self.style_tone = ctk.CTkEntry(style_frame, font=ctk.CTkFont(family=FONT_FAMILY, size=12))
        self.style_tone.insert(0, sp.get("tone", ""))
        self.style_tone.grid(row=2, column=1, padx=15, pady=5, sticky="ew")

        _slabel(3, "每条最多句数")
        self.style_max_sent = ctk.CTkEntry(style_frame, font=ctk.CTkFont(family=FONT_FAMILY, size=12), width=80)
        self.style_max_sent.insert(0, str(sp.get("max_sentences", "")))
        self.style_max_sent.grid(row=3, column=1, padx=15, pady=5, sticky="w")

        _slabel(4, "允许 emoji")
        self.style_emoji = ctk.CTkSwitch(style_frame, text="",
                                         font=ctk.CTkFont(family=FONT_FAMILY, size=12))
        if sp.get("emoji", True):
            self.style_emoji.select()
        else:
            self.style_emoji.deselect()
        self.style_emoji.grid(row=4, column=1, padx=15, pady=5, sticky="w")

        _slabel(5, "禁忌词(逗号分隔)")
        self.style_forbidden = ctk.CTkEntry(style_frame, font=ctk.CTkFont(family=FONT_FAMILY, size=12))
        self.style_forbidden.insert(0, ", ".join(sp.get("forbidden", []) or []))
        self.style_forbidden.grid(row=5, column=1, padx=15, pady=5, sticky="ew")

        _slabel(6, "口头禅(逗号分隔)")
        self.style_pet = ctk.CTkEntry(style_frame, font=ctk.CTkFont(family=FONT_FAMILY, size=12))
        self.style_pet.insert(0, ", ".join(sp.get("pet_words", []) or []))
        self.style_pet.grid(row=6, column=1, padx=15, pady=5, sticky="ew")

        _slabel(7, "补充备注")
        self.style_notes = ctk.CTkEntry(style_frame, font=ctk.CTkFont(family=FONT_FAMILY, size=12))
        self.style_notes.insert(0, sp.get("notes", ""))
        self.style_notes.grid(row=7, column=1, padx=15, pady=5, sticky="ew")

        style_btn_frame = ctk.CTkFrame(style_frame, fg_color="transparent")
        style_btn_frame.grid(row=8, column=0, columnspan=2, padx=15, pady=(12, 15), sticky="ew")

        ctk.CTkButton(style_btn_frame, text="💾 保存并生效",
                      font=ctk.CTkFont(size=12, weight="bold"),
                      fg_color=WC_COLORS["accent"], hover_color=WC_COLORS["accent_hover"],
                      text_color="#FFFFFF",
                      command=self._save_reply_style).pack(side="left", padx=(0, 8), fill="x", expand=True)

        ctk.CTkButton(style_btn_frame, text="🧪 测试风格",
                      font=ctk.CTkFont(size=12),
                      fg_color=WC_COLORS["info"], hover_color=WC_COLORS["accent_hover"],
                      command=self._test_reply_style).pack(side="left", fill="x", expand=True)

        # 高级设置（默认隐藏）
        self.advanced_frame = ctk.CTkFrame(scroll, fg_color="transparent")

        # OCR设置
        ocr_frame = ctk.CTkFrame(self.advanced_frame, fg_color=WC_COLORS["card"], corner_radius=14)
        ocr_frame.pack(fill="x", padx=20, pady=(0, 20))
        ocr_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(ocr_frame, text="OCR 识别优化",
                     font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
                     text_color=WC_COLORS["text"]).grid(row=0, column=0, columnspan=2, padx=15, pady=(15, 10), sticky="w")

        wechat_cfg = self.config_data.get("wechat", {})
        ctk.CTkLabel(ocr_frame, text="识别精度 (0.5-1.0)", font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                     text_color=WC_COLORS["text_muted"]).grid(row=1, column=0, padx=15, pady=5, sticky="w")
        self.slider_scale = ctk.CTkSlider(ocr_frame, from_=0.5, to=1.0, number_of_steps=10)
        self.slider_scale.set(wechat_cfg.get("ocr_scale", 0.85))
        self.slider_scale.grid(row=1, column=1, padx=15, pady=5, sticky="ew")

        ctk.CTkLabel(ocr_frame, text="最低置信度 (0.3-0.9)", font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                     text_color=WC_COLORS["text_muted"]).grid(row=2, column=0, padx=15, pady=5, sticky="w")
        self.slider_conf = ctk.CTkSlider(ocr_frame, from_=0.3, to=0.9, number_of_steps=12)
        self.slider_conf.set(wechat_cfg.get("ocr_min_confidence", 0.60))
        self.slider_conf.grid(row=2, column=1, padx=15, pady=5, sticky="ew")

        self.switch_merge = ctk.CTkSwitch(ocr_frame, text="合并同气泡多行文本",
                                          font=ctk.CTkFont(family=FONT_FAMILY, size=12))
        if wechat_cfg.get("ocr_merge_bubble", True):
            self.switch_merge.select()
        self.switch_merge.grid(row=3, column=0, columnspan=2, padx=15, pady=5, sticky="w")

        self.switch_denoise = ctk.CTkSwitch(ocr_frame, text="图片预处理（降噪+锐化）",
                                            font=ctk.CTkFont(family=FONT_FAMILY, size=12))
        if wechat_cfg.get("ocr_denoise", True):
            self.switch_denoise.select()
        self.switch_denoise.grid(row=4, column=0, columnspan=2, padx=15, pady=5, sticky="w")

        # AI训练设置
        ai_frame = ctk.CTkFrame(scroll, fg_color=WC_COLORS["card"], corner_radius=14)
        ai_frame.pack(fill="x", padx=20, pady=10)
        ai_frame.grid_columnconfigure(1, weight=1)
        self.ai_frame = ai_frame

        ctk.CTkLabel(ai_frame, text="AI 智能学习",
                     font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
                     text_color=WC_COLORS["text"]).grid(row=0, column=0, columnspan=2, padx=15, pady=(15, 10), sticky="w")

        ctk.CTkLabel(ai_frame, text="前10次运行AI辅助学习识别规则，之后自动切换纯规则模式",
                     font=ctk.CTkFont(family=FONT_FAMILY, size=11), text_color="gray").grid(row=1, column=0, columnspan=2, padx=15, pady=(0, 10), sticky="w")

        ai_btn_frame = ctk.CTkFrame(ai_frame, fg_color="transparent")
        ai_btn_frame.grid(row=2, column=0, columnspan=2, padx=15, pady=(0, 15), sticky="w")

        ctk.CTkButton(ai_btn_frame, text="查看/编辑规则库",
                      command=self._open_rules_file).pack(side="left", padx=5)
        ctk.CTkButton(ai_btn_frame, text="重置训练",
                      command=self._reset_ai_training).pack(side="left", padx=5)
        ctk.CTkButton(ai_btn_frame, text="清除去重",
                      fg_color=WC_COLORS["warning"],
                      command=self._clear_dedup_cache).pack(side="left", padx=5)

        # ==================== Obsidian 同步设置 ====================
        obsidian_frame = ctk.CTkFrame(scroll)
        obsidian_frame.pack(fill="x", padx=10, pady=10)
        self.obsidian_frame = obsidian_frame

        ctk.CTkLabel(obsidian_frame, text="Obsidian 同步",
                    font=ctk.CTkFont(family=FONT_FAMILY, size=16, weight="bold")).pack(anchor="w", padx=15, pady=(10, 5))

        ctk.CTkLabel(obsidian_frame, text="将提取的消息同步到Obsidian vault，支持文件直写和REST API双模式",
                    font=ctk.CTkFont(family=FONT_FAMILY, size=12)).pack(anchor="w", padx=15, pady=(0, 10))

        # 启用同步
        self.obsidian_enabled_var = ctk.BooleanVar(value=self.config_data.get("obsidian", {}).get("auto_sync", False))
        ctk.CTkSwitch(obsidian_frame, text="启用自动同步",
                     variable=self.obsidian_enabled_var).pack(anchor="w", padx=15, pady=5)

        # 删除联动：删除历史时同步删除 Obsidian 笔记
        self.obsidian_delete_link_var = ctk.BooleanVar(
            value=self.config_data.get("obsidian", {}).get("delete_link", False))
        ctk.CTkSwitch(obsidian_frame, text="删除历史时联动删除 Obsidian 笔记",
                     variable=self.obsidian_delete_link_var).pack(anchor="w", padx=15, pady=5)

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
                    font=ctk.CTkFont(family=FONT_FAMILY, size=11)).pack(anchor="w", padx=15, pady=(0, 10))

        # ===== V4 高级功能开关 =====
        adv = self.config_data.get("obsidian", {}).get("advanced", {})
        filt = self.config_data.get("obsidian", {}).get("filter", {})

        self.obs_adv_vars = {}
        adv_items = [
            ("enable_bidirectional_links", "双向内部链接 ([[联系人]]/[[日期]])"),
            ("enable_tags", "元标签自动生成 (#微信-广告 等)"),
            ("enable_read", "读取Obsidian知识库作为AI回复上下文"),
            ("enable_tasks", "Tasks 待办聚合块"),
            ("enable_profile", "联系人画像独立笔记"),
            ("enable_summary", "AI 会话摘要"),
            ("enable_dataview", "笔记底部附 Dataview 查询片段"),
            ("enable_canvas", "自动维护社交图谱 Canvas"),
            ("enable_webhook", "写入后触发 webhook 回调"),
        ]
        ctk.CTkLabel(obsidian_frame, text="高级联动功能:",
                     font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold")).pack(anchor="w", padx=15, pady=(8, 2))
        for key, label in adv_items:
            var = ctk.BooleanVar(value=adv.get(key, True if key not in ("enable_read", "enable_canvas", "enable_webhook") else False))
            ctk.CTkSwitch(obsidian_frame, text=label, variable=var).pack(anchor="w", padx=25, pady=1)
            self.obs_adv_vars[key] = var

        # webhook URL
        ctk.CTkLabel(obsidian_frame, text="Webhook URL:").pack(anchor="w", padx=15, pady=(6, 0))
        self.obs_webhook_entry = ctk.CTkEntry(obsidian_frame, width=400,
                                              placeholder_text="https://your-n8n-or-script-endpoint")
        self.obs_webhook_entry.pack(fill="x", padx=15, pady=2)
        if adv.get("webhook_url"):
            self.obs_webhook_entry.insert(0, adv.get("webhook_url"))

        # 过滤规则
        ctk.CTkLabel(obsidian_frame, text="同步过滤规则:",
                     font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold")).pack(anchor="w", padx=15, pady=(10, 2))
        self.obs_filter_vars = {}
        filt_items = [
            ("skip_ads", "跳过广告消息"),
            ("skip_low_priority", "低优先级(urgency<=1)不入库"),
            ("only_tasks_or_urgent", "只同步待办/紧急消息"),
        ]
        for key, label in filt_items:
            var = ctk.BooleanVar(value=filt.get(key, False))
            ctk.CTkSwitch(obsidian_frame, text=label, variable=var).pack(anchor="w", padx=25, pady=1)
            self.obs_filter_vars[key] = var

        # ==================== 错误面板（稳定性诊断） ====================
        # 展示最近 50 条 warning/error，避免关键异常被静默吞掉、用户看不到原因
        err_frame = ctk.CTkFrame(scroll)
        err_frame.pack(fill="x", padx=15, pady=(14, 6))
        ctk.CTkLabel(err_frame, text="🛠 错误面板（最近诊断）",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=12, pady=(8, 2))
        ctk.CTkLabel(err_frame,
                     text="程序运行中出现的 warning/error 会汇总到这里，便于排查『莫名出错』。",
                     font=ctk.CTkFont(family=FONT_FAMILY, size=11), text_color=("gray60", "gray40")).pack(
            anchor="w", padx=12, pady=(0, 4))
        self.error_panel_text = ctk.CTkTextbox(err_frame, height=150, wrap="word")
        self.error_panel_text.pack(fill="x", padx=12, pady=(2, 6))
        self.error_panel_text.configure(state="disabled")
        _err_btn_row = ctk.CTkFrame(err_frame, fg_color="transparent")
        _err_btn_row.pack(anchor="w", padx=12, pady=(0, 8))
        ctk.CTkButton(_err_btn_row, text="刷新", width=90, height=30,
                      command=self._refresh_error_panel).pack(side="left", padx=(0, 8))
        ctk.CTkButton(_err_btn_row, text="清空", width=90, height=30,
                      command=self._clear_error_panel).pack(side="left")

        # 保存按钮
        ctk.CTkButton(scroll, text="保存设置", width=120, height=36,
                      font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
                      fg_color=WC_COLORS["accent"], hover_color=WC_COLORS["accent_hover"],
                      command=self.save_settings).pack(pady=(0, 20))

    # ================================================================
    # V2: 消息面板 + 消息卡片（Minecraft像素风格 + 彩色标签）
    # ================================================================

    def _pixel_stripe(self, parent, colors, height=3, side="top"):
        """
        生成像素风格顶部/底部条纹装饰（模拟MC 3px像素条）。
        colors: 颜色列表，按像素重复。例如 ["#5D8C2E", "#7DB244"]
        """
        import tkinter as tk
        from PIL import Image, ImageTk
        w = 280
        h = max(2, height)
        img = Image.new("RGB", (w, h), colors[0])
        pixels = img.load()
        for x in range(w):
            c = colors[x % len(colors)]
            for y in range(h):
                pixels[x, y] = tuple(int(c.lstrip("#")[i:i+2], 16) for i in (0, 2, 4))
        photo = ImageTk.PhotoImage(img)
        lbl = tk.Label(parent, image=photo, bd=0, highlightthickness=0)
        lbl.image = photo
        if side == "top":
            lbl.pack(side="top", fill="x")
        else:
            lbl.pack(side="bottom", fill="x")
        return lbl

    def _create_message_panel(self, parent):
        """V3: 微信PC原版消息气泡
        - 背景: 微信聊天页 #F5F5F5
        - 自己消息: 右侧绿色 #95EC69 气泡（圆角 4px，文字靠左，靠右下对齐头像）
        - 对方消息: 左侧白色 #FFFFFF 气泡
        - 群聊: 对方气泡上方显示 小字号 灰色 昵称（11px #505050）
        - 重要: 左/右侧 ⭐ 红色小标签
        - 新消息放顶部（用户偏好）。
        """
        import tkinter as tk

        parent.configure(fg_color=WC_COLORS["bg"])

        self.msg_list_frame = ctk.CTkScrollableFrame(
            parent, fg_color=WC_COLORS["bg"], corner_radius=0,
            border_width=0,
            scrollbar_button_color=WC_COLORS["border"], scrollbar_button_hover_color=WC_COLORS["text_muted"],
        )
        self.msg_list_frame.pack(fill="both", expand=True)
        self.msg_list_frame_inner = self.msg_list_frame

        # 底部消息详情卡片（点击消息弹出，再次点击或点×收起）
        self.detail_bar = ctk.CTkFrame(
            parent, fg_color=WC_COLORS["bg_dark"], corner_radius=14,
            border_width=1, border_color=WC_COLORS["border"], height=120)
        self.detail_bar.pack_forget()
        self._detail_bar_visible = False
        self._detail_bar_msg = None
        self._build_detail_bar()

        # ===== P0: AI 建议回复气泡卡 (输入栏上方, 默认隐藏) =====
        self.suggest_bar = ctk.CTkFrame(
            parent, fg_color=WC_COLORS["accent_light"], corner_radius=14,
            border_width=1, border_color=WC_COLORS["accent"], height=72)
        self.suggest_bar.pack_forget()
        self._suggest_bar_visible = False
        self._build_suggest_bar()

        # 底部装饰输入栏（macOS 风格）
        input_bar = ctk.CTkFrame(
            parent, fg_color=WC_COLORS["input_bar_bg"], height=44,
            corner_radius=0, border_width=0,
        )
        input_bar.pack(fill="x", side="bottom")
        input_bar.pack_propagate(False)

        # 工具栏图标（模拟微信底栏）
        tools = ["😊", "📎", "📁", "✂️"]
        tool_frame = ctk.CTkFrame(input_bar, fg_color="transparent")
        tool_frame.pack(side="left", padx=(12, 0), pady=6)
        for t in tools:
            tb = ctk.CTkLabel(
                tool_frame, text=t, width=28, height=28,
                font=ctk.CTkFont(family=FONT_FAMILY, size=14),
                text_color=WC_COLORS["text_muted"],
                fg_color="transparent",
            )
            tb.pack(side="left", padx=2)

        # 提示文字
        hint = ctk.CTkLabel(
            input_bar, text="NOYA Chat 微信助手 · 监控运行中",
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            text_color=WC_COLORS["text_muted2"],
        )
        hint.pack(side="right", padx=14, pady=6)
        self._input_bar_hint = hint

        # 空状态（空的微信聊天小提示）
        self.msg_empty_label = ctk.CTkFrame(
            self.msg_list_frame_inner, fg_color="transparent")
        self.msg_empty_label.pack(pady=36, padx=20, fill="x")

        ctk.CTkLabel(
            self.msg_empty_label,
            text="暂无新消息",
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            text_color=WC_COLORS["text_muted"],
        ).pack(pady=(0, 6))
        ctk.CTkLabel(
            self.msg_empty_label,
            text="开启监控后，这里会实时显示识别到的聊天消息",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=WC_COLORS["text_muted2"],
            wraplength=420, justify="center",
        ).pack()

    # ==============================================================
    # V3: 右侧详情面板（关键词/提取字段/摘要/置信度）
    # ==============================================================
    def _build_detail_bar(self):
        """底部消息详情卡片（横向紧凑布局，点击消息弹出）"""
        bar = self.detail_bar
        bar.grid_columnconfigure(0, weight=1)
        bar.grid_columnconfigure(1, weight=0)
        bar.grid_rowconfigure(0, weight=0)
        bar.grid_rowconfigure(1, weight=1)

        # 顶行：标题 + 关闭按钮
        top = ctk.CTkFrame(bar, fg_color="transparent", height=28)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(6, 0))
        top.grid_propagate(False)
        self.detail_bar_title = ctk.CTkLabel(
            top, text="📋 消息详情",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=WC_COLORS["text"])
        self.detail_bar_title.pack(side="left")
        close_btn = ctk.CTkLabel(
            top, text="✕", width=24, height=24,
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=WC_COLORS["text_muted"], cursor="hand2")
        close_btn.pack(side="right")
        close_btn.bind("<Button-1>", lambda e: self._hide_detail_bar())

        # 内容行：横向4格信息 + 标签
        content = ctk.CTkFrame(bar, fg_color="transparent")
        content.grid(row=1, column=0, sticky="nsew", padx=10, pady=4)
        content.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="dc")

        self._detail_labels = {}
        info_rows = [
            ("contact", "会话"),
            ("sender", "发送方"),
            ("time", "时间"),
            ("conf", "置信度"),
        ]
        for i, (key, name) in enumerate(info_rows):
            cell = ctk.CTkFrame(content, fg_color="transparent")
            cell.grid(row=0, column=i, sticky="nsew", padx=2)
            ctk.CTkLabel(cell, text=name,
                         font=ctk.CTkFont(family=FONT_FAMILY, size=9),
                         text_color=WC_COLORS["text_muted"]).pack(anchor="w")
            val = ctk.CTkLabel(cell, text="—",
                               font=ctk.CTkFont(family=FONT_FAMILY, size=10, weight="bold"),
                               text_color=WC_COLORS["text"], anchor="w")
            val.pack(anchor="w", fill="x")
            self._detail_labels[key] = val

        # 标签区（横向）
        self.detail_tags_frame = ctk.CTkFrame(content, fg_color="transparent")
        self.detail_tags_frame.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(4, 0))
        self._detail_tags_placeholder = ctk.CTkLabel(
            self.detail_tags_frame, text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=10), text_color=WC_COLORS["text_muted"])
        self._detail_tags_placeholder.pack(anchor="w")

        # 保持兼容：detail_fields_frame 也指向同一个标签区
        self.detail_fields_frame = self.detail_tags_frame
        self._detail_content_label = self._detail_tags_placeholder

    def _show_detail_bar(self, msg_data):
        """弹出底部详情卡片：如果是对方消息，异步触发 AI 建议回复"""
        self._detail_bar_msg = msg_data
        try:
            self.detail_bar.pack(fill="x", side="bottom", before=self._input_bar_hint.master)
        except Exception:
            self.detail_bar.pack(fill="x", side="bottom")
        self._detail_bar_visible = True
        self._update_detail_panel(msg_data)
        # ===== P0: 对方消息 → 异步请求 AI 建议回复 =====
        try:
            sender = msg_data.get("sender", "other")
            contact = msg_data.get("contact")
            content = str(msg_data.get("content", "") or "").strip()
            if sender != "me" and contact and content and len(content) >= 2:
                self._async_gen_suggestion(contact, content)
        except Exception:
            pass

    def _hide_detail_bar(self):
        """收起底部详情卡片"""
        self.detail_bar.pack_forget()
        self._detail_bar_visible = False
        self._detail_bar_msg = None

    # ===== P0: AI 建议回复气泡卡相关方法 =====
    def _build_suggest_bar(self):
        bar = self.suggest_bar
        bar.grid_columnconfigure(0, weight=1)
        bar.grid_rowconfigure(1, weight=1)
        top = ctk.CTkFrame(bar, fg_color="transparent", height=24)
        top.grid(row=0, column=0, sticky="ew", padx=10, pady=(6, 0))
        top.grid_propagate(False)
        self.suggest_bar_title = ctk.CTkLabel(top, text="🤖 AI 建议回复",
            font=ctk.CTkFont(size=11, weight="bold"), text_color=WC_COLORS["accent"])
        self.suggest_bar_title.pack(side="left")
        close_btn = ctk.CTkLabel(top, text="✕", width=20, height=20,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=WC_COLORS["text_muted"], cursor="hand2")
        close_btn.pack(side="right")
        close_btn.bind("<Button-1>", lambda e: self._hide_suggest_bar())
        body_wrap = ctk.CTkFrame(bar, fg_color="transparent")
        body_wrap.grid(row=1, column=0, sticky="nsew", padx=10, pady=(2, 6))
        body_wrap.grid_columnconfigure(0, weight=1)
        self.suggest_text = ctk.CTkLabel(body_wrap, text="—",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12), text_color=WC_COLORS["text"],
            anchor="w", justify="left", wraplength=720)
        self.suggest_text.grid(row=0, column=0, sticky="w")
        btn_row = ctk.CTkFrame(body_wrap, fg_color="transparent")
        btn_row.grid(row=0, column=1, sticky="e", padx=(8, 0))
        self.suggest_copy_btn = ctk.CTkButton(btn_row, text="📋 复制",
            width=60, height=26, corner_radius=8,
            fg_color=WC_COLORS["accent"], text_color="#FFFFFF",
            hover_color=WC_COLORS["accent_hover"],
            font=ctk.CTkFont(size=10, weight="bold"),
            command=self._copy_suggestion)
        self.suggest_copy_btn.pack(side="left", padx=2)
        self.suggest_regen_btn = ctk.CTkButton(btn_row, text="🔄 重新生成",
            width=84, height=26, corner_radius=8,
            fg_color=WC_COLORS["card"], text_color=WC_COLORS["text"],
            hover_color=WC_COLORS["card_hover"], border_width=1,
            border_color=WC_COLORS["border"],
            font=ctk.CTkFont(size=10),
            command=self._regen_suggestion)
        self.suggest_regen_btn.pack(side="left", padx=2)

    def _show_suggest_bar(self, text, contact=None):
        try:
            self._suggest_bar_text = (text or "").strip()
            self._suggest_bar_contact = contact or self._active_contact
            if not self._suggest_bar_text: return
            display = self._suggest_bar_text
            if len(display) > 280: display = display[:280] + "…"
            self.suggest_text.configure(text=display)
            if not self._suggest_bar_visible:
                try: self.suggest_bar.pack(fill="x", side="bottom",
                        before=self._input_bar_hint.master, padx=8, pady=(0, 4))
                except Exception:
                    self.suggest_bar.pack(fill="x", side="bottom", padx=8, pady=(0, 4))
                self._suggest_bar_visible = True
        except Exception as e:
            try: self._on_log("warning", f"[建议回复] 显示失败: {e}")
            except Exception: pass

    def _hide_suggest_bar(self):
        try: self.suggest_bar.pack_forget()
        except Exception: pass
        self._suggest_bar_visible = False
        self._suggest_bar_text = None
        self._suggest_bar_contact = None

    def _copy_suggestion(self):
        text = getattr(self, "_suggest_bar_text", "") or ""
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.update()
            self.suggest_copy_btn.configure(text="✅ 已复制")
            self.after(1200, lambda: self.suggest_copy_btn.configure(text="📋 复制"))
            self._on_log("info", f"[建议回复] 已复制到剪贴板 ({len(text)}字)")

    def _regen_suggestion(self):
        contact = getattr(self, "_suggest_bar_contact", None) or self._active_contact
        if not contact or contact == self._contact_filter_all:
            self._on_log("warning", "[建议回复] 请先在左侧选中一个具体联系人"); return
        msgs = self._load_conversation(contact)
        last_other = None
        for m in msgs:
            if m.get("sender") != "me": last_other = m
        if not last_other:
            self._on_log("warning", "[建议回复] 该联系人还没有对方消息"); return
        self._hide_suggest_bar()
        self._on_log("info", f"[建议回复] 正在为「{contact}」重新生成...")
        self._async_gen_suggestion(contact, last_other.get("content", ""))

    def _async_gen_suggestion(self, contact, content):
        _eng = getattr(self, "engine", None)
        if not _eng or not _eng.is_running():
            self._on_log("warning", "[建议回复] 监控已停止，请先启动监控再生成"); return
        if not getattr(_eng, "llm_client", None):
            self._on_log("warning", "[建议回复] LLM 未初始化，请先启动监控"); return
        import threading
        def _worker():
            try:
                role = self.engine.role_manager.get_role_for(contact)
                ctx_mapped = []
                ctx = self.get_conversation_context(contact) or []
                for m in ctx[-10:]:
                    if isinstance(m, dict):
                        role_side = "assistant" if m.get("sender") == "me" else "user"
                        c = str(m.get("content", "")).strip()
                    else:
                        c = str(m).strip()
                        role_side = "user"
                    if c: ctx_mapped.append({"role": role_side, "content": c})
                reply = self.engine.llm_client.generate_reply(contact, content, role, ctx_mapped)
                if reply:
                    self.after(0, lambda r=reply: self._show_suggest_bar(r, contact))
                else:
                    self.after(0, lambda: self._on_log("warning", "[建议回复] LLM 返回为空"))
            except Exception as e:
                self.after(0, lambda: self._on_log("error", f"[建议回复] 生成失败: {e}"))
        threading.Thread(target=_worker, daemon=True, name="suggest-gen").start()

    # ===== P0: 4 个全局快捷键方法 =====
    def _hotkey_focus_search(self, _event=None):
        try:
            self._switch_nav("monitor")
            self.contact_search_entry.focus_set()
            self.contact_search_entry.select_range(0, "end")
            self._append_log("debug", "[快捷键] Ctrl+F → 搜索框聚焦")
        except Exception: pass
        return "break"

    def _hotkey_toggle_important(self, _event=None):
        try:
            c = self._active_contact
            if not c or c == self._contact_filter_all:
                self._append_log("warning", "[快捷键] 请先在左侧选择联系人 (Ctrl+D)"); return
            cur = False
            msgs = self._load_conversation(c)
            if msgs: cur = bool(msgs[0].get("is_important", False))
            new_val = not cur
            for m in msgs:
                m["is_important"] = new_val
                if new_val and not m.get("importance_reason"):
                    m["importance_reason"] = "Ctrl+D 手动标记为重要"
            self._history_dirty = True
            try: self._flush_history()
            except Exception: pass
            self._rebuild_contact_list()
            badge = "⭐ 重要" if new_val else "已取消重要"
            self._append_log("info", f"[快捷键] Ctrl+D → 「{c}」{badge}")
            try: self.chat_title.configure(text=f"{c} {' ⭐' if new_val else ''}")
            except Exception: pass
        except Exception as e:
            try: self._append_log("error", f"[快捷键] Ctrl+D 失败: {e}")
            except Exception: pass
        return "break"

    def _hotkey_goto_settings(self, _event=None):
        try:
            self._switch_nav("settings")
            self._append_log("debug", "[快捷键] Ctrl+Shift+S → 设置页")
        except Exception: pass
        return "break"

    def _hotkey_minimize_or_close(self, _event=None):
        try:
            if getattr(self, "_suggest_bar_visible", False):
                self._hide_suggest_bar(); return "break"
            if getattr(self, "_detail_bar_visible", False):
                self._hide_detail_bar(); return "break"
            self.iconify()
            self._append_log("debug", "[快捷键] Esc → 窗口最小化")
        except Exception: pass
        return "break"

    def _create_main_preview(self, parent):
        """V3: 主界面截图预览区（轻量嵌入中栏）"""
        import tkinter as tk
        from PIL import Image, ImageTk

        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(parent, fg_color=WC_COLORS["header"], height=32)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)
        header.grid_columnconfigure(0, weight=1)

        self.main_preview_title = ctk.CTkLabel(
            header, text="🖥️ 实时画面",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=WC_COLORS["text"], anchor="w",
        )
        self.main_preview_title.grid(row=0, column=0, padx=10, sticky="w")
        self.main_preview_status = ctk.CTkLabel(
            header, text="等待截图…",
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            text_color=WC_COLORS["text_muted"], anchor="e",
        )
        self.main_preview_status.grid(row=0, column=1, padx=10, sticky="e")

        self.main_preview_label = tk.Label(
            parent, bg=WC_COLORS["bg_dark"],
            highlightbackground=WC_COLORS["border"], highlightthickness=1)
        self.main_preview_label.grid(row=1, column=0, sticky="nsew", padx=8, pady=8)
        try:
            placeholder = Image.new("RGB", (480, 320), color=WC_COLORS["bg"])
            self._main_placeholder_photo = ImageTk.PhotoImage(placeholder)
            self.main_preview_label.configure(image=self._main_placeholder_photo)
            self.main_preview_label.image = self._main_placeholder_photo
        except Exception:
            pass

    def _on_new_message(self, msg_data):
        """新消息回调（线程安全 + 上下文管理）"""
        try:
            contact = msg_data.get("contact", "?")
            content_text = str(msg_data.get("content", ""))[:30]
            self._on_log("info", f"[UI] 收到新消息回调: {contact}: {content_text}")

            if getattr(self, "_context_enabled", False) and contact and contact != self._contact_filter_all:
                if contact not in self._conv_context:
                    self._conv_context[contact] = []
                self._conv_context[contact].append({
                    "sender": msg_data.get("sender", "other"),
                    "content": msg_data.get("content", ""),
                    "timestamp": msg_data.get("timestamp", ""),
                    "keywords": msg_data.get("matched_keywords", []),
                })
                _max_turns = getattr(self, "_context_max_turns", 10)
                maxlen = _max_turns * 2
                if len(self._conv_context[contact]) > maxlen:
                    self._conv_context[contact] = self._conv_context[contact][-maxlen:]

            self.after(0, lambda d=dict(msg_data): self._add_message_card(d))
        except Exception as e:
            print(f"[实时消息] 回调调度失败: {e}")

    def get_conversation_context(self, contact):
        """获取指定联系人的对话上下文"""
        return self._conv_context.get(contact, [])

    def clear_conversation_context(self, contact=None):
        """清空对话上下文"""
        if contact:
            self._conv_context.pop(contact, None)
        else:
            self._conv_context.clear()
        self._on_log("info", f"[上下文] 已清空{' ' + contact if contact else '全部'}对话上下文")

    def _set_context_enabled(self, enabled):
        """开关对话上下文"""
        self._context_enabled = enabled
        self._on_log("info", f"[上下文] 对话上下文已{'开启' if enabled else '关闭'}")

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

    def _build_tag_chip(self, parent, text, color_bg, color_fg, icon=""):
        """V2: 彩色标签小胶囊（关键词/重要/提取信息/摘要）"""
        txt = f"{icon} {text}" if icon else text
        chip = ctk.CTkLabel(
            parent, text=txt,
            fg_color=color_bg, text_color=color_fg, corner_radius=4,
            font=ctk.CTkFont(family=FONT_FAMILY, size=10, weight="bold"),
            padx=6, pady=2,
        )
        return chip

    def _highlight_content(self, content, msg_data):
        """
        V2: 在纯文本内容中按规则追加彩色标签（关键词用橙色、提取信息用蓝色、重要原因用红色）。
        因为CTkLabel不支持inline富文本，我们在"标签条"里以chip形式展示。
        """
        tags = []
        # 重要消息
        if msg_data.get("is_important"):
            tags.append(("❗ 重要", WC_COLORS["danger"], "#FFFFFF"))
        # 重要原因
        if msg_data.get("is_important") and msg_data.get("importance_reason"):
            reason = str(msg_data["importance_reason"])[:24]
            tags.append((f"📌 {reason}", WC_COLORS["danger_light"], "#FFFFFF"))
        # 关键词分类（橙色）
        cats = msg_data.get("keyword_categories") or msg_data.get("keywords")
        if isinstance(cats, list) and cats:
            cat_text = "、".join([str(c) for c in cats[:3]])
            if len(cats) > 3:
                cat_text += f"+{len(cats)-3}"
            tags.append((f"🏷 {cat_text}", WC_COLORS["keyword"], "#FFFFFF"))
        # 分类优先级规则（蓝色）：classification + priority
        cls = msg_data.get("classification") or ""
        if cls:
            pri = msg_data.get("priority", 0) or 0
            cls_text = f"🗂 {cls}"
            if pri:
                cls_text += f" P{int(pri)}"
            tags.append((cls_text, WC_COLORS["info"], "#FFFFFF"))
        # 提取信息（蓝色）
        extracted = msg_data.get("extracted_fields") or msg_data.get("extracted")
        if extracted and isinstance(extracted, dict):
            for k, v in list(extracted.items())[:2]:
                val = str(v)[:16]
                tags.append((f"💎 {k}: {val}", WC_COLORS["info"], "#FFFFFF"))
        # LLM摘要（紫色）
        llm = msg_data.get("llm_analysis") or {}
        if isinstance(llm, dict) and llm.get("summary"):
            tags.append((f"📝 摘要: {str(llm['summary'])[:24]}",
                         WC_COLORS["summary"], "#FFFFFF"))
        # 置信度（低置信度打黄色标签，提示OCR不确定）
        conf = msg_data.get("confidence")
        if conf is not None and isinstance(conf, (int, float)) and conf < 0.7:
            tags.append((f"⚠ OCR {conf:.0%}", WC_COLORS["warning"], "#2B1E10"))
        return tags

    def _add_message_card_impl(self, msg_data):
        """
        V3: 微信 PC 原版气泡（左白右绿，群聊上方显示群成员名）
        - 最新消息按微信风格显示在底部。
        - 同时更新左侧会话列表卡、右侧详情面板。
        """
        import tkinter as tk

        # 移除空状态
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
        content = str(msg_data.get("content", ""))
        timestamp = msg_data.get("timestamp", datetime.now().strftime("%H:%M:%S"))
        is_important = bool(msg_data.get("is_important", False))
        is_self = (sender == "me")
        is_group = bool(msg_data.get("is_group", False))
        group_member = msg_data.get("group_member") or None
        confidence = msg_data.get("confidence")

        # —— 更新左侧会话卡（新消息置为 active + 未读+1 if 非active，或更新 preview）
        try:
            short_preview = content.replace("\n", " ")[:26]
            if is_self:
                short_preview = f"我: {short_preview}"
            elif group_member:
                short_preview = f"{group_member}: {short_preview}"
            else:
                short_preview = f"{contact}: {short_preview}"
            self._append_contact_card(
                contact, short_preview,
                is_group=is_group,
                unread=0 if self._active_contact == contact else 1,
                active=(self._active_contact is None),
            )
        except Exception:
            pass

        # —— 存入按联系人划分的消息池（最新在前，单联系人上限 80）——
        # msg_key 去重：同一 msg_key（基础卡/更新卡、重复上报）原位更新，不新增
        # 内容级兜底去重：红点路径/主循环路径可能因 timestamp 不同生成不同
        # msg_key，导致同一条消息建两张卡。用 (contact|sender|content) + 10s
        # 时间窗兜底，重复上报只更新不新建。
        import time as _t
        _now = _t.time()
        if not hasattr(self, "_recent_ui_keys"):
            self._recent_ui_keys = {}
        _ckey = f"{contact}|{sender}|{content}"
        _last = self._recent_ui_keys.get(_ckey)
        if _last is not None and (_now - _last) < 10.0:
            # 内容级兜底去重：同一消息在10s内重复上报，优先用 msg_key（哈希）保证与首次存储的索引键一致
            mk = msg_data.get("msg_key") or _ckey
        else:
            self._recent_ui_keys[_ckey] = _now
            mk = msg_data.get("msg_key") or _ckey
        store = self._messages_store.setdefault(contact, [])
        if not hasattr(self, "_msg_index"):
            self._msg_index = {}
        contact_idx = self._msg_index.setdefault(contact, {})
        if mk:
            _i = contact_idx.get(mk)
            if _i is not None and _i < len(store):
                _m = store[_i]
                merged = dict(_m)
                merged.update(msg_data)
                merged["_seq"] = _m.get("_seq")
                store[_i] = merged
                try:
                    self._update_detail_panel(merged)
                except Exception:
                    pass
                self._schedule_history_save()
                try:
                    self._refresh_bubble_in_place(contact, mk, merged)
                except Exception:
                    self._rebuild_message_list()
                return
        self._msg_seq += 1
        stored = dict(msg_data)
        stored["_seq"] = self._msg_seq
        store.insert(0, stored)
        if mk and hasattr(self, "_msg_index"):
            ci = self._msg_index.setdefault(contact, {})
            ci[mk] = 0
            for idx, m in enumerate(store):
                k = m.get("msg_key")
                if k:
                    ci[k] = idx
        if len(store) > 500:
            del store[500:]
        # —— 维护会话索引（轻量，供左栏卡片/统计使用；不承载正文）——
        try:
            _ci = self._conv_index.setdefault(contact, {
                "count": 0, "preview": "", "last_sender": sender,
                "last_time": timestamp, "unread": 0, "is_group": is_group,
            })
            _ci["count"] = _ci.get("count", 0) + 1
            _ci["preview"] = content.replace("\n", " ")[:26]
            _ci["last_sender"] = sender
            _ci["last_time"] = timestamp or _ci.get("last_time", "")
            _ci["is_group"] = bool(_ci.get("is_group", False)) or is_group
            if self._active_contact != contact:
                _ci["unread"] = _ci.get("unread", 0) + 1
        except Exception:
            pass
        # —— “全部会话”实时新增（切换回去时从 db 重载，避免重复）——
        try:
            _is_all_now = (self._active_contact is None) or (self._active_contact == self._contact_filter_all)
            if _is_all_now:
                self._all_messages_live.append(dict(stored))
        except Exception:
            pass
        self._schedule_history_save()

        # —— 更新右侧详情面板
        try:
            self._update_detail_panel(msg_data)
        except Exception:
            pass

        # —— 更新主聊天顶栏（chat_subtitle）
        if hasattr(self, 'chat_subtitle'):
            status_color = WC_COLORS["danger"] if is_important else WC_COLORS["online_dot"]
            tag = "（重要）" if is_important else ""
            self.chat_subtitle.configure(
                text=f"● 最新: {contact} · {timestamp}{tag}",
                text_color=status_color,
            )

        # —— 自动弹出：收到对方(非自己)新消息且当前不在该会话时，把会话切到前台 ——
        # 像微信一样弹到主聊天区，避免"卡片出来了但主区看不到消息"。自己发的(me)不弹，
        # 全部会话视图(is_all)本就显示、也不弹（不打断当前视图）。
        auto_popped = False
        if sender != "me" and self._active_contact not in (None, contact, self._contact_filter_all):
            try:
                self._set_active_contact(contact)   # 重建主区(已包含本条气泡)
                auto_popped = True
            except Exception:
                pass

        # —— 增量插入新气泡（微信风格：底部插入，不重建）——
        try:
            active = self._active_contact
            is_all = (active is None) or (active == self._contact_filter_all)
            is_shown = is_all or (active == contact)
            if is_shown and not auto_popped:
                # 增量插入到底部，不触发全量重建
                # （若本回合已自动切到该会话，_set_active_contact 重建时已含本条，跳过避免重复气泡）
                self._append_message_row(stored)
                try:
                    self.msg_list_frame._parent_canvas.yview_moveto(1.0)
                except Exception:
                    pass
            # 不重建：只有用户切换会话时 _set_active_contact 才会重建
        except Exception:
            pass


    # ==============================================================
    # V3: 详情面板内容更新
    # ==============================================================
    def _update_detail_panel(self, msg_data):
        if not getattr(self, "_detail_labels", None):
            return

        contact = msg_data.get("contact", "未知")
        sender = msg_data.get("sender", "other")
        is_group = bool(msg_data.get("is_group", False))
        group_member = msg_data.get("group_member") or None
        timestamp = msg_data.get("timestamp", "—")
        confidence = msg_data.get("confidence")
        content = str(msg_data.get("content", ""))

        # 基础信息
        self._detail_labels["contact"].configure(
            text=(f"{contact}  {'（👥群聊）' if is_group else '（👤私聊）'}"))
        if sender == "me":
            self._detail_labels["sender"].configure(text="🟢 我（自己发出）")
        elif is_group and group_member:
            self._detail_labels["sender"].configure(text=f"👥 群成员 · {group_member}")
        else:
            self._detail_labels["sender"].configure(text=f"👤 对方 · {contact}")
        self._detail_labels["time"].configure(text=str(timestamp))

        if confidence is not None and isinstance(confidence, (int, float)):
            pct = f"{confidence * 100:.0f}%"
            col = WC_COLORS["danger"] if confidence < 0.6 else (
                WC_COLORS["warning"] if confidence < 0.75 else WC_COLORS["accent"])
            self._detail_labels["conf"].configure(text=pct, text_color=col)
        else:
            self._detail_labels["conf"].configure(text="—")

        # 标签区：重绘所有chip
        for w in self.detail_tags_frame.winfo_children():
            try:
                w.destroy()
            except Exception:
                pass
        tags = self._highlight_content(content, msg_data)
        if not tags:
            placeholder = ctk.CTkLabel(
                self.detail_tags_frame, text="(无特殊标签)",
                font=ctk.CTkFont(family=FONT_FAMILY, size=10), text_color=WC_COLORS["text_muted"])
            placeholder.pack(padx=10, pady=8, anchor="w")
        else:
            tags_wrap = ctk.CTkFrame(self.detail_tags_frame, fg_color="transparent")
            tags_wrap.pack(fill="x", padx=8, pady=6)
            for i, (txt, bg, fg) in enumerate(tags):
                chip = ctk.CTkLabel(tags_wrap, text=txt, fg_color=bg, text_color=fg,
                                    corner_radius=4, padx=6, pady=2,
                                    font=ctk.CTkFont(family=FONT_FAMILY, size=10, weight="bold"))
                chip.grid(row=i // 3, column=i % 3, padx=2, pady=2, sticky="w")

        # 提取字段区：正文 + 字段 + 摘要
        for w in self.detail_fields_frame.winfo_children():
            try:
                w.destroy()
            except Exception:
                pass

        ctk.CTkLabel(self.detail_fields_frame,
                     text="正文：",
                     font=ctk.CTkFont(family=FONT_FAMILY, size=10, weight="bold"),
                     text_color=WC_COLORS["text_muted"], anchor="w").pack(
            padx=10, pady=(8, 2), fill="x")
        content_label = ctk.CTkLabel(
            self.detail_fields_frame, text=content[:1200],
            font=ctk.CTkFont(family=FONT_FAMILY, size=10), text_color=WC_COLORS["text"],
            wraplength=240, anchor="w", justify="left",
        )
        content_label.pack(padx=10, pady=(0, 6), anchor="w", fill="x")

        extracted = msg_data.get("extracted_fields") or msg_data.get("extracted") or {}
        if extracted and isinstance(extracted, dict):
            ctk.CTkLabel(self.detail_fields_frame,
                         text="字段：",
                         font=ctk.CTkFont(family=FONT_FAMILY, size=10, weight="bold"),
                         text_color=WC_COLORS["text_muted"], anchor="w").pack(
                padx=10, pady=(2, 2), fill="x")
            for k, v in extracted.items():
                rowf = ctk.CTkFrame(self.detail_fields_frame, fg_color="transparent")
                rowf.pack(fill="x", padx=10, pady=1)
                ctk.CTkLabel(rowf, text=f"{k}:",
                             font=ctk.CTkFont(family=FONT_FAMILY, size=10, weight="bold"),
                             text_color=WC_COLORS["info"], anchor="w", width=60).pack(
                    side="left")
                ctk.CTkLabel(rowf, text=str(v)[:60],
                             font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                             text_color=WC_COLORS["text"],
                             anchor="w", justify="left", wraplength=180).pack(
                    side="left", fill="x", expand=True)

        llm = msg_data.get("llm_analysis") or {}
        if isinstance(llm, dict) and llm.get("summary"):
            ctk.CTkLabel(self.detail_fields_frame,
                         text="摘要：",
                         font=ctk.CTkFont(family=FONT_FAMILY, size=10, weight="bold"),
                         text_color=WC_COLORS["text_muted"], anchor="w").pack(
                padx=10, pady=(6, 2), fill="x")
            ctk.CTkLabel(self.detail_fields_frame,
                         text=str(llm["summary"])[:600],
                         font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                         text_color=WC_COLORS["summary"],
                         wraplength=240, anchor="w", justify="left").pack(
                padx=10, pady=(0, 8), fill="x", anchor="w")

    def _create_search_panel(self, parent):
        """创建搜索面板"""
        search_frame = ctk.CTkFrame(parent)
        search_frame.pack(fill="x", padx=10, pady=5)

        ctk.CTkLabel(search_frame, text="🔍 搜索:").pack(side="left", padx=5)
        self.search_entry_all = ctk.CTkEntry(search_frame, width=200, placeholder_text="搜索联系人或消息内容...")
        self.search_entry_all.pack(side="left", padx=5)

        search_btn = ctk.CTkButton(search_frame, text="搜索", width=60, command=self._do_search)
        search_btn.pack(side="left", padx=5)

        self.search_results_frame = ctk.CTkScrollableFrame(parent, height=200)
        self.search_results_frame.pack(fill="both", expand=True, padx=10, pady=5)

    def _do_search(self):
        keyword = self.search_entry_all.get().strip()
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
                            font=ctk.CTkFont(family=FONT_FAMILY, size=12)).pack(anchor="w", padx=8, pady=3)
            if not results:
                ctk.CTkLabel(self.search_results_frame, text="未找到匹配消息").pack(pady=10)

    def _create_stats_panel(self, parent):
        """创建统计面板：外层可滚动，指标卡片网格 + 炫酷图表 + 可滚动详情。"""
        # 外层可滚动容器（彻底解决"无法下滑看不到"）
        scroll = ctk.CTkScrollableFrame(parent, fg_color="transparent",
                                        corner_radius=0)
        scroll.pack(fill="both", expand=True, padx=8, pady=8)
        scroll.grid_columnconfigure(0, weight=1)

        # 标题
        ctk.CTkLabel(scroll, text="📊 数据统计",
                     font=ctk.CTkFont(size=16, weight="bold"),
                     text_color=WC_COLORS["text"]).pack(anchor="w", padx=6, pady=(4, 10))

        # 指标卡片网格（2列）
        self.stats_labels = {}
        stat_items = [
            ("总消息", "💬", WC_COLORS["accent"]),
            ("重要消息", "⭐", WC_COLORS["danger"]),
            ("联系人数", "👤", "#4CAF50"),
            ("提取信息", "📋", "#FF9800"),
            ("今日消息", "📅", "#2196F3"),
            ("本周消息", "📆", "#9C27B0"),
            ("已回复", "✉️", "#00BCD4"),
            ("OCR次数", "🔤", "#795548"),
        ]
        grid = ctk.CTkFrame(scroll, fg_color="transparent")
        grid.pack(fill="x", padx=2, pady=(0, 8))
        grid.grid_columnconfigure(0, weight=1)
        grid.grid_columnconfigure(1, weight=1)
        for i, (label, icon, color) in enumerate(stat_items):
            card = ctk.CTkFrame(grid, fg_color=WC_COLORS["card"], corner_radius=14,
                                border_width=1, border_color=WC_COLORS["border"])
            r, c = divmod(i, 2)
            card.grid(row=r, column=c, padx=5, pady=5, sticky="nsew")
            ctk.CTkLabel(card, text=f"{icon} {label}",
                         font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                         text_color=WC_COLORS["text_muted"]).pack(anchor="w", padx=12, pady=(10, 0))
            val_label = ctk.CTkLabel(card, text="—", font=ctk.CTkFont(family=FONT_FAMILY, size=22, weight="bold"),
                                     text_color=color)
            val_label.pack(anchor="w", padx=12, pady=(2, 10))
            self.stats_labels[label] = val_label

        # 发送者分布
        row2 = ctk.CTkFrame(scroll, fg_color=WC_COLORS["card"], corner_radius=14,
                           border_width=1, border_color=WC_COLORS["border"])
        row2.pack(fill="x", padx=2, pady=6)
        ctk.CTkLabel(row2, text="👥 发送者分布", font=ctk.CTkFont(size=11),
                     text_color=WC_COLORS["text_muted"]).pack(anchor="w", padx=12, pady=(8, 0))
        self.stats_sender_label = ctk.CTkLabel(row2, text="—",
                                                font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                                                text_color=WC_COLORS["text"],
                                                wraplength=420, justify="left")
        self.stats_sender_label.pack(anchor="w", padx=12, pady=(2, 10))

        # 按钮行
        btn_row = ctk.CTkFrame(scroll, fg_color="transparent")
        btn_row.pack(fill="x", padx=2, pady=6)
        ctk.CTkButton(btn_row, text="🔄 刷新统计", height=34,
                      fg_color=WC_COLORS["accent"], hover_color=WC_COLORS["accent_hover"],
                      command=self._refresh_stats).pack(side="left", padx=3)
        ctk.CTkButton(btn_row, text="📋 今日报告", height=34,
                      command=self._generate_today_report).pack(side="left", padx=3)

        # 可视化图表区
        ctk.CTkLabel(scroll, text="📈 可视化统计",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=WC_COLORS["text"]).pack(anchor="w", padx=6, pady=(8, 4))
        self.stats_chart = ctk.CTkLabel(scroll, text="（点击「刷新统计」生成图表）",
                                        fg_color=WC_COLORS["card"], corner_radius=14,
                                        height=240)
        self.stats_chart.pack(fill="x", padx=2, pady=(0, 10))

        # 详情文本框
        ctk.CTkLabel(scroll, text="📝 详细数据",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=WC_COLORS["text"]).pack(anchor="w", padx=6, pady=(4, 4))
        self.stats_detail = ctk.CTkTextbox(scroll, height=160, font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                                           wrap="word", fg_color=WC_COLORS["card"],
                                           border_width=1, border_color=WC_COLORS["border"])
        self.stats_detail.pack(fill="x", padx=2, pady=(0, 10))
        self.stats_detail.insert("1.0", "点击「刷新统计」查看详细数据...")
        self.stats_detail.configure(state="disabled")

    def _refresh_stats(self):
        """刷新数据统计面板（增强版：多维度 + 详情 + 可视化 + 容错）。
        V3.1: 不依赖引擎——无引擎时用独立存储(_get_storage)统计历史数据。
        """
        try:
            has_engine = bool(getattr(self, 'engine', None))

            # 默认为0
            for k in self.stats_labels:
                self.stats_labels[k].configure(text="0")

            detail_lines = []

            # 1. 存储统计（SQLite 数据库，可独立于引擎）
            storage = self._get_storage()
            if storage:
                try:
                    s = storage.get_stats()
                    if s and s.get("total_messages", 0) > 0:
                        self.stats_labels["总消息"].configure(text=str(s.get("total_messages", 0)))
                        self.stats_labels["重要消息"].configure(text=str(s.get("important_messages", 0)))
                        self.stats_labels["联系人数"].configure(text=str(s.get("total_contacts", 0)))
                        self.stats_labels["今日消息"].configure(text=str(s.get("today_messages", 0)))
                        self.stats_labels["本周消息"].configure(text=str(s.get("week_messages", 0)))
                        detail_lines.append(f"📊 数据库统计 (更新: {s.get('last_updated', '—')})")
                        detail_lines.append(f"  总消息: {s['total_messages']}  重要: {s['important_messages']}")
                        detail_lines.append(f"  联系人: {s['total_contacts']}  今日: {s['today_messages']}  本周: {s['week_messages']}")
                        senders = s.get("sender_distribution", {})
                        if senders:
                            top = sorted(senders.items(), key=lambda x: x[1], reverse=True)[:5]
                            sender_text = ", ".join([f"{k}:{v}" for k, v in top])
                            self.stats_sender_label.configure(text=sender_text[:60])
                            detail_lines.append(f"  发送者Top5: {sender_text}")
                        else:
                            self.stats_sender_label.configure(text="—")
                        detail_lines.append("")
                    else:
                        detail_lines.append("📊 数据库: 暂无消息记录")
                        detail_lines.append("")
                except Exception as e:
                    detail_lines.append(f"⚠️ 数据库统计异常: {e}")
                    detail_lines.append("")
            else:
                detail_lines.append("⚠️ 存储模块未初始化")
                detail_lines.append("")

            # 2. 引擎运行时统计（实时数据，仅有时可用）
            if has_engine:
                try:
                    e = self.engine.get_stats()
                    self.stats_labels["提取信息"].configure(text=str(e.get("extracted", 0)))
                    self.stats_labels["已回复"].configure(text=str(e.get("replies_sent", 0)))
                    self.stats_labels["OCR次数"].configure(text=str(e.get("ocr_calls", 0)))
                    detail_lines.append("⚡ 引擎实时统计")
                    detail_lines.append(f"  截图帧数: {e.get('frames_captured', 0)}  OCR调用: {e.get('ocr_calls', 0)}")
                    detail_lines.append(f"  检测消息: {e.get('messages_detected', 0)}  提取信息: {e.get('extracted', 0)}")
                    detail_lines.append(f"  重要消息: {e.get('important', 0)}  已回复: {e.get('replies_sent', 0)}")
                    detail_lines.append("")
                except Exception as e2:
                    detail_lines.append(f"⚠️ 引擎统计异常: {e2}")
                    detail_lines.append("")
            else:
                self.stats_labels["提取信息"].configure(text="—")
                self.stats_labels["已回复"].configure(text="—")
                self.stats_labels["OCR次数"].configure(text="—")
                detail_lines.append("💡 未启动监控：仅显示数据库历史统计（点击「开始监控」可查看实时数据）")
                detail_lines.append("")

            # 3. UI会话索引统计（轻量，不遍历全量正文）
            if hasattr(self, '_conv_index') and self._conv_index:
                total_msgs = sum(v.get("count", 0) for v in self._conv_index.values())
                total_contacts = len(self._conv_index)
                detail_lines.append(f"💾 会话索引: {total_msgs} 条消息, {total_contacts} 个联系人")
                if total_contacts > 0:
                    top_contacts = sorted(self._conv_index.items(),
                                         key=lambda x: x[1].get("count", 0), reverse=True)[:8]
                    detail_lines.append("  会话Top8:")
                    for name, idx in top_contacts:
                        detail_lines.append(f"    {name}: {idx.get('count', 0)}条")

            self._set_stats_detail("\n".join(detail_lines) if detail_lines else "暂无统计数据")

            # 4. V3.1: 生成炫酷可视化图表（不依赖引擎）
            self._render_stats_chart(storage)

        except Exception as e:
            self._set_stats_default()
            import traceback
            self._set_stats_detail(f"❌ 统计刷新异常:\n{traceback.format_exc()[-300:]}")
            try:
                self._on_log("error", f"[统计] 刷新异常: {e}")
            except Exception:
                pass

    def _render_stats_chart(self, storage):
        """V3.1: 用 matplotlib 生成炫酷统计图（环形 + 柱状），内嵌到统计面板。
        不依赖引擎：优先用传入 storage，否则用 _get_storage()。
        """
        try:
            import tkinter as tk
            from PIL import Image, ImageTk
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            from matplotlib.patches import Wedge

            st = storage or self._get_storage()
            if st is None:
                self.stats_chart.configure(text="（无数据，无法生成图表）")
                return

            s = st.get_stats() or {}
            total = s.get("total_messages", 0)
            important = s.get("important_messages", 0)
            contacts = s.get("total_contacts", 0)
            today = s.get("today_messages", 0)
            week = s.get("week_messages", 0)
            senders = s.get("sender_distribution", {}) or {}

            fig = plt.figure(figsize=(5.4, 3.0), dpi=110, facecolor="#1e1e24")
            fig.patch.set_alpha(0.0)

            # ---- 左：环形图（消息构成：重要 vs 普通） ----
            ax1 = fig.add_axes([0.04, 0.12, 0.44, 0.78])
            ax1.set_facecolor("#1e1e24")
            normal = max(total - important, 0)
            vals = [important, normal] if total > 0 else [1, 0]
            colors_donut = ["#FF5B5B", "#2D9CDB"]
            if total > 0:
                wedges, _ = ax1.pie(vals, colors=colors_donut, startangle=90,
                                    wedgeprops=dict(width=0.38, edgecolor="#1e1e24", linewidth=2))
                ax1.text(0, 0, f"{total}\n消息", ha="center", va="center",
                         fontsize=13, fontweight="bold", color="#FFFFFF")
            else:
                ax1.text(0.5, 0.5, "暂无数据", ha="center", va="center", color="#888")
            ax1.set_title("消息构成", color="#E0E0E0", fontsize=10)
            ax1.legend(["重要 ⭐", "普通"], loc="lower center", bbox_to_anchor=(0.5, -0.18),
                       fontsize=7, frameon=False, labelcolor="#BBBBBB")

            # ---- 右：柱状图（今日/本周/联系人数/重要） ----
            ax2 = fig.add_axes([0.56, 0.16, 0.40, 0.66])
            ax2.set_facecolor("#1e1e24")
            cats = ["今日", "本周", "联系人", "重要"]
            vals2 = [today, week, contacts, important]
            bar_colors = ["#27AE60", "#2D9CDB", "#9B51E0", "#FF5B5B"]
            bars = ax2.bar(cats, vals2, color=bar_colors, width=0.62,
                           edgecolor="#1e1e24", linewidth=1.5, zorder=3)
            ax2.set_title("关键指标", color="#E0E0E0", fontsize=10)
            ax2.spines["top"].set_visible(False)
            ax2.spines["right"].set_visible(False)
            ax2.spines["left"].set_color("#444")
            ax2.spines["bottom"].set_color("#444")
            ax2.tick_params(colors="#BBBBBB", labelsize=8)
            ax2.set_ylim(0, max(vals2 + [1]) * 1.25)
            for b, v in zip(bars, vals2):
                ax2.text(b.get_x() + b.get_width()/2, v + max(vals2 + [1])*0.02,
                         str(v), ha="center", va="bottom", fontsize=8, color="#FFFFFF", fontweight="bold")
            ax2.grid(axis="y", color="#333", linewidth=0.6, zorder=0)

            fig.canvas.draw()
            buf = fig.canvas.buffer_rgba()
            w, h = fig.canvas.get_width_height()
            img = Image.frombytes("RGBA", (w, h), buf)
            rgb = Image.new("RGB", (w, h), "#1e1e24")
            rgb.paste(img, (0, 0), img)
            photo = ImageTk.PhotoImage(rgb)
            plt.close(fig)

            self.stats_chart.configure(image=photo, text="")
            self.stats_chart.image = photo
        except Exception as _e:
            try:
                self._on_log("error", f"[统计图表] 生成失败: {_e}")
                self.stats_chart.configure(text="（图表生成失败，请看日志）")
            except Exception:
                pass

    def _set_stats_default(self):
        """将所有统计标签重置为默认值"""
        for k in self.stats_labels:
            try:
                self.stats_labels[k].configure(text="—")
            except Exception:
                pass
        try:
            self.stats_sender_label.configure(text="—")
        except Exception:
            pass

    def _set_stats_detail(self, text):
        """更新统计详情文本框"""
        try:
            self.stats_detail.configure(state="normal")
            self.stats_detail.delete("1.0", "end")
            self.stats_detail.insert("1.0", text)
            self.stats_detail.configure(state="disabled")
        except Exception:
            pass

    def _append_stats_detail(self, text):
        """追加统计详情"""
        try:
            self.stats_detail.configure(state="normal")
            current = self.stats_detail.get("1.0", "end-1c")
            self.stats_detail.delete("1.0", "end")
            self.stats_detail.insert("1.0", text if current.strip() in ("点击「刷新统计」查看详细数据...", "—", "")
                                    else current + "\n" + text)
            self.stats_detail.configure(state="disabled")
        except Exception:
            pass
    def _run_diagnosis_async(self):
        """后台诊断，不阻塞UI"""
        try:
            diagnosis_ok = self._diagnose_system()
            if not diagnosis_ok:
                self.after(0, lambda: self._on_log("warning", "⚠️ 诊断发现问题，建议修复后再启动"))
                self.after(0, lambda: messagebox.showwarning(
                    "诊断结果",
                    "系统诊断发现一些问题，可能影响使用体验。"
                ))
        except Exception as e:
            self.after(0, lambda: self._on_log("error", f"诊断异常: {e}"))
    def _diagnose_system(self):
        """系统诊断（修复启动问题的辅助工具）"""
        self._on_log("info", "🔍 开始系统诊断...")

        issues = []

        # 1. 检查微信窗口
        try:
            from window_manager import find_wechat_window
            window = find_wechat_window()
            if window:
                self._on_log("info", f"✅ 微信窗口检测正常: {window.title}")
            else:
                issues.append("未找到微信窗口，请确保微信已打开并登录")
                self._on_log("warning", "❌ 微信窗口未找到")
        except Exception as e:
            issues.append(f"微信窗口检测失败: {e}")
            self._on_log("error", f"❌ 微信窗口检测异常: {e}")

        # 2. 检查配置文件
        try:
            if os.path.exists(self.config_path):
                self._on_log("info", f"✅ 配置文件存在: {self.config_path}")
            else:
                issues.append("配置文件不存在")
                self._on_log("error", f"❌ 配置文件不存在: {self.config_path}")
        except Exception as e:
            issues.append(f"配置文件检查失败: {e}")
            self._on_log("error", f"❌ 配置文件检查异常: {e}")

        # 3. 检查依赖模块
        required_modules = [
            ("PaddleOCR", "paddleocr"),
            ("OpenCV", "cv2"),
            ("customtkinter", "customtkinter"),
            ("requests", "requests"),
            ("yaml", "yaml"),
        ]

        for module_name, import_name in required_modules:
            try:
                __import__(import_name)
                self._on_log("info", f"✅ {module_name} 模块正常")
            except ImportError:
                issues.append(f"{module_name} 模块缺失")
                self._on_log("error", f"❌ {module_name} 模块缺失")

        # 4. 检查数据目录
        try:
            data_dir = "data"
            if os.path.exists(data_dir):
                self._on_log("info", f"✅ 数据目录存在: {data_dir}")
            else:
                os.makedirs(data_dir, exist_ok=True)
                self._on_log("info", f"✅ 数据目录已创建: {data_dir}")
        except Exception as e:
            issues.append(f"数据目录操作失败: {e}")
            self._on_log("error", f"❌ 数据目录操作异常: {e}")

        # 5. 检查API Key配置
        try:
            api_key = self.config_data.get("llm", {}).get("api_key", "")
            if api_key:
                self._on_log("info", f"✅ API Key配置正常: {api_key[:10]}...")
            else:
                issues.append("API Key配置无效或缺失")
                self._on_log("warning", "⚠️ API Key配置无效或缺失（自动回复功能将不可用）")
        except Exception as e:
            issues.append(f"API Key检查失败: {e}")
            self._on_log("error", f"❌ API Key检查异常: {e}")

        # 诊断结果
        if issues:
            self._on_log("warning", f"🔧 发现 {len(issues)} 个问题:")
            for i, issue in enumerate(issues, 1):
                self._on_log("warning", f"  {i}. {issue}")
            self._on_log("info", "💡 建议修复上述问题后再启动监控")
        else:
            self._on_log("info", "✅ 系统诊断通过，所有检查项正常")

        return len(issues) == 0

    def _auto_refresh_stats(self):
        """自动刷新统计（每10秒）"""
        try:
            if hasattr(self, 'engine') and self.engine and self.engine.is_running():
                self._refresh_stats()
                self.after(10000, self._auto_refresh_stats)
        except Exception as e:
            self._on_log("warning", f"[统计] 自动刷新异常: {e}")

    def _toggle_advanced_settings(self):
        if self.advanced_settings_var.get():
            self.advanced_frame.pack(fill="x", padx=10, pady=5)
        else:
            self.advanced_frame.pack_forget()

    def _jump_to_setting(self, attr):
        """V3.1: 设置页导航——平滑滚动到指定区块，并高亮当前选中按钮。"""
        try:
            frame = getattr(self, attr, None)
            if frame is None or not frame.winfo_exists():
                return
            # 确保 advanced 区块可见
            if attr == "advanced_frame" and not self.advanced_settings_var.get():
                self.advanced_settings_var.set(True)
                self._toggle_advanced_settings()
            # CTkScrollableFrame 内部 canvas 滚动到目标 y
            scroll = frame.master
            canvas = getattr(scroll, "_parent_canvas", None)
            if canvas is not None:
                frame.update_idletasks()
                canvas.configure(scrollregion=canvas.bbox("all"))
                all_box = canvas.bbox("all")
                if all_box:
                    y = frame.winfo_y()
                    canvas.yview_moveto(max(0.0, y / max(all_box[3], 1)))
            # 高亮区块 + 复位其他
            try:
                orig = frame.cget("fg_color")
                frame.configure(fg_color=WC_COLORS["accent_light"])
                frame.after(700, lambda: frame.configure(fg_color=orig))
            except Exception:
                pass
            # 导航按钮选中态：点中的变强调色，其他复位
            for a, b in (self._settings_nav_btns or {}).items():
                try:
                    if a == attr:
                        b.configure(fg_color=WC_COLORS["accent"],
                                    text_color="#FFFFFF")
                    else:
                        b.configure(fg_color=WC_COLORS["bg"],
                                    text_color=WC_COLORS["text"])
                except Exception:
                    pass
        except Exception as _e:
            try:
                self._on_log("error", f"[设置导航] 跳转失败: {_e}")
            except Exception:
                pass

    # ========== 事件处理 ==========

    def start_monitoring(self):
        """开始监控（修复版：增强错误处理和用户反馈）"""
        try:
            # 按钮点击反馈
            self.btn_start.configure(state="disabled")
            self._on_log("info", "━━━ 正在启动监控，请稍候 ━━━")

            # 诊断放到后台线程，避免阻塞UI
            self._on_log("info", "🔍 后台运行启动前诊断...")
            threading.Thread(target=self._run_diagnosis_async, daemon=True).start()

            # 检查微信窗口是否已打开
            from window_manager import find_wechat_window
            test_window = find_wechat_window()
            if not test_window:
                self._on_log("warning", "⚠️ 未找到微信窗口")
                self._on_log("info", "💡 请确保微信已打开并登录")
                self.btn_start.configure(state="normal")
                messagebox.showwarning(
                    "微信未找到",
                    "未找到微信窗口，请确保：\n1. 微信已打开\n2. 已登录账号\n3. 窗口标题不包含'AI'或'助手'"
                )
                return

            self._on_log("info", f"✅ 检测到微信窗口: {test_window.title}")

            # 导入主模块
            try:
                from main import WeChatEngine
                self._on_log("info", "📦 正在加载监控引擎...")
            except ImportError as e:
                self._on_log("error", f"❌ 模块导入失败: {e}")
                self._on_log("error", "💡 请检查依赖是否完整安装")
                self.btn_start.configure(state="normal")
                messagebox.showerror(
                    "模块导入失败",
                    f"无法导入必要模块: {e}\n\n请运行: pip install -r requirements.txt"
                )
                return

            # 创建引擎实例
            try:
                self.engine = WeChatEngine(self.config_path, callbacks={
                    "on_log": self._on_log,
                    "on_extract": self._on_extract,
                    "on_reply": self._on_reply,
                    "on_stats": self._on_stats,
                    "on_status": self._on_status,
                    "on_new_message": self._on_new_message,
                    "on_capture": self._on_capture,
                })
                # ===== P0: 把 UI 的对话上下文取数回调注入引擎 =====
                try:
                    self.engine._fetch_context_fn = self.get_conversation_context
                    self.engine._context_turns = self._context_max_turns
                    self._on_log("info", "[上下文] 已挂载到自动回复引擎 (最近%d轮)" % self._context_max_turns)
                except Exception as _e:
                    self._on_log("warning", "[上下文] 挂载到引擎失败: %s" % _e)
                # V4: 绑定 Obsidian 写入失败弹窗告警
                try:
                    self.engine._on_obsidian_error_cb = self._on_obsidian_write_error
                except Exception:
                    pass
                self._on_log("info", "✅ 监控引擎创建成功")
            except Exception as e:
                self._on_log("error", f"❌ 引擎创建失败: {e}")
                import traceback
                self._on_log("error", traceback.format_exc()[-300:])
                self.btn_start.configure(state="normal")
                messagebox.showerror(
                    "引擎创建失败",
                    f"创建监控引擎失败: {e}\n\n请查看日志了解详情"
                )
                return

            # 设置自动回复
            auto_reply = self.config_data.get("auto_reply", {}).get("enabled", False)
            self.engine.auto_reply_enabled = auto_reply
            self._on_log("info", f"📢 自动回复: {'启用' if auto_reply else '禁用'}")

            # 后台线程执行启动流程
            self._on_log("info", "🚀 正在启动后台监控线程...")
            threading.Thread(target=self._start_engine_thread, daemon=True).start()

        except Exception as e:
            self._on_log("error", f"❌ 启动过程异常: {e}")
            import traceback
            self._on_log("error", traceback.format_exc()[-500:])
            self.btn_start.configure(state="normal")
            messagebox.showerror(
                "启动异常",
                f"启动过程发生异常: {e}\n\n请查看日志了解详情"
            )

    def _start_engine_thread(self):
        """后台执行engine.start()，分步加载避免UI卡顿（修复版：增强错误处理）"""
        try:
            self._on_log("info", "🔧 正在启动引擎...")
            self.engine.start()

            # 启动成功后的处理
            if self.engine.is_running():
                self.after(0, lambda: [
                    self.btn_stop.configure(state="normal"),
                    self._on_log("info", "✅ 监控已成功启动！")
                ])
                self.after(0, self._rebuild_contact_list)
                self.after(1000, self._auto_refresh_stats)
                self._on_log("info", "📊 自动刷新已启动（每10秒更新）")
            else:
                self.after(0, lambda: [
                    self.btn_start.configure(state="normal"),
                    self._on_log("error", "❌ 启动未成功，引擎状态异常")
                ])

        except Exception as e:
            error_msg = f"启动异常: {e}"
            self._on_log("error", f"❌ {error_msg}")
            import traceback
            self._on_log("error", f"完整堆栈：\n{traceback.format_exc()[-500:]}")

            self.after(0, lambda: [
                self.btn_start.configure(state="normal"),
                self._on_log("warning", "💡 请检查：1.微信是否打开 2.配置是否正确 3.依赖是否完整")
            ])

    def stop_monitoring(self):
        if self.engine:
            eng = self.engine
            self.engine = None
            threading.Thread(target=eng.stop, daemon=True).start()
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self._on_log("info", "已发送停止信号")

    def _toggle_monitoring(self):
        if self.engine and self.engine.is_running():
            self.stop_monitoring()
        else:
            self.start_monitoring()

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
                debug_dir = _app_path("debug")
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
        """
        V2: 在提取结果区域显示测试识别结果（卡片式UI+彩色标签）。
        严格匹配用户偏好：
          - 绿色🟢 = 自己消息
          - 白色⚪ = 对方消息（深色文字用羊皮纸背景衬托）
          - 红色🔴 = 重要 / 低置信度警告
          - 橙色🟠 = 关键词（这里用于"多行"等辅助信息）
          - 蓝色🔵 = 提取信息（这里用于置信度高/中）
          - 紫色🟣 = 摘要/统计头
        """
        self.result_text.configure(state="normal")
        self.result_text.delete("1.0", "end")

        # V2: 用户偏好的彩色标签系统（与MC主色系统一致）
        self.result_text.tag_config("header",
                                    foreground=WC_COLORS["summary"],
                                    font=ctk.CTkFont(family=FONT_FAMILY, weight="bold"))
        self.result_text.tag_config("stats",
                                    foreground=WC_COLORS["info"],
                                    font=ctk.CTkFont(family=FONT_FAMILY, weight="bold"))
        # 绿 = 我
        self.result_text.tag_config("me_label",
                                    foreground="#FFFFFF",
                                    background=WC_COLORS["accent"],
                                    font=ctk.CTkFont(family=FONT_FAMILY, weight="bold"))
        self.result_text.tag_config("me_text",
                                    foreground=WC_COLORS["accent_hover"],
                                    font=ctk.CTkFont(family=FONT_FAMILY))
        # 白 = 对方（用深色棕字衬托，不用白字在白底看不见）
        self.result_text.tag_config("other_label",
                                    foreground=WC_COLORS["bg_dark"],
                                    background=WC_COLORS["card"],
                                    font=ctk.CTkFont(family=FONT_FAMILY, weight="bold"))
        self.result_text.tag_config("other_text",
                                    foreground=WC_COLORS["text"],
                                    font=ctk.CTkFont(family=FONT_FAMILY))
        # 红 = 重要 / 低置信度
        self.result_text.tag_config("conf_low",
                                    foreground="#FFFFFF",
                                    background=WC_COLORS["danger"],
                                    font=ctk.CTkFont(family=FONT_FAMILY, weight="bold"))
        # 橙 = 关键词 / 辅助信息（多行）
        self.result_text.tag_config("conf_mid",
                                    foreground="#2B1E10",
                                    background=WC_COLORS["warning"],
                                    font=ctk.CTkFont(family=FONT_FAMILY, weight="bold"))
        # 蓝 = 高置信度 / 提取
        self.result_text.tag_config("conf_high",
                                    foreground="#FFFFFF",
                                    background=WC_COLORS["info"],
                                    font=ctk.CTkFont(family=FONT_FAMILY, weight="bold"))
        # 橙 = 行数标签（关键词色）
        self.result_text.tag_config("line_count",
                                    foreground="#FFFFFF",
                                    background=WC_COLORS["keyword"],
                                    font=ctk.CTkFont(family=FONT_FAMILY, weight="bold"))
        # 分隔（橡木棕像素色）
        self.result_text.tag_config("separator",
                                    foreground=WC_COLORS["border_light"])
        self.result_text.tag_config("pad", foreground=WC_COLORS["bg"])

        me_count = sum(1 for r in results if r.get("sender") == "me")
        other_count = len(results) - me_count

        # 标题
        self.result_text.insert("end", "  OCR识别结果  ", "header")
        self.result_text.insert("end", "\n")
        self.result_text.insert(
            "end",
            f"  共 {len(results)} 条  |  🟢我 {me_count}  ⚪对方 {other_count}\n\n",
            "stats"
        )

        for i, r in enumerate(results):
            sender = r.get("sender", "other")
            conf = r.get("confidence", 0)
            text = r["text"]
            lines = text.split("\n")
            line_count = len(lines)

            # === 颜色分配 ===
            if sender == "me":
                label_tag = "me_label"
                text_tag = "me_text"
                label_text = " 🟢 我 "
            else:
                label_tag = "other_label"
                text_tag = "other_text"
                label_text = " ⚪ 对方 "

            if conf >= 0.90:
                conf_tag = "conf_high"
                conf_txt = f" 🔵 高 {conf:.0%} "
            elif conf >= 0.70:
                conf_tag = "conf_mid"
                conf_txt = f" 🟠 中 {conf:.0%} "
            else:
                conf_tag = "conf_low"
                conf_txt = f" 🔴 低 {conf:.0%} "

            # 先写空白间隔（防止tag紧贴）
            self.result_text.insert("end", "  ")
            self.result_text.insert("end", label_text, label_tag)
            self.result_text.insert("end", "  ")
            self.result_text.insert("end", conf_txt, conf_tag)
            if line_count > 1:
                self.result_text.insert("end", "  ")
                self.result_text.insert("end", f" 🟠 {line_count}行 ", "line_count")
            self.result_text.insert("end", "\n")

            # 正文（最多显示4行）
            for idx, ln in enumerate(lines[:4]):
                display_ln = ln[:100] if ln else " "
                self.result_text.insert("end", f"    {display_ln}\n", text_tag)
            if line_count > 4:
                self.result_text.insert(
                    "end",
                    f"    ……（另 {line_count - 4} 行省略）\n",
                    "line_count"
                )

            # V2: MC风像素分隔（橡木棕）
            self.result_text.insert(
                "end",
                "  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓\n",
                "separator"
            )

        self.result_text.configure(state="disabled")

    def on_reply_switch(self):
        enabled = self.reply_switch.get() == 1
        self.config_data.setdefault("auto_reply", {})["enabled"] = enabled
        if self.engine:
            self.engine.set_auto_reply(enabled)
        self._on_log("info", f"自动回复已{'开启' if enabled else '关闭'}")
        try:
            import yaml
            config_path = _app_path(self.config_path)
            with open(config_path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            raw.setdefault("auto_reply", {})["enabled"] = enabled
            with open(config_path, "w", encoding="utf-8") as f:
                yaml.dump(raw, f, allow_unicode=True, default_flow_style=False)
            self._on_log("info", "自动回复状态已保存到配置文件")
        except Exception as e:
            self._on_log("warning", f"自动回复状态保存失败: {e}")


    def on_preview_mode_switch(self):
        """预览确认模式开关"""
        enabled = self.preview_mode_switch.get()
        self.config_data.setdefault("auto_reply", {})["preview_mode"] = enabled
        if self.engine:
            self.engine.auto_reply_config["preview_mode"] = enabled
        self._on_log("info", f"预览确认模式: {'开启' if enabled else '关闭'}（生成回复后{'需手动确认' if enabled else '自动发送'}）")
        try:
            import yaml
            config_path = _app_path(self.config_path)
            with open(config_path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            raw.setdefault("auto_reply", {})["preview_mode"] = enabled
            with open(config_path, "w", encoding="utf-8") as f:
                yaml.dump(raw, f, allow_unicode=True, default_flow_style=False)
        except Exception as e:
            self._on_log("warning", f"预览模式状态保存失败: {e}")

    def _on_provider_changed(self, choice):
        """厂商切换：自动填充 API 地址和默认模型"""
        p = self._llm_providers.get(choice)
        if not p:
            return
        # 自定义：保持当前值不变
        if choice == "自定义" and p["url"] == "":
            return
        # 切换厂商时自动填充 URL 和模型
        self.entry_url.delete(0, "end")
        self.entry_url.insert(0, p["url"])
        self.entry_model.delete(0, "end")
        self.entry_model.insert(0, p["model"])
        self.llm_status_label.configure(text=f"已切换至 {choice}", text_color=WC_COLORS["text_muted"])

    def _test_llm_connection(self):
        base_url = self.entry_url.get().strip()
        api_key = self.entry_key.get().strip()
        model = self.entry_model.get().strip()

        if not base_url:
            self.llm_status_label.configure(text="❌ 请填写 API 地址", text_color=WC_COLORS["danger"])
            return
        if not api_key:
            self.llm_status_label.configure(text="❌ 请填写 API Key", text_color=WC_COLORS["danger"])
            return
        if not model:
            self.llm_status_label.configure(text="❌ 请填写模型名称", text_color=WC_COLORS["danger"])
            return

        self.llm_status_label.configure(text="⏳ 正在测试连接...", text_color=WC_COLORS["info"])
        self.btn_test_llm.configure(state="disabled", text="测试中...")

        def _do_test():
            try:
                from llm_client import LLMClient
                llm_cfg = {
                    "base_url": base_url,
                    "api_key": api_key,
                    "model": model,
                    "max_tokens": 10,
                    "temperature": 0.1,
                    "provider": "custom",
                    "thinking": False,
                }
                client = LLMClient(llm_cfg)
                ok, msg = client.test_connection()
                if ok:
                    status_text = f"✅ {msg}"
                    status_color = WC_COLORS["accent"]
                else:
                    status_text = f"❌ {msg}"
                    status_color = WC_COLORS["danger"]
            except Exception as e:
                status_text = f"❌ 异常: {str(e)[:60]}"
                status_color = WC_COLORS["danger"]

            def _update_ui():
                try:
                    self.llm_status_label.configure(text=status_text, text_color=status_color)
                    self.btn_test_llm.configure(state="normal", text="🔗 测试连接")
                except Exception:
                    pass
            try:
                self.after(0, _update_ui)
            except Exception:
                pass

        threading.Thread(target=_do_test, daemon=True).start()

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
        rules_path = _app_path("learned_rules.json")
        if os.path.exists(rules_path):
            subprocess.Popen(["notepad", rules_path])
        else:
            self._on_log("warning", "规则库文件不存在")

    def _reset_ai_training(self):
        """重置AI训练"""
        try:
            import json, os
            rules_path = _app_path("learned_rules.json")
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

    def _clear_dedup_cache(self):
        """清除红点跨轮去重缓存和自动回复指纹"""
        try:
            eng = getattr(self, "_engine", None)
            if eng:
                eng.clear_reddot_seen()
                eng.clear_reply_fingerprints()
                self._on_log("info", "✅ 去重缓存已清除，下次检测将重新识别所有消息")
            else:
                # 引擎未启动时直接清文件
                import os, json
                rp = _app_path("data", "reddot_seen.json")
                if os.path.exists(rp):
                    os.remove(rp)
                    self._on_log("info", "✅ 去重缓存文件已删除")
                else:
                    self._on_log("info", "去重缓存为空，无需清除")
        except Exception as e:
            self._on_log("error", f"清除去重失败: {e}")

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

    def _on_obsidian_write_error(self, msg):
        """V4: Obsidian 写入失败 UI 弹窗告警（不静默吞掉）"""
        try:
            self._on_log("error", f"[Obsidian] 写入异常: {msg}")
            messagebox.showwarning(
                "Obsidian 同步失败",
                f"写入 Obsidian 失败，消息可能未同步：\n\n{msg}\n\n"
                f"请检查 Vault 路径是否正确、磁盘空间、或 Obsidian 是否被占用。"
            )
        except Exception:
            pass

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
            self.config_data["llm"]["provider"] = self.provider_var.get()
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
            # V4: 高级功能开关 + 过滤规则
            _adv = obsidian_cfg.setdefault("advanced", {})
            for k, v in self.obs_adv_vars.items():
                _adv[k] = v.get()
            _adv["webhook_url"] = self.obs_webhook_entry.get().strip()
            _filt = obsidian_cfg.setdefault("filter", {})
            for k, v in self.obs_filter_vars.items():
                _filt[k] = v.get()
            self.config_data["obsidian"] = obsidian_cfg

            config_path = self._ensure_config_file()
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
        self.preview_win.title("NOYA Chat 微信助手 - 监控预览")
        self.preview_win.geometry("960x680")
        self.preview_win.configure(bg=WC_COLORS["bg"])
        self.preview_win.minsize(860, 580)

        # ===== 顶部标题栏（微信风格）=====
        title_bar = tk.Frame(self.preview_win, bg=WC_COLORS["sidebar"], height=36)
        title_bar.pack(fill="x", side="top")
        title_bar.pack_propagate(False)

        tk.Label(title_bar, text="🤖 NOYA Chat 微信助手",
                 bg=WC_COLORS["sidebar"], fg=WC_COLORS["accent"],
                 font=ctk.CTkFont(family=FONT_FAMILY, weight="bold")).pack(side="left", padx=15, pady=6)

        self.preview_status = tk.Label(title_bar, text="● 等待截图...",
                                        bg=WC_COLORS["sidebar"], fg=WC_COLORS["text_muted"],
                                        font=("Consolas", 10))
        self.preview_status.pack(side="right", padx=15, pady=6)

        # ===== 主体：左侧联系人 + 右侧预览 =====
        body = tk.Frame(self.preview_win, bg=WC_COLORS["bg"])
        body.pack(fill="both", expand=True)

        # ===== 左侧联系人面板 =====
        left_panel = tk.Frame(body, bg=WC_COLORS["sidebar"], width=200)
        left_panel.pack(side="left", fill="y")
        left_panel.pack_propagate(False)

        # 搜索框
        search_frame = tk.Frame(left_panel, bg=WC_COLORS["sidebar"])
        search_frame.pack(fill="x", padx=10, pady=10)
        tk.Entry(search_frame, font=ctk.CTkFont(family=FONT_FAMILY),
                 bg=WC_COLORS["sidebar_hover"], fg=WC_COLORS["text"], insertbackground=WC_COLORS["text"],
                 relief="flat", highlightthickness=0).pack(fill="x", ipady=4)

        # 标签
        tk.Label(left_panel, text="最近监控",
                 bg=WC_COLORS["sidebar"], fg=WC_COLORS["text_muted"],
                 font=ctk.CTkFont(family=FONT_FAMILY)).pack(anchor="w", padx=15, pady=(5, 8))

        # 联系人列表（滚动）
        list_container = tk.Frame(left_panel, bg=WC_COLORS["sidebar"])
        list_container.pack(fill="both", expand=True, padx=5)

        canvas = tk.Canvas(list_container, bg=WC_COLORS["sidebar"], highlightthickness=0)
        scrollbar = tk.Scrollbar(list_container, orient="vertical", command=canvas.yview)
        self._preview_list_frame = tk.Frame(canvas, bg=WC_COLORS["sidebar"])

        self._preview_list_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=self._preview_list_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # 默认联系人占位
        self._preview_contacts = {}
        self._update_preview_contacts()

        # ===== 右侧预览区 =====
        right_panel = tk.Frame(body, bg=WC_COLORS["bg"])
        right_panel.pack(side="left", fill="both", expand=True)

        # 聊天头部
        chat_header = tk.Frame(right_panel, bg=WC_COLORS["bg"], height=48,
                               highlightbackground=WC_COLORS["border"], highlightthickness=2)
        chat_header.pack(fill="x", side="top")
        chat_header.pack_propagate(False)

        self.chat_preview_title = tk.Label(chat_header, text="📡 实时监控画面",
                                            bg=WC_COLORS["bg"], fg=WC_COLORS["text"],
                                            font=ctk.CTkFont(family=FONT_FAMILY, weight="bold"))
        self.chat_preview_title.pack(side="left", padx=15, pady=12)

        self.chat_preview_sub = tk.Label(chat_header, text="屏幕外监控中",
                                          bg=WC_COLORS["bg"], fg=WC_COLORS["accent"],
                                          font=ctk.CTkFont(family=FONT_FAMILY))
        self.chat_preview_sub.pack(side="right", padx=15, pady=12)

        # 截图预览区
        preview_container = tk.Frame(right_panel, bg=WC_COLORS["bg"])
        preview_container.pack(fill="both", expand=True, padx=10, pady=10)

        # 模拟微信窗口边框
        self.preview_label = tk.Label(
            preview_container, bg="black",
            highlightbackground=WC_COLORS["border"], highlightthickness=1,
        )
        self.preview_label.pack(fill="both", expand=True)

        # 底部信息栏
        bottom_bar = tk.Frame(right_panel, bg=WC_COLORS["bg"], height=32,
                               highlightbackground=WC_COLORS["border"], highlightthickness=2)
        bottom_bar.pack(fill="x", side="bottom")
        bottom_bar.pack_propagate(False)

        self.preview_info = tk.Label(bottom_bar,
                                      text="分辨率: -- | 状态: 等待截图",
                                      bg=WC_COLORS["bg"], fg=WC_COLORS["text_muted"],
                                      font=ctk.CTkFont(family=FONT_FAMILY))
        self.preview_info.pack(pady=6)

        # 默认占位图
        placeholder = Image.new("RGB", (720, 480), (240, 240, 240))
        draw = ImageDraw.Draw(placeholder)
        draw.rectangle([0, 0, 719, 479], outline="#E5E5EA", width=1)
        draw.text((260, 200), "等待监控截图...", fill="#86868B")
        draw.text((230, 240), "启动监控后将实时显示微信画面", fill="#AEAEB2")
        self._placeholder_photo = ImageTk.PhotoImage(placeholder)
        self.preview_label.configure(image=self._placeholder_photo)

        self.preview_win.protocol("WM_DELETE_WINDOW", self._close_preview)
        # 预览窗口专用联系人 frame（避免误操作主 UI contact_list_frame）
        if hasattr(self, "contact_list_frame") and self.contact_list_frame.winfo_exists():
            for ch in self.contact_list_frame.winfo_children():
                try:
                    if ch.winfo_class() in ("Frame", "TFrame", "CTkFrame"):
                        self._preview_contacts_frame = ch
                        break
                except Exception:
                    pass
        if not hasattr(self, "_preview_contacts_frame"):
            self._preview_contacts_frame = self.contact_list_frame
        self._on_log("info", "[预览窗口] 微信风格预览已打开")

    def _update_preview_contacts(self):
        """更新预览窗口联系人列表（仅操作预览窗口内部 frame，不碰主 UI 会话列表）"""
        if not hasattr(self, '_preview_contacts'):
            return
        # 预览窗口专用 frame（不是主 UI 的 contact_list_frame）
        frame = getattr(self, "_preview_contacts_frame", None)
        if frame is None or not frame.winfo_exists():
            return
        try:
            for widget in frame.winfo_children():
                widget.destroy()
        except Exception:
            return

        items = list(self._preview_contacts.items())
        if not items:
            items = [
                ("等待中...", {"time": "--:--", "unread": 0}),
            ]

        for name, info in items:
            contact_row = tk.Frame(self.contact_list_frame, bg=WC_COLORS["sidebar"], cursor="hand2")
            contact_row.pack(fill="x", padx=5, pady=2)

            avatar_frame = tk.Frame(contact_row, bg=WC_COLORS["accent"], width=36, height=36)
            avatar_frame.pack(side="left", padx=(10, 8), pady=6)
            avatar_frame.pack_propagate(False)
            tk.Label(avatar_frame, text=name[0] if name else "?",
                     bg=WC_COLORS["accent"], fg=WC_COLORS["text"],
                     font=ctk.CTkFont(family=FONT_FAMILY, weight="bold")).pack(expand=True)

            info_frame = tk.Frame(contact_row, bg=WC_COLORS["sidebar"])
            info_frame.pack(side="left", fill="x", expand=True, pady=6)

            name_row = tk.Frame(info_frame, bg=WC_COLORS["sidebar"])
            name_row.pack(fill="x")
            tk.Label(name_row, text=name, bg=WC_COLORS["sidebar"], fg=WC_COLORS["text"],
                     font=ctk.CTkFont(family=FONT_FAMILY, weight="bold")).pack(side="left")
            tk.Label(name_row, text=info.get("time", ""), bg=WC_COLORS["sidebar"], fg=WC_COLORS["text_muted"],
                     font=("Consolas", 8)).pack(side="right")

            msg_row = tk.Frame(info_frame, bg=WC_COLORS["sidebar"])
            msg_row.pack(fill="x")
            preview = info.get("preview", "")[:20]
            tk.Label(msg_row, text=preview or "暂无新消息", bg=WC_COLORS["sidebar"], fg=WC_COLORS["text_muted"],
                     font=ctk.CTkFont(family=FONT_FAMILY)).pack(anchor="w")

            unread = info.get("unread", 0)
            if unread > 0:
                badge = tk.Label(contact_row, text=str(unread),
                                 bg="#FA5151", fg=WC_COLORS["text"], width=2,
                                 font=ctk.CTkFont(family=FONT_FAMILY, weight="bold"))
                badge.place(relx=1.0, y=8, x=-12, anchor="ne")

    def _close_preview(self):
        """关闭预览窗口"""
        try:
            self.preview_win.destroy()
        except Exception:
            pass
        self._on_log("info", "[预览窗口] 已关闭")

    def _debug_log(self, message):
        """把 UI 调试信息追加到 ui_debug.log（APP_BASE 下，打包/开发通用），
        用于定位“点击会话卡后中栏空白”等仅在真实显示环境才暴露的问题。"""
        try:
            import datetime as _dt
            _p = os.path.join(APP_BASE, "ui_debug.log")
            _ts = _dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]
            with open(_p, "a", encoding="utf-8") as _f:
                _f.write(f"[{_ts}] {message}\n")
        except Exception:
            pass

    def _on_log(self, level, message=None):
        if message is None:
            message = level
            level = "info"
        # ★ 错误可见性：内存环形缓冲，保留最近 50 条 warning/error，供错误面板查看
        if level in ("warning", "error"):
            try:
                if not hasattr(self, "_error_buffer"):
                    self._error_buffer = []
                self._error_buffer.append(
                    (datetime.now().strftime("%H:%M:%S"), level, message))
                if len(self._error_buffer) > 50:
                    self._error_buffer = self._error_buffer[-50:]
            except Exception:
                pass
        self.after(0, lambda: self._append_log(level, message))

    def _set_log_level(self, level):
        """设置日志级别: debug/info/warning/error"""
        valid = ("debug", "info", "warning", "error")
        level = level.lower()
        if level in valid:
            self._log_level = level
            self._on_log("info", f"[日志] 级别设为: {level}")

    def _log_level_enabled(self, level):
        """检查目标级别是否启用"""
        order = {"debug": 0, "info": 1, "warning": 2, "error": 3}
        current = order.get(getattr(self, "_log_level", "info"), 1)
        target = order.get(level, 1)
        return target >= current

    def _append_log(self, level, message):
        if not self._log_level_enabled(level):
            return
        timestamp = datetime.now().strftime("%H:%M:%S")
        prefix = {"debug": "◆ ", "info": "", "warning": "⚠ ", "error": "✖ "}.get(level, "")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{timestamp}] {prefix}{message}\n")
        # 日志行数上限：仅保留最近 800 行，防止长期运行 UI 卡顿/内存膨胀
        try:
            line_count = int(self.log_text.index("end-1c").split(".")[0])
            if line_count > 800:
                self.log_text.delete("1.0", f"{line_count - 800}.0")
        except Exception:
            pass
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _refresh_error_panel(self):
        """刷新错误面板：把内存环形缓冲里的 warning/error 渲染出来。"""
        try:
            if not hasattr(self, "_error_buffer"):
                self._error_buffer = []
            buf = getattr(self, "_error_buffer", [])
            self.error_panel_text.configure(state="normal")
            self.error_panel_text.delete("1.0", "end")
            if not buf:
                self.error_panel_text.insert("end", "（暂无 warning/error）\n")
            else:
                for ts, lvl, msg in buf:
                    _p = "✖ " if lvl == "error" else "⚠ "
                    self.error_panel_text.insert("end", f"[{ts}] {_p}{msg}\n")
            self.error_panel_text.configure(state="disabled")
            self._on_log("info", f"[错误面板] 已刷新，共 {len(buf)} 条")
        except Exception as e:
            self._on_log("error", f"[错误面板] 刷新失败: {e}")

    def _clear_error_panel(self):
        """清空错误面板与内存缓冲。"""
        try:
            self._error_buffer = []
            self.error_panel_text.configure(state="normal")
            self.error_panel_text.delete("1.0", "end")
            self.error_panel_text.insert("end", "（已清空）\n")
            self.error_panel_text.configure(state="disabled")
            self._on_log("info", "[错误面板] 已清空")
        except Exception as e:
            self._on_log("error", f"[错误面板] 清空失败: {e}")

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

        # 行数封顶：防止长期运行内存无限膨胀（超过600行删除最旧的200行）
        try:
            _line_count = int(self.result_text.index("end-1c").split(".")[0])
            if _line_count > 600:
                self.result_text.delete("1.0", "200.0")
        except Exception:
            pass

        self.result_text.see("end")
        self.result_text.configure(state="disabled")

    def _on_reply(self, contact, reply=None):
        """V3 P0-5: AI回复回执 — 兼容两种签名：
        老：_on_reply(contact: str, reply: str)
        新：_on_reply(payload: dict)  字段：contact/reply/status(sent/preview/failed)/method/sent_ok/timestamp/role
        """
        # —— 签名归一化 ——
        payload = None
        if isinstance(contact, dict):
            payload = contact
            contact = payload.get("contact", "")
            reply = payload.get("reply", "")
        if not contact:
            contact = "未知会话"

        # —— 日志 ——
        if isinstance(payload, dict):
            st = payload.get("status", "unknown")
            st_text = {"sent": "✅已发送", "preview": "⌛待确认", "failed": "❌发送失败"}.get(st, st)
            self.after(0, lambda c=contact, r=reply, s=st_text, m=(payload or {}).get("method", ""):
                       self._append_log("info", f"[回复·{s}] {c}: {r[:50]}{(' ('+m+')') if m else ''}"))
        else:
            self.after(0, lambda c=contact, r=reply:
                       self._append_log("info", f"[回复] {c}: {r[:50]}"))

        # —— 插入回执气泡（sender=me 绿色自己 + 🤖AI徽章 + 状态） ——
        try:
            import hashlib as _hl
            _now = (payload or {}).get("timestamp") or datetime.now().strftime("%H:%M")
            _st = (payload or {}).get("status", "sent") if payload else "sent"
            _sent_ok = bool(payload.get("sent_ok", True)) if payload else True
            _method = (payload or {}).get("method", "") if payload else ""
            _role = (payload or {}).get("role", "") if payload else ""

            # 状态徽章文字
            if _st == "preview":
                _badge = "🤖AI·待确认 ⌛"
                _importance_reason = f"AI自动回复（预览模式，已粘贴输入框，角色：{_role or '默认'}，方法：{_method or '剪贴板'}）"
            elif _st == "failed":
                _badge = "🤖AI·发送失败 ❌"
                _importance_reason = f"AI自动回复（所有方法失败，请手动检查微信窗口焦点）角色：{_role or '默认'}"
            else:
                _badge = "🤖AI·已发送 ✔"
                _importance_reason = f"AI自动回复（已通过 {_method or '智能发送'} 发出，角色：{_role or '默认'}）"

            # 合成一条"自己消息"并走实时消息管道 → 自动按会话过滤/重建/右栏同步/写盘
            msg_key_src = f"ai_reply|{contact}|{_now}|{reply}"
            msg_key = _hl.md5(msg_key_src.encode("utf-8")).hexdigest()[:14]

            msg_data = {
                "msg_key": msg_key,
                "is_update": False,
                "contact": contact,
                "sender": "me",
                "content": reply,
                "timestamp": _now,
                "is_important": (_st == "failed"),  # 失败高亮红色警告
                "importance_reason": _importance_reason,
                "keywords": ["🤖AI自动回复", _st],
                "categories": ["AI助手"],
                "regex_extracts": {},
                "extracted_fields": {"回复方法": _method, "回复角色": _role, "回复状态": _st},
                "summary": f"{_badge} {reply[:20]}…" if len(reply) > 20 else f"{_badge} {reply}",
                "classification": "AI助手",
                "priority": 2,
                "is_group": False,
                "group_member": None,
                "confidence": 1.0,
                "sender_confidence": 1.0,
                # 专属回执字段（用于气泡顶部徽章条）
                "_ai_reply_receipt": True,
                "_ai_badge": _badge,
                "_ai_status": _st,
            }
            self.after(0, lambda m=msg_data: self._on_new_message(m))
            # ===== P0: AI 已发送/待确认的回复，都作为"建议回复"在底部显示，用户可快速再次复制 =====
            if reply and len(reply) >= 2:
                try:
                    self.after(150, lambda r=reply, c=contact: self._show_suggest_bar(r, c))
                except Exception:
                    pass
        except Exception as e:
            try:
                self._append_log("warning", f"[回复·回执] 气泡创建失败: {e}")
            except Exception:
                pass

        # —— 同步 Obsidian：作为"自己消息"存档 ——
        try:
            if isinstance(payload, dict) and hasattr(self, "engine") and self.engine is not None \
                    and getattr(self.engine, "obsidian", None) is not None and self.engine.obsidian.enabled:
                sync_data = {
                    "contact": contact,
                    "sender": "me",
                    "raw_text": reply,
                    "timestamp": (payload.get("timestamp") if payload else None)
                                  or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "matched_keywords": ["🤖AI自动回复"],
                    "regex_extracts": {},
                    "is_important": (payload.get("status") == "failed") if payload else False,
                    "importance_reason": f"AI自动回复（状态：{_st}）",
                    "llm_analysis": {"summary": f"🤖AI{_st}: {reply[:40]}"},
                }
                try:
                    self.engine.obsidian.sync_message(sync_data)
                except Exception:
                    pass
        except Exception:
            pass

    def _on_stats(self, stats):
        self.after(0, lambda s=dict(stats): self._update_stats(s))

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
            "processing_reply": ("⏳ 生成回复中...", WC_COLORS["info"]),
            "processing_ocr": ("⏳ OCR 识别中...", WC_COLORS["info"]),
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
        # 处理中状态：按钮保持但禁用
        elif status.startswith("processing"):
            self.btn_start.configure(state="disabled")
            self.btn_stop.configure(state="normal")

    def _ensure_result_tags(self):
        """确保结果文本框的标签样式已配置"""
        self.result_text.tag_config("header", foreground=WC_COLORS["accent"])
        self.result_text.tag_config("separator", foreground=WC_COLORS["border"])
        self.result_text.tag_config("contact", foreground=WC_COLORS["online_dot"])
        self.result_text.tag_config("msg", foreground=WC_COLORS["text"])
        self.result_text.tag_config("time", foreground=WC_COLORS["text_muted"])

    def _search_messages(self):
        """搜索消息 - 同时搜索数据库和内存历史"""
        keyword = self.search_entry_msg.get().strip()
        if not keyword:
            self._on_log("warning", "[搜索] 请输入关键词")
            return

        self._on_log("info", f"[搜索] 正在搜索: {keyword}")
        all_results = []
        db_count = 0
        mem_count = 0

        try:
            if self.engine and self.engine.storage:
                db_results = self.engine.storage.query(keyword=keyword, limit=200)
                db_count = len(db_results)
                for r in db_results:
                    all_results.append({
                        "contact": r.get("contact", "?"),
                        "sender": r.get("sender", "other"),
                        "raw_text": r.get("raw_text", ""),
                        "timestamp": r.get("timestamp", ""),
                        "is_important": r.get("is_important", False),
                        "source": "数据库",
                    })
        except Exception as e:
            self._on_log("error", f"[搜索] 数据库查询失败: {e}")

        try:
            for contact, msgs in self._messages_store.items():
                for msg in msgs:
                    c_text = str(msg.get("content", ""))
                    if keyword.lower() in c_text.lower():
                        mem_count += 1
                        all_results.append({
                            "contact": contact,
                            "sender": msg.get("sender", "other"),
                            "raw_text": c_text,
                            "timestamp": msg.get("timestamp", ""),
                            "is_important": msg.get("is_important", False),
                            "source": "实时",
                        })
        except Exception as e:
            self._on_log("error", f"[搜索] 内存搜索失败: {e}")

        seen = set()
        deduped = []
        for r in all_results:
            key = (r["contact"], r["raw_text"])
            if key not in seen:
                seen.add(key)
                deduped.append(r)

        self._on_log("info", f"[搜索] 数据库:{db_count} 实时:{mem_count} 合计(去重):{len(deduped)}")

        try:
            self._ensure_result_tags()
            self.result_text.configure(state="normal")
            self.result_text.delete("1.0", "end")

            if not deduped:
                self.result_text.insert("end", "未找到匹配消息\n")
            else:
                contacts = {}
                for r in deduped:
                    name = r["contact"]
                    if name not in contacts:
                        contacts[name] = []
                    contacts[name].append(r)

                self.result_text.insert("end", f"搜索: \"{keyword}\" — 共 {len(deduped)} 条 (DB:{db_count} 实时:{mem_count})\n", "header")
                self.result_text.insert("end", "-" * 50 + "\n\n", "separator")

                for name in sorted(contacts.keys()):
                    msgs = contacts[name]
                    self.result_text.insert("end", f"  {name} ({len(msgs)}条)\n", "contact")
                    for msg in msgs:
                        s = msg["sender"]
                        sender = "我" if s == "me" else msg.get("contact", "对方")
                        important = " ★" if msg["is_important"] else ""
                        src = f"[{msg.get('source', '')}] " if msg.get("source") else ""
                        self.result_text.insert("end", f"    {src}[{sender}]{important} {msg['raw_text'][:60]}\n", "msg")
                        self.result_text.insert("end", f"       {msg['timestamp']}\n", "time")
                    self.result_text.insert("end", "\n")

            self.result_text.configure(state="disabled")
        except Exception as e:
            self._on_log("error", f"[搜索] 渲染结果失败: {e}")

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
        report_dir = _app_path("data", "reports")
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


def _install_crash_handler():
    """全局崩溃捕获：把任何未捕获异常（含 tkinter 回调里的）写 crash.log 并弹窗。
    解决“点击后静默闪退无信息”的问题。"""
    import sys as _s
    import tkinter as _tk

    crash_path = os.path.join(APP_BASE, "crash.log")

    # faulthandler：C 扩展崩溃（paddle/opencv segfault）时，写出崩溃瞬间正在执行的
    # Python 代码行（sys.excepthook 抓不到 C 崩溃，进程直接被 OS 杀）。这是定位
    # “点开始监控就静默闪退”的唯一可靠手段。
    try:
        import faulthandler
        _fh = open(crash_path, "a", encoding="utf-8")
        # 仅启用 faulthandler：真正的 C 扩展崩溃（paddle/opencv segfault）时，
        # 会自动把崩溃瞬间 traceback 写进 crash.log，且不杀进程。
        # 注意：绝不能加 dump_traceback_later(N, exit=True) —— GUI 主循环本就
        # 长期阻塞等待事件（用户不操作就一直 running），会被误判“卡死”而自杀退出。
        faulthandler.enable(_fh)
    except Exception:
        pass

    def _handler(_exc_type, _exc_val, _exc_tb):
        import traceback as _tb
        try:
            msg = "".join(_tb.format_exception(_exc_type, _exc_val, _exc_tb))
        except Exception:
            msg = f"{_exc_type}: {_exc_val}\n"
        try:
            with open(crash_path, "a", encoding="utf-8") as _f:
                _f.write("=" * 60 + "\n" + msg + "\n")
        except Exception:
            pass
        try:
            messagebox.showerror(
                "程序异常（已记录到 crash.log）",
                f"发生未捕获异常，请截图下方内容或打开 crash.log 发给我：\n\n{str(_exc_val)[:600]}")
        except Exception:
            pass

    # 主线程未捕获异常
    _s.excepthook = _handler
    # tkinter after/bind 回调里的异常（默认被静默忽略，是“闪退”的常见真凶）
    try:
        _tk.Tk.report_callback_exception = staticmethod(_handler)
    except Exception:
        pass


def run_ui(config_path="config.yaml"):
    """启动UI应用"""
    _install_crash_handler()
    app = WeChatAIApp(config_path)
    app.mainloop()


if __name__ == "__main__":
    run_ui()