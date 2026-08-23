"""
红点监控模块 — 实时监控微信左侧联系人列表的未读消息红点
================================================================
【核心策略：模板匹配优先 → HSV 6特征兜底 → OCR只找联系人名】
1. 截图左侧联系人列表区域
2. 【方案4】模板匹配：用预截的红点模板做 cv2.matchTemplate()，精准定位
3. 【兜底】HSV 6大特征检测红色圆形（颜色、面积、圆形度、位置、内部白字等）
4. OCR识别侧边栏所有文字（只用于提取联系人名）
5. 按Y坐标接近性，把红点与左侧联系人名匹配
6. 返回有未读消息的联系人列表（含Y坐标用于点击切换）

【关键改进】
- 模板匹配比HSV颜色检测准10倍，不受颜色偏差影响
- 首次运行自动从HSV检测结果中截取红点模板并保存
- 后续运行优先使用模板匹配，HSV作为fallback
- 只有真正存在红色圆形徽章的行，才算未读消息
"""
import re
import os
import time
import logging
import numpy as np
import cv2

logger = logging.getLogger(__name__)


class RedDotMonitor:
    """左侧联系人列表红点监控器"""

    # 消息预览/系统提示关键词（绝不可能是联系人名；防 OCR 把预览行当成联系人）
    _PREVIEW_KEYWORDS = (
        "草稿", "撤回", "拍了拍", "收到红包", "添加了", "邀请你",
        "以下是新消息", "以上是打招呼", "以下为打招呼",
    )
    # OCR 常把 "[18条] 南阳本地宝" 当一行 → 必须剥掉前导未读数前缀
    _COUNT_PREFIX_RE = re.compile(r'^\s*\[?\s*\d{1,3}\s*条\s*\]?\s*')

    def __init__(self, config):
        self.config = config or {}
        self.enabled = self.config.get("enabled", False)
        self.sidebar_width_ratio = self.config.get("sidebar_width_ratio", 0.25)
        # 左侧导航栏宽度占比（侧边栏内的最左图标列，需屏蔽其红点/OCR噪声，避免误识别为未读）
        self.sidebar_nav_width_ratio = self.config.get("sidebar_nav_width_ratio", 0.20)
        # 红点↔联系人名匹配的Y容差(像素)：行名到下方预览约25-35px，过大易跨行匹配到预览文字
        # （导致联系人名变成"[草稿] 哈哈，这么!"这种垃圾），过小可能漏匹配真实名字。原默认45→28。
        self._match_y_band = self.config.get("match_y_band", 28)
        self.sidebar_top_ratio = self.config.get("sidebar_top_ratio", 0.08)
        self.sidebar_bottom_ratio = self.config.get("sidebar_bottom_ratio", 0.95)
        self.red_min_area = self.config.get("red_min_area", 100)
        self.red_max_area = self.config.get("red_max_area", 5000)
        self._cooldown = self.config.get("cooldown_seconds", 60)
        # ★ 多帧时序投票：红点需连续出现 N 轮(poll)才判定为真实未读。
        #   单帧 HSV/OCR/模板抖动(残影、半渲染、PrintWindow黑图)会被直接滤掉，
        #   这是"识别到别的地方/乱套"的最大剩余来源。默认2轮即可挡掉绝大多数瞬时误触发。
        self._vote_threshold = self.config.get("vote_threshold", 2)
        self._vote = {}  # key: 红点Y取整(几何稳定) -> 连续命中轮数
        self._processed = {}
        self._last_check_time = 0
        self._last_debug = ""
        # 黑名单联系人（由 main 注入，含通配符*）：在诊断与待处理计数中排除，
        # 避免"公众号/文件传输助手"等常驻红点污染"匹配=N"造成误判焦虑
        self.blacklist = []
        # ★ 会话级嫌疑黑名单：被 main 判定为"点开后无任何新消息(全部跨轮去重)"的联系人，
        # 说明红点大概率假性命中(红头像/红色UI元素)或早已读过 → 本会话内不再点击，
        # 根治"反复误点已读会话"。重启或清空缓存时重置。
        self._suspect = set()
        # 侧边栏截图在客户区中的实际原点 + 客户区实际尺寸（PrintWindow实测，避免外框/DPI偏差）
        self._sidebar_client_origin = None  # (x, y) 截图左上角在客户区中的坐标
        self._detected_sidebar_top = None  # 侧栏自适应：检测到的列表真实上边界（位图Y，None=未启用）
        self._last_client_width = 0
        self._last_client_height = 0
        self._last_sidebar_img = None  # 最近一次侧边栏截图（用于预览）

        # === 模板匹配配置（方案4） ===
        self.template_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "debug"
        )
        os.makedirs(self.template_dir, exist_ok=True)
        self.template_path = os.path.join(self.template_dir, "red_dot_template.png")
        self.template_threshold = self.config.get("template_threshold", 0.75)
        self._template_cache = None
        self._template_auto_capture = self.config.get("template_auto_capture", True)

        # === CNN 红点分类器（二次确认，消除误触发） ===
        self._cnn_classifier = None
        cnn_cfg = self.config.get("cnn_classifier", {})
        if cnn_cfg.get("enabled", True):
            try:
                from red_dot_classifier import get_red_dot_cnn
                self._cnn_classifier = get_red_dot_cnn(cnn_cfg)
                if self._cnn_classifier.enabled:
                    logger.info("[红点监控] CNN 红点分类器已启用（二次确认）")
                else:
                    logger.info("[红点监控] CNN 分类器将自动收集样本训练")
            except Exception as e:
                logger.info("[红点监控] CNN 分类器初始化跳过: %s", e)

    def get_sidebar_region(self, window):
        left, top = window.left, window.top
        w, h = window.width, window.height
        sidebar_left = left
        sidebar_top = top + int(h * self.sidebar_top_ratio)
        sidebar_width = int(w * self.sidebar_width_ratio)
        sidebar_height = int(h * (self.sidebar_bottom_ratio - self.sidebar_top_ratio))
        return (sidebar_left, sidebar_top, sidebar_width, sidebar_height)

    def _detect_sidebar_list_top(self, full_img):
        """
        侧栏自适应：在 PrintWindow 全窗位图的侧边栏区域，检测联系人列表真实上边界。
        在侧边栏中部竖列(x=12%宽)上，从顶部向下找第一个"明显非背景"且连续3行的位置。
        仅当检测到的上边界比默认比例起点更靠上时采用（避免裁掉顶部联系人）；
        检测失败或边界更靠下时返回 None，调用方回退 sidebar_top_ratio。
        """
        try:
            import numpy as np
            if full_img is None or full_img.ndim < 2:
                return None
            h, w = full_img.shape[:2]
            x = int(w * 0.12)
            col = full_img[:, x]
            if col.ndim == 2:  # 灰度
                col = np.stack([col] * 3, axis=-1)
            col = col.astype(int)
            bg = np.median(col[int(h * 0.02):int(h * 0.05), :], axis=0)
            for y in range(int(h * 0.03), min(int(h * 0.28), h - 4)):
                d = np.abs(col[y] - bg).sum()
                if d > 120:
                    if all(np.abs(col[y + k] - bg).sum() > 80 for k in range(3)):
                        return y
        except Exception:
            pass
        return None

    def capture_sidebar(self, window):
        from screenshot import capture_region, capture_via_printwindow
        region = self.get_sidebar_region(window)
        left, top = region[0], region[1]

        # 屏幕外模式：mss截不到屏幕外，用 PrintWindow 全窗截图后裁剪侧边栏
        if left < -1000 or top < -1000:
            from window_manager import _get_hwnd
            hwnd = _get_hwnd(window)
            if hwnd:
                full = capture_via_printwindow(hwnd)
                if full is not None:
                    # 侧边栏区域在窗口内的相对坐标
                    x = left - window.left
                    y = top - window.top
                    w, h = region[2], region[3]
                    # ★ 侧栏自适应：若检测到列表上边界比默认比例起点更靠上，
                    #   则上移裁剪起点（避免顶部联系人被裁掉/坐标偏移）；失败回退比例
                    _det = self._detect_sidebar_list_top(full)
                    if _det is not None and 0 <= _det < y:
                        y = _det
                        self._detected_sidebar_top = _det
                        logger.info("[屏幕外] 侧栏自适应: 列表上边界 %dpx（默认 %dpx）→ 裁剪起点上移至 %d",
                                    _det, int(h * self.sidebar_top_ratio), y)
                    # ★ 客户区原点修正（关键）：
                    #   实测证明 PrintWindow 位图内容为"全窗口"（含标题栏），位图第i行=窗口第i行，
                    #   因此 SendMessageW 点击用的客户区行 = 位图行 - client_top_offset。
                    #   client_top_offset = 窗口顶部到客户区顶部的距离，用 ClientToScreen 精确实测；
                    #   之前用 window.height - full.shape[0] 估算标题栏，该值含底部隐形边框会多减。
                    import win32gui
                    _wr = win32gui.GetWindowRect(hwnd)
                    _co = win32gui.ClientToScreen(hwnd, (0, 0))
                    client_top_offset = max(0, _co[1] - _wr[1])
                    y_origin = y - client_top_offset
                    logger.info("[屏幕外] 全窗口截图(含标题栏)，客户区原点修正: "
                                "y %d→%d (顶部偏移=%dpx)", y, y_origin, client_top_offset)
                    logger.info("[屏幕外] 侧边栏PrintWindow裁剪: rel(%d,%d) %dx%d",
                                x, y, w, h)
                    # ★ 记录客户区实际尺寸 + 裁剪原点（点击坐标用实测值，避免外框/标题栏/DPI偏差）
                    self._sidebar_client_origin = (int(x), int(y_origin))
                    self._last_client_width = int(full.shape[1])
                    self._last_client_height = int(full.shape[0])
                    self._last_sidebar_img = full[y:y + h, x:x + w]
                    return self._last_sidebar_img
            logger.warning("[屏幕外] 侧边栏截图失败（PrintWindow无效）")
            return None

        sidebar_img = capture_region(region)
        self._last_sidebar_img = sidebar_img
        return sidebar_img

    # ================================================================
    # 方案4：模板匹配检测红点
    # ================================================================

    def _load_template(self):
        if self._template_cache is not None:
            return self._template_cache
        if os.path.exists(self.template_path):
            # 修复：cv2.imread 不支持中文路径，用 np.fromfile + cv2.imdecode
            try:
                import numpy as np
                with open(self.template_path, "rb") as f:
                    file_bytes = np.frombuffer(f.read(), dtype=np.uint8)
                template = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
                if template is not None and template.size > 0:
                    # ★ 校验模板确实是红色（曾截到绿色图标导致模板匹配永久失效）
                    t_hsv = cv2.cvtColor(template, cv2.COLOR_BGR2HSV)
                    t_h, t_s, t_v = cv2.split(t_hsv)
                    red_ratio = float(
                        (((t_h <= 10) | (t_h >= 170)) & (t_s > 100) & (t_v > 120)).sum()
                    ) / t_h.size
                    if red_ratio < 0.30:
                        logger.warning(
                            f"[模板匹配] 模板非红色(红色占比{red_ratio:.0%})，"
                            f"判定为损坏模板并删除: {self.template_path}")
                        try:
                            os.remove(self.template_path)
                        except Exception:
                            pass
                        return None
                    self._template_cache = template
                    logger.info(f"[模板匹配] 已加载红点模板: {self.template_path} "
                               f"尺寸={template.shape[:2]} 红色占比={red_ratio:.0%}")
                    return template
            except Exception as e:
                logger.warning(f"[模板匹配] 加载模板失败: {e}")
        return None

    def _save_template_from_hsv(self, image, red_dots):
        if not red_dots or not self._template_auto_capture:
            return False
        best = max(red_dots, key=lambda d: (d["area"], d["circularity"]))
        x, y, w, h = best["x"], best["y"], best["w"], best["h"]
        pad = 3
        x1 = max(0, x - pad)
        y1 = max(0, y - pad)
        x2 = min(image.shape[1], x + w + pad)
        y2 = min(image.shape[0], y + h + pad)
        template = image[y1:y2, x1:x2].copy()
        if template.size > 0 and template.shape[0] >= 10 and template.shape[1] >= 10:
            # ★ 保存前校验：截取区域必须以红色为主（防止把绿色图标等误存为模板）
            t_hsv = cv2.cvtColor(template, cv2.COLOR_BGR2HSV)
            t_h, t_s, t_v = cv2.split(t_hsv)
            red_ratio = float(
                (((t_h <= 10) | (t_h >= 170)) & (t_s > 100) & (t_v > 120)).sum()
            ) / t_h.size
            if red_ratio < 0.30:
                logger.warning(f"[模板匹配] 截取区域红色占比过低({red_ratio:.0%})，放弃保存模板")
                return False
            from PIL import Image as PILImage
            template_rgb = template[:, :, ::-1].copy()
            PILImage.fromarray(template_rgb).save(self.template_path)
            self._template_cache = template
            logger.info(f"[模板匹配] 自动截取红点模板并保存: {self.template_path} "
                        f"尺寸={template.shape[:2]} 红色占比={red_ratio:.0%} "
                        f"来源红点=({best['center_x']},{best['center_y']}) "
                        f"area={best['area']:.0f}")
            return True
        return False

    def detect_red_dots_by_template(self, image):
        template = self._load_template()
        if template is None:
            logger.info("[模板匹配] 无模板文件，跳过模板匹配（首次运行会自动截取）")
            return []
        if image is None:
            return []
        h_img, w_img = image.shape[:2]
        h_tmpl, w_tmpl = template.shape[:2]
        if h_tmpl > h_img or w_tmpl > w_img:
            logger.warning(f"[模板匹配] 模板({w_tmpl}x{h_tmpl})大于图像({w_img}x{h_img})，跳过")
            return []
        scales = [0.8, 0.9, 1.0, 1.1, 1.2]
        all_matches = []
        for scale in scales:
            scaled_w = int(w_tmpl * scale)
            scaled_h = int(h_tmpl * scale)
            if scaled_w < 5 or scaled_h < 5:
                continue
            if scaled_w > w_img or scaled_h > h_img:
                continue
            scaled_tmpl = cv2.resize(template, (scaled_w, scaled_h))
            result = cv2.matchTemplate(image, scaled_tmpl, cv2.TM_CCOEFF_NORMED)
            locs = np.where(result >= self.template_threshold)
            for pt in zip(*locs[::-1]):
                score = result[pt[1], pt[0]]
                cx = pt[0] + scaled_w // 2
                cy = pt[1] + scaled_h // 2
                # 与HSV一致：真实徽章在头像右上角(x≈15%-55%)。
                # 用 sidebar_nav_width_ratio 覆盖整个左侧导航栏列(避免无数字红点误判)
                _nav_end = int(w_img * self.sidebar_nav_width_ratio)
                if cx < _nav_end:
                    continue
                all_matches.append({
                    "x": pt[0], "y": pt[1], "w": scaled_w, "h": scaled_h,
                    "center_x": cx, "center_y": cy,
                    "area": scaled_w * scaled_h,
                    "circularity": 0.8, "white_ratio": 0,
                    "score": float(score), "method": "template",
                })
        all_matches = self._nms(all_matches, overlap_thresh=0.3)
        all_matches.sort(key=lambda d: (-d.get("score", 0), d["center_y"]))
        logger.info(f"[模板匹配] 匹配到 {len(all_matches)} 个红点 "
                    f"(阈值={self.template_threshold}, 尺度={scales})")
        for m in all_matches:
            logger.info(f"  ✔ 模板红点: center=({m['center_x']},{m['center_y']}) "
                        f"score={m['score']:.3f}")
        return all_matches

    def _nms(self, matches, overlap_thresh=0.3):
        if not matches:
            return []
        boxes = []
        for m in matches:
            boxes.append([m["x"], m["y"], m["x"] + m["w"], m["y"] + m["h"], m.get("score", 0)])
        boxes = np.array(boxes)
        x1, y1, x2, y2, scores = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3], boxes[:, 4]
        areas = (x2 - x1 + 1) * (y2 - y1 + 1)
        order = scores.argsort()[::-1]
        keep = []
        while order.size > 0:
            i = order[0]
            keep.append(i)
            xx1 = np.maximum(x1[i], x1[order[1:]])
            yy1 = np.maximum(y1[i], y1[order[1:]])
            xx2 = np.minimum(x2[i], x2[order[1:]])
            yy2 = np.minimum(y2[i], y2[order[1:]])
            w = np.maximum(0, xx2 - xx1 + 1)
            h = np.maximum(0, yy2 - yy1 + 1)
            overlap = (w * h) / areas[order[1:]]
            inds = np.where(overlap <= overlap_thresh)[0]
            order = order[inds + 1]
        return [matches[i] for i in keep]

    # ================================================================
    # HSV 6大特征检测红点（兜底）
    # ================================================================

    def detect_red_dots_by_hsv(self, image):
        if image is None:
            return []
        h, w = image.shape[:2]
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        # 微信红点精确颜色 #FA5151 → HSV约(0, 230, 250)
        lower_red1 = np.array([0, 150, 200])
        upper_red1 = np.array([8, 255, 255])
        mask1 = cv2.inRange(hsv, lower_red1, upper_red1)
        lower_red2 = np.array([172, 150, 200])
        upper_red2 = np.array([180, 255, 255])
        mask2 = cv2.inRange(hsv, lower_red2, upper_red2)
        mask = cv2.bitwise_or(mask1, mask2)
        # 3x3形态学：5x5会腐蚀小徽章（12x12徽章面积从113→100，卡在阈值边缘）
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        try:
            from PIL import Image as PILImage
            mask_rgb = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
            highlighted = cv2.addWeighted(image, 0.7, mask_rgb, 0.3, 0)
            highlighted_rgb = highlighted[:, :, ::-1].copy()
            PILImage.fromarray(highlighted_rgb).save(
                os.path.join(self.template_dir, "red_dot_mask.png"))
        except Exception:
            pass
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        red_dots = []
        rejected = []
        for cnt in contours:
            area = cv2.contourArea(cnt)
            x, y, cw, ch = cv2.boundingRect(cnt)
            center_x = x + cw // 2
            center_y = y + ch // 2
            # 真实微信红点：无数字小红点约80-150，带数字徽章200-2000
            # 面积下限从100降到50（3x3形态学后小徽章约100，无数字圆点更小）
            if area < 50 or area > 3000:
                rejected.append(f"area={area:.0f}")
                continue
            aspect_ratio = float(cw) / max(ch, 1)
            if aspect_ratio < 0.5 or aspect_ratio > 2.0:
                rejected.append(f"ar={aspect_ratio:.2f}")
                continue
            perimeter = cv2.arcLength(cnt, True)
            circularity = 4 * 3.14159 * area / (perimeter * perimeter) if perimeter > 0 else 0
            if circularity < 0.5:
                rejected.append(f"circ={circularity:.2f}")
                continue
            # 位置过滤：真实徽章在头像右上角（x≈15%-55%侧边栏宽度）。
            # 旧版在行右侧（x>70%）。下限用 sidebar_nav_width_ratio 覆盖整个左侧导航栏列，
            # 防止导航栏红点(无数字)被误判为未读消息 —— 这是"识别到别的地方"的主要根因。
            _nav_end = int(w * self.sidebar_nav_width_ratio)
            if center_x < _nav_end:
                rejected.append(f"x={center_x}(左侧导航栏,nav_end={_nav_end})")
                continue
            roi = image[y:y+ch, x:x+cw]
            white_ratio = 0.0
            _max_cc_ratio = 0.0
            if roi.size > 0:
                roi_hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
                white_mask = cv2.inRange(roi_hsv, np.array([0, 0, 200]), np.array([180, 60, 255]))
                _tot = max(roi.shape[0] * roi.shape[1], 1)
                white_ratio = cv2.countNonZero(white_mask) / _tot
                # ★ 数字ROI确认：白字须聚成单个足够大的连通域(数字笔画)，
                #   排除零散白边/白点把"红色头像/红色UI元素"误判为带数字的未读徽章。
                if white_ratio >= 0.06:
                    try:
                        _num, _labels, _stats, _cents = cv2.connectedComponentsWithStats(
                            white_mask, 8)
                        if _num > 1:
                            _max_cc_ratio = float(_stats[1:, cv2.CC_STAT_AREA].max()) / _tot
                    except Exception:
                        pass
            # 必须有白色数字才算是未读徽章（阈值过低会把红色头像/红色UI元素误判为未读，
            # 典型症状：反复误点已读会话。0.03→0.06 收紧，真实数字徽章白字占比通常>10%）。
            # 数字笔画需聚成单个足够大的连通域(_max_cc_ratio>=0.02)，零散白点不计数。
            if white_ratio < 0.06 or _max_cc_ratio < 0.02:
                rejected.append(f"white={white_ratio:.2f}/cc={_max_cc_ratio:.2f}(无数字)")
                continue
            red_dots.append({
                "x": x, "y": y, "w": cw, "h": ch,
                "center_x": center_x, "center_y": center_y,
                "area": area, "circularity": circularity,
                "white_ratio": white_ratio, "method": "hsv",
            })
        red_dots.sort(key=lambda d: (-d.get("white_ratio", 0), d["center_y"]))
        logger.info(f"[HSV检测] 图像={w}x{h}, 轮廓={len(contours)}, "
                     f"通过6特征={len(red_dots)}, 拒绝={len(rejected)}")
        if rejected:
            logger.info(f"  拒绝原因(前6): {rejected[:6]}")
        for d in red_dots:
            logger.info(f"  ✔ HSV红点: center=({d['center_x']},{d['center_y']}) "
                         f"area={d['area']:.0f} circ={d['circularity']:.2f} "
                         f"white={d['white_ratio']:.2f}")
        if red_dots and self._template_cache is None:
            self._save_template_from_hsv(image, red_dots)
        return red_dots

    def detect_red_dots(self, image):
        """统一检测入口：模板匹配优先 → HSV兜底，合并去重。"""
        template_dots = self.detect_red_dots_by_template(image)
        hsv_dots = self.detect_red_dots_by_hsv(image)
        all_dots = list(template_dots)
        used_y = set()
        for d in template_dots:
            used_y.add(d["center_y"])
        for d in hsv_dots:
            overlap = any(abs(d["center_y"] - uy) < 25 for uy in used_y)
            if not overlap:
                all_dots.append(d)
                used_y.add(d["center_y"])
        all_dots.sort(key=lambda d: (-d.get("white_ratio", 0), -d.get("score", 0), d["center_y"]))
        # 注意：不要按 x 坐标过滤"右侧红点"——微信4.0真实徽章位于头像右上角，
        # 实测横跨侧边栏宽度的 15%-55%（多列布局），x 过滤会误杀真实未读徽章。
        # 非徽章红色元素由 HSV 6特征(面积/圆形度/白字)与模板匹配分数自然排除。

        # === CNN 二次确认：过滤误检（红色头像/红包图标/红色UI元素） ===
        if self._cnn_classifier is not None:
            sidebar_img = getattr(self, "_last_sidebar_img", None)
            if sidebar_img is not None and all_dots:
                before = len(all_dots)
                all_dots = self._cnn_classifier.filter_red_dots(
                    all_dots, sidebar_img)
                after = len(all_dots)
                if before != after:
                    logger.info(
                        "[红点CNN] 二次确认: %d → %d 个 (过滤 %d 个误检)",
                        before, after, before - after)

        logger.info(f"[红点检测汇总] 模板={len(template_dots)}个, "
                    f"HSV={len(hsv_dots)}个, 合并去重后={len(all_dots)}个")
        return all_dots

    # ================================================================
    # OCR + 联系人名匹配
    # ================================================================

    def _strip_count_prefix(self, text):
        """剥掉 OCR 把 "[18条] 南阳本地宝" 整体识别成一行的前导未读数前缀"""
        if not text:
            return ""
        return self._COUNT_PREFIX_RE.sub("", text).strip()

    def _is_valid_contact_name(self, text):
        """
        判定一段 OCR 文本是否是合法的"侧边栏联系人名"。
        关键修复：
        - 移除"公众号"黑名单 —— 公众号订阅号行的名字就叫"公众号"，黑掉它会让匹配器
          拿不到真实名字、回退到下方预览文字"[18条]南阳本地宝"，那才是
          "全部会话里出现 [18条]南阳本地宝："的根因；导航栏"公众号"图标由 x<nav_end
          位置过滤已挡住，不必再用名称黑名单。
        - 长度上限/句末标点/预览关键词拒绝 → 防止"[草稿] 哈哈，这么!"这类消息预览被当成联系人名。
        """
        if not text:
            return False
        t = self._strip_count_prefix(text)
        if len(t) < 1:
            return False
        if len(t) > 16:
            return False  # 消息预览通常 >16 字；真实联系人名 2-12 字
        if t.isdigit():
            return False
        if re.match(r'^\d{1,2}[:：]\d{2}$', t):
            return False
        if re.match(r'^(昨天|今天|前天|后天|明天|星期[一二三四五六日天\d]?|周[一二三四五六日天\d]|\/\d{1,2}|-\d{1,2})$', t):
            return False
        if re.match(r'^\[?\d{1,3}\+?\s*条\]?$', t):
            return False
        if re.match(r'^\d{1,3}\+?$', t):
            return False
        # 含句末/句中标点 → 多半是消息正文/预览，不是名字
        if re.search(r'[。！!？?,，;；、…：]', t):
            return False
        # 以消息预览/系统提示开头（直接拒掉 [草稿] 哈哈，这么! 这类）
        for kw in self._PREVIEW_KEYWORDS:
            if t.startswith(kw) or f"[{kw}]" in t:
                return False
        # 侧边栏导航/系统文本（位置 x<nav_end 已过滤一次，这里保留兜底）
        # 注意：已去掉"公众号" — 理由见上方 docstring
        if t in {"搜索", "Search", "search", "全部", "设置", "通讯录", "文件",
                 "收藏", "朋友圈", "聊天", "视频号", "小程序"}:
            return False
        return True

    def ocr_sidebar(self, image):
        from ocr_engine import recognize
        results = recognize(image, scale=1.0, min_confidence=0.40,
                            merge_bubble=False, denoise=False)
        return results

    def match_red_dots_to_contacts(self, red_dots, ocr_results, image_w):
        matched = []
        used_ocr_idx = set()
        if not red_dots:
            logger.info("[红点匹配] 无红点，无需匹配")
            return []
        if not ocr_results:
            logger.warning("[红点匹配] OCR无结果，使用Y坐标兜底")
            for dot in red_dots:
                matched.append({
                    "contact": f"未读_{dot['center_y']}",
                    "red_dot_y": dot["center_y"], "red_dot_x": dot["center_x"],
                    "confidence": 0, "unread_count": "?",
                    "method": dot.get("method", "hsv") + "_only",
                })
            return matched
        ocr_sorted = sorted(enumerate(ocr_results), key=lambda ir: ir[1].get("y_center", 0))
        _nav_end = int(image_w * self.sidebar_nav_width_ratio)
        for dot in red_dots:
            dot_y, dot_x = dot["center_y"], dot["center_x"]
            # ★ 防御性屏蔽：即使 HSV/模板漏过，匹配阶段再拦一次导航栏红点
            if dot_x < _nav_end:
                logger.info(f"[红点匹配] ⚠ 红点({dot_x},{dot_y}) 在左侧导航栏(nav_end={_nav_end})，跳过")
                continue
            best_idx, best_y_diff, best_text = -1, 999, None
            for idx_original, r in ocr_sorted:
                if idx_original in used_ocr_idx:
                    continue
                # ★ 屏蔽左侧导航栏 OCR 文本（图标/无联系人名，避免"未读"被关联到导航图标）
                if r.get("x_center", 0) < _nav_end:
                    continue
                text = r.get("text", "").strip()
                y_center = r.get("y_center", 0)
                y_diff = abs(y_center - dot_y)
                if y_diff <= self._match_y_band and y_diff < best_y_diff and self._is_valid_contact_name(text):
                    best_idx, best_y_diff, best_text = idx_original, y_diff, text
            if best_text is not None:
                clean_name = best_text
                # 剥前导 [N条] 未读数前缀（OCR 经常把 "[18条] 南阳本地宝" 当一行）
                clean_name = self._strip_count_prefix(clean_name)
                # 去尾部 " 数字+" 残留
                m = re.match(r'^(.+?)\s+\d{1,3}\+?$', clean_name)
                if m and len(m.group(1)) >= 2:
                    clean_name = m.group(1).strip()
                # 清理后若不再合法（空 / 仍是预览文字 / 过长等）→ 跳过，避免存进垃圾联系人名
                if not self._is_valid_contact_name(clean_name):
                    logger.info(f"[红点匹配] ⚠ 红点({dot_x},{dot_y}) 清理后 '{clean_name}' 仍无效，跳过")
                    continue
                name_y = ocr_results[best_idx].get("y_center", dot_y)
                matched.append({
                    "contact": clean_name,
                    "red_dot_y": dot_y,         # 红点像素 Y（保留用于日志/调试）
                    "red_dot_x": dot_x,
                    "name_y": name_y,           # 匹配到的名字中心 Y（用于点击定位，比红点Y更精准）
                    "confidence": ocr_results[best_idx].get("confidence", 0),
                    "unread_count": "?", "method": dot.get("method", "hsv") + "+ocr",
                })
                used_ocr_idx.add(best_idx)
                logger.info(f"[红点匹配] ✔ 红点({dot_x},{dot_y}) ↔ '{clean_name}' "
                            f"y_diff={best_y_diff}px [{dot.get('method','?')}]")
            else:
                # ★ 不再生成 "未读_y" 幻像联系人（避免点击到完全无关的聊天行 → "乱套"根因）
                #    真实场景下：经过 nav-bar 屏蔽 + 行带收紧后，绝大部分红点都能匹配到名称；
                #    极少数 OCR 真的读不到名字的，宁可让该未读暂不处理，也不要误点别处。
                logger.info(f"[红点匹配] ⚠ 红点({dot_x},{dot_y}) 在{self._match_y_band}px带内未匹配到有效联系人名，跳过(避免误点击)")
                continue
        return matched

    def get_unread_contacts(self, window, debug=False):
        if not self.enabled:
            return []
        try:
            sidebar_img = self.capture_sidebar(window)
            if sidebar_img is None:
                logger.warning("[红点] 侧边栏截图失败")
                return []
            h, w = sidebar_img.shape[:2]
            logger.info(f"[红点] === 开始扫描左侧栏未读消息 === 图像尺寸: {w}x{h}")
            try:
                from PIL import Image as PILImage
                sidebar_rgb = sidebar_img[:, :, ::-1].copy()
                PILImage.fromarray(sidebar_rgb).save(
                    os.path.join(self.template_dir, "sidebar_capture.png"))
            except Exception:
                pass
            red_dots = self.detect_red_dots(sidebar_img)
            logger.info(f"[红点] 步骤1 检测到 {len(red_dots)} 个红色未读标记")
            # 调试：无红点时保存截图供分析
            if not red_dots:
                try:
                    from PIL import Image as PILImage
                    debug_path = os.path.join(self.template_dir, "debug_no_reddot.png")
                    rgb = cv2.cvtColor(sidebar_img, cv2.COLOR_BGR2RGB)
                    PILImage.fromarray(rgb).save(debug_path)
                    logger.info(f"[红点] 调试截图已保存: {debug_path} (大小={sidebar_img.shape})")
                except Exception as e:
                    logger.warning(f"[红点] 调试截图保存失败: {e}")
                self._last_debug = "无红点（模板+HSV均未检测到）"
                logger.info("[红点] 无未读标记，继续监控当前窗口")
                return []
            ocr_results = self.ocr_sidebar(sidebar_img)
            logger.info(f"[红点] 步骤2 OCR识别到 {len(ocr_results)} 条文字")
            ocr_debug = []
            for r in ocr_results:
                ocr_debug.append(f"'{r.get('text','')[:20]}'@y={r.get('y_center',0)}x={r.get('x_center',0)}")
            logger.info(f"[红点] OCR详情(前10): {ocr_debug[:10]}")
            unread_raw = self.match_red_dots_to_contacts(red_dots, ocr_results, w)
            logger.info(f"[红点] 步骤3 匹配结果: {len(unread_raw)} 个未读联系人 → "
                        f"{[u['contact'] for u in unread_raw]}")
            now = time.time()
            seen_names = set()
            fresh_unread = []
            cooling = []
            # === 多帧时序投票：仅当某红点在连续 N 轮都被检测到，才放行为"待处理" ===
            # 用红点Y(几何稳定,不受OCR名字抖动影响)做投票键；窗口缩放会自然重置投票。
            _this_round = {}
            for item in unread_raw:
                _key = int(round(item["red_dot_y"] / 5.0) * 5)
                self._vote[_key] = self._vote.get(_key, 0) + 1
                _this_round[_key] = item
            for _k in list(self._vote.keys()):
                if _k not in _this_round:
                    self._vote[_k] = max(0, self._vote[_k] - 1)
                    if self._vote[_k] <= 0:
                        del self._vote[_k]
            _voted_raw = [
                item for item in unread_raw
                if self._vote.get(int(round(item["red_dot_y"] / 5.0) * 5), 0) >= self._vote_threshold
            ]
            _dropped_by_vote = len(unread_raw) - len(_voted_raw)
            if _dropped_by_vote:
                logger.info(f"[红点] 时序投票拦截 {_dropped_by_vote} 个单帧闪现红点(需连续"
                            f"{self._vote_threshold}轮,当前票数={ {k: self._vote[k] for k in _this_round} })")
            for item in _voted_raw:
                name = item["contact"]
                # 黑名单联系人（含通配符*匹配）：直接跳过，不计入待处理
                _bl_skip = False
                for _bl in self.blacklist:
                    if "*" in _bl:
                        if _bl.replace("*", "") in name:
                            _bl_skip = True
                            break
                    elif _bl == name or _bl in name:
                        _bl_skip = True
                        break
                if _bl_skip:
                    logger.info(f"[红点] 黑名单联系人跳过(诊断): {name}")
                    continue
                # 会话级嫌疑：点开后无任何新消息(全部跨轮去重)的联系人，本会话不再点击
                if self.is_suspect(name):
                    logger.warning(f"[红点] 嫌疑联系人跳过(曾误点已读): {name}")
                    continue
                norm_name = re.sub(r'\s+', '', name)
                if norm_name in seen_names:
                    logger.info(f"[红点] 去重: 重复的联系人 '{name}'，跳过")
                    continue
                seen_names.add(norm_name)
                # 同时检查规范化名称和原始名称（确保mark_processed存储的键能被找到）
                last_processed = max(
                    self._processed.get(norm_name, 0),
                    self._processed.get(name, 0)
                )
                elapsed = now - last_processed
                if elapsed > self._cooldown:
                    fresh_unread.append(item)
                    logger.info(f"[红点] ✅ 通过冷却检查: {name} (已冷却{elapsed:.0f}s)")
                else:
                    remaining = int(self._cooldown - elapsed)
                    cooling.append(f"{name}({remaining}s)")
                    logger.info(f"[红点] ⏸ 冷却中: {name} 剩余{remaining}s，跳过")
            _blk_in_raw = sum(
                1 for it in unread_raw
                if any((("*" in b and b.replace("*", "") in it["contact"]) or
                        b == it["contact"] or b in it["contact"])
                       for b in self.blacklist)
            )
            _valid_match = len(unread_raw) - _blk_in_raw
            self._last_debug = (f"红点={len(red_dots)}个, OCR={len(ocr_results)}条, "
                                f"匹配={_valid_match}个(黑名单{_blk_in_raw}), "
                                f"待处理={len(fresh_unread)}个, "
                                f"冷却中={cooling}")
            logger.info(f"[红点] 诊断: {self._last_debug}")
            if fresh_unread:
                logger.info(f"[红点] ★★★ 待处理未读联系人: {[u['contact'] for u in fresh_unread]} ★★★")
            else:
                if cooling:
                    logger.info(f"[红点] 未读均处于冷却中({len(cooling)}个)，{cooling[0]} 后到期将重试点击")
                else:
                    logger.info("[红点] 未检测到未读消息，继续监控当前窗口")
            return fresh_unread
        except Exception as e:
            logger.error(f"[红点] 监控失败: {e}")
            import traceback
            logger.error(traceback.format_exc()[-300:])
            self._last_debug = f"异常: {e}"
            return []

    def mark_processed(self, contact_name):
        # 规范化联系人名（与检查冷却时一致，避免键不匹配）
        import re
        norm_name = re.sub(r'\s+', '', contact_name)
        self._processed[norm_name] = time.time()
        self._processed[contact_name] = time.time()  # 同时存储原始名，兼容
        logger.info(f"[红点] 已标记处理: {contact_name} (规范化={norm_name}), 冷却{self._cooldown}s")

    # ================================================================
    # 会话级嫌疑黑名单（根治"反复误点已读会话"）
    # ================================================================
    def mark_suspect(self, contact_name):
        """标记某联系人为嫌疑(点开后无任何新消息)。本会话内不再点击。"""
        import re
        norm_name = re.sub(r'\s+', '', contact_name)
        self._suspect.add(norm_name)
        self._suspect.add(contact_name)
        logger.warning(f"[红点] 标记嫌疑(无新消息疑似误点): {contact_name} → 本会话内不再点击")

    def is_suspect(self, contact_name):
        import re
        norm_name = re.sub(r'\s+', '', contact_name)
        return norm_name in self._suspect or contact_name in self._suspect

    def clear_suspect(self, contacts=None):
        """清空嫌疑黑名单。contacts=None 清空全部；否则只清指定（规范化+原样）。"""
        import re
        if contacts is None:
            self._suspect = set()
            return
        for c in contacts:
            self._suspect.discard(c)
            self._suspect.discard(re.sub(r'\s+', '', c))

    def get_click_position(self, window, red_dot_y_in_sidebar, red_dot_x_in_sidebar=None):
        """根据红点在侧边栏截图中的位置，计算屏幕点击坐标"""
        left, top = window.left, window.top
        w, h = window.width, window.height
        sidebar_top = top + int(h * self.sidebar_top_ratio)
        sidebar_left = left
        # X坐标：点击联系人名称区域（侧边栏65%处，避开左侧头像）
        if red_dot_x_in_sidebar is not None:
            # 用红点X但向左偏移（红点在右侧，名称在中部）
            click_x = sidebar_left + max(int(red_dot_x_in_sidebar * 0.5), int(w * 0.05))
        else:
            click_x = sidebar_left + int(w * self.sidebar_width_ratio * 0.65)
        click_y = sidebar_top + red_dot_y_in_sidebar
        logger.info(f"[红点] 点击位置: 屏幕({click_x},{click_y})")
        return (click_x, click_y)

    def get_click_client_position(self, window, red_dot_y_in_sidebar, red_dot_x_in_sidebar=None):
        """
        计算客户区相对坐标（用于SendMessageW后台点击）
        与get_click_position不同，此方法返回的是相对于窗口客户区左上角的坐标
        这样即使窗口在屏幕外也能正确定位
        ★ 优先使用 PrintWindow 实测的裁剪原点/客户区尺寸（避免标题栏/DPI偏差点错联系人）
        """
        # === 实测优先：用 capture_sidebar 记录的裁剪原点 + 客户区尺寸 ===
        if self._sidebar_client_origin is not None and self._last_client_width > 0:
            origin_x, origin_y = self._sidebar_client_origin
            full_w = self._last_client_width
            # X坐标：点击联系人名称区域（侧边栏65%处，避开左侧头像）
            if red_dot_x_in_sidebar is not None:
                # 用红点X但向左偏移（红点在右侧，名称在中部）
                rel_x_in_sidebar = max(int(red_dot_x_in_sidebar * 0.5), int(full_w * 0.05))
                click_x = origin_x + int(full_w * self.sidebar_width_ratio * 0.35) + rel_x_in_sidebar
            else:
                click_x = origin_x + int(full_w * self.sidebar_width_ratio * 0.65)
            # Y坐标：裁剪原点 + 红点在裁剪图中的Y
            click_y = origin_y + int(red_dot_y_in_sidebar)
            logger.info(f"[红点] 客户区点击位置(实测): rel({click_x},{click_y}) "
                        f"原点={self._sidebar_client_origin}")
            return (click_x, click_y)

        # === 兜底：按窗口尺寸比例估算 ===
        w, h = window.width, window.height
        if red_dot_x_in_sidebar is not None:
            rel_x_in_sidebar = max(int(red_dot_x_in_sidebar * 0.5), int(w * 0.05))
            click_x = int(w * self.sidebar_width_ratio * 0.35) + rel_x_in_sidebar
        else:
            click_x = int(w * self.sidebar_width_ratio * 0.65)
        click_y = int(h * self.sidebar_top_ratio) + int(red_dot_y_in_sidebar)
        logger.info(f"[红点] 客户区点击位置(估算): rel({click_x},{click_y})")
        return (click_x, click_y)

    def should_check(self, interval=3):
        now = time.time()
        if now - self._last_check_time >= interval:
            self._last_check_time = now
            return True
        return False