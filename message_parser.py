"""
消息解析模块 — 识别发送者、去重、构建对话上下文
"""
import hashlib
import logging
import time
from collections import deque

logger = logging.getLogger(__name__)


class MessageParser:
    """
    消息解析器，负责：
    1. 从OCR结果中识别消息气泡（按Y坐标分组）
    2. 根据视觉特征区分"我"和"对方"的消息
    3. 消息去重（稳定帧确认 + 已处理hash集合）
    4. 构建对话上下文（给LLM用）
    5. 只回复"对方"的消息，忽略自己的消息
    """

    def __init__(self, stable_frames=2, context_size=10, stable_timeout=2.0):
        self.stable_frames = stable_frames
        self.context_size = context_size
        # 稳定帧超时兜底：增量检测在画面静止时不再调用feed，
        # 候选消息可能永远凑不够stable_frames帧 → 超时后强制确认
        self.stable_timeout = stable_timeout

        # 已处理消息的hash集合（永久去重）
        self.processed_hashes = set()

        # 候选消息：{text_hash: {"text": ..., "frames_seen": N, "first_seen": ts}}
        self.candidate_pool = {}

        # 对话上下文：deque of {"role": "user"/"assistant", "content": "..."}
        self.conversation = deque(maxlen=context_size * 2)

        # 已加入上下文的文本hash（防止重复堆积）
        self._context_hashes = set()

        # 最近发送的回复（用于过滤OCR误读自己的回复）
        self.my_recent_replies = deque(maxlen=5)

    def _hash(self, text):
        """文本哈希"""
        normalized = text.strip()
        return hashlib.md5(normalized.encode("utf-8")).hexdigest()

    def _group_by_bubble(self, ocr_results):
        """
        根据Y坐标将OCR结果分组为消息气泡。
        气泡之间通常有间隔（>40px），同一气泡内文本Y坐标接近。
        放宽到40px以适应屏幕外截图的Y坐标波动。
        """
        if not ocr_results:
            return []

        groups = []
        current_group = [ocr_results[0]]

        for i in range(1, len(ocr_results)):
            prev = ocr_results[i - 1]
            curr = ocr_results[i]
            gap = curr["y_center"] - prev["y_center"]
            if gap > 40:
                groups.append(current_group)
                current_group = [curr]
            else:
                current_group.append(curr)

        if current_group:
            groups.append(current_group)

        return groups

    def _get_bubble_sender(self, bubble_items):
        """确定气泡发送者：综合位置和颜色判断"""
        if not bubble_items:
            return "other"

        # 统计 sender
        me_count = sum(1 for r in bubble_items if r.get("sender") == "me")
        other_count = sum(1 for r in bubble_items if r.get("sender") == "other")

        # 如果有不同判断，用多数投票
        if me_count > other_count:
            return "me"
        if other_count > me_count:
            return "other"

        # 票数相同或都为0，用位置判断（取第一条的位置）
        r = bubble_items[0]
        bbox = r.get("bbox", [])
        if len(bbox) >= 2:
            left_x = min(p[0] for p in bbox)
            right_x = max(p[0] for p in bbox)
            # 如果气泡右边界接近窗口右侧 → me
            # 如果气泡左边界在左侧1/3 → other
            if left_x < 100:
                return "other"
        return "other"  # 默认当作对方消息，避免漏报

    def _get_bubble_text(self, bubble_items):
        """提取气泡的完整文本"""
        return "\n".join(r["text"] for r in bubble_items)

    def _is_my_reply(self, text):
        """检查文本是否可能是我刚发出的回复（OCR误读）"""
        for reply in self.my_recent_replies:
            if len(text) > 3 and len(reply) > 3:
                if text[:10] == reply[:10] or reply in text or text in reply:
                    return True
        return False

    def feed(self, ocr_results):
        """
        输入一帧OCR结果，返回本次发现的新消息和对话上下文。

        Args:
            ocr_results: OCR识别结果列表（需包含 sender 字段）

        Returns:
            dict: {
                "new_messages": [{"sender": "other", "content": "..."}],
                "context": [{"role": "user"/"assistant", "content": "..."}]
            }
        """
        if not ocr_results:
            return {"new_messages": [], "context": list(self.conversation)}

        groups = self._group_by_bubble(ocr_results)
        if not groups:
            return {"new_messages": [], "context": list(self.conversation)}

        # --- 构建可见上下文 ---
        visible_context = []
        for group in groups:
            sender = self._get_bubble_sender(group)
            text = self._get_bubble_text(group)
            if not text.strip():
                continue
            role = "assistant" if sender == "me" else "user"
            visible_context.append({"role": role, "content": text})

        # 将可见消息合并到持久上下文（去重：同一内容不重复添加）
        for item in visible_context:
            h = self._hash(item["content"])
            if h not in self._context_hashes:
                self._context_hashes.add(h)
                self.conversation.append(item)

        # --- 只检查最后一条消息是否需要回复 ---
        last_group = groups[-1]
        last_sender = self._get_bubble_sender(last_group)
        last_text = self._get_bubble_text(last_group)
        text_hash = self._hash(last_text)

        # 我自己的消息 → 不回复
        if last_sender == "me":
            return {"new_messages": [], "context": list(self.conversation)}

        # 已处理过 → 不回复
        if text_hash in self.processed_hashes:
            return {"new_messages": [], "context": list(self.conversation)}

        # 疑似自己的回复（OCR误读）→ 不回复
        if self._is_my_reply(last_text):
            return {"new_messages": [], "context": list(self.conversation)}

        # --- 稳定帧确认（带超时兜底） ---
        now_ts = time.time()
        if text_hash in self.candidate_pool:
            self.candidate_pool[text_hash]["frames_seen"] += 1
        else:
            self.candidate_pool[text_hash] = {
                "text": last_text,
                "frames_seen": 1,
                "first_seen": now_ts,
            }

        candidate = self.candidate_pool[text_hash]
        waited = now_ts - candidate.get("first_seen", now_ts)
        # stable_frames=1 时第一帧就确认；超时也确认
        if candidate["frames_seen"] >= self.stable_frames or waited >= self.stable_timeout:
            self.processed_hashes.add(text_hash)
            if text_hash in self.candidate_pool:
                del self.candidate_pool[text_hash]
            return {
                "new_messages": [{"sender": "other", "content": last_text}],
                "context": list(self.conversation),
            }

        # 清理过期候选
        if len(self.candidate_pool) > 50:
            self.candidate_pool.clear()

        # 诊断日志：为什么没确认
        import logging
        logging.getLogger(__name__).debug(
            f"[parser] 候选未确认: frames={candidate['frames_seen']}/{self.stable_frames}, "
            f"waited={waited:.1f}/{self.stable_timeout}, pool={len(self.candidate_pool)}, "
            f"text={last_text[:30]}")

        return {"new_messages": [], "context": list(self.conversation)}

    def flush_stable_timeout(self):
        """
        超时确认候选消息（主循环在画面静止、跳过OCR时调用）。
        增量检测跳过帧 → feed不再被调用 → 候选永远凑不够stable_frames，
        此方法把等待超过stable_timeout的候选强制确认为新消息。

        Returns:
            list of {"sender": "other", "content": "..."}
        """
        now_ts = time.time()
        confirmed = []
        for text_hash in list(self.candidate_pool.keys()):
            cand = self.candidate_pool[text_hash]
            waited = now_ts - cand.get("first_seen", now_ts)
            if waited >= self.stable_timeout:
                self.processed_hashes.add(text_hash)
                del self.candidate_pool[text_hash]
                confirmed.append({"sender": "other", "content": cand["text"]})
        return confirmed

    def add_to_context(self, role, content):
        """添加消息到对话上下文"""
        self.conversation.append({"role": role, "content": content})

    def get_context(self):
        """获取当前对话上下文"""
        return list(self.conversation)

    def mark_reply_sent(self, reply_text):
        """记录自己发出的回复，用于后续过滤"""
        self.my_recent_replies.append(reply_text)