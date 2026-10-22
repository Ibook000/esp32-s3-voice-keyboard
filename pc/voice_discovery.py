"""发现蓝牙键盘：显式地址 → 已验证缓存 → Windows 已配对设备 → 广播扫描。

扫描发现不等于验证成功；只有接收器连接并检查语音服务后才写入设备缓存。
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

from voice_protocol import SERVICE_UUID


def read_json(path: Path) -> dict:
    """读取可缺省的状态/缓存；文件不存在、损坏或不是字典时返回空字典。"""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def write_json(path: Path, value: dict) -> None:
    """先写临时文件再替换，避免另一个进程读到写了一半的 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def known_device(address: str, name: str):
    """根据已知地址构造连接目标；Windows 无需再等一次设备广播。"""
    if sys.platform != "win32":
        return address
    from bleak.backends.device import BLEDevice

    if not re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", address):
        raise ValueError("Invalid BLE MAC address")
    # 已连接为 HID 的键盘可能不再广播，但 WinRT 仍可按已知地址连接。
    return BLEDevice(address.upper(), name, details={})


async def paired_windows_devices(name: str) -> list:
    """按设备名查询 Windows 已配对 BLE 设备，并转换为 Bleak 连接目标。"""
    if sys.platform != "win32":
        return []
    from winrt.windows.devices.bluetooth import BluetoothLEDevice
    from winrt.windows.devices.enumeration import DeviceInformation

    selector = BluetoothLEDevice.get_device_selector_from_pairing_state(True)
    # 兼容 PyWinRT 新旧版本对重载方法的不同命名。
    enumerate_devices = getattr(
        DeviceInformation, "find_all_async_aqs_filter", DeviceInformation.find_all_async
    )
    devices = await asyncio.wait_for(enumerate_devices(selector), 10)
    result = []
    for info in devices:
        if info.name.casefold() != name.casefold():
            continue
        device = await asyncio.wait_for(BluetoothLEDevice.from_id_async(info.id), 5)
        if device is not None:
            try:
                digits = f"{device.bluetooth_address:012X}"
                address = ":".join(digits[i : i + 2] for i in range(0, 12, 2))
                result.append(known_device(address, name))
            finally:
                device.close()
    return result


def choose(devices: list):
    """按地址去重；多台匹配设备时要求明确指定，避免误连另一块板子。"""
    unique = {device.address.upper(): device for device in devices}
    if len(unique) > 1:
        raise RuntimeError(
            "Multiple matching keyboards found; select once using --address: " + ", ".join(unique)
        )
    return next(iter(unique.values()), None)


def remember_device(args, device) -> None:
    """连接及服务验证成功后缓存地址；下次启动可以跳过扫描。"""
    folder = getattr(args, "state_dir", None)
    address = getattr(device, "address", None) or getattr(args, "address", None)
    if folder and address:
        write_json(
            Path(folder) / "device.json",
            {"name": args.name, "address": address, "service": SERVICE_UUID},
        )


async def discover_device(args):
    """按优先级寻找目标；重连失败后 ignore_cache 会让程序重新发现设备。"""
    from bleak import BleakScanner

    if args.address:
        return known_device(args.address, args.name)
    folder = getattr(args, "state_dir", None)
    if folder and not getattr(args, "ignore_cache", False):
        cached = read_json(Path(folder) / "device.json")
        if cached.get("name") == args.name and cached.get("service") == SERVICE_UUID:
            try:
                device = known_device(cached["address"], args.name)
                print(f"Using verified keyboard: {cached['address']}", flush=True)
                return device
            except (KeyError, ValueError, TypeError):
                pass
    paired = []
    try:
        paired = await paired_windows_devices(args.name)
    except Exception as exc:
        print(f"Paired-device lookup unavailable; scanning instead: {exc}", flush=True)
    device = choose(paired)
    if device is not None:
        print(f"Found paired keyboard: {device.address}", flush=True)
        return device
    print("Scanning for keyboard...", flush=True)
    discovered = await BleakScanner.discover(timeout=6, return_adv=True)
    # 广播中含语音服务 UUID 的设备优先；没有时才回退到设备名匹配。
    service_matches, name_matches = [], []
    for device, adv in discovered.values():
        if SERVICE_UUID in [uuid.lower() for uuid in adv.service_uuids]:
            service_matches.append(device)
        elif (adv.local_name or device.name or "").casefold() == args.name.casefold():
            name_matches.append(device)
    device = choose(service_matches or name_matches)
    if device is None:
        raise RuntimeError("Keyboard not found; waiting for power/Bluetooth/pairing")
    print(f"Found keyboard: {device.name} ({device.address})", flush=True)
    return device
