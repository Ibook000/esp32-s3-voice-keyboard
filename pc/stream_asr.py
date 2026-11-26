"""流式中文识别：sherpa-onnx 在线 Zipformer，边收边解码，按端点定稿。

只在本机 CPU 上跑，不联网。音频以 20 ms 一帧喂进来，每帧立即解码，
检测到句尾停顿时把这一段定稿并清空缓冲，剩下的继续听。
"""

from __future__ import annotations

import time
import wave
from pathlib import Path

SAMPLE_RATE = 16000
MODEL_ROOT = Path(__file__).resolve().parents[1] / ".pio" / "asr_models"

# 预设名 → (目录名, 归档下载名)。目录里需要 encoder/decoder/joiner 和 tokens.txt。
MODELS = {
    "zipformer-zh": (
        "sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30",
        "sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30.tar.bz2",
    ),
    "zipformer-zh-14m": (
        "sherpa-onnx-streaming-zipformer-zh-14M-2023-02-23",
        "sherpa-onnx-streaming-zipformer-zh-14M-2023-02-23.tar.bz2",
    ),
}
DEFAULT_MODEL = "zipformer-zh"
PREFERRED_FILES = ("encoder.int8.onnx", "decoder.onnx", "joiner.int8.onnx", "tokens.txt")
FALLBACK_FILES = ("encoder.onnx", "decoder.onnx", "joiner.onnx", "tokens.txt")
FILES_14M = (
    (
        "encoder-epoch-99-avg-1.int8.onnx",
        "decoder-epoch-99-avg-1.onnx",
        "joiner-epoch-99-avg-1.int8.onnx",
        "tokens.txt",
    ),
    (
        "encoder-epoch-99-avg-1.onnx",
        "decoder-epoch-99-avg-1.onnx",
        "joiner-epoch-99-avg-1.onnx",
        "tokens.txt",
    ),
)


def model_dir(name: str = DEFAULT_MODEL) -> Path:
    """返回模型目录；未下载时返回预期路径，由调用方决定是否自动下载。"""
    folder, _ = MODELS.get(name, MODELS[DEFAULT_MODEL])
    return MODEL_ROOT / folder


def model_files(name: str = DEFAULT_MODEL) -> tuple[Path, Path, Path, Path]:
    """按目录里实际存在的文件挑 int8 或 fp32 组合，缺文件时报错。"""
    folder = model_dir(name)
    candidates = (PREFERRED_FILES, FALLBACK_FILES)
    if name == "zipformer-zh-14m":
        candidates = (*FILES_14M, *candidates)
    for names in candidates:
        files = tuple(folder / item for item in names)
        if all(path.is_file() for path in files):
            return files  # encoder, decoder, joiner, tokens
    raise RuntimeError(
        f"流式识别模型缺失：{folder}。请先执行 tools/fetch_asr_model.py 下载 {name}。"
    )


class StreamRecognizer:
    """一段会话对应一个 OnlineStream；端点定稿后自动重置，继续听下一句。"""

    def __init__(self, model: str = DEFAULT_MODEL, threads: int = 2):
        import numpy as np
        import sherpa_onnx

        encoder, decoder, joiner, tokens = model_files(model)
        self._np = np
        self._rec = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=str(tokens),
            encoder=str(encoder),
            decoder=str(decoder),
            joiner=str(joiner),
            num_threads=max(1, threads),
            sample_rate=SAMPLE_RATE,
            feature_dim=80,
            decoding_method="greedy_search",
            enable_endpoint_detection=True,
            # 一句话说完停顿 1 秒就定稿上屏，长句最多连续 20 秒。
            rule1_min_trailing_silence=2.4,
            rule2_min_trailing_silence=1.0,
            rule3_min_utterance_length=20,
        )
        self._stream = None

    def begin(self) -> None:
        """开始一段新录音：丢弃上一段的状态。"""
        self._stream = self._rec.create_stream()

    def push(self, pcm: bytes) -> tuple[str, bool]:
        """喂入 PCM16 字节；返回（当前文本，是否已到句尾定稿）。"""
        if self._stream is None:
            self.begin()
        samples = self._np.frombuffer(pcm, dtype="<i2").astype(self._np.float32) / 32768.0
        stream = self._stream
        stream.accept_waveform(SAMPLE_RATE, samples)
        while self._rec.is_ready(stream):
            self._rec.decode_stream(stream)
        if self._rec.is_endpoint(stream):
            text = str(self._rec.get_result_all(stream).text).strip()
            self._rec.reset(stream)
            return text, True
        return str(self._rec.get_result(stream)).strip(), False

    def end(self) -> str:
        """录音结束：补 0.5 秒静音把尾巴解完，返回最后一段文字。"""
        if self._stream is None:
            return ""
        stream = self._stream
        stream.accept_waveform(
            SAMPLE_RATE, self._np.zeros(int(0.5 * SAMPLE_RATE), dtype=self._np.float32)
        )
        stream.input_finished()
        while self._rec.is_ready(stream):
            self._rec.decode_stream(stream)
        text = str(self._rec.get_result_all(stream).text).strip()
        self._stream = None
        return text


def decode_wav(path: Path, model: str = DEFAULT_MODEL, threads: int = 2) -> str:
    """整段 WAV 离线校验用：按 20 ms 一帧模拟实时输入，返回全文。"""
    recognizer = StreamRecognizer(model, threads)
    recognizer.begin()
    with wave.open(str(path), "rb") as source:
        pcm = source.readframes(source.getnframes())
    pieces = []
    for offset in range(0, len(pcm), SAMPLE_RATE * 2 // 50):
        text, done = recognizer.push(pcm[offset : offset + SAMPLE_RATE * 2 // 50])
        if done and text:
            pieces.append(text)
    tail = recognizer.end()
    if tail:
        pieces.append(tail)
    return "".join(pieces)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="按 20 ms 帧流式解码一个 WAV")
    parser.add_argument("wav", type=Path)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    options = parser.parse_args()
    start = time.perf_counter()
    print(decode_wav(options.wav, options.model))
    print(f"cpu {time.perf_counter() - start:.2f}s")
