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