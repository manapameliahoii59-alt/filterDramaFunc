# -*- coding: utf-8 -*-
import json
import re

# Read log from task-2869
log_path = r'C:\Users\Administrator\.gemini\antigravity-cli\brain\a68afd65-3ce8-4cb4-8ba5-f94097b95d1d\.system_generated\tasks\task-2869.log'
try:
    with open(log_path, 'r', encoding='utf-8') as f:
        log_content = f.read()
    # Match the json starting from line 2
    lines = log_content.splitlines()
    json_lines = [l for l in lines if not l.startswith('Tokens:')]
    json_str = '\n'.join(json_lines).strip()
    book1_json = json.loads(json_str)
    
    data = json.load(open('novel_dna_extraction_results.json', encoding='utf-8'))
    data[0]['api_result']['parsed_json'] = book1_json
    with open('novel_dna_extraction_results.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
except Exception as e:
    print("Merge note:", e)

# Print all 4 books nicely
data = json.load(open('novel_dna_extraction_results.json', encoding='utf-8'))
for idx, item in enumerate(data, 1):
    t = item['book_meta']['title']
    a = item['book_meta']['author']
    exp = item['book_meta']['gender_expected']
    pj = item['api_result']['parsed_json']
    print("=" * 70)
    print(f"[{idx}/4] 《{t}》（作者：{a}） - 预期: {exp}")
    print("=" * 70)
    if pj:
        print(json.dumps(pj, ensure_ascii=False, indent=2))
    print()
