"""
doodle_detector.py
沙雕漫与粗制表情包简笔画检测算子
通过 Canny 边缘密度与色彩直方图熵识别极简火柴人、粗糙涂鸦与低幼表情包
"""
import cv2
import numpy as np
from typing import Optional, List
from .base import BaseDetector
from ..models import DetectorResult


class DoodleDetector(BaseDetector):
    name: str = "DoodleDetector"

    def run_detection(
        self,
        video_path: Optional[str] = None,
        video_frames: Optional[List[np.ndarray]] = None,
        **kwargs
    ) -> DetectorResult:
        frames = video_frames
        if frames is None and video_path:
            frames = self._sample_frames(video_path, max_frames=8)

        if not frames:
            return DetectorResult(
                detector_name=self.name,
                triggered_veto=False,
                diagnostic_message="无可用帧，跳过沙雕简笔画检测",
                metric_scores={"edge_density": 0.0, "color_entropy": 0.0}
            )

        edge_densities = []
        color_entropies = []

        for frame in frames:
            resized = cv2.resize(frame, (480, 270))
            gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
            
            # 1. Canny 边缘密度分析（商用漫剧线稿精细、发丝服饰褶皱丰富；火柴人/简笔画边缘极低）
            edges = cv2.Canny(gray, 60, 160)
            edge_density = float(np.count_nonzero(edges) / edges.size)
            edge_densities.append(edge_density)

            # 2. 色彩直方图信息熵（简笔画通常为纯白底+几条黑线，色彩极度单调）
            hsv = cv2.cvtColor(resized, cv2.COLOR_BGR2HSV)
            # 统计 H 饱和度与 V 亮度直方图
            hist = cv2.calcHist([hsv], [0, 1], None, [16, 16], [0, 180, 0, 256])
            hist_norm = hist.ravel() / (hist.sum() + 1e-7)
            # 计算香农熵
            non_zeros = hist_norm[hist_norm > 0]
            entropy = float(-np.sum(non_zeros * np.log2(non_zeros)))
            color_entropies.append(entropy)

        avg_edge_density = float(np.mean(edge_densities))
        avg_color_entropy = float(np.mean(color_entropies))

        # 同时满足线条极度稀疏与色彩极度贫瘠，判定为粗制火柴人/沙雕涂鸦
        is_sparse_lines = avg_edge_density < self.config.doodle_edge_density_max
        is_monochrome = avg_color_entropy < self.config.doodle_color_entropy_max
        triggered = is_sparse_lines and is_monochrome

        msg = (
            f"检测到线条极度稀疏（边缘密度: {avg_edge_density:.4f} < {self.config.doodle_edge_density_max}）"
            f"且色彩单调（熵: {avg_color_entropy:.2f} < {self.config.doodle_color_entropy_max}），"
            f"判定为粗制沙雕漫/火柴人表情包漫，缺乏商业付费转化品相"
            if triggered else "通过：画面包含正常商业漫剧线稿复杂度与色彩层次"
        )

        return DetectorResult(
            detector_name=self.name,
            triggered_veto=triggered,
            reason_code="DETECTED_ROUGH_DOODLE_OR_MEME" if triggered else None,
            diagnostic_message=msg,
            metric_scores={
                "edge_density": round(avg_edge_density, 4),
                "color_entropy": round(avg_color_entropy, 2),
                "is_sparse_lines": 1.0 if is_sparse_lines else 0.0,
                "is_monochrome": 1.0 if is_monochrome else 0.0
            }
        )

    def _sample_frames(self, video_path: str, max_frames: int = 8) -> List[np.ndarray]:
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
