#!/usr/bin/env bash
# deploy/setup.sh — 一键部署脚本（在 ECS 上以 root 运行）
# 用法：sudo bash deploy/setup.sh
set -euo pipefail

# 仓库根目录：脚本在 <repo>/deploy/setup.sh，向上一级即为根
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE_DIR="/etc/systemd/system"
ENV_FILE="/etc/oj.env"
OJ_USER="oj"

echo "=== OJ 部署脚本 ==="

# ── 1. 确认以 root 运行 ────────────────────────────────────
if [ "$(id -u)" -ne 0 ]; then
    echo "错误：请以 root 或 sudo 运行此脚本" >&2
    exit 1
fi

# ── 2. 安装系统依赖 ───────────────────────────────────────
echo "[1/6] 安装依赖..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip git
pip3 install -q flask

# ── 3. 创建低权限用户 ─────────────────────────────────────
echo "[2/6] 创建低权限用户 ${OJ_USER}..."
if id "${OJ_USER}" &>/dev/null; then
    echo "  用户 ${OJ_USER} 已存在，跳过"
else
    useradd -r -s /sbin/nologin -d /nonexistent -M "${OJ_USER}"
    echo "  已创建系统用户 ${OJ_USER}"
fi

# ── 4. 设置目录权限 ───────────────────────────────────────
echo "[3/6] 设置目录权限..."
# 确保 oj 目录存在
mkdir -p "${REPO_DIR}/oj/uploads" "${REPO_DIR}/oj/viz"
# 数据目录归 oj 用户，代码目录只读
chown -R root:root "${REPO_DIR}"
chown -R "${OJ_USER}:${OJ_USER}" "${REPO_DIR}/oj/uploads" \
                                   "${REPO_DIR}/oj/viz"
# DB 文件若已存在则转交；否则创建空文件
touch "${REPO_DIR}/oj/oj.db"
chown "${OJ_USER}:${OJ_USER}" "${REPO_DIR}/oj/oj.db"
chmod 660 "${REPO_DIR}/oj/oj.db"

# ── 5. 安装环境变量文件 ───────────────────────────────────
echo "[4/6] 安装环境变量文件..."
if [ -f "${ENV_FILE}" ]; then
    echo "  ${ENV_FILE} 已存在，跳过（如需重置请手动删除）"
else
    cp "$(dirname "$0")/oj.env.example" "${ENV_FILE}"
    chown root:"${OJ_USER}" "${ENV_FILE}"
    chmod 640 "${ENV_FILE}"
    echo "  已创建 ${ENV_FILE}"
    echo ""
    echo "  !! 请立即编辑 ${ENV_FILE} 修改 OJ_ADMIN 为随机字符串 !!"
    echo "  示例：sudo nano ${ENV_FILE}"
    echo ""
fi

# ── 6. 安装 systemd 服务（替换路径占位符）────────────────
echo "[5/6] 安装 systemd 服务..."
DEPLOY_DIR="$(dirname "$0")"
sed "s|/home/hackerson|${REPO_DIR}|g" "${DEPLOY_DIR}/oj-server.service" > "${SERVICE_DIR}/oj-server.service"
sed "s|/home/hackerson|${REPO_DIR}|g" "${DEPLOY_DIR}/oj-worker.service" > "${SERVICE_DIR}/oj-worker.service"
systemctl daemon-reload
systemctl enable oj-server oj-worker
echo "  服务已注册：oj-server, oj-worker（路径: ${REPO_DIR}）"

# ── 7. 初始化数据库 ───────────────────────────────────────
echo "[6/6] 初始化数据库..."
cd "${REPO_DIR}"
sudo -u "${OJ_USER}" python3 oj/db.py init
echo "  数据库初始化完成"

# ── 完成 ──────────────────────────────────────────────────
echo ""
echo "=== 部署完成 ==="
echo "  仓库路径: ${REPO_DIR}"
echo ""
echo "下一步："
echo "  1. 编辑管理员密钥：sudo nano ${ENV_FILE}"
echo "  2. 启动服务：sudo systemctl start oj-server oj-worker"
echo "  3. 查看状态：sudo systemctl status oj-server oj-worker"
echo "  4. 查看日志：sudo journalctl -u oj-server -f"
echo "              sudo journalctl -u oj-worker -f"
echo "  5. 生成演示榜单数据：sudo -u ${OJ_USER} python3 ${REPO_DIR}/tools/seed_demo.py"
echo ""
echo "批量添加队伍（可选）："
echo "  sudo -u ${OJ_USER} python3 ${REPO_DIR}/oj/db.py addteam \"队伍名称\""
