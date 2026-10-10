"""
spec_detector.py
短剧漫剧规格体量与低质品类检测算子
负责粗筛一票否决：
1. 总集数不足 25 集（体量过小、无法形成商业闭环的夭折/测试剧）
2. 第一集时长不足 50 秒（片段过短破碎、缺乏叙事戏剧弧光）
3. PPT漫、动态PPT、静态漫等低质伪动态物料
4. 沙雕漫、火柴人、表情包简笔画等粗制滥造物料
"""
import re
import os
import cv2
from typing import Optional, List, Dict, Any
from .base import BaseDetector
from ..models import DetectorResult

# 低质沙雕简笔画漫关键词
DOODLE_KEYWORDS = [
    "沙雕漫", "沙雕动画", "沙雕表情包", "表情包漫", "火柴人漫", "火柴人动画",
    "简笔画漫", "魔性简笔画", "草图漫", "粗制草图", "简笔涂鸦"
]


class SpecDetector(BaseDetector):
    name: str = "SpecDetector"

    def run_detection(
        self,
        video_path: Optional[str] = None,
        dialogue_text: Optional[str] = None,
        total_episodes: Optional[int] = None,
        ep1_duration_sec: Optional[float] = None,
        **kwargs
    ) -> DetectorResult:
        text_to_check = dialogue_text or ""
        lower_text = text_to_check.lower()

        # ----------------------------------------------------
        # 1. 低质品类排查：沙雕表情包漫一票否决
        # (注：PPT漫严格依据视频画面光流/仿射位移识别，绝不通过文本标题标签误判)
        # ----------------------------------------------------
        matched_doodle = [k for k in DOODLE_KEYWORDS if k in lower_text]
        if matched_doodle:
            return DetectorResult(
                detector_name=self.name,
                triggered_veto=True,
                reason_code="DETECTED_ROUGH_DOODLE_OR_MEME",
                diagnostic_message=f"品类硬伤：检测到明确标注为【{matched_doodle[0]}】，属于火柴人粗制草图涂鸦，直接一票否决淘汰",
                metric_scores={"is_doodle": 1.0}
            )

        # ----------------------------------------------------
        # 2. 总集数硬门槛检测（必须 > 35 集）
        # ----------------------------------------------------
        episodes = total_episodes
        if episodes is None and text_to_check:
            # 从文本中智能提取集数（例如：共15集、全20集、[18集]、总集数: 12集）
            ep_match = re.search(r'(?:共|全|总计|总集数[:：]?\s*|\[|\()(\d+)\s*集', text_to_check)
            if ep_match:
                try:
                    episodes = int(ep_match.group(1))
                except Exception:
                    pass

        min_ep = getattr(self.config, "min_total_episodes", 35)
        if episodes is not None and episodes > 0 and episodes <= min_ep:
            return DetectorResult(
                detector_name=self.name,
                triggered_veto=True,
                reason_code="DETECTED_FEW_EPISODES",
                diagnostic_message=f"规格硬伤：总集数仅 {episodes} 集（未达 > {min_ep} 集商业硬门槛），体量过小无法形成商业变现闭环，判定为测试剧或夭折剧淘汰",
                metric_scores={"total_episodes": float(episodes)}
            )

        # ----------------------------------------------------
        # 3. 第一集时长硬门槛检测（必须 > 50 秒）
        # ----------------------------------------------------
        duration = ep1_duration_sec
        # 若输入是本地视频文件，直接用 OpenCV 读取视频时长
        if duration is None and video_path and os.path.isfile(video_path):
            try:
                cap = cv2.VideoCapture(video_path)
                fps = cap.get(cv2.CAP_PROP_FPS)
                frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
                cap.release()
                if fps > 0 and frames > 0:
                    duration = frames / fps
            except Exception:
                pass

        # 若未从视频读取，尝试从文本标签中提取（例如：首集30秒、首集45s、[首集40秒]）
        if duration is None and text_to_check:
            # 匹配 秒: 首集40秒 / 首集40s
            dur_match_sec = re.search(r'(?:首集|第1集|第一集)(?:时长)?[:：]?\s*(\d+)\s*(?:秒|s|S)', text_to_check)
            if dur_match_sec:
                try:
                    duration = float(dur_match_sec.group(1))
                except Exception:
                    pass
            else:
                # 匹配 分秒: 首集0分40秒
                dur_match_min = re.search(r'(?:首集|第1集|第一集)(?:时长)?[:：]?\s*(\d+)\s*分(?:钟)?(?:\s*(\d+)\s*秒)?', text_to_check)
                if dur_match_min:
                    try:
                        m = float(dur_match_min.group(1))
                        s = float(dur_match_min.group(2) or 0)
                        duration = m * 60.0 + s
                    except Exception:
                        pass

        min_dur = getattr(self.config, "min_ep1_duration_sec", 50.0)
        if duration is not None and duration > 0 and duration <= min_dur:
            return DetectorResult(
                detector_name=self.name,
                triggered_veto=True,
                reason_code="DETECTED_SHORT_EPISODE_1",
                diagnostic_message=f"规格硬伤：第一集时长仅 {duration:.1f} 秒（未达 > {min_dur:.0f} 秒硬门槛），剧情过于短碎缺乏戏剧展开，判定为劣质碎片物料淘汰",
                metric_scores={"ep1_duration_sec": float(duration)}
            )

        # 全部通过
        ep_info = f"{episodes}集" if episodes else "集数未标注"
        dur_info = f"{duration:.0f}s" if duration else "时长未标注"
        return DetectorResult(
            detector_name=self.name,
            triggered_veto=False,
            reason_code=None,
            diagnostic_message=f"通过：规格体量合格 ({ep_info}, {dur_info})，无沙雕漫低质标签",
            metric_scores={
                "total_episodes": float(episodes or 0),
                "ep1_duration_sec": float(duration or 0)
            }
        )
