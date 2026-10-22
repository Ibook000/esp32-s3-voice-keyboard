"""解析固件 HID 描述符，检查键盘和鼠标滚轮报告的位宽与相对坐标属性。"""

from __future__ import annotations

import re
import unittest
from collections import defaultdict
from pathlib import Path


class HidReportTests(unittest.TestCase):
    def test_keyboard_and_relative_wheel_report_layouts(self):
        source = (Path(__file__).resolve().parents[2] / "src/keyboard_ble.cpp").read_text(
            encoding="utf-8"
        )
        source = re.sub(r"//[^\n]*|/\*.*?\*/", "", source, flags=re.S)
        body = source.split("uint8_t reportMap[] = {", 1)[1].split("};", 1)[0]
        data = bytes(int(value, 16) for value in re.findall(r"0x([0-9A-Fa-f]+)", body))
        bits = defaultdict(int)
        report_id = report_size = report_count = page = 0
        usages = []
        wheel = None
        consumer = None
        index = 0
        while index < len(data):
            prefix = data[index]
            index += 1
            self.assertNotEqual(prefix, 0xFE, "Long HID items require extending this parser")
            size = (0, 1, 2, 4)[prefix & 3]
            self.assertLessEqual(index + size, len(data), "Truncated HID item")
            value = int.from_bytes(data[index : index + size], "little")
            index += size
            kind, tag = (prefix >> 2) & 3, prefix >> 4
            if kind == 1:
                if tag == 0:
                    page = value
                elif tag == 7:
                    report_size = value
                elif tag == 8:
                    report_id = value
                elif tag == 9:
                    report_count = value
            elif kind == 2 and tag == 0:
                usages.append(value)
            elif kind == 0:
                if tag in (8, 9):
                    bits[report_id, tag] += report_size * report_count
                if tag == 8 and page == 1 and 0x38 in usages:
                    wheel = (report_id, report_size, report_count, value, list(usages))
                if tag == 8 and page == 0x0C:
                    consumer = (report_id, report_size, report_count, value)
                usages.clear()
        self.assertEqual(dict(bits), {(1, 8): 64, (1, 9): 8, (2, 8): 32, (3, 8): 16})
        self.assertEqual(consumer, (3, 16, 1, 0x00))
        self.assertEqual(wheel, (2, 8, 3, 0x06, [0x30, 0x31, 0x38]))


if __name__ == "__main__":
    unittest.main()
