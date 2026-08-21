"""
NOYA Chat 微信助手 - UI主题常量
========================
微信PC原版配色方案，供 ui_app.py 共享。
独立文件便于统一维护与复用。
"""
# ============================================================
# V3 UI 配色：严格微信 PC 原版风格
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

    # === 头像（默认色块）===
    "avatar_me": "#07C160",
    "avatar_other": "#DCDCDC",

    # === 群聊成员名 ===
    "member_name": "#07C160",

    # === 状态 ===
    "online_dot": "#07C160",
    "offline_dot": "#888888",
}