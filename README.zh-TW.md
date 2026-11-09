<p align="center"><img src="https://raw.githubusercontent.com/overdoingism/SagatVNCViewer/main/docs/logo.png" alt="SagatVNC Viewer logo" width="128"></p>

# SagatVNC Viewer

[English](README.md)

在 Windows 上一直找不到好用的 VNC Viewer，乾脆自己寫一個。

它是把 [TigerVNC](https://github.com/TigerVNC/tigervnc) 的 viewer 移植成 Python 的版本：

- 只用標準函式庫（`socket`、`ssl`、`zlib`、`tkinter`）。
- 解碼迴圈裡最慢的幾段可以選用 Rust 加速，不裝也能跑，只是慢一些。

**平台**：主要在 Windows 上天天使用。Linux 和 macOS 上也能跑，CI 測試會過，但沒有人真的拿來用過。遇到問題歡迎開 issue。

> 與 TigerVNC 專案無關，也未經其認可。

## 下載

到 [Releases](https://github.com/overdoingism/SagatVNCViewer/releases) 下載：

| 檔案 | 用途 |
|------|------|
| `SagatVNCViewer-<版本>-windows-x64.zip` | Windows 10/11。解壓後直接執行 `SagatVNCViewer.exe`，不用安裝 |
| `SagatVNCViewer-<版本>-windows-arm64.zip` | ARM 版 Windows 11 |
| `*-py3-none-<平台>.whl` | 用 `pip install` 安裝，已內含 Rust 加速（Windows/Linux/macOS） |
| `*-py3-none-any.whl` | 其他平台用，純 Python |

### 這些檔案可以信嗎？

所有檔案都由 GitHub Actions 從對應 tag 的原始碼編出來，不是在誰的電腦上編的。建置記錄公開，每個檔案都附有簽章過的建置來源證明（build provenance attestation）。

想確認下載的檔案確實出自這裡，可以用 `gh` 驗證：

```bash
gh attestation verify SagatVNCViewer-0.2.0-windows-x64.zip --repo overdoingism/SagatVNCViewer
```

也可以拿 Release 裡的 `SHA256SUMS` 比對雜湊值。

exe 沒有程式碼簽章，第一次執行時 SmartScreen 可能會跳警告，按「其他資訊」>「仍要執行」即可。

## 使用

- 執行 exe 會先出現連線對話框，輸入 `主機:顯示編號`（例如 `myhost:1` 就是 port 5901）。
- 也可以把 TigerVNC 存的 `.tigervnc` 檔拖到 exe 上。
- 連線後按 **F8**（或點視窗左上角的圖示）開選單。改設定時選單會留著，按 *Close menu*、Esc 或 F8 關閉。裡面有這些功能：
  - 畫質、縮放、全螢幕
  - 鍵盤設定、剪貼簿
  - 儲存設定
  - 問題排解（log）

從原始碼執行：

```bash
python -m sagatvnc myhost:1
python -m sagatvnc -help        # 所有選項
```

選項名稱和 TigerVNC 的 `vncviewer` 相同。

更多說明（英文）：

- [設置指南](setting_guide/)：連線、安全類型、X509 憑證、畫面編碼、特殊按鍵
- [問題排除](troubleshooting/)：高 DPI 縮放、游標偏移、Ctrl+V 只送出 Ctrl、視窗縮放、Windows 登入畫面斷線（改用 UltraVNC）

## 自己編譯

```bash
python packaging/build_accel.py               # Rust 加速（需要 cargo）
python -m unittest discover -s tests -t .     # 測試
python packaging/build_exe.py                 # Windows exe（需要 pyinstaller、pillow）
```

發佈流程見 [RELEASING.md](RELEASING.md)。

## 授權

GPL-3.0-or-later，全文見 [LICENSE](LICENSE)。

- 本程式是 TigerVNC 的衍生作品。TigerVNC 採用 GPL「第 2 版或（由你選擇）任何更新版本」，這裡選擇以第 3 版或更新版本發佈。
- TigerVNC 原本的授權條款放在 [licenses/TigerVNC-LICENCE.TXT](licenses/TigerVNC-LICENCE.TXT)，原始碼檔頭也保留了上游的著作權聲明。
- exe 和 wheel 內含的第三方元件（Python、OpenSSL、Tcl/Tk、Pillow、Rust 標準函式庫等）列在 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
