"""
AI训练引擎 — AI辅助学习 → 规则固化 → 离线推理
================================================================
三阶段渐进式学习：
  阶段1（前10次）：AI看截图+OCR → 智能判断 → 记录规则
  阶段2（10次后）：纯规则处理，不调用AI
  阶段3（手动重置）：微信更新UI后重新学习

学到的规则保存在 learned_rules.json，支持手动编辑。
"""
import os
import json
import time
import base64
import logging
import numpy as np
import cv2

logger = logging.getLogger(__name__)

# 规则库路径
RULES_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "learned_rules.json"
)

# 训练样本目录
TRAINING_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "training_data"
)


class AITrainer:
    """AI辅助学习引擎"""

    def __init__(self, llm_config, training_threshold=10):
        """
        Args:
            llm_config: LLM配置字典
            training_threshold: AI学习次数阈值，超过后切换纯规则模式
        """
        self.llm_config = llm_config or {}
        self.training_threshold = training_threshold
        self.training_count = 0
        self.rules = self._load_rules()
        self._llm_client = None

        os.makedirs(TRAINING_DIR, exist_ok=True)

        logger.info(f"[AI训练] 初始化完成, 训练次数={self.training_count}/{self.training_threshold}, "
                    f"规则库={'已加载' if self.rules else '空'}")

    @property
    def llm_client(self):
        """懒加载LLM客户端"""
        if self._llm_client is None:
            try:
                from llm_client import LLMClient
                self._llm_client = LLMClient(self.llm_config)
            except Exception as e:
                logger.error(f"[AI训练] LLM客户端初始化失败: {e}")
        return self._llm_client

    def should_use_ai(self):
        """是否还需要AI辅助"""
        return self.training_count < self.training_threshold

    def get_progress(self):
        """获取训练进度"""
        return {
            "current": self.training_count,
            "threshold": self.training_threshold,
            "phase": "AI学习期" if self.should_use_ai() else "规则运行期",
            "rules_loaded": len(self.rules) if self.rules else 0,
        }

    # ================================================================
    # 规则库管理
    # ================================================================

    def _load_rules(self):
        """加载已学规则"""
        try:
            if os.path.exists(RULES_PATH):
                with open(RULES_PATH, "r", encoding="utf-8") as f:
                    rules = json.load(f)
                # 读取训练次数
                self.training_count = rules.get("_meta", {}).get("training_count", 0)
                logger.info(f"[AI训练] 加载规则库: {len(rules)-1} 条规则, 训练次数={self.training_count}")
                return rules
        except Exception as e:
            logger.warning(f"[AI训练] 规则库加载失败: {e}")
        return self._default_rules()

    def _default_rules(self):
        """默认规则模板"""
        return {
            "_meta": {
                "training_count": 0,
                "last_updated": "",
                "version": "1.0",
                "description": "AI学习规则库 — 可手动编辑"
            },
            "red_dot": {
                "hsv_lower": [0, 120, 180],
                "hsv_upper": [10, 255, 255],
                "min_area": 150,
                "max_area": 5000,
                "min_circularity": 0.5,
                "min_x_ratio": 0.30,
                "description": "红点检测参数（HSV阈值、面积、圆形度、位置）"
            },
            "contact_name": {
                "valid_patterns": [],
                "invalid_patterns": [
                    r"^\d{1,2}[:：]\d{2}$",
                    r"^\d{1,3}\+?$",
                    r"^(昨天|今天|前天|明天|星期[一二三四五六日天]?|周[一二三四五六日天]?)$",
                    r"^\[?\d{1,3}\+?\s*条\]?$"
                ],
                "description": "联系人名识别规则"
            },
            "new_message": {
                "other_x_threshold": 0.35,
                "me_x_threshold": 0.65,
                "description": "新消息发送者判断（对方靠左，自己靠右）"
            }
        }

    def _save_rules(self):
        """保存规则库"""
        try:
            self.rules["_meta"]["training_count"] = self.training_count
            self.rules["_meta"]["last_updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
            with open(RULES_PATH, "w", encoding="utf-8") as f:
                json.dump(self.rules, f, ensure_ascii=False, indent=2)
            logger.info(f"[AI训练] 规则库已保存, 训练次数={self.training_count}")
        except Exception as e:
            logger.error(f"[AI训练] 规则库保存失败: {e}")

    def reset_training(self):
        """重置训练（微信更新UI后重新学习）"""
        self.training_count = 0
        self.rules = self._default_rules()
        self._save_rules()
        logger.info("[AI训练] 训练已重置，重新进入AI学习期")

    def edit_rules(self, new_rules):
        """手动编辑规则库"""
        if isinstance(new_rules, str):
            new_rules = json.loads(new_rules)
        new_rules["_meta"] = self.rules.get("_meta", {})
        new_rules["_meta"]["training_count"] = self.training_count
        new_rules["_meta"]["last_updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self.rules = new_rules
        self._save_rules()
        logger.info("[AI训练] 规则库已手动更新")

    # ================================================================
    # AI分析（阶段1：前10次）
    # ================================================================

    def _image_to_base64(self, image):
        """numpy图片转base64"""
        try:
            from PIL import Image
            import io
            rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            buf = io.BytesIO()
            Image.fromarray(rgb).save(buf, format="PNG")
            return base64.b64encode(buf.getvalue()).decode("utf-8")
        except Exception as e:
            logger.error(f"[AI训练] 图片转base64失败: {e}")
            return None

    def analyze_with_ai(self, ocr_results, screenshot=None, context="sidebar"):
        """
        AI辅助分析：让AI看截图+OCR，判断红点/联系人/新消息。

        Args:
            ocr_results: OCR识别结果列表
            screenshot: numpy截图（可选）
            context: "sidebar"=侧边栏红点检测, "chat"=聊天消息提取

        Returns:
            dict: AI分析结果
        """
        if not self.llm_client:
            logger.warning("[AI训练] LLM不可用，回退规则模式")
            return self.analyze_with_rules(ocr_results, screenshot, context)

        # 构建OCR文本摘要
        ocr_summary = []
        for i, r in enumerate(ocr_results[:30]):
            text = r.get("text", "")[:50]
            y = int(r.get("y_center", 0))
            x = int(r.get("x_center", 0))
            ocr_summary.append(f"{i+1}. \"{text}\" @ ({x},{y})")

        ocr_text = "\n".join(ocr_summary)

        # 构建提示词
        if context == "sidebar":
            system_prompt = """你是一个微信界面分析专家。分析微信侧边栏的OCR结果和截图，判断哪些位置有未读红点，哪些文字是联系人名。
返回严格的JSON格式。"""
            user_prompt = f"""请分析以下微信侧边栏数据：

OCR识别结果：
{ocr_text}

请以JSON格式返回分析结果：
{{
  "red_dots": [
    {{"position": [x, y], "contact": "联系人名", "unread_count": "数字或?", "reason": "判断依据"}}
  ],
  "contacts": ["联系人名1", "联系人名2"],
  "not_contacts": ["被排除的文字1", "被排除的文字2"],
  "rules_learned": {{
    "red_dot_feature": "红点的视觉特征描述",
    "contact_name_feature": "联系人名的命名规律",
    "invalid_text_feature": "非联系人名的文字特征"
  }}
}}"""
        else:  # chat
            system_prompt = """你是一个微信消息分析专家。分析微信聊天区域的OCR结果和截图，判断哪些是新消息，哪些是历史消息，每条消息的发送者是谁。
返回严格的JSON格式。"""
            user_prompt = f"""请分析以下微信聊天区域数据：

OCR识别结果：
{ocr_text}

请以JSON格式返回分析结果：
{{
  "new_messages": [
    {{"text": "消息内容", "sender": "other或me", "reason": "判断依据"}}
  ],
  "rules_learned": {{
    "sender_position": "发送者位置规律",
    "new_message_feature": "新消息的视觉特征"
  }}
}}"""

        messages = [{"role": "system", "content": system_prompt}]

        # 如果有截图，发送多模态消息
        if screenshot is not None:
            img_b64 = self._image_to_base64(screenshot)
            if img_b64:
                user_content = [
                    {"type": "text", "text": user_prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}}
                ]
                messages.append({"role": "user", "content": user_content})
            else:
                messages.append({"role": "user", "content": user_prompt})
        else:
            messages.append({"role": "user", "content": user_prompt})

        # 调用LLM
        try:
            response = self.llm_client._call_raw(messages, max_tokens=1000, temperature=0.2)
            if not response:
                logger.warning("[AI训练] AI返回空，回退规则模式")
                return self.analyze_with_rules(ocr_results, screenshot, context)

            # 解析JSON
            result = self._parse_ai_response(response)
            if result:
                # 学习并更新规则
                self._update_rules_from_ai(result, context)
                self.training_count += 1
                self._save_rules()
                logger.info(f"[AI训练] AI分析完成({self.training_count}/{self.training_threshold}), "
                            f"规则已更新")

                # 保存训练样本
                self._save_training_sample(ocr_results, screenshot, result, context)

                return result
            else:
                logger.warning("[AI训练] AI响应解析失败，回退规则模式")
                return self.analyze_with_rules(ocr_results, screenshot, context)

        except Exception as e:
            logger.error(f"[AI训练] AI分析失败: {e}")
            return self.analyze_with_rules(ocr_results, screenshot, context)

    def _parse_ai_response(self, response):
        """解析AI返回的JSON"""
        try:
            # 尝试直接解析
            result = json.loads(response)
            return result
        except json.JSONDecodeError:
            # 尝试提取JSON块
            import re
            match = re.search(r'\{[\s\S]*\}', response)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
            logger.warning(f"[AI训练] JSON解析失败, 原始响应: {response[:200]}")
            return None

    def _update_rules_from_ai(self, ai_result, context):
        """从AI分析结果中学习规则"""
        try:
            if context == "sidebar":
                rules = ai_result.get("rules_learned", {})

                # 学习联系人名特征
                if "contact_name_feature" in rules:
                    contact_rules = self.rules.setdefault("contact_name", {})
                    if "learned_features" not in contact_rules:
                        contact_rules["learned_features"] = []
                    feature = rules["contact_name_feature"]
                    if feature not in contact_rules["learned_features"]:
                        contact_rules["learned_features"].append(feature)
                        # 保留最近10条
                        contact_rules["learned_features"] = contact_rules["learned_features"][-10:]

                # 学习无效文本特征
                if "invalid_text_feature" in rules:
                    contact_rules = self.rules.setdefault("contact_name", {})
                    if "learned_invalid" not in contact_rules:
                        contact_rules["learned_invalid"] = []
                    feature = rules["invalid_text_feature"]
                    if feature not in contact_rules["learned_invalid"]:
                        contact_rules["learned_invalid"].append(feature)
                        contact_rules["learned_invalid"] = contact_rules["learned_invalid"][-10:]

                # 学习红点特征
                if "red_dot_feature" in rules:
                    red_rules = self.rules.setdefault("red_dot", {})
                    if "learned_features" not in red_rules:
                        red_rules["learned_features"] = []
                    feature = rules["red_dot_feature"]
                    if feature not in red_rules["learned_features"]:
                        red_rules["learned_features"].append(feature)
                        red_rules["learned_features"] = red_rules["learned_features"][-10:]

            elif context == "chat":
                rules = ai_result.get("rules_learned", {})
                msg_rules = self.rules.setdefault("new_message", {})
                if "sender_position" in rules:
                    if "learned_features" not in msg_rules:
                        msg_rules["learned_features"] = []
                    msg_rules["learned_features"].append(rules["sender_position"])
                    msg_rules["learned_features"] = msg_rules["learned_features"][-10:]
                if "new_message_feature" in rules:
                    if "learned_new_features" not in msg_rules:
                        msg_rules["learned_new_features"] = []
                    msg_rules["learned_new_features"].append(rules["new_message_feature"])
                    msg_rules["learned_new_features"] = msg_rules["learned_new_features"][-10:]

        except Exception as e:
            logger.warning(f"[AI训练] 规则学习失败: {e}")

    def _save_training_sample(self, ocr_results, screenshot, ai_result, context):
        """保存训练样本（用于调试和回溯）"""
        try:
            timestamp = int(time.time() * 1000)
            sample_path = os.path.join(TRAINING_DIR, f"sample_{context}_{timestamp}.json")

            sample = {
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "context": context,
                "ocr_results": [{"text": r.get("text", ""), "x": int(r.get("x_center", 0)),
                                 "y": int(r.get("y_center", 0))} for r in ocr_results[:30]],
                "ai_result": ai_result,
            }

            with open(sample_path, "w", encoding="utf-8") as f:
                json.dump(sample, f, ensure_ascii=False, indent=2)

            # 保存截图（只保存前10个样本的截图，避免占空间）
            if screenshot is not None and self.training_count <= 10:
                from PIL import Image
                img_path = os.path.join(TRAINING_DIR, f"sample_{context}_{timestamp}.png")
                rgb = cv2.cvtColor(screenshot, cv2.COLOR_BGR2RGB)
                Image.fromarray(rgb).save(img_path)

            # 清理旧样本（只保留最近50个）
            samples = [f for f in os.listdir(TRAINING_DIR) if f.startswith("sample_")]
            if len(samples) > 100:
                samples.sort()
                for old in samples[:len(samples) - 100]:
                    os.remove(os.path.join(TRAINING_DIR, old))

        except Exception as e:
            logger.debug(f"[AI训练] 样本保存失败: {e}")

    # ================================================================
    # 规则分析（阶段2：10次后）
    # ================================================================

    def analyze_with_rules(self, ocr_results, screenshot=None, context="sidebar"):
        """
        纯规则分析：用学到的规则直接判断，不调用AI。
        """
        if context == "sidebar":
            return self._rules_analyze_sidebar(ocr_results, screenshot)
        else:
            return self._rules_analyze_chat(ocr_results, screenshot)

    def _rules_analyze_sidebar(self, ocr_results, screenshot):
        """规则模式：侧边栏分析"""
        import re

        # 如果有截图，用HSV检测红点
        red_dots = []
        if screenshot is not None:
            red_rules = self.rules.get("red_dot", {})
            red_dots = self._detect_red_dots_hsv(screenshot, red_rules)

        # 联系人名过滤
        contact_rules = self.rules.get("contact_name", {})
        invalid_patterns = contact_rules.get("invalid_patterns", [])

        contacts = []
        not_contacts = []

        for r in ocr_results:
            text = r.get("text", "").strip()
            if not text:
                continue

            is_valid = True
            for pattern in invalid_patterns:
                if re.match(pattern, text):
                    is_valid = False
                    not_contacts.append(text)
                    break

            if is_valid:
                # 额外检查：纯数字排除
                if text.isdigit():
                    not_contacts.append(text)
                else:
                    contacts.append(text)

        # 匹配红点与联系人
        matched = []
        if red_dots:
            for dot in red_dots:
                dot_y = dot.get("center_y", 0)
                best_name = None
                best_diff = 999
                for r in ocr_results:
                    text = r.get("text", "").strip()
                    y = r.get("y_center", 0)
                    y_diff = abs(y - dot_y)
                    if y_diff <= 60 and y_diff < best_diff:
                        # 检查是否是合法联系人名
                        is_invalid = any(re.match(p, text) for p in invalid_patterns)
                        if not is_invalid and not text.isdigit() and text:
                            best_name = text
                            best_diff = y_diff

                matched.append({
                    "position": [dot.get("center_x", 0), dot_y],
                    "contact": best_name or f"未读_{dot_y}",
                    "unread_count": "?",
                    "reason": f"规则匹配(y_diff={best_diff}px)"
                })

        return {
            "red_dots": matched,
            "contacts": contacts,
            "not_contacts": not_contacts,
            "rules_learned": {}
        }

    def _rules_analyze_chat(self, ocr_results, screenshot):
        """规则模式：聊天消息分析"""
        msg_rules = self.rules.get("new_message", {})
        other_threshold = msg_rules.get("other_x_threshold", 0.35)
        me_threshold = msg_rules.get("me_x_threshold", 0.65)

        if not ocr_results:
            return {"new_messages": [], "rules_learned": {}}

        # 获取图像宽度
        img_w = 1
        if screenshot is not None:
            img_w = screenshot.shape[1]
        else:
            max_x = max((r.get("x_center", 0) for r in ocr_results), default=1)
            img_w = max(max_x * 1.5, 1)

        new_messages = []
        for r in ocr_results:
            text = r.get("text", "").strip()
            if not text:
                continue
            x_ratio = r.get("x_center", 0) / img_w

            if x_ratio < other_threshold:
                sender = "other"
            elif x_ratio > me_threshold:
                sender = "me"
            elif x_ratio < 0.48:
                sender = "other"
            elif x_ratio > 0.52:
                sender = "me"
            else:
                sender = "other"

            if sender == "other":
                new_messages.append({
                    "text": text,
                    "sender": sender,
                    "reason": f"规则判断(x={x_ratio:.1%})"
                })

        return {"new_messages": new_messages, "rules_learned": {}}

    def _detect_red_dots_hsv(self, image, red_rules):
        """使用规则库中的HSV参数检测红点"""
        if image is None:
            return []

        h, w = image.shape[:2]
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)

        lower = np.array(red_rules.get("hsv_lower", [0, 120, 180]))
        upper = np.array(red_rules.get("hsv_upper", [10, 255, 255]))
        mask1 = cv2.inRange(hsv, lower, upper)

        # 第二段红色
        lower2 = np.array([170, 120, 180])
        upper2 = np.array([180, 255, 255])
        mask2 = cv2.inRange(hsv, lower2, upper2)
        mask = cv2.bitwise_or(mask1, mask2)

        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        min_area = red_rules.get("min_area", 150)
        max_area = red_rules.get("max_area", 5000)
        min_circ = red_rules.get("min_circularity", 0.5)
        min_x_ratio = red_rules.get("min_x_ratio", 0.30)

        red_dots = []
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < min_area or area > max_area:
                continue

            x, y, cw, ch = cv2.boundingRect(cnt)
            center_x = x + cw // 2
            center_y = y + ch // 2

            if center_x < w * min_x_ratio:
                continue

            perimeter = cv2.arcLength(cnt, True)
            circularity = 4 * 3.14159 * area / (perimeter * perimeter) if perimeter > 0 else 0
            if circularity < min_circ:
                continue

            red_dots.append({
                "x": x, "y": y, "w": cw, "h": ch,
                "center_x": center_x, "center_y": center_y,
                "area": area, "circularity": circularity,
            })

        return red_dots

    # ================================================================
    # 统一入口
    # ================================================================

    def analyze(self, ocr_results, screenshot=None, context="sidebar"):
        """
        统一分析入口：根据训练进度选择AI或规则。

        Args:
            ocr_results: OCR识别结果
            screenshot: numpy截图（可选，AI模式会发给LLM看）
            context: "sidebar" 或 "chat"

        Returns:
            dict: 分析结果
        """
        if self.should_use_ai():
            return self.analyze_with_ai(ocr_results, screenshot, context)
        else:
            return self.analyze_with_rules(ocr_results, screenshot, context)
