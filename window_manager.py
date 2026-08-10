"""
窗口管理模块 — 查找、定位、操作微信窗口
"""
import time
import logging

logger = logging.getLogger(__name__)
import pygetwindow as gw
import pyautogui
import win32con


def find_wechat_window():
    """
    查找微信主窗口，返回窗口对象，找不到返回 None。

    方案5优化：使用win32gui.EnumWindows + 类名匹配，比纯标题匹配更可靠。
    微信PC版窗口类名: WeChatMainWndForPC
    """
    # 优先方案: win32gui 类名匹配（最可靠）
    try:
        import win32gui

        found_hwnd = None

        def _enum_callback(hwnd, _):
            nonlocal found_hwnd
            if not win32gui.IsWindowVisible(hwnd):
                return True
            class_name = win32gui.GetClassName(hwnd)
            # 微信PC版主窗口类名
            if class_name == "WeChatMainWndForPC":
                title = win32gui.GetWindowText(hwnd)
                # 排除自身UI窗口
                if title and ("AI" not in title and "助手" not in title):
                    found_hwnd = hwnd
                    return False  # 找到了，停止枚举
            return True

        win32gui.EnumWindows(_enum_callback, None)

        if found_hwnd:
            rect = win32gui.GetWindowRect(found_hwnd)
            left, top, right, bottom = rect
            title = win32gui.GetWindowText(found_hwnd)
            # 构造兼容pygetwindow的对象
            class _WinWrapper:
                pass
            w = _WinWrapper()
            w._hWnd = found_hwnd
            w.left = left
            w.top = top
            w.width = right - left
            w.height = bottom - top
            w.title = title
            w.isMinimized = False
            w.visible = True
            # 提供 activate 方法
            def _activate(self):
                win32gui.ShowWindow(self._hWnd, win32con.SW_RESTORE)
                win32gui.SetForegroundWindow(self._hWnd)
            w.activate = lambda: _activate(w)
            return w
    except Exception as e:
        logger.debug(f"win32gui查找失败，回退到pygetwindow: {e}")

    # 回退方案: pygetwindow 标题匹配
    windows = gw.getWindowsWithTitle("微信")
    for w in windows:
        if w.width > 400 and w.height > 300:
            title = w.title or ""
            if "AI" in title or "助手" in title:
                continue
            return w
    return None


def get_chat_region(window):
    """
    获取聊天消息区域（排除左侧联系人栏、顶部标题栏、底部输入框）。
    微信4.x布局：左侧栏约30%，顶部标题栏约8%，底部输入框约15%。
    """
    left, top = window.left, window.top
    w, h = window.width, window.height
    chat_left = left + int(w * 0.30)     # 左侧裁掉30%（跳过整个联系人栏）
    chat_top = top + int(h * 0.08)       # 顶部裁8%（跳过标题栏和搜索栏）
    chat_width = int(w * 0.68)           # 宽度68%（30%~98%）
    chat_height = int(h * 0.77)          # 高度77%（8%~85%，跳过底部输入框）
    return (chat_left, chat_top, chat_width, chat_height)


def get_input_box_position(window):
    """估算微信输入框的点击位置"""
    left, top = window.left, window.top
    w, h = window.width, window.height

    # 输入框在聊天区域下方，大约窗口高度 65%-85% 之间
    click_x = left + int(w * 0.50)
    click_y = top + int(h * 0.80)

    return (click_x, click_y)


def focus_window(window):
    """激活并置顶微信窗口"""
    try:
        window.activate()
        time.sleep(0.2)
        return True
    except Exception:
        # 备用方案：点击窗口标题栏
        try:
            pyautogui.click(window.left + window.width // 2, window.top + 15)
            time.sleep(0.2)
            return True
        except Exception:
            return False


def get_contact_name(window):
    """
    从微信窗口标题中提取联系人名称。
    微信聊天窗口标题格式： "联系人名 - 微信" 或 "联系人名"
    """
    title = window.title
    # 去掉 " - 微信" 后缀
    if " - 微信" in title:
        return title.split(" - 微信")[0].strip()
    elif "微信" in title:
        return title.replace("微信", "").strip()
    return title.strip()


def is_window_visible(window):
    """检查窗口是否可见（未被完全遮挡）"""
    try:
        # 重新获取窗口信息
        w = gw.getWindowsWithTitle(window.title)
        if not w:
            return False
        w = w[0]
        # 检查窗口是否最小化
        return not w.isMinimized and w.visible
    except Exception:
        return False


def setup_window_guide():
    """
    引导用户设置微信窗口位置。
    返回找到的微信窗口对象。
    """
    print("\n" + "=" * 60)
    print("  微信 AI 自动回复 — 窗口设置")
    print("=" * 60)
    print()
    print("  请确保：")
    print("  1. 微信PC版已打开并登录")
    print("  2. 微信窗口没有被最小化")
    print("  3. 已经打开你想监听的聊天窗口")
    print()

    window = None
    retries = 0
    while window is None and retries < 5:
        window = find_wechat_window()
        if window is None:
            retries += 1
            print(f"  [!] 未找到微信窗口，请打开微信后按 Enter 重试... ({retries}/5)")
            try:
                input()
            except (EOFError, OSError):
                # Non-interactive mode — just retry once
                print("  (非交互模式，自动重试...)")
                import time
                time.sleep(1)
        else:
            break

    if window is None:
        print("  [X] 未能找到微信窗口，程序退出")
        return None

    print(f"  [OK] 找到微信窗口: {window.title}")
    print(f"     位置: left={window.left}, top={window.top}")
    print(f"     大小: {window.width}x{window.height}")
    print()
    print("  [TIP] 提示：请保持微信窗口在屏幕上的位置不变")
    print("     如果移动了窗口，需要重启程序")
    print()
    print("=" * 60)

    return window
