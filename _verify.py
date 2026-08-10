import io
import py_compile
import sys

print('='*60)
print('验证1-4: 检查关键代码存在')
print('='*60)

# ===== 检查 red_dot_monitor.py =====
with io.open('red_dot_monitor.py', 'r', encoding='utf-8') as f:
    rdm = f.read()

print('\n--- red_dot_monitor.py ---')
all_pass = True

# 验证1: RIGHT_X_THRESHOLD 存在
check1 = 'RIGHT_X_THRESHOLD' in rdm
print(f'1. RIGHT_X_THRESHOLD 存在: {check1}')
all_pass = all_pass and check1
if check1:
    import re
    m = re.search(r'RIGHT_X_THRESHOLD = int\(w \* 0\.65\)', rdm)
    print(f'   计算式正确: {bool(m)}')
    all_pass = all_pass and bool(m)
    m2 = re.search(r'\[OCR检测\] 侧边栏w=\{w\}, 右侧阈值x>', rdm)
    print(f'   日志输出正确: {bool(m2)}')

# 验证2: '过滤纯数字' 日志存在
check2 = '过滤纯数字' in rdm
print(f'2. 过滤纯数字日志存在: {check2}')
all_pass = all_pass and check2
if check2:
    import re as _re2
    m2 = _re2.search(r"过滤纯数字'\{text\}': x=\{int\(x\)\}<\{RIGHT_X_THRESHOLD\}", rdm)
    print(f'   格式正确: {bool(m2)}')

# 验证3: 'HSV红点辅助补充' 存在(没有<2的条件)
check3a = 'HSV红点辅助补充' in rdm
check3b = 'len(unread_contacts) < 2' not in rdm
check3 = check3a and check3b
print(f'3. HSV红点辅助补充 存在(无<2条件): {check3}')
all_pass = all_pass and check3
print(f'   HSV红点辅助补充存在: {check3a}')
print(f'   无len<2条件: {check3b}')

# 额外检查其他修改点
print(f'\n   [附加] 规则B: match_b and x >= RIGHT_X_THRESHOLD: {"match_b and x >= RIGHT_X_THRESHOLD" in rdm}')
print(f'   [附加] 规则C: 过滤N条日志: {"过滤N条" in rdm}')
x1 = 'x < RIGHT_X_THRESHOLD:\n                    continue' in rdm
x2 = 'x < RIGHT_X_THRESHOLD:\n                continue' in rdm
print(f'   [附加] 规则D: x阈值continue: {x1 or x2}')

# ===== 检查 storage.py =====
with io.open('storage.py', 'r', encoding='utf-8') as f:
    stg = f.read()

print('\n--- storage.py ---')
# 验证4: isinstance dict 代码存在
check4 = 'isinstance(row["regex_extracts"], dict)' in stg
print(f'4. isinstance dict 代码存在: {check4}')
all_pass = all_pass and check4
if check4:
    check4b = 'isinstance(row["regex_extracts"], list)' in stg
    print(f'   同时支持list: {check4b}')
    all_pass = all_pass and check4b

print('\n' + '='*60)
print('验证5-6: py_compile 编译检查')
print('='*60)

# 验证5: py_compile red_dot_monitor.py
try:
    py_compile.compile('red_dot_monitor.py', doraise=True)
    print('5. py_compile red_dot_monitor.py: 通过 ✓')
    check5 = True
except py_compile.PyCompileError as e:
    print(f'5. py_compile red_dot_monitor.py: 失败 ✗')
    print(f'   错误: {e}')
    check5 = False
    all_pass = False

# 验证6: py_compile storage.py
try:
    py_compile.compile('storage.py', doraise=True)
    print('6. py_compile storage.py: 通过 ✓')
    check6 = True
except py_compile.PyCompileError as e:
    print(f'6. py_compile storage.py: 失败 ✗')
    print(f'   错误: {e}')
    check6 = False
    all_pass = False

print('\n' + '='*60)
print('验证总结')
print('='*60)
if all_pass:
    print('✅ 全部验证通过！')
else:
    print('❌ 部分验证未通过，请检查上方日志')
    sys.exit(1)
