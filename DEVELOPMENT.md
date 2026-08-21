# NOYA Chat 微信助手 — 开发接手指南

> 给下一个会话 / 任务使用的快速上手文档。先读本文件，再读 PROJECT_STRUCTURE.md。
> 最后更新：2026-08-21（提交 c9433cc，v3.0）

## 1. 如何运行

```bash
# GUI 模式（推荐，小白向）
python ui_app.py

# 或双击 start.bat（自动检查依赖并启动 GUI）

# CLI 模式（测试引擎用）
python main.py --config config.yaml
```

依赖安装：`pip install -r requirements.txt` + `pip install pywin32 psutil`

## 2. 核心架构与数据流

```
[微信窗口] --截图--> [OCR识别] --解析--> [消息数据]
    ↑                        ↓
[红点检测]              [AI抽取/重要度/待办]
    ↑                        ↓
[窗口管理]           [存储SQLite] --导出--> JSON/CSV
                            ↓
                    [Obsidian归档] / [AI自动回复(默认关)]
```

关键链路：
1. `window_manager` 找到微信窗口（标题不含“AI/助手”，避免自识别）
2. `screenshot.PrintWindow` 截图客户区（flags=3，支持屏幕外/后台）
3. `ocr_engine`（PaddleOCR scale=1.0，置信度阈值 0.40）识别文字
4. `smart_monitor` 帧差异检测，画面静止跳过 OCR
5. `message_parser` 按气泡位置/颜色区分收发方，MD5 去重
6. `red_dot_monitor` 侧边栏扫红点（3s），有未读则切换会话提取后还原窗口
7. `extractor` + `llm_client` 做 AI 抽取；`storage` 落库；`obsidian_sync` 归档

## 3. 关键设计决策（改代码前必读的硬约束）

- **最小化=移到屏幕外**：微信窗口最小化时移到 `(-10000, 0)`，完全不可见但保持渲染；
  恢复时回到原位置。用独立线程检测前台窗口立即还原。
- **后台点击**：用 `SendMessageW` 注入客户区相对坐标点击，不用物理鼠标，避免窗口闪现。
- **红点检测 6 特征**：颜色 #FA5151 / 面积 100-3000 / 圆度>0.5 / x>宽度35% / 内部白字 / 按白字占比排序。
- **联系人匹配**：红点与 OCR 联系人名按 y_diff<=60 匹配，不限制 X 方向。
- **冷却机制**：已处理联系人 60s 冷却；候选池需 2 帧稳定确认；MD5 去重避免重复。
- **UI 窗口标题不能含“微信”**：避免被窗口检测误判。
- **API Key 安全**：只放 config.yaml（已 gitignore）；禁止提交 config.yaml.* / *.bak* / *.backup.*。

## 4. 已完成的优化与修复（v3.0 → c9433cc）

- Bug：群消息 KeyError、CLI 启动崩溃、Obsidian 块 NameError、属性覆盖、滚动方向矛盾
- Bug：实时消息不显示（NoneType / after 线程安全）
- 后台：SendMessageW 后台点击、窗口屏幕外移动 + 自动还原、DPI 坐标修正
- 性能：自动回复线程化、result_text 行数封顶(600)、预览暂停、列表增量更新
- 重构：ui_theme.py 拆分主题常量、main.py 抽取 _physical_click、修复裸 except
- 清理：删除 1.py / visualizer.py / ImgDemo/ / _fix_*.py / _verify.py 等孤儿文件
- 安全：git filter-branch 重写历史清除泄露的 API Key 并 force push
- 文档：README 去冲突标记、补 ui_theme.py 目录项

## 5. 已知问题与注意事项

- **工作目录坑**：IDE 若把工作目录打开在 `__pycache__`，Write/Edit 工具会被限制，
  需重新打开项目根目录 `d:\下载\wechat-ai-reply-main`。
- 自动回复默认关闭，建议用预览确认模式（生成后粘贴输入框，人工确认发送）。
- 微信 PC 更新可能改变 UI 布局，红点/气泡检测参数需重新校准（用 calibration.py）。
- 屏幕外监控依赖窗口渲染，若微信暂停渲染（如长时间挂起）可能截到空白。

## 6. 续接任务的建议步骤

1. 先读本文件 + PROJECT_STRUCTURE.md + README.md 快速对齐上下文
2. 检查 `git log --oneline -5` 与 `git status` 看最新进度
3. 启动方式：`python ui_app.py` 或 `python main.py --config config.yaml`
4. 语法自检：`python -m py_compile *.py`
5. 改完记得：勿提交 config.yaml；推送前 grep 一次 `sk-` 防泄露