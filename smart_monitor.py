"""
增量检测引擎 — 4层优化替代全量OCR
==================================
绝技1: 帧间差异检测 — 画面没变化就跳过OCR
绝技2: 变化区域裁剪 — 只对变化部分做OCR
绝技3: 感知哈希去重 — 比MD5更鲁棒的消息去重
绝技4: 底部新消息检测 — 新消息必在底部，只监控底部
"""
import cv2
import numpy as np
import logging
import time
from collections import deque

logger = logging.getLogger(__name__)


class SmartMonitor:
    """增量检测引擎"""

    def __init__(self, config=None):
        self.config = config or {}

        # 绝技1参数：帧间差异
        self.diff_threshold = self.config.get("diff_threshold", 15)  # 像素差异阈值
        self.diff_area_ratio = self.config.get("diff_area_ratio", 0.005)  # 变化面积占比阈值(0.5%)
        self._last_frame = None

        # 绝技2参数：变化区域裁剪
        self.crop_padding = self.config.get("crop_padding", 10)  # 裁剪边距
        self.min_crop_area = self.config.get("min_crop_area", 500)  # 最小裁剪面积
        self._ocr_cache = {}  # 区域哈希 -> OCR结果缓存

        # 绝技3参数：感知哈希
        self.phash_size = self.config.get("phash_size", 8)  # pHash尺寸
        self.hamming_threshold = self.config.get("hamming_threshold", 5)  # 汉明距离阈值
        self._seen_phashes = []  # [(phash, text), ...]
        self._max_phash_cache = 200  # 最多缓存200条

        # 绝技4参数：底部检测
        self.bottom_ratio = self.config.get("bottom_ratio", 0.25)  # 只看底部25%
        self._last_bottom_hash = None  # 上一帧底部的哈希

        # 统计
        self.stats = {
            "frames_checked": 0,
            "frames_skipped_no_change": 0,
            "frames_skipped_bottom_same": 0,
            "ocr_calls_full": 0,
            "ocr_calls_incremental": 0,
            "ocr_calls_saved": 0,
            "messages_deduplicated": 0,
        }

    def should_run_ocr(self, current_frame):
        """
        绝技1+4：判断是否需要做OCR。
        先检查底部是否变化（绝技4），再检查整帧差异（绝技1）。

        Returns:
            (need_ocr, diff_regions)
            need_ocr: True表示需要做OCR
            diff_regions: 变化区域列表 [(x,y,w,h), ...]，空列表表示全图OCR
        """
        self.stats["frames_checked"] += 1

        if current_frame is None:
            return False, []

        h, w = current_frame.shape[:2]

        # === 绝技4：底部检测 ===
        bottom_h = int(h * self.bottom_ratio)
        bottom_region = current_frame[h - bottom_h:, :]

        # 计算底部区域的感知哈希
        bottom_hash = self._compute_phash(bottom_region)
        if self._last_bottom_hash is not None:
            hamming = self._hamming_distance(bottom_hash, self._last_bottom_hash)
            if hamming == 0:
                # 底部完全没变化，连差异检测都不用做
                self.stats["frames_skipped_bottom_same"] += 1
                self._last_bottom_hash = bottom_hash
                return False, []
        self._last_bottom_hash = bottom_hash

        # === 绝技1：帧间差异检测 ===
        if self._last_frame is None:
            # 第一帧，全图OCR
            self._last_frame = current_frame.copy()
            return True, []

        # 计算帧间差异
        gray_curr = cv2.cvtColor(current_frame, cv2.COLOR_BGR2GRAY)
        gray_last = cv2.cvtColor(self._last_frame, cv2.COLOR_BGR2GRAY)
        diff = cv2.absdiff(gray_curr, gray_last)
        _, diff_mask = cv2.threshold(diff, self.diff_threshold, 255, cv2.THRESH_BINARY)

        # 形态学操作去噪
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        diff_mask = cv2.morphologyEx(diff_mask, cv2.MORPH_CLOSE, kernel)
        diff_mask = cv2.morphologyEx(diff_mask, cv2.MORPH_OPEN, kernel)

        # 计算变化面积占比
        changed_pixels = cv2.countNonZero(diff_mask)
        total_pixels = h * w
        change_ratio = changed_pixels / total_pixels

        if change_ratio < self.diff_area_ratio:
            # 变化太小，跳过
            self.stats["frames_skipped_no_change"] += 1
            self._last_frame = current_frame.copy()
            return False, []

        # === 绝技2：提取变化区域 ===
        contours, _ = cv2.findContours(diff_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        regions = []
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < self.min_crop_area:
                continue
            x, y, rw, rh = cv2.boundingRect(cnt)

            # 扩展裁剪区域（加上下文边距）
            px = self.crop_padding
            x1 = max(0, x - px)
            y1 = max(0, y - px)
            x2 = min(w, x + rw + px)
            y2 = min(h, y + rh + px)

            # 合并重叠区域
            merged = False
            for i, (mx, my, mw, mh) in enumerate(regions):
                if self._rects_overlap(x1, y1, x2, y2, mx, my, mx + mw, my + mh):
                    # 合并
                    nx1 = min(x1, mx)
                    ny1 = min(y1, my)
                    nx2 = max(x2, mx + mw)
                    ny2 = max(y2, my + mh)
                    regions[i] = (nx1, ny1, nx2 - nx1, ny2 - ny1)
                    merged = True
                    break

            if not merged:
                regions.append((x1, y1, x2 - x1, y2 - y1))

        self._last_frame = current_frame.copy()

        if not regions:
            # 有差异但没找到有效区域，全图OCR
            return True, []

        return True, regions

    def incremental_ocr(self, frame, regions, ocr_func):
        """
        绝技2：只对变化区域做OCR。

        Args:
            frame: 完整截图
            regions: 变化区域列表 [(x,y,w,h), ...]
            ocr_func: OCR函数，签名 ocr_func(image) -> list of {text, confidence, ...}

        Returns:
            list of OCR结果，带坐标偏移修正
        """
        if not regions:
            # 全图OCR
            self.stats["ocr_calls_full"] += 1
            return ocr_func(frame)

        h, w = frame.shape[:2]
        all_results = []
        seen_texts = set()

        for (rx, ry, rw, rh) in regions:
            # 裁剪变化区域
            crop = frame[ry:ry + rh, rx:rx + rw]
            if crop.size == 0:
                continue

            # 检查缓存
            crop_hash = self._compute_phash(crop)
            crop_key = crop_hash.tobytes() if crop_hash is not None else None
            cached = self._ocr_cache.get(crop_key) if crop_key is not None else None
            if cached is not None:
                self.stats["ocr_calls_saved"] += 1
                # 修正坐标偏移
                for r in cached:
                    r = r.copy()
                    r["y_center"] = r.get("y_center", 0) + ry
                    r["x_center"] = r.get("x_center", 0) + rx
                    if "bbox" in r and r["bbox"]:
                        r["bbox"] = [[p[0] + rx, p[1] + ry] for p in r["bbox"]]
                    all_results.append(r)
                continue

            # 对裁剪区域做OCR
            self.stats["ocr_calls_incremental"] += 1
            crop_results = ocr_func(crop)

            if crop_results:
                # 缓存结果
                self._ocr_cache[crop_key] = crop_results
                if len(self._ocr_cache) > 50:
                    # 清理一半缓存
                    keys = list(self._ocr_cache.keys())
                    for k in keys[:25]:
                        del self._ocr_cache[k]

                # 修正坐标偏移
                for r in crop_results:
                    r["y_center"] = r.get("y_center", 0) + ry
                    r["x_center"] = r.get("x_center", 0) + rx
                    if "bbox" in r and r["bbox"]:
                        r["bbox"] = [[p[0] + rx, p[1] + ry] for p in r["bbox"]]

                    # 去重
                    text = str(r.get("text", "")).strip()
                    if text and text not in seen_texts:
                        seen_texts.add(text)
                        all_results.append(r)

        return all_results

    def deduplicate(self, text):
        """
        绝技3：感知哈希去重。
        判断这条消息是否之前见过（用pHash比MD5更鲁棒）。

        Returns:
            True = 重复消息（应跳过）
            False = 新消息
        """
        if not text or len(text) < 1:
            return True

        phash = self._text_phash(text)

        for seen_hash, seen_text in self._seen_phashes:
            hamming = self._hamming_distance(phash, seen_hash)
            if hamming <= self.hamming_threshold:
                # 相似消息
                self.stats["messages_deduplicated"] += 1
                logger.debug(f"pHash去重: '{text[:20]}' ~ '{seen_text[:20]}' (hamming={hamming})")
                return True

        # 新消息，加入缓存
        self._seen_phashes.append((phash, text))
        if len(self._seen_phashes) > self._max_phash_cache:
            self._seen_phashes.pop(0)

        return False

    def _compute_phash(self, image):
        """计算图像的感知哈希"""
        try:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
            resized = cv2.resize(gray, (self.phash_size, self.phash_size))
            avg = resized.mean()
            phash = (resized > avg).flatten()
            return phash
        except Exception:
            return None

    def _text_phash(self, text):
        """计算文本的简单哈希（基于字符频率）"""
        # 简单方案：用字符的unicode值分布做哈希
        hash_bits = []
        if not text:
            return np.zeros(self.phash_size * self.phash_size, dtype=bool)

        # 取字符特征
        chars = list(text.replace(" ", "").replace("\n", ""))
        if not chars:
            return np.zeros(self.phash_size * self.phash_size, dtype=bool)

        # 用字符的unicode值生成特征向量
        features = np.zeros(self.phash_size * self.phash_size)
        for i, c in enumerate(chars):
            val = ord(c)
            features[val % len(features)] += 1
            features[(val // 7) % len(features)] += 0.5

        avg = features.mean() if features.mean() > 0 else 0
        phash = features > avg
        return phash

    def _hamming_distance(self, hash1, hash2):
        """计算两个哈希的汉明距离"""
        if hash1 is None or hash2 is None:
            return 999
        if len(hash1) != len(hash2):
            return 999
        return np.count_nonzero(hash1 != hash2)

    def _rects_overlap(self, x1, y1, x2, y2, mx1, my1, mx2, my2):
        """判断两个矩形是否重叠"""
        return not (x2 < mx1 or mx2 < x1 or y2 < my1 or my2 < y1)

    def reset(self):
        """重置状态（切换联系人时调用）"""
        self._last_frame = None
        self._last_bottom_hash = None
        self._seen_phashes = []
        self._ocr_cache = {}

    def get_stats(self):
        """获取统计信息"""
        total = self.stats["frames_checked"]
        skipped = self.stats["frames_skipped_no_change"] + self.stats["frames_skipped_bottom_same"]
        skip_rate = (skipped / total * 100) if total > 0 else 0
        ocr_saved = self.stats["ocr_calls_saved"]
        return {
            **self.stats,
            "skip_rate": f"{skip_rate:.1f}%",
            "ocr_saved": ocr_saved,
        }

    def log_stats(self):
        """输出统计日志"""
        s = self.get_stats()
        logger.info(
            f"[增量检测] 检查{s['frames_checked']}帧 | "
            f"跳过{s['frames_skipped_no_change'] + s['frames_skipped_bottom_same']}帧({s['skip_rate']}) | "
            f"全图OCR:{s['ocr_calls_full']} 增量OCR:{s['ocr_calls_incremental']} "
            f"缓存命中:{s['ocr_calls_saved']} | "
            f"去重:{s['messages_deduplicated']}条"
        )
