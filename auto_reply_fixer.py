"""
自动回复修复模块 - 修复自动回复失效问题
============================================
修复内容：
1. LLM客户端初始化失败检测
2. API Key有效性验证
3. 发送机制容错处理
4. 多种发送模式自动切换
5. 自动回复日志增强
"""
import time
import logging
import requests
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)


class AutoReplyFixer:
    """自动回复修复器"""

    def __init__(self, llm_config, auto_reply_config):
        self.llm_config = llm_config or {}
        self.auto_reply_config = auto_reply_config or {}
        self.llm_client = None
        self.api_key_valid = False
        self.send_methods = ["clipboard", "typing", "offscreen"]
        self.current_send_method = 0
        self.send_failures = {}  # 记录各方法失败次数

    def validate_api_key(self) -> bool:
        """验证API Key是否有效"""
        try:
            api_key = self.llm_config.get("api_key", "")
            if not api_key or not api_key.startswith("sk-"):
                logger.error("[自动回复修复] API Key格式无效")
                return False

            # 测试API调用
            base_url = self.llm_config.get("base_url", "https://dashscope.aliyuncs.com/compatible-mode")
            model = self.llm_config.get("model", "qwen3.7-flash-2026-07-15")

            response = requests.post(
                f"{base_url.rstrip('/')}/v1/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "test"}],
                    "max_tokens": 10
                },
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                },
                timeout=10
            )

            if response.status_code == 200:
                logger.info("[自动回复修复] API Key验证通过")
                self.api_key_valid = True
                return True
            else:
                logger.error(f"[自动回复修复] API Key验证失败: {response.status_code} - {response.text[:100]}")
                return False

        except Exception as e:
            logger.error(f"[自动回复修复] API Key验证异常: {e}")
            return False

    def init_llm_client(self) -> bool:
        """初始化LLM客户端"""
        try:
            if not self.api_key_valid and not self.validate_api_key():
                return False

            from llm_client import LLMClient
            self.llm_client = LLMClient(self.llm_config)

            # 测试调用
            test_reply = self.llm_client.generate_reply(
                "测试", "你好",
                {"name": "测试", "system_prompt": "你是一个测试助手", "reply_style": "简洁"},
                []
            )

            if test_reply:
                logger.info(f"[自动回复修复] LLM客户端初始化成功，测试回复: {test_reply[:20]}...")
                return True
            else:
                logger.error("[自动回复修复] LLM客户端测试调用失败")
                return False

        except Exception as e:
            logger.error(f"[自动回复修复] LLM客户端初始化异常: {e}")
            return False

    def smart_send_reply(self, window, reply: str) -> bool:
        """智能发送回复 - 自动切换发送模式"""
        if not reply or not reply.strip():
            logger.warning("[自动回复修复] 回复内容为空，跳过发送")
            return False

        methods_tried = []

        # 尝试所有发送方法
        for i, method in enumerate(self.send_methods):
            method_index = (self.current_send_method + i) % len(self.send_methods)
            current_method = self.send_methods[method_index]
            methods_tried.append(current_method)

            logger.info(f"[自动回复修复] 尝试发送方法 {i+1}/{len(self.send_methods)}: {current_method}")

            try:
                success = self._send_by_method(window, reply, current_method)

                if success:
                    # 成功后，优先使用此方法
                    self.current_send_method = method_index
                    self.send_failures[current_method] = 0
                    logger.info(f"[自动回复修复] 发送成功，使用方法: {current_method}")
                    return True
                else:
                    # 记录失败次数
                    self.send_failures[current_method] = self.send_failures.get(current_method, 0) + 1
                    logger.warning(f"[自动回复修复] 方法 {current_method} 失败 ({self.send_failures[current_method]}次)")

            except Exception as e:
                self.send_failures[current_method] = self.send_failures.get(current_method, 0) + 1
                logger.error(f"[自动回复修复] 方法 {current_method} 异常: {e}")

        logger.error(f"[自动回复修复] 所有发送方法均失败，已尝试: {methods_tried}")
        return False

    def _send_by_method(self, window, reply: str, method: str) -> bool:
        """按指定方法发送"""
        if method == "clipboard":
            return self._send_by_clipboard(window, reply)
        elif method == "typing":
            return self._send_by_typing(window, reply)
        elif method == "offscreen":
            return self._send_offscreen(window, reply)
        else:
            return False

    def _send_by_clipboard(self, window, reply: str) -> bool:
        """剪贴板方式发送"""
        try:
            from sender import send_text
            return send_text(window, reply)
        except Exception as e:
            logger.error(f"[剪贴板发送] 失败: {e}")
            return False

    def _send_by_typing(self, window, reply: str) -> bool:
        """打字方式发送（更慢但更可靠）"""
        try:
            from sender import send_text_type_mode
            return send_text_type_mode(window, reply)
        except Exception as e:
            logger.error(f"[打字发送] 失败: {e}")
            return False

    def _send_offscreen(self, window, reply: str) -> bool:
        """屏幕外发送"""
        try:
            from sender import send_text_offscreen
            return send_text_offscreen(window, reply)
        except Exception as e:
            logger.error(f"[屏幕外发送] 失败: {e}")
            return False

    def enhanced_auto_reply(self, contact: str, content: str, sender: str,
                           context: Optional[List] = None, window=None) -> Optional[str]:
        """增强版自动回复"""
        try:
            # 1. 检查基本条件
            if sender == "me":
                logger.debug("[自动回复修复] 自己的消息，不回复")
                return None

            if not self.llm_client and not self.init_llm_client():
                logger.error("[自动回复修复] LLM客户端不可用")
                return None

            # 2. 获取角色配置
            from role_manager import RoleManager
            role_manager = RoleManager()
            role = role_manager.get_role_for(contact)

            logger.info(f"[自动回复修复] 联系人: {contact}, 角色: {role['name']}, 风格: {role['reply_style']}")

            # 3. 生成回复
            context = context or []
            reply = self.llm_client.generate_reply(contact, content, role, context)

            if not reply:
                logger.warning("[自动回复修复] LLM未生成回复")
                return None

            logger.info(f"[自动回复修复] 生成回复: {reply[:80]}...")

            # 4. 发送延迟
            send_delay = self.auto_reply_config.get("send_delay", 0.5)
            time.sleep(send_delay)

            # 5. 智能发送
            if window:
                success = self.smart_send_reply(window, reply)
                if success:
                    return reply
                else:
                    logger.error("[自动回复修复] 发送失败")
                    return None
            else:
                logger.warning("[自动回复修复] 窗口不可用，无法发送")
                return reply  # 返回回复但不发送

        except Exception as e:
            logger.error(f"[自动回复修复] 自动回复异常: {e}")
            import traceback
            logger.error(traceback.format_exc()[-200:])
            return None

    def get_diagnostic_info(self) -> Dict:
        """获取诊断信息"""
        return {
            "api_key_valid": self.api_key_valid,
            "llm_client_initialized": self.llm_client is not None,
            "available_send_methods": self.send_methods,
            "current_send_method": self.send_methods[self.current_send_method],
            "send_failures": self.send_failures,
            "total_failures": sum(self.send_failures.values()),
        }


# ============================================================
# 集成到 WeChatEngine 的补丁
# ============================================================

def patch_wechat_engine_auto_reply(engine):
    """为WeChatEngine添加自动回复修复"""
    if not hasattr(engine, 'auto_reply_fixer'):
        engine.auto_reply_fixer = AutoReplyFixer(
            engine.llm_config,
            engine.auto_reply_config
        )

        # 替换原有的 _maybe_auto_reply 方法
        original_method = engine._maybe_auto_reply

        def fixed_auto_reply(contact, content, sender, context=None):
            logger.info(f"[自动回复修复] 收到消息 - 联系人: {contact}, 发送者: {sender}, 内容: {content[:30]}...")

            # 使用修复后的逻辑
            reply = engine.auto_reply_fixer.enhanced_auto_reply(
                contact, content, sender, context, engine.window
            )

            if reply:
                engine.stats["replies_sent"] += 1
                engine.parser.add_to_context("assistant", reply)
                engine.parser.mark_reply_sent(reply)
                engine._log("info", f"[已回复] {reply[:50]}")
                engine._on_reply(contact, reply)
                return True

            return False

        engine._maybe_auto_reply = fixed_auto_reply
        logger.info("[自动回复修复] 已集成到WeChatEngine")