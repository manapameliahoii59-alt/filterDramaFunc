# -*- coding: utf-8 -*-
"""
test_novel_dna_extraction.py
使用 DeepSeek-V4.1-Flash (deepseek-flash) 官方 API
仅输入【书名 + 作者】，测试 6 大黄金维度 + 12~18 个分层热词（L1 宏观 + L2 微观）的标准 JSON 抽取
涵盖 2 部男频 + 2 部女频代表作
"""
import os
import re
import sys
import json
import time
import urllib.request
from typing import Dict, Any, List

def _load_env_key() -> str:
    env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_file):
        try:
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip() and not line.startswith("#") and "=" in line:
                        k, v = line.strip().split("=", 1)
                        if k.strip() == "DEEPSEEK_API_KEY":
                            return v.strip()
        except Exception:
            pass
    return os.environ.get("DEEPSEEK_API_KEY", "")

API_KEY = _load_env_key()
API_URL = "https://api.deepseek.com/chat/completions"
MODEL_NAME = "deepseek-flash"

TEST_BOOKS = [
    {
        "title": "青山",
        "author": "会说话的肘子",
        "gender_expected": "男频（高难隐喻书名测试）"
    },
    {
        "title": "夜无疆",
        "author": "辰东",
        "gender_expected": "男频（极简冷酷书名测试）"
    },
    {
        "title": "嫡嫁千金",
        "author": "千山茶客",
        "gender_expected": "女频古言换身复仇（《墨雨云间》原著）"
    },
    {
        "title": "真千金她是全能大佬",
        "author": "卿浅",
        "gender_expected": "女频现代豪门真假千金/马甲打脸"
    }
]

SYSTEM_PROMPT = """你是一位拥有十年经验的资深网文IP总监与短剧总策划。你的分析客观、犀利、精准，擅长洞察故事核心冲突、人设反差、爆款叙事基因与短剧黄金三集爽点。
请严格输出合法的标准 JSON 格式数据，不要输出任何 Markdown 标记或多余解释文字，直接以 '{' 开头，以 '}' 结尾。"""

USER_PROMPT_TEMPLATE = """请根据以下网络小说的书名与作者，推测并提炼其核心故事基因与短剧改编爆款特征（若该书为你已知的作品，请优先基于实际真实情节提炼；若未完全收录，请结合书名与作者风格规律合理推演）。

书名：《{title}》
作者：{author}

【任务要求】：
请严格按照以下 JSON Schema 输出标准 JSON 格式数据，不得缺失任何字段：
{{
  "title": "{title}",
  "author": "{author}",
  "track_and_audience": {{
    "gender_track": "男频 或 女频",
    "main_category": "主题材（如：都市/现言/玄幻/古言）",
    "sub_category": "细分赛道（如：战神回归/豪门先婚后爱/真假千金马甲/末世极寒/古言换身复仇）",
    "target_audience": "核心受众群体画像（如：下沉男性中青年 / 一二线年轻白领女性 / 银发中老年）"
  }},
  "tone_and_intensity": {{
    "narrative_pace": "叙事节奏（如：极致快节奏 / 快节奏 / 慢热）",
    "emotional_tone": "情绪调性（如：强情绪反杀 / 极度压抑反转 / 轻松发疯整顿 / 催泪虐心 / 极致爽快）"
  }},
  "identity_contrast": {{
    "surface_identity": "开局表面受辱/被压迫/被轻视的身份",
    "hidden_identity": "真实隐藏身份（大佬/首富/战神/神医/真千金等）",
    "contrast_gap": "身份反差烈度描述"
  }},
  "opening_crisis_hook": {{
    "conflict_type": "危机类型（如：被退婚/替嫁/假千金陷害/家族灭门/重病被抛弃/逼签断绝关系书）",
    "crisis_description": "前60秒生死线核心冲突与迫害事件描述"
  }},
  "payoff_mode": {{
    "leverage": "破局依托（男频：金手指/系统/功法/认知差；女频：隐藏马甲/血缘反差/豪门契约/专业技能/信息差）",
    "core_payoff": "核心爽点类型（打脸虐渣 / 追妻火葬场 / 权谋反杀 / 全家悔恨 / 绝不原谅）"
  }},
  "hierarchical_tropes": {{
    "l1_macro_tropes": [
      "3~5个宏观母题标签，如：#真假千金, #追妻火葬场, #大女主复仇, #战神归来, #断绝关系"
    ],
    "l2_micro_elements": [
      "8~12个微观情节看点标签，必须细致展现具体剧情冲突、道具、动作与名场面，如：#替嫁植物人, #雨夜罚跪, #撕毁百亿婚书, #全家悔恨下跪, #绝不原谅, #抢夺母亲遗物"
    ]
  }}
}}"""


