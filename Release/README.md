# Release packages

此目錄保存可直接解壓執行的版本封裝。每個 ZIP 都包含 Python 原始碼、相依套件清單與使用說明。

- `v1.0.0`：Bootloader ISP 與 App Optiboot 序列燒錄
- `v1.1.0`：新增 USBtinyISP／USB ISP App FW 燒錄與選用的 Optiboot 重燒

解壓縮後執行：

```bash
python3 -m pip install -r requirements.txt
python3 app.py
```
