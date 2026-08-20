# 功能修复使用指南

## 🚀 快速开始

### 1. 一键修复（推荐）

```bash
# 运行修复工具
python fix_integration.py --fix

# 运行诊断
python fix_integration.py --diagnose
```

### 2. 配置文件修复

```bash
# 自动修复配置问题
python config_fixer.py

# 仅验证配置
python config_fixer.py --validate-only
```

---

## 🔧 功能修复详解

### 🤖 自动回复功能修复

#### **问题诊断**
- ❌ LLM客户端初始化失败
- ❌ API Key配置无效
- ❌ 发送机制不稳定
- ❌ 红点路径无回复逻辑

#### **修复方案**

1. **API Key验证**
   ```python
   # 自动验证API Key有效性
   fixer.validate_api_key()
   ```

2. **LLM客户端修复**
   - 增加初始化失败检测
   - 添加测试调用验证
   - 提供详细错误日志

3. **智能发送机制**
   - 多种发送模式自动切换：
     - 剪贴板模式（快速）
     - 打字模式（可靠）
     - 屏幕外模式（后台）
   - 失败自动重试
   - 发送状态跟踪

4. **增强自动回复逻辑**
   - 支持红点路径回复
   - 增加回复前验证
   - 优化上下文处理

#### **使用方法**

```python
# 在 main.py 中集成
from fix_integration import apply_fixes

# 在 WeChatEngine 初始化后
engine = WeChatEngine()
apply_fixes(engine)  # 应用所有修复
```

---

### 📊 数据统计功能修复

#### **问题诊断**
- ❌ 统计数据不实时更新
- ❌ 统计查询失败无容错
- ❌ 统计维度单一
- ❌ UI显示不友好

#### **修复方案**

1. **实时统计更新**
   - 自动缓存机制（30秒TTL）
   - 数据变更自动失效缓存
   - 异步统计计算

2. **多维度统计**
   - 基础统计：总消息、重要消息、联系人数
   - 时间统计：今日、本周、24小时分布
   - 发送者统计：自己/对方分布
   - 关键词统计：Top 20热门关键词
   - 情绪统计：积极/中性/消极分布
   - 分类统计：工作/生活/广告等

3. **增强UI显示**
   - 实时数据刷新
   - 多标签页展示
   - 可视化图表支持

4. **容错处理**
   - 统计失败返回缓存数据
   - 数据库异常自动降级
   - 详细的错误日志

#### **使用方法**

```python
# 获取增强统计
from stats_fixer import patch_storage_with_stats

# 应用到存储模块
enhanced_stats = patch_storage_with_stats(engine.storage)

# 获取统计数据
stats = enhanced_stats.get_cached_stats()
print(f"总消息数: {stats['total_messages']}")
print(f"今日消息: {stats['today_messages']}")

# 获取热门关键词
trending = enhanced_stats.get_trending_keywords(hours=24, top_n=10)

# 获取联系人时间线
timeline = enhanced_stats.get_contact_timeline("张三", days=7)
```

---

## 🎯 功能多样性优化

### 🤖 自动回复功能多样性

#### **1. 多角色支持**

```yaml
# config.yaml
roles:
  tech_expert:
    name: 技术顾问
    reply_style: 专业、简洁
    system_prompt: 你是一个技术顾问，回答简洁专业...

  customer_service:
    name: 客服助手
    reply_style: 礼貌、耐心
    system_prompt: 你是一个专业的客服助手，礼貌耐心...

  assistant:
    name: 智能助手
    reply_style: 高效、准确
    system_prompt: 你是一个智能助手，高效准确...

  friend:
    name: 朋友
    reply_style: 轻松、友好
    system_prompt: 你是一个友好的朋友，说话轻松自然...
```

#### **2. 智能回复策略**

```python
# 回复模式配置
auto_reply:
  enabled: true
  send_delay: 1.0  # 发送延迟（秒）
  max_retries: 3   # 最大重试次数
  retry_delay: 2.0 # 重试延迟（秒）
  send_methods:    # 发送方法优先级
    - clipboard    # 剪贴板（快速）
    - typing       # 打字（可靠）
    - offscreen    # 屏幕（后台）
```

#### **3. 上下文感知回复**

- 自动维护最近3条对话历史
- 根据联系人选择不同角色
- 支持群聊和私聊不同回复策略

#### **4. 回复质量控制**

- 长度限制：1-3句话
- 内容过滤：避免重复和无意义回复
- 发送频率控制：避免触发风控

---

### 📊 数据统计功能多样性

#### **1. 实时监控面板**

```python
# 六大实时指标
- 截图帧数 (frames_captured)
- OCR调用次数 (ocr_calls)
- 消息检测数 (messages_detected)
- 信息提取数 (extracted)
- 重要消息数 (important)
- 回复发送数 (replies_sent)
```

#### **2. 历史数据分析**

- 按时间范围统计（今日/本周/本月）
- 按联系人统计（Top 10）
- 按消息类型统计（工作/生活/广告）
- 按情绪统计（积极/中性/消极）

