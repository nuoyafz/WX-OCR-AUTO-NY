"""
消息解析模块 — 识别发送者、去重、构建对话上下文
"""
import hashlib
import logging
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

    def __init__(self, stable_frames=2, context_size=10):
        self.stable_frames = stable_frames
        self.context_size = context_size

        # 已处理消息的hash集合（永久去重）
        self.processed_hashes = set()

        # 候选消息：{text_hash: {"text": ..., "frames_seen": N}}
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
        气泡之间通常有间隔（>25px），同一气泡内文本Y坐标接近。
        """
        if not ocr_results:
            return []

        groups = []
        current_group = [ocr_results[0]]

        for i in range(1, len(ocr_results)):
            prev = ocr_results[i - 1]
            curr = ocr_results[i]
            gap = curr["y_center"] - prev["y_center"]
            if gap > 25:
                groups.append(current_group)
                current_group = [curr]
            else:
                current_group.append(curr)

        if current_group:
            groups.append(current_group)

        return groups

    def _get_bubble_sender(self, bubble_items):
        """确定气泡发送者：对气泡内所有文本的 sender 做多数投票"""
        if not bubble_items:
            return "other"
        me_count = sum(1 for r in bubble_items if r.get("sender") == "me")
        return "me" if me_count > len(bubble_items) // 2 else "other"

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

        # --- 稳定帧确认 ---
        if text_hash in self.candidate_pool:
            self.candidate_pool[text_hash]["frames_seen"] += 1
        else:
            self.candidate_pool[text_hash] = {
                "text": last_text,
                "frames_seen": 1,
            }

        candidate = self.candidate_pool[text_hash]
        if candidate["frames_seen"] >= self.stable_frames:
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

        return {"new_messages": [], "context": list(self.conversation)}

    def add_to_context(self, role, content):
        """添加消息到对话上下文"""
        self.conversation.append({"role": role, "content": content})

    def get_context(self):
        """获取当前对话上下文"""
        return list(self.conversation)

    def mark_reply_sent(self, reply_text):
        """记录自己发出的回复，用于后续过滤"""
        self.my_recent_replies.append(reply_text)