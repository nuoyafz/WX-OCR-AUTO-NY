"""
可视化调试模块 — 用 OpenCV 在截图上绘制 OCR 识别结果和状态信息
"""
import cv2
import numpy as np


def draw_debug_overlay(image, ocr_results, step, stats):
    """
    在截图上绘制调试信息：OCR文本框、发送者标签、状态栏。

    Args:
        image: numpy.ndarray (BGR格式，原始截图)
        ocr_results: OCR识别结果列表（含 sender 字段）
        step: 当前步骤名称字符串
        stats: 统计信息 dict

    Returns:
        numpy.ndarray 带标注的图像
    """
    # 复制原图，避免修改原始数据
    canvas = image.copy()
    h, w = canvas.shape[:2]

    # --- 状态栏（顶部半透明黑条） ---
    bar_height = 45
    overlay = canvas.copy()
    cv2.rectangle(overlay, (0, 0), (w, bar_height), (0, 0, 0), -1)
    canvas = cv2.addWeighted(overlay, 0.55, canvas, 0.45, 0)

    # 步骤名
    cv2.putText(
        canvas, f"[{step}]",
        (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA,
    )

    # 统计信息
    runtime_sec = (stats.get("stats_time", 0) - stats.get("start_time", 0)).total_seconds() if hasattr(stats.get("stats_time", None), "total_seconds") else 0
    stat_text = (
        f"Frames: {stats.get('frames_captured', 0)}  "
        f"OCR: {stats.get('ocr_calls', 0)}  "
        f"Msgs: {stats.get('messages_detected', 0)}  "
        f"Replies: {stats.get('replies_sent', 0)}"
    )
    cv2.putText(
        canvas, stat_text,
        (8, 41), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (200, 200, 200), 1, cv2.LINE_AA,
    )

    # --- OCR 文本框 ---
    if ocr_results:
        for r in ocr_results:
            bbox = r["bbox"]
            text = r.get("text", "")
            confidence = r.get("confidence", 0)
            sender = r.get("sender", "unknown")

            # 计算矩形四角
            pts = np.array(bbox, dtype=np.int32)
            x_min = int(min(p[0] for p in bbox))
            y_min = int(min(p[1] for p in bbox))
            x_max = int(max(p[0] for p in bbox))
            y_max = int(max(p[1] for p in bbox))

            # 颜色：绿色=我，蓝色=对方
            if sender == "me":
                color = (0, 220, 0)   # BGR green
            else:
                color = (220, 120, 0)  # BGR blue-ish

            # 画矩形框
            cv2.polylines(canvas, [pts], True, color, 2)
            cv2.rectangle(canvas, (x_min, y_min), (x_max, y_max), color, 1)

            # 标签：sender + 置信度
            label = f"{'ME' if sender == 'me' else 'OTHER'} {confidence:.0%}"
            label_y = y_min - 6 if y_min > 15 else y_max + 16

            # 标签背景
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.4, 1)
            cv2.rectangle(
                canvas,
                (x_min, label_y - th - 4),
                (x_min + tw + 6, label_y + 2),
                color, -1,
            )
            cv2.putText(
                canvas, label,
                (x_min + 3, label_y), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA,
            )

    # --- 底部消息计数 ---
    if ocr_results:
        me_count = sum(1 for r in ocr_results if r.get("sender") == "me")
        other_count = len(ocr_results) - me_count
        footer = f"ME: {me_count}  |  OTHER: {other_count}  |  Total: {len(ocr_results)} text blocks"
        cv2.putText(
            canvas, footer,
            (8, h - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (180, 180, 180), 1, cv2.LINE_AA,
        )

    return canvas


def show_debug_window(annotated_image, window_name="WeChat AI Reply - Debug"):
    """
    显示调试窗口（非阻塞，置顶）。

    Args:
        annotated_image: draw_debug_overlay 返回的带标注图像
        window_name: 窗口标题
    """
    # 首次创建时设为置顶
    if not hasattr(show_debug_window, "_initialized"):
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
        cv2.setWindowProperty(window_name, cv2.WND_PROP_TOPMOST, 1)
        cv2.resizeWindow(window_name, 600, 400)
        show_debug_window._initialized = True

    cv2.imshow(window_name, annotated_image)
    cv2.waitKey(1)


def close_debug_window(window_name="WeChat AI Reply - Debug"):
    """关闭调试窗口"""
    cv2.destroyWindow(window_name)