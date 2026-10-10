"""
base.py
负向一票否决检测算子抽象基类
"""
from abc import ABC, abstractmethod
import time
from typing import Optional, List, Dict, Any
from ..models import DetectorResult, CoarseFilterConfig


class BaseDetector(ABC):
    """检测算子抽象基类"""
    name: str = "BaseDetector"

    def __init__(self, config: CoarseFilterConfig):
        self.config = config

    @abstractmethod
    def run_detection(
        self,
        video_path: Optional[str] = None,
        dialogue_text: Optional[str] = None,
        video_frames: Optional[List[Any]] = None,
        audio_path: Optional[str] = None,
        **kwargs
    ) -> DetectorResult:
        """
        执行具体的检测逻辑
        子类必须实现此方法
        """
        pass

    def detect(self, **kwargs) -> DetectorResult:
        """包装执行，统一耗时记录与异常捕获"""
        start_t = time.perf_counter()
        try:
            res = self.run_detection(**kwargs)
        except Exception as e:
            res = DetectorResult(
                detector_name=self.name,
                triggered_veto=False,
                diagnostic_message=f"算子执行异常已跳过: {str(e)}",
                metric_scores={"error": 1.0}
            )
        cost_ms = (time.perf_counter() - start_t) * 1000.0
        res.cost_time_ms = round(cost_ms, 2)
        return res
