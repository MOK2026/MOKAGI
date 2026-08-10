

# 安裝 `xvfb`、`x11vnc`、`fluxbox`、`websockify`、`firefox`。

    bash ~/.mok/html/webTools/novncsetup_desktop.sh



### 步驟 2：啟動桌面服務

bash

    bash ~/.mok/html/webTools/novncstart_desktop.sh start


這會依序啟動：
- **Xvfb** (虛擬顯示器 `:1`，1280x800)
- **fluxbox** (輕量視窗管理器)
- **x11vnc** (VNC 伺服器，監聽 `localhost:5900`)
- **websockify** (WebSocket 橋接，`6080` → `5900`)

### 步驟 3：驗證

bash
    bash ~/.mok/html/webTools/novncstart_desktop.sh status

應該四個服務都顯示 `1`（表示正在運行）。