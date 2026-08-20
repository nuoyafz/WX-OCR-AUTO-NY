"""
信息提取引擎 — 关键词/正则匹配 + LLM结构化提取
================================================
三层提取策略：
1. 关键词匹配：监控紧急/会议/金钱/联系方式/任务等关键词
2. 正则提取：自动提取手机号/邮箱/网址/金额/日期/时间/身份证
3. LLM智能提取（可选）：消息分类/紧急度/摘要/待办/情绪分析
"""
import re
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class InfoExtractor:
    """信息提取引擎"""

    def __init__(self, extraction_config, llm_config=None):
        self.keywords = extraction_config.get("keywords", {})
        self.regex_rules = self._compile_regex_rules(
            extraction_config.get("regex_rules", [])
        )
        llm_extract_cfg = extraction_config.get("llm_extract", {})
        self.llm_enabled = llm_extract_cfg.get("enabled", False)
        self.llm_fields = llm_extract_cfg.get("fields", [])
        self.llm_config = llm_config
        self._llm_client = None

        # 加载自定义规则
        self.custom_keywords = []
        self.custom_regex = []
        custom_cfg = extraction_config.get("custom_rules", {}) if extraction_config else {}
        for kw_rule in custom_cfg.get("keywords", []):
            self.custom_keywords.append({
                "words": kw_rule.get("words", []),
                "category": kw_rule.get("category", "自定义"),
                "important": kw_rule.get("important", False),
            })
        for rx_rule in custom_cfg.get("regex", []):
            try:
                self.custom_regex.append({
                    "name": rx_rule.get("name", ""),
                    "pattern": re.compile(rx_rule.get("pattern", "")),
                    "group": rx_rule.get("group", "自定义"),
                })
            except Exception as e:
                logger.warning(f"自定义正则编译失败: {rx_rule.get('name', '?')} - {e}")

        # 加载分类优先级规则（classification）
        self.classification_enabled = True
        self.classification_categories = []
        cls_cfg = extraction_config.get("classification", {}) if extraction_config else {}
        if cls_cfg:
            self.classification_enabled = bool(cls_cfg.get("enabled", True))
            for c in cls_cfg.get("categories", []):
                kws = c.get("keywords", [])
                if isinstance(kws, str):
                    kws = [k.strip() for k in kws.split(",") if k.strip()]
                self.classification_categories.append({
                    "name": c.get("name", "未命名"),
                    "priority": int(c.get("priority", 0)),
                    "important": bool(c.get("important", False)),
                    "keywords": kws or [],
                })

        if self.custom_keywords or self.custom_regex:
            logger.info(f"已加载 {len(self.custom_keywords)} 组自定义关键词, {len(self.custom_regex)} 个自定义正则")
    def _compile_regex_rules(self, rules):
        compiled = []
        for rule in rules:
            try:
                compiled.append({
                    "name": rule["name"],
                    "pattern": re.compile(rule["pattern"]),
                    "group": rule.get("group", "other"),
                })
            except re.error as e:
                logger.warning(f"正则规则编译失败 {rule.get('name', '?')}: {e}")
        return compiled

    def set_classification(self, categories):
        """UI 实时更新分类优先级规则（不重建 extractor）"""
        self.classification_categories = []
        for c in categories or []:
            kws = c.get("keywords", [])
            if isinstance(kws, str):
                kws = [k.strip() for k in kws.split(",") if k.strip()]
            self.classification_categories.append({
                "name": c.get("name", "未命名"),
                "priority": int(c.get("priority", 0)),
                "important": bool(c.get("important", False)),
                "keywords": kws or [],
            })

    def _classify(self, text, extra=None):
        """按分类优先级规则归类，返回按 priority 降序的命中类别列表（最高优先级在前）。"""
        if not self.classification_enabled or not self.classification_categories:
            return []
        chat_kind = (extra or {}).get("chat_kind")
        matched = []
        for cat in self.classification_categories:
            kws = cat.get("keywords") or []
            hit = any(kw and kw in text for kw in kws) if kws else False
            # 无关键词的结构性类别（如“群聊”）：会话为群聊时自动命中（无需关键词）
            if not hit and not kws and chat_kind == "group":
                hit = True
            if hit:
                matched.append({
                    "name": cat.get("name", "未命名"),
                    "priority": int(cat.get("priority", 0)),
                    "important": bool(cat.get("important", False)),
                })
        matched.sort(key=lambda c: c["priority"], reverse=True)
        return matched

    def extract(self, text, sender="other", contact_name="", timestamp=None, extra=None):
        if timestamp is None:
            timestamp = datetime.now()

        result = {
            "contact": contact_name,
            "sender": sender,
            "raw_text": text,
            "timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "matched_keywords": [],
            "keyword_categories": [],
            "regex_extracts": {},
            "is_important": False,
            "importance_reason": "",
            "classification": "",
            "priority": 0,
        }

        matched_cats, matched_kws = self._match_keywords(text)
        result["keyword_categories"] = matched_cats
        result["matched_keywords"] = matched_kws

        if "urgent" in matched_cats:
            result["is_important"] = True
            result["importance_reason"] = "包含紧急关键词"
        elif "task" in matched_cats and sender == "other":
            result["is_important"] = True
            result["importance_reason"] = "对方提出任务请求"

        regex_results = self._regex_extract(text)
        result["regex_extracts"] = regex_results

        if regex_results.get("contact") or regex_results.get("money"):
            if not result["is_important"]:
                result["is_important"] = True
                result["importance_reason"] = "包含联系方式或金额信息"

        # 自定义关键词匹配
        for rule in self.custom_keywords:
            for word in rule["words"]:
                if word in text:
                    if word not in result["matched_keywords"]:
                        result["matched_keywords"].append(word)
                    if rule["category"] not in result["keyword_categories"]:
                        result["keyword_categories"].append(rule["category"])
                    if rule["important"]:
                        result["is_important"] = True
                        result["importance_reason"] = result["importance_reason"] or f"自定义关键词: {word}"

        # —— 分类优先级（classification）：归类 + 优先级 + 重要判定 ——
        cls_hits = self._classify(text, extra)
        if cls_hits:
            for c in cls_hits:
                if c["name"] not in result["keyword_categories"]:
                    result["keyword_categories"].append(c["name"])
            top = cls_hits[0]  # 已按 priority 降序，取最高优先级
            result["classification"] = top["name"]
            result["priority"] = top["priority"]
            if top["important"]:
                if not result["is_important"]:
                    result["is_important"] = True
                    result["importance_reason"] = f"分类命中: {top['name']}"

        # 自定义正则提取
        for rx in self.custom_regex:
            matches = rx["pattern"].findall(text)
            if matches:
                result["regex_extracts"][rx["name"]] = matches if isinstance(matches, list) else [matches]
                if rx["group"] not in result["keyword_categories"]:
                    result["keyword_categories"].append(rx["group"])
        if self.llm_enabled and self.llm_config and sender == "other":
            llm_result = self._llm_extract(text, contact_name)
            if llm_result:
                result["llm_analysis"] = llm_result
                try:
                    urgency = int(llm_result.get("urgency", 0))
                    if urgency >= 4 and not result["is_important"]:
                        result["is_important"] = True
                        result["importance_reason"] = f"LLM判断紧急度={urgency}"
                except (ValueError, TypeError):
                    pass
            else:
                result["llm_analysis"] = {}
        else:
            result["llm_analysis"] = {}

        return result

    def _match_keywords(self, text):
        if not self.keywords or not text:
            return [], []
        text_lower = text.lower()
        matched_categories = []
        matched_keywords = []
        for category, kws in self.keywords.items():
            for kw in kws:
                if kw.lower() in text_lower:
                    if category not in matched_categories:
                        matched_categories.append(category)
                    if kw not in matched_keywords:
                        matched_keywords.append(kw)
        return matched_categories, matched_keywords

    def _regex_extract(self, text):
        if not self.regex_rules or not text:
            return {}
        results = {}
        for rule in self.regex_rules:
            matches = rule["pattern"].findall(text)
            if matches:
                group = rule["group"]
                if group not in results:
                    results[group] = []
                seen = set()
                for m in matches:
                    val = m if isinstance(m, str) else m[0]
                    if val and val not in seen:
                        seen.add(val)
                        results[group].append({"type": rule["name"], "value": val})
        return results

    def _llm_extract(self, text, contact_name=""):
        if not text or len(text.strip()) < 2:
            return {}
        try:
            client = self._get_llm_client()
            if client is None:
                return {}
            fields_desc = "\n".join(f"- {f['name']}: {f['desc']}" for f in self.llm_fields)
            system_prompt = (
                "你是信息提取助手。从微信聊天消息中提取结构化信息。\n"
                "严格按照JSON格式返回，不要输出任何其他内容。\n"
                "如果某个字段无法判断，返回默认值（数字返回1，字符串返回未知，数组返回[]）。\n\n"
                f"需要提取的字段：\n{fields_desc}\n\n"
                "返回格式示例：\n"
                '{"category": "工作", "urgency": 3, "summary": "明天开会", '
                '"action_items": ["确认会议室"], "sentiment": "中性"}'
            )
            user_prompt = f"联系人：{contact_name}\n消息内容：{text}"
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
            reply = client._call_raw(messages, max_tokens=300, temperature=0.1)
            if not reply:
                return {}
            reply = reply.strip()
            if reply.startswith("```"):
                reply = reply.split("\n", 1)[-1] if "\n" in reply else reply[3:]
            if reply.endswith("```"):
                reply = reply[:-3]
            reply = reply.strip()
            json_start = reply.find("{")
            json_end = reply.rfind("}")
            if json_start >= 0 and json_end > json_start:
                json_str = reply[json_start:json_end + 1]
                return json.loads(json_str)
            else:
                logger.warning(f"LLM返回非JSON格式: {reply[:100]}")
                return {}
        except json.JSONDecodeError as e:
            logger.warning(f"LLM提取结果JSON解析失败: {e}")
            return {}
        except Exception as e:
            logger.error(f"LLM提取失败: {e}")
            return {}

    def _get_llm_client(self):
        if self._llm_client is not None:
            return self._llm_client
        if not self.llm_config:
            return None
        api_key = self.llm_config.get("api_key", "")
        if not api_key or api_key in ("", "your-api-key-here"):
            return None
        from llm_client import LLMClient
        self._llm_client = LLMClient(self.llm_config)
        return self._llm_client

    def extract_batch(self, messages, contact_name=""):
        results = []
        for msg in messages:
            ts = msg.get("timestamp")
            if isinstance(ts, str):
                try:
                    ts = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    ts = datetime.now()
            elif ts is None:
                ts = datetime.now()
            result = self.extract(
                text=msg["content"],
                sender=msg.get("sender", "other"),
                contact_name=contact_name,
                timestamp=ts,
            )
            results.append(result)
        return results
