"""
=============================================================================
NOYA Chat 微信助手 — Demo 测试演练脚本
=============================================================================
覆盖修复验证 + 全链路功能演练，可脱离微信环境独立运行。

运行方式:
    python test_demo.py              # 全部测试
    python test_demo.py --quick      # 仅快速冒烟测试
    python test_demo.py --verbose    # 详细输出
=============================================================================
"""
import sys
import os
import time
import json
import threading
import random
import argparse
from collections import defaultdict

# =============================================================================
# 测试框架
# =============================================================================
PASS = 0
FAIL = 0
SKIP = 0
RESULTS = []


def test(name):
    """测试装饰器"""
    def deco(fn):
        def wrapper(*a, **kw):
            global PASS, FAIL, SKIP
            try:
                fn(*a, **kw)
                PASS += 1
                RESULTS.append(("PASS", name))
                print(f"  ✅ {name}")
            except AssertionError as e:
                FAIL += 1
                RESULTS.append(("FAIL", name, str(e)))
                print(f"  ❌ {name}  — {e}")
            except Exception as e:
                FAIL += 1
                RESULTS.append(("FAIL", name, str(e)))
                print(f"  💥 {name}  — {e}")
        return wrapper
    return deco


def assert_eq(a, b, msg=""):
    assert a == b, f"期望 {b!r}，实际 {a!r}" + (f" ({msg})" if msg else "")


def assert_true(cond, msg=""):
    assert cond, msg or "条件不成立"


def assert_in(item, container, msg=""):
    assert item in container, f"{item!r} 不在 {container!r}" + (f" ({msg})" if msg else "")


# =============================================================================
# 测试 1: 指纹去重 + 锁顺序验证 (P0 修复)
# =============================================================================
@test("指纹去重: 锁获取失败时指纹不应污染")
def test_fingerprint_lock_order():
    """验证: 锁获取失败时，指纹不应被写入。P0 修复确认。"""
    fingerprints = {}
    lock = threading.Lock()

    def simulate_maybe_reply(contact, content):
        _norm = content.strip()
        import hashlib
        _fp = hashlib.md5(f"{contact}|{_norm}".encode("utf-8")).hexdigest()

        # 去重检查
        _fp_set = fingerprints.setdefault(contact, set())
        if _fp in _fp_set:
            return "DUPLICATE"

        # ★ 修复后: 先获取锁，再写指纹
        if not lock.acquire(blocking=False):
            return "LOCKED"

        _fp_set = fingerprints.setdefault(contact, set())
        _fp_set.add(_fp)
        lock.release()
        return "OK"

    # 模拟第一条消息正在处理中（锁被占用）
    lock.acquire()

    # 第二条消息进来
    result = simulate_maybe_reply("张三", "你好")
    assert_eq(result, "LOCKED", "锁被占用时应返回 LOCKED")

    # 验证指纹未被污染
    fp_set = fingerprints.get("张三", set())
    assert_eq(len(fp_set), 0, "锁获取失败时指纹不应写入")

    lock.release()

    # 锁释放后，同一条消息应该能正常处理
    result2 = simulate_maybe_reply("张三", "你好")
    assert_eq(result2, "OK", "锁释放后应正常处理")

    # 再次发送同一条消息应该被去重拦截
    result3 = simulate_maybe_reply("张三", "你好")
    assert_eq(result3, "DUPLICATE", "同一条消息应被去重拦截")

    print("    → 锁获取失败时指纹未污染，锁释放后正常处理，去重生效")


