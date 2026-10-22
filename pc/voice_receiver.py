"""PC 接收入口：连接蓝牙键盘，保存录音，并按需识别中文、输入当前窗口。

阅读顺序：main → receive → voice_protocol.ClipAssembler → voice_audio.SpeechOutput。
直接运行是前台交互模式；--daemon 由 voice_background 管理重连和退出。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import math
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from voice_audio import SpeechOutput, save_wav
from voice_protocol import (
    AUDIO_UUID,
    CANCEL,
    CONTROL_UUID,
    ERRORS,
    SAMPLE_RATE,
    SERVICE_UUID,
    START,
    STATUS_UUID,
    STOP,
    TEST_TONE,
    ClipAssembler,
    DeviceStatus,
    State,
)


def console_reader(loop: asyncio.AbstractEventLoop, events: asyncio.Queue) -> None:
    """用独立线程等待终端输入，避免 input() 阻塞蓝牙接收循环。"""

    def read() -> None:
        while True:
            try:
                command = input().strip().lower()
            except EOFError:
                return
            try:
                loop.call_soon_threadsafe(events.put_nowait, ("command", command))
            except RuntimeError:
                return
            if command == "q":
                return

    threading.Thread(target=read, name="voice-console", daemon=True).start()


async def find_device(args: argparse.Namespace):
    """延迟导入蓝牙发现模块，让 --help 和协议测试不依赖蓝牙环境。"""
    from voice_discovery import discover_device

    return await discover_device(args)


async def handle_recording_command(
    client, command: str, current: DeviceStatus | None
) -> bytes | None:
    """把终端调试命令转为 BLE 操作，并返回最新状态包供会话循环处理。

    空字符串代表终端 Enter；录音中执行停止，空闲时执行开始。
    传输中拒绝开始下一段；未知命令直接忽略。退出命令由会话循环处理。
    """
    if command not in ("", "start", "stop", "n", "x"):
        return None
    if command == "x":
        op = CANCEL
    elif command == "stop" or (command == "" and current and current.state == State.RECORDING):
        op = STOP
    elif current and current.state in (State.RECORDING, State.SENDING):
        print("Device is busy; wait, stop, or cancel with x.", flush=True)
        return None
    else:
        op = TEST_TONE if command == "n" else START
    await client.write_gatt_char(CONTROL_UUID, bytes([op]), response=True)
    # 等设备处理控制命令后立即读状态，连续按 Enter 时才能正确切换开始/停止。
    await asyncio.sleep(0.04)
    return bytes(await client.read_gatt_char(STATUS_UUID))


def save_clip(clip: ClipAssembler, folder: Path) -> Path:
    """为完整录音生成唯一文件名；PCM16 每个采样占 2 字节。"""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = folder / f"voice_{stamp}_{clip.session}.wav"
    save_wav(path, bytes(clip.pcm))
    seconds = len(clip.pcm) / (SAMPLE_RATE * 2)
    print(f"Saved {seconds:.2f}s: {path.resolve()}", flush=True)
    return path


@contextlib.asynccontextmanager
async def borrowed_connection(client):
    """工作台提供已有连接：只清理语音订阅，连接仍供快捷键配置使用。"""
    try:
        yield client
    finally:
        for uuid in (AUDIO_UUID, STATUS_UUID):
            if client.is_connected:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(client.stop_notify(uuid), 3)


async def receive(args: argparse.Namespace, output: SpeechOutput, *, client=None) -> None:
    """处理一次蓝牙连接；断线向外抛错，由后台模式决定是否重连。

    所有通知、终端调试命令和断线都汇入 events，串行更新录音状态。
    完整录音交给工作线程识别，主循环继续及时确认后续音频帧。
    """
    from bleak import BleakClient

    from voice_discovery import known_device, remember_device

    supplied = client is not None
    device = known_device(args.address, args.name) if supplied else await find_device(args)
    events: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    clip: ClipAssembler | None = None  # 当前会话的分片缓存和已解码音频。
    saved_session: int | None = None  # 状态可能重复通知，同一会话只保存一次。
    current: DeviceStatus | None = None
    last_progress = time.monotonic()
    last_status_packet: bytes | None = None

    def disconnected(_):
        loop.call_soon_threadsafe(events.put_nowait, ("disconnected", None))

    async def delayed_stop():
        await asyncio.sleep(args.duration)
        await events.put(("command", "stop"))

    stop_task = None
    runtime = getattr(args, "runtime", None)
    if runtime:
        runtime.update(
            status="connecting", connected=False, address=getattr(device, "address", args.address)
        )
    speech_tasks: set[asyncio.Task] = set()
    speech_lock = asyncio.Lock()  # 同一个识别模型一次只处理一段录音。

    async def process_saved_clip(path: Path, test_tone: bool):
        async with speech_lock:
            try:
                await asyncio.to_thread(output.process, path, test_tone)
            except Exception as exc:
                await events.put(("speech_error", exc))

    # 1. 仅访问自定义语音服务；HID 键盘的受保护属性由 Windows 管理。
    client = client or BleakClient(
        device,
        services=[SERVICE_UUID],
        disconnected_callback=disconnected,
        pair=sys.platform != "darwin",
        timeout=30,
        winrt={"use_cached_services": False},
    )
    async with borrowed_connection(client) if supplied else client:
        if client.services.get_service(SERVICE_UUID) is None:
            raise RuntimeError(
                "Voice service not discovered. If voice firmware is installed, refresh this keyboard's Windows pairing/GATT cache."
            )
        missing = [
            uuid
            for uuid in (CONTROL_UUID, AUDIO_UUID, STATUS_UUID)
            if client.services.get_characteristic(uuid) is None
        ]
        if missing:
            raise RuntimeError(
                "Voice service has incomplete characteristics; refresh this keyboard's Windows pairing/GATT cache."
            )
        await client.start_notify(
            STATUS_UUID, lambda _, data: events.put_nowait(("status", bytes(data)))
        )
        await client.start_notify(
            AUDIO_UUID, lambda _, data: events.put_nowait(("audio", bytes(data)))
        )
        initial = bytes(await client.read_gatt_char(STATUS_UUID))
        current = DeviceStatus.parse(initial)
        print("Voice notifications subscribed; microphone capture is available.", flush=True)
        remember_device(args, device)
        # 旧进程确认过的音频已经丢失，重连后必须取消旧会话，不能接着拼接。
        if current.state in (State.RECORDING, State.SENDING):
            await client.write_gatt_char(CONTROL_UUID, bytes([CANCEL]), response=True)
            await asyncio.sleep(0.15)
            current = DeviceStatus.parse(bytes(await client.read_gatt_char(STATUS_UUID)))
            while not events.empty():
                kind, _ = events.get_nowait()
                if kind == "disconnected":
                    raise RuntimeError("Keyboard disconnected while resetting its old session")
            print("Cancelled an unfinished session from the previous receiver.", flush=True)
        if runtime:
            runtime.update(
                status="connected",
                connected=True,
                address=getattr(device, "address", args.address),
                last_error=None,
            )
        print(f"Connected; ATT MTU={client.mtu_size}.", flush=True)
        if args.type_text:
            print(
                "Text input enabled: focus the destination window before releasing the physical voice button.",
                flush=True,
            )
        # 2. 日常由键盘 REC 按钮控制；定时录制和前台终端命令仅用于调试。
        if args.duration is not None:
            await client.write_gatt_char(
                CONTROL_UUID, bytes([TEST_TONE if args.test_tone else START]), response=True
            )
            stop_task = asyncio.create_task(delayed_stop())
        elif not getattr(args, "daemon", False):
            print("Enter=start/stop, n=test tone, x=cancel, q=quit.", flush=True)
            console_reader(loop, events)

        next_poll = time.monotonic() + 1
        try:
            while True:
                # 3. 每秒补读状态，弥补完成/错误通知丢失；超时不保存残缺音频。
                if not client.is_connected:
                    raise RuntimeError("Keyboard disconnected; incomplete audio was not saved")
                now = time.monotonic()
                if now >= next_poll:
                    # 状态特征可重复读取，不依赖通知一定送达。
                    packet = bytes(await client.read_gatt_char(STATUS_UUID))
                    await events.put(("status", packet))
                    next_poll = time.monotonic() + 1
                if clip is not None and not clip.complete and now - last_progress > args.timeout:
                    raise RuntimeError("Recording/transfer stalled; incomplete audio was not saved")
                if args.duration is not None and now - last_progress > args.timeout:
                    raise RuntimeError("Device did not finish the requested recording")
                try:
                    kind, data = await asyncio.wait_for(events.get(), 0.25)
                except asyncio.TimeoutError:
                    continue

                if kind == "disconnected":
                    raise RuntimeError("Keyboard disconnected; incomplete audio was not saved")
                if kind == "speech_error":
                    print(f"Speech recognition failed; WAV is retained: {data}", file=sys.stderr)
                    continue
                if kind == "command":
                    if data == "q":
                        break
                    packet = await handle_recording_command(client, data, current)
                    if packet is not None:
                        current = DeviceStatus.parse(packet)
                        await events.put(("status", packet))
                    continue

                # 4. 状态通知决定会话边界；音频通知负责拼片、校验、解码和 ACK。
                if kind == "status":
                    current = DeviceStatus.parse(data)
                    changed = data != last_status_packet
                    if changed:
                        print(
                            f"Voice {current.state.name.lower()}; session={current.session}; frames={current.frames}",
                            flush=True,
                        )
                        last_status_packet = data
                        last_progress = time.monotonic()
                        if runtime:
                            runtime.update(status=current.state.name.lower(), connected=True)
                    if current.state == State.ERROR:
                        message = ERRORS.get(current.error, f"Device error {current.error}")
                        clip = None
                        if args.duration is not None:
                            raise RuntimeError(message)
                        if changed:
                            print(message, flush=True)
                        continue
                    if current.state in (State.RECORDING, State.SENDING):
                        if clip is None or clip.session != current.session:
                            clip = ClipAssembler(current.session)
                            saved_session = None
                        clip.update(current)
                    elif (
                        current.state == State.COMPLETE and clip and clip.session == current.session
                    ):
                        clip.update(current)

                elif kind == "audio" and clip:
                    try:
                        ack = clip.feed(data)
                    except ValueError as exc:
                        print(f"Waiting for frame retry: {exc}", flush=True)
                        continue
                    if ack is not None:
                        await client.write_gatt_char(CONTROL_UUID, ack, response=True)
                        last_progress = time.monotonic()

                if clip and clip.complete and saved_session != clip.session:
                    # 5. 收齐全部帧且设备报告 COMPLETE，才保存 WAV 并启动识别。
                    path = save_clip(clip, args.output)
                    saved_session = clip.session
                    # 单次模式等识别结束再退出；常驻模式在工作线程排队识别。
                    if args.duration is not None:
                        await asyncio.to_thread(output.process, path, clip.test_tone)
                    else:
                        task = asyncio.create_task(process_saved_clip(path, clip.test_tone))
                        speech_tasks.add(task)
                        task.add_done_callback(speech_tasks.discard)
                    last_progress = time.monotonic()
                    if args.duration is not None:
                        break
        finally:
            # 6. 退出时取消定时任务/未完成录音，等待已保存录音识别。
            if runtime:
                runtime.update(connected=False)
            if stop_task:
                stop_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await stop_task
            if (
                client.is_connected
                and current is not None
                and current.state in (State.RECORDING, State.SENDING)
            ):
                with contextlib.suppress(Exception):
                    await client.write_gatt_char(CONTROL_UUID, bytes([CANCEL]), response=True)
            if speech_tasks:
                await asyncio.gather(*speech_tasks)


def parse_args(argv=None) -> argparse.Namespace:
    """解析前台/常驻共用参数，并在连接设备前检查无效组合。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="ibook Keyboard", help="设备名称，默认 ibook Keyboard")
    parser.add_argument("--address", help="指定 BLE MAC 地址；多台同名设备时用于明确选择")
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Windows 常驻模式：自动发现、断线重连和日志，使用键盘 REC 按钮录音",
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=Path(__file__).parent / "runtime",
        help="状态、日志和设备地址缓存目录，默认 pc/runtime",
    )
    parser.add_argument(
        "--retry-delay", type=float, default=3, help="重连等待基数（秒），默认 3，上限 30"
    )
    parser.add_argument(
        "--duration",
        type=float,
        help="录制指定秒数，接收一段后退出，范围 0.1～10 秒",
    )
    parser.add_argument(
        "--test-tone",
        action="store_true",
        help="接收设备生成的 440 Hz 测试音，默认 3 秒，无需麦克风",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parent / "recordings",
        help="WAV 和转写文本的保存目录，默认 pc/recordings",
    )
    parser.add_argument(
        "--timeout", type=float, default=120, help="接收无进展超时（秒），默认 120，至少 15"
    )
    parser.add_argument(
        "--transcribe", action="store_true", help="使用 faster-whisper 在本地识别语音"
    )
    parser.add_argument(
        "--model", default="small", help="多语言 Whisper 模型名称或本地目录，默认 small"
    )
    parser.add_argument("--language", default="zh", help="识别语言，默认 zh（中文）")
    parser.add_argument(
        "--type-text",
        action="store_true",
        help="把识别文字输入当前焦点窗口，需 Windows 和 --transcribe",
    )
    args = parser.parse_args(argv)
    if args.test_tone and args.duration is None:
        args.duration = 3
    if args.duration is not None and not 0.1 <= args.duration <= 10:
        parser.error("--duration must be between 0.1 and 10 seconds")
    if not math.isfinite(args.timeout) or args.timeout < 15:
        parser.error("--timeout must be at least 15 seconds")
    if args.type_text and (not args.transcribe or sys.platform != "win32"):
        parser.error("--type-text requires --transcribe and Windows")
    if args.daemon and (sys.platform != "win32" or args.duration is not None):
        parser.error("--daemon requires Windows and cannot be combined with timed recordings")
    if not math.isfinite(args.retry_delay) or args.retry_delay < 0.1:
        parser.error("--retry-delay must be a finite number >= 0.1")
    return args


def main() -> int:
    """先准备模型，再运行接收器；常驻模式额外管理互斥锁、日志和状态文件。"""
    args = parse_args()
    output = SpeechOutput(args)
    instance = None
    runtime = None
    try:
        if args.daemon:
            from voice_background import Runtime, SingleInstance, configure_logging

            instance = SingleInstance(args.state_dir)
            configure_logging(args.state_dir)
            runtime = Runtime(args.state_dir)
            args.runtime = runtime
            runtime.update(status="loading_model")
        output.prepare()
        if args.daemon:
            from voice_background import run_daemon

            asyncio.run(run_daemon(args, output, receive))
        else:
            asyncio.run(receive(args, output))
    except KeyboardInterrupt:
        print("Stopped.")
        return 130
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        if runtime:
            runtime.update(last_error=str(exc))
        return 1
    finally:
        if runtime:
            runtime.update(status="stopped", connected=False)
        if instance:
            instance.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
