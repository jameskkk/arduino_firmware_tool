"""Tkinter GUI for ATmega328P bootloader and application flashing."""

from __future__ import annotations

import codecs
from datetime import datetime
import os
from pathlib import Path
import queue
import shlex
import signal
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from arduino_flasher import (
    AppOptions,
    AvrdudeProgressParser,
    BootloaderOptions,
    FlashError,
    build_app_command,
    build_bootloader_command,
    find_avrdude,
    find_optiboot_hex,
    list_serial_ports,
    validate_hex_file,
)


APP_VERSION = "1.0.0"
APP_TITLE = f"ATmega328P Firmware Tool v{APP_VERSION}"


class FirmwareTool(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("900x700")
        self.minsize(760, 600)

        self._events: queue.Queue[tuple[str, object]] = queue.Queue()
        self._process: subprocess.Popen[bytes] | None = None
        self._worker: threading.Thread | None = None
        self._running = False
        self._port_map: dict[str, str] = {}

        self.mode = tk.StringVar(value="bootloader")
        self.hex_path = tk.StringVar(value=find_optiboot_hex())
        self.avrdude_path = tk.StringVar(value=find_avrdude())
        self.config_path = tk.StringVar()
        self.port = tk.StringVar()
        self.programmer = tk.StringVar(value="usbasp")
        self.boot_baud = tk.StringVar()
        self.bitclock = tk.StringVar()
        self.app_protocol = tk.StringVar(value="arduino")
        self.app_baud = tk.StringVar(value="115200")
        self.low_fuse = tk.StringVar(value="0xFF")
        self.high_fuse = tk.StringVar(value="0xDE")
        self.extended_fuse = tk.StringVar(value="0xFD")
        self.unlock_bits = tk.StringVar(value="0x3F")
        self.lock_bits = tk.StringVar(value="0x0F")
        self.status_text = tk.StringVar(value="就緒")
        self.progress = tk.DoubleVar(value=0)

        self._build_ui()
        self._mode_changed()
        self._refresh_ports()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(80, self._poll_events)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=14)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(4, weight=1)

        mode_box = ttk.LabelFrame(root, text="燒錄類型", padding=10)
        mode_box.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Radiobutton(
            mode_box,
            text="Bootloader（需 ISP Programmer，會設定 Uno fuse）",
            variable=self.mode,
            value="bootloader",
            command=self._mode_changed,
        ).pack(side="left", padx=(0, 24))
        ttk.Radiobutton(
            mode_box,
            text="一般 App FW（透過 Optiboot 序列埠）",
            variable=self.mode,
            value="app",
            command=self._mode_changed,
        ).pack(side="left")

        file_box = ttk.LabelFrame(root, text="工具與韌體", padding=10)
        file_box.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        file_box.columnconfigure(1, weight=1)
        self._path_row(file_box, 0, "HEX 檔案", self.hex_path, self._browse_hex)
        self._path_row(file_box, 1, "avrdude", self.avrdude_path, self._browse_avrdude)
        self._path_row(
            file_box, 2, "avrdude.conf（選填）", self.config_path, self._browse_config
        )

        settings = ttk.LabelFrame(root, text="燒錄設定", padding=10)
        settings.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        settings.columnconfigure(1, weight=1)
        settings.columnconfigure(3, weight=1)

        ttk.Label(settings, text="連接埠").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.port_combo = ttk.Combobox(settings, textvariable=self.port)
        self.port_combo.grid(row=0, column=1, columnspan=2, sticky="ew")
        ttk.Button(settings, text="重新整理", command=self._refresh_ports).grid(
            row=0, column=3, sticky="e", padx=(8, 0)
        )

        self.boot_frame = ttk.Frame(settings)
        self.boot_frame.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(10, 0))
        for col in range(6):
            self.boot_frame.columnconfigure(col, weight=1 if col % 2 else 0)
        ttk.Label(self.boot_frame, text="Programmer").grid(row=0, column=0, sticky="w")
        programmer_combo = ttk.Combobox(
            self.boot_frame,
            textvariable=self.programmer,
            values=("usbasp", "arduino", "stk500v1", "atmelice_isp", "usbtiny"),
        )
        programmer_combo.grid(row=0, column=1, sticky="ew", padx=(8, 16))
        ttk.Label(self.boot_frame, text="Baud（選填）").grid(row=0, column=2, sticky="w")
        ttk.Entry(self.boot_frame, textvariable=self.boot_baud, width=10).grid(
            row=0, column=3, sticky="ew", padx=(8, 16)
        )
        ttk.Label(self.boot_frame, text="Bitclock -B（選填）").grid(
            row=0, column=4, sticky="w"
        )
        ttk.Entry(self.boot_frame, textvariable=self.bitclock, width=10).grid(
            row=0, column=5, sticky="ew", padx=(8, 0)
        )

        fuse_values = (
            ("Low", self.low_fuse),
            ("High", self.high_fuse),
            ("Extended", self.extended_fuse),
            ("Unlock", self.unlock_bits),
            ("Lock", self.lock_bits),
        )
        for index, (label, variable) in enumerate(fuse_values):
            ttk.Label(self.boot_frame, text=label).grid(
                row=1, column=index, sticky="w", pady=(10, 0)
            )
            ttk.Entry(self.boot_frame, textvariable=variable, width=8).grid(
                row=2, column=index, sticky="ew", padx=(0, 8)
            )

        self.app_frame = ttk.Frame(settings)
        self.app_frame.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(10, 0))
        self.app_frame.columnconfigure(1, weight=1)
        self.app_frame.columnconfigure(3, weight=1)
        ttk.Label(self.app_frame, text="Protocol").grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            self.app_frame,
            textvariable=self.app_protocol,
            values=("arduino", "wiring", "stk500v1"),
        ).grid(row=0, column=1, sticky="ew", padx=(8, 16))
        ttk.Label(self.app_frame, text="Baud").grid(row=0, column=2, sticky="w")
        ttk.Combobox(
            self.app_frame,
            textvariable=self.app_baud,
            values=("115200", "57600", "19200"),
        ).grid(row=0, column=3, sticky="ew", padx=(8, 0))

        action_frame = ttk.Frame(root)
        action_frame.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        action_frame.columnconfigure(0, weight=1)
        self.progress_bar = ttk.Progressbar(
            action_frame, variable=self.progress, maximum=100, mode="determinate"
        )
        self.progress_bar.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        ttk.Label(action_frame, textvariable=self.status_text, width=20).grid(
            row=0, column=1, padx=(0, 10)
        )
        self.start_button = ttk.Button(
            action_frame, text="開始燒錄", command=self._start_flash
        )
        self.start_button.grid(row=0, column=2, padx=(0, 8))
        self.cancel_button = ttk.Button(
            action_frame, text="取消", command=self._cancel_flash, state="disabled"
        )
        self.cancel_button.grid(row=0, column=3)

        log_box = ttk.LabelFrame(root, text="燒錄訊息", padding=8)
        log_box.grid(row=4, column=0, sticky="nsew")
        log_box.columnconfigure(0, weight=1)
        log_box.rowconfigure(0, weight=1)
        self.log = tk.Text(
            log_box,
            wrap="word",
            state="disabled",
            font=("TkFixedFont", 11),
            background="#111827",
            foreground="#e5e7eb",
            insertbackground="white",
        )
        self.log.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(log_box, orient="vertical", command=self.log.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scrollbar.set)

    @staticmethod
    def _path_row(
        parent: ttk.LabelFrame,
        row: int,
        label: str,
        variable: tk.StringVar,
        callback: object,
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=3)
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=3)
        ttk.Button(parent, text="瀏覽…", command=callback).grid(
            row=row, column=2, padx=(8, 0), pady=3
        )

    def _browse_hex(self) -> None:
        initial = Path(self.hex_path.get()).expanduser().parent
        selected = filedialog.askopenfilename(
            title="選擇 Intel HEX 韌體",
            initialdir=initial if initial.is_dir() else Path.cwd(),
            filetypes=(("Intel HEX", "*.hex"), ("所有檔案", "*.*")),
        )
        if selected:
            self.hex_path.set(selected)

    def _browse_avrdude(self) -> None:
        selected = filedialog.askopenfilename(title="選擇 avrdude 執行檔")
        if selected:
            self.avrdude_path.set(selected)

    def _browse_config(self) -> None:
        selected = filedialog.askopenfilename(
            title="選擇 avrdude.conf",
            filetypes=(("avrdude config", "*.conf"), ("所有檔案", "*.*")),
        )
        if selected:
            self.config_path.set(selected)

    def _mode_changed(self) -> None:
        if self.mode.get() == "bootloader":
            self.boot_frame.grid()
            self.app_frame.grid_remove()
        else:
            self.boot_frame.grid_remove()
            self.app_frame.grid()

    def _refresh_ports(self) -> None:
        current = self.port.get()
        ports = list_serial_ports()
        self._port_map = {
            f"{device} — {description}" if description != device else device: device
            for device, description in ports
        }
        values = list(self._port_map)
        self.port_combo["values"] = values
        if current in self._port_map or current in self._port_map.values():
            return
        if values:
            self.port.set(values[0])

    def _actual_port(self) -> str:
        displayed = self.port.get().strip()
        return self._port_map.get(displayed, displayed)

    def _build_command(self) -> tuple[list[str], object]:
        hex_path = str(Path(self.hex_path.get()).expanduser())
        info = validate_hex_file(hex_path)
        avrdude = self.avrdude_path.get().strip()
        if not avrdude:
            raise FlashError("請指定 avrdude 執行檔。")
        if os.sep in avrdude or (os.altsep and os.altsep in avrdude):
            executable = Path(avrdude).expanduser()
            if not executable.is_file():
                raise FlashError(f"找不到 avrdude：{executable}")
            avrdude = str(executable)

        if self.mode.get() == "bootloader":
            programmer = self.programmer.get().strip()
            # USB programmers address the device over USB and must not receive a
            # randomly selected UART path. Serial-based ISP programmers do need it.
            usb_programmers = {"usbasp", "atmelice_isp", "usbtiny"}
            programmer_port = "" if programmer in usb_programmers else self._actual_port()
            options = BootloaderOptions(
                avrdude=avrdude,
                config_file=self.config_path.get(),
                hex_file=hex_path,
                programmer=programmer,
                port=programmer_port,
                baud=self.boot_baud.get(),
                bitclock=self.bitclock.get(),
                low_fuse=self.low_fuse.get(),
                high_fuse=self.high_fuse.get(),
                extended_fuse=self.extended_fuse.get(),
                unlock_bits=self.unlock_bits.get(),
                lock_bits=self.lock_bits.get(),
            )
            if not options.programmer:
                raise FlashError("請指定 ISP Programmer。")
            command = build_bootloader_command(options)
        else:
            options = AppOptions(
                avrdude=avrdude,
                config_file=self.config_path.get(),
                hex_file=hex_path,
                port=self._actual_port(),
                baud=self.app_baud.get(),
                protocol=self.app_protocol.get().strip(),
            )
            if not options.protocol:
                raise FlashError("請指定 avrdude protocol。")
            command = build_app_command(options)
        return command, info

    def _start_flash(self) -> None:
        if self._running:
            return
        try:
            command, info = self._build_command()
        except FlashError as exc:
            messagebox.showerror("設定錯誤", str(exc), parent=self)
            return

        kind = "Bootloader" if self.mode.get() == "bootloader" else "App FW"
        if self.mode.get() == "bootloader":
            confirmed = messagebox.askyesno(
                "確認燒錄 Bootloader",
                "此操作會執行 Chip Erase、改寫 fuse/lock bits，並燒錄 Bootloader。\n\n"
                "請確認目標為 ATmega328P，且 ISP 接線與電壓正確。是否繼續？",
                icon="warning",
                parent=self,
            )
            if not confirmed:
                return

        self._clear_log()
        address = (
            f"0x{info.lowest_address:04X}–0x{info.highest_address:04X}"
            if info.lowest_address is not None and info.highest_address is not None
            else "未知"
        )
        self._append_log(f"[{self._timestamp()}] 準備燒錄 {kind}\n")
        self._append_log(f"HEX：{info.data_bytes} bytes，位址 {address}\n")
        self._append_log(f"命令：{shlex.join(command)}\n\n")
        self.progress.set(2)
        self.status_text.set("正在啟動 avrdude…")
        self._running = True
        self.start_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self._worker = threading.Thread(
            target=self._run_avrdude, args=(command,), daemon=True
        )
        self._worker.start()

    def _run_avrdude(self, command: list[str]) -> None:
        parser = AvrdudeProgressParser()
        try:
            creationflags = 0
            popen_kwargs: dict[str, object] = {}
            if os.name == "nt":
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
            else:
                popen_kwargs["start_new_session"] = True
            self._process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                bufsize=0,
                creationflags=creationflags,
                **popen_kwargs,
            )
            assert self._process.stdout is not None
            decoder = codecs.getincrementaldecoder("utf-8")("replace")
            while True:
                chunk = self._process.stdout.read(256)
                if not chunk:
                    break
                text = decoder.decode(chunk)
                if text:
                    self._events.put(("output", text))
                    self._events.put(("progress", parser.feed(text)))
            tail = decoder.decode(b"", final=True)
            if tail:
                self._events.put(("output", tail))
            return_code = self._process.wait()
            parser.finish(return_code == 0)
            self._events.put(("done", return_code))
        except FileNotFoundError:
            self._events.put(
                ("error", "找不到 avrdude。請安裝 avrdude 或使用「瀏覽」指定執行檔。")
            )
        except OSError as exc:
            self._events.put(("error", f"無法啟動 avrdude：{exc}"))
        finally:
            self._process = None

    def _poll_events(self) -> None:
        try:
            while True:
                event, payload = self._events.get_nowait()
                if event == "output":
                    self._append_log(str(payload).replace("\r", "\n"))
                elif event == "progress":
                    value = float(payload)
                    self.progress.set(value)
                    if value >= 68:
                        self.status_text.set("正在驗證…")
                    elif value >= 10:
                        self.status_text.set("正在寫入…")
                    else:
                        self.status_text.set("正在連線…")
                elif event == "done":
                    self._finish(int(payload))
                elif event == "error":
                    self._append_log(f"\n錯誤：{payload}\n")
                    self._set_idle("啟動失敗")
                    messagebox.showerror("無法燒錄", str(payload), parent=self)
        except queue.Empty:
            pass
        self.after(80, self._poll_events)

    def _finish(self, return_code: int) -> None:
        if return_code == 0:
            self.progress.set(100)
            self._append_log(f"\n[{self._timestamp()}] 燒錄與驗證完成。\n")
            self._set_idle("完成")
            messagebox.showinfo("燒錄完成", "韌體已成功燒錄並驗證。", parent=self)
        elif return_code < 0:
            self._append_log(f"\n[{self._timestamp()}] 燒錄已取消。\n")
            self._set_idle("已取消")
        else:
            self._append_log(
                f"\n[{self._timestamp()}] avrdude 失敗（exit code {return_code}）。\n"
            )
            self._set_idle("燒錄失敗")
            messagebox.showerror(
                "燒錄失敗",
                "avrdude 回報錯誤，請查看下方燒錄訊息。",
                parent=self,
            )

    def _cancel_flash(self) -> None:
        process = self._process
        if process is None or process.poll() is not None:
            return
        self.status_text.set("正在取消…")
        self.cancel_button.configure(state="disabled")
        try:
            if os.name == "nt":
                process.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
            else:
                os.killpg(process.pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass

    def _set_idle(self, status: str) -> None:
        self._running = False
        self.status_text.set(status)
        self.start_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")

    def _append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    @staticmethod
    def _timestamp() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _on_close(self) -> None:
        if self._running:
            if not messagebox.askyesno(
                "燒錄進行中", "燒錄尚未結束，確定要取消並離開嗎？", parent=self
            ):
                return
            self._cancel_flash()
        self.destroy()


def main() -> int:
    try:
        app = FirmwareTool()
    except tk.TclError as exc:
        print(f"無法啟動 GUI：{exc}", file=sys.stderr)
        return 1
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
