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

    def __init__(self, config):
        self.config = config or {}
        self.enabled = self.config.get("enabled", False)
        self.sidebar_width_ratio = self.config.get("sidebar_width_ratio", 0.25)
        self.sidebar_top_ratio = self.config.get("sidebar_top_ratio", 0.08)
        self.sidebar_bottom_ratio = self.config.get("sidebar_bottom_ratio", 0.95)
        self.red_min_area = self.config.get("red_min_area", 100)
        self.red_max_area = self.config.get("red_max_area", 5000)
        self._cooldown = self.config.get("cooldown_seconds", 60)
        self._processed = {}
        self._last_check_time = 0
        self._last_debug = ""

        # === 模板匹配配置（方案4） ===
        self.template_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "debug"
        )
        os.makedirs(self.template_dir, exist_ok=True)
        self.template_path = os.path.join(self.template_dir, "red_dot_template.png")
        self.template_threshold = self.config.get("template_threshold", 0.75)
        self._template_cache = None
        self._template_auto_capture = self.config.get("template_auto_capture", True)

    def get_sidebar_region(self, window):
        left, top = window.left, window.top
        w, h = window.width, window.height
        sidebar_left = left
        sidebar_top = top + int(h * self.sidebar_top_ratio)
        sidebar_width = int(w * self.sidebar_width_ratio)
        sidebar_height = int(h * (self.sidebar_bottom_ratio - self.sidebar_top_ratio))
        return (sidebar_left, sidebar_top, sidebar_width, sidebar_height)

    def capture_sidebar(self, window):
        from screenshot import capture_region
        region = self.get_sidebar_region(window)
        return capture_region(region)

    # ================================================================
    # 方案4：模板匹配检测红点
    # ================================================================

    def _load_template(self):
        if self._template_cache is not None:
            return self._template_cache
        if os.path.exists(self.template_path):
            template = cv2.imread(self.template_path)
            if template is not None and template.size > 0:
                self._template_cache = template
                logger.info(f"[模板匹配] 已加载红点模板: {self.template_path} "
                           f"尺寸={template.shape[:2]}")
                return template
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
            from PIL import Image as PILImage
            template_rgb = template[:, :, ::-1].copy()
            PILImage.fromarray(template_rgb).save(self.template_path)
            self._template_cache = template
            logger.info(f"[模板匹配] 自动截取红点模板并保存: {self.template_path} "
                        f"尺寸={template.shape[:2]} "
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
                if cx < w_img * 0.35:
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
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
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
            # 真实微信红点面积约200-2000，收紧范围排除头像/图标
            if area < 100 or area > 3000:
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
            # 未读红点在侧边栏右侧（x > 50%宽度），排除左侧头像区域
            if center_x < w * 0.35:
                rejected.append(f"x={center_x}(左侧非红点)")
                continue
            roi = image[y:y+ch, x:x+cw]
            white_ratio = 0.0
            if roi.size > 0:
                roi_hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
                white_mask = cv2.inRange(roi_hsv, np.array([0, 0, 200]), np.array([180, 60, 255]))
                white_ratio = cv2.countNonZero(white_mask) / max(roi.shape[0] * roi.shape[1], 1)
            # 必须有白色数字才算是未读徽章
            if white_ratio < 0.03:
                rejected.append(f"white={white_ratio:.2f}(无白字)")
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
        logger.info(f"[红点检测汇总] 模板={len(template_dots)}个, "
                    f"HSV={len(hsv_dots)}个, 合并去重后={len(all_dots)}个")
        return all_dots

    # ================================================================
    # OCR + 联系人名匹配
    # ================================================================

    def _is_valid_contact_name(self, text):
        if not text:
            return False
        t = text.strip()
        if len(t) < 1:
            return False
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
        for dot in red_dots:
            dot_y, dot_x = dot["center_y"], dot["center_x"]
            best_idx, best_y_diff, best_text = -1, 999, None
            for idx_original, r in ocr_sorted:
                if idx_original in used_ocr_idx:
                    continue
                text = r.get("text", "").strip()
                y_center = r.get("y_center", 0)
                y_diff = abs(y_center - dot_y)
                if y_diff <= 60 and y_diff < best_y_diff and self._is_valid_contact_name(text):
                    best_idx, best_y_diff, best_text = idx_original, y_diff, text
            if best_text is not None:
                clean_name = best_text
                m = re.match(r'^(.+?)\s+\d{1,3}\+?$', clean_name)
                if m and len(m.group(1)) >= 2:
                    clean_name = m.group(1).strip()
                matched.append({
                    "contact": clean_name, "red_dot_y": dot_y, "red_dot_x": dot_x,
                    "confidence": ocr_results[best_idx].get("confidence", 0),
                    "unread_count": "?", "method": dot.get("method", "hsv") + "+ocr",
                })
                used_ocr_idx.add(best_idx)
                logger.info(f"[红点匹配] ✔ 红点({dot_x},{dot_y}) ↔ '{clean_name}' "
                            f"y_diff={best_y_diff}px [{dot.get('method','?')}]")
            else:
                matched.append({
                    "contact": f"未读_{dot_y}", "red_dot_y": dot_y, "red_dot_x": dot_x,
                    "confidence": 0, "unread_count": "?",
                    "method": dot.get("method", "hsv") + "_only",
                })
                logger.info(f"[红点匹配] ⚠ 红点({dot_x},{dot_y}) 兜底: 未读_{dot_y}")
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
            for item in unread_raw:
                name = item["contact"]
                norm_name = re.sub(r'\s+', '', name)
                if norm_name in seen_names:
                    logger.info(f"[红点] 去重: 重复的联系人 '{name}'，跳过")
                    continue
                seen_names.add(norm_name)
                last_processed = self._processed.get(name, 0)
                elapsed = now - last_processed
                if elapsed > self._cooldown:
                    fresh_unread.append(item)
                else:
                    remaining = int(self._cooldown - elapsed)
                    cooling.append(f"{name}({remaining}s)")
                    logger.info(f"[红点] 冷却中: {name} 剩余{remaining}s，跳过")
            self._last_debug = (f"红点={len(red_dots)}个, OCR={len(ocr_results)}条, "
                                f"匹配={len(unread_raw)}个, 待处理={len(fresh_unread)}个, "
                                f"冷却中={cooling}")
            logger.info(f"[红点] 诊断: {self._last_debug}")
            if fresh_unread:
                logger.info(f"[红点] ★★★ 待处理未读联系人: {[u['contact'] for u in fresh_unread]} ★★★")
            else:
                logger.info("[红点] 未检测到待处理的未读消息，继续监控当前窗口")
            return fresh_unread
        except Exception as e:
            logger.error(f"[红点] 监控失败: {e}")
            import traceback
            logger.error(traceback.format_exc()[-300:])
            self._last_debug = f"异常: {e}"
            return []

    def mark_processed(self, contact_name):
        self._processed[contact_name] = time.time()
        logger.info(f"[红点] 已标记处理: {contact_name}, 冷却{self._cooldown}s")

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

    def should_check(self, interval=3):
        now = time.time()
        if now - self._last_check_time >= interval:
            self._last_check_time = now
            return True
        return False
