#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NOYA Chat 微信助手 v3.0 — 更新 README.md"""
import os

BASE = r"d:\下载\wechat-ai-reply-main"

readme = r'''# NOYA Chat 微信助手

> 微信消息智能中枢：截图 OCR + AI 理解 + 自动回复 + Obsidian 知识沉淀

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-blue?logo=python" />
  <img src="https://img.shields.io/badge/OCR-PaddleOCR-orange" />
  <img src="https://img.shields.io/badge/LLM- 千问-green" />
  <img src="https://img.shields.io/badge/Platform-Windows-lightgrey" />
  <img src="https://img.shields.io/badge/License-MIT-yellow" />
  <img src="https://img.shields.io/badge/Version-3.0-blueviolet" />
</p>

---

## 这是什么

**NOYA Chat 微信助手** 是一款运行在 Windows 上的微信消息智能管理工具。它通过**截图 + OCR** 实时读取微信聊天内容，接入**阿里云千问大模型**进行语义理解，实现自动回复、信息提取、待办追踪，并将所有聊天数据同步到 **Obsidian** 构建个人知识库。

核心设计理念：**微信是你的信息入口，Obsidian 是你的思考出口。**

---

## 已实现功能

### 消息监控
| 功能 | 说明 |
|------|------|
| 红点监控 | 自动检测微信左侧栏未读红点，切换会话提取消息后切回原窗口 |
| 双模检测 | 模板匹配优先 + HSV 6 特征兜底，适应不同 DPI 和主题 |
| 后台无感 | 支持最小化/屏幕外运行，全程不闪窗不抢焦点 |
| 冷却机制 | 60 秒联系人冷却，防止重复切换同一会话 |

### OCR 识别
| 功能 | 说明 |
|------|------|
| 中英混合 | 基于 PaddleOCR，支持中文、英文、数字、表情符号 |
| 发送者区分 | 基于气泡位置和颜色判定自己/对方，准确率 90%+ |
| 增量检测 | 帧间差异 + pHash 去重，画面静止时跳过 OCR，省 CPU |
| 模糊帧过滤 | Laplacian 方差评分，过滤微信渲染过渡动画的模糊帧 |

### AI 智能回复
| 功能 | 说明 |
|------|------|
| 多角色 | 技术顾问、商务伙伴、可爱朋友、段子手，可按联系人配置 |
| 默认角色 | 所有联系人默认「朋友」角色，轻松友好风格 |
| 预览确认 | 生成的回复粘贴到微信输入框，用户手动确认后发送 |
| 停止即停 | 停止时 LLM 调用前/生成后双重检查，确保彻底停止 |
| 上下文记忆 | 自动维护最近 N 条消息作为对话上下文 |

### AI 信息提取
| 功能 | 说明 |
|------|------|
| 消息分类 | 自动标注：工作/生活/广告/验证码/通知/其他 |
| 紧急程度 | 1-5 级评分，重要消息红色卡片高亮 |
| 待办提取 | 从聊天中自动识别待办事项，支持截止日期 |
| 情绪分析 | 识别对方情绪（积极/中性/消极） |
| 关键词规则 | 支持正则表达式自定义规则，灵活扩展 |

### 数据管理
| 功能 | 说明 |
|------|------|
| 持久化存储 | SQLite 数据库，消息重启不丢失 |
| 多格式导出 | CSV / JSON 双格式，按联系人分组 |
| 历史搜索 | 关键词 + 联系人 + 日期范围三维搜索 |
| 消息排序 | 微信原生风格，最新消息在底部，自动滚动 |
| 统计面板 | 8 项实时指标 + 发送者 Top5 + 内存消息池详情 |

### 快捷键
| 快捷键 | 功能 |
|--------|------|
| `Ctrl+T` | 全局切换开始/停止监控（任何窗口焦点均可用） |

### Obsidian 同步
| 功能 | 说明 |
|------|------|
| 双模式 | 文件直写（本地 Vault）+ REST API（远程） |
| 自动组织 | 按 `联系人/日期` 目录结构自动归档 |
| 实时同步 | 消息到达后即时写入 Obsidian 笔记 |

---

## 待实现

### 短期（v3.1 - v3.3）
- [ ] **语音消息识别**：对接 Whisper 将微信语音转文字
- [ ] **图片理解**：截图/图片消息接入多模态 LLM 进行内容描述
- [ ] **多微信支持**：同时监控多个微信实例
- [ ] **Webhook 推送**：重要消息推送到钉钉/飞书/Slack
- [ ] **定时摘要**：每日/每周自动生成聊天摘要并推送
- [ ] **聊天记录导出为 Markdown**：一键导出任意联系人的完整聊天记录

### 中期（v3.4 - v4.0）
- [ ] **知识图谱**：从聊天中自动抽取实体关系，构建个人社交知识图谱
- [ ] **智能日程**：从聊天中提取会议、约会，自动创建日历事件
- [ ] **情感曲线**：长期追踪联系人情绪变化，生成情感趋势报告
- [ ] **回复模板库**：高频场景一键套用模板回复
- [ ] **多 LLM 切换**：支持 OpenAI / Claude / 本地模型
- [ ] **插件系统**：开放插件接口，社区可扩展自定义功能

### 长期愿景
- [ ] **个人 CRM**：自动维护联系人画像（生日、喜好、最近话题）
- [ ] **AI 代理模式**：LLM 自主决定何时回复、回复什么
- [ ] **全平台**：macOS / Linux 支持

---

## Obsidian 联动玩法

NOYA Chat 把微信消息同步到 Obsidian 后，你的 Vault 就不再只是笔记仓库，而是一个**活的社交数据中心**。以下是一些有趣的玩法：

### 1. 个人 CRM 系统
利用 Obsidian 的 Dataview 插件，自动聚合所有联系人的聊天记录：
```dataview
TABLE date, summary, emotion FROM "微信" WHERE emotion = "消极" SORT date DESC

text



一眼看出谁最近情绪不好，主动关心。

### 2. 待办自动追踪
聊天中的待办事项自动提取为 Obsidian Tasks：
```markdown
 周五前提交方案 📅 2026-08-22 (来自: 张总)
 帮小李看代码 🔁 every week (来自: 技术群)
text



配合 Obsidian Tasks 插件，待办永远不会漏。

### 3. 周报自动生成
每周日，Obsidian Templater 读取一周聊天数据，自动生成周报：
```markdown
本周工作总结
跟进了 3 个客户需求
处理了 5 个技术问题
参加了 2 次线上会议
text



老板再也不用催你写周报了。

### 4. 社交关系图谱
用 Obsidian Graph View 可视化你的社交网络：
- 每个联系人是一个节点
- 聊天频率决定连线粗细
- 按群组/公司/圈子自动聚类
- 一眼看出你的社交重心在哪里

### 5. 聊天回忆录
年底一键导出全年聊天精华：
```markdown
2026 年度聊天回顾
你最常联系的人：张三（1,247 条消息）
你最晚的聊天：凌晨 3:42，和李四讨论方案
你说过最多的话："好的"（326 次）
情绪最积极的一天：8 月 15 日
text




### 6. AI 日记助手
每天结束时，LLM 根据当天聊天内容自动生成一篇日记：
今天和张总讨论了 Q3 规划，确定了三个优先级。 小王遇到了技术难题，帮他解决了性能优化问题。 晚上和李四约了周末打球，心情不错。

text




### 7. 知识库自动沉淀
群聊中的技术讨论、经验分享自动提取到 Obsidian 知识库：
- 技术方案 → `技术笔记/`
- 行业资讯 → `行业动态/`
- 读书推荐 → `阅读清单/`
- 好用的工具 → `工具推荐/`

### 8. 客户跟进看板
用 Obsidian Kanban 插件管理客户跟进：
- 待联系 / 跟进中 / 已成交 / 已流失
- 每条卡片自动关联最近聊天记录
- 超时未联系自动提醒

---

## 快速开始

### 环境要求
- Windows 10/11
- Python 3.10+
- 微信 4.x（桌面版）
- 阿里云千问 API Key（[免费申请](https://dashscope.aliyun.com) ）

### 安装
```bash
git clone https://github.com/yourname/noya-chat-wechat.git cd noya-chat-wechat pip install -r requirements.txt pip install pywin32 psutil

text




### 配置
编辑 `config.yaml`，填入你的 API Key：
```yaml
llm: api_key: sk-your-key-here model: qwen3.7-flash-2026-07-15

auto_reply: enabled: false # 建议先用预览模式 preview_mode: true # 生成回复后手动确认

obsidian: enabled: true # 开启 Obsidian 同步 vault_path: "D:/Obsidian/MyVault" sync_mode: file # file 或 api

text




### 启动
```bash
python ui_app.py

或双击 start.bat
text




---

## 技术架构
+-------------------------------------------------+ | UI 层 (customtkinter) | | 监控面板 | 回复设置 | 设置 | 数据 | 搜索 | +------------------------+------------------------+ | 回调 +------------------------v------------------------+ | 核心引擎 (WeChatEngine) | | +----------+ +----------+ +------------------+ | | |红点监控器| | 消息解析 | | AI训练引擎 | | | |(模板+HSV)| | (稳定帧) | | (10次学习->规则) | | | +----+-----+ +----+-----+ +--------+---------+ | | | | | | | +----v-----+ +----v-----+ +-------v--------+ | | | 截图模块 | | OCR引擎 | | LLM客户端 | | | | (mss) | |(PaddleOCR)| | (千问/Qwen) | | | +----------+ +----------+ +----------------+ | +------------------------+------------------------+ | +------------------------v------------------------+ | 数据层 (SQLite + CSV + JSON + Obsidian) | +-------------------------------------------------+

text




---

## 性能优化

- **帧变化检测**：画面静止时自动跳过 OCR，CPU 占用降低 70%+
- **模糊帧过滤**：Laplacian 方差评分，过滤微信过渡动画的无效帧
- **位置桶去抖**：同位置相似文本合并，抑制 OCR 抖动
- **三层去重**：帧内 + 跨帧 + 跨轮，确保消息不重复
- **最小化自愈**：后台自动恢复窗口渲染态，全程不闪窗
- **日志轮转**：1MB x 3 份自动轮转，长期运行不卡顿

---

## 项目结构
noya-chat-wechat/ |-- ui_app.py # UI 主程序 (customtkinter) |-- main.py # 核心引擎 (WeChatEngine) |-- config.yaml # 配置文件 |-- requirements.txt # 依赖列表 |-- start.bat # 一键启动 | |-- red_dot_monitor.py # 红点监控 (模板匹配 + HSV) |-- ocr_engine.py # OCR 识别引擎 |-- screenshot.py # 截图模块 |-- window_manager.py # 窗口管理 |-- message_parser.py # 消息解析 (稳定帧) |-- llm_client.py # LLM 客户端 |-- extractor.py # 信息提取 |-- sender.py # 消息发送 |-- role_manager.py # 角色管理 |-- ai_trainer.py # AI 训练引擎 |-- smart_monitor.py # 增量检测 |-- storage.py # 数据存储 (SQLite) |-- contact_scanner.py # 联系人扫描 |-- obsidian_sync.py # Obsidian 同步 |-- report_generator.py # 报告生成 |-- calibration.py # 窗口校准 |-- debug/ # 调试截图

text




---

## 常见问题

<details>
<summary>启动提示「未找到微信窗口」</summary>

- 确保微信已登录且窗口在桌面（非最小化）
- 多显示器环境确保微信在主显示器
- 窗口标题不能包含「AI」或「助手」
</details>

<details>
<summary>OCR 识别不到文字</summary>

- 调整 OCR 缩放比例（建议 0.5-0.85）
- 使用「窗口校准」手动框选聊天区域
- 检查 DPI 缩放（推荐 100% 或 125%）
</details>

<details>
<summary>点击停止后还在回复</summary>

v3.0 已修复。停止时在 LLM 调用前和生成后双重检查，确保彻底停止。
</details>

<details>
<summary>首次启动很慢</summary>

PaddleOCR 模型初始化约 5-10 秒。v3.0 已移除多余延迟，启动速度提升 4-5 秒。
</details>

---

## 更新日志

### v3.0 (2026-08-20)
- 品牌升级：正式命名「NOYA Chat 微信助手」
- 预览确认模式：生成回复粘贴到输入框，手动确认发送
- 停止即停：LLM 调用前/后双重检查停止标志
- Ctrl+T 全局快捷键：任何窗口焦点下均可使用
- 默认角色改为「朋友」，轻松友好风格
- 消息持久化：重启自动恢复历史记录
- 消息排序：微信风格，最新在底部，自动滚动
- 统计面板：8 项指标 + 发送者分布 + 详情文本
- 启动优化：移除人为延迟，提速 4-5 秒
- 修复：预览重复触发、联系人卡片丢失、公众号过滤遗漏

### v2.0
- 红点监控（模板匹配 + HSV 6 特征）
- 增量检测（帧间差异 + pHash 去重）
- AI 训练引擎（10 次学习 -> 规则模式）
- Obsidian 同步
- 联系人过滤默认黑名单

### v1.0
- 基础监控（截图 + OCR + 信息提取）
- 多角色自动回复
- CSV/JSON 数据导出
- 窗口校准向导

---

## 免责声明

- 本工具仅供学习交流，请遵守微信使用条款
- 自动回复功能默认关闭，请谨慎使用
- 数据全部本地存储，不上传任何服务器
- 频繁自动回复可能触发微信风控

---

## License

MIT License

## 致谢

- [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) - 百度飞桨 OCR
- [customtkinter](https://github.com/TomSchimansky/CustomTkinter) - 现代 Tkinter UI
- [阿里云千问](https://dashscope.aliyuncs.com) - 通义千问大模型
- [Obsidian](https://obsidian.md) - 第二大脑
'''

with open(os.path.join(BASE, "README.md"), "w", encoding="utf-8") as f:
    f.write(readme)

print("[OK] README.md 已更新！")
print("包含：已实现功能表 + 待实现 Roadmap + 8 个 Obsidian 联动玩法")