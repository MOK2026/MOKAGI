#!/usr/bin/env bash
# ==============================================
# ================== 基礎設定 ===================
# ==============================================
# 先刪除舊的資料夾(如果有的話）
# rm -rf ~/.mok
# sudo rm -rf ~/.mok



set -o pipefail
update_date="202607271012_暫時可用版"
MOKAGIName="mok"
PROJECT_DIR="${HOME}/.${MOKAGIName}"
AGENT_ROOT="${PROJECT_DIR}/agent"
GITHUB_REPO="https://github.com/MOK2026/MOKAGI"
GITHUB_REPO_RAW="https://raw.githubusercontent.com/MOK2026/MOKAGI/refs/heads/main"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}=========================================="
echo -e " 👼 [0/11] 開始安裝 mok_agi_${update_date}  👼"
echo -e "==========================================${NC}"

# -----------------------------------------------
# [1/11] 建立目錄結構
# -----------------------------------------------
echo -e "${GREEN}=========================================="
echo -e " [1/11] 建立目錄結構"
echo -e "==========================================${NC}"

mkdir -p "${PROJECT_DIR}"
mkdir -p "${PROJECT_DIR}/core"
mkdir -p "${PROJECT_DIR}/tools"
mkdir -p "${PROJECT_DIR}/skill"
mkdir -p "${PROJECT_DIR}/frontends"
mkdir -p "${PROJECT_DIR}/html"
mkdir -p "${PROJECT_DIR}/.memory"
mkdir -p "${PROJECT_DIR}/.chroma_data"
mkdir -p "${AGENT_ROOT}"

cd "${PROJECT_DIR}"
export MOKAGI_HOME="${MOKAGIName}"
export PYTHONPATH="${PROJECT_DIR}:${PYTHONPATH}"

# -----------------------------------------------
# [2/11] Agent 配置偵測與建立
# -----------------------------------------------
echo -e "${GREEN}=========================================="
echo -e " [2/11] Agent 配置偵測與建立"
echo -e "==========================================${NC}"

get_agent_name() {
    local cfg="$1"
    grep -E '^MOK_AGENT_NAME=' "$cfg" 2>/dev/null | cut -d'=' -f2- | tr -d '"' | head -1
}

