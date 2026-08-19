"""
发送模块 — 模拟键盘操作发送微信消息
"""
import time
import logging
import pyautogui
import pyperclip

logger = logging.getLogger(__name__)

# 安全设置
pyautogui.FAILSAFE = True  # 鼠标移到左上角时中断
pyautogui.PAUSE = 0.1      # 每个操作间隔0.1秒


def click_input_box(window):
    """
    点击微信输入框，确保焦点在输入框上。

    Args:
        window: 微信窗口对象
    """
    from window_manager import get_input_box_position

    x, y = get_input_box_position(window)
    pyautogui.click(x, y)
    time.sleep(0.15)


def clear_input_box():
    """清空输入框内容"""
    pyautogui.hotkey("ctrl", "a")  # 全选
    time.sleep(0.05)
    pyautogui.press("backspace")   # 删除
    time.sleep(0.05)


def paste_text(text):
    """
    使用剪贴板粘贴文本到输入框。

    Args:
        text: 要粘贴的文本
    """
    pyperclip.copy(text)
    time.sleep(0.05)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.1)


def send_message():
    """按回车发送消息"""
    pyautogui.press("enter")
    time.sleep(0.1)


def send_text(window, text):
    """
    完整的发送流程：点击输入框 → 清空 → 粘贴 → 发送。

    Args:
        window: 微信窗口对象
        text: 要发送的文本

    Returns:
        bool: 是否成功
    """
    try:
        # 1. 点击输入框
        click_input_box(window)
        time.sleep(0.1)

        # 2. 清空
        clear_input_box()

        # 3. 粘贴
        paste_text(text)

        # 4. 发送
        send_message()

        logger.info(f"已发送消息: {text[:50]}...")
        return True

    except Exception as e:
        logger.error(f"发送消息失败: {e}")
        return False


def send_text_type_mode(window, text):
    """
    备用方案：逐字打字模式（比粘贴慢但更可靠）。

    Args:
        window: 微信窗口对象
        text: 要发送的文本

    Returns:
        bool: 是否成功
    """
    try:
        click_input_box(window)
        time.sleep(0.1)
        clear_input_box()

        # 逐字输入
        pyautogui.write(text, interval=0.02)
        time.sleep(0.1)

        send_message()

        logger.info(f"已发送消息(打字模式): {text[:50]}...")
        return True

    except Exception as e:
        logger.error(f"发送消息失败(打字模式): {e}")
        return False


# ================================================================
# 屏幕外模式：后台消息发送（PostMessage 模拟键盘）
# ================================================================