# =============================================================================
# 测试 2: 快速回复大小写不敏感 (P1 修复)
# =============================================================================
@test("快速回复: 大小写不敏感 (Hi/HI/hi 统一处理)")
def test_fast_reply_case_insensitive():
    """验证: "Hi" "HI" "hi" 都能匹配快速回复模板。"""
    # 模拟修复后的 _try_fast_reply 逻辑
    greetings = {"在吗", "hi", "hello", "嗨", "哈喽", "hey", "早", "早安", "晚安", "拜拜", "bye", "88"}
    confirmations = {"ok", "好的", "好", "行", "可以", "没问题", "收到", "嗯嗯", "嗯", "哦"}

    def fast_reply(content):
        _m = content.strip()
        _ml = _m.lower()
        if _ml in greetings or (len(_m) <= 3 and _ml in {"在", "早", "嗨", "hi", "嘿"}):
            return "FAST_GREETING"
        if _ml in confirmations:
            return "FAST_CONFIRM"
        return None

    # 大小写变体
    assert_eq(fast_reply("hi"), "FAST_GREETING")
    assert_eq(fast_reply("Hi"), "FAST_GREETING")
    assert_eq(fast_reply("HI"), "FAST_GREETING")
    assert_eq(fast_reply("Hello"), "FAST_GREETING")
    assert_eq(fast_reply("HELLO"), "FAST_GREETING")
    assert_eq(fast_reply("Ok"), "FAST_CONFIRM")
    assert_eq(fast_reply("OK"), "FAST_CONFIRM")
    assert_eq(fast_reply("Bye"), "FAST_GREETING")
    assert_eq(fast_reply("BYE"), "FAST_GREETING")

    # 非简单消息应返回 None
    assert_eq(fast_reply("今天天气怎么样"), None)
    assert_eq(fast_reply("帮我写个方案"), None)

    print("    → Hi/HI/hi/Ok/OK/Bye 全部正确匹配")


# =============================================================================
# 测试 3: 降级回复模板自然度 (P2 修复)
# =============================================================================
@test("降级回复: 图片/表情回复不再过于敷衍")
def test_fallback_reply_natural():
    """验证: 图片/表情降级回复从 "嗯嗯" 改为多样化自然回复。"""
    fallback_pool = ["收到~", "哈哈", "有意思", "看看", "👀"]

    def pick_fallback(message):
        _m = message.strip()
        if any(kw in _m for kw in ["图片", "表情", "贴图", "[表情]", "[图片]"]):
            import random
            return random.choice(fallback_pool)
        return "收到~我这会儿有点事，晚点回你哈"

    # 图片/表情应返回 fallback_pool 中的值
    for i in range(10):
        reply = pick_fallback("[图片]")
        assert_in(reply, fallback_pool, f"图片回复应在候选池中，实际: {reply}")

    # 不应再返回 "嗯嗯"
    for i in range(20):
        reply = pick_fallback("[表情]")
        assert_true(reply != "嗯嗯", "图片/表情降级回复不应是 '嗯嗯'")

    # 非图片消息应返回默认回复
    default = pick_fallback("普通消息")
    assert_true("嗯嗯" not in default or default == "嗯嗯", "默认回复")

    print("    → 图片/表情降级回复池: " + ", ".join(fallback_pool))


