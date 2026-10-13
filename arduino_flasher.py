"""Core helpers for flashing an ATmega328P with avrdude.

This module intentionally has no GUI dependencies, which keeps command building,
HEX validation, and progress parsing easy to test.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import re
import shutil
import sys
from typing import Iterable


class FlashError(ValueError):
    """Raised when flash settings or an input file are invalid."""


@dataclass(frozen=True)
class HexInfo:
    data_bytes: int
    lowest_address: int | None
    highest_address: int | None


@dataclass(frozen=True)
class BootloaderOptions:
    avrdude: str
    hex_file: str
    programmer: str = "usbasp"
    port: str = ""
    baud: str = ""
    config_file: str = ""
    bitclock: str = ""
    mcu: str = "atmega328p"
    low_fuse: str = "0xFF"
    high_fuse: str = "0xDE"
    extended_fuse: str = "0xFD"
    unlock_bits: str = "0x3F"
    lock_bits: str = "0x0F"


@dataclass(frozen=True)
class AppOptions:
    avrdude: str
    hex_file: str
    port: str
    baud: str = "115200"
    protocol: str = "arduino"
    config_file: str = ""
    mcu: str = "atmega328p"


def normalize_hex_byte(value: str, label: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"(?:0[xX])?[0-9a-fA-F]{1,2}", value):
        raise FlashError(f"{label} 必須是 00 到 FF 的十六進位數值。")
    return f"0x{int(value, 16):02X}"


def validate_hex_file(path: str | os.PathLike[str]) -> HexInfo:
    """Validate checksums and address ranges in an Intel HEX file."""
    hex_path = Path(path).expanduser()
    if not hex_path.is_file():
        raise FlashError(f"找不到 HEX 檔案：{hex_path}")
    if hex_path.suffix.lower() != ".hex":
        raise FlashError("請選擇副檔名為 .hex 的 Intel HEX 檔案。")

    base_address = 0
    data_bytes = 0
    lowest: int | None = None
    highest: int | None = None
    eof_seen = False

    try:
        lines = hex_path.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as exc:
        raise FlashError(f"無法讀取 HEX 檔案：{exc}") from exc

    if not lines:
        raise FlashError("HEX 檔案是空的。")

    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
            continue
        if eof_seen:
            raise FlashError(f"第 {line_number} 行：EOF 記錄後仍有資料。")
        if not line.startswith(":"):
            raise FlashError(f"第 {line_number} 行：不是 Intel HEX 記錄。")
        payload = line[1:]
        if len(payload) < 10 or len(payload) % 2:
            raise FlashError(f"第 {line_number} 行：記錄長度不正確。")
        try:
            record = bytes.fromhex(payload)
        except ValueError as exc:
            raise FlashError(f"第 {line_number} 行：含有非十六進位字元。") from exc
        if record[0] + 5 != len(record):
            raise FlashError(f"第 {line_number} 行：資料長度欄位不正確。")
        if sum(record) & 0xFF:
            raise FlashError(f"第 {line_number} 行：checksum 錯誤。")

        byte_count = record[0]
        address = (record[1] << 8) | record[2]
        record_type = record[3]
        data = record[4:-1]

        if record_type == 0x00:
            absolute = base_address + address
            if byte_count:
                end = absolute + byte_count - 1
                lowest = absolute if lowest is None else min(lowest, absolute)
                highest = end if highest is None else max(highest, end)
                data_bytes += byte_count
        elif record_type == 0x01:
            if byte_count != 0:
                raise FlashError(f"第 {line_number} 行：EOF 記錄格式錯誤。")
            eof_seen = True
        elif record_type == 0x02:
            if byte_count != 2:
                raise FlashError(f"第 {line_number} 行：段位址記錄格式錯誤。")
            base_address = int.from_bytes(data, "big") << 4
        elif record_type == 0x04:
            if byte_count != 2:
                raise FlashError(f"第 {line_number} 行：線性位址記錄格式錯誤。")
            base_address = int.from_bytes(data, "big") << 16
        elif record_type not in (0x03, 0x05):
            raise FlashError(f"第 {line_number} 行：不支援的記錄類型 0x{record_type:02X}。")

    if not eof_seen:
        raise FlashError("HEX 檔案缺少 EOF 記錄。")
    if data_bytes == 0:
        raise FlashError("HEX 檔案不含可燒錄資料。")
    if highest is not None and highest >= 32 * 1024:
        raise FlashError(
            f"HEX 最高位址 0x{highest:X} 超出 ATmega328P 的 32 KB Flash。"
        )
    return HexInfo(data_bytes, lowest, highest)


def _base_command(avrdude: str, config_file: str, mcu: str, programmer: str) -> list[str]:
    command = [avrdude]
    if config_file.strip():
        command.extend(["-C", config_file.strip()])
    command.extend(["-v", "-p", mcu, "-c", programmer])
    return command


def build_bootloader_command(options: BootloaderOptions) -> list[str]:
    command = _base_command(
        options.avrdude, options.config_file, options.mcu, options.programmer
    )
    if options.port.strip():
        command.extend(["-P", options.port.strip()])
    if options.baud.strip():
        if not options.baud.strip().isdigit():
            raise FlashError("Programmer baud rate 必須是整數。")
        command.extend(["-b", options.baud.strip()])
    if options.bitclock.strip():
        command.extend(["-B", options.bitclock.strip()])

    low = normalize_hex_byte(options.low_fuse, "Low fuse")
    high = normalize_hex_byte(options.high_fuse, "High fuse")
    extended = normalize_hex_byte(options.extended_fuse, "Extended fuse")
    unlock = normalize_hex_byte(options.unlock_bits, "Unlock bits")
    lock = normalize_hex_byte(options.lock_bits, "Lock bits")
    command.extend(
        [
            "-e",
            "-U",
            f"lock:w:{unlock}:m",
            "-U",
            f"efuse:w:{extended}:m",
            "-U",
            f"hfuse:w:{high}:m",
            "-U",
            f"lfuse:w:{low}:m",
            "-U",
            f"flash:w:{options.hex_file}:i",
            "-U",
            f"lock:w:{lock}:m",
        ]
    )
    return command


def build_app_command(options: AppOptions) -> list[str]:
    if not options.port.strip():
        raise FlashError("燒錄 App FW 時必須選擇序列埠。")
    if not options.baud.strip().isdigit():
        raise FlashError("Baud rate 必須是整數。")
    command = _base_command(
        options.avrdude, options.config_file, options.mcu, options.protocol
    )
    command.extend(
        [
            "-P",
            options.port.strip(),
            "-b",
            options.baud.strip(),
            "-D",
            "-U",
            f"flash:w:{options.hex_file}:i",
        ]
    )
    return command


def find_avrdude() -> str:
    """Return the first likely avrdude executable, or a useful default name."""
    on_path = shutil.which("avrdude")
    if on_path:
        return on_path

    candidates: list[Path] = []
    if sys.platform == "darwin":
        candidates.extend(
            [
                Path.home() / "Library/Arduino15/packages/arduino/tools/avrdude",
                Path("/Applications/Arduino IDE.app/Contents/Resources/app/lib/backend/resources"),
            ]
        )
    elif os.name == "nt":
        local = Path(os.environ.get("LOCALAPPDATA", ""))
        candidates.append(local / "Arduino15/packages/arduino/tools/avrdude")
    else:
        candidates.append(Path.home() / ".arduino15/packages/arduino/tools/avrdude")

    for root in candidates:
        if not root.exists():
            continue
        matches = sorted(root.glob("**/bin/avrdude*"), reverse=True)
        for match in matches:
            if match.is_file() and (os.name == "nt" or os.access(match, os.X_OK)):
                return str(match)
    return "avrdude"


def find_optiboot_hex() -> str:
    """Find Arduino AVR core's standard ATmega328P Optiboot image if installed."""
    roots: list[Path] = []
    if sys.platform == "darwin":
        roots.append(Path.home() / "Library/Arduino15/packages/arduino/hardware/avr")
    elif os.name == "nt":
        roots.append(
            Path(os.environ.get("LOCALAPPDATA", ""))
            / "Arduino15/packages/arduino/hardware/avr"
        )
    else:
        roots.append(Path.home() / ".arduino15/packages/arduino/hardware/avr")
    for root in roots:
        if root.exists():
            matches = sorted(
                root.glob("*/bootloaders/optiboot/optiboot_atmega328.hex"),
                reverse=True,
            )
            if matches:
                return str(matches[0])
    return ""


