import io

file_path = r'd:\下载\wechat-ai-reply-main\storage.py'
with io.open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# 找到原来处理 regex_extracts 的代码块并替换
old_code = '''                    regex_parts = []
                    for k, v in row["regex_extracts"].items():
                        if v:
                            val_str = ", ".join(v) if isinstance(v, list) else str(v)
                            regex_parts.append(f"{k}: {val_str}")
                    regex_str = " | ".join(regex_parts)'''

new_code = '''                    if row["regex_extracts"] and isinstance(row["regex_extracts"], dict):
                        regex_parts = []
                        for k, v in row["regex_extracts"].items():
                            if v:
                                val_str = ", ".join(v) if isinstance(v, list) else str(v)
                                regex_parts.append(f"{k}: {val_str}")
                        regex_str = " | ".join(regex_parts)
                    elif row["regex_extracts"] and isinstance(row["regex_extracts"], list):
                        regex_str = " | ".join(str(x) for x in row["regex_extracts"])
                    else:
                        regex_str = ""'''

if old_code in content:
    content = content.replace(old_code, new_code)
    print('storage.py: regex_extracts 处理代码已替换为 dict/list 兼容版本')
else:
    print('storage.py: 未找到匹配的旧代码，检查内容...')
    idx = content.find('regex_parts = []')
    if idx >= 0:
        print('  找到 regex_parts = [] 在位置', idx)
        print('  上下文:')
        print(content[idx-50:idx+400])
    else:
        print('  完全找不到 regex_parts = []')

with io.open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)
print('storage.py 写入完成')