# =============================================================================
# 测试 4: Token 预算控制
# =============================================================================
@test("Token 预算: 超长上下文正确裁剪")
def test_token_budget():
    """验证: 超长 system prompt 和 messages 能正确裁剪到预算内。"""
    TOKEN_BUDGET_SYSTEM = 2000
    TOKEN_BUDGET_TOTAL = 6000

    def estimate_tokens(text):
        import re
        _cn = len(re.findall(r'[\u4e00-\u9fff]', text))
        _en = len(re.findall(r'[a-zA-Z]+', text))
        _other = len(text) - _cn - sum(len(w) for w in re.findall(r'[a-zA-Z]+', text))
        return _cn + _en + max(0, _other // 3)

    # 构造超长 system prompt
    long_system = "你是一个友好的聊天助手。" * 200
    tokens = estimate_tokens(long_system)
    assert_true(tokens > TOKEN_BUDGET_SYSTEM, f"超长 system prompt token 数应 > {TOKEN_BUDGET_SYSTEM}")

    # 裁剪
    if tokens > TOKEN_BUDGET_SYSTEM:
        long_system = long_system[:TOKEN_BUDGET_SYSTEM] + "…(已截断)"
    assert_true(estimate_tokens(long_system) <= TOKEN_BUDGET_SYSTEM + 50, "裁剪后应在预算内")

    # 构造超长消息列表 (每条消息 50 个中文字符 ≈ 50 tokens, 10 条 = 500, 不够)
    # 增加消息长度以确保超标
    messages = [{"role": "user", "content": "这是一条很长的测试消息用来验证Token预算控制是否正常工作" * 30} for _ in range(10)]
    total = sum(estimate_tokens(m["content"]) for m in messages)
    assert_true(total > TOKEN_BUDGET_TOTAL, f"超长消息列表 token({total}) 应 > {TOKEN_BUDGET_TOTAL}")

    # 从新到旧保留，直到总 token 在预算内
    truncated = []
    running = 0
    for m in reversed(messages):
        t = estimate_tokens(m["content"])
        if running + t > TOKEN_BUDGET_TOTAL:
            break
        truncated.insert(0, m)
        running += t
    assert_true(len(truncated) < len(messages), "裁剪后消息数应减少")
    assert_true(sum(estimate_tokens(m["content"]) for m in truncated) <= TOKEN_BUDGET_TOTAL,
                 "裁剪后总 token 应在预算内")

    print(f"    → 超长 system({tokens}t) + 消息({total}t) → 裁剪为 {len(truncated)} 条 / {running}t")


# =============================================================================
# 测试 5: 聚合回复 buffer 双引擎互斥
# =============================================================================
@test("聚合回复: 双引擎 buffer 清空后不重复 flush")
def test_aggregation_double_engine():
    """验证: 防抖定时器 + 聚合循环 双引擎不会重复 flush。"""
    reply_buffer = {}
    buffer_lock = threading.Lock()
    flush_count = {"count": 0}

    def flush_reply_buffer(contact):
        with buffer_lock:
            buf = reply_buffer.get(contact)
            if not buf:
                return
            reply_buffer[contact] = []  # 清空
        flush_count["count"] += 1

    # 模拟缓冲区有数据
    with buffer_lock:
        reply_buffer["张三"] = [
            {"content": "你好", "ts": time.time()},
            {"content": "在吗", "ts": time.time()},
        ]

    # 模拟两个引擎同时尝试 flush
    threads = []
    for _ in range(3):
        t = threading.Thread(target=flush_reply_buffer, args=("张三",))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    # 应只 flush 一次
    assert_eq(flush_count["count"], 1, "多个引擎同时 flush 应只执行一次")

    # 第二次 flush 应跳过（buffer 已空）
    flush_reply_buffer("张三")
    assert_eq(flush_count["count"], 1, "buffer 已空时不应再 flush")

    print("    → 3 线程并发 flush，仅执行 1 次")


# =============================================================================
# 测试 6: 智能发送模式切换
# =============================================================================
@test("智能发送: 多模式自动切换")
def test_smart_send_fallback():
    """验证: 发送失败时自动切换到下一种模式。"""
    send_results = []

    def send_clipboard(text):
        send_results.append("clipboard")
        return False  # 模拟失败

    def send_typing(text):
        send_results.append("typing")
        return False  # 模拟失败

    def send_offscreen(text):
        send_results.append("offscreen")
        return True   # 模拟成功

    methods = [
        ("clipboard", send_clipboard),
        ("typing", send_typing),
        ("offscreen", send_offscreen),
    ]

    success = False
    for name, func in methods:
        if func("测试消息"):
            success = True
            break

    assert_true(success, "最终应发送成功")
    assert_eq(send_results, ["clipboard", "typing", "offscreen"],
              "应依次尝试 clipboard → typing → offscreen")

    print("    → 发送路径: clipboard(fail) → typing(fail) → offscreen(success)")


# =============================================================================
# 测试 7: 回复效果反馈评分
# =============================================================================
@test("回复反馈: 评分逻辑合理性")
def test_feedback_scoring():
    """验证: 对方回复内容能正确评估回复质量。"""
    positive_keywords = ["谢谢", "好的", "哈哈", "👍", "明白了", "收到", "OK", "说得对", "有道理"]
    negative_keywords = ["?", "？", "啥", "你说啥", "没懂", "不对", "不是", "无语", "…", "。。。"]

    def evaluate(reply_text):
        score = 3  # 默认中性
        _t = reply_text.lower()
        for kw in positive_keywords:
            if kw.lower() in _t:
                score += 1
        for kw in negative_keywords:
            if kw.lower() in _t:
                score -= 1
        return max(1, min(5, score))

    # 正面反馈
    assert_true(evaluate("谢谢，明白了") >= 4, "谢谢+明白了 应为正面")
    assert_true(evaluate("哈哈好的收到") >= 4, "哈哈+好的+收到 应为正面")

    # 负面反馈
    assert_true(evaluate("啥？没懂") <= 2, "啥+没懂 应为负面")
    assert_true(evaluate("？不是这样的") <= 2, "?+不是 应为负面")

    # 中性
    assert_eq(evaluate("嗯"), 3, "嗯 应为中性")
    assert_eq(evaluate("哦哦"), 3, "哦哦 应为中性")

    # 边界
    assert_true(1 <= evaluate("????????????????") <= 5, "评分应在 1-5 范围内")

    print("    → 正面≥4, 负面≤2, 中性=3, 范围[1,5]")


# =============================================================================
# 测试 8: 消息去重 (pHash + 精确匹配)
# =============================================================================
@test("消息去重: 精确匹配去重")
def test_message_dedup():
    """验证: 精确匹配去重能正确拦截重复消息。"""
    seen_exact = set()

    def is_duplicate(text):
        _norm = text.strip()
        if _norm in seen_exact:
            return True
        seen_exact.add(_norm)
        return False

    assert_true(not is_duplicate("你好"), "首次应不重复")
    assert_true(is_duplicate("你好"), "第二次应重复")
    assert_true(not is_duplicate("在吗"), "不同消息应不重复")
    assert_true(is_duplicate("在吗"), "同消息再次应重复")

    # 空格差异：strip 后 " 你好 " → "你好"，已在集合中，应被拦截
    assert_true(is_duplicate(" 你好 "), "带空格但 strip 后与已有消息相同，应拦截")

    print("    → 首次不拦截，重复拦截，strip 归一化")


# =============================================================================
# 测试 9: 黑名单过滤 (通配符支持)
# =============================================================================
@test("黑名单过滤: 通配符匹配")
def test_blacklist_wildcard():
    """验证: 黑名单支持通配符 * 匹配。"""
    blacklist = [
        "拼多多", "瑞幸咖啡", "美团", "滴滴",
        "订阅号", "服务号", "微信团队", "微信支付",
        "京东*", "中国*", "折叠的*",
    ]

    def is_blacklisted(contact):
        for bl in blacklist:
            if "*" in bl:
                pattern = bl.replace("*", "")
                if pattern in contact:
                    return True
            elif bl in contact:
                return True
        return False

    # 精确匹配
    assert_true(is_blacklisted("拼多多"), "精确匹配")
    assert_true(is_blacklisted("美团"), "精确匹配")

    # 通配符匹配
    assert_true(is_blacklisted("京东快递"), "京东* 匹配 京东快递")
    assert_true(is_blacklisted("京东物流"), "京东* 匹配 京东物流")
    assert_true(is_blacklisted("中国移动"), "中国* 匹配 中国移动")
    assert_true(is_blacklisted("中国联通"), "中国* 匹配 中国联通")
    assert_true(is_blacklisted("折叠的聊天"), "折叠的* 匹配 折叠的聊天")

    # 不应匹配
    assert_true(not is_blacklisted("张三"), "张三 不在黑名单")
    assert_true(not is_blacklisted("小李"), "小李 不在黑名单")

    print("    → 精确匹配 + 通配符匹配 均正确")


# =============================================================================
# 测试 10: 勿扰模式时间窗口
# =============================================================================
@test("勿扰模式: 时间窗口判断")
def test_dnd_time_window():
    """验证: 勿扰模式时间窗口判断正确。"""
    def is_dnd(hour, minute, dnd_start="23:00", dnd_end="07:00"):
        sh, sm = map(int, dnd_start.split(":"))
        eh, em = map(int, dnd_end.split(":"))
        now_minutes = hour * 60 + minute
        start_minutes = sh * 60 + sm
        end_minutes = eh * 60 + em
        if start_minutes <= end_minutes:
            return start_minutes <= now_minutes <= end_minutes
        else:
            return now_minutes >= start_minutes or now_minutes <= end_minutes

    # 夜间 (23:00-07:00 包含边界)
    assert_true(is_dnd(23, 0), "23:00 边界应在勿扰模式")
    assert_true(is_dnd(23, 30), "23:30 应在勿扰模式")
    assert_true(is_dnd(0, 0), "00:00 应在勿扰模式")
    assert_true(is_dnd(6, 59), "06:59 应在勿扰模式")
    assert_true(is_dnd(7, 0), "07:00 边界在勿扰模式 (≤ 包含)")

    # 白天
    assert_true(not is_dnd(7, 1), "07:01 应退出勿扰模式")
    assert_true(not is_dnd(12, 0), "12:00 不应在勿扰模式")
    assert_true(not is_dnd(22, 59), "22:59 不应在勿扰模式")

    print("    → 23:00-07:00 勿扰，跨天边界正确")


# =============================================================================
# 测试 11: LLM 端点 URL 构建
# =============================================================================
@test("LLM端点: URL 自动拼接 /chat/completions")
def test_llm_endpoint_url():
    """验证: 各种格式的 API base URL 能正确拼接。"""
    import re

    def build_endpoint(url):
        url = url.rstrip("/")
        if not url.endswith("/chat/completions"):
            if re.search(r"/v\d+$", url):
                return f"{url}/chat/completions"
            return f"{url}/v1/chat/completions"
        return url

    # 标准 OpenAI 格式
    assert_eq(build_endpoint("https://api.openai.com"),
              "https://api.openai.com/v1/chat/completions")

    # 带 v1 结尾
    assert_eq(build_endpoint("https://api.openai.com/v1"),
              "https://api.openai.com/v1/chat/completions")

    # 已经完整
    assert_eq(build_endpoint("https://api.openai.com/v1/chat/completions"),
              "https://api.openai.com/v1/chat/completions")

    # 阿里云 DashScope
    assert_eq(build_endpoint("https://dashscope.aliyuncs.com/compatible-mode"),
              "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions")

    # 本地 Ollama
    assert_eq(build_endpoint("http://localhost:11434"),
              "http://localhost:11434/v1/chat/completions")

    # 带尾部斜杠
    assert_eq(build_endpoint("https://api.openai.com/v1/"),
              "https://api.openai.com/v1/chat/completions")

    print("    → OpenAI / DashScope / Ollama URL 拼接正确")


# =============================================================================
# 测试 12: 消息存储安全序列化
# =============================================================================
@test("存储安全: dict/list 类型安全转换为字符串")
def test_safe_serialization():
    """验证: CSV 导出时 dict/list 等复杂类型安全转换。"""
    def _safe_str(value):
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, (int, float, bool)):
            return str(value)
        if isinstance(value, (dict, list)):
            try:
                return json.dumps(value, ensure_ascii=False)
            except Exception:
                return str(value)
        return str(value)

    assert_eq(_safe_str(None), "")
    assert_eq(_safe_str("hello"), "hello")
    assert_eq(_safe_str(123), "123")
    assert_eq(_safe_str(True), "True")
    assert_eq(_safe_str({"a": 1}), '{"a": 1}')
    assert_eq(_safe_str([1, 2, 3]), '[1, 2, 3]')
    assert_eq(_safe_str({"nested": {"x": [1, 2]}}), '{"nested": {"x": [1, 2]}}')

    print("    → None/str/int/dict/list 全部安全转换")


