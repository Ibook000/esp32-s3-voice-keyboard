"""音频输出：保存 PCM16 WAV，调用本地 Whisper 识别，再按需输入文字。

蓝牙接收不依赖识别模型；只安装 requirements.txt 时也能保存 WAV。
"""

from __future__ import annotations

import argparse
import wave
from pathlib import Path

from voice_protocol import SAMPLE_RATE
from windows_input import type_text_windows


def save_wav(path: Path, pcm: bytes, rate: int = SAMPLE_RATE) -> None:
    """写入单声道、16 位 WAV；采样率默认与固件一致，为 16 kHz。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm)


class SpeechOutput:
    """复用一个 CPU int8 模型；模型只在 prepare() 中加载一次。"""

    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.model = None

    def prepare(self) -> None:
        """仅开启 --transcribe 时加载模型；首次使用模型名可能需要联网下载。"""
        if not self.args.transcribe:
            return
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError("Install pc/requirements-asr.txt to enable transcription") from exc
        print("Loading speech model (first use may download model files)...", flush=True)
        cache = Path(__file__).resolve().parents[1] / ".pio" / "asr_models"
        options = dict(device="cpu", compute_type="int8", download_root=str(cache), cpu_threads=2)
        # 已缓存的模型直接离线加载，避免每次启动查询下载服务。
        try:
            self.model = WhisperModel(self.args.model, local_files_only=True, **options)
        except (OSError, ValueError):
            self.model = WhisperModel(self.args.model, **options)

    def process(self, path: Path, test_tone: bool) -> str | None:
        """识别已保存的 WAV，写同名 UTF-8 文本；测试音自动跳过识别。"""
        if self.model is None or test_tone:
            return
        import numpy as np

        # WAV 格式固定，直接读取 PCM，避免依赖 PyAV 的文件解码接口。
        # 模型需要 16 kHz 浮点数组：小端 int16 除以 32768，归一化到 [-1, 1)。
        with wave.open(str(path), "rb") as wav:
            if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, SAMPLE_RATE):
                raise ValueError("Speech input must be 16 kHz mono PCM16 WAV")
            pcm = wav.readframes(wav.getnframes())
        audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        segments, _ = self.model.transcribe(
            audio,
            language=self.args.language,
            vad_filter=True,  # 先检测人声，减少静音片段产生的错误文字。
            beam_size=5,
            condition_on_previous_text=False,  # 各段独立，避免前一段文字影响下一段。
        )
        text = "".join(segment.text for segment in segments).strip()
        path.with_suffix(".txt").write_text(text + "\n", encoding="utf-8")
        print(f"Recognized: {text or '(no speech detected)'}", flush=True)
        if text and self.args.type_text:
            # 输入目标取决于此刻的焦点窗口；WAV 和文本已先保存，可独立查看。
            type_text_windows(text)
        return text
