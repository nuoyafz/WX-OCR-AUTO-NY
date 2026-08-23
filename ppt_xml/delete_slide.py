import json
import subprocess
import sys

def delete_slide(presentation_id, slide_id):
    params = json.dumps({"xml_presentation_id": presentation_id, "slide_id": slide_id}, ensure_ascii=False)
    result = subprocess.run(
        ["lark-cli", "slides", "xml_presentation.slide", "delete", "--params", params],
        capture_output=True, text=True, encoding='utf-8'
    )
    print(result.stdout)
    if result.stderr:
        print("STDERR:", result.stderr, file=sys.stderr)
    return result.returncode

if __name__ == "__main__":
    sys.exit(delete_slide(sys.argv[1], sys.argv[2]))