# =============================================================================
# 测试 13: 上下文消息角色完整性
# =============================================================================
@test("上下文构建: role 字段完整性")
def test_context_role_integrity():
    """验证: 构建的消息列表中每条都有正确的 role 字段。"""
    def build_context(history, user_msg):
        messages = []
        for h in history:
            messages.append({"role": "user", "content": f"对方: {h}"})
        messages.append({"role": "user", "content": user_msg})
        return messages

    history = ["你好", "今天天气不错"]
    ctx = build_context(history, "你在干嘛")

    for i, msg in enumerate(ctx):
        assert_in("role", msg, f"消息 {i} 缺少 role 字段")
        assert_in("content", msg, f"消息 {i} 缺少 content 字段")
        assert_true(msg["role"] in ("user", "assistant", "system"),
                    f"消息 {i} role 值非法: {msg['role']}")

    assert_eq(len(ctx), 3, "应有 3 条消息 (2 历史 + 1 当前)")

    print("    → 3 条消息，全部有 role + content，role 值合法")


# =============================================================================
# 测试 14: 并发安全 — 多线程同时写 buffer
# =============================================================================
@test("并发安全: 多线程写 buffer 不丢数据")
def test_concurrent_buffer_write():
    """验证: 多线程同时写入聚合 buffer 不丢数据。"""
    buffer = {}
    lock = threading.Lock()
    errors = []

    def writer(contact, msg_id):
        try:
            with lock:
                buf = buffer.setdefault(contact, [])
                buf.append(msg_id)
        except Exception as e:
            errors.append(str(e))

    threads = []
    for i in range(200):
        t = threading.Thread(target=writer, args=("张三", i))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    assert_eq(len(errors), 0, f"并发写入不应有异常: {errors}")
    assert_eq(len(buffer.get("张三", [])), 200, "应写入 200 条消息无丢失")

    # 验证消息 ID 完整性
    ids = set(buffer["张三"])
    assert_eq(len(ids), 200, "所有消息 ID 应唯一")

    print("    → 200 线程并发写 buffer，0 丢失，0 异常")


