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
import re
import json
import threading
import time
import yaml
import customtkinter as ctk
from tkinter import messagebox, filedialog
from datetime import datetime

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

# ============================================================
# V3 UI 配色：严格微信 PC 原版风格（废弃像素风/MC风）
# 参考：
#   - 左侧导航条:        #2C2C2C （微信PC左侧深蓝灰）
#   - 会话列表:          #EBEBEB / 选中 #D6D6D6 / 悬停 #DDDDDD
#   - 聊天区背景:        #F5F5F5 （微信PC聊天页白底微灰）
#   - 聊天顶部栏:        #EDEDED
#   - 搜索框背景:        #DCDCDC / #E8E8E8
#   - 自己气泡:          #95EC69 （官方微信PC 经典草绿，带尖角）
#   - 对方气泡:          #FFFFFF （白色圆角）
#   - 重要高亮:          #FFF2CC （微信PC 置顶/重要 淡米黄）
#   - 主绿色(按钮/在线点) #07C160
#   - 标题文字:          #191919
#   - 次要文字:          #888888 / #999999
#   - 分割线:            #E5E5E5
# ============================================================
WC_COLORS = {
    # === 背景层 ===
    "bg": "#F5F5F5",                  # 聊天页背景（微灰接近白）
    "bg_dark": "#EDEDED",             # 聊天顶部栏
    "sidebar": "#2C2C2C",             # 微信PC左侧导航
    "sidebar_hover": "#383838",       # 悬停
    "sidebar_active": "#07C160",      # 选中绿
    "card": "#FFFFFF",                # 会话列表卡片 / 提取面板卡片
    "card_hover": "#DDDDDD",          # 会话悬停
    "card_active": "#D6D6D6",         # 会话选中（微信PC会话灰）
    "header": "#EDEDED",              # 聊天顶部栏 / 面板标题

    # === 强调色（微信官方色）===
    "accent": "#07C160",              # 微信绿
    "accent_hover": "#06AE56",
    "accent_light": "#95EC69",        # 自己气泡底色
    "danger": "#FA5151",              # 官方微信红（重要）
    "danger_light": "#FFE5E5",
    "warning": "#FFF2CC",             # 重要高亮（米黄底）
    "info": "#1485EE",                # 微信蓝（链接/提取）
    "info_light": "#E8F3FF",
    "summary": "#888888",             # 摘要灰
    "keyword": "#FF8A00",             # 微信橙（关键词）

    # === 文字 ===
    "text": "#191919",                # 主文字 深灰
    "text_muted": "#888888",          # 次要文字（时间/副标题）
    "text_muted2": "#B2B2B2",         # 更次（会话预览副文字）
    "text_dark": "#FFFFFF",           # 深底白字

    # === 边框/分割 ===
    "border": "#E5E5E5",
    "border_light": "#EEEEEE",
    "shadow": "#00000020",            # 轻阴影

    # === 微信PC气泡 ===
    "bubble_self": "#95EC69",
    "bubble_self_border": "#95EC69",
    "bubble_other": "#FFFFFF",
    "bubble_other_border": "#F0F0F0",
    "bubble_important": "#FFF2CC",
    "bubble_important_border": "#FFD98A",

    # === 头像（默认色块，未启用自定义头像图片时）===
    "avatar_me": "#07C160",
    "avatar_other": "#DCDCDC",

    # === 状态 ===
    "online_dot": "#07C160",
    "offline_dot": "#888888",
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

        # ====== 顶部控制条（微信 PC 风格圆角 8px） ======
        ctrl = ctk.CTkFrame(tab, fg_color=WC_COLORS["header"], corner_radius=8, height=54)
        ctrl.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 4))
        ctrl.grid_columnconfigure(8, weight=1)

        self.btn_start = ctk.CTkButton(
            ctrl, text="▶ 开始监控", width=110, height=32,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=WC_COLORS["accent"], hover_color=WC_COLORS["accent_hover"],
            corner_radius=6, command=self.start_monitoring,
        )
        self.btn_start.grid(row=0, column=0, padx=(12, 4), pady=11)

        self.btn_stop = ctk.CTkButton(
            ctrl, text="■ 停止", width=80, height=32, corner_radius=6,
            font=ctk.CTkFont(size=12),
            fg_color=WC_COLORS["danger"], hover_color="#E04848",
            command=self.stop_monitoring, state="disabled",
        )
        self.btn_stop.grid(row=0, column=1, padx=4, pady=11)

        self.btn_test = ctk.CTkButton(
            ctrl, text="🔍 识别", width=72, height=32, corner_radius=6,
            font=ctk.CTkFont(size=12),
            fg_color="#8A8A8A", hover_color="#7A7A7A",
            command=self.test_ocr,
        )
        self.btn_test.grid(row=0, column=2, padx=4, pady=11)

        self.btn_preview = ctk.CTkButton(
            ctrl, text="🖼 预览", width=72, height=32, corner_radius=6,
            fg_color="#5B6BE7", hover_color="#4A59D0",
            font=ctk.CTkFont(size=12), command=self._open_preview_window)
        self.btn_preview.grid(row=0, column=3, padx=4, pady=11)

        self.btn_show_wechat = ctk.CTkButton(
            ctrl, text="👁 显示微信", width=96, height=32, corner_radius=6,
            font=ctk.CTkFont(size=12),
            fg_color=WC_COLORS["accent"], hover_color=WC_COLORS["accent_hover"],
            command=self._show_wechat_window,
        )
        self.btn_show_wechat.grid(row=0, column=4, padx=4, pady=11)

        # 顶栏右侧：当前会话显示
        self.top_current_label = ctk.CTkLabel(
            ctrl, text="当前会话：未连接",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=WC_COLORS["text"], anchor="e",
        )
        self.top_current_label.grid(row=0, column=8, padx=(10, 16), sticky="e")

        # ====== V3 统计信息（微信绿/红/灰 官方色，扁平圆角） ======
        stats_frame = ctk.CTkFrame(tab, fg_color=WC_COLORS["card"], corner_radius=8,
                                   border_width=1, border_color=WC_COLORS["border"])
        stats_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=(4, 6))
        for i in range(6):
            stats_frame.grid_columnconfigure(i, weight=1)

        self.stat_labels = {}
        stat_items = [
            ("frames",    "📷 截图",   WC_COLORS["info"]),
            ("ocr",       "🔤 OCR",   "#9B59B6"),
            ("messages",  "💬 消息",   WC_COLORS["accent"]),
            ("extracted", "📋 提取",   WC_COLORS["keyword"]),
            ("important", "⭐ 重要",   WC_COLORS["danger"]),
            ("replies",   "✉️ 回复",   "#7B5EE0"),
        ]
        for i, (key, label, val_color) in enumerate(stat_items):
            col = ctk.CTkFrame(stats_frame, fg_color="transparent")
            col.grid(row=0, column=i, padx=4, pady=8, sticky="nsew")
            col.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(col, text=label,
                         font=ctk.CTkFont(size=11),
                         text_color=WC_COLORS["text_muted"]).grid(
                row=0, column=0, pady=(0, 2), sticky="ew")
            val_label = ctk.CTkLabel(col, text="0",
                                     font=ctk.CTkFont(size=22, weight="bold"),
                                     text_color=val_color)
            val_label.grid(row=1, column=0, sticky="ew")
            self.stat_labels[key] = val_label

        # ====== V3: 微信 PC 三栏主体 ======
        three_col = ctk.CTkFrame(tab, fg_color="transparent")
        three_col.grid(row=2, column=0, sticky="nsew", padx=10, pady=(4, 10))
        three_col.grid_columnconfigure(0, weight=0, minsize=220)
        three_col.grid_columnconfigure(1, weight=3)
        three_col.grid_columnconfigure(2, weight=0, minsize=280)
        three_col.grid_rowconfigure(0, weight=1)

        # 左：会话列表（微信 PC #EBEBEB 会话灰背景）
        contacts_col = ctk.CTkFrame(three_col, fg_color="#EBEBEB", corner_radius=8,
                                     width=240, border_width=1, border_color=WC_COLORS["border"])
        contacts_col.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        contacts_col.grid_propagate(True)
        self._create_contact_list_panel(contacts_col)

        # 中：聊天消息 + 日志 Tabview（微信PC聊天页 #F5F5F5）
        center_col = ctk.CTkFrame(three_col, fg_color=WC_COLORS["bg"], corner_radius=8,
                                 border_width=1, border_color=WC_COLORS["border"])
        center_col.grid(row=0, column=1, sticky="nsew", padx=2)
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
            font=ctk.CTkFont(size=10),
            text_color=WC_COLORS["online_dot"], anchor="e",
        )
        self.chat_subtitle.grid(row=0, column=1, padx=14, pady=10, sticky="e")

        # 中栏 Tabview（消息 / 日志）
        self.view_tabview = ctk.CTkTabview(
            center_col, fg_color=WC_COLORS["bg"], corner_radius=0,
            border_width=0, segmented_button_fg_color=WC_COLORS["header"],
            segmented_button_selected_color="#FFFFFF",
            segmented_button_selected_hover_color="#F9F9F9",
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
            log_frame, font=ctk.CTkFont(size=12),
            fg_color="#FAFAFA", text_color=WC_COLORS["text"],
            wrap="word", state="disabled",
            border_width=1, border_color=WC_COLORS["border"], corner_radius=4,
        )
        self.log_text.grid(row=0, column=0, sticky="nsew", pady=(0, 4))

        # 日志底部内置"最新提取结果"
        result_frame = ctk.CTkFrame(log_frame, fg_color="#FAFAFA", corner_radius=4,
                                     border_width=1, border_color=WC_COLORS["border"])
        result_frame.grid(row=1, column=0, sticky="nsew")
        result_frame.grid_columnconfigure(0, weight=1)
        result_frame.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(result_frame, text="📋 最新提取结果",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=WC_COLORS["text"]).grid(
            row=0, column=0, sticky="w", padx=10, pady=(8, 4))
        self.result_text = ctk.CTkTextbox(
            result_frame, font=ctk.CTkFont(size=12),
            fg_color="#FFFFFF", text_color=WC_COLORS["text"],
            wrap="word", state="disabled",
        )
        self.result_text.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 8))

        # 预览：嵌入中栏
        self._create_main_preview(tab_preview)

        # 右：详情面板（关键词/提取字段/摘要/置信度）
        detail_col = ctk.CTkFrame(three_col, fg_color=WC_COLORS["card"], corner_radius=8,
                                   width=300, border_width=1, border_color=WC_COLORS["border"])
        detail_col.grid(row=0, column=2, sticky="nsew", padx=(4, 0))
        self._create_detail_panel(detail_col)

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
            search_wrap, placeholder_text="🔍 搜索", height=28, corner_radius=4,
            fg_color="#DCDCDC", placeholder_text_color=WC_COLORS["text_muted"],
            text_color=WC_COLORS["text"], border_width=0,
            font=ctk.CTkFont(size=11),
        )
        self.contact_search_entry.pack(fill="x", side="top")

        # 会话列表滚动
        self.contact_list_frame = ctk.CTkScrollableFrame(
            parent, fg_color="#EBEBEB", corner_radius=0,
            scrollbar_button_color="#C8C8C8", scrollbar_button_hover_color="#AAAAAA",
        )
        self.contact_list_frame.grid(row=1, column=0, sticky="nsew", padx=0, pady=(4, 0))

        # 会话卡片集合 {contact: (frame, title_lbl, preview_lbl, dot_lbl, badge_lbl)}
        self._contact_cards = {}
        self._active_contact = None
        # 按联系人划分的消息池：contact -> [msg_data, ...]，最新在前（索引0）。
        # 点击会话卡时据此重建右侧消息列表，避免 pack_forget 过滤导致的空白/顺序错乱。
        # 持久化到 data/messages_history.json（防抖写盘），重启后保留历史。
        self._history_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "data", "messages_history.json")
        self._history_save_job = None      # after() 句柄（防抖）
        self._history_dirty = False
        self._messages_store = {}
        self._msg_seq = 0
        self._load_history()

        # 初始占位会话卡（点击不会有效果，仅做引导）
        self._append_contact_card("会话名称", "启动监控后自动汇聚会话…",
                                  is_group=False, unread=0, active=False)

    def _load_history(self):
        """启动时加载消息历史（data/messages_history.json）"""
        try:
            if os.path.exists(self._history_path):
                with open(self._history_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    for contact, msgs in raw.items():
                        if not isinstance(msgs, list) or not msgs:
                            continue
                        cleaned = []
                        for m in msgs:
                            if isinstance(m, dict) and m.get("content") is not None:
                                cleaned.append(m)
                        if cleaned:
                            self._messages_store[str(contact)] = cleaned
                    # 恢复 seq：取全局最大 _seq + 1
                    max_seq = 0
                    for msgs in self._messages_store.values():
                        for m in msgs:
                            if isinstance(m.get("_seq"), int):
                                max_seq = max(max_seq, m["_seq"])
                    self._msg_seq = max_seq + 1
        except Exception:
            pass

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
        """把消息池写盘（最新在前，按 JSON list 保存）"""
        self._history_save_job = None
        if not self._history_dirty:
            return
        self._history_dirty = False
        try:
            with open(self._history_path, "w", encoding="utf-8") as f:
                json.dump(self._messages_store, f, ensure_ascii=False, indent=1)
        except Exception:
            pass

    def _append_contact_card(self, contact, preview_text, is_group=False,
                              unread=0, active=False):
        """V3: 新建或更新一个"会话卡片"（微信PC会话卡 #EBEBEB 悬停/选中变灰）"""
        import tkinter as tk

        if contact in self._contact_cards:
            info = self._contact_cards[contact]
            info["preview"].configure(text=preview_text[:26])
            info["is_group"] = is_group
            info["unread"] += unread
            un = info["unread"]
            if un > 0:
                info["badge"].configure(text=str(un) if un < 99 else "99+")
                info["badge"].pack(side="right", padx=(0, 6))
            else:
                info["badge"].pack_forget()
            if active:
                self._set_active_contact(contact)
            return info["frame"]

        # 新卡片
        card = tk.Frame(self.contact_list_frame, bg="#EBEBEB", height=56,
                        highlightthickness=0)
        card.pack(fill="x", side="top")
        card.pack_propagate(False)

        # 头像（左）— 自己绿/对方灰，群聊加两个小人标识
        avatar_bg = WC_COLORS["avatar_me"] if not is_group else "#2AA7E3"
        avatar_char = "👥" if is_group else "👤"
        avatar = ctk.CTkLabel(card, text=avatar_char, width=36, height=36, corner_radius=4,
                              fg_color=avatar_bg, text_color="#FFFFFF",
                              font=ctk.CTkFont(size=15))
        avatar.pack(side="left", padx=(10, 8), pady=10)

        # 文字区
        text_wrap = tk.Frame(card, bg="#EBEBEB")
        text_wrap.pack(side="left", fill="both", expand=True, pady=9)

        header_row = tk.Frame(text_wrap, bg="#EBEBEB")
        header_row.pack(fill="x")

        title_lbl = tk.Label(header_row, text=contact, bg="#EBEBEB", fg=WC_COLORS["text"],
                             font=("Microsoft YaHei", 12, "bold"), anchor="w")
        title_lbl.pack(side="left")

        # 未读红点 Badge（微信PC 红色小圆标，初始pack_forget）
        badge = tk.Label(header_row, text="1", bg=WC_COLORS["danger"], fg="#FFFFFF",
                         font=("Microsoft YaHei", 9, "bold"),
                         padx=5, pady=0, borderwidth=0)

        preview = tk.Label(text_wrap, text=preview_text[:26], bg="#EBEBEB",
                           fg=WC_COLORS["text_muted2"],
                           font=("Microsoft YaHei", 10), anchor="w")
        preview.pack(fill="x", side="top")

        def _on_enter(_e):
            for w in (card, text_wrap, header_row):
                w.configure(bg=WC_COLORS["card_hover"])
            title_lbl.configure(bg=WC_COLORS["card_hover"])
            preview.configure(bg=WC_COLORS["card_hover"])

        def _on_leave(_e):
            bg_new = WC_COLORS["card_active"] if self._active_contact == contact else "#EBEBEB"
            for w in (card, text_wrap, header_row):
                w.configure(bg=bg_new)
            title_lbl.configure(bg=bg_new)
            preview.configure(bg=bg_new)

        def _on_click(_e):
            self._set_active_contact(contact)

        for w in (card, avatar, text_wrap, header_row, title_lbl, preview):
            w.bind("<Enter>", _on_enter)
            w.bind("<Leave>", _on_leave)
            w.bind("<Button-1>", _on_click)

        self._contact_cards[contact] = {
            "frame": card, "title": title_lbl, "preview": preview,
            "badge": badge, "is_group": is_group, "unread": unread,
            "avatar": avatar,
        }

        if active:
            self._set_active_contact(contact)
        else:
            if unread > 0:
                badge.configure(text=str(unread) if unread < 99 else "99+")
                badge.pack(side="right", padx=(0, 8))
        return card

    def _set_active_contact(self, contact):
        """V3: 点击选中会话卡时，卡片背景变 #D6D6D6 模拟微信PC会话选中灰"""
        self._active_contact = contact
        # 切换会话 → 按联系人重建消息列表（只显示当前会话的消息，避免过滤导致的空白）
        self._rebuild_message_list()
        for name, info in self._contact_cards.items():
            is_active = (name == contact)
            bg = WC_COLORS["card_active"] if is_active else "#EBEBEB"
            for w in (info["frame"], info["title"].master, info["title"].master.master):
                try:
                    w.configure(bg=bg)
                except Exception:
                    pass
            try:
                info["title"].configure(bg=bg)
                info["preview"].configure(bg=bg)
            except Exception:
                pass
            if is_active:
                info["unread"] = 0
                info["badge"].pack_forget()
        # 标题栏同步
        try:
            is_group = bool(self._contact_cards.get(contact, {}).get("is_group"))
            prefix = "👥" if is_group else "💬"
            self.chat_title.configure(text=f"{prefix} {contact}")
            if hasattr(self, "top_current_label"):
                self.top_current_label.configure(
                    text=f"当前会话：{contact} {'（群聊）' if is_group else ''}")
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
            row, text=avatar_text, width=36, height=36, corner_radius=4,
            fg_color=avatar_bg, text_color="#FFFFFF",
            font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"),
        )

        # 消息气泡 + 昵称/时间
        text_col = ctk.CTkFrame(row, fg_color="transparent")

        if is_self:
            # 右对齐（微信风格）：头像最右，气泡贴右（靠头像），时间/分类在气泡左侧
            avatar.pack(side="right", padx=(6, 10))
            text_col.pack(side="right", fill="x", expand=True)

            bubble_bg = WC_COLORS["bubble_self"]
            bubble_text_color = "#111111"

            wrap = ctk.CTkFrame(text_col, fg_color="transparent")
            wrap.pack(side="right")      # 先 pack → 最右（贴头像）
            outer_bubble = ctk.CTkFrame(wrap, fg_color=bubble_bg, corner_radius=4)
            outer_bubble.pack(side="right", anchor="e")
            content_lbl = ctk.CTkLabel(
                outer_bubble, text=content[:360] if len(content) <= 360 else content[:360] + "…",
                font=ctk.CTkFont(family="Microsoft YaHei", size=12),
                text_color=bubble_text_color, anchor="w", justify="left",
                wraplength=380,
                padx=10, pady=7,
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
                font=ctk.CTkFont(size=9),
                text_color=WC_COLORS["text_muted2"])
            time_lbl.pack(side="right", padx=(6, 0), pady=(20, 0), anchor="s")

            if cls:
                cls_chip = ctk.CTkLabel(
                    text_col, text=f"🗂 {cls}{(' P' + str(int(pri))) if pri else ''}",
                    font=ctk.CTkFont(size=10, weight="bold"),
                    text_color="#FFFFFF", fg_color=WC_COLORS["info"])
                cls_chip.pack(side="right", padx=(6, 0), pady=(20, 0), anchor="s")
        else:
            avatar.pack(side="left", padx=(10, 6))
            text_col.pack(side="left", fill="x", expand=True)

            if is_group and group_member:
                member_lbl = ctk.CTkLabel(
                    text_col, text=str(group_member),
                    font=ctk.CTkFont(size=10),
                    text_color=WC_COLORS["member_name"], anchor="w",
                )
                member_lbl.pack(side="top", anchor="w", padx=(2, 0), pady=(0, 2))

            wrap = ctk.CTkFrame(text_col, fg_color="transparent")
            wrap.pack(side="left", fill="x")

            outer_bubble = ctk.CTkFrame(wrap, fg_color="#FFFFFF", corner_radius=4,
                                        border_width=1, border_color=WC_COLORS["border_light"])
            outer_bubble.pack(side="left", anchor="w")
            content_lbl = ctk.CTkLabel(
                outer_bubble, text=content[:360] if len(content) <= 360 else content[:360] + "…",
                font=ctk.CTkFont(family="Microsoft YaHei", size=12),
                text_color=WC_COLORS["text"], anchor="w", justify="left",
                wraplength=380,
                padx=10, pady=7,
            )
            content_lbl.pack(anchor="w")

            time_lbl = ctk.CTkLabel(
                text_col, text=timestamp,
                font=ctk.CTkFont(size=9),
                text_color=WC_COLORS["text_muted2"])
            time_lbl.pack(side="left", padx=(6, 0), pady=(20, 0), anchor="s")

            if cls:
                cls_chip = ctk.CTkLabel(
                    text_col, text=f"🗂 {cls}{(' P' + str(int(pri))) if pri else ''}",
                    font=ctk.CTkFont(size=10, weight="bold"),
                    text_color="#FFFFFF", fg_color=WC_COLORS["info"])
                cls_chip.pack(side="left", padx=(6, 0), pady=(20, 0), anchor="s")

            if is_important:
                star = ctk.CTkLabel(text_col, text="⭐",
                                    font=ctk.CTkFont(size=11),
                                    text_color=WC_COLORS["danger"], fg_color="transparent")
                star.pack(side="left", padx=(4, 0), pady=(18, 0), anchor="s")

        # 顶部对齐（调用方按"最新在前"顺序逐条打包，实现最新置顶）
        row.pack(side="top", fill="x", padx= 6, pady=4)
        return row

    def _rebuild_message_list(self):
        """按当前选中联系人重建右侧消息列表：单联系人显示该会话，None 显示全部（最新置顶）。"""
        try:
            for c in list(self.msg_list_frame_inner.winfo_children()):
                try:
                    c.destroy()
                except Exception:
                    pass
            self.msg_empty_label = None

            active = self._active_contact
            if active is None:
                items = []
                for msgs in self._messages_store.values():
                    items.extend(msgs)
                items.sort(key=lambda x: x.get("_seq", 0), reverse=True)
            else:
                items = list(self._messages_store.get(active, []))

            if not items:
                txt = "暂无聊天记录" if active is None else f"暂无与「{active}」的聊天记录"
                self.msg_empty_label = ctk.CTkLabel(
                    self.msg_list_frame_inner, text=txt,
                    font=ctk.CTkFont(size=12), text_color=WC_COLORS["text_muted"])
                self.msg_empty_label.pack(pady=30)
                return

            # items 已是最新在前（store.insert(0,...) / 全局倒序），逐条 pack 顶部即最新置顶
            for m in items:
                self._build_message_row(self.msg_list_frame_inner, m)
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
            name_e = ctk.CTkEntry(row, width=90, font=ctk.CTkFont(size=12))
            name_e.insert(0, cat["name"])
            name_e.pack(side="left", padx=4)
            pri_e = ctk.CTkEntry(row, width=50, font=ctk.CTkFont(size=12))
            pri_e.insert(0, str(cat["priority"]))
            pri_e.pack(side="left", padx=4)
            imp_var = ctk.BooleanVar(value=bool(cat["important"]))
            imp_sw = ctk.CTkSwitch(row, text="重要", variable=imp_var, width=64)
            if cat["important"]:
                imp_sw.select()
            imp_sw.pack(side="left", padx=4)
            kw_e = ctk.CTkEntry(row, width=300, font=ctk.CTkFont(size=12))
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

    def _save_classification_rules(self):
        try:
            cats = self._collect_classification_cats()
            cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")
            with open(cfg_path, "r", encoding="utf-8") as f:
                text = f.read()
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

        # 分类优先级规则
        cls_frame = ctk.CTkFrame(scroll, fg_color=WC_COLORS["card"], corner_radius=8)
        cls_frame.pack(fill="x", padx=20, pady=10)
        ctk.CTkLabel(cls_frame, text="📑 分类优先级规则",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=WC_COLORS["text"]).pack(anchor="w", padx=15, pady=(10, 2))
        ctk.CTkLabel(cls_frame, text="为消息归类并设定优先级（工作/私事/群聊…）。命中 important 的类即标记为重要消息；群聊无需关键词，按会话类型自动归类。",
                     font=ctk.CTkFont(size=11), text_color="gray").pack(anchor="w", padx=15)

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
            scrollbar_button_color="#D5D5D5", scrollbar_button_hover_color="#B8B8B8",
        )
        self.msg_list_frame.pack(fill="both", expand=True)
        self.msg_list_frame_inner = self.msg_list_frame

        # 空状态（空的微信聊天小提示）
        self.msg_empty_label = ctk.CTkFrame(
            self.msg_list_frame_inner, fg_color="transparent")
        self.msg_empty_label.pack(pady=36, padx=20, fill="x")

        ctk.CTkLabel(
            self.msg_empty_label,
            text="暂无新消息",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=WC_COLORS["text_muted"],
        ).pack(pady=(0, 6))
        ctk.CTkLabel(
            self.msg_empty_label,
            text="开启监控后，这里会实时显示识别到的聊天消息",
            font=ctk.CTkFont(size=11),
            text_color=WC_COLORS["text_muted2"],
            wraplength=420, justify="center",
        ).pack()

    # ==============================================================
    # V3: 右侧详情面板（关键词/提取字段/摘要/置信度）
    # ==============================================================
    def _create_detail_panel(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(4, weight=1)

        title = ctk.CTkLabel(
            parent, text="📋 消息详情",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=WC_COLORS["text"],
        )
        title.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))

        # 基础信息区
        self.detail_info_frame = ctk.CTkFrame(
            parent, fg_color="#FAFAFA", corner_radius=6,
            border_width=1, border_color=WC_COLORS["border_light"])
        self.detail_info_frame.grid(row=1, column=0, sticky="ew",
                                     padx=12, pady=(0, 8))
        self.detail_info_frame.grid_columnconfigure(1, weight=1)

        self._detail_labels = {}
        info_rows = [
            ("contact",  "会话"),
            ("sender",   "发送方"),
            ("time",     "时间"),
            ("conf",     "OCR置信度"),
        ]
        for i, (key, name) in enumerate(info_rows):
            ctk.CTkLabel(self.detail_info_frame, text=name,
                         font=ctk.CTkFont(size=10),
                         text_color=WC_COLORS["text_muted"]).grid(
                row=i, column=0, padx=(10, 4), pady=4, sticky="nw")
            val = ctk.CTkLabel(self.detail_info_frame, text="—",
                               font=ctk.CTkFont(size=10),
                               text_color=WC_COLORS["text"],
                               anchor="w", justify="left", wraplength=160)
            val.grid(row=i, column=1, padx=(0, 10), pady=4, sticky="ew")
            self._detail_labels[key] = val

        # 标签摘要（关键词/重要/提取）
        ctk.CTkLabel(parent, text="🏷 标签",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=WC_COLORS["text"]).grid(
            row=2, column=0, sticky="w", padx=14, pady=(6, 4))

        self.detail_tags_frame = ctk.CTkFrame(
            parent, fg_color="#FAFAFA", corner_radius=6,
            border_width=1, border_color=WC_COLORS["border_light"])
        self.detail_tags_frame.grid(row=3, column=0, sticky="ew",
                                     padx=12, pady=(0, 8))
        self._detail_tags_placeholder = ctk.CTkLabel(
            self.detail_tags_frame, text="(等待消息)",
            font=ctk.CTkFont(size=10), text_color=WC_COLORS["text_muted"])
        self._detail_tags_placeholder.pack(padx=10, pady=8, anchor="w")

        # 提取字段区（可滚动）
        ctk.CTkLabel(parent, text="💎 提取字段 / 内容摘要",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=WC_COLORS["text"]).grid(
            row=4, column=0, sticky="nw", padx=14, pady=(2, 4))

        self.detail_fields_frame = ctk.CTkScrollableFrame(
            parent, fg_color="#FAFAFA", corner_radius=6,
            border_width=1, border_color=WC_COLORS["border_light"])
        self.detail_fields_frame.grid(row=5, column=0, sticky="nsew",
                                       padx=12, pady=(0, 12))
        parent.grid_rowconfigure(5, weight=4)

        self._detail_content_label = ctk.CTkLabel(
            self.detail_fields_frame, text="(等待新消息)",
            font=ctk.CTkFont(size=10), text_color=WC_COLORS["text_muted"],
            anchor="nw", justify="left", wraplength=240,
        )
        self._detail_content_label.pack(padx=10, pady=8, fill="x", anchor="w")

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
            font=ctk.CTkFont(size=10),
            text_color=WC_COLORS["text_muted"], anchor="e",
        )
        self.main_preview_status.grid(row=0, column=1, padx=10, sticky="e")

        self.main_preview_label = tk.Label(
            parent, bg="#E8E8E8",
            highlightbackground=WC_COLORS["border"], highlightthickness=1)
        self.main_preview_label.grid(row=1, column=0, sticky="nsew", padx=8, pady=8)
        try:
            placeholder = Image.new("RGB", (480, 320), color="#F0F0F0")
            self._main_placeholder_photo = ImageTk.PhotoImage(placeholder)
            self.main_preview_label.configure(image=self._main_placeholder_photo)
            self.main_preview_label.image = self._main_placeholder_photo
        except Exception:
            pass

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

    def _build_tag_chip(self, parent, text, color_bg, color_fg, icon=""):
        """V2: 彩色标签小胶囊（关键词/重要/提取信息/摘要）"""
        txt = f"{icon} {text}" if icon else text
        chip = ctk.CTkLabel(
            parent, text=txt,
            fg_color=color_bg, text_color=color_fg, corner_radius=4,
            font=ctk.CTkFont(size=10, weight="bold"),
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
        - 最新消息放顶部（用户偏好）。
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
        mk = msg_data.get("msg_key")
        store = self._messages_store.setdefault(contact, [])
        if mk:
            for _i, _m in enumerate(store):
                if _m.get("msg_key") == mk:
                    merged = dict(_m)
                    merged.update(msg_data)
                    merged["_seq"] = _m.get("_seq")
                    store[_i] = merged
                    try:
                        self._update_detail_panel(merged)
                    except Exception:
                        pass
                    self._schedule_history_save()
                    self._rebuild_message_list()
                    return
        self._msg_seq += 1
        stored = dict(msg_data)
        stored["_seq"] = self._msg_seq
        store.insert(0, stored)      # 最新置顶
        if len(store) > 80:
            del store[80:]
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

        # —— 按当前选中联系人重建右侧消息列表（修复：点击联系人空白 / 需两次点击）——
        self._rebuild_message_list()


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
                font=ctk.CTkFont(size=10), text_color=WC_COLORS["text_muted"])
            placeholder.pack(padx=10, pady=8, anchor="w")
        else:
            tags_wrap = ctk.CTkFrame(self.detail_tags_frame, fg_color="transparent")
            tags_wrap.pack(fill="x", padx=8, pady=6)
            for i, (txt, bg, fg) in enumerate(tags):
                chip = ctk.CTkLabel(tags_wrap, text=txt, fg_color=bg, text_color=fg,
                                    corner_radius=4, padx=6, pady=2,
                                    font=ctk.CTkFont(size=10, weight="bold"))
                chip.grid(row=i // 3, column=i % 3, padx=2, pady=2, sticky="w")

        # 提取字段区：正文 + 字段 + 摘要
        for w in self.detail_fields_frame.winfo_children():
            try:
                w.destroy()
            except Exception:
                pass

        ctk.CTkLabel(self.detail_fields_frame,
                     text="正文：",
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color=WC_COLORS["text_muted"], anchor="w").pack(
            padx=10, pady=(8, 2), fill="x")
        content_label = ctk.CTkLabel(
            self.detail_fields_frame, text=content[:1200],
            font=ctk.CTkFont(size=10), text_color=WC_COLORS["text"],
            wraplength=240, anchor="w", justify="left",
        )
        content_label.pack(padx=10, pady=(0, 6), anchor="w", fill="x")

        extracted = msg_data.get("extracted_fields") or msg_data.get("extracted") or {}
        if extracted and isinstance(extracted, dict):
            ctk.CTkLabel(self.detail_fields_frame,
                         text="字段：",
                         font=ctk.CTkFont(size=10, weight="bold"),
                         text_color=WC_COLORS["text_muted"], anchor="w").pack(
                padx=10, pady=(2, 2), fill="x")
            for k, v in extracted.items():
                rowf = ctk.CTkFrame(self.detail_fields_frame, fg_color="transparent")
                rowf.pack(fill="x", padx=10, pady=1)
                ctk.CTkLabel(rowf, text=f"{k}:",
                             font=ctk.CTkFont(size=10, weight="bold"),
                             text_color=WC_COLORS["info"], anchor="w", width=60).pack(
                    side="left")
                ctk.CTkLabel(rowf, text=str(v)[:60],
                             font=ctk.CTkFont(size=10),
                             text_color=WC_COLORS["text"],
                             anchor="w", justify="left", wraplength=180).pack(
                    side="left", fill="x", expand=True)

        llm = msg_data.get("llm_analysis") or {}
        if isinstance(llm, dict) and llm.get("summary"):
            ctk.CTkLabel(self.detail_fields_frame,
                         text="摘要：",
                         font=ctk.CTkFont(size=10, weight="bold"),
                         text_color=WC_COLORS["text_muted"], anchor="w").pack(
                padx=10, pady=(6, 2), fill="x")
            ctk.CTkLabel(self.detail_fields_frame,
                         text=str(llm["summary"])[:600],
                         font=ctk.CTkFont(size=10),
                         text_color=WC_COLORS["summary"],
                         wraplength=240, anchor="w", justify="left").pack(
                padx=10, pady=(0, 8), fill="x", anchor="w")

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
        """刷新数据统计面板（5 行：总消息/重要/联系人/提取信息/今日消息）"""
        try:
            if hasattr(self, 'engine') and self.engine:
                if self.engine.get_storage():
                    stats = self.engine.get_storage().get_stats()
                    self.stats_labels["总消息"].configure(text=str(stats.get("total_messages", 0)))
                    self.stats_labels["重要消息"].configure(text=str(stats.get("important_messages", 0)))
                    self.stats_labels["联系人数"].configure(text=str(stats.get("total_contacts", 0)))
                eng_stats = self.engine.get_stats()
                self.stats_labels["提取信息"].configure(text=str(eng_stats.get("extracted", 0)))
                self.stats_labels["今日消息"].configure(text=str(eng_stats.get("messages_detected", 0)))
        except Exception as e:
            self._on_log("warning", f"[统计] 刷新失败: {e}")

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
                                    font=("Microsoft YaHei", 12, "bold"))
        self.result_text.tag_config("stats",
                                    foreground=WC_COLORS["info"],
                                    font=("Microsoft YaHei", 10, "bold"))
        # 绿 = 我
        self.result_text.tag_config("me_label",
                                    foreground="#FFFFFF",
                                    background=WC_COLORS["accent"],
                                    font=("Microsoft YaHei", 10, "bold"))
        self.result_text.tag_config("me_text",
                                    foreground=WC_COLORS["accent_hover"],
                                    font=("Microsoft YaHei", 11))
        # 白 = 对方（用深色棕字衬托，不用白字在白底看不见）
        self.result_text.tag_config("other_label",
                                    foreground=WC_COLORS["bg_dark"],
                                    background=WC_COLORS["card"],
                                    font=("Microsoft YaHei", 10, "bold"))
        self.result_text.tag_config("other_text",
                                    foreground=WC_COLORS["text"],
                                    font=("Microsoft YaHei", 11))
        # 红 = 重要 / 低置信度
        self.result_text.tag_config("conf_low",
                                    foreground="#FFFFFF",
                                    background=WC_COLORS["danger"],
                                    font=("Microsoft YaHei", 10, "bold"))
        # 橙 = 关键词 / 辅助信息（多行）
        self.result_text.tag_config("conf_mid",
                                    foreground="#2B1E10",
                                    background=WC_COLORS["warning"],
                                    font=("Microsoft YaHei", 10, "bold"))
        # 蓝 = 高置信度 / 提取
        self.result_text.tag_config("conf_high",
                                    foreground="#FFFFFF",
                                    background=WC_COLORS["info"],
                                    font=("Microsoft YaHei", 10, "bold"))
        # 橙 = 行数标签（关键词色）
        self.result_text.tag_config("line_count",
                                    foreground="#FFFFFF",
                                    background=WC_COLORS["keyword"],
                                    font=("Microsoft YaHei", 9, "bold"))
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
        # 日志行数上限：仅保留最近 800 行，防止长期运行 UI 卡顿/内存膨胀
        try:
            line_count = int(self.log_text.index("end-1c").split(".")[0])
            if line_count > 800:
                self.log_text.delete("1.0", f"{line_count - 800}.0")
        except Exception:
            pass
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