def list_serial_ports() -> list[tuple[str, str]]:
    """List serial ports using pyserial when installed, with a stdlib fallback."""
    try:
        from serial.tools import list_ports  # type: ignore[import-not-found]

        ports = [
            (port.device, port.description or port.device)
            for port in list_ports.comports()
        ]
        return sorted(ports)
    except ImportError:
        pass

    patterns: Iterable[str]
    if sys.platform == "darwin":
        patterns = ("/dev/cu.*", "/dev/tty.*")
    elif os.name == "nt":
        return []
    else:
        patterns = ("/dev/ttyACM*", "/dev/ttyUSB*")
    found: set[str] = set()
    for pattern in patterns:
        found.update(str(path) for path in Path("/").glob(pattern.lstrip("/")))
    return [(port, port) for port in sorted(found)]


class AvrdudeProgressParser:
    """Translate avrdude's per-operation percentages into one overall value."""

    _percent = re.compile(r"(\d{1,3})\s*%")

    def __init__(self) -> None:
        self.phase = "preparing"
        self.value = 2.0
        self._pending = ""

    def feed(self, text: str) -> float:
        self._pending += text
        records = re.split(r"[\r\n]", self._pending)
        self._pending = records.pop()
        for record in records:
            self._consume(record)
        # avrdude may redraw a bar without a newline; parse the live fragment too.
        if "%" in self._pending:
            self._consume(self._pending, update_phase=False)
        return self.value

    def _consume(self, record: str, update_phase: bool = True) -> None:
        lowered = record.lower()
        if update_phase:
            if "writing" in lowered and "flash" in lowered:
                self.phase = "writing"
                self.value = max(self.value, 10.0)
            elif ("verifying" in lowered and "flash" in lowered) or (
                self.phase == "writing"
                and "reading" in lowered
                and "flash" in lowered
                and "input file" not in lowered
            ):
                self.phase = "verifying"
                self.value = max(self.value, 68.0)

        matches = self._percent.findall(record)
        if not matches:
            return
        percent = min(100, int(matches[-1]))
        if self.phase == "writing":
            mapped = 10 + percent * 0.55
        elif self.phase == "verifying":
            mapped = 68 + percent * 0.27
        else:
            mapped = min(9, 2 + percent * 0.07)
        self.value = max(self.value, mapped)

    def finish(self, succeeded: bool) -> float:
        if succeeded:
            self.value = 100.0
        return self.value
