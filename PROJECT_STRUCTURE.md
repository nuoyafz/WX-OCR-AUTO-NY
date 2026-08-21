# NOYA Chat 微信助手 — 项目结构说明

> 用于快速识别项目文件职责，方便换任务 / 换会话后继续开发。
> 最后更新：2026-08-21（提交 c9433cc，v3.0）

## 一、项目概览

Windows 本地微信消息智能管理工具。基于 **窗口截图 + OCR** 实时解析微信会话，
接入大模型做语义解析 / 信息抽取 / 智能回复，并归档到 SQLite 与 Obsidian。

- 入口：`ui_app.py`（GUI，推荐）/ `main.py`（CLI）
- 技术栈：Python 3.10+ / customtkinter / PaddleOCR / OpenCV / mss / pywin32 / SQLite
- 定位：非微信协议逆向，纯屏幕截图 OCR 方案；所有数据本地存储

## 二、目录结构

```
wechat-ai-reply-main/
├── ui_app.py               # UI主程序（customtkinter）· 入口1（GUI）
├── ui_theme.py             # UI主题常量（WC_COLORS，从 ui_app.py 拆分）
├── main.py                 # 核心引擎 WeChatEngine · 入口2（CLI）
├── window_manager.py       # 微信窗口查找 / 定位 / 屏幕外最小化管理
├── screenshot.py           # 截图模块（mss，多显示器 + DPI）
├── ocr_engine.py           # OCR引擎（微信OCR / PaddleOCR + 优化V2）
├── wechat_ocr_engine.py    # 微信内置 OCR 引擎（WeChatOCR.exe）
├── smart_monitor.py        # 增量帧检测（4层优化替代全量OCR）
├── message_parser.py       # 消息解析：发送者识别 / 去重 / 上下文
├── red_dot_monitor.py      # 红点监控（模板匹配优先 + HSV六特征兜底）
├── extractor.py            # AI信息抽取（待办 / 情绪 / 分类 / 关键词）
├── llm_client.py           # 大模型API客户端（阿里云千问 Qwen）
├── role_manager.py         # AI角色管理（角色定义 + 联系人映射）
├── sender.py               # 消息发送（键盘模拟 / 剪贴板 / 屏幕外）
├── tuning.py               # 识别阈值统一源（气泡分组/发送者/群聊投票，可配置覆盖）
├── storage.py              # SQLite持久化 + JSON/CSV按联系人导出
├── obsidian_sync.py        # Obsidian同步（文件直写 / REST API 双模式）
├── report_generator.py     # 每日报告生成（日报/周报）
├── contact_scanner.py      # 多联系人批量扫描模块
├── calibration.py          # 窗口校准向导（DPI / 多显示器）
├── ai_trainer.py           # AI训练引擎（学习→规则固化→离线推理）
├── config.example.yaml     # 配置模板（含占位 API Key）
├── requirements.txt        # Python 依赖清单
├── start.bat               # Windows 一键启动（运行 ui_app.py）
├── banner.txt              # 启动横幅文字
├── learned_rules.json      # AI训练学习得到的规则（运行期生成）
├── __init__.py
├── README.md               # 用户说明文档（功能/架构/FAQ）
├── FIXES_README.md         # 历史修复记录文档
├── LICENSE                 # MIT License
├── 使用说明.html           # 图形化使用说明
├── assets/                 # README 演示截图（demo.png/demo2/demo3.png）
├── data/                   # 运行时数据（SQLite / 导出文件，被 git 忽略）
└── debug/                  # 调试截图 / 红点模板（被 git 忽略）
```

## 三、核心模块依赖关系

```
ui_app.py（界面） ──回调──> main.py（WeChatEngine 核心引擎）
                              ├── window_manager.py  窗口定位/移动/后台点击
                              ├── screenshot.py      截图
                              ├── ocr_engine.py      文字识别
                              ├── smart_monitor.py   帧差异检测（跳过静止帧）
                              ├── message_parser.py  解析消息/去重
                              ├── red_dot_monitor.py 未读红点检测
                              ├── extractor.py + llm_client.py   AI抽取
                              ├── storage.py         持久化
                              ├── obsidian_sync.py   Obsidian归档
                              └── role_manager.py / sender.py   自动回复
```

## 四、配置文件说明

- `config.yaml`：真实配置（API Key 等敏感信息）。**已被 .gitignore 忽略，严禁提交。**
- `config.example.yaml`：模板，用占位符示例所有可配置项。

关键配置项：`llm.api_key / base_url / model`、`auto_reply.enabled`（默认关）、
`obsidian`（vault_path / sync_mode）、`red_dot_monitor`（扫描/冷却/阈值）。

## 五、数据与资源

- `data/`：SQLite 数据库、导出文件（JSON/CSV）
- `debug/`：调试截图（sidebar_capture.png、red_dot_mask.png 等）与红点模板
- `assets/`：README 演示图