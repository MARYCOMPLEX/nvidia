#!/usr/bin/env bash

set -e
export UUID=${UUID:-$(uuidgen -r)}
export DEBIAN_FRONTEND=noninteractive

APP_DIR="/opt/myapp"
STATE_FILE="${APP_DIR}/.project_type"
APP_NAME=$(tr -dc a-z </dev/urandom | head -c 6)
SUBPATH=${UUID}

REPO="https://raw.githubusercontent.com/MARYCOMPLEX/nvidia/main"

[[ $EUID -ne 0 ]] && echo -e "\033[1;91m请root用户下运行脚本，输入：sudo -i 切换到root用户后再次运行！\033[0m" && exit 1

if [[ "$1" == "-u" || "$1" == "u" || "$1" == "uninstall" ]]; then
    echo "执行卸载操作..."
    if [[ -f "${STATE_FILE}" ]]; then
        INSTALLED_TYPE=$(cat "${STATE_FILE}")
        echo "检测到已安装项目: ${INSTALLED_TYPE}"
    else
        echo "未检测到项目状态文件，将执行清理..."
        INSTALLED_TYPE="unknown"
    fi
    pm2 delete all 2>/dev/null || true
    pm2 save >/dev/null 2>&1 || true
    echo "删除 PM2 开机自启"
    pm2 unstartup systemd -u root --hp /root >/dev/null 2>&1 || true
    echo "删除项目目录"
    rm -rf "${APP_DIR}"
    echo ""
    echo -e "\e[1;32m卸载完成\033[0m"
    exit 0
fi

PROJECT_TYPE="python"
echo -e "\e[1;33m部署 NovaAI Inference API 服务\033[0m"

echo "安装依赖中，请稍等..."
apt-get update -qq
apt-get install -y -qq curl wget git ca-certificates gnupg python3 python3-venv python3-pip >/dev/null 2>&1

mkdir -p "${APP_DIR}"
echo "${PROJECT_TYPE}" > "${STATE_FILE}"
cd "${APP_DIR}"

echo "正在安装 PM2 ..."
if ! command -v pm2 &> /dev/null; then
    if ! command -v node &> /dev/null; then
        curl -fsSL https://deb.nodesource.com/setup_current.x | bash - >/dev/null 2>&1
        apt-get install -y -qq nodejs >/dev/null 2>&1
    fi
    npm install -g pm2 >/dev/null 2>&1
fi

echo "下载核心文件..."
wget -q -O app.py "${REPO}/app.py"
wget -q -O index.html "${REPO}/index.html"
wget -q -O requirements.txt "${REPO}/requirements.txt"

echo "创建 Python 虚拟环境..."
python3 -m venv venv
source venv/bin/activate

echo "安装 Python 依赖..."
pip install --upgrade pip >/dev/null 2>&1
pip install -r requirements.txt >/dev/null 2>&1

echo "配置 UUID 和路径..."
sed -i "s/UUID = os.environ.get('UUID', '[^']*')/UUID = os.environ.get('UUID', '${UUID}')/" app.py
sed -i "s/SUB_PATH = os.environ.get('SUB_PATH', '[^']*')/SUB_PATH = os.environ.get('SUB_PATH', 'api\/v1\/license')/" app.py
sed -i "s/WSPATH = os.environ.get('WSPATH', '[^']*')/WSPATH = os.environ.get('WSPATH', 'ws\/v1\/completions')/" app.py

echo "启动项目..."
pm2 start app.py \
    --name "${APP_NAME}" \
    --interpreter "${APP_DIR}/venv/bin/python3" \
    >/dev/null 2>&1

pm2 startup systemd -u root --hp /root >/dev/null 2>&1
pm2 save >/dev/null 2>&1

IP=$(curl -sm 5 https://api-ipv4.ip.sb/ip)

echo ""
echo -e "\e[1;32m安装完成\033[0m"
echo ""
echo "项目类型: NovaAI Inference API"
echo "APP_NAME: ${APP_NAME}"
echo "UUID: ${UUID}"
echo ""
echo -e "\e[1;32m=== 访问地址 ===\033[0m"
echo -e "  首页:      http://${IP}:3000/"
echo -e "  模型列表:  http://${IP}:3000/api/v1/models"
echo -e "  License:   http://${IP}:3000/api/v1/license"
echo -e "  健康检查:  http://${IP}:3000/api/v1/health"
echo ""
echo -e "\e[1;33m客户端配置路径: /ws/v1/completions\033[0m"
echo -e "\033[1;91m请将上面的 3000 端口映射后再访问\033[0m\n"
