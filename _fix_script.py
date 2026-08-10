import io

# ========== 修改 red_dot_monitor.py ==========
file_path = r'd:\下载\wechat-ai-reply-main\red_dot_monitor.py'
with io.open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

print('开始修改 red_dot_monitor.py...')

# 1A: h,w 计算后加RIGHT_X_THRESHOLD
old_1a = '''        h, w = image.shape[:2]

        # 1. 全量OCR'''
new_1a = '''        h, w = image.shape[:2]
        # 右侧区域阈值：未读红点在列表项右侧(x>65%w)，联系人在左侧(x<50%w)，预览在中间
        RIGHT_X_THRESHOLD = int(w * 0.65)
        logger.info(f"[OCR检测] 侧边栏w={w}, 右侧阈值x>{RIGHT_X_THRESHOLD}")

        # 1. 全量OCR'''
if old_1a in content:
    content = content.replace(old_1a, new_1a)
    print('1A 完成: RIGHT_X_THRESHOLD 已添加')
else:
    print('1A 失败: 未找到匹配代码')

# 1B: 规则A(纯数字) 加X校验
old_1b = '''            # 规则A: 纯数字 = 未读消息数 (如 "8", "99+")
            if self._is_unread_number(text):
                contact = self._find_nearest_contact_above(ocr_results, i)'''
new_1b = '''            # 规则A: 纯数字 = 未读消息数 (如 "8", "99+")
            if self._is_unread_number(text):
                if x < RIGHT_X_THRESHOLD:
                    logger.info(f"[OCR检测] 过滤纯数字'{text}': x={int(x)}<{RIGHT_X_THRESHOLD}")
                    continue
                contact = self._find_nearest_contact_above(ocr_results, i)'''
if old_1b in content:
    content = content.replace(old_1b, new_1b)
    print('1B 完成: 规则A 纯数字X校验已添加')
else:
    print('1B 失败: 未找到匹配代码')

# 1C: 规则B([N条]) — 重写整个if分支
old_1c = '''            # 规则B: [N条]格式 (如 "[9条]宝泉景区:河南宝泉旅")
            match_b = re.search(r'\[(\d+)条\]', text)
            if match_b:
                count = match_b.group(1)
                # 联系人名在[条]后面的文字
                name_match = re.search(r'\](.+)', text)
                contact_name = name_match.group(1).strip()[:20] if name_match else f"未读_{int(y)}"
                if contact_name and len(contact_name) >= 2:
                    unread_contacts.append({
                        "contact": contact_name,
                        "red_dot_y": int(y),
                        "red_dot_x": int(x),
                        "confidence": r.get("confidence", 0),
                        "unread_count": count,
                        "method": "ocr_bracket",
                    })
                    used_indices.add(i)
                    logger.info(f"[OCR检测] 未读([N条]): '{contact_name}' 数量={count} y={int(y)}")
                    continue'''
new_1c = '''            # 规则B: [N条]格式 + X必须在右侧 + 联系人从上方找(不能用同行预览文字)
            match_b = re.search(r'\[(\d+)条\]', text)
            if match_b and x >= RIGHT_X_THRESHOLD:
                count = match_b.group(1)
                contact = self._find_nearest_contact_above(ocr_results, i)
                contact_name = contact["text"] if contact else None
                if contact_name and len(contact_name) >= 2:
                    unread_contacts.append({
                        "contact": contact_name,
                        "red_dot_y": int(y),
                        "red_dot_x": int(x),
                        "confidence": r.get("confidence", 0),
                        "unread_count": count,
                        "method": "ocr_bracket",
                    })
                    used_indices.add(i)
                    logger.info(f"[OCR检测] 未读([N条]): '{contact_name}' 数量={count} y={int(y)}")
                    continue'''
if old_1c in content:
    content = content.replace(old_1c, new_1c)
    print('1C 完成: 规则B [N条]已重写')
else:
    print('1C 失败: 未找到匹配代码')

# 1D: 规则C(N条) 加X校验
old_1d = '''            # 规则C: N条 格式 (如 "3条" 独立出现)
            match_c = re.match(r'^(\d+)\s*条', text)
            if match_c:
                count = match_c.group(1)'''
new_1d = '''            # 规则C: N条 格式 (如 "3条" 独立出现)
            match_c = re.match(r'^(\d+)\s*条', text)
            if match_c:
                if x < RIGHT_X_THRESHOLD:
                    logger.info(f"[OCR检测] 过滤N条'{text}': x={int(x)}<{RIGHT_X_THRESHOLD}")
                    continue
                count = match_c.group(1)'''
if old_1d in content:
    content = content.replace(old_1d, new_1d)
    print('1D 完成: 规则C N条X校验已添加')
else:
    print('1D 失败: 未找到匹配代码')

# 1E: 规则D(行尾数字) 加X校验
old_1e = '''                # 排除时间格式
                if not re.search(r'\d{1,2}[:：]\d{2}', name):'''
new_1e = '''                if x < RIGHT_X_THRESHOLD:
                    continue
                # 排除时间格式
                if not re.search(r'\d{1,2}[:：]\d{2}', name):'''
if old_1e in content:
    content = content.replace(old_1e, new_1e)
    print('1E 完成: 规则D 行尾数字X校验已添加')
else:
    print('1E 失败: 未找到匹配代码')

