"""
core/pipeline.py
短剧漫剧准入粗筛 · AI多模态真实评估与大盘风向标调度引擎 (CoarseFilterPipeline)

核心重构升级：
1. 彻底移除写死 88分画面、95分视听的虚假保底代码；
2. 接入真实大模型 API (Qwen3.8-Flash / DeepSeek)，结合简介、台词与第一集物料深度评估；
3. PPT漫零容忍法则：画面检测为刚体平移、或大模型断定为 PPT漫、或标题明确为 PPT漫者，直接打低分一票否决淘汰，绝不评 S 级！
4. 真实归纳漫剧赛道类型 (genre_category) 与核心爽点标签 (genre_tags)；
5. 联动四大真实小说平台（起点、纵横、晋江、潇湘）榜单数据库，按 AI 提炼的赛道进行真实大盘加权 (+10~20分)。
"""
import time
from typing import Optional, List, Dict, Any
from .models import CoarseFilterConfig, CoarseFilterResult, DetectorResult
from .detectors import (
    PPTDetector,
    DoodleDetector,
    VisualDefectDetector,
    DensityDetector,
    CharacterDetector,
    SpecDetector,
    AudioGlitchDetector
)
from .ai_evaluator import AIDramaEvaluator
from novel_trends import TrendMatcher


