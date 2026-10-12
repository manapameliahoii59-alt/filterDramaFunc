# -*- coding: utf-8 -*-
"""
novel_trends/novel_dna_extractor.py
基于【方案 A】（仅书名 + 作者）调用 DeepSeek-V4.1-Flash
全自动提取网文 6 大黄金维度与 12~18 个分层热词（L1 宏观母题 + L2 微观情节词）
内置本地增量缓存，确保已提取书籍零重复 Token 消耗
"""
import os
import re
import json
import time
import urllib.request
from typing import Dict, Any, List, Optional, Tuple
from .models import NovelDnaProfile
def _load_env_config() -> Tuple[str, str, str]:
    """读取环境配置，优先使用阿里云百炼 DashScope (Qwen3.8-Flash)，备用 DeepSeek"""
    env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    env_map = {}
    if os.path.exists(env_file):
        try:
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip() and not line.startswith("#") and "=" in line:
                        k, v = line.strip().split("=", 1)
                        env_map[k.strip()] = v.strip()
        except Exception:
            pass

    # 1. 优先使用 DashScope Qwen3.8-Flash
    dashscope_key = env_map.get("DASHSCOPE_API_KEY") or os.environ.get("DASHSCOPE_API_KEY", "")
    if dashscope_key:
        model = env_map.get("DASHSCOPE_MODEL") or os.environ.get("DASHSCOPE_MODEL", "qwen3.8-flash")
        url = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
        return dashscope_key, url, model

    # 2. 备用 DeepSeek
    deepseek_key = env_map.get("DEEPSEEK_API_KEY") or os.environ.get("DEEPSEEK_API_KEY", "")
    if deepseek_key:
        return deepseek_key, "https://api.deepseek.com/chat/completions", "deepseek-flash"

    return "", "", ""

from typing import Tuple
API_KEY, API_URL, MODEL_NAME = _load_env_config()

import threading

DNA_KB_PATH = os.path.join(os.path.dirname(__file__), "data", "novel_dna_kb.json")

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


class NovelDnaExtractor:
    """网文爆款基因提取服务 (方案 A)"""

    def __init__(self, storage_path: str = DNA_KB_PATH):
        self.storage_path = os.path.abspath(storage_path)
        os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)
        self._lock = threading.Lock()
        self.dna_cache: Dict[str, Dict[str, Any]] = self._load_cache()

    def _load_cache(self) -> Dict[str, Dict[str, Any]]:
        if os.path.isfile(self.storage_path):
            try:
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[NovelDnaExtractor] 读取缓存异常: {e}")
        return {}

    def _save_cache(self):
        with self._lock:
            try:
                temp_path = self.storage_path + f".tmp.{os.getpid()}"
                with open(temp_path, "w", encoding="utf-8") as f:
                    json.dump(self.dna_cache, f, ensure_ascii=False, indent=2)
                if os.path.exists(self.storage_path):
                    os.remove(self.storage_path)
                os.rename(temp_path, self.storage_path)
            except Exception as e:
                print(f"[NovelDnaExtractor] 保存缓存失败: {e}")

    def _make_key(self, title: str, author: str) -> str:
        return f"{title.strip()}_{author.strip()}"

    def get_novel_dna(self, title: str, author: str, force_refresh: bool = False) -> Optional[NovelDnaProfile]:
        """
        获取单部小说的 DNA 特征（已存在则 0 延迟复用本地缓存）
        """
        key = self._make_key(title, author)
        with self._lock:
            cached_item = self.dna_cache.get(key)
        if not force_refresh and cached_item:
            try:
                return NovelDnaProfile.parse_obj(cached_item) if hasattr(NovelDnaProfile, "parse_obj") else NovelDnaProfile.model_validate(cached_item)
            except Exception:
                pass

        # 发起大模型 (Qwen3.8-Flash / DeepSeek) 调用
        profile_data = self._call_llm_extract(title, author)
        if profile_data:
            with self._lock:
                self.dna_cache[key] = profile_data
            self._save_cache()
            try:
                return NovelDnaProfile.parse_obj(profile_data) if hasattr(NovelDnaProfile, "parse_obj") else NovelDnaProfile.model_validate(profile_data)
            except Exception:
                pass
        return None

    def _call_llm_extract(self, title: str, author: str) -> Optional[Dict[str, Any]]:
        """调用大语言模型 (优先通义千问 Qwen3.8-Flash，备用 DeepSeek) 提取 6 维特征"""
        if not API_KEY or not API_URL:
            print(f"[NovelDnaExtractor] 未检测到大模型 API_KEY，跳过大模型调用")
            return None

        prompt = USER_PROMPT_TEMPLATE.format(title=title, author=author)
        payload = {
            "model": MODEL_NAME,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.3,
            "max_tokens": 1800,
            "response_format": {"type": "json_object"}
        }
        if "dashscope" in API_URL:
            payload["enable_thinking"] = False
        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json"
        }

        for attempt in range(1, 3):
            try:
                req = urllib.request.Request(API_URL, data=json.dumps(payload).encode('utf-8'), headers=headers)
                with urllib.request.urlopen(req, timeout=60) as resp:
                    raw = json.loads(resp.read().decode('utf-8'))
                    choice = raw['choices'][0]
                    content = choice['message'].get('content', '')
                    clean_str = content.strip()
                    if clean_str.startswith("```json"):
                        clean_str = clean_str[7:]
                    if clean_str.startswith("```"):
                        clean_str = clean_str[3:]
                    if clean_str.endswith("```"):
                        clean_str = clean_str[:-3]
                    
                    parsed = json.loads(clean_str.strip())
                    return parsed
            except Exception as e:
                if attempt == 1:
                    time.sleep(1)
                    continue
                print(f"[NovelDnaExtractor] 调用大模型 ({MODEL_NAME}) 提取《{title}》异常 (第{attempt}次重试失败): {e}")
                return None

    def _call_deepseek_extract(self, title: str, author: str) -> Optional[Dict[str, Any]]:
        """向后兼容别名"""
        return self._call_llm_extract(title, author)

    def get_all_profiles(self) -> List[NovelDnaProfile]:
        """获取本地库中所有已分析小说的 DNA 列表"""
        profiles = []
        for key, data in self.dna_cache.items():
            try:
                p = NovelDnaProfile.parse_obj(data) if hasattr(NovelDnaProfile, "parse_obj") else NovelDnaProfile.model_validate(data)
                profiles.append(p)
            except Exception:
                pass
        return profiles
