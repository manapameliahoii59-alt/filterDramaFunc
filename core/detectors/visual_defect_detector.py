"""
visual_defect_detector.py
画质硬伤与黑屏卡顿检测算子
检测严重失焦模糊、黑屏断流与画面完全冻结
"""
import cv2
import numpy as np
from typing import Optional, List
from .base import BaseDetector
from ..models import DetectorResult


class VisualDefectDetector(BaseDetector):
    name: str = "VisualDefectDetector"

    def run_detection(
        self,
        video_path: Optional[str] = None,
        video_frames: Optional[List[np.ndarray]] = None,
        **kwargs
    ) -> DetectorResult:
        frames = video_frames
        if frames is None and video_path:
            frames = self._sample_frames(video_path, max_frames=10)

        if not frames:
            return DetectorResult(
                detector_name=self.name,
                triggered_veto=False,
                diagnostic_message="无可用帧，跳过画质硬伤检测",
                metric_scores={"blur_score": 0.0, "black_frame_ratio": 0.0}
            )

        blur_scores = []
        black_frames = 0
        frozen_frames = 0
        total_frames = len(frames)

        prev_gray = None
        for frame in frames:
            resized = cv2.resize(frame, (480, 270))
            gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)

            # 1. 拉普拉斯算子方差评估清晰度（正常商用视频通常 > 120，严重失焦模糊 < 60）
            lap_var = cv2.Laplacian(gray, cv2.CV_64F).var()
            blur_scores.append(float(lap_var))

            # 2. 黑屏检测（全图平均亮度 < 12 判定为黑屏）
            is_curr_black = np.mean(gray) < 12.0
            if is_curr_black:
                black_frames += 1

            # 3. 画面冻结检测（仅对非全黑画面，相邻抽样帧绝对差几乎为 0 判定为冻结）
            if prev_gray is not None and not is_curr_black:
                diff = np.mean(cv2.absdiff(prev_gray, gray))
                if diff < 0.8:
                    frozen_frames += 1
            prev_gray = gray

        avg_blur = float(np.mean(blur_scores)) if blur_scores else 0.0
        black_ratio = black_frames / total_frames
        frozen_ratio = frozen_frames / max(1, total_frames - 1)

        is_severe_blur = avg_blur < self.config.blur_laplacian_min
        is_high_black = black_ratio > self.config.black_frame_ratio_max
        is_frozen = frozen_ratio > 0.60

        triggered = is_severe_blur or is_high_black or is_frozen
        reason = None
        reasons_list = []

        # 错误码优先级：黑屏 > 严重模糊 > 画面冻结
        if is_frozen:
            reasons_list.append(f"画面严重冻结卡顿(冻结帧占比: {frozen_ratio:.1%})")
            reason = "DETECTED_FROZEN_VIDEO"
        if is_severe_blur:
            reasons_list.append(f"严重失焦模糊(清晰度方差: {avg_blur:.1f} < {self.config.blur_laplacian_min})")
            reason = "DETECTED_SEVERE_BLUR"
        if is_high_black:
            reasons_list.append(f"大面积黑屏断流(黑屏占比: {black_ratio:.1%})")
            reason = "DETECTED_SEVERE_BLACK_SCREEN"

        msg = (
            f"检测到画质致命缺陷：{'; '.join(reasons_list)}，直接拦截"
            if triggered else f"通过：画质清晰度正常 (Laplacian: {avg_blur:.1f})，无异常黑屏与冻结"
        )

        return DetectorResult(
            detector_name=self.name,
            triggered_veto=triggered,
            reason_code=reason,
            diagnostic_message=msg,
            metric_scores={
                "blur_laplacian_variance": round(avg_blur, 1),
                "black_frame_ratio": round(black_ratio, 3),
                "frozen_frame_ratio": round(frozen_ratio, 3)
            }
        )

    def _sample_frames(self, video_path: str, max_frames: int = 10) -> List[np.ndarray]:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return []
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total <= 0:
            cap.release()
            return []
        step = max(1, total // max_frames)
        sampled = []
        for idx in range(0, min(total, max_frames * step), step):
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ret, frame = cap.read()
            if ret:
                sampled.append(frame)
            if len(sampled) >= max_frames:
                break
        cap.release()
        return sampled