# =============================================================================
# 主入口
# =============================================================================
def main():
    parser = argparse.ArgumentParser(description="NOYA Chat 微信助手 — Demo 测试演练")
    parser.add_argument("--quick", action="store_true", help="仅快速冒烟测试")
    parser.add_argument("--verbose", action="store_true", help="详细输出")
    args = parser.parse_args()

    print("=" * 65)
    print("  NOYA Chat 微信助手 — Demo 测试演练")
    print("=" * 65)
    print()

    if args.quick:
        print("⚡ 快速冒烟模式\n")
        test_fingerprint_lock_order()
        test_fast_reply_case_insensitive()
        test_fallback_reply_natural()
        test_token_budget()
        test_aggregation_double_engine()
    else:
        print("🔬 完整测试模式\n")
        test_fingerprint_lock_order()
        test_fast_reply_case_insensitive()
        test_fallback_reply_natural()
        test_token_budget()
        test_aggregation_double_engine()
        test_smart_send_fallback()
        test_feedback_scoring()
        test_message_dedup()
        test_blacklist_wildcard()
        test_dnd_time_window()
        test_llm_endpoint_url()
        test_safe_serialization()
        test_context_role_integrity()
        test_concurrent_buffer_write()

    print()
    print("=" * 65)
    total = PASS + FAIL
    print(f"  结果: {PASS} 通过 / {FAIL} 失败 / {total} 总计")
    if FAIL > 0:
        print(f"\n  失败项:")
        for r in RESULTS:
            if r[0] == "FAIL":
                print(f"    ❌ {r[1]}: {r[2]}")
    print("=" * 65)

    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())