#### **3. 关键词趋势分析**

- 热门关键词实时排名
- 关键词时间趋势图
- 关键词关联分析

#### **4. 联系人分析**

- 消息频率统计
- 重要消息分布
- 情绪倾向分析
- 时间活跃度

#### **5. 导出功能**

- CSV格式导出
- JSON格式导出
- 按联系人分组导出
- 按时间范围导出

---

## 🔍 诊断工具

### 运行诊断

```bash
# 完整诊断
python fix_integration.py --diagnose

# 仅配置验证
python config_fixer.py --validate-only
```

### 诊断报告内容

```
🔍 功能诊断报告

🤖 自动回复功能:
   状态: ✅ 已修复
   API Key有效: ✅
   LLM客户端: ✅
   当前发送方法: clipboard
   总失败次数: 0

📊 数据统计功能:
   状态: ✅ 已修复
   缓存状态: ✅ 正常
   总消息数: 1234

⚙️  系统状态:
   自动回复开关: ✅ 开启
   LLM客户端: ✅ 可用
   存储模块: ✅ 可用
   存储类型: sqlite
```

---

## 💡 使用建议

### 自动回复最佳实践

1. **API Key配置**
   - 使用有效的阿里云千问API Key
   - 定期检查API Key有效期
   - 设置合理的调用频率限制

2. **回复策略**
   - 发送延迟设置为1-2秒，避免风控
   - 选择合适的角色和回复风格
   - 测试不同发送方法的稳定性

3. **联系人管理**
   - 为重要联系人配置专门角色
   - 使用白名单过滤不需要回复的联系人
   - 定期检查回复日志

### 数据统计最佳实践

1. **数据管理**
   - 定期清理历史数据
   - 备份重要统计数据
   - 使用SQLite模式获得最佳性能

2. **统计监控**
   - 定期查看统计面板
   - 关注关键词趋势变化
   - 分析联系人活跃度

3. **报告生成**
   - 启用自动日报/周报
   - 设置合理的报告时间
   - 定期导出重要数据

---

## 🛠️ 故障排除

### 自动回复不工作

1. **检查API Key**
   ```bash
   python config_fixer.py --validate-only
   ```

2. **查看诊断报告**
   ```bash
   python fix_integration.py --diagnose
   ```

3. **检查日志文件**
   ```bash
   tail -f wechat_ai_reply.log | grep "自动回复"
   ```

### 统计数据不更新

1. **清除缓存**
   ```python
   engine.enhanced_stats.invalidate_cache()
   ```

2. **强制刷新**
   ```python
   stats = engine.enhanced_stats.get_cached_stats(force_refresh=True)
   ```

3. **检查数据库**
   ```bash
   sqlite3 data/messages.db "SELECT COUNT(*) FROM messages;"
   ```

---

## 📈 性能优化

### 自动回复优化

- 使用缓存减少LLM调用
- 批量处理相似消息
- 异步发送机制

### 统计优化

- 统计结果缓存（30秒）
- 增量统计计算
- 异步数据更新

---

## 🔒 安全建议

1. **API Key保护**
   - 不要将API Key提交到版本控制
   - 定期更换API Key
   - 使用环境变量存储敏感信息

2. **数据隐私**
   - 定期清理敏感数据
   - 加密存储重要配置
   - 限制数据访问权限

---

## 📞 技术支持

如遇问题，请提供以下信息：

1. 诊断报告输出
2. 日志文件内容（最近100行）
3. 配置文件内容（脱敏后）
4. 具体错误信息和复现步骤

---

## 🎉 更新日志

### v2.1 (当前版本)

- ✅ 修复自动回复功能失效
- ✅ 修复数据统计失效
- ✅ 增加智能发送机制
- ✅ 增强多维度统计
- ✅ 添加诊断工具
- ✅ 优化配置管理

### v2.0

- 红点监控
- 增量检测
- AI训练引擎
- Obsidian同步

---

**修复完成后，建议重启程序并测试各项功能！**

---

## v2.0 更新日志 (2026-08-20)

### 修复内容

1. **启动延迟修复**：移除 initialize() 中所有人工 time.sleep() 调用，启动速度提升 4-5 秒。

2. **预览确认模式**：
   - 新增预览确认模式开关，生成回复后粘贴到微信输入框不自动发送
   - 用户可手动检查后按 Enter 发送
   - 修复预览模式下重复触发 LLM 的问题（标记已回复状态）

3. **默认角色调整**：所有联系人默认角色改为朋友（轻松友好风格）

4. **Ctrl+T 全局快捷键**：bind() 改为 bind_all()，任何窗口焦点下均可使用

5. **启动诊断优化**：系统诊断移至后台线程，启动监控不再卡顿

6. **消息列表持久化**：
   - 重启后自动从 data/messages_history.json 恢复历史消息
   - 联系人卡片自动重建
   - 消息列表自动显示

7. **消息排序优化**：按微信风格，最新消息显示在底部，自动滚动到最新位置

### 配置文件变更

config.yaml 新增:
  auto_reply.preview_mode: false
  default_role: friend
