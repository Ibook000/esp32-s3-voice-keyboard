"""网页、BLE 客户端共用的按键配置校验与二进制协议。"""

from __future__ import annotations

VERSION = 1
CONFIG_UUID = "cba10004-7d2f-4a6b-9c11-18d5e0010001"
STATUS_UUID = "cba10005-7d2f-4a6b-9c11-18d5e0010001"
BEGIN, STAGE, COMMIT, ABORT, STAGE_ROTARY, STAGE_SLEEP = 1, 2, 3, 4, 5, 6
DEFAULT_KEYS = [
    {"id": 1, "label": "复制", "modifiers": 1, "key": 6},
    {"id": 2, "label": "粘贴", "modifiers": 1, "key": 25},
    {"id": 3, "label": "撤销", "modifiers": 1, "key": 29},
]
ERRORS = {
    1: "配置格式无效，请检查按键名称和组合键。",
    2: "配置会话已失效，请重新保存；若另一窗口正在配置，请先结束操作。",
    3: "配置尚未完整传输，请重新保存。",
    4: "请松开实体按键、旋钮，并结束录音或发送后重试。",
    5: "键盘未能保存配置，原设置仍然保留。",
}


def validate_keys(value):
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError("需要完整的三个快捷键配置。")
    result = []
    for index, binding in enumerate(value, 1):
        if (
            not isinstance(binding, dict)
            or type(binding.get("id")) is not int
            or binding["id"] != index
        ):
            raise ValueError("按键顺序必须为 KEY1、KEY2、KEY3。")
        modifiers, key, label = binding.get("modifiers"), binding.get("key"), binding.get("label")
        if type(modifiers) is not int or not 0 <= modifiers <= 255:
            raise ValueError("修饰键配置无效。")
        if type(key) is not int or not 4 <= key <= 0x65:
            raise ValueError("请选择一个普通键。")
        if not isinstance(label, str) or not label.strip() or label != label.strip():
            raise ValueError("显示名称不能为空，首尾不能带空格。")
        if any(ord(c) < 32 or ord(c) == 127 for c in label):
            raise ValueError("显示名称不能包含控制字符。")
        if sum(1 if ord(c) < 128 else 2 for c in label) > 4:
            raise ValueError("显示名称最多 2 个汉字或 4 个英文字符。")
        try:
            label.encode("gb2312")
        except UnicodeEncodeError as exc:
            raise ValueError("显示名称请使用常用汉字、英文或数字。") from exc
        if len(label.encode("utf-8")) > 12:
            raise ValueError("显示名称过长。")
        result.append({"id": index, "label": label, "modifiers": modifiers, "key": key})
    return result


def stage_packet(tx, index, binding):
    label = binding["label"].encode("utf-8")
    return (
        bytes([VERSION, STAGE, tx, index, binding["modifiers"], binding["key"], len(label)]) + label
    )


def decode_response(data):
    if len(data) not in (53, 54, 56) or data[0] != VERSION or (len(data) >= 54 and data[53] > 1):
        raise ValueError("键盘配置协议不匹配，请升级本项目的配套固件。")
    if len(data) == 56 and int.from_bytes(data[54:56], "little") > 120:
        raise ValueError("深睡时间超出范围。")
    keys = []
    for index in range(3):
        offset = 8 + index * 15
        size = data[offset + 2]
        if size > 12:
            raise ValueError("键盘返回的名称长度无效。")
        keys.append(
            {
                "id": index + 1,
                "modifiers": data[offset],
                "key": data[offset + 1],
                "label": bytes(data[offset + 3 : offset + 3 + size]).decode("utf-8"),
            }
        )
    return {
        "result": data[1],
        "transaction": data[2],
        "operation": data[3],
        "index": data[4],
        "mask": data[5],
        "revision": int.from_bytes(data[6:8], "little"),
        "keys": validate_keys(keys),
        "sleep_minutes": int.from_bytes(data[54:56], "little") if len(data) == 56 else None,
        "rotary_mode": ("volume" if data[53] else "scroll") if len(data) >= 54 else None,
    }
