import json
import os

with open("ab_test_deepseek_results.json", "r", encoding="utf-8") as f:
    data = json.load(f)

for i, d in enumerate(data, 1):
    title = d['book_info']['title']
    print(f"==================================================")
    print(f"Book {i}: {title}")
    print(f"==================================================")
    
    # Check Plan A
    pa = d['plan_a']
    print(f"[Plan A Content ({len(pa['content'])} chars)]:")
    print(pa['content'][:300] + "...\n")
    
    # Check Plan B
    pb = d['plan_b']
    print(f"[Plan B Content ({len(pb['content'])} chars)]:")
    print(pb['content'][:300] + "...\n")
