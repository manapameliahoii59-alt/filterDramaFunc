"""
density_detector.py
叙事信息密度与注水拖沓检测算子
通过词汇多样性 (TTR)、连续句冗余重叠度识别原地倒车、废话口癖注水剧
"""
import re
from typing import Optional, List, Set
from .base import BaseDetector
from ..models import DetectorResult


class DensityDetector(BaseDetector):
    name: str = "DensityDetector"

    def run_detection(
        self,
        dialogue_text: Optional[str] = None,
        **kwargs
    ) -> DetectorResult:
        if not dialogue_text or len(dialogue_text.strip()) < 15:
            return DetectorResult(
                detector_name=self.name,
                triggered_veto=False,
                diagnostic_message="未提供足够长度对白文本，跳过信息密度检测",
                metric_scores={"narrative_redundancy_ratio": 0.0, "ttr_diversity": 1.0}
            )

        # 简单中文分句与字符清洗
        sentences = [s.strip() for s in re.split(r'[。！？\n；!?]+', dialogue_text) if len(s.strip()) > 2]
        
        # 1. 词汇/字多样性 (Type-Token Ratio, TTR)
        # 统计单字与双字切片丰富度
        chars = [c for c in dialogue_text if '\u4e00' <= c <= '\u9fa5' or c.isalnum()]
        if not chars:
            return DetectorResult(
                detector_name=self.name,
                triggered_veto=False,
                diagnostic_message="文本中无有效汉字字符",
                metric_scores={}
            )

        unique_chars = set(chars)
        ttr = len(unique_chars) / max(1, len(chars))

        # 2. 连续句重叠与冗余拉扯计算 (Jaccard 重叠率)
        high_overlap_pairs = 0
        total_pairs = max(1, len(sentences) - 1)

        for i in range(len(sentences) - 1):
            s1_set = set(sentences[i])
            s2_set = set(sentences[i + 1])
            if not s1_set or not s2_set:
                continue
            intersection = len(s1_set & s2_set)
            # 使用重叠系数 (Overlap Coefficient = 交集 / 较短句集合大小)
            # 针对短句拉扯（如“你敢打我”与“我打你怎么了”），交集占短句达 70% 即可准确捕获
            min_len = min(len(s1_set), len(s2_set))
            overlap_coef = intersection / min_len if min_len > 0 else 0.0
            
            if overlap_coef >= 0.50:
                high_overlap_pairs += 1

        redundancy_ratio = high_overlap_pairs / total_pairs

        # 0. 提示词与未清洗草稿标签污染检测
        prompt_pollution_regex = re.compile(
            r'(\[镜头.*?\]|\[特写.*?\]|\[转场.*?\]|\[全景.*?\]|\[中景.*?\]|\[bgm.*?\]|\[BGM.*?\]|'
            r'prompt:|negative prompt:|场景:|机位:|景别:|分镜:)',
            re.IGNORECASE
        )
        pollution_matches = prompt_pollution_regex.findall(dialogue_text)
        has_prompt_pollution = len(pollution_matches) >= 1

        # 判定：满足以下任意一项即判定为劣质废品剧
        # 1. 残留未清洗的 AI 分镜提示词标签
        # 2. 极高相邻句重复拉扯率 (>= 0.70)
        # 3. 较高重复拉扯率 (>= redundancy_ratio_max) 且词汇多样性匮乏 (<= min_ttr_ratio)
        # 4. 极度词汇单一贫瘠 (<= 0.22)
        is_high_redundancy = redundancy_ratio >= 0.70
        is_combined_bloat = (redundancy_ratio >= self.config.redundancy_ratio_max) and (ttr <= self.config.min_ttr_ratio)
        is_extreme_low_ttr = ttr <= 0.22

        triggered = has_prompt_pollution or is_high_redundancy or is_combined_bloat or is_extreme_low_ttr

        if has_prompt_pollution:
            reason = "DETECTED_PROMPT_POLLUTION"
            msg = f"格式硬伤：残留未清洗的分镜提示词标签({','.join(pollution_matches[:2])})，属于未经清洗的低质工业草稿物料"
        elif triggered:
            reason = "DETECTED_LOW_INFORMATION_DENSITY"
            msg = (
                f"严重注水：检测到极低叙事信息密度（词汇多样性 TTR: {ttr:.2f}，相邻句重复拉扯率: {redundancy_ratio:.1%}），"
                f"属于通篇车轱辘吵架复读、缺乏剧情推进的低质注水剧"
            )
        else:
            reason = None
            msg = f"通过：对白信息密度正常 (TTR: {ttr:.2f}, 冗余率: {redundancy_ratio:.1%})"

        return DetectorResult(
            detector_name=self.name,
            triggered_veto=triggered,
            reason_code=reason,
            diagnostic_message=msg,
            metric_scores={
                "ttr_diversity": round(ttr, 3),
                "narrative_redundancy_ratio": round(redundancy_ratio, 3),
                "sentence_count": float(len(sentences)),
                "char_count": float(len(chars))
            }
        )