def send_text_offscreen(window, text):
    """
    屏幕外模式发送消息：使用 PostMessage/SendMessage 模拟键盘操作。
    不依赖物理鼠标键盘，窗口可以在屏幕外。

    Args:
        window: 微信窗口对象
        text: 要发送的文本

    Returns:
        bool: 是否成功
    """
    import ctypes
    import win32gui
    import win32con
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    hwnd = getattr(window, '_hWnd', None)
    if not hwnd or not win32gui.IsWindow(hwnd):
        logger.error("[后台发送] 无效的窗口句柄")
        return False

    try:
        from window_manager import post_background_click_client, _get_hwnd
        hwnd = _get_hwnd(window)
        if not hwnd:
            logger.error("[后台发送] 无法获取窗口句柄")
            return False

        # Step 1: 点击输入框（客户区坐标）
        # get_input_box_position 返回屏幕坐标，需要转为客户区坐标
        from window_manager import get_input_box_position
        screen_x, screen_y = get_input_box_position(window)
        import win32gui
        rect = win32gui.GetWindowRect(hwnd)
        input_cx = screen_x - rect[0]
        input_cy = screen_y - rect[1]
        logger.info(f"[后台发送] 点击输入框 客户区({input_cx}, {input_cy}) 屏幕({screen_x}, {screen_y})")
        post_background_click_client(hwnd, input_cx, input_cy)
        time.sleep(0.15)

        # Step 2: 用 EM_REPLACESEL 或 WM_SETTEXT 设置输入框内容
        # 先找到输入框子窗口
        input_hwnd = _find_input_control(hwnd)
        if input_hwnd:
            # 用 WM_SETTEXT 设置输入框文本
            encoded_text = text.encode('utf-16-le')
            user32.SendMessageW(input_hwnd, win32con.WM_SETTEXT, 0, encoded_text)
            time.sleep(0.1)
        else:
            # 回退：用键盘消息模拟粘贴
            _send_key_sequence(hwnd, text)

        # Step 3: 发送（按 Enter 键）
        _send_enter_key(hwnd)
        time.sleep(0.15)

        logger.info(f"[后台发送] ✔ 已发送: {text[:50]}...")
        return True

    except Exception as e:
        logger.error(f"[后台发送] 失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


def _find_input_control(hwnd):
    """查找微信输入框控件句柄"""
    import ctypes
    import win32gui
    import win32con
    from ctypes import wintypes

    user32 = ctypes.windll.user32

    class EnumData(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("best", wintypes.HWND),
            ("text", wintypes.WCHAR * 256),
        ]

    best_hwnd = None

    def _enum_child_callback(child_hwnd, _):
        nonlocal best_hwnd
        if not win32gui.IsWindowVisible(child_hwnd):
            return True
        class_name = win32gui.GetClassName(child_hwnd)
        text = win32gui.GetWindowText(child_hwnd)
        # 微信输入框通常是 Edit 类或包含 "输入" 文本
        if class_name in ("Edit", "RichEdit20W", "RICHEDIT50W"):
            best_hwnd = child_hwnd
            return True
        # Qt 窗口可能没有标准 Edit 控件，查找包含输入提示的子窗口
        if text and ("输入" in text or "搜索" in text or "Type" in text):
            best_hwnd = child_hwnd
        return True

    # 枚举所有子窗口
    win32gui.EnumChildWindows(hwnd, _enum_child_callback, None)

    # 如果没找到，尝试用 WM_FINDWINDOW 或默认取第一个子窗口
    if not best_hwnd:
        # 回退：找尺寸较小的子窗口（输入框通常比消息列表小）
        def _find_small_child(child_hwnd, _):
            nonlocal best_hwnd
            rect = win32gui.GetWindowRect(child_hwnd)
            w = rect[2] - rect[0]
            h = rect[3] - rect[1]
            if 50 < w < 500 and 20 < h < 100:
                best_hwnd = child_hwnd
            return True
        win32gui.EnumChildWindows(hwnd, _find_small_child, None)

    return best_hwnd


def _send_key_sequence(hwnd, text):
    """通过 PostMessage 发送按键序列（回退方案）"""
    import ctypes
    import win32con

    user32 = ctypes.windll.user32
    VK_A = 0x41
    VK_V = 0x56
    VK_CONTROL = 0x11

    # Ctrl+A 全选
    user32.PostMessageW(hwnd, win32con.WM_KEYDOWN, VK_CONTROL, 0)
    user32.PostMessageW(hwnd, win32con.WM_KEYDOWN, VK_A, 0)
    user32.PostMessageW(hwnd, win32con.WM_KEYUP, VK_A, 0)
    user32.PostMessageW(hwnd, win32con.WM_KEYUP, VK_CONTROL, 0)
    time.sleep(0.05)

    # 设置剪贴板并粘贴
    import pyperclip
    pyperclip.copy(text)
    time.sleep(0.05)

    # Ctrl+V 粘贴
    user32.PostMessageW(hwnd, win32con.WM_KEYDOWN, VK_CONTROL, 0)
    user32.PostMessageW(hwnd, win32con.WM_KEYDOWN, VK_V, 0)
    user32.PostMessageW(hwnd, win32con.WM_KEYUP, VK_V, 0)
    user32.PostMessageW(hwnd, win32con.WM_KEYUP, VK_CONTROL, 0)
    time.sleep(0.1)


def _send_enter_key(hwnd):
    """通过 PostMessage 发送 Enter 键"""
    import ctypes
    import win32con

    user32 = ctypes.windll.user32
    VK_RETURN = 0x0D
    user32.PostMessageW(hwnd, win32con.WM_KEYDOWN, VK_RETURN, 0)
    user32.PostMessageW(hwnd, win32con.WM_KEYUP, VK_RETURN, 0)
    time.sleep(0.1)