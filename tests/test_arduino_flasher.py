from pathlib import Path
import tempfile
import unittest

from arduino_flasher import (
    AppOptions,
    AvrdudeProgressParser,
    BootloaderOptions,
    FlashError,
    build_app_command,
    build_bootloader_command,
    validate_hex_file,
)


class HexValidationTests(unittest.TestCase):
    def test_valid_hex(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "firmware.hex"
            path.write_text(":0401000001020304F1\n:00000001FF\n", encoding="ascii")
            info = validate_hex_file(path)
            self.assertEqual(info.data_bytes, 4)
            self.assertEqual(info.lowest_address, 0x100)
            self.assertEqual(info.highest_address, 0x103)

    def test_bad_checksum_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.hex"
            path.write_text(":0401000001020304F0\n:00000001FF\n", encoding="ascii")
            with self.assertRaisesRegex(FlashError, "checksum"):
                validate_hex_file(path)


class CommandTests(unittest.TestCase):
    def test_bootloader_command_contains_safe_uno_sequence(self) -> None:
        command = build_bootloader_command(
            BootloaderOptions(avrdude="avrdude", hex_file="optiboot_atmega328.hex")
        )
        self.assertIn("-e", command)
        self.assertIn("hfuse:w:0xDE:m", command)
        self.assertIn("flash:w:optiboot_atmega328.hex:i", command)
        self.assertEqual(command[-1], "lock:w:0x0F:m")

    def test_usb_programmer_can_omit_port(self) -> None:
        command = build_bootloader_command(
            BootloaderOptions(
                avrdude="avrdude",
                hex_file="optiboot.hex",
                programmer="usbasp",
                port="",
            )
        )
        self.assertNotIn("-P", command)

    def test_app_requires_port(self) -> None:
        with self.assertRaisesRegex(FlashError, "序列埠"):
            build_app_command(AppOptions("avrdude", "app.hex", port=""))


class ProgressTests(unittest.TestCase):
    def test_progress_is_monotonic_across_write_and_verify(self) -> None:
        parser = AvrdudeProgressParser()
        values = [
            parser.feed("avrdude: writing flash (512 bytes):\n"),
            parser.feed("Writing | #### | 50% 0.1s\r"),
            parser.feed("Writing | ######## | 100% 0.2s\n"),
            parser.feed("avrdude: verifying flash memory against image\n"),
            parser.feed("Reading | #### | 50% 0.1s\r"),
            parser.finish(True),
        ]
        self.assertEqual(values, sorted(values))
        self.assertEqual(values[-1], 100)

    def test_reading_input_file_is_not_mistaken_for_verification(self) -> None:
        parser = AvrdudeProgressParser()
        parser.feed("Reading 512 bytes for flash from input file optiboot.hex\n")
        self.assertEqual(parser.phase, "preparing")


if __name__ == "__main__":
    unittest.main()
