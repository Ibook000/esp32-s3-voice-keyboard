"""BLE 语音协议：解析状态、拼接分片、CRC16 校验、IMA ADPCM 解码。

本模块只使用标准库，可脱离蓝牙硬件测试。常量必须与
include/voice_protocol.h、include/ima_adpcm.h 及 docs/voice-protocol.md 一致。
"""

from __future__ import annotations

import binascii
import struct
from dataclasses import dataclass
from enum import IntEnum

# 同一个服务下有三条特征：电脑写控制命令，设备通知音频和状态。
SERVICE_UUID = "cba10000-7d2f-4a6b-9c11-18d5e0010001"
CONTROL_UUID = "cba10001-7d2f-4a6b-9c11-18d5e0010001"
AUDIO_UUID = "cba10002-7d2f-4a6b-9c11-18d5e0010001"
STATUS_UUID = "cba10003-7d2f-4a6b-9c11-18d5e0010001"
STATUS = struct.Struct("<BBBBHHHHIHH")  # 小端 20 字节：版本、类型、状态及录音元数据。
FRAGMENT = struct.Struct("<BBHHH")  # 小端 8 字节：版本、类型、会话、帧号、块内偏移。
SAMPLE_RATE = 16000  # 每秒采样数；识别模型和 WAV 使用相同采样率。
FRAME_SAMPLES = 320  # 每帧 20 ms，每个采样解码后占 2 字节。
BLOCK_BYTES = 168  # 每帧 ADPCM：6 字节头 + 160 字节编码数据 + 2 字节 CRC。
MAX_FRAMES = 500  # 500 × 20 ms = 10 秒。
STOP, START, TEST_TONE, ACK, CANCEL = range(5)

# IMA ADPCM 标准步长表和索引增量表；编码器与解码器必须一致。
STEPS = (
    7,
    8,
    9,
    10,
    11,
    12,
    13,
    14,
    16,
    17,
    19,
    21,
    23,
    25,
    28,
    31,
    34,
    37,
    41,
    45,
    50,
    55,
    60,
    66,
    73,
    80,
    88,
    97,
    107,
    118,
    130,
    143,
    157,
    173,
    190,
    209,
    230,
    253,
    279,
    307,
    337,
    371,
    408,
    449,
    494,
    544,
    598,
    658,
    724,
    796,
    876,
    963,
    1060,
    1166,
    1282,
    1411,
    1552,
    1707,
    1878,
    2066,
    2272,
    2499,
    2749,
    3024,
    3327,
    3660,
    4026,
    4428,
    4871,
    5358,
    5894,
    6484,
    7132,
    7845,
    8630,
    9493,
    10442,
    11487,
    12635,
    13899,
    15289,
    16818,
    18500,
    20350,
    22385,
    24623,
    27086,
    29794,
    32767,
)
INDEX = (-1, -1, -1, -1, 2, 4, 6, 8)


class State(IntEnum):
    """设备状态：空闲 → 录音 → 发送 → 完成；异常时进入 ERROR。"""

    IDLE = 0
    RECORDING = 1
    SENDING = 2
    COMPLETE = 3
    ERROR = 4


ERRORS = {
    1: "Microphone pins are not configured in include/voice_config.h.",
    2: "I2S capture failed. Check microphone type, power, channel and wiring.",
    3: "Device could not allocate its recording buffer/task/command queue.",
    4: "Audio notifications are not subscribed. Start the receiver first.",
    5: "BLE disconnected; the unfinished recording was discarded.",
    6: "Audio ACK timed out after retries; the transfer was cancelled.",
    7: "Recording cancelled.",
    8: "Recording was empty. Hold the button longer than 20 ms.",
    9: "Invalid control command.",
}


@dataclass(frozen=True)
class DeviceStatus:
    """设备状态快照；session 区分录音，frames 是帧数，samples 是采样总数。

    flags 的 bit0 表示测试音，bit1 表示麦克风引脚已配置。
    """

    state: State
    error: int
    session: int
    frames: int
    sample_rate: int
    frame_samples: int
    samples: int
    max_seconds: int
    flags: int

    @classmethod
    def parse(cls, packet: bytes) -> DeviceStatus:
        """校验协议版本和录音格式，拒绝与当前解码器不兼容的状态包。"""
        if len(packet) != STATUS.size:
            raise ValueError("Status packet must contain 20 bytes")
        version, kind, state, *fields = STATUS.unpack(packet)
        if version != 1 or kind != 0x10:
            raise ValueError("Unsupported voice protocol")
        result = cls(State(state), *fields)
        if (
            result.sample_rate != SAMPLE_RATE
            or result.frame_samples != FRAME_SAMPLES
            or not 1 <= result.max_seconds <= 10
            or not 0 <= result.frames <= result.max_seconds * 50
            or result.samples != result.frames * FRAME_SAMPLES
            or result.flags & ~3
        ):
            raise ValueError("Invalid or unsupported recording format")
        return result


