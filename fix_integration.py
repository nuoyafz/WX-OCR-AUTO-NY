"""
功能修复集成工具 - 一键修复自动回复和数据统计问题
==================================================
使用方法：
1. 在 main.py 中导入此模块
2. 在 WeChatEngine 初始化后调用 apply_fixes(engine)
"""
import logging

logger = logging.getLogger(__name__)


def apply_fixes(engine):
    """应用所有修复"""
    logger.info("========== 开始应用功能修复 ==========")

    # 1. 修复自动回复
    try:
        from auto_reply_fixer import patch_wechat_engine_auto_reply
        patch_wechat_engine_auto_reply(engine)
        logger.info("✅ 自动回复修复已应用")
    except Exception as e:
        logger.error(f"❌ 自动回复修复失败: {e}")

    # 2. 修复数据统计
    try:
        from stats_fixer import patch_storage_with_stats
        engine.enhanced_stats = patch_storage_with_stats(engine.storage)
        logger.info("✅ 数据统计修复已应用")
    except Exception as e:
        logger.error(f"❌ 数据统计修复失败: {e}")

    logger.info("========== 功能修复完成 ==========")

    return engine


def create_diagnostic_report(engine) -> dict:
    """创建诊断报告"""
    report = {
        "timestamp": __import__('datetime').datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "auto_reply": {},
        "statistics": {},
        "system": {}
    }

    # 自动回复诊断
    if hasattr(engine, 'auto_reply_fixer'):
        report["auto_reply"] = engine.auto_reply_fixer.get_diagnostic_info()
        report["auto_reply"]["status"] = "已修复"
    else:
        report["auto_reply"]["status"] = "未修复"
        report["auto_reply"]["error"] = "auto_reply_fixer 未初始化"

    # 统计系统诊断
    if hasattr(engine, 'enhanced_stats'):
        try:
            stats = engine.enhanced_stats.get_cached_stats(force_refresh=True)
            report["statistics"]["status"] = "已修复"
            report["statistics"]["cache_status"] = "正常" if stats else "异常"
            report["statistics"]["total_messages"] = stats.get("total_messages", 0)
        except Exception as e:
            report["statistics"]["status"] = "修复但异常"
            report["statistics"]["error"] = str(e)
    else:
        report["statistics"]["status"] = "未修复"
        report["statistics"]["error"] = "enhanced_stats 未初始化"

    # 系统状态
    report["system"] = {
        "auto_reply_enabled": engine.auto_reply_enabled,
        "llm_client_available": engine.llm_client is not None,
        "storage_available": engine.storage is not None,
        "storage_type": engine.storage.storage_type if engine.storage else "未知",
    }

    return report


def print_diagnostic_report(report):
    """打印诊断报告"""
    print("\n" + "="*60)
    print("         功能诊断报告")
    print("="*60)

    print(f"\n⏰ 生成时间: {report['timestamp']}")

    # 自动回复状态
    print("\n🤖 自动回复功能:")
    ar_status = report["auto_reply"].get("status", "未知")
    if ar_status == "已修复":
        print(f"   状态: ✅ {ar_status}")
        print(f"   API Key有效: {'✅' if report['auto_reply'].get('api_key_valid') else '❌'}")
        print(f"   LLM客户端: {'✅' if report['auto_reply'].get('llm_client_initialized') else '❌'}")
        print(f"   当前发送方法: {report['auto_reply'].get('current_send_method', '未知')}")
        print(f"   总失败次数: {report['auto_reply'].get('total_failures', 0)}")
    else:
        print(f"   状态: ❌ {ar_status}")
        if "error" in report["auto_reply"]:
            print(f"   错误: {report['auto_reply']['error']}")

    # 统计系统状态
    print("\n📊 数据统计功能:")
    stats_status = report["statistics"].get("status", "未知")
    if stats_status == "已修复":
        print(f"   状态: ✅ {stats_status}")
        print(f"   缓存状态: {'✅' if report['statistics'].get('cache_status') == '正常' else '❌'}")
        print(f"   总消息数: {report['statistics'].get('total_messages', 0)}")
    else:
        print(f"   状态: ❌ {stats_status}")
        if "error" in report["statistics"]:
            print(f"   错误: {report['statistics']['error']}")

    # 系统状态
    print("\n⚙️  系统状态:")
    sys_status = report["system"]
    print(f"   自动回复开关: {'✅ 开启' if sys_status.get('auto_reply_enabled') else '❌ 关闭'}")
    print(f"   LLM客户端: {'✅ 可用' if sys_status.get('llm_client_available') else '❌ 不可用'}")
    print(f"   存储模块: {'✅ 可用' if sys_status.get('storage_available') else '❌ 不可用'}")
    print(f"   存储类型: {sys_status.get('storage_type', '未知')}")

    print("\n" + "="*60)


