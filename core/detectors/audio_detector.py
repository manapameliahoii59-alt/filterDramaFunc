"""
audio_detector.py
音频故障与静音检测算子
通过短时能量 RMS 与波形截断失真检测严重静音、破音爆音
"""
import numpy as np
from typing import Optional
from .base import BaseDetector
from ..models import DetectorResult


class AudioGlitchDetector(BaseDetector):
    name: str = "AudioGlitchDetector"

    def run_detection(
        self,
        audio_path: Optional[str] = None,
        video_path: Optional[str] = None,
        audio_samples: Optional[np.ndarray] = None,
        sr: int = 16000,
        **kwargs
    ) -> DetectorResult:
        y = audio_samples
        
        # 若传入了音频数组直接使用，若未传入尝试读取
        if y is None and (audio_path or video_path):
            path_to_load = audio_path or video_path
            y = self._load_audio(path_to_load, sr=sr)

        if y is None or len(y) == 0:
            return DetectorResult(
                detector_name=self.name,
                triggered_veto=False,
                diagnostic_message="未检测到音频轨道或音频解析跳过",
                metric_scores={"silence_ratio": 0.0, "clipping_ratio": 0.0}
            )

        # 1. 计算静音比例（RMS 能量低于 0.003 判定为静音帧）
        frame_len = int(sr * 0.1)  # 100ms 一帧
        if len(y) < frame_len:
            return DetectorResult(detector_name=self.name, triggered_veto=False)

        num_frames = len(y) // frame_len
        y_cut = y[:num_frames * frame_len].reshape(num_frames, frame_len)
        rms = np.sqrt(np.mean(y_cut ** 2, axis=1))

        silent_frames = np.sum(rms < 0.003)
        silence_ratio = float(silent_frames / max(1, num_frames))

        # 2. 计算削波失真（破音 Clipping 判定：样本绝对值达到或接近 0.99）
        clipping_samples = np.sum(np.abs(y) >= 0.985)
        clipping_ratio = float(clipping_samples / max(1, len(y)))

        is_high_silence = silence_ratio >= self.config.silence_ratio_max
        is_severe_clipping = clipping_ratio >= self.config.clipping_ratio_max
        triggered = is_high_silence or is_severe_clipping

        reason = None
        reasons_list = []
        if is_high_silence:
            reasons_list.append(f"长时间无声或严重消音(静音占比: {silence_ratio:.1%} >= {self.config.silence_ratio_max:.0%})")
            reason = "DETECTED_AUDIO_SILENCE"
        if is_severe_clipping:
            reasons_list.append(f"严重爆音与削波失真(破音率: {clipping_ratio:.2%} >= {self.config.clipping_ratio_max:.1%})")
            reason = "DETECTED_AUDIO_CLIPPING"

        msg = (
            f"检测到音频致命缺陷：{'; '.join(reasons_list)}，听感严重恶劣"
            if triggered else f"通过：音频能量与波形正常 (静音率: {silence_ratio:.1%}, 破音率: {clipping_ratio:.2%})"
        )

        return DetectorResult(
            detector_name=self.name,
            triggered_veto=triggered,
            reason_code=reason,
            diagnostic_message=msg,
            metric_scores={
                "silence_ratio": round(silence_ratio, 3),
                "clipping_ratio": round(clipping_ratio, 4),
                "avg_rms_energy": round(float(np.mean(rms)), 4)
            }
        )

    def _load_audio(self, file_path: str, sr: int = 16000) -> Optional[np.ndarray]:
        """安全加载音频样本"""
        try:
            import librosa
            # 仅加载前 30 秒以节省耗时
            y, _ = librosa.load(file_path, sr=sr, mono=True, duration=30.0)
            return y
        except Exception:
            return None
