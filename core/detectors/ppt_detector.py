"""
ppt_detector.py
PPT漫与静态刚体轮播检测算子
通过稠密光流 (Farneback Optical Flow) 检测画面是否为单张插画整体平移/缩放
"""
import cv2
import numpy as np
from typing import Optional, List, Any
from .base import BaseDetector
from ..models import DetectorResult


class PPTDetector(BaseDetector):
    name: str = "PPTDetector"

    def run_detection(
        self,
        video_path: Optional[str] = None,
        video_frames: Optional[List[np.ndarray]] = None,
        **kwargs
    ) -> DetectorResult:
        frames = video_frames
        
        # 若未提供内存帧，从视频抽样快速读取
        if frames is None and video_path:
            frames = self._sample_frames(video_path, max_frames=self.config.ppt_min_frames_to_sample)
            
        if not frames or len(frames) < 2:
            return DetectorResult(
                detector_name=self.name,
                triggered_veto=False,
                diagnostic_message="有效抽样帧不足，跳过PPT漫检测",
                metric_scores={"ppt_rigid_ratio": 0.0}
            )

        rigid_motion_count = 0
        valid_motion_pairs = 0
        variances = []
        has_local_dynamic = False

        # 连续帧光流分析
        prev_gray = cv2.cvtColor(cv2.resize(frames[0], (320, 240)), cv2.COLOR_BGR2GRAY)
        
        for i in range(1, len(frames)):
            curr_gray = cv2.cvtColor(cv2.resize(frames[i], (320, 240)), cv2.COLOR_BGR2GRAY)
            
            # 计算 Farneback 稠密光流
            flow = cv2.calcOpticalFlowFarneback(
                prev_gray, curr_gray, None,
                pyr_scale=0.5, levels=3, winsize=15,
                iterations=3, poly_n=5, poly_sigma=1.2, flags=0
            )
            
            mag, ang = cv2.cartToPolar(flow[..., 0], flow[..., 1])
            motion_mask = mag > 0.5
            coverage = float(np.sum(motion_mask) / mag.size)
            
            # 1. 动静分离：若画面存在局部独立动作（人物五官微动、说话、肢体手势，覆盖率在 1%~25%）
            if 0.01 <= coverage < 0.25 and np.sum(motion_mask) > 50:
                has_local_dynamic = True
                valid_motion_pairs += 1
                prev_gray = curr_gray
                continue

            # 2. 全局大幅运动：若属于大面积整屏运动（覆盖率 >= 25%）
            if coverage >= 0.25:
                valid_motion_pairs += 1
                active_angles = ang[motion_mask]
                
                # 角度方差与方向一致性（单图平移时光流方向高度一致，标准差极低）
                sin_sum = np.sum(np.sin(active_angles))
                cos_sum = np.sum(np.cos(active_angles))
                r_len = np.sqrt(sin_sum**2 + cos_sum**2) / len(active_angles)
                circular_var = 1.0 - r_len  # 0代表方向完全一致，1代表完全杂乱
                variances.append(float(circular_var))
                
                # 检测是否为单图平移 (Pan) 刚体位移
                is_rigid = circular_var < 0.20
                
                # 检测是否为单图缩放/推拉 (Zoom) 刚体位移 (全局 2D 仿射单应性模型拟合)
                if not is_rigid and np.sum(motion_mask) > 100:
                    try:
                        y_idx, x_idx = np.where(motion_mask)
                        if len(x_idx) > 200:
                            sample_indices = np.random.choice(len(x_idx), 200, replace=False)
                            x_idx = x_idx[sample_indices]
                            y_idx = y_idx[sample_indices]
                        src_pts = np.float32(np.column_stack([x_idx, y_idx]))
                        dst_pts = src_pts + flow[y_idx, x_idx]
                        _, inliers = cv2.estimateAffinePartial2D(src_pts, dst_pts, method=cv2.RANSAC, ransacReprojThreshold=2.0)
                        if inliers is not None and (np.sum(inliers) / len(inliers)) >= 0.82:
                            is_rigid = True
                    except Exception:
                        pass
                
                if is_rigid:
                    rigid_motion_count += 1

            prev_gray = curr_gray

        if valid_motion_pairs == 0:
            # 抽样帧间几乎无位移：检查是否整片完全静止（死图/定格漫）
            frame_diffs = [
                float(np.mean(np.abs(frames[i].astype(np.float32) - frames[0].astype(np.float32))))
                for i in range(1, len(frames))
            ]
            rigid_ratio = 1.0 if frame_diffs and np.mean(frame_diffs) < 2.0 else 0.0
        else:
            rigid_ratio = rigid_motion_count / valid_motion_pairs

        # 若存在真漫剧的局部人物肢体微动，且非全屏刚体推拉，予以豁免
        if has_local_dynamic and rigid_ratio < 0.50:
            rigid_ratio = 0.0

        avg_var = float(np.mean(variances)) if variances else 0.0
        triggered = rigid_ratio >= self.config.ppt_rigid_ratio_threshold

        msg = (
            f"画面检测硬伤：检测到画面具有高度刚体平移/缩放特征（刚体率: {rigid_ratio:.2f} >= {self.config.ppt_rigid_ratio_threshold}），"
            f"缺乏分层与局部肢体动作，判定为低成本PPT漫/伪动态漫"
            if triggered else "通过：画面包含自然多峰光流或局部肢体动作"
        )

        return DetectorResult(
            detector_name=self.name,
            triggered_veto=triggered,
            reason_code="DETECTED_PPT_SLIDESHOW" if triggered else None,
            diagnostic_message=msg,
            metric_scores={
                "ppt_rigid_ratio": round(float(rigid_ratio), 3),
                "motion_direction_variance": round(avg_var, 3),
                "valid_motion_pairs": float(valid_motion_pairs)
            }
        )

    def _sample_frames(self, video_path: str, max_frames: int = 15) -> List[np.ndarray]:
        """等间隔快速抽样"""
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