def call_deepseek_json(title: str, author: str) -> Dict[str, Any]:
    prompt = USER_PROMPT_TEMPLATE.format(title=title, author=author)
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3,
        "max_tokens": 4000,
        "response_format": {"type": "json_object"}
    }
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }

    req = urllib.request.Request(API_URL, data=json.dumps(payload).encode('utf-8'), headers=headers)
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=60) as resp:
        elapsed = round(time.time() - t0, 2)
        raw = json.loads(resp.read().decode('utf-8'))
        choice = raw['choices'][0]
        content = choice['message'].get('content', '')
        reasoning = choice['message'].get('reasoning_content', '')
        usage = raw.get('usage', {})
        response_id = raw.get('id')

        # 解析 JSON
        parsed_json = None
        try:
            # 去除可能的 markdown 包裹
            clean_str = content.strip()
            if clean_str.startswith("```json"):
                clean_str = clean_str[7:]
            if clean_str.startswith("```"):
                clean_str = clean_str[3:]
            if clean_str.endswith("```"):
                clean_str = clean_str[:-3]
            parsed_json = json.loads(clean_str.strip())
        except Exception as e:
            # 尝试在文本中抓取 JSON 块
            m = re.search(r'(\{[\s\S]*\})', content)
            if m:
                try:
                    parsed_json = json.loads(m.group(1))
                except Exception:
                    pass

        return {
            "elapsed_seconds": elapsed,
            "response_id": response_id,
            "usage": usage,
            "raw_content": content,
            "parsed_json": parsed_json,
            "reasoning_len": len(reasoning)
        }


def main():
    print("=" * 70)
    print("启动实机测试：仅【书名 + 作者】抽取【6 黄金维度 + 12~18 分层热词】")
    print(f"模型: {MODEL_NAME} | 测试样本: {len(TEST_BOOKS)} 部")
    print("=" * 70)

    results = []
    for idx, book in enumerate(TEST_BOOKS, 1):
        t = book['title']
        a = book['author']
        exp = book['gender_expected']
        print(f"\n[{idx}/{len(TEST_BOOKS)}] 测试: 《{t}》（作者: {a}） - {exp}")
        print("  -> 正在发起 DeepSeek API 请求...", end="", flush=True)
        res = call_deepseek_json(t, a)
        print(f" 完成! 耗时: {res['elapsed_seconds']}s | 输入Tokens: {res['usage'].get('prompt_tokens')} | 输出Tokens: {res['usage'].get('completion_tokens')}")

        pj = res['parsed_json']
        if pj:
            print("  [解析成功] 提炼结果摘要:")
            print(f"    - 赛道受众: {pj.get('track_and_audience', {}).get('gender_track')} | {pj.get('track_and_audience', {}).get('sub_category')}")
            print(f"    - 人设反差: 表面[{pj.get('identity_contrast', {}).get('surface_identity')}] -> 真实[{pj.get('identity_contrast', {}).get('hidden_identity')}]")
            print(f"    - 开局冲突: {pj.get('opening_crisis_hook', {}).get('conflict_type')}")
            print(f"    - 破局依托: {pj.get('payoff_mode', {}).get('leverage')}")
            l1 = pj.get('hierarchical_tropes', {}).get('l1_macro_tropes', [])
            l2 = pj.get('hierarchical_tropes', {}).get('l2_micro_elements', [])
            print(f"    - L1 宏观标签 ({len(l1)}个): {l1}")
            print(f"    - L2 微观标签 ({len(l2)}个): {l2}")
        else:
            print("  [警告] JSON 解析失败，请查看 raw_content!")

        results.append({
            "book_meta": book,
            "api_result": res
        })

    # 保存原始测试结果 JSON
    out_json = "novel_dna_extraction_results.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n全部测试完成！详细 JSON 已保存至: {out_json}")


if __name__ == "__main__":
    main()
