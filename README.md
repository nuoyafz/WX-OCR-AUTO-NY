# 微信AI助手

> 智能监控微信消息，自动OCR识别 + AI信息提取 + 可选自动回复

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python)
![PaddleOCR](https://img.shields.io/badge/OCR-PaddleOCR-orange)
![LLM](https://img.shields.io/badge/LLM-千问-green)
![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey)
![License](https://img.shields.io/badge/License-MIT-yellow)

## 简介

微信AI助手是一款基于 Python 的微信消息智能监控工具。它通过截图 + OCR 实时识别微信聊天界面文字，结合千问大模型（LLM）进行信息提取和自动回复。核心功能是**红点监控**——自动检测微信左侧栏的未读消息红点，切换到未读联系人提取消息后切回原窗口，实现后台无感监控。

## 核心功能

### 红点监控（核心）
- 每3秒扫描微信左侧栏，检测未读消息红点
- 模板匹配优先 + HSV 6特征兜底的双重检测方案
- 自动切换到未读联系人 → 提取消息 → 切回原窗口
- 60秒冷却机制，防止重复切换
- 首次运行自动截取红点模板，后续精度持续提升

### OCR 识别
- 基于 PaddleOCR，支持中英文混合识别
- 发送者区分（自己/对方），基于消息气泡位置和颜色
- 增量检测：帧间差异 + pHash 去重，仅在画面变化时触发OCR
- 底部区域裁剪，减少无效识别

### AI 信息提取
- 接入阿里云千问 LLM，自动提取：
  - 消息分类（工作/生活/广告/验证码/通知/其他）
  - 紧急程度（1-5级）
  - 一句话摘要
  - 待办事项列表
  - 情绪分析（积极/中性/消极）
- 自定义关键词规则，支持正则表达式
- 重要消息自动标记并彩色卡片展示

### AI 自动回复（默认关闭）
- 多角色回复风格：技术顾问、商务伙伴、可爱朋友、段子手
- 为不同联系人配置不同角色
- AI训练引擎：前10次AI辅助学习，之后切换为纯规则模式
- 可配置发送延迟，避免触发风控

### 数据管理
- SQLite 存储 + CSV/JSON 双格式导出
- 按联系人分组，退出时自动导出
- 历史消息搜索（关键词 + 联系人 + 日期范围）
- 日报/周报自动生成（HTML格式）

### Obsidian 同步
- 支持文件直写和 REST API 两种模式
- 自动同步消息到 Obsidian Vault
- 按联系人和日期组织笔记结构

## 快速开始

### 环境要求
- Windows 10/11
- Python 3.10+
- 微信 4.x（桌面版）
- 阿里云千问 API Key（用于AI提取和回复）

### 一键启动
```bash
# 1. 克隆仓库
git clone https://github.com/yourname/wechat-ai-reply.git
cd wechat-ai-reply

# 2. 双击 start.bat（推荐）
#    或手动安装依赖：
pip install -r requirements.txt
pip install pywin32 psutil  # requirements.txt 可能缺失

# 3. 修改 config.yaml 中的 LLM API Key
# 4. 启动
python ui_app.py
```

### 配置说明
编辑 `config.yaml`，重点配置：
```yaml
llm:
  api_key: sk-your-key-here        # 阿里云千问API Key
  model: qwen3.7-flash-2026-07-15

red_dot_monitor:
  enabled: true                     # 红点监控开关
  cooldown_seconds: 15              # 联系人冷却时间

auto_reply:
  enabled: false                    # 自动回复（默认关闭）
```

## 界面预览

| 标签页 | 功能 |
|--------|------|
| 监控 | 开始/停止监控、统计面板、实时消息卡片、运行日志 |
| 回复设置 | 自动回复开关、角色管理、AI训练重置 |
| 设置 | LLM配置、OCR参数、联系人过滤、Obsidian同步、窗口校准 |
| 搜索 | 历史消息搜索、重要消息查看、报告生成 |

### 消息卡片配色
- 🟢 绿色：自己的消息
- ⚪ 白色：对方的消息
- 🔴 红色：重要/紧急消息
- 🟠 橙色：关键词命中
- 🔵 蓝色：提取的信息
- 🟣 紫色：AI 摘要

## 技术架构

```
┌─────────────────────────────────────────────────┐
│                   UI 层 (customtkinter)          │
│  监控面板 │ 回复设置 │ 设置 │ 搜索 │ 报告         │
└────────────────────┬────────────────────────────┘
                     │ 回调
┌────────────────────▼────────────────────────────┐
│              核心引擎 (WeChatEngine)              │
│  ┌──────────┐ ┌──────────┐ ┌──────────────────┐ │
│  │红点监控器│ │ 消息解析 │ │  AI训练引擎      │ │
│  │(模板+HSV)│ │ (稳定帧) │ │ (10次学习→规则) │ │
│  └────┬─────┘ └────┬─────┘ └────────┬─────────┘ │
│       │            │                │            │
│  ┌────▼─────┐ ┌────▼─────┐ ┌───────▼────────┐  │
│  │ 截图模块 │ │ OCR引擎  │ │  LLM客户端     │  │
│  │ (mss)   │ │(PaddleOCR││  (千问/Qwen)    │  │
│  └──────────┘ └──────────┘ └────────────────┘  │
└────────────────────┬────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────┐
│        数据层 (SQLite + CSV + JSON + Obsidian)   │
└─────────────────────────────────────────────────┘
```

## 稳定性与性能优化

### 帧变化检测（省 CPU）
- 主循环用感知哈希 + absdiff 做帧间差异检测：底部区域无变化、或整帧变化占比低于阈值时，**直接跳过整轮 OCR**（截图照做、OCR 不做）。
- **最小化/屏幕外模式同样启用**（`_is_min` 时变化检测只用于决定"要不要 OCR"，需要识别时仍走全量 OCR 防漏），聊天画面静止时 CPU 占用大幅下降。

### 模糊帧质量评分
- 截图后计算 **Laplacian 方差** 评估清晰度，低于 `blur_min_var`（默认 25，可在 `config.yaml` 的 `wechat` 段调整）的模糊帧（渲染中 / 过渡动画）直接跳过本轮 OCR，避免把过渡帧误识别成垃圾文本。

### OCR 位置桶去抖
- OCR 结果按 `(x_center//60, y_center//60)` 位置桶分组，同桶且文本相似度 ≥ 0.9 的行合并为一条（保留置信度最高者），抑制"同一消息每帧 OCR 抖动 → 近似重复"。

### 发送者判定历史一致性（V4.5）
- `identify_senders_v4` 增加跨帧位置桶投票：同一位置连续 ≥ 2 帧判定一致才采用并提置信至 0.90，8 帧未更新自动过期——抑制单帧颜色/位置误判，自己的消息稳定显示在**右侧绿色气泡**。

### 三层去重 + 持久化
- 帧内：`seen_texts` + 位置桶去抖
- 跨帧：parser 稳定帧机制 + `processed_hashes`
- 跨轮：红点路径 `_reddot_msg_seen`（持久化 `data/reddot_seen.json`，重启不重复上报）
- 消息历史：UI 消息池防抖写盘 `data/messages_history.json`，重启保留聊天记录

### 最小化点击自愈
- 处理未读消息前调用 `ensure_window_rendering` 把窗口恢复到"屏幕外可见"的可渲染后台态（最小化窗口 GPU 停渲染 → PrintWindow 全黑 → 无法验证是否切换到正确联系人），再执行 SendMessageW 后台点击，全程不闪窗。

### 日志轮转
- 文件日志 `RotatingFileHandler`（1MB × 3 份自动轮转）；UI 日志框仅保留最近 800 行，长期运行不卡顿。

### 消息气泡布局
- 消息区为微信 PC 风格：**对方消息左侧白色气泡，自己的消息右侧绿色气泡**（贴右靠头像，时间/分类标签在气泡左侧）。

## 红点检测方案

采用**模板匹配优先 + HSV兜底**的双重方案：

### 模板匹配（优先）
- 首次运行时从HSV检测结果中自动截取红点模板
- 后续运行使用 `cv2.matchTemplate()` 精准定位
- 多尺度匹配（0.8x ~ 1.2x），适应不同DPI
- NMS（非极大值抑制）去重

### HSV 6特征检测（兜底）
| 特征 | 阈值 | 说明 |
|------|------|------|
| 颜色 | #FA5151 | 微信红点精确颜色 |
| 面积 | 100-3000 px² | 排除头像和图标 |
| 圆形度 | > 0.5 | 排除非圆形红色元素 |
| 位置 | x > 35% 宽度 | 排除左侧头像区 |
| 内部白字 | white_ratio > 0.03 | 必须有白色数字 |
| 排序 | 按 white_ratio 降序 | 优先处理数字最多的红点 |

## 项目结构

```
wechat-ai-reply/
├── ui_app.py              # UI主程序（customtkinter）
├── main.py                # 核心引擎（WeChatEngine）
├── config.yaml            # 配置文件
├── requirements.txt       # 依赖列表
├── start.bat              # 一键启动脚本
├── banner.txt             # 启动字符画
├── 使用说明.html          # 详细使用说明
│
├── red_dot_monitor.py     # 红点监控（模板+HSV）
├── ocr_engine.py          # OCR识别引擎
├── screenshot.py          # 截图模块
├── window_manager.py      # 窗口管理
├── message_parser.py      # 消息解析（稳定帧）
├── llm_client.py          # LLM客户端
├── extractor.py           # 信息提取
├── sender.py              # 消息发送
├── role_manager.py        # 角色管理
├── ai_trainer.py          # AI训练引擎
├── smart_monitor.py       # 增量检测
├── storage.py             # 数据存储
├── contact_scanner.py     # 联系人扫描
├── obsidian_sync.py       # Obsidian同步
├── report_generator.py    # 报告生成
├── calibration.py         # 窗口校准
└── debug/                 # 调试截图目录
    ├── red_dot_template.png    # 红点模板
    ├── sidebar_capture.png     # 侧边栏截图
    └── red_dot_mask.png        # 红点掩码
```

## 依赖清单

| 包名 | 用途 |
|------|------|
| paddleocr | OCR 文字识别 |
| paddlepaddle | PaddleOCR 运行时依赖 |
| opencv-python | 图像处理 / 模板匹配 |
| numpy | 数值计算 |
| Pillow | 图像处理（中文路径支持） |
| customtkinter | UI 界面 |
| pyautogui | 鼠标键盘控制 |
| pygetwindow | 窗口查找 |
| pywin32 | Windows API（DPI/窗口） |
| mss | 高速截图 |
| PyYAML | 配置文件解析 |
| requests | HTTP 请求（LLM API） |
| psutil | 进程监控 |
| pyperclip | 剪贴板操作 |

## 配置项详解

### 红点监控
```yaml
red_dot_monitor:
  enabled: true              # 开关
  cooldown_seconds: 15       # 联系人冷却（秒）
  red_min_area: 150          # 红点最小面积
  red_max_area: 5000         # 红点最大面积
  sidebar_width_ratio: 0.25  # 侧边栏宽度比例
  template_threshold: 0.75   # 模板匹配置信度
  template_auto_capture: true # 自动截取模板
```

### 增量检测
```yaml
wechat:
  smart_monitor:
    enabled: true
    diff_threshold: 15       # 像素差异阈值
    diff_area_ratio: 0.005   # 变化区域比例
    hamming_threshold: 5     # pHash 汉明距离
    bottom_ratio: 0.25       # 底部检测比例
```

### 联系人过滤
```yaml
contacts_filter:
  whitelist: []              # 白名单（空=全部监控）
  blacklist:                 # 黑名单（默认含系统账号）
    - 公众号
    - 服务号
    - 订阅号
    - 文件传输助手
    - 折叠的群聊
    # ... 更多默认项
```

## 开发说明

### 添加自定义角色
在 `config.yaml` 的 `roles` 下添加：
```yaml
roles:
  my_role:
    name: 我的角色
    reply_style: 友好、随和
    system_prompt: 你是一个友好的朋友...
```

### 添加关键词规则
```yaml
extraction:
  custom_rules:
    keywords:
      - category: 自定义类别
        important: true
        words: [关键词1, 关键词2]
    regex:
      - group: 自定义组
        name: 规则名
        pattern: '\d{4}-\d{2}-\d{2}'
```

## 常见问题

<details>
<summary>启动时提示「未找到微信窗口」</summary>

- 确保微信已登录且窗口显示在桌面（非最小化）
- 窗口标题不能包含「AI」或「助手」（会与本程序冲突）
- 多显示器环境下，确保微信在主显示器
</details>

<details>
<summary>OCR 识别不到文字</summary>

- 检查微信窗口是否被其他窗口遮挡
- 在设置页调整 OCR 缩放比例（建议 0.5-0.85）
- 使用「窗口校准」功能手动框选聊天区域
- 查看 DPI 缩放设置（推荐 100% 或 125%）
</details>

<details>
<summary>红点检测不到未读消息</summary>

- 查看 `debug/` 目录下的调试截图
- 确认红点颜色为 #FA5151（微信标准红）
- 调整 `red_min_area` 和 `red_max_area` 参数
- 首次运行会自动截取红点模板，后续精度会提升
</details>

<details>
<summary>首次启动很慢</summary>

首次启动需要初始化 PaddleOCR 模型和 LLM 客户端，约5-10秒。程序会分步加载并显示进度日志，属正常现象。
</details>

## 注意事项

- 本工具仅供学习交流，请遵守微信使用条款
- 自动回复功能默认**关闭**，开启请谨慎配置
- 建议先用「测试OCR」验证识别准确性，再开启自动回复
- 频繁的自动回复可能触发微信风控，请合理设置延迟
- 数据本地存储，不上传任何服务器

## 更新日志

### v2.0
- 新增红点监控（模板匹配 + HSV 6特征）
- 新增增量检测（帧间差异 + pHash 去重）
- 新增 AI 训练引擎（10次学习 → 规则模式）
- 新增 Obsidian 同步
- 优化首次启动体验（分步加载 + 进度日志）
- 新增一键启动脚本（start.bat）
- 新增联系人过滤默认黑名单

### v1.0
- 基础监控功能（截图 + OCR + 信息提取）
- 多角色自动回复
- CSV/JSON 数据导出
- 窗口校准向导

## License

MIT License - 详见 [LICENSE](LICENSE)

## 致谢

- [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) - 百度飞桨 OCR
- [customtkinter](https://github.com/TomSchimansky/CustomTkinter) - 现代 Tkinter UI
- [阿里云千问](https://dashscope.aliyuncs.com) - 通义千问大模型
