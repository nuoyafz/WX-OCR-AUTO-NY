# -*- coding: utf-8 -*-
"""性能优化补丁 - 解决切换卡顿问题"""
import os

filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ui_app.py')
with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

changes = 0

# ============================================================
# 优化1: 在 _rebuild_message_list 中添加增量更新判断
# ============================================================
old_rebuild_start = '''    def _rebuild_message_list(self):
        """V3: 按当前选中会话重建气泡列表，最新消息在底部（微信风格）。
        - active == _contact_filter_all 或 None → 合并所有会话消息，按 _seq 正序（旧→新）。
        - active == 某个 contact → 只取该会话的消息，倒序排列后 pack。
        """
        try:
            # 清空：先 destroy 所有子节点
            for child in list(self.msg_list_frame_inner.winfo_children()):'''

new_rebuild_start = '''    def _rebuild_message_list(self, force=False):
        """V3: 按当前选中会话重建气泡列表，最新消息在底部（微信风格）。
        - active == _contact_filter_all 或 None → 合并所有会话消息，按 _seq 正序（旧→新）。
        - active == 某个 contact → 只取该会话的消息，倒序排列后 pack。
        - force=True 时强制全量重建，否则尝试增量更新。
        """
        try:
            active = self._active_contact
            is_all = (active == self._contact_filter_all)
            is_usage = (active == self._contact_filter_usage)
            if active is None:
                is_all = True

            # 获取当前会话的消息
            if is_all:
                items = self._get_all_view_messages()
            elif is_usage:
                items = []
            else:
                items = list(reversed(self._load_conversation(active)))

            # 性能：限制渲染条数
            _MAX = 100
            total_items = len(items)
            if total_items > _MAX:
                items = items[:_MAX]

            # 检查是否可以增量更新（会话未变且不是强制重建）
            current_key = f"{active}_{len(items)}"
            if not force and hasattr(self, '_last_rendered_key') and self._last_rendered_key == current_key:
                # 会话和消息数量都没变，跳过重建
                return

            # 清空：先 destroy 所有子节点
            for child in list(self.msg_list_frame_inner.winfo_children()):'''

if old_rebuild_start in content:
    content = content.replace(old_rebuild_start, new_rebuild_start, 1)
    changes += 1
    print("OK: 添加增量更新判断逻辑")
else:
    print("FAIL: _rebuild_message_list start not found")

# ============================================================
# 优化2: 在 _rebuild_message_list 末尾添加状态记录
# ============================================================
old_rebuild_end = '''            # 关键修复：强制刷新布局 + 重算 CTkScrollableFrame 的 canvas scrollregion，
            # 否则从"全部会话"切到单卡时，旧内容被销毁但视口不刷新，表现成"空白"。
            try:
                self.msg_list_frame.update_idletasks()
                _canvas = getattr(self.msg_list_frame, "_parent_canvas", None)
                if _canvas is not None:
                    _canvas.configure(scrollregion=_canvas.bbox("all"))
                    _canvas.yview_moveto(1.0)
            except Exception:
                pass

        except Exception:
            import traceback as _tb
            try:
                _msg = _tb.format_exc()
                logger.warning("[重建消息列表] 异常: %s", _msg[-500:])
                self._debug_log("[重建消息列表] 异常:\\n" + _msg)'''

new_rebuild_end = '''            # 关键修复：强制刷新布局
            try:
                self.msg_list_frame.update_idletasks()
                _canvas = getattr(self.msg_list_frame, "_parent_canvas", None)
                if _canvas is not None:
                    _canvas.configure(scrollregion=_canvas.bbox("all"))
                    _canvas.yview_moveto(1.0)
            except Exception:
                pass

            # 记录当前渲染状态
            self._last_rendered_key = current_key

        except Exception:
            import traceback as _tb
            try:
                _msg = _tb.format_exc()
                logger.warning("[重建消息列表] 异常: %s", _msg[-500:])
                self._debug_log("[重建消息列表] 异常:\\n" + _msg)'''

if old_rebuild_end in content:
    content = content.replace(old_rebuild_end, new_rebuild_end, 1)
    changes += 1
    print("OK: 添加状态记录逻辑")
else:
    print("FAIL: _rebuild_message_list end not found")

# ============================================================
# 优化3: 在空状态分支也添加状态记录
# ============================================================
old_empty_state = '''                ctk.CTkLabel(
                    self.msg_empty_label, text=txt,
                    font=ctk.CTkFont(family=FONT_FAMILY, size=12), text_color=WC_COLORS["text_muted"],
                    wraplength=480, justify="center",
                ).pack()
                return

            # 微信风格：最新在顶部，逐条 pack'''

new_empty_state = '''                ctk.CTkLabel(
                    self.msg_empty_label, text=txt,
                    font=ctk.CTkFont(family=FONT_FAMILY, size=12), text_color=WC_COLORS["text_muted"],
                    wraplength=480, justify="center",
                ).pack()
                self._last_rendered_key = current_key
                return

            # 微信风格：最新在顶部，逐条 pack'''

if old_empty_state in content:
    content = content.replace(old_empty_state, new_empty_state, 1)
    changes += 1
    print("OK: 空状态分支添加状态记录")
else:
    print("FAIL: empty state not found")

# ============================================================
# 优化4: 修改 _set_active_contact 使用 force=True
# ============================================================
old_set_active = '''        # 切换会话 → 重建消息列表（最新在顶）
        try:
            self._rebuild_message_list()'''

new_set_active = '''        # 切换会话 → 重建消息列表（最新在顶），强制重建以清除旧内容
        try:
            self._rebuild_message_list(force=True)'''

if old_set_active in content:
    content = content.replace(old_set_active, new_set_active, 1)
    changes += 1
    print("OK: _set_active_contact 使用 force=True")
else:
    print("FAIL: _set_active_contact not found")

# 写入
with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)

print(f"\nDONE: {changes} changes applied")
