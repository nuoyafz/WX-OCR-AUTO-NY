
# -*- coding: utf-8 -*-
import os

BASE = os.path.dirname(os.path.abspath(__file__))

main_path = os.path.join(BASE, "main.py")
ui_path = os.path.join(BASE, "ui_app.py")

# ==================== 修复 main.py ====================
print("=== 修复 main.py ===")

with open(main_path, "r", encoding="utf-8") as f:
    main_content = f.read()

# 修复1: 移除阻塞式 join()
old1 = '        if self._thread and self._thread.is_alive():\n            self._thread.join(timeout=3)\n\n        if self.storage'
new1 = '        if self.storage'
if old1 in main_content:
    main_content = main_content.replace(old1, new1)
    print("  [OK] 修复1: 移除阻塞式 join()")
else:
    print("  [跳过] 修复1: join 代码块未找到（可能已修复）")

# 修复2: 移除硬编码黑名单
old2 = '            # 默认黑名单：服务号、订阅号等\n            default_blacklist = ["拼多多", "瑞幸咖啡", "美团", "饿了么", "滴滴", "抖音", "快手", "京东", "淘宝", "天猫"]\n            all_blacklist = list(set(blacklist + default_blacklist))'
new2 = '            all_blacklist = list(blacklist)'
if old2 in main_content:
    main_content = main_content.replace(old2, new2)
    print("  [OK] 修复2: 使用配置文件中的完整黑名单")
else:
    print("  [跳过] 修复2: 黑名单代码块未找到（可能已修复）")

with open(main_path, "w", encoding="utf-8") as f:
    f.write(main_content)
print("  main.py 已保存")

# ==================== 修复 ui_app.py ====================
print("\n=== 修复 ui_app.py ===")

with open(ui_path, "r", encoding="utf-8") as f:
    ui_content = f.read()

# 修复3: 非阻塞停止按钮
old3 = '    def stop_monitoring(self):\n        if self.engine:\n            self.engine.stop()\n            self.engine = None\n        self.btn_start.configure(state="normal")\n        self.btn_stop.configure(state="disabled")'
new3 = '    def stop_monitoring(self):\n        if self.engine:\n            eng = self.engine\n            self.engine = None\n            threading.Thread(target=eng.stop, daemon=True).start()\n        self.btn_start.configure(state="normal")\n        self.btn_stop.configure(state="disabled")\n        self._on_log("info", "已发送停止信号")\n\n    def _toggle_monitoring(self):\n        if self.engine and self.engine.is_running():\n            self.stop_monitoring()\n        else:\n            self.start_monitoring()'
if old3 in ui_content:
    ui_content = ui_content.replace(old3, new3)
    print("  [OK] 修复3: 停止按钮改为非阻塞 + 添加 _toggle_monitoring")
else:
    print("  [跳过] 修复3: stop_monitoring 未找到")

# 修复4: 添加 Ctrl+T 快捷键
old4 = '        self.after(100, self._poll_capture)'
new4 = '        self.after(100, self._poll_capture)\n\n        # 快捷键 Ctrl+T 切换开始/停止\n        self.bind("<Control-t>", lambda e: self._toggle_monitoring())\n        self.bind("<Control-T>", lambda e: self._toggle_monitoring())'
if old4 in ui_content:
    ui_content = ui_content.replace(old4, new4)
    print("  [OK] 修复4: 添加 Ctrl+T 快捷键")
else:
    print("  [跳过] 修复4: 快捷键插入点未找到")

with open(ui_path, "w", encoding="utf-8") as f:
    f.write(ui_content)
print("  ui_app.py 已保存")

print("\n===== 全部完成！重启程序即可生效 =====")
input("按回车键退出...")
