"""
角色管理模块 — 读取配置，管理角色定义和联系人映射
"""
import yaml
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class RoleManager:
    """管理角色定义和联系人→角色映射"""

    def __init__(self, config_path="config.yaml"):
        self.config_path = Path(config_path)
        self.config = {}
        self.roles = {}
        self.contacts = {}
        self.default_role = "tech_expert"
        self._load_config()

    def _load_config(self):
        """加载配置文件"""
        if not self.config_path.exists():
            logger.warning(f"配置文件不存在: {self.config_path}，使用默认配置")
            self._use_defaults()
            return

        with open(self.config_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f) or {}

        self.roles = self.config.get("roles") or {}
        self.contacts = self.config.get("contacts") or {}
        self.default_role = self.config.get("default_role") or "tech_expert"

        logger.info(f"已加载 {len(self.roles)} 个角色, {len(self.contacts)} 个联系人映射")

    def _use_defaults(self):
        """使用硬编码默认配置"""
        self.roles = {
            "tech_expert": {
                "name": "技术顾问",
                "system_prompt": "你是一个技术顾问，回答简洁专业，喜欢用emoji。回复控制在2-4句话。",
                "reply_style": "专业、简洁",
            },
        }
        self.contacts = {}

    def get_role_for(self, sender_name):
        """
        根据发送者名称获取角色配置。

        Args:
            sender_name: 发送者名称（微信昵称）

        Returns:
            dict: {"name": ..., "system_prompt": ..., "reply_style": ...}
        """
        # 精确匹配
        role_key = self.contacts.get(sender_name)

        # 模糊匹配（发送者名称包含关键词）
        if role_key is None:
            for contact, rk in self.contacts.items():
                if contact in sender_name or sender_name in contact:
                    role_key = rk
                    break

        # 使用默认角色
        if role_key is None:
            role_key = self.default_role

        role = self.roles.get(role_key)
        if role is None:
            logger.warning(f"角色 '{role_key}' 不存在，使用第一个可用角色")
            role = next(iter(self.roles.values()), {
                "name": "默认",
                "system_prompt": "你是一个友好的聊天助手。",
                "reply_style": "自然",
            })

        return role

    def get_llm_config(self):
        """获取LLM配置"""
        return self.config.get("llm", {})

    def get_wechat_config(self):
        """获取微信监听配置"""
        return self.config.get("wechat", {})

    def get_logging_config(self):
        """获取日志配置"""
        return self.config.get("logging", {})