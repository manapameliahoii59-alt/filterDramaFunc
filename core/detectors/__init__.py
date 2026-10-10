"""
detectors package
包含所有负向一票否决检测算子
"""
from .base import BaseDetector
from .ppt_detector import PPTDetector
from .doodle_detector import DoodleDetector
from .visual_defect_detector import VisualDefectDetector
from .density_detector import DensityDetector
from .character_detector import CharacterDetector
from .spec_detector import SpecDetector
from .audio_detector import AudioGlitchDetector

__all__ = [
    "BaseDetector",
    "PPTDetector",
    "DoodleDetector",
    "VisualDefectDetector",
    "DensityDetector",
    "CharacterDetector",
    "SpecDetector",
    "AudioGlitchDetector",
]


