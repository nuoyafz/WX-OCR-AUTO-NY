# -*- coding: utf-8 -*-
"""修复重启后联系人卡片丢失的问题"""
import os

filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ui_app.py')
with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

changes = 0

# ============================================================
# 1. 修复 _load_conversation 使用绝对路径
# ============================================================
old_load_conv = '''    def _load_conversation(self, contact):
        """懒加载某联系人完整消息体（精确匹配，从 db），缓存到 _messages_store[contact]。"""
        if contact in self._messages_store:
            return self._messages_store[contact]
        _st = self._get_storage()
        _msgs = []
        if _st is not None:
            try:
                import sqlite3
                _db = getattr(_st, "db_path", "data/messages.db")
                _conn = sqlite3.connect(_db)'''

new_load_conv = '''    def _load_conversation(self, contact):
        """懒加载某联系人完整消息体（精确匹配，从 db），缓存到 _messages_store[contact]。"""
        if contact in self._messages_store:
            return self._messages_store[contact]
        _st = self._get_storage()
        _msgs = []
        if _st is not None:
            try:
                import sqlite3
                import os as _os
                _db = getattr(_st, "db_path", _app_path("data", "messages.db"))
                if not _os.path.isabs(_db):
                    _db = _app_path(_db)
                self._debug_log(f"[懒加载] 联系人 {contact!r}: db_path={_db}")
                _conn = sqlite3.connect(_db)'''

if old_load_conv in content:
    content = content.replace(old_load_conv, new_load_conv, 1)
    changes += 1
    print("OK: _load_conversation 使用绝对路径")
else:
    print("FAIL: _load_conversation not found")

# ============================================================
# 2. 添加启动诊断日志
# ============================================================
old_load_history_end = '''        self._storage_cache = None     # 自建存储对象缓存（避免 engine 未初始化时删除/查询失效）
        self._load_history()

        # V3.2: 顶部两张系统卡（永远存在，置于最上方）：'''

new_load_history_end = '''        self._storage_cache = None     # 自建存储对象缓存（避免 engine 未初始化时删除/查询失效）
        self._load_history()
        print(f"[启动诊断] _load_history 完成, _conv_index={len(self._conv_index)} 个联系人")

        # V3.2: 顶部两张系统卡（永远存在，置于最上方）：'''

if old_load_history_end in content:
    content = content.replace(old_load_history_end, new_load_history_end, 1)
    changes += 1
    print("OK: 添加 _load_history 诊断日志")
else:
    print("FAIL: _load_history end not found")

# ============================================================
# 3. 在 _rebuild_contact_list 调用处添加诊断
# ============================================================
old_rebuild_call = '''        # 启动渲染：按会话索引把每个联系人渲染为独立左栏卡片。
        # 修复回归：此前 _load_history 只建 _conv_index 却漏调 _rebuild_contact_list，
        # 导致启动后左栏只剩"全部会话"，历史联系人的独立卡片永不出现。
        try:
            self._rebuild_contact_list()
        except Exception:
            pass'''

new_rebuild_call = '''        # 启动渲染：按会话索引把每个联系人渲染为独立左栏卡片。
        # 修复回归：此前 _load_history 只建 _conv_index 却漏调 _rebuild_contact_list，
        # 导致启动后左栏只剩"全部会话"，历史联系人的独立卡片永不出现。
        print(f"[启动诊断] 准备调用 _rebuild_contact_list, _conv_index={len(self._conv_index)} 个联系人")
        try:
            self._rebuild_contact_list()
            print(f"[启动诊断] _rebuild_contact_list 完成, _contact_cards={len(self._contact_cards)} 个")
        except Exception as _e:
            print(f"[启动诊断] _rebuild_contact_list 异常: {_e}")
            import traceback as _tb
            _tb.print_exc()'''

if old_rebuild_call in content:
    content = content.replace(old_rebuild_call, new_rebuild_call, 1)
    changes += 1
    print("OK: 添加 _rebuild_contact_list 诊断日志")
else:
    print("FAIL: _rebuild_contact_list call not found")

# ============================================================
# 4. 在 _rebuild_conv_index 末尾添加诊断日志
# ============================================================
old_conv_index_end = '''                _idx["preview"] = _content
                _idx["last_sender"] = _sender
                _idx["last_time"] = _r["timestamp"] or _idx["last_time"]
        except Exception as _e:
            try:
                self._on_log("warning", f"[会话索引] 构建失败: {_e}")
            except Exception:
                pass'''

new_conv_index_end = '''                _idx["preview"] = _content
                _idx["last_sender"] = _sender
                _idx["last_time"] = _r["timestamp"] or _idx["last_time"]
            # 诊断日志：记录前5个联系人
            try:
                _sample = list(self._conv_index.keys())[:5]
                self._debug_log(f"[会话索引] 构建完成: {len(self._conv_index)} 个联系人 | 前5: {_sample}")
            except Exception:
                pass
        except Exception as _e:
            try:
                self._on_log("warning", f"[会话索引] 构建失败: {_e}")
            except Exception:
                pass'''

if old_conv_index_end in content:
    content = content.replace(old_conv_index_end, new_conv_index_end, 1)
    changes += 1
    print("OK: 添加 _rebuild_conv_index 诊断日志")
else:
    print("FAIL: _rebuild_conv_index end not found")

# 写入
with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)

print(f"\nDONE: {changes} changes applied")
