"""
配置文件修复助手 - 快速修复配置问题
==================================
功能：
1. API Key有效性检查
2. 自动回复配置优化
3. 数据统计配置优化
4. 角色配置增强
5. 配置文件备份
"""
import os
import yaml
import logging
import requests
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class ConfigFixer:
    """配置文件修复器"""

    def __init__(self, config_path="config.yaml"):
        self.config_path = Path(config_path)
        self.backup_path = self.config_path.with_suffix(f".yaml.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}")
        self.config = {}
        self.issues = []
        self.fixes = []

    def load_config(self) -> bool:
        """加载配置文件"""
        try:
            if not self.config_path.exists():
                self.issues.append("❌ 配置文件不存在")
                return False

            with open(self.config_path, 'r', encoding='utf-8') as f:
                self.config = yaml.safe_load(f) or {}

            logger.info(f"✅ 配置文件加载成功: {self.config_path}")
            return True

        except Exception as e:
            self.issues.append(f"❌ 配置文件加载失败: {e}")
            return False

    def backup_config(self) -> bool:
        """备份配置文件"""
        try:
            if self.config_path.exists():
                import shutil
                shutil.copy2(self.config_path, self.backup_path)
                logger.info(f"✅ 配置文件已备份: {self.backup_path}")
                return True
        except Exception as e:
            self.issues.append(f"❌ 备份失败: {e}")
            return False

    def validate_api_key(self) -> bool:
        """验证API Key"""
        try:
            llm_config = self.config.get("llm", {})
            api_key = llm_config.get("api_key", "")

            if not api_key:
                self.issues.append("❌ API Key未配置")
                return False

            if not api_key.startswith("sk-"):
                self.issues.append(f"❌ API Key格式无效: {api_key[:10]}...")
                return False

            # 测试API调用
            base_url = llm_config.get("base_url", "https://dashscope.aliyuncs.com/compatible-mode")
            model = llm_config.get("model", "qwen3.7-flash-2026-07-15")

            logger.info(f"🔍 验证API Key: {api_key[:10]}...")

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
                logger.info("✅ API Key验证通过")
                return True
            else:
                error_msg = response.text[:100] if hasattr(response, 'text') else str(response.status_code)
                self.issues.append(f"❌ API Key验证失败: {error_msg}")
                return False

        except requests.exceptions.Timeout:
            self.issues.append("❌ API Key验证超时")
            return False
        except Exception as e:
            self.issues.append(f"❌ API Key验证异常: {e}")
            return False

    def fix_auto_reply_config(self):
        """修复自动回复配置"""
        try:
            auto_reply = self.config.setdefault("auto_reply", {})

            # 确保基础配置存在
            if "enabled" not in auto_reply:
                auto_reply["enabled"] = False
                self.fixes.append("✅ 添加自动回复开关配置 (默认关闭)")

            if "send_delay" not in auto_reply:
                auto_reply["send_delay"] = 1.0
                self.fixes.append("✅ 添加发送延迟配置 (默认1秒)")

            # 优化发送延迟建议
            current_delay = auto_reply.get("send_delay", 0.5)
            if current_delay < 0.5:
                auto_reply["send_delay"] = 1.0
                self.fixes.append(f"⚠️  发送延迟过短({current_delay}s)，已调整为1.0s避免风控")

            # 添加高级配置
            if "max_retries" not in auto_reply:
                auto_reply["max_retries"] = 3
                self.fixes.append("✅ 添加最大重试次数配置")

            if "retry_delay" not in auto_reply:
                auto_reply["retry_delay"] = 2.0
                self.fixes.append("✅ 添加重试延迟配置")

            if "send_methods" not in auto_reply:
                auto_reply["send_methods"] = ["clipboard", "typing", "offscreen"]
                self.fixes.append("✅ 添加发送方法优先级配置")

            logger.info("✅ 自动回复配置修复完成")

        except Exception as e:
            self.issues.append(f"❌ 自动回复配置修复失败: {e}")

    def enhance_roles_config(self):
        """增强角色配置"""
        try:
            roles = self.config.setdefault("roles", {})

            # 确保有默认角色
            if "tech_expert" not in roles:
                roles["tech_expert"] = {
                    "name": "技术顾问",
                    "reply_style": "专业、简洁",
                    "system_prompt": "你是一个技术顾问，知识渊博。回答问题时简洁专业。回复控制在2-4句话，不要长篇大论。"
                }
                self.fixes.append("✅ 添加技术顾问角色")

            # 添加更多实用角色
            role_templates = {
                "customer_service": {
                    "name": "客服助手",
                    "reply_style": "礼貌、耐心",
                    "system_prompt": "你是一个专业的客服助手，礼貌耐心，善于解答问题。回复要友好有温度，1-3句话即可。"
                },
                "assistant": {
                    "name": "智能助手",
                    "reply_style": "高效、准确",
                    "system_prompt": "你是一个智能助手，高效准确地回答问题。回复简洁明了，控制在2句话内。"
                },
                "friend": {
                    "name": "朋友",
                    "reply_style": "轻松、友好",
                    "system_prompt": "你是一个友好的朋友，说话轻松自然。回复像日常聊天一样，1-2句话即可。"
                }
            }

            for role_key, role_config in role_templates.items():
                if role_key not in roles:
                    roles[role_key] = role_config
                    self.fixes.append(f"✅ 添加{role_config['name']}角色")

            logger.info("✅ 角色配置增强完成")

        except Exception as e:
            self.issues.append(f"❌ 角色配置增强失败: {e}")

    def fix_statistics_config(self):
        """修复统计配置"""
        try:
            # 确保存储配置正确
            storage = self.config.setdefault("storage", {})

            if "type" not in storage:
                storage["type"] = "sqlite"
                self.fixes.append("✅ 设置存储类型为SQLite")

            if "db_path" not in storage:
                storage["db_path"] = "data/messages.db"
                self.fixes.append("✅ 添加数据库路径配置")

            # 确保报告配置存在
            report = self.config.setdefault("report", {})

            if "auto_daily" not in report:
                report["auto_daily"] = True
                self.fixes.append("✅ 启用自动日报")

            if "daily_time" not in report:
                report["daily_time"] = "23:00"
                self.fixes.append("✅ 设置日报时间")

            logger.info("✅ 统计配置修复完成")

        except Exception as e:
            self.issues.append(f"❌ 统计配置修复失败: {e}")

    def fix_llm_config(self):
        """修复LLM配置"""
        try:
            llm = self.config.setdefault("llm", {})

            # 确保基础配置存在
            if "model" not in llm:
                llm["model"] = "qwen3.7-flash-2026-07-15"
                self.fixes.append("✅ 设置默认模型")

            if "temperature" not in llm:
                llm["temperature"] = 0.3
                self.fixes.append("✅ 设置温度参数")

            if "max_tokens" not in llm:
                llm["max_tokens"] = 500
                self.fixes.append("✅ 设置最大token数")

            if "provider" not in llm:
                llm["provider"] = "custom"
                self.fixes.append("✅ 设置提供商")

            logger.info("✅ LLM配置修复完成")

        except Exception as e:
            self.issues.append(f"❌ LLM配置修复失败: {e}")

    def save_config(self) -> bool:
        """保存配置文件"""
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                yaml.dump(self.config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

            logger.info(f"✅ 配置文件已保存: {self.config_path}")
            return True

        except Exception as e:
            self.issues.append(f"❌ 配置文件保存失败: {e}")
            return False

    def auto_fix(self) -> dict:
        """自动修复所有配置问题"""
        logger.info("="*60)
        logger.info("        开始配置文件自动修复")
        logger.info("="*60)

        results = {
            "success": False,
            "issues": [],
            "fixes": [],
            "api_key_valid": False
        }

        # 1. 加载配置
        if not self.load_config():
            results["issues"] = self.issues
            return results

        # 2. 备份配置
        self.backup_config()

        # 3. 验证API Key
        api_key_valid = self.validate_api_key()
        results["api_key_valid"] = api_key_valid

        if not api_key_valid:
            logger.warning("⚠️  API Key无效，自动回复功能可能无法使用")
            logger.info("💡 请在config.yaml中配置有效的API Key")

        # 4. 修复各项配置
        self.fix_auto_reply_config()
        self.fix_llm_config()
        self.enhance_roles_config()
        self.fix_statistics_config()

        # 5. 保存配置
        if self.save_config():
            results["success"] = True

        results["issues"] = self.issues
        results["fixes"] = self.fixes

        # 6. 打印报告
        self.print_report(results)

        return results

    def print_report(self, results):
        """打印修复报告"""
        print("\n" + "="*60)
        print("         配置修复报告")
        print("="*60)

        # API Key状态
        print(f"\n🔑 API Key状态:")
        if results["api_key_valid"]:
            print("   ✅ 有效 - 自动回复功能正常")
        else:
            print("   ❌ 无效 - 需要配置有效的API Key")
            print("   💡 获取方式: 访问 https://dashscope.aliyuncs.com/")

        # 问题列表
        if self.issues:
            print(f"\n⚠️  发现的问题 ({len(self.issues)}):")
            for issue in self.issues:
                print(f"   {issue}")
        else:
            print("\n✅ 未发现问题")

        # 修复列表
        if self.fixes:
            print(f"\n🔧 已应用的修复 ({len(self.fixes)}):")
            for fix in self.fixes:
                print(f"   {fix}")
        else:
            print("\nℹ️  无需修复")

        # 总体状态
        print(f"\n📊 总体状态:")
        if results["success"]:
            print("   ✅ 配置修复完成")
        else:
            print("   ❌ 配置修复失败")

        # 后续建议
        print(f"\n💡 后续建议:")
        if not results["api_key_valid"]:
            print("   1. 配置有效的API Key以启用自动回复")
        print("   2. 根据需要调整角色配置")
        print("   3. 在UI中测试各项功能")
        print("   4. 查看日志了解详细运行情况")

        print("\n" + "="*60)


def main():
    """命令行入口"""
    import argparse

    parser = argparse.ArgumentParser(description="配置文件修复助手")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    parser.add_argument("--validate-only", action="store_true", help="仅验证不修复")

    args = parser.parse_args()

    # 设置日志
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    try:
        fixer = ConfigFixer(args.config)

        if args.validate_only:
            # 仅验证
            print("🔍 仅验证模式...")
            if fixer.load_config():
                api_key_valid = fixer.validate_api_key()
                print(f"\nAPI Key状态: {'✅ 有效' if api_key_valid else '❌ 无效'}")
            else:
                print("❌ 配置文件加载失败")
        else:
            # 自动修复
            results = fixer.auto_fix()

            if not results["success"]:
                print("\n❌ 修复失败，请检查错误信息")
                return 1

        return 0

    except Exception as e:
        logger.error(f"执行失败: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(main())