#!/usr/bin/env python3
import json, os, glob

USER_INFO = {
    "{{name}}": "Jessica Morgan",
    "{{email}}": "jessica.morgan@yahoo.com",
    "{{street}}": "Maple Avenue",
    "{{house_number}}": "742",
    "{{zip}}": "60614",
    "{{city}}": "Chicago",
    "{{state}}": "IL",
    "{{country}}": "USA",
    "{{card}}": "4242424242424242",
    "{{cvv}}": "123",
    "{{expiry_date}}": "12/28",
}

task_dir = os.path.dirname(os.path.abspath(__file__))
for fp in glob.glob(os.path.join(task_dir, "*.json")):
    try:
        data = json.load(open(fp))
    except:
        continue
    if data.get("task_tag") not in ["EndtoEnd", "Checkout"]:
        continue
    if "instruction" not in data:
        continue
    instr = data["instruction"]
    orig = instr
    for k, v in USER_INFO.items():
        instr = instr.replace(k, v)
    if instr != orig:
        data["instruction"] = instr
        json.dump(data, open(fp, "w"), indent=4, ensure_ascii=False)
        print(f"Updated: {os.path.basename(fp)}")
print("Done")
