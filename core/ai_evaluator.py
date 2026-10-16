"""
core/ai_evaluator.py
基于真实大模型 API (Qwen3.8-Flash / DeepSeek) 的短剧/漫剧多维智能评估引擎
负责：
1. 严打 PPT 漫：仅当画面确诊为刚体平移轮播，或标题明确标注 PPT漫/静态漫时一票否决淘汰
2. 纯剧单与简介分析：评估题材吸量度、反转张力、悬念钩子 (0-40分)
3. 叙事紧凑度与密度评估 (0-30分)
4. 精准归纳漫剧赛道 (genre_category) 与核心爽点标签 (genre_tags)
"""
import os
import re
import json
import requests
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(ENV_PATH)


class AIDramaEvaluator:
    """真实大模型剧目评估器"""

    def __init__(self):
        self.dashscope_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
        self.deepseek_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        self.preferred_model = os.getenv("DASHSCOPE_MODEL", "qwen3.8-flash").strip()

    def _call_llm(self, prompt: str) -> Optional[str]:
        """优先调用阿里云百炼 (Qwen3.8-Flash)，备用 DeepSeek"""
        # 1. 优先尝试 DashScope
        if self.dashscope_key:
            headers = {
                "Authorization": f"Bearer {self.dashscope_key}",
                "Content-Type": "application/json"
            }
            data = {
                "model": self.preferred_model,
                "messages": [
                    {"role": "system", "content": "你是一名资深短剧/漫剧选品与商业化评估专家。直接输出纯 JSON 格式，不要返回任何额外解释。"},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.2,
                "max_tokens": 400,
                "response_format": {"type": "json_object"}
            }
            try:
                resp = requests.post(
                    "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
                    headers=headers,
                    json=data,
                    timeout=15
                )
                if resp.status_code == 200:
                    res_json = resp.json()
                    return res_json["choices"][0]["message"]["content"]
            except Exception as e:
                pass

        # 2. 备用 DeepSeek
        if self.deepseek_key:
            headers = {
                "Authorization": f"Bearer {self.deepseek_key}",
                "Content-Type": "application/json"
            }
            data = {
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": "你是一名资深短剧/漫剧选品与商业化评估专家。直接输出纯 JSON 格式。"},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.2,
                "max_tokens": 400,
                "response_format": {"type": "json_object"}
            }
            try:
                resp = requests.post(
                    "https://api.deepseek.com/chat/completions",
                    headers=headers,
                    json=data,
                    timeout=15
                )
                if resp.status_code == 200:
                    res_json = resp.json()
                    return res_json["choices"][0]["message"]["content"]
            except Exception as e:
                pass

        return None

    def evaluate_drama(
        self,
        title: str,
        abstract: str = "",
        episodes: Optional[int] = None,
        ep1_dialogue: str = "",
        visual_info: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        调用大模型对剧目进行真实多维商业潜力评估
        """
        visual_desc = ""
        is_ppt_strict = False
        
        # 1. 严格检查标题是否明确标为 PPT 漫
        lower_title = title.lower()
        if any(k in lower_title for k in ["ppt漫", "动态ppt", "静态漫", "ppt动画"]):
            is_ppt_strict = True
            visual_desc = "标题明确标注为 PPT 漫/静态漫"

        # 2. 检查视频光流是否确诊为刚体推拉
        if visual_info:
            rigid_ratio = visual_info.get("ppt_rigid_ratio", 0.0)
            if visual_info.get("is_rigid_ppt", False) or rigid_ratio >= 0.75:
                is_ppt_strict = True
                visual_desc = f"画面检测为全局刚体位移 (刚体光流比: {rigid_ratio:.1%})，无人物局部微动，确诊为 PPT 漫"

        prompt = f"""请评估以下待审短剧/漫剧：
【剧名】：{title}
【总集数】：{episodes if episodes else '未标明'}
【剧情简介/台词】：{abstract if abstract else '（仅剧名）'}
【画面特征】：{visual_desc if visual_desc else '（纯文本，待抓取第1集视频质检）'}

【评估准则】：
1. is_ppt_slideshow: 仅当画面确诊为刚体推拉轮播、或标题明确标为PPT漫/静态漫时为 true。普通的纯剧名或正常商业剧绝不能误判为 PPT漫！
2. story_tension_score (0-40分): 评估开篇戏剧冲突、反差与吸引力。有明确打脸/反转/悬念给 25~35分；平淡流水账给 10~15分。
3. narrative_density_score (0-30分): 节奏紧凑度。
4. genre_category: 准确归纳该剧赛道（如：都市反转打脸、女频真假千金断亲、职场反击爽剧、都市萌宠反差、穿越反套路等）。
5. genre_tags: 3-5个核心爽点标签。
6. brief_review: 30字以内精炼专业选品点评。

返回纯 JSON 格式：
{{
  "is_ppt_slideshow": <true/false>,
  "story_tension_score": <0.0-40.0>,
  "narrative_density_score": <0.0-30.0>,
  "genre_category": "<赛道类型>",
  "genre_tags": ["<标签1>", "<标签2>", "<标签3>"],
  "brief_review": "<精炼点评>"
}}"""

        raw_output = self._call_llm(prompt)
        if raw_output:
            try:
                cleaned = re.sub(r"^```(?:json)?\s*", "", raw_output.strip())
                cleaned = re.sub(r"\s*```$", "", cleaned.strip())
                result = json.loads(cleaned)

                # 强行校准 PPT 标识
                final_ppt = is_ppt_strict or bool(result.get("is_ppt_slideshow", False))
                if is_ppt_strict:
                    result["story_tension_score"] = min(12.0, float(result.get("story_tension_score", 12.0)))
                    result["narrative_density_score"] = min(8.0, float(result.get("narrative_density_score", 8.0)))
                    result["brief_review"] = "【一票否决】确诊为PPT漫伪动态轮播，画质品相极低淘汰"

                return {
                    "success": True,
                    "is_ppt_slideshow": final_ppt,
                    "story_tension_score": float(result.get("story_tension_score", 24.0)),
                    "narrative_density_score": float(result.get("narrative_density_score", 18.0)),
                    "genre_category": str(result.get("genre_category", "综合短剧")),
                    "genre_tags": list(result.get("genre_tags", [])),
                    "brief_review": str(result.get("brief_review", "大模型评估完成"))
                }
            except Exception as e:
                pass

        # 降级保底
        return {
            "success": False,
            "is_ppt_slideshow": is_ppt_strict,
            "story_tension_score": 10.0 if is_ppt_strict else 22.0,
            "narrative_density_score": 5.0 if is_ppt_strict else 18.0,
            "genre_category": "都市/爽剧",
            "genre_tags": ["都市", "短剧"],
            "brief_review": "系统启发式评估"
        }
