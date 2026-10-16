"""
core/dialogue_transcriber.py
基于阿里 SenseVoiceSmall / FunASR 的离线短剧第一集台词高精度提取器
"""
import os
import re
import shutil
import tempfile
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional

_GLOBAL_ASR_MODEL = None


def get_asr_model():
    """懒加载单例模式加载本地已缓存的 SenseVoiceSmall 语音模型"""
    global _GLOBAL_ASR_MODEL
    if _GLOBAL_ASR_MODEL is None:
        from funasr import AutoModel
        # 静默抑制非关键日志
        _GLOBAL_ASR_MODEL = AutoModel(
            model="iic/SenseVoiceSmall",
            vad_model="iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
            punc_model="iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch",
            device="cpu",
            disable_update=True
        )
    return _GLOBAL_ASR_MODEL


class DialogueTranscriber:
    """第一集开篇台词提取器"""

    def __init__(self):
        pass

    def extract_audio(self, video_path: Path | str, output_audio: Path | str) -> bool:
        """通过 ffmpeg 转换音频为 FunASR 推荐的 16kHz 单声道 wav"""
        video_p = Path(video_path).resolve()
        output_p = Path(output_audio).resolve()
        output_p.parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_p),
            "-vn",
            "-ar", "16000",
            "-ac", "1",
            "-f", "wav",
            str(output_p)
        ]
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return res.returncode == 0 and output_p.exists() and output_p.stat().st_size > 1024


    def clean_sensevoice_text(self, text: str) -> str:
        """清洗 SenseVoice 输出的特殊标签（如 <|zh|><|NEUTRAL|><|Speech|><|woitn|>）"""
        if not text:
            return ""
        # 移除 <|...|> 标签
        cleaned = re.sub(r"<\|.*?\|>", "", text)
        # 移除多余空白
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    def transcribe(self, media_path: Path | str) -> Dict[str, Any]:
        """
        提取视频/音频的开篇台词文本
        返回格式:
        {
            "success": bool,
            "dialogue_text": str,
            "char_count": int,
            "error": Optional[str]
        }
        """
        media_p = Path(media_path).resolve()
        if not media_p.exists():
            return {"success": False, "dialogue_text": "", "char_count": 0, "error": f"文件不存在: {media_p}"}

        audio_file = None

        try:
            if media_p.suffix.lower() != ".wav":
                temp_audio = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
                temp_audio.close()
                audio_file = Path(temp_audio.name)
                ok = self.extract_audio(media_p, audio_file)
                if not ok:
                    return {"success": False, "dialogue_text": "", "char_count": 0, "error": "ffmpeg 抽离/转换音轨失败"}
                audio_target = audio_file
            else:
                audio_target = media_p


            model = get_asr_model()
            res = model.generate(input=str(audio_target), batch_size_s=300)
            if not res or not isinstance(res, list) or len(res) == 0:
                return {"success": True, "dialogue_text": "（未检测到有效台词或人声）", "char_count": 0, "error": None}

            raw_text = res[0].get("text", "")
            clean_text = self.clean_sensevoice_text(raw_text)

            return {
                "success": True,
                "dialogue_text": clean_text if clean_text else "（未检测到有效台词或人声）",
                "char_count": len(clean_text),
                "error": None
            }
        except Exception as e:
            return {
                "success": False,
                "dialogue_text": "",
                "char_count": 0,
                "error": f"语音识别异常: {str(e)}"
            }
        finally:
            if audio_file and audio_file.exists():
                try:
                    audio_file.unlink()
                except Exception:
                    pass

