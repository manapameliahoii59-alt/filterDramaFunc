"""
pipeline.py
短剧漫剧准入粗筛 · 多维加权打分与网文大盘风向标调度引擎 (CoarseFilterPipeline)
核心机制：
1. 摒弃死板一刀切，采用多方位综合加权打分制 (画面30% + 规格20% + 密度20% + 对抗20% + 视听10%)；
2. 低质劣质项 (PPT漫/沙雕漫/夭折集数/车轱辘吵架) 赋予惩罚性极低分，拉低总分自然淘汰；
3. 联动四大真实小说平台（起点、纵横、晋江、潇湘）榜单知识库，命中近期畅销大热题材额外赋予高额热度加权 (+10~20分)；
4. 准入门槛基准线：60.0 分。
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
from novel_trends import TrendMatcher


class CoarseFilterPipeline:
    """粗筛多维加权打分调度管线"""

    def __init__(self, config: Optional[CoarseFilterConfig] = None):
        self.config = config or CoarseFilterConfig()
        self.trend_matcher = TrendMatcher()
        
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
        **kwargs
    ) -> CoarseFilterResult:
        """
        对输入的短剧/漫剧物料执行多方位综合加权打分与风向标裁决
        """
        start_time = time.perf_counter()
        
        veto_reasons: List[str] = []
        diagnostics: List[str] = []
        all_scores: Dict[str, float] = {}
        detector_details: List[DetectorResult] = []

        # ----------------------------------------------------------------------
        # 1. 执行各底层探测算子
        # ----------------------------------------------------------------------
        for detector in self.detectors:
            res = detector.detect(
                video_path=video_path,
                dialogue_text=dialogue_text,
                audio_path=audio_path,
                video_frames=video_frames,
                **kwargs
            )
            detector_details.append(res)
            
            for k, v in res.metric_scores.items():
                all_scores[f"{detector.name}.{k}"] = v

            if res.triggered_veto:
                if res.reason_code:
                    veto_reasons.append(res.reason_code)
                if res.diagnostic_message:
                    diagnostics.append(f"[{detector.name}] {res.diagnostic_message}")

        # ----------------------------------------------------------------------
        # 2. 多维度加权得分核算 (各维度满分 100 分)
        # ----------------------------------------------------------------------
        # 维度 1: 画面动态与品类形态 (权重 30%)
        # 优质真动态/真人: 90~95分；PPT漫: 0分；沙雕漫: 15分；未提供视频默认基准分: 88分
        visual_form_score = 88.0
        if "DETECTED_PPT_SLIDESHOW" in veto_reasons:
            visual_form_score = 0.0
        elif "DETECTED_ROUGH_DOODLE_OR_MEME" in veto_reasons:
            visual_form_score = 15.0
        elif video_path or video_frames:
            visual_form_score = 92.0

        # 维度 2: 商业规格与体量门槛 (权重 20%)
        # 达标 (>35集且>50s): 95分；总集数不足: 15分；首集过短: 25分
        spec_scale_score = 95.0
        if "DETECTED_FEW_EPISODES" in veto_reasons:
            spec_scale_score = 15.0
        elif "DETECTED_SHORT_EPISODE_1" in veto_reasons:
            spec_scale_score = 25.0

        # 维度 3: 台词叙事与信息密度 (权重 20%)
        # 紧凑高信息量: 90分；严重车轱辘吵架: 15分；提示词污染: 30分
        dialogue_density_score = 90.0
        if "DETECTED_LOW_INFORMATION_DENSITY" in veto_reasons:
            dialogue_density_score = 15.0
        elif "DETECTED_PROMPT_POLLUTION" in veto_reasons:
            dialogue_density_score = 30.0

        # 维度 4: 角色主体与戏剧对抗 (权重 20%)
        # 鲜明主角+强复仇对抗: 92分；纯风景散文无主角: 10分；平淡无对抗: 25分
        character_conflict_score = 92.0
        if "DETECTED_NO_CLEAR_PROTAGONIST" in veto_reasons:
            character_conflict_score = 10.0
        elif "DETECTED_LACK_DRAMATIC_CONFLICT" in veto_reasons:
            character_conflict_score = 25.0

        # 维度 5: 视听底层物理画质 (权重 10%)
        # 正常清朗: 95分；模糊/黑屏/静音/破音: 15分
        audiovisual_quality_score = 95.0
        if any(r in veto_reasons for r in [
            "DETECTED_SEVERE_BLUR", "DETECTED_SEVERE_BLACK_SCREEN",
            "DETECTED_FROZEN_VIDEO", "DETECTED_AUDIO_SILENCE", "DETECTED_AUDIO_CLIPPING"
        ]):
            audiovisual_quality_score = 15.0

        # 基础五维加权总分 (总分 100 分)
        base_weighted_score = (
            0.30 * visual_form_score +
            0.20 * spec_scale_score +
            0.20 * dialogue_density_score +
            0.20 * character_conflict_score +
            0.10 * audiovisual_quality_score
        )

        dimension_scores = {
            "visual_form": visual_form_score,
            "spec_scale": spec_scale_score,
            "dialogue_density": dialogue_density_score,
            "character_conflict": character_conflict_score,
            "audiovisual_quality": audiovisual_quality_score
        }

        # ----------------------------------------------------------------------
        # 3. 网文大盘爆款题材风向标比对与加权提分 (Trend Bonus: 最高 20.0 分)
        # ----------------------------------------------------------------------
        title_to_match = drama_title or ""
        content_to_match = dialogue_text or ""
        trend_res = self.trend_matcher.match_drama_trend(title_to_match, content_to_match)
        trend_bonus = trend_res.get("bonus_score", 0.0)

        # 严密防守：若存在任何致命硬伤 (如画面缺陷、规格不足、缺少主角、未清洗提示词、车轱辘注水)，
        # 直接熔断网文大盘加分资格 (trend_bonus = 0.0)，劣质物料坚决不予加分
        if veto_reasons or visual_form_score < 30.0:
            trend_bonus = 0.0

        if trend_bonus >= 8.0:
            diagnostics.append(f"[网文大盘加分 +{trend_bonus}分] {trend_res.get('evidence_text', '')}")

        dimension_scores["trend_bonus"] = trend_bonus

        # ----------------------------------------------------------------------
        # 4. 综合总分核算与准入决策
        # ----------------------------------------------------------------------
        # 恶性缺陷惩罚项：每个恶性缺陷扣 20 分
        penalty = len(veto_reasons) * 20.0
        composite_score = round(max(0.0, min(100.0, base_weighted_score + trend_bonus - penalty)), 1)

        # 准入门槛线：60.0 分
        passed = (composite_score >= 60.0)
        action = "PASS" if passed else "VETO"

        # 评级划分
        if composite_score >= 80.0:
            final_grade = "S"
        elif composite_score >= 65.0:
            final_grade = "A"
        elif composite_score >= 60.0:
            final_grade = "B"
        else:
            final_grade = "C"

        # 若通过粗筛，执行精筛商业潜力亮点提取
        fine_res = None
        if passed:
            from .fine_ranker import FineRanker
            fine_ranker = FineRanker()
            text_to_eval = dialogue_text or ""
            fine_res = fine_ranker.evaluate(text_to_eval)
            if fine_res.highlights:
                diagnostics.append(f"[潜力亮点] {' | '.join(fine_res.highlights)}")

        total_cost_ms = (time.perf_counter() - start_time) * 1000.0

        return CoarseFilterResult(
            passed=passed,
            action=action,
            grade=final_grade,
            composite_score=composite_score,
            dimension_scores=dimension_scores,
            trend_evidence=trend_res,
            veto_reasons=veto_reasons,
            diagnostics=diagnostics,
            scores=all_scores,
            fine_screening=fine_res,
            detector_details=detector_details,
            total_execution_time_ms=round(total_cost_ms, 2)
        )