def decode_block(block: bytes) -> bytes:
    """解码一个独立 ADPCM 块，返回小端、有符号 PCM16 数据。

    每块自带第一个采样 predictor 和步长索引，不依赖前一帧的解码状态。
    CRC 覆盖除末尾校验值以外的整个块，校验失败时由上层等待设备重传。
    """
    if len(block) < 8:
        raise ValueError("Truncated ADPCM block")
    count, predictor, index, reserved = struct.unpack_from("<HhBB", block)
    if not 1 <= count <= FRAME_SAMPLES or index > 88 or reserved:
        raise ValueError("Invalid ADPCM header")
    if len(block) != 8 + count // 2:
        raise ValueError("Invalid ADPCM length")
    if binascii.crc_hqx(block[:-2], 0xFFFF) != int.from_bytes(block[-2:], "little"):
        raise ValueError("ADPCM checksum mismatch")
    samples = [predictor]
    for i in range(count - 1):
        # 每个编码占 4 bit，一个字节先读低半字节，再读高半字节。
        code = (block[6 + i // 2] >> ((i & 1) * 4)) & 15
        step = STEPS[index]
        diff = step >> 3
        if code & 4:
            diff += step
        if code & 2:
            diff += step >> 1
        if code & 1:
            diff += step >> 2
        predictor += -diff if code & 8 else diff
        # 限幅至 int16 范围，并更新下一采样使用的步长索引。
        predictor = min(32767, max(-32768, predictor))
        index = min(88, max(0, index + INDEX[code & 7]))
        samples.append(predictor)
    return struct.pack(f"<{count}h", *samples)


class ClipAssembler:
    """将同一录音会话的有序分片拼成音频，仅确认完整且通过 CRC 的帧。

    partial 保存未收齐的 ADPCM 块，pcm 累积解码后的音频。
    ACK 丢失时重复确认已接收帧，但不重复追加音频；坏帧等待设备超时重传。
    只有收到 COMPLETE 且所有帧齐全，complete 才设为 True。
    """

    def __init__(self, session: int):
        self.session = session
        self.next_sequence = 0
        self.partial = bytearray()
        self.pcm = bytearray()
        self.expected_frames: int | None = None
        self.complete = False
        self.test_tone = False

    def update(self, status: DeviceStatus) -> None:
        """用状态包记录预期帧数，并在设备完成时检查是否有缺帧。"""
        if status.session != self.session:
            raise ValueError("Status belongs to another recording")
        self.test_tone = bool(status.flags & 1)
        if status.state in (State.SENDING, State.COMPLETE):
            if self.expected_frames is not None and self.expected_frames != status.frames:
                raise ValueError("Recording length changed during transfer")
            if not 1 <= status.frames <= MAX_FRAMES or self.next_sequence > status.frames:
                raise ValueError("Invalid recording length")
            self.expected_frames = status.frames
        if status.state == State.COMPLETE:
            if self.next_sequence != status.frames:
                raise ValueError("Device completed a recording with missing frames")
            self.complete = True

    def feed(self, packet: bytes) -> bytes | None:
        """喂入一个 BLE 分片；完整帧返回 ACK 命令，尚未收齐则返回 None。"""
        if len(packet) <= FRAGMENT.size:
            raise ValueError("Truncated audio fragment")
        version, kind, session, sequence, offset = FRAGMENT.unpack_from(packet)
        if version != 1 or kind != 0x20:
            raise ValueError("Invalid audio fragment type")
        if session != self.session:
            return None
        if sequence >= MAX_FRAMES or offset >= BLOCK_BYTES:
            raise ValueError("Audio fragment out of bounds")
        if sequence < self.next_sequence:
            # 旧帧说明上一次 ACK 可能丢失，再确认即可，不再解码或追加。
            return struct.pack("<BHH", ACK, session, sequence)
        if sequence != self.next_sequence:
            return None
        if self.expected_frames is not None and sequence >= self.expected_frames:
            raise ValueError("More audio frames than announced")
        payload = packet[FRAGMENT.size :]
        if offset + len(payload) > BLOCK_BYTES:
            raise ValueError("Oversized audio fragment")
        if offset == 0:
            self.partial.clear()
        if offset != len(self.partial):
            # 中间缺片时等待下一轮 offset=0 重传，防止拼出错位音频。
            return None
        self.partial.extend(payload)
        if len(self.partial) != BLOCK_BYTES:
            return None
        block = bytes(self.partial)
        self.partial.clear()
        decoded = decode_block(block)
        if len(decoded) != FRAME_SAMPLES * 2:
            raise ValueError("Unexpected audio frame length")
        self.pcm.extend(decoded)
        self.next_sequence += 1
        return struct.pack("<BHH", ACK, session, sequence)
