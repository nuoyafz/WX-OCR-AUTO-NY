"""
窗口管理模块 — 查找、定位、操作微信窗口
"""
import time
import logging

logger = logging.getLogger(__name__)
import pygetwindow as gw
import pyautogui
import win32con


def _get_normal_rect(hwnd):
    """
    获取窗口的还原态矩形（GetWindowPlacement.rcNormalPosition）。
    最小化时 GetWindowRect 返回 -32000 的小矩形，此函数仍能拿到真实尺寸。
    返回 (left, top, right, bottom) 或 None
    """
    import win32gui
    try:
        placement = win32gui.GetWindowPlacement(hwnd)
        rect = placement[4]  # rcNormalPosition
        return (rect[0], rect[1], rect[2], rect[3])
    except Exception:
        return None


def find_wechat_window():
    """
    查找微信主窗口，返回窗口对象，找不到返回 None。

    关键修复：微信4.x存在多个Qt51514QWindowIcon窗口（主窗"微信"、辅助窗"Weixin"等），
    之前取第一个命中导致抓到237x56辅助小窗。现在：
    1. 枚举收集全部候选
    2. 用 GetWindowPlacement 还原尺寸过滤（最小化时也准确）
    3. 优先标题"微信"，排除"Weixin"等辅助窗
    """
    # 优先方案: win32gui 类名匹配（最可靠）
    try:
        import win32gui

        candidates = []  # (优先级, hwnd, 标题, 还原矩形)

        def _enum_callback(hwnd, _):
            if not win32gui.IsWindow(hwnd):
                return True
            # ★ 排除工具自身进程窗口（彻底防止 OCR 把工具自己的界面当成微信）：
            #   微信是独立进程(WeChat.exe)，工具是 Python 进程，按 PID 排除最稳。
            try:
                import os as _os
                import win32process as _wp
                _tid, _pid = _wp.GetWindowThreadProcessId(hwnd)
                if _pid == _os.getpid():
                    return True
            except Exception:
                pass
            class_name = win32gui.GetClassName(hwnd)
            title = win32gui.GetWindowText(hwnd)

            is_wechat_class = (
                class_name == "WeChatMainWndForPC" or
                class_name == "Qt51514QWindowIcon" or
                (class_name == "Chrome_WidgetWin_1" and title and "微信" in title)
            )
            if not is_wechat_class and title == "微信":
                is_wechat_class = True

            if not is_wechat_class:
                return True
            # 排除自身UI窗口
            if title and ("AI" in title or "助手" in title):
                return True

            # 用还原态矩形判断真实尺寸（最小化时GetWindowRect不可靠）
            normal = _get_normal_rect(hwnd)
            if normal is None:
                return True
            nw = normal[2] - normal[0]
            nh = normal[3] - normal[1]

            # 主聊天窗尺寸过滤：还原尺寸必须够大（辅助窗"Weixin"仅约237x56）
            if nw < 500 or nh < 350:
                logger.info("[窗口查找] 跳过辅助窗: title=%r, 还原尺寸=%dx%d",
                            title, nw, nh)
                return True

            # 优先级：标题"微信"最高，含"微信"次之，其他（如Weixin）最低
            if title == "微信":
                priority = 3
            elif title and "微信" in title:
                priority = 2
            else:
                priority = 1
            candidates.append((priority, hwnd, title, normal))
            return True

        win32gui.EnumWindows(_enum_callback, None)

        if candidates:
            candidates.sort(key=lambda c: -c[0])
            _, found_hwnd, title, normal = candidates[0]
            if len(candidates) > 1:
                logger.info("[窗口查找] %d个候选，已选最优: %r (hwnd=%s)",
                            len(candidates), title, found_hwnd)

            left, top, right, bottom = normal
            # 构造兼容pygetwindow的对象（坐标用还原态，即使当前最小化）
            class _WinWrapper:
                pass
            is_minimized = win32gui.IsIconic(found_hwnd)
            is_visible = win32gui.IsWindowVisible(found_hwnd)
            w = _WinWrapper()
            w._hWnd = found_hwnd
            w.left = left
            w.top = top
            w.width = right - left
            w.height = bottom - top
            w.title = title
            w.isMinimized = is_minimized
            w.visible = is_visible
            # 把还原尺寸写入屏幕外模式原始位置记录（自愈兜底用）
            try:
                _offscreen_original[found_hwnd] = (left, top, right - left, bottom - top)
            except Exception:
                pass
            if is_minimized:
                logger.info("[窗口查找] 微信主窗已最小化 (hwnd=%s)，还原尺寸=%dx%d，标记为后台模式",
                            found_hwnd, right - left, bottom - top)
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
    微信聊天窗口标题格式： "联系人名 - 微信" 或 "联系人名" 或 "群名(5) - 微信"

    V2: 对群聊标题去掉人数括号，例如「项目讨论组(8) - 微信」→ 返回「项目讨论组」
    V2: 也可通过 include_info=True 拿到 (contact, is_group) 元组
    """
    title = getattr(window, "title", "") or ""
    # 去掉 " - 微信" 后缀
    if " - 微信" in title:
        raw = title.split(" - 微信")[0].strip()
    elif "微信" in title:
        raw = title.replace("微信", "").strip()
    else:
        raw = title.strip()

    # 去掉结尾的 (8) / （8）人数括号
    import re as _re
    cleaned = _re.sub(r'\s*[（(]\s*\d+\s*[)）]\s*$', '', raw).strip()

    # 微信4.x 所有聊天窗口的系统标题恒为"微信"，无法提供联系人/群名，
    # 返回空字符串，由上层回退到「顶部标题栏OCR / 红点匹配名」取真实会话名，
    # 避免把群聊/私聊全部误存为"微信"。
    if not cleaned or cleaned == "微信":
        return ""
    return cleaned


def analyze_chat_context(window):
    """
    V2: 综合判断当前窗口是"群聊"还是"私聊"。
    优先用标题特征（稳定廉价），回退到未知。
    返回 dict:
      {"contact": 联系人/群名, "is_group": True/False/None, "source": "title_pattern/unknown"}
    """
    contact = get_contact_name(window)
    title = getattr(window, "title", "") or ""

    import re as _re
    # 1) 标题中出现括号+数字："开心一家人(56)" → 群聊
    if _re.search(r'[（(]\s*\d+\s*[)）]', title):
        return {"contact": contact, "is_group": True, "source": "title_bracket_num"}

    # 2) 群名后缀关键词
    group_suffix = ("群", "班", "组", "队", "家族", "家人", "部门",
                    "办公室", "同事", "同学", "校友", "战友",
                    "俱乐部", "商会", "协会", "支部")
    if contact and any(suf in contact for suf in group_suffix):
        return {"contact": contact, "is_group": True, "source": "title_suffix"}

    # 3) 标题包含联系人 & 没有" - 微信"（或有微信后缀但联系人很像人名）→ 暂时标记 unknown，
    #    交给 OCR recognize_with_group_enhance 最终裁决
    return {"contact": contact, "is_group": None, "source": "unknown"}


def is_window_visible(window):
    """
    检查窗口是否存在（支持最小化监控模式）。
    最小化窗口也返回 True，由 capture_minimized_window 处理截图。
    """
    try:
        hwnd = getattr(window, '_hWnd', None)
        if hwnd:
            # 用 win32gui 检查窗口是否有效
            import win32gui
            return win32gui.IsWindow(hwnd)
        # 回退到 pygetwindow
        w = gw.getWindowsWithTitle(window.title)
        if not w:
            return False
        return True  # 窗口存在即可，最小化也允许
    except Exception:
        return False


def is_window_minimized(window):
    """检查窗口是否最小化"""
    try:
        hwnd = getattr(window, '_hWnd', None)
        if hwnd:
            import win32gui
            return win32gui.IsIconic(hwnd)
        w = gw.getWindowsWithTitle(window.title)
        if w:
            return w[0].isMinimized
        return False
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


# ================================================================
# 屏幕外保活模式（最小化 → 恢复 → 移到屏幕外）
# 窗口完全在屏幕外(-10000,0)，用户不可见。
# 截图时短暂恢复 → PrintWindow → 移回屏幕外。
# 点击用客户区坐标 + PostMessage，不需要窗口可见。
# ================================================================

OFFSCREEN_X = -10000
OFFSCREEN_Y = 0

# 记录每个窗口的原始可见位置 hwnd -> (left, top, width, height)
_offscreen_original = {}


def _get_hwnd(window):
    """获取窗口句柄（统一入口）"""
    import win32gui
    hwnd = getattr(window, '_hWnd', None)
    if not hwnd or not win32gui.IsWindow(hwnd):
        title = getattr(window, 'title', '')
        if title:
            try:
                hwnd = win32gui.FindWindow(None, title)
            except Exception:
                hwnd = None
    if hwnd and win32gui.IsWindow(hwnd):
        if getattr(window, '_hWnd', None) != hwnd:
            window._hWnd = hwnd
        return hwnd
    return None


def remember_window_rect(window):
    """记录窗口的原始可见位置（仅在正常可见状态时记录）"""
    import win32gui
    hwnd = _get_hwnd(window)
    if not hwnd:
        return
    if win32gui.IsIconic(hwnd):
        return  # 最小化时的坐标是-32000，无效
    rect = win32gui.GetWindowRect(hwnd)
    left = rect[0]
    if left < -1000:
        return  # 已在屏幕外，不记录
    _offscreen_original[hwnd] = (rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1])


def is_window_offscreen(window):
    """检查窗口是否处于屏幕外模式"""
    left = getattr(window, 'left', 0)
    if left < -1000:
        return True
    try:
        import win32gui
        hwnd = _get_hwnd(window)
        if hwnd:
            rect = win32gui.GetWindowRect(hwnd)
            return rect[0] < -1000
    except Exception:
        pass
    return False


def move_window_offscreen(window):
    """
    将窗口移到屏幕外(-10000,0)，用户不可见。
    点击通过客户区坐标+PostMessage实现，不需要窗口可见。
    截图时由 capture_minimized_window 短暂恢复后截图。
    """
    import win32gui
    import win32con
    import time

    hwnd = _get_hwnd(window)
    if not hwnd:
        logger.warning("[屏幕外] 无法获取窗口句柄")
        return False

    remember_window_rect(window)

    orig = _offscreen_original.get(hwnd)
    if orig:
        w, h = orig[2], orig[3]
    else:
        try:
            placement = win32gui.GetWindowPlacement(hwnd)
            normal = placement[4]
            w = normal[2] - normal[0]
            h = normal[3] - normal[1]
        except Exception:
            rect = win32gui.GetWindowRect(hwnd)
            w = rect[2] - rect[0]
            h = rect[3] - rect[1]

    if w < 200 or h < 200:
        logger.warning("[屏幕外] 窗口尺寸异常 %dx%d，跳过", w, h)
        return False

    style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
    is_min = (style & win32con.WS_MINIMIZE) != 0

    stop_offscreen_watcher()
    time.sleep(0.1)

    # ★ 关键：先SetWindowPos设位置到屏幕外（窗口仍最小化），
    #   再ShowWindow取消最小化——窗口直接在屏幕外变为可见，用户零感知。
    #   之前是先SW_RESTORE再移动，导致窗口在原位置闪现。

    # Step 1: 窗口仍最小化时，先设位置和尺寸到屏幕外
    #   SetWindowPos对最小化窗口会设置其"还原位置"
    logger.info("[屏幕外] 先设位置到(%d,%d) %dx%d（仍最小化）", OFFSCREEN_X, OFFSCREEN_Y, w, h)
    win32gui.SetWindowPos(
        hwnd, 0, OFFSCREEN_X, OFFSCREEN_Y, w, h,
        win32con.SWP_NOACTIVATE | win32con.SWP_NOZORDER | win32con.SWP_NOOWNERZORDER
    )
    time.sleep(0.1)

    # Step 2: 取消最小化，窗口在屏幕外位置变为可见（不激活）
    if is_min:
        logger.info("[屏幕外] 取消最小化（窗口已在屏幕外，用户看不到）")
        win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
        time.sleep(0.2)

    # Step 3: 验证位置，Qt对抗则强制重设
    rect_check = win32gui.GetWindowRect(hwnd)
    if rect_check[0] > -1000:
        logger.warning("[屏幕外] Qt重置了位置(实际x=%d)，用FRAMECHANGED强制",
                       rect_check[0])
        win32gui.SetWindowPos(
            hwnd, 0, OFFSCREEN_X, OFFSCREEN_Y, w, h,
            win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW |
            win32con.SWP_FRAMECHANGED | win32con.SWP_NOZORDER
        )
        time.sleep(0.15)

    # Step 4: 强制Qt重绘
    try:
        import ctypes
        RDW_INVALIDATE = 0x0001
        RDW_UPDATENOW = 0x0100
        RDW_ALLCHILDREN = 0x0080
        ctypes.windll.user32.RedrawWindow(
            hwnd, None, None, RDW_INVALIDATE | RDW_UPDATENOW | RDW_ALLCHILDREN)
        time.sleep(0.1)
    except Exception:
        pass

    # 同步窗口对象坐标
    rect_final = win32gui.GetWindowRect(hwnd)
    window.left = rect_final[0]
    window.top = rect_final[1]
    window.width = rect_final[2] - rect_final[0]
    window.height = rect_final[3] - rect_final[1]

    logger.info("[屏幕外] ✔ 窗口已在屏幕外(%d,%d) %dx%d，状态=SHOWNOACTIVATE，后台监控中",
                window.left, window.top, window.width, window.height)

    start_offscreen_watcher(window)
    return True


# ================================================================
# 纯后台点击（屏幕外模式专用 — 窗口全程不可见）
# 核心：窗口保持屏幕外(-10000,0) 可见但不激活(SW_SHOWNOACTIVATE)，
#       参与Windows命中测试 → 把光标SetCursorPos到屏幕外窗口内坐标，
#       mouse_event物理点击命中屏幕外窗口。
# 绝不恢复窗口、绝不激活、绝不移到屏幕内。
# ================================================================

def _ensure_offscreen_visible(hwnd, window=None):
    """确保窗口在屏幕外可见（不激活、不恢复）。
    关键技巧：最小化窗口用 SetWindowPlacement 预置"恢复位置"到屏幕外，
    再 SW_RESTORE → 窗口恢复到屏幕外，绝不弹回桌面（SW_SHOWNOACTIVATE
    对最小化窗口无效，无法取消最小化，这是之前"弹回桌面"的根因）。
    """
    import win32gui
    import win32con
    import time

    rect = win32gui.GetWindowRect(hwnd)
    w = max(rect[2] - rect[0], 100)
    h = max(rect[3] - rect[1], 100)

    # 1. 若最小化 → 预置恢复位置到屏幕外，再取消最小化（恢复到屏幕外）
    if win32gui.IsIconic(hwnd):
        try:
            wpl = list(win32gui.GetWindowPlacement(hwnd))
            wpl[3] = (OFFSCREEN_X, OFFSCREEN_Y, OFFSCREEN_X + w, OFFSCREEN_Y + h)
            wpl[4] = win32con.SW_SHOWNORMAL
            win32gui.SetWindowPlacement(hwnd, tuple(wpl))
            time.sleep(0.05)
        except Exception:
            pass
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(0.15)

    # 2. 若被Qt拖回屏幕内 → 重新放回屏幕外
    rect2 = win32gui.GetWindowRect(hwnd)
    if rect2[0] > -1000:
        win32gui.SetWindowPos(
            hwnd, 0, OFFSCREEN_X, OFFSCREEN_Y, w, h,
            win32con.SWP_NOACTIVATE | win32con.SWP_NOZORDER
        )
        time.sleep(0.1)

    # 3. 同步窗口对象坐标
    try:
        window.left = OFFSCREEN_X
        window.top = OFFSCREEN_Y
        window.width = w
        window.height = h
    except Exception:
        pass
    return True


def _activate_offscreen_window(hwnd):
    """
    在屏幕外激活窗口（绕过Windows前台锁定）。
    Qt 4.x 只处理"前台激活窗口"的物理输入 → 点击/滚动前必须先激活。
    SetForegroundWindow 不要求窗口在可见区域：窗口保持在屏幕外(-10000,0)，
    用户看不到，但 Qt 认为它已激活 → 物理点击命中后会被处理。
    """
    import ctypes
    import win32gui
    import win32api
    import win32con
    import time

    # 1. 绕过前台锁定（允许任意进程设置前台窗口）
    try:
        ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASF_ANY
    except Exception:
        pass
    try:
        # SPI_SETFOREGROUNDLOCKTIMEOUT：临时禁用前台锁定超时
        ctypes.windll.user32.SystemParametersInfoW(0x2001, 0, 0, 0)
    except Exception:
        pass

    # 2. 确保可见（窗口已在屏幕外，SW_SHOWNOACTIVATE 不抢焦点）
    win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
    time.sleep(0.05)

    # 3. ALT 键兜底（经典技巧：让当前线程获得设置前台窗口的权限）
    try:
        win32api.keybd_event(0x12, 0, 0, 0)  # VK_MENU down
        win32api.keybd_event(0x12, 0, win32con.KEYEVENTF_KEYUP, 0)  # up
        time.sleep(0.02)
    except Exception:
        pass

    # 4. 设为前台激活
    win32gui.SetForegroundWindow(hwnd)
    time.sleep(0.08)

    # 5. 验证激活结果
    fg = win32gui.GetForegroundWindow()
    return fg == hwnd


def _rehide_if_qt_moved(hwnd):
    """Qt 可能在激活后把窗口弹回屏幕内 → 立即重新藏回屏幕外"""
    import win32gui
    import win32con
    import time
    try:
        rect = win32gui.GetWindowRect(hwnd)
        if rect[0] > -1000:
            w = rect[2] - rect[0]
            h = rect[3] - rect[1]
            logger.warning("[后台点击] Qt弹回窗口(x=%d)，立即移回屏幕外", rect[0])
            win32gui.SetWindowPos(
                hwnd, 0, OFFSCREEN_X, OFFSCREEN_Y, w, h,
                win32con.SWP_NOACTIVATE | win32con.SWP_NOZORDER
            )
            time.sleep(0.1)
            return True
    except Exception:
        pass
    return False


def click_offscreen_window(window, client_x, client_y, verify_func=None):
    """
    屏幕外点击（SendMessageW 消息注入，窗口全程屏幕外不可见）：
    ★ 关键认知：光标无法到达屏幕外坐标（Windows 钳制在虚拟屏幕内），
      所以物理点击(SetCursorPos+mouse_event)对屏幕外窗口根本不可能。
    ★ 方案：窗口保持在屏幕外 → 真实激活（Qt需要前台）→
      用 SendMessageW 把鼠标消息【同步注入】到窗口客户区坐标。
      SendMessageW 是同步阻塞调用，微信的窗口过程必须处理，
      客户区坐标由 lParam 直接指定（不依赖真实光标位置）。
    """
    import ctypes
    import win32gui
    import win32api
    import win32con
    import time
    u32 = ctypes.windll.user32

    hwnd = None
    try:
        if hasattr(window, '_hWnd'):
            hwnd = window._hWnd
    except Exception:
        pass

    if not hwnd or not win32gui.IsWindow(hwnd):
        logger.warning("[后台点击] 无效窗口句柄")
        return False

    try:
        # 确保窗口在屏幕外且可见（取消最小化到屏幕外，不弹回桌面）
        _ensure_offscreen_visible(hwnd, window)
        _acquire_interaction()
        time.sleep(0.05)

        # 保存当前前台窗口（操作后恢复）
        prev_fg = None
        try:
            prev_fg = win32gui.GetForegroundWindow()
        except Exception:
            pass

        # ★ 真实激活屏幕外窗口（Qt 需要前台才处理鼠标消息）
        activated = _activate_offscreen_window(hwnd)
        if not activated:
            logger.warning("[后台点击] 屏幕外激活失败，仍尝试消息注入")
        time.sleep(0.05)

        # 客户区坐标 → lParam（高16位=y，低16位=x）
        cx = int(client_x) & 0xFFFF
        cy = int(client_y) & 0xFFFF
        lparam = (cy << 16) | cx
        MK_LBUTTON = 0x0001

        logger.info("[后台点击] SendMessageW注入: 客户区(%d,%d) 激活=%s 窗口屏幕外",
                    cx, cy, activated)

        # 注入完整点击序列（同步阻塞，微信窗口过程必须处理）
        u32.SendMessageW(hwnd, 0x0006, 1, 0)        # WM_ACTIVATE(WA_ACTIVE)
        u32.SendMessageW(hwnd, 0x0007, 0, 0)        # WM_SETFOCUS
        u32.SendMessageW(hwnd, 0x0200, 0, lparam)   # WM_MOUSEMOVE
        u32.SendMessageW(hwnd, 0x0201, MK_LBUTTON, lparam)  # WM_LBUTTONDOWN
        u32.SendMessageW(hwnd, 0x0202, 0, lparam)   # WM_LBUTTONUP
        u32.SendMessageW(hwnd, 0x0200, 0, lparam)   # WM_MOUSEMOVE

        # 等 Qt/Chromium 处理点击
        time.sleep(0.6)

        # ★ Qt 若把窗口弹回屏幕内 → 立即藏回屏幕外
        _rehide_if_qt_moved(hwnd)

        # 恢复前台窗口
        if prev_fg and win32gui.IsWindow(prev_fg):
            try:
                win32gui.SetForegroundWindow(prev_fg)
            except Exception:
                pass

        logger.info("[后台点击] ✔ 注入完成: 客户区(%d,%d)，窗口保持屏幕外",
                    cx, cy)

        # 验证（可选）
        if verify_func:
            try:
                ok = verify_func()
                if not ok:
                    logger.warning("[后台点击] 验证失败，点击可能未生效")
            except Exception as e:
                logger.warning("[后台点击] 验证异常: %s", e)

        _release_interaction(window)
        return True

    except Exception as e:
        logger.warning("[后台点击] 异常: %s", e)
        try:
            if 'prev_fg' in dir() and prev_fg and win32gui.IsWindow(prev_fg):
                win32gui.SetForegroundWindow(prev_fg)
        except Exception:
            pass
        _release_interaction(window)
        return False


def scroll_offscreen_window(window, client_x, client_y, delta=3):
    """
    屏幕外滚动：SendMessageW 注入 WM_MOUSEWHEEL。
    窗口全程屏幕外不可见，光标不移动。
    """
    import ctypes
    import win32gui
    import win32api
    import win32con
    import time
    u32 = ctypes.windll.user32

    hwnd = None
    try:
        if hasattr(window, '_hWnd'):
            hwnd = window._hWnd
    except Exception:
        pass

    if not hwnd or not win32gui.IsWindow(hwnd):
        return False

    try:
        _ensure_offscreen_visible(hwnd, window)
        _acquire_interaction()
        time.sleep(0.05)

        prev_fg = None
        try:
            prev_fg = win32gui.GetForegroundWindow()
        except Exception:
            pass

        # ★ 真实激活屏幕外窗口
        activated = _activate_offscreen_window(hwnd)
        time.sleep(0.05)

        cx = int(client_x) & 0xFFFF
        cy = int(client_y) & 0xFFFF
        lparam = (cy << 16) | cx

        logger.info("[后台滚动] SendMessageW注入: 客户区(%d,%d) delta=%d 激活=%s",
                    cx, cy, delta, activated)

        # 注入滚轮（wParam 高16位=delta，每格120）
        for _ in range(delta):
            u32.SendMessageW(hwnd, 0x020A, 120 << 16, lparam)  # WM_MOUSEWHEEL
            time.sleep(0.03)

        time.sleep(0.3)

        # ★ Qt 若弹回 → 藏回屏幕外
        _rehide_if_qt_moved(hwnd)

        if prev_fg and win32gui.IsWindow(prev_fg):
            try:
                win32gui.SetForegroundWindow(prev_fg)
            except Exception:
                pass

        _release_interaction(window)
        logger.info("[后台滚动] ✔ 注入完成: 客户区(%d,%d) delta=%d，窗口保持屏幕外",
                    cx, cy, delta)
        return True

    except Exception as e:
        logger.warning("[后台滚动] 异常: %s", e)
        try:
            if 'prev_fg' in dir() and prev_fg and win32gui.IsWindow(prev_fg):
                win32gui.SetForegroundWindow(prev_fg)
        except Exception:
            pass
        _release_interaction(window)
        return False


def post_background_click_client(hwnd_or_window, client_x, client_y):
    """兼容旧接口 — 委托给 click_offscreen_window"""
    return click_offscreen_window(hwnd_or_window, client_x, client_y)


def post_background_scroll(hwnd_or_window, client_x, client_y, delta=3):
    """兼容旧接口 — 委托给 scroll_offscreen_window"""
    return scroll_offscreen_window(hwnd_or_window, client_x, client_y, delta)


def simulate_click_window(window, client_x, client_y):
    """
    纯消息模拟点击（可见模式专用，光标完全不移动）：
    用 SendMessageW 向窗口过程注入 WM_LBUTTONDOWN/UP，不依赖真实光标位置，
    因此用户移动鼠标也不会误点。

    与 click_offscreen_window 的区别：
    - 不调用 _ensure_offscreen_visible（不把窗口搬离屏幕）
    - 不调用 SetForegroundWindow（不抢夺用户当前前台应用）
    仅用 SendMessageW 直接投递鼠标消息到 hwnd 的窗口过程（同步阻塞），
    并先发 WM_ACTIVATE/WM_SETFOCUS 让 Qt 处理合成输入。
    """
    import ctypes
    import win32gui
    import win32con
    import time
    u32 = ctypes.windll.user32

    hwnd = getattr(window, "_hWnd", None)
    if not hwnd or not win32gui.IsWindow(hwnd):
        try:
            hwnd = _get_hwnd(window)
        except Exception:
            pass
    if not hwnd or not win32gui.IsWindow(hwnd):
        logger.warning("[模拟点击] 无效窗口句柄")
        return False

    try:
        _acquire_interaction()
        time.sleep(0.05)

        cx = int(client_x) & 0xFFFF
        cy = int(client_y) & 0xFFFF
        lparam = (cy << 16) | cx
        MK_LBUTTON = 0x0001

        # 让 Qt 处理合成输入（不抢真实前台/光标）
        u32.SendMessageW(hwnd, 0x0006, 1, 0)        # WM_ACTIVATE(WA_ACTIVE)
        u32.SendMessageW(hwnd, 0x0007, 0, 0)        # WM_SETFOCUS
        u32.SendMessageW(hwnd, 0x0200, 0, lparam)   # WM_MOUSEMOVE
        u32.SendMessageW(hwnd, 0x0201, MK_LBUTTON, lparam)  # WM_LBUTTONDOWN
        u32.SendMessageW(hwnd, 0x0202, 0, lparam)   # WM_LBUTTONUP
        u32.SendMessageW(hwnd, 0x0200, 0, lparam)   # WM_MOUSEMOVE

        time.sleep(0.3)
        _release_interaction(window)
        logger.info(f"[模拟点击] ✔ SendMessageW 注入完成: 客户区({cx},{cy})，真实光标未移动")
        return True
    except Exception as e:
        logger.error(f"[模拟点击] 失败: {e}")
        try:
            _release_interaction(window)
        except Exception:
            pass
        return False


def edge_click_window(window, client_x, client_y, verify_func=None):
    """兼容旧接口 — 委托给 click_offscreen_window"""
    return click_offscreen_window(window, client_x, client_y, verify_func)


def edge_scroll_window(window, client_x, client_y, delta=3):
    """兼容旧接口 — 委托给 scroll_offscreen_window"""
    return scroll_offscreen_window(window, client_x, client_y, delta)


def post_background_click(hwnd, screen_x, screen_y):
    """保留旧接口"""
    return click_offscreen_window(hwnd, 0, 0)


# ================================================================
# 交互锁：edge_click/edge_scroll/截图恢复期间，抑制保活和watcher干预
# ================================================================
import threading

_watcher_thread = None
_watcher_stop = None
_interaction_lock = threading.Event()  # set=锁定中(交互进行)，clear=空闲


def _acquire_interaction():
    """进入交互（点击/滚动/截图恢复）：停watcher + 上锁"""
    stop_offscreen_watcher()
    _interaction_lock.set()


def _release_interaction(window=None):
    """退出交互：解锁 + 重启watcher"""
    _interaction_lock.clear()
    if window is not None:
        start_offscreen_watcher(window)


def stop_offscreen_watcher():
    """停止 watcher 线程"""
    global _watcher_stop
    if _watcher_stop is not None:
        _watcher_stop.set()


def start_offscreen_watcher(window):
    """
    启动独立 watcher 线程（屏幕外模式期间常驻）：
    每0.3秒检查一次：若微信窗口成为前台（用户点了任务栏/Alt+Tab）
    且仍在屏幕外 → 立即恢复到原可见位置。
    """
    global _watcher_thread, _watcher_stop
    import win32gui

    if _watcher_thread is not None and _watcher_thread.is_alive():
        return

    _watcher_stop = threading.Event()

    def _watch():
        hwnd = _get_hwnd(window)
        if not hwnd:
            return
        logger.info("[watcher] 屏幕外恢复监视已启动 (hwnd=%s)", hwnd)
        while not _watcher_stop.is_set():
            try:
                # 交互锁期间（点击/截图恢复中）不判断，防止误恢复
                if _interaction_lock.is_set():
                    _watcher_stop.wait(0.3)
                    continue
                if not win32gui.IsWindow(hwnd):
                    break
                fg = win32gui.GetForegroundWindow()
                if fg == hwnd:
                    rect = win32gui.GetWindowRect(hwnd)
                    if rect[0] < -1000:
                        logger.info("[watcher] ✅ 检测到用户激活微信 → 恢复窗口")
                        bring_window_back(window)
                        return
            except Exception:
                pass
            _watcher_stop.wait(0.3)
        logger.info("[watcher] 监视线程退出")

    _watcher_thread = threading.Thread(target=_watch, daemon=True, name="offscreen-watcher")
    _watcher_thread.start()


def bring_window_back(window, force_center=False):
    """从屏幕外恢复到原始可见位置，支持强制居中兜底"""
    import win32gui
    import win32con
    import ctypes
    hwnd = _get_hwnd(window)
    if not hwnd:
        return False
    
    logger.info("[屏幕外] 开始恢复窗口...")
    
    orig = _offscreen_original.get(hwnd)
    target_x, target_y, target_w, target_h = None, None, None, None

    if force_center or not orig:
        screen_w = ctypes.windll.user32.GetSystemMetrics(0)
        screen_h = ctypes.windll.user32.GetSystemMetrics(1)
        target_w, target_h = 1000, 700
        target_x = max(0, (screen_w - target_w) // 4)
        target_y = max(0, (screen_h - target_h) // 4)
        if not orig:
            logger.warning("[屏幕外] 无原始位置记录，强制居中到(%d,%d)", target_x, target_y)
    else:
        target_x, target_y, target_w, target_h = orig[0], orig[1], orig[2], orig[3]
        logger.info("[屏幕外] 恢复到原始位置(%d,%d)", target_x, target_y)

    # 使用 SetWindowPlacement 一步恢复窗口位置和状态
    class WINDOWPLACEMENT(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_uint),
            ("flags", ctypes.c_uint),
            ("showCmd", ctypes.c_uint),
            ("ptMinPosition", ctypes.c_long * 2),
            ("ptMaxPosition", ctypes.c_long * 2),
            ("rcNormalPosition", ctypes.c_long * 4),
        ]

    wp = WINDOWPLACEMENT()
    wp.length = ctypes.sizeof(WINDOWPLACEMENT)
    wp.showCmd = 1  # SW_SHOWNORMAL
    wp.rcNormalPosition[0] = target_x
    wp.rcNormalPosition[1] = target_y
    wp.rcNormalPosition[2] = target_x + target_w
    wp.rcNormalPosition[3] = target_y + target_h

    # 一步完成恢复
    ctypes.windll.user32.SetWindowPlacement(hwnd, ctypes.byref(wp))
    
    # 激活窗口到前台
    time.sleep(0.1)
    win32gui.SetForegroundWindow(hwnd)
    
    # 同步 window 对象坐标
    window.left, window.top, window.width, window.height = target_x, target_y, target_w, target_h
    
    logger.info("[屏幕外] ✔ 窗口已恢复到可见位置(%d,%d) %dx%d", target_x, target_y, target_w, target_h)
    return True


# 全局变量：记录窗口恢复检测状态
_last_fg_check = 0  # 上次检查前景窗口的时间
_window_restore_requested = False  # 窗口恢复请求标志


def keep_alive_offscreen(window):
    """
    保活循环（在主循环中每次调用）：
    1. 用户最小化微信 → 移到屏幕外后台监控
    2. 窗口被隐藏 → 恢复显示+移屏幕外
    3. 屏幕外+用户激活(任务栏/Alt+Tab) → 移回可见
    4. 屏幕外+未激活 → 保持后台
    5. 正常可见 → 记录原始位置
    
    改进：增加多种激活检测方式
    """
    import win32gui
    import win32con
    import ctypes
    global _last_fg_check, _window_restore_requested
    
    hwnd = _get_hwnd(window)
    if not hwnd:
        return

    try:
        style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
        is_min = (style & win32con.WS_MINIMIZE) != 0
        is_vis = (style & win32con.WS_VISIBLE) != 0
    except Exception:
        return

    rect = win32gui.GetWindowRect(hwnd)
    offscreen = rect[0] < -1000 or rect[2] < 0

    # ★ 交互锁检查：edge_click/edge_scroll/截图恢复期间，禁止保活逻辑干预
    #   （防止误判用户激活、防止污染原始位置记录）
    #   注意：_interaction_lock 是 threading.Event，必须用 .is_set() 判断，
    #   直接 `if _interaction_lock:` 恒为真（Event对象永远truthy），
    #   会导致保活逻辑永不执行——最小化后无法移到屏幕外，红点检测全失效。
    if _interaction_lock.is_set():
        return

    # 用户激活检测：只用 GetForegroundWindow（全局唯一可靠方式）
    #   - GetFocus/GetActiveWindow 是线程相关的，跨线程永远返回0，无效
    #   - 标题检测已删除：点击联系人后标题从"微信"变成联系人名，
    #     曾被误判为"用户激活"导致窗口被拉回桌面（严重bug）
    user_activated = False
    try:
        fg = win32gui.GetForegroundWindow()
        if fg == hwnd:
            user_activated = True
    except Exception:
        pass
    
    # 场景1：用户最小化 → 移到屏幕外后台监控
    if is_min:
        logger.info("[保活] 检测到微信最小化 → 移到屏幕外后台监控")
        move_window_offscreen(window)
        return

    # 场景2：窗口被隐藏 → 直接移到屏幕外（move_window_offscreen内部处理显示）
    if not is_vis:
        logger.info("[保活] 检测到微信被隐藏 → 移到屏幕外后台监控")
        move_window_offscreen(window)
        return

    # 场景3：屏幕外+用户激活 → 移回可见
    if offscreen and user_activated:
        logger.info("[保活] ✅ 检测到用户激活微信窗口 → 移回可见位置")
        bring_window_back(window)
        return

    # 场景4：屏幕外+未激活 → 保持后台
    if offscreen:
        return

    # 场景5：正常可见 → 记录原始位置
    if not offscreen and not is_min:
        remember_window_rect(window)
