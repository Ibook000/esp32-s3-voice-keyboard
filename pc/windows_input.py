"""Windows 输入适配：将识别文字通过 Unicode 模拟键盘事件输入当前窗口。

蓝牙数据处理与 Windows API 分离；本模块只负责识别后的文字输入。
"""

from __future__ import annotations

import ctypes


def type_text_windows(text: str) -> None:
    """逐个 UTF-16 单元发送按下/松开事件；不改剪贴板，不自动按 Enter。"""
    from ctypes import wintypes as w

    class Keyboard(ctypes.Structure):
        _fields_ = [
            ("vk", w.WORD),
            ("scan", w.WORD),
            ("flags", w.DWORD),
            ("time", w.DWORD),
            ("extra", ctypes.c_size_t),
        ]

    class Mouse(ctypes.Structure):
        # INPUT 是联合体，即使只发送键盘事件，也要保留鼠标结构以保证尺寸正确。
        _fields_ = [
            ("dx", w.LONG),
            ("dy", w.LONG),
            ("data", w.DWORD),
            ("flags", w.DWORD),
            ("time", w.DWORD),
            ("extra", ctypes.c_size_t),
        ]

    class Hardware(ctypes.Structure):
        _fields_ = [("message", w.DWORD), ("low", w.WORD), ("high", w.WORD)]

    class Union(ctypes.Union):
        _fields_ = [("keyboard", Keyboard), ("mouse", Mouse), ("hardware", Hardware)]

    class Input(ctypes.Structure):
        _fields_ = [("type", w.DWORD), ("value", Union)]

    send = ctypes.WinDLL("user32", use_last_error=True).SendInput
    send.argtypes = [w.UINT, ctypes.POINTER(Input), ctypes.c_int]
    send.restype = w.UINT
    # 中文通常占一个 UTF-16 单元；emoji 等字符由一对代理项表示。
    encoded = text.encode("utf-16-le")
    for i in range(0, len(encoded), 2):
        code = int.from_bytes(encoded[i : i + 2], "little")
        events = (Input * 2)(
            # type=1 表示键盘；0x0004 为 Unicode，0x0002 为松开。
            Input(1, Union(keyboard=Keyboard(0, code, 0x0004, 0, 0))),
            Input(1, Union(keyboard=Keyboard(0, code, 0x0006, 0, 0))),
        )
        if send(2, events, ctypes.sizeof(Input)) != 2:
            raise OSError("Windows rejected text input; check target window permissions")
