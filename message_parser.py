"""
消息解析模块 — 识别发送者、去重、构建对话上下文
"""
import hashlib
import logging
import time
from collections import deque

from tuning import BUBBLE_GROUP_GAP_PX

logger = logging.getLogger(__name__)


class MessageParser:
    """
    V2 消息解析器（新增：自己消息识别 + 群成员字段）
    ——————————————————————————————————————————————————
    1. 从OCR结果中识别消息气泡（按Y坐标分组）
    2. 根据视觉特征区分"我"和"对方"的消息（对方群聊还能区分具体群成员）
    3. 消息去重（稳定帧确认 + 已处理hash集合）
    4. 构建对话上下文（给LLM用）
    5. V2: 不再过滤自己的消息 → 自己的消息也会进入 new_messages（但不触发自动回复）
    """

    def __init__(self, stable_frames=2, context_size=10, stable_timeout=2.0,
                 include_own_messages=True):
        self.stable_frames = stable_frames
        self.context_size = context_size
        self.stable_timeout = stable_timeout
        # V2: 是否把"自己"发的消息也当作 new_messages 上报（默认 True）
        self.include_own_messages = include_own_messages

        # 已处理消息的hash集合（永久去重）
        self.processed_hashes = set()

        # 候选消息：{text_hash: {"text": ..., "frames_seen": N, "first_seen": ts,
        #                          "sender": "me"/"other", "group_member": xxx}}
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
        气泡之间通常有间隔（>BUBBLE_GROUP_GAP_PX），同一气泡内文本Y坐标接近。
        间距阈值统一取自 tuning.BUBBLE_GROUP_GAP_PX（默认40px，适应屏幕外Y波动）。
        """
        if not ocr_results:
            return []

        groups = []
        current_group = [ocr_results[0]]

        for i in range(1, len(ocr_results)):
            prev = ocr_results[i - 1]
            curr = ocr_results[i]
            gap = curr["y_center"] - prev["y_center"]
            if gap > BUBBLE_GROUP_GAP_PX:
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
        """检查文本是否可能是我刚发出的回复（OCR误读）。
        收紧判定：只有"完全相同"或"前10字相同且长度接近"才跳过，
        避免把用户手动发的、与回复相似或包含回复内容的消息误跳过。"""
        if not text or not text.strip():
            return False
        for reply in self.my_recent_replies:
            if not reply or not reply.strip():
                continue
            if text.strip() == reply.strip():
                return True
            # 前10字相同 + 长度差≤3（容忍OCR首尾抖动），且都≥8字才判重
            if len(text) >= 8 and len(reply) >= 8 and \
               abs(len(text) - len(reply)) <= 3 and text[:10] == reply[:10]:
                return True
        return False

    def feed(self, ocr_results):
        """
        V2: 输入一帧OCR结果，返回本次发现的新消息和对话上下文。
        新行为：
          - 自己的消息也会出现在 new_messages 里（sender="me"）
          - 群聊：每条 other 消息携带 group_member 字段

        Args:
            ocr_results: OCR识别结果列表（需包含 sender / group_member 字段）

        Returns:
            dict: {
                "new_messages": [{"sender": "me"/"other",
                                   "content": "...",
                                   "group_member": "张三"|None,
                                   "confidence": 0.x,
                                   "sender_confidence": 0.x }],
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

        for item in visible_context:
            h = self._hash(item["content"])
            if h not in self._context_hashes:
                self._context_hashes.add(h)
                self.conversation.append(item)

        # --- V2: 对 *所有气泡*（从最后几条往前倒推）都做候选确认，
        #        避免因为有"最后一条是我"而跳过了倒数第二条对方新消息。
        #        最多检查最后 12 条气泡（性能与完备性的折衷） ---
        newly_confirmed = []
        now_ts = time.time()

        for group in groups[-12:]:
            sender = self._get_bubble_sender(group)
            text = self._get_bubble_text(group)
            if not text.strip():
                continue

            # 额外字段（群成员 / 置信度）
            group_member = None
            avg_confidence = 0.0
            avg_sender_conf = 0.0
            n = len(group)
            if n > 0:
                # 群成员：取第一个带 group_member 的，一般一条气泡只对应一个成员
                for r in group:
                    if r.get("group_member"):
                        group_member = r["group_member"]
                        break
                avg_confidence = sum(float(r.get("confidence", 0.0)) for r in group) / n
                avg_sender_conf = sum(float(r.get("sender_confidence", 0.5)) for r in group) / n

            # 去重key：文本 + sender + group_member（同一句话不同成员发的不能视为重复）
            key_parts = [sender, text.strip()]
            if group_member:
                key_parts.append("gm:" + group_member)
            text_hash = self._hash("|".join(key_parts))

            # V2: 自己的消息也允许上报（如果 include_own_messages=True）
            #  但我们仍把它和对方消息区分处理；
            #  自动回复逻辑在 main.py 里基于 sender 判断不触发回复即可。
            if sender == "me" and not self.include_own_messages:
                # 历史兼容：用户关掉自己消息上报时，仍加入上下文但不新消息
                self.processed_hashes.add(text_hash)
                continue

            if text_hash in self.processed_hashes:
                continue
            # 疑似我刚发的回复文本（OCR复读）→ 仍可能是我的真实消息，这里不再丢，
            # 但把 my_recent_replies 放进来后 main.py 的"避免AI复读自己"逻辑更稳：
            if sender == "me" and self._is_my_reply(text):
                # 是我刚刚由机器人发送的 → 跳过（避免循环）
                self.processed_hashes.add(text_hash)
                continue

            # 稳定帧
            if text_hash in self.candidate_pool:
                self.candidate_pool[text_hash]["frames_seen"] += 1
            else:
                self.candidate_pool[text_hash] = {
                    "text": text,
                    "sender": sender,
                    "group_member": group_member,
                    "confidence": avg_confidence,
                    "sender_confidence": avg_sender_conf,
                    "frames_seen": 1,
                    "first_seen": now_ts,
                }

            candidate = self.candidate_pool[text_hash]
            waited = now_ts - candidate.get("first_seen", now_ts)
            if candidate["frames_seen"] >= self.stable_frames or waited >= self.stable_timeout:
                self.processed_hashes.add(text_hash)
                if text_hash in self.candidate_pool:
                    del self.candidate_pool[text_hash]
                newly_confirmed.append({
                    "sender": candidate["sender"],
                    "content": candidate["text"],
                    "group_member": candidate.get("group_member"),
                    "confidence": round(candidate.get("confidence", 0.0), 3),
                    "sender_confidence": round(candidate.get("sender_confidence", 0.0), 3),
                })

        # 清理过量候选
        if len(self.candidate_pool) > 80:
            self.candidate_pool.clear()

        return {"new_messages": newly_confirmed,
                "context": list(self.conversation)}

    def flush_stable_timeout(self):
        """
        超时确认候选消息（主循环在画面静止、跳过OCR时调用）。
        返回 list of {"sender", "content", "group_member"}
        """
        now_ts = time.time()
        confirmed = []
        for text_hash in list(self.candidate_pool.keys()):
            cand = self.candidate_pool[text_hash]
            waited = now_ts - cand.get("first_seen", now_ts)
            if waited >= self.stable_timeout:
                self.processed_hashes.add(text_hash)
                del self.candidate_pool[text_hash]
                confirmed.append({
                    "sender": cand.get("sender", "other"),
                    "content": cand.get("text", ""),
                    "group_member": cand.get("group_member"),
                    "confidence": round(cand.get("confidence", 0.0), 3),
                    "sender_confidence": round(cand.get("sender_confidence", 0.0), 3),
                })
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