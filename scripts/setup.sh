#!/bin/bash
#
# 电商自动化 Agent 一键安装脚本
#
# 【功能】
# 1. 检查系统依赖
# 2. 安装 Python 依赖
# 3. 安装 Playwright 浏览器
# 4. 创建必要的目录结构
# 5. 配置环境变量
#

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 项目根目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "========================================"
echo "  电商自动化 Agent 安装脚本"
echo "========================================"
echo ""

# 进入项目目录
cd "$PROJECT_ROOT"

# 1. 检查 Python 版本
echo -e "${YELLOW}[1/6]${NC} 检查 Python 环境..."
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}错误: 未找到 Python3，请先安装 Python 3.9+${NC}"
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
REQUIRED_VERSION="3.9"

if [ "$(printf '%s\n' "$REQUIRED_VERSION" "$PYTHON_VERSION" | sort -V | head -n1)" != "$REQUIRED_VERSION" ]; then
    echo -e "${RED}错误: Python 版本过低，需要 3.9+，当前版本: $PYTHON_VERSION${NC}"
    exit 1
fi

echo -e "${GREEN}✓${NC} Python 版本: $PYTHON_VERSION"

# 2. 检查系统依赖
echo -e "${YELLOW}[2/6]${NC} 检查系统依赖..."

# 检查 apt-get（Debian/Ubuntu）
if command -v apt-get &> /dev/null; then
    echo "安装系统依赖..."
    sudo apt-get update -qq
    
    # Playwright 依赖
    sudo apt-get install -y \
        wget \
        gnupg \
        ca-certificates \
        fonts-liberation \
        libasound2 \
        libatk-bridge2.0-0 \
        libatk1.0-0 \
        libcups2 \
        libdbus-1-3 \
        libdrm2 \
        libgbm1 \
        libgtk-3-0 \
        libnspr4 \
        libnss3 \
        libx11-xcb1 \
        libxcomposite1 \
        libxdamage1 \
        libxfixes3 \
        libxkbcommon0 \
        libxrandr2 \
        xdg-utils \
        libxext6 \
        libxrender1 \
        > /dev/null 2>&1
    
    echo -e "${GREEN}✓${NC} 系统依赖安装完成"
    
# 检查 yum（CentOS/RHEL）
elif command -v yum &> /dev/null; then
    echo "安装系统依赖..."
    sudo yum install -y \
        wget \
        which \
        xorg-x11-fonts* \
        at-spi2-atk \
        cups-libs \
        libdrm \
        libgbm \
        libgtk-3 \
        libnspr \
        libnss3 \
        libxcb \
        libxcomposite \
        libxdamage \
        libxfixes \
        libxkbcommon \
        libxrandr \
        xdg-utils \
        > /dev/null 2>&1
    
    echo -e "${GREEN}✓${NC} 系统依赖安装完成"
    
else
    echo -e "${YELLOW}警告: 未找到包管理器，跳过系统依赖安装${NC}"
fi

# 3. 创建虚拟环境（可选）
echo -e "${YELLOW}[3/6]${NC} 配置 Python 环境..."

if [ -z "$VIRTUAL_ENV" ]; then
    echo -e "${YELLOW}提示: 建议使用虚拟环境运行本项目${NC}"
    read -p "是否创建虚拟环境? (y/n, 默认: n): " -n 1 -r
    echo ""
    
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        python3 -m venv venv
        source venv/bin/activate
        echo -e "${GREEN}✓${NC} 虚拟环境已创建并激活"
    fi
fi

# 4. 安装 Python 依赖
echo -e "${YELLOW}[4/6]${NC} 安装 Python 依赖..."

pip install --upgrade pip -q
pip install -r requirements.txt -q

# 安装 playwright-stealth（反检测）
pip install playwright-stealth -q

echo -e "${GREEN}✓${NC} Python 依赖安装完成"

# 5. 安装 Playwright 浏览器
echo -e "${YELLOW}[5/6]${NC} 安装 Playwright 浏览器..."

# 下载浏览器（使用腾讯镜像加速）
export PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright
playwright install chromium
playwright install-deps chromium

echo -e "${GREEN}✓${NC} Playwright 浏览器安装完成"

# 6. 创建目录结构
echo -e "${YELLOW}[6/6]${NC} 创建目录结构..."

mkdir -p logs
mkdir -p logs/screenshots
mkdir -p data
mkdir -p data/orders
mkdir -p data/refunds
mkdir -p data/print_tasks
mkdir -p data/sessions
mkdir -p downloads

echo -e "${GREEN}✓${NC} 目录结构创建完成"

# 7. 配置示例（如果不存在）
if [ ! -f "config.yaml" ]; then
    echo -e "${YELLOW}提示: config.yaml 不存在，创建示例配置...${NC}"
    # config.yaml 应该由用户提供，这里不创建
fi

# 8. 检查安装结果
echo ""
echo "========================================"
echo -e "${GREEN}安装完成！${NC}"
echo "========================================"
echo ""
echo "下一步操作:"
echo "1. 配置 config.yaml 文件"
echo "2. 设置环境变量（如果需要）:"
echo "   export SHOP_USERNAME=your_username"
echo "   export SHOP_PASSWORD=your_password"
echo ""
echo "启动命令:"
echo "   python main.py"
echo ""
echo "Docker 部署:"
echo "   docker-compose up -d"
echo ""

# 运行健康检查（如果可能）
if [ -f "scripts/healthcheck.py" ]; then
    echo "运行快速检查..."
    python3 scripts/healthcheck.py || true
fi
