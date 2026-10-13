# ATmega328P Firmware Tool

以 Python/Tkinter 製作的圖形化燒錄工具，可執行：

- 透過 ISP programmer 燒錄 `optiboot_atmega328.hex` Bootloader
- 透過 Optiboot 序列埠燒錄一般 App Firmware
- 透過 USBtinyISP、USBasp 或 Atmel-ICE 直接燒錄 App Firmware
- ISP App 模式可選擇在同一次 erase 後重燒 Optiboot，保留序列上傳能力
- 選擇 HEX、`avrdude` 與選用的 `avrdude.conf` 路徑
- 顯示燒錄進度、完整 avrdude 訊息、成功或錯誤結果
- 開始前驗證 Intel HEX 格式、checksum 與 ATmega328P Flash 位址範圍
- 燒錄中可取消

## 執行環境

- Python 3.10 以上（需含 Tkinter）
- `avrdude`，可使用 PATH 內的版本或在 GUI 中選擇執行檔
- `pyserial` 為選用套件；安裝後可顯示更完整的序列埠名稱

```bash
python3 -m pip install -r requirements.txt
python3 app.py
```

macOS 若已安裝 Arduino IDE/Arduino CLI，程式會嘗試自動尋找 Arduino15 目錄中的 `avrdude`。找不到時可按「瀏覽…」手動選擇。

## 燒錄 Bootloader

1. 使用 ISP 線路連接 programmer 與 ATmega328P（MISO、MOSI、SCK、RESET、VCC、GND）。
2. 選擇「Bootloader」及 `optiboot_atmega328.hex`。
3. 選擇 programmer。USBasp 通常不需指定連接埠；Arduino as ISP 通常選 `arduino` 或 `stk500v1`、對應序列埠與 `19200` baud。
4. Uno 預設值為 Low `0xFF`、High `0xDE`、Extended `0xFD`、Unlock `0x3F`、Lock `0x0F`。
5. 按「開始燒錄」並確認警告。

> Fuse 設定與時脈、板型及 Bootloader 大小有關。預設值只適用於 Arduino Uno / ATmega328P 16 MHz Optiboot；其他硬體請先依設計修改。

## 透過 Optiboot 序列埠燒錄 App FW

1. 選擇「一般 App FW（透過 Optiboot 序列埠）」及 App 的 `.hex`。
2. 選擇開發板序列埠。
3. Uno Optiboot 通常使用 `arduino` protocol / `115200` baud；舊款 Nano Bootloader 常使用 `57600` baud。
4. 按「開始燒錄」。此模式使用 `-D`，不會執行 chip erase，以保留 Bootloader。

## 透過 USBtinyISP／ISP 燒錄 App FW

1. 使用 ISP 線路連接 programmer 與 ATmega328P（MISO、MOSI、SCK、RESET、VCC、GND）。
2. 選擇「一般 App FW（透過 ISP Programmer／USBtinyISP）」及 App 的 `.hex`。
3. 選擇 `usbtiny`、`usbasp` 或 `atmelice_isp`。USB programmer 不使用序列埠或 baud rate。
4. 若燒錄後仍需透過序列埠上傳，勾選「同次重燒 Optiboot」，並確認 Bootloader HEX 路徑。
5. 按「開始燒錄」並確認警告。

ISP 模式會先執行 chip erase，確保 Flash 位元可正確重寫：

- 未勾選重燒 Optiboot：只寫入 App，原 Bootloader 會被清除。
- 勾選重燒 Optiboot：依序寫入 App 與 Bootloader，再套用 Arduino Uno 16 MHz 的 fuse/lock 設定。

> App HEX 不可與 Bootloader HEX 的 Flash 位址重疊；工具會在開始前檢查並阻止重疊的映像檔。

## 測試

```bash
python3 -m unittest discover -s tests -v
```