existing_agents=()
if [ -d "$AGENT_ROOT" ]; then
    for dir in "$AGENT_ROOT"/*/; do
        [ -d "$dir" ] || continue
        agent_name=$(basename "$dir")
        cfg_file="${dir}.${agent_name}"
        if [ -f "$cfg_file" ]; then
            existing_agents+=("$agent_name")
        fi
    done
fi

if [ ${#existing_agents[@]} -eq 0 ]; then
    echo -e "${YELLOW}尚未發現任何 Agent，請為此 Agent 命名（例如：joe、yun、sam）：${NC}"
    read -p "Agent 名稱: " MOK_AGENT_NAME_INPUT
    MOK_AGENT_NAME_INPUT=$(echo "$MOK_AGENT_NAME_INPUT" | xargs | cut -c1-32)
    if [ -z "$MOK_AGENT_NAME_INPUT" ]; then
        MOK_AGENT_NAME_INPUT="default"
    fi
    echo -e "${GREEN}建立 Agent: ${MOK_AGENT_NAME_INPUT}${NC}"
    agent_dir="${AGENT_ROOT}/${MOK_AGENT_NAME_INPUT}"
    mkdir -p "$agent_dir"
    cfg_file="${agent_dir}/.${MOK_AGENT_NAME_INPUT}"
    curl -sL "${GITHUB_REPO_RAW}/env.env" -o "$cfg_file"
    sed -i "s/__MOK_AGENT_NAME_PLACEHOLDER__/${MOK_AGENT_NAME_INPUT}/g" "$cfg_file"
    mkdir -p "${agent_dir}/soul"
    mkdir -p "${agent_dir}/logs"
else
    echo -e "${GREEN}發現 ${#existing_agents[@]} 個現有 Agent：${NC}"
    for agent in "${existing_agents[@]}"; do
        echo -e "  - $agent"
        agent_dir="${AGENT_ROOT}/${agent}"
        mkdir -p "${agent_dir}/soul"
        mkdir -p "${agent_dir}/logs"
    done
fi

# -----------------------------------------------
# [3/11] 安裝 Ollama
# -----------------------------------------------
echo -e "${GREEN}=========================================="
echo -e " 🦙 [3/11] 安裝 Ollama"
echo -e "==========================================${NC}"

if [ -z "${MOK_MODEL_NAME}" ]; then
    MOK_MODEL_NAME="minimax-m3:cloud"
fi

sudo rm -f /etc/systemd/system/ollama.service.d/override.conf
if ! command -v ollama &> /dev/null; then
    curl -fsSL https://ollama.com/install.sh | sh
fi
sudo mkdir -p /etc/systemd/system/ollama.service.d
cat <<EOF | sudo tee /etc/systemd/system/ollama.service.d/override.conf
[Service]
Environment="OLLAMA_HOST=[::]:11434"
Environment="OLLAMA_NUM_THREADS=${MOK_NUM_THREADS:-2}"
EOF
sudo systemctl daemon-reload
sudo systemctl restart ollama
sleep 5

# -----------------------------------------------
# [4/11] 下載模型
# -----------------------------------------------
echo -e "${GREEN}=========================================="
echo -e " [4/11] 下載模型 ${MOK_MODEL_NAME}"
echo -e "==========================================${NC}"
export OLLAMA_HOST="[::]:11434"
if ollama list | grep -q "^${MOK_MODEL_NAME} "; then
    echo -e "${YELLOW}模型 ${MOK_MODEL_NAME} 已存在，跳過下載。${NC}"
else
    echo "正在從 Ollama 庫拉取模型..."
    if ! ollama pull ${MOK_MODEL_NAME}; then
        echo -e "${RED}模型下載失敗，請檢查名稱或網絡。${NC}"
        exit 1
    fi
fi

cat > Modelfile <<EOF
FROM ${MOK_MODEL_NAME}
PARAMETER num_ctx ${MOK_num_ctx:-16384}
PARAMETER num_predict ${MOK_num_predict:-8192}
PARAMETER temperature ${MOK_temperature:-0.8}
PARAMETER repeat_penalty ${MOK_repeat_penalty:-1.5}
PARAMETER presence_penalty ${MOK_presence_penalty:-0.6}
PARAMETER frequency_penalty ${MOK_frequency_penalty:-0.5}
PARAMETER top_p ${MOK_top_p:-0.9}
PARAMETER top_k ${MOK_top_k:-50}
EOF
ollama create ${MOK_MODEL_NAME} -f Modelfile
rm Modelfile

# -----------------------------------------------
# [5/11] 安裝 Python 依賴
# -----------------------------------------------
echo -e "${GREEN}=========================================="
echo -e " [5/11] 安裝 Python 依賴"
echo -e "==========================================${NC}"
sudo apt-get update -qq 2>/dev/null
sudo apt-get install -y -qq python3 python3-pip 2>/dev/null || true
pip install python-telegram-bot httpx flask flask-socketio watchdog openai --quiet

# ==============================================
# ============ 全目錄下載函數 ==================
# ==============================================

# 下載 GitHub 目錄下所有檔案（自動爬取，不限檔案數量）
download_github_dir() {
    local remote_dir="$1"      # 遠端目錄名稱（如 core、tools、frontends、html）
    local local_dir="$2"       # 本地目錄名稱（通常與 remote_dir 相同）
    local target_path="${PROJECT_DIR}/${local_dir}"

    echo -e "${YELLOW}正在下載 ${remote_dir}/ 所有檔案...${NC}"
    mkdir -p "${target_path}"

    # 用 GitHub API 取得目錄檔案清單（遞迴下載子目錄）
    local api_url="https://api.github.com/repos/MOK2026/MOKAGI/contents/${remote_dir}"
    local temp_file=$(mktemp)

    curl -sL "$api_url" -o "$temp_file"

    # 檢查是否成功取得資料
    if ! grep -q '"name"' "$temp_file" 2>/dev/null; then
        echo -e "${RED}❌ 無法取得 ${remote_dir}/ 檔案清單，請檢查網路或 GitHub API。${NC}"
        rm -f "$temp_file"
        return 1
    fi

    # 解析 JSON，取出所有 type 為 file 的 name
    local file_names=$(grep -E '"name"|"type"' "$temp_file" | paste - - | grep -E '"type":\s*"file"' | sed -E 's/.*"name":\s*"([^"]+)".*/\1/')

    # 解析 JSON，取出所有 type 為 dir 的 name（遞迴下載用）
    local dir_names=$(grep -E '"name"|"type"' "$temp_file" | paste - - | grep -E '"type":\s*"dir"' | sed -E 's/.*"name":\s*"([^"]+)".*/\1/')

    if [ -z "$file_names" ] && [ -z "$dir_names" ]; then
        echo -e "${YELLOW}⚠️ ${remote_dir}/ 目錄中沒有找到任何檔案或子目錄。${NC}"
        rm -f "$temp_file"
        return 0
    fi

    local count=0
    for fname in $file_names; do
        # 跳過 __pycache__、.gitkeep 等
        if [[ "$fname" == __pycache__* ]] || [[ "$fname" == .* ]] && [ "$fname" != ".env" ]; then
            continue
        fi
        local raw_url="${GITHUB_REPO_RAW}/${remote_dir}/${fname}"
        echo -e "  ↓ ${fname}"
        curl -sL "$raw_url" -o "${target_path}/${fname}"
        ((count++))
    done

    rm -f "$temp_file"

    # 遞迴下載子目錄
    if [ -n "$dir_names" ]; then
        for dname in $dir_names; do
            download_github_dir "${remote_dir}/${dname}" "${local_dir}/${dname}"
        done
    fi

    echo -e "${GREEN}✅ ${remote_dir}/ 下載完成，共 ${count} 個檔案。${NC}"
}

