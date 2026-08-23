import json
import subprocess
import sys

def create_slide(presentation_id, xml_file):
    with open(xml_file, 'r', encoding='utf-8') as f:
        content = f.read()
    data = json.dumps({"slide": {"content": content}}, ensure_ascii=False)
    params = json.dumps({"xml_presentation_id": presentation_id}, ensure_ascii=False)
    result = subprocess.run(
        ["lark-cli", "slides", "xml_presentation.slide", "create",
         "--params", params, "--data", data],
        capture_output=True, text=True, encoding='utf-8'
    )
    print(result.stdout)
    if result.stderr:
        print("STDERR:", result.stderr, file=sys.stderr)
    return result.returncode

if __name__ == "__main__":
    pid = sys.argv[1]
    xmlf = sys.argv[2]
    sys.exit(create_slide(pid, xmlf))