# ============================================================
# UI 集成 - 在UI中添加诊断和修复按钮
# ============================================================

def add_diagnostic_ui(ui_app):
    """在UI中添加诊断和修复功能"""
    import customtkinter as ctk

    # 创建诊断按钮
    diagnostic_btn = ctk.CTkButton(
        ui_app.settings_tab,
        text="🔍 功能诊断",
        command=lambda: run_diagnostic_ui(ui_app)
    )
    diagnostic_btn.pack(pady=5)

    # 创建修复按钮
    fix_btn = ctk.CTkButton(
        ui_app.settings_tab,
        text="🔧 应用修复",
        command=lambda: apply_fixes_ui(ui_app)
    )
    fix_btn.pack(pady=5)

    logger.info("[UI] 已添加诊断和修复按钮")


def run_diagnostic_ui(ui_app):
    """运行UI诊断"""
    try:
        if hasattr(ui_app, 'engine') and ui_app.engine:
            report = create_diagnostic_report(ui_app.engine)
            print_diagnostic_report(report)

            # 在UI中显示结果
            result_text = f"""
🔍 诊断报告已生成

🤖 自动回复: {report['auto_reply'].get('status', '未知')}
📊 数据统计: {report['statistics'].get('status', '未知')}

详细信息请查看控制台输出
            """
            ui_app._on_log("info", result_text.strip())
        else:
            ui_app._on_log("error", "引擎未初始化，无法运行诊断")
    except Exception as e:
        ui_app._on_log("error", f"诊断失败: {e}")


def apply_fixes_ui(ui_app):
    """在UI中应用修复"""
    try:
        if hasattr(ui_app, 'engine') and ui_app.engine:
            ui_app._on_log("info", "开始应用修复...")
            apply_fixes(ui_app.engine)
            ui_app._on_log("info", "✅ 修复应用完成！")

            # 运行诊断验证
            report = create_diagnostic_report(ui_app.engine)
            print_diagnostic_report(report)
        else:
            ui_app._on_log("error", "引擎未初始化，无法应用修复")
    except Exception as e:
        ui_app._on_log("error", f"修复应用失败: {e}")


# ============================================================
# 命令行工具
# ============================================================

def main():
    """命令行入口"""
    import argparse

    parser = argparse.ArgumentParser(description="微信AI助手功能修复工具")
    parser.add_argument("--diagnose", action="store_true", help="运行诊断")
    parser.add_argument("--fix", action="store_true", help="应用修复")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")

    args = parser.parse_args()

    # 设置日志
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    try:
        # 导入引擎
        from main import WeChatEngine

        # 创建引擎实例（不启动）
        engine = WeChatEngine(args.config)

        if args.diagnose:
            print("运行诊断...")
            report = create_diagnostic_report(engine)
            print_diagnostic_report(report)

        if args.fix:
            print("应用修复...")
            apply_fixes(engine)
            print("修复完成！")
            report = create_diagnostic_report(engine)
            print_diagnostic_report(report)

        if not args.diagnose and not args.fix:
            print("请指定 --diagnose 或 --fix 参数")
            parser.print_help()

    except Exception as e:
        logger.error(f"执行失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()