# -----------------------------------------------
# [6/11] 安裝核心 AI 引擎（全目錄下載）
# -----------------------------------------------
echo -e "${GREEN}=========================================="
echo -e " [6/11] 安裝核心 AI 引擎（全目錄下載）"
echo -e "==========================================${NC}"
download_github_dir "core" "core"

# -----------------------------------------------
# [7/11] 安裝基本工具（全目錄下載）
# -----------------------------------------------
echo -e "${GREEN}=========================================="
echo -e " [7/11] 安裝基本工具（全目錄下載）"
echo -e "==========================================${NC}"
download_github_dir "tools" "tools"

# -----------------------------------------------
# [8/11] 安裝前端介面（全目錄下載）
# -----------------------------------------------
echo -e "${GREEN}=========================================="
echo -e " [8/11] 安裝前端介面（全目錄下載）"
echo -e "==========================================${NC}"
download_github_dir "frontends" "frontends"
download_github_dir "html" "html"

# -----------------------------------------------
# [9/11] 反向隧道密鑰（選擇性）
# -----------------------------------------------
echo -e "${GREEN}=========================================="
echo -e " [9/11] 私人GPU模型(如有)反向隧道專用密鑰（GPU → CPU）"
echo -e "==========================================${NC}"
REVERSE_KEY_DIR="/home/ubuntu/.ssh"
REVERSE_PRIVATE_KEY="$REVERSE_KEY_DIR/id_rsa_reverse"
REVERSE_PUBLIC_KEY="$REVERSE_PRIVATE_KEY.pub"

if [ ! -f "$REVERSE_PRIVATE_KEY" ]; then
    sudo -u ubuntu ssh-keygen -t rsa -b 4096 -N "" -f "$REVERSE_PRIVATE_KEY"
    echo "💖 反向隧道密鑰已生成：$REVERSE_PRIVATE_KEY"
else
    echo "💖 反向隧道密鑰已存在"
fi
if ! grep -q "$(cat "$REVERSE_PUBLIC_KEY" 2>/dev/null)" /home/ubuntu/.ssh/authorized_keys 2>/dev/null; then
    echo "📌 將公鑰添加到 authorized_keys..."
    cat "$REVERSE_PUBLIC_KEY" >> /home/ubuntu/.ssh/authorized_keys
    chmod 600 /home/ubuntu/.ssh/authorized_keys
    echo "💖 公鑰已添加"
else
    echo "💖 公鑰已存在於 authorized_keys"
fi



# 在 MOKAGI.sh 結尾（[11/11] 之前）加入：
echo "修正檔案權限..."
sudo chown -R $(whoami):$(whoami) "${PROJECT_DIR}"


# -----------------------------------------------
# [10/11] PM2 啟動
# -----------------------------------------------
echo -e "${GREEN}=========================================="
echo -e " [10/11] PM2 啟動 (統一進程 mok_agi)"
echo -e "==========================================${NC}"
if ! command -v pm2 &> /dev/null; then
    curl -fsSL https://deb.nodesource.com/setup_18.x | sudo bash -
    sudo apt-get install -y nodejs
    sudo npm install -g pm2
fi

pm2 delete mok_agi 2>/dev/null || true
pm2 start "${PROJECT_DIR}/core/launcher.py" \
    --name "mok_agi" \
    --interpreter python3 \
    --cwd "${PROJECT_DIR}" \
    --log-date-format "YYYY-MM-DD HH:MM:SS"
pm2 save

# -----------------------------------------------
# [11/11] 完成
# -----------------------------------------------
echo -e "${GREEN}=========================================="
echo -e " 🎉 [11/11] mok_agi_${update_date} 部署完成！ 🎉"
echo -e "=========================================="
echo ""
echo -e " GITHUB: ${GITHUB_REPO}"
echo ""
echo -e " 查看全部日誌: pm2 logs mok_agi"
echo -e " 重啟所有服務: pm2 restart mok_agi"
echo -e " 停止所有服務: pm2 stop mok_agi"
echo ""
echo -e " 模型列表:     ollama list"
echo -e " 強制清理:     sudo pkill -f 'ollama runner'"
echo ""
echo -e " 🌐 網頁界面訪問方式："
echo -e "    本機訪問： http://127.0.0.1:5000"
echo -e "    遠端訪問： http://<您的伺服器IP>:5000"
echo -e "    （請確保防火牆已開放 5000 埠，或使用 SSH 隧道）"
echo -e "    首次使用請先選擇左側 Agent，即可開始對話。"
echo ""
echo -e " 檢查模型隧道： ss -tlnp | grep -E '11434|11435'"
echo -e "==========================================${NC}"