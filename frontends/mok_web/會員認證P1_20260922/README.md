# 會員認證 P1：Passkey / WebAuthn（2026-09-22，作者：凜）

## 一句話
在 P0（bcrypt + TOTP + TG OTP）之上，加入 **Passkey** 登入：指紋、Face ID、
Windows Hello、實體安全金鑰。生物特徵留在使用者裝置，伺服器只存公開金鑰。

## 生效條件（重要）
WebAuthn **只在 HTTPS 或 localhost 可用**。本站對外為 `https://64071181.xyz`
（Cloudflare tunnel → 127.0.0.1:5000）。若換網域，需同步調整：
- 環境變數 `MOK_WEB_RP_ID`（預設 64071181.xyz）
- 環境變數 `MOK_WEB_ORIGINS`（預設 https://64071181.xyz，逗號分隔多個）

## 新增路由
| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/security/passkey` | Passkey 管理頁（需登入） |
| GET | `/api/auth/webauthn/list` | 列出我的 Passkey |
| POST | `/api/auth/webauthn/register/begin` | 註冊挑戰（需登入） |
| POST | `/api/auth/webauthn/register/complete` | 驗證並存入公開金鑰 |
| POST | `/api/auth/webauthn/login/begin` | 登入挑戰（可帶 username，空＝無帳號登入） |
| POST | `/api/auth/webauthn/login/complete` | 驗證簽章 → 寫入 session |
| POST | `/api/auth/webauthn/rename` | 憑證改名 |
| POST | `/api/auth/webauthn/delete` | 憑證刪除 |

## 資料表（member.db）
- `webauthn_credentials`：公開金鑰、簽章計數、裝置類型、備份狀態
- `webauthn_challenges`：一次性挑戰（TTL 300 秒，用完即刪）

## 安全設計
1. 挑戰為一次性、比對後立即刪除，綁定 rp_id 與 origin。
2. 來源白名單：非允許的 Origin 直接 400。
3. 簽章計數遞增檢查：計數未增加即記 `passkey_clone_suspect` 審計（疑似憑證被複製）。
4. 登入失敗會寫入 `login_fail`，沿用 P0 的 15 分鐘 5 次鎖定。
5. 全程寫入 `auth_audit`（passkey_registered / passkey_login_fail / login_ok）。

## 載入方式
由 `mok_web/保丁.py` 掃描器自動載入（目錄名 `會員認證P1_20260922`）。
`/login` 與 `/security` 的 Passkey 入口採「惰性換裝」，不受補丁載入順序影響；
改名（前綴加 `_`）即可停用本補丁。

## 驗收紀錄（2026-09-22）
以 Playwright + CDP 虛擬驗證器（ctap2/internal，模擬指紋）於獨立測試站實測：
密碼登入 → 註冊 Passkey（成功）→ 清 cookie → **Passkey 登入成功**，
`auth_audit` 出現 `passkey_registered` 與 `login_ok/passkey`。測試資料已清除。
