# 项目长期笔记 — wechat-ai-reply（微信AI助手）

## 项目本质
纯视觉方案微信监控/自动回复：**截图 + OCR + LLM，不 hook 微信进程/内存**。安全但受窗口渲染状态限制。

## 运行环境
- Windows，Python 3.10（system，在 `C:\Users\fangzhou\AppData\Local\Programs\Python\Python310`），微信 4.x（Qt/Chromium）。
- 入口 `ui_app.py`（customtkinter UI），配置 `config.yaml`。核心引擎 `main.py` 的 `WeChatEngine`。
- 托管 Python 3.13 在 `C:\Users\fangzhou\.workbuddy\binaries\...`，但项目依赖（paddleocr/pywin32/mss/numpy）装在 **3.10**，跑功能测试要用 3.10；语法检查用 3.13 即可。

## 后台化（"最小化监控"）核心认知
- **截不到图的真正原因有两种，都不是代码 bug，是 Windows/Qt 硬限制**：
  1. **最小化(WS_MINIMIZE)**：GPU 停止渲染 → PrintWindow 黑图。
  2. **隐藏(WS_VISIBLE 被清除)**：微信"关闭收到托盘"是**隐藏而非最小化**，`IsIconic`/`WS_MINIMIZE` 检测不到！此时窗口可能还停在屏幕上但不可见，GPU 同样停渲染 → PrintWindow 黑图。**这是最常见的踩坑点**（用户说"最小化"其实是隐藏）。
- 正确做法 = `minimize_mode: offscreen`：检测到 **最小化 OR 隐藏** → `SW_RESTORE`(最小化) 或 `SW_SHOWNOACTIVATE`(隐藏) 恢复可见 + SetWindowPos 移到屏幕外(-4000,0) 并带 `SWP_SHOWWINDOW`。用户看不到（等同后台），但 Qt 持续渲染，PrintWindow 可截。
- 关键：判断隐藏要读 `GWL_STYLE & WS_VISIBLE`，不能只看 `IsIconic`。`window_manager.keep_alive_offscreen` / `move_window_offscreen` / `screenshot.ensure_window_rendering` 三处都已处理"隐藏"分支。
- 抖音"刘嬷嬷"/CSDN/掘金方案与此相同（ensure_visible 只做 SW_RESTORE，不保持最小化）。

## PrintWindow 要点（screenshot.py `capture_via_printwindow`）
- **flags=3 (PW_CLIENTONLY|PW_RENDERFULLCONTENT) 优先**，对 Qt/QML 微信最有效。
- 位图尺寸必须和 flags 匹配：flags=3 配客户区尺寸(GetClientRect)；flags=2(整窗)配客户区位图会错位黑屏。
- **PrintWindow 返回非0也可能吐黑图**，必须读回像素校验(mean<1 或 std<5 判黑)后降级到下一个 flags。
- 窗口矩形用 `DwmGetWindowAttribute(EXTENDED_FRAME_BOUNDS)` 比 GetWindowRect 准（剔除隐形边框/DPI偏移）。

## OCR 裁剪铁律
- PrintWindow 截的是**整个客户区（含左侧联系人栏）**。做聊天消息 OCR 前必须先用 `crop_chat_region_img()` 剔掉左侧栏(~30%)、顶部(~8%)、底部输入框，否则联系人列表/群名/时间戳会被误识别成"新消息"（这是"实时消息不显示/显示垃圾"的根因）。
- 红点检测的侧栏截图走 `red_dot_monitor.capture_sidebar` 独立路径，不要给它剔侧栏。

## 注意
- 微信有多个 `Qt51514QWindowIcon` 窗口，查找主窗要用 GetWindowPlacement 还原尺寸过滤（辅助小窗仅 ~237x56）。
- `window_manager.find_wechat_window` 可能误匹配标题含"wechat/微信"的浏览器标签页（Edge/Chrome_WidgetWin_1）。
- 改完代码必须重启 `ui_app.py` 才生效（旧进程缓存旧模块）。

## 用户约定
- **每次更新代码前/后都要先 git 提交**（用户明确要求）。
- 用户沟通偏好：极简指令式（"重新""修改p0"），给结论+可执行改法，不要长篇铺垫。

## 最小化时"点击未读消息不准确"——根因与修复（169e3f9）
- **症状**：最小化模式下，处理未读联系人时第二个开始经常"点不准/切换不生效/识别内容错"。
- **根因**：入口进入 _handle_unread 时没强制保证窗口可渲染。若窗口处于最小化/隐藏态，GPU 停渲染，**PrintWindow 截到全黑**（debug/preview/fail_*.png 206 字节黑图就是铁证）→ 无法验证 SendMessageW 是否真切到了正确联系人 → 后续 OCR 也失效 → 程序表现"不准"。**首次成功+后续黑图**的时序就是这条根因的指纹。
- **修复**：offscreen 模式下，进 _handle_unread 入口前先 `ensure_window_rendering(hwnd, window)`（最小化→先设屏幕外位置→SW_SHOWNOACTIVATE→RedrawWindow）把窗口恢复到"屏幕外可见"的可渲染后台态，再重新判 `_was_offscreen`；同时用 `_acquire_interaction`/`_release_interaction` 暂停 keep_alive watcher 防竞态。
- **debug 证据路径**：`debug/preview/capture_*.png`（截图）+ `fail_*.png`（~200 字节黑图）+ `red_dot_mask.png`（红点检测掩码），对比时间戳可直接复现"点→切→黑→重试仍黑"链路。

## 分类优先级规则系统（用户可自定义 工作/私事/群聊 等优先级）
- 配置在 `config.yaml` 的 `extraction.classification`：`enabled` + `categories`（name/priority/important/keywords）。
- 逻辑在 `extractor.py`：`_classify(text, extra)` 按 priority 降序返回命中类别；**无关键词的结构性类别（如"群聊"）在 `chat_kind=="group"` 时自动命中**。
- UI（ui_app.py Settings Tab）可编辑/新增/删除/导入JSON/导出JSON，"保存并生效"调用 `engine.update_classification(cats)` 实时生效。
- **保存实现坑**：整文件 `yaml.safe_dump` 会抹掉注释。已改为正则仅替换 `    categories:` 子段（`_save_classification_rules`），保留 classification 头/ enabled 行/注释。