# 1F: HSV辅助始终执行
old_1f = '''        # 3. HSV红点辅助:OCR检测不到的未读,用红点位置补充
        if len(unread_contacts) < 2:
            logger.info(f"[OCR检测] OCR只找到{len(unread_contacts)}个未读,启用HSV红点辅助检测")
            red_dots = self.detect_red_dots(image)
            for dot in red_dots:
                dot_y = dot["center_y"]
                dot_x = dot["center_x"]
                # 检查这个红点是否已被OCR检测到(Y坐标接近)
                already_found = any(abs(u["red_dot_y"] - dot_y) < 30 for u in unread_contacts)
                if not already_found:
                    # 用HSV红点的Y坐标找上方联系人名
                    contact = None
                    for j, r2 in enumerate(ocr_results):
                        if j in used_indices:
                            continue
                        t2 = r2.get("text", "").strip()
                        y2 = r2.get("y_center", 0)
                        if abs(y2 - dot_y) <= 40 and not t2.isdigit() and len(t2) >= 2:
                            if not re.match(r'\d{1,2}[:：]\d{2}', t2):
                                if not re.match(r'昨天|今天|星期|周[一二三四五六日]', t2):
                                    if not re.search(r'\[.*条\]', t2):
                                        contact = t2
                                        break
                    contact_name = contact if contact else f"未读_{dot_y}"
                    unread_contacts.append({
                        "contact": contact_name,
                        "red_dot_y": dot_y,
                        "red_dot_x": dot_x,
                        "confidence": 0,
                        "unread_count": "?",
                        "method": "hsv_assist",
                    })
                    logger.info(f"[OCR检测] HSV辅助: '{contact_name}' y={dot_y}")'''
new_1f = '''        # 3. HSV红点辅助：始终执行(不只<2个)，补充OCR漏识别的小点(如"亚磊 1")
        logger.info(f"[OCR检测] OCR找到{len(unread_contacts)}个未读，HSV红点辅助补充")
        red_dots = self.detect_red_dots(image)
        for dot in red_dots:
            dot_y = dot["center_y"]
            dot_x = dot["center_x"]
            # 检查这个红点是否已被OCR检测到(Y坐标接近)
            already_found = any(abs(u["red_dot_y"] - dot_y) < 30 for u in unread_contacts)
            if not already_found:
                # 用HSV红点的Y坐标找上方联系人名
                contact = None
                for j, r2 in enumerate(ocr_results):
                    if j in used_indices:
                        continue
                    t2 = r2.get("text", "").strip()
                    y2 = r2.get("y_center", 0)
                    if abs(y2 - dot_y) <= 40 and not t2.isdigit() and len(t2) >= 2:
                        if not re.match(r'\d{1,2}[:：]\d{2}', t2):
                            if not re.match(r'昨天|今天|星期|周[一二三四五六日]', t2):
                                if not re.search(r'\[.*条\]', t2):
                                    contact = t2
                                    break
                contact_name = contact if contact else f"未读_{dot_y}"
                unread_contacts.append({
                    "contact": contact_name,
                    "red_dot_y": dot_y,
                    "red_dot_x": dot_x,
                    "confidence": 0,
                    "unread_count": "?",
                    "method": "hsv_assist",
                })
                logger.info(f"[OCR检测] HSV辅助: '{contact_name}' y={dot_y}")'''
if old_1f in content:
    content = content.replace(old_1f, new_1f)
    print('1F 完成: HSV辅助始终执行(删除<2条件)')
else:
    print('1F 失败: 未找到匹配代码')
    # 打印一下周围内容看看
    import re as _re
    idx = content.find('HSV红点辅助')
    if idx >= 0:
        print('  找到 HSV红点辅助 位置:')
        print(content[idx:idx+300])

with io.open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)
print('red_dot_monitor.py 写入完成')

# ========== 修改 storage.py ==========
file_path2 = r'd:\下载\wechat-ai-reply-main\storage.py'
with io.open(file_path2, 'r', encoding='utf-8') as f:
    content2 = f.read()

print('\n开始修改 storage.py...')

old_storage = '''                row_regex = " | ".join(row["regex_extracts"]) if row["regex_extracts"] else ""'''
new_storage = '''                if row["regex_extracts"] and isinstance(row["regex_extracts"], dict):
                    row_regex = " | ".join(f"{k}: {v}" for k, v in row["regex_extracts"].items() if v)
                elif row["regex_extracts"] and isinstance(row["regex_extracts"], list):
                    row_regex = " | ".join(str(x) for x in row["regex_extracts"])
                else:
                    row_regex = ""'''

if old_storage in content2:
    content2 = content2.replace(old_storage, new_storage)
    print('storage.py: row_regex 已替换为 dict/list 兼容版本')
else:
    print('storage.py: 未找到精确匹配的 old_storage，尝试查找其他位置...')
    lines = content2.split('\n')
    found = False
    for i, line in enumerate(lines):
        if 'regex_extracts' in line and 'join' in line and 'row_regex' in line:
            print(f'  找到类似代码 行{i+1}: {line.strip()}')
            # 用这个行替换
            indent = len(line) - len(line.lstrip())
            lines[i] = ' ' * indent + 'if row["regex_extracts"] and isinstance(row["regex_extracts"], dict):\n'
            lines[i] += ' ' * (indent + 4) + 'row_regex = " | ".join(f"{k}: {v}" for k, v in row["regex_extracts"].items() if v)\n'
            lines[i] += ' ' * indent + 'elif row["regex_extracts"] and isinstance(row["regex_extracts"], list):\n'
            lines[i] += ' ' * (indent + 4) + 'row_regex = " | ".join(str(x) for x in row["regex_extracts"])\n'
            lines[i] += ' ' * indent + 'else:\n'
            lines[i] += ' ' * (indent + 4) + 'row_regex = ""'
            found = True
            content2 = '\n'.join(lines)
            break
    if not found:
        print('  完全未找到相关代码，跳过 storage.py 修改')

with io.open(file_path2, 'w', encoding='utf-8') as f:
    f.write(content2)
print('storage.py 写入完成')
print('\n全部修改完成！')
