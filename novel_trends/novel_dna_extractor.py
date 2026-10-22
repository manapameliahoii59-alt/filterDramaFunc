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
import requests
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

SYSTEM_PROMPT = """你是一位拥有十年经验的资深网文IP总监与短剧总策划。你的分析客观、犀利、精准，擅长洞察故事核心冲突、人设反差、破局底牌与短剧第一集黄金爽点。
请严格输出合法的标准 JSON 格式数据，不要输出任何 Markdown 标记或多余解释文字，直接以 '{' 开头，以 '}' 结尾。"""

USER_PROMPT_TEMPLATE = """请根据以下网络小说的书名与作者，推测并提炼其核心故事基因与短剧改编 6 大黄金维度爆款特征（若该书为你已知的作品，请优先基于实际真实情节提炼；若未完全收录，请结合书名与作者风格规律合理推演）。

书名：《{title}》
作者：{author}

【任务要求】：
请严格按照以下 6 大维度 JSON Schema 输出标准 JSON 格式数据，不得缺失任何字段：
{{
  "title": "{title}",
  "author": "{author}",
  "data_quality_check": {{
    "is_synopsis_valid": true,
    "has_payoff_in_opening": true,
    "extraction_confidence": "高"
  }},
  "track_dna": {{
    "gender_track": "男频 / 女频 / 泛性别",
    "worldview_setting": "世界观基底（如：现代都市 / 末日天灾 / 规则怪谈 / 玄幻修仙 / 古代架空 / 脑洞变异）",
    "core_sub_genre": "核心细分流派（如：末世囤货安全屋 / 真假千金断亲 / 全家偷听心声 / 神豪返利系统 / 战神隐忍退婚 / 四个首富哥哥团宠）"
  }},
  "protagonist_and_leverage_dna": {{
    "surface_status": "开局（第1-2章）主角受迫害弱势状态",
    "hidden_or_awakened_identity": "隐藏/觉醒身份（如：唯一SSS级异能掌控者 / 跨国财阀唯一继承人 / 重生先知者）",
    "leverage_category": "破局底牌6大分类之一：系统面板类 / 重生先知类 / 异能空间类 / 隐藏马甲类 / 契约羁绊类 / 极致智商类",
    "leverage_specific_mechanism": "底牌具体运作规则与触发机制"
  }},
  "conflict_and_payoff_dna": {{
    "antagonist_relationship": "迫害者关系（如：偏心父母与绿茶假千金 / 势利丈母娘与小舅子）",
    "inciting_incident": "第一集导火索事件（第1章发生的危机冲突）",
    "extreme_aggro_behavior": "反派极度拉仇恨的具体恶行或道德绑架话术逻辑",
    "first_counterattack_payoff": "前5~6章内主角完成的第一次实质性反击打脸爽点事件"
  }},
  "ep1_matching_anchors": {{
    "key_props_and_scenes": [
      "4~8个视频画面中肉眼可见的具象道具或场景（如：断亲书、B超单、百亿黑卡、极寒暴风雪、系统弹窗）"
    ],
    "core_dialogue_themes": [
      "3~5个第一集吵架/冲突台词主题（如：逼让保送名额、嘲讽主角吃软饭、末日倒计时借贷囤货）"
    ],
    "character_archetypes": [
      "出场人物典型标签（如：偏心生母、绿茶养妹、冷酷首富小叔、嚣张富二代）"
    ]
  }},
  "hierarchical_tropes": {{
    "l1_macro_tropes": [
      "3~5个宏观母题标签，如：#真假千金, #末日囤货, #战神归来, #全员读心, #绝不原谅"
    ],
    "l2_micro_elements": [
      "8~12个微观情节看点标签，必须细致展现具体剧情动作与名场面，如：#开局签断亲书, #清空百亿物资, #雨夜罚跪, #当众退婚打脸, #打造末日安全屋"
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

        session = getattr(self, "_session", None)
        if session is None:
            self._session = requests.Session()
            session = self._session

        for attempt in range(1, 4):
            try:
                # 若此前重试过且目标为阿里云百炼，自动尝试直连 bypass 代理
                proxies = {'http': None, 'https': None} if (attempt > 1 and "dashscope" in API_URL) else None
                resp = session.post(
                    API_URL,
                    json=payload,
                    headers=headers,
                    timeout=45,
                    proxies=proxies
                )
                resp.raise_for_status()
                raw = resp.json()
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
                if attempt < 3:
                    time.sleep(1.5)
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