class CoarseFilterPipeline:
    """粗筛 AI 真实多维评估调度管线"""

    def __init__(self, config: Optional[CoarseFilterConfig] = None):
        self.config = config or CoarseFilterConfig()
        self.trend_matcher = TrendMatcher()
        self.ai_evaluator = AIDramaEvaluator()

        # 注册检测算子
        self.detectors = [
            SpecDetector(self.config),           # 1. 规格体量与低质品类
            DensityDetector(self.config),        # 2. 文本信息密度与提示词污染
            CharacterDetector(self.config),      # 3. 人物主角与戏剧对抗冲突
            VisualDefectDetector(self.config),   # 4. 画质硬伤与黑屏
            DoodleDetector(self.config),         # 5. 沙雕表情包简笔画
            PPTDetector(self.config),            # 6. PPT漫与刚体平移
            AudioGlitchDetector(self.config)     # 7. 音频故障与静音
        ]

    def filter_drama(
        self,
        video_path: Optional[str] = None,
        dialogue_text: Optional[str] = None,
        audio_path: Optional[str] = None,
        video_frames: Optional[List[Any]] = None,
        drama_title: Optional[str] = None,
        abstract: Optional[str] = None,
        total_episodes: Optional[int] = None,
        **kwargs
    ) -> CoarseFilterResult:
        """
        对输入的短剧/漫剧物料执行真实大模型多维评估与大盘数据库比对
        """
        start_time = time.perf_counter()

        veto_reasons: List[str] = []
        diagnostics: List[str] = []
        all_scores: Dict[str, float] = {}

        # ----------------------------------------------------------------------
        # 1. 执行底层物理特征探测（音视频物理指标与文本硬规则）
        # ----------------------------------------------------------------------
        visual_info = {}
        for detector in self.detectors:
            res = detector.detect(
                video_path=video_path,
                dialogue_text=dialogue_text,
                audio_path=audio_path,
                video_frames=video_frames,
                total_episodes=total_episodes,
                **kwargs
            )
            for k, v in res.metric_scores.items():
                all_scores[f"{detector.name}.{k}"] = v

            if res.triggered_veto:
                if res.reason_code:
                    veto_reasons.append(res.reason_code)
                if res.diagnostic_message:
                    diagnostics.append(f"[{detector.name}] {res.diagnostic_message}")

            # 收集画面物理指标供大模型辅助审查
            if detector.name == "PPTDetector":
                visual_info["ppt_rigid_ratio"] = res.metric_scores.get("ppt_rigid_ratio", 0.0)
                visual_info["is_rigid_ppt"] = res.triggered_veto
            elif detector.name == "DoodleDetector":
                visual_info["edge_density"] = res.metric_scores.get("edge_density", 0.0)

        # ----------------------------------------------------------------------
        # 2. 调用真实大语言/多模态模型 API 进行深度内容与画质品相评估
        # ----------------------------------------------------------------------
        clean_title = drama_title or "未知剧目"
        clean_content = dialogue_text or abstract or ""
        
        # 显式识别标题中的 PPT 标签
        has_title_ppt = any(p in clean_title.lower() for p in ["ppt", "动态ppt", "静态漫"])
        if has_title_ppt:
            veto_reasons.append("DETECTED_PPT_SLIDESHOW")
            visual_info["is_rigid_ppt"] = True

        ai_eval = self.ai_evaluator.evaluate_drama(
            title=clean_title,
            abstract=clean_content,
            episodes=total_episodes,
            ep1_dialogue=clean_content if len(clean_content) > 30 else "",
            visual_info=visual_info if video_path or has_title_ppt else None
        )

        # ----------------------------------------------------------------------
        # 3. PPT 漫与恶性低质项严厉裁决（零容忍法则）
        # ----------------------------------------------------------------------
        is_ppt_vetoed = has_title_ppt or ai_eval.get("is_ppt_slideshow", False) or ("DETECTED_PPT_SLIDESHOW" in veto_reasons)
        is_doodle_vetoed = "DETECTED_ROUGH_DOODLE_OR_MEME" in veto_reasons

        if is_ppt_vetoed:
            if "DETECTED_PPT_SLIDESHOW" not in veto_reasons:
                veto_reasons.append("DETECTED_PPT_SLIDESHOW")
            diagnostics.append("[画质一票否决] 确诊为单张插画整体推拉平移的 PPT 伪动态漫，画质恶劣，坚决打低分淘汰！")
        
        if is_doodle_vetoed:
            diagnostics.append("[品类一票否决] 检测为火柴人/粗制沙雕表情包涂鸦漫，坚决打低分淘汰！")

        # ----------------------------------------------------------------------
        # 4. 联动大盘数据库进行题材风向加权比对
        # ----------------------------------------------------------------------
        genre_category = ai_eval.get("genre_category", "综合短剧")
        genre_tags = ai_eval.get("genre_tags", [])
        brief_review = ai_eval.get("brief_review", "")

        # 组合丰富上下文比对网文大盘
        trend_query = f"{clean_title} {genre_category} {' '.join(genre_tags)} {clean_content}"
        trend_res = self.trend_matcher.match_drama_trend(clean_title, trend_query)
        trend_bonus = trend_res.get("bonus_score", 0.0)

        # 若命中一票否决，坚决取消任何大盘加分
        if veto_reasons:
            trend_bonus = 0.0

        if trend_bonus >= 8.0:
            diagnostics.append(f"[大盘热度加分 +{trend_bonus}分] {trend_res.get('evidence_text', '')}")

        # ----------------------------------------------------------------------
        # 5. 真实加权综合总分核算与评级决策
        # ----------------------------------------------------------------------
        # 基础分组成：情节冲突 (0-40) + 叙事紧凑度 (0-30) + 规格达标基础分 (15分)
        tension_score = ai_eval.get("story_tension_score", 20.0)
        density_score = ai_eval.get("narrative_density_score", 15.0)
        spec_base = 0.0 if any(r in veto_reasons for r in ["DETECTED_FEW_EPISODES", "DETECTED_SHORT_EPISODE_1"]) else 15.0

        # 若为 PPT 漫或严重缺陷，总分强行压制在 35 分以下
        if is_ppt_vetoed or is_doodle_vetoed:
            composite_score = round(min(30.0, tension_score * 0.5 + density_score * 0.5), 1)
            passed = False
            action = "VETO"
            final_grade = "C"
        elif veto_reasons:
            penalty = len(veto_reasons) * 20.0
            composite_score = round(max(0.0, tension_score + density_score + spec_base + trend_bonus - penalty), 1)
            passed = False
            action = "VETO"
            final_grade = "C"
        else:
            composite_score = round(min(100.0, tension_score + density_score + spec_base + trend_bonus), 1)
            # 准入门槛：60 分
            passed = (composite_score >= 60.0)
            action = "PASS" if passed else "VETO"
            if composite_score >= 80.0:
                final_grade = "S"
            elif composite_score >= 65.0:
                final_grade = "A"
            elif composite_score >= 50.0:
                final_grade = "B"
            else:
                final_grade = "C"

        total_cost_ms = (time.perf_counter() - start_time) * 1000.0

        dimension_scores = {
            "story_tension": tension_score,
            "narrative_density": density_score,
            "spec_base": spec_base,
            "trend_bonus": trend_bonus,
            "genre_category": genre_category,
            "genre_tags": genre_tags,
            "brief_review": brief_review,
            "is_ppt": is_ppt_vetoed
        }

        return CoarseFilterResult(
            passed=passed,
            action=action,
            composite_score=composite_score,
            grade=final_grade,
            dimension_scores=dimension_scores,
            trend_evidence=trend_res if trend_bonus > 0 else None,
            veto_reasons=veto_reasons,
            scores=all_scores,
            diagnostics=diagnostics,
            total_execution_time_ms=total_cost_ms
        )
