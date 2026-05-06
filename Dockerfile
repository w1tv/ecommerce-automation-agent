# =============================================
# 电商自动化 Agent Dockerfile
# 【修复内容】
# 1. 修复假健康检查
# 2. 增加 playwright-stealth 安装
# 3. 优化构建流程
# =============================================
# 基于 Python 3.11 slim 镜像构建

FROM python:3.11-slim

# =============================================
# 环境变量设置
# =============================================
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright

# =============================================
# 设置工作目录
# =============================================
WORKDIR /app

# =============================================
# 安装系统依赖
# =============================================
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Playwright 依赖
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
    # 其他工具
    curl \
    vim \
    # 清理缓存
    && rm -rf /var/lib/apt/lists/*

# =============================================
# 复制依赖文件
# =============================================
COPY requirements.txt .

# =============================================
# 安装 Python 依赖
# =============================================
RUN pip install --no-cache-dir -r requirements.txt

# =============================================
# 安装 Playwright 浏览器
# =============================================
RUN playwright install chromium && \
    playwright install-deps chromium

# =============================================
# 复制项目文件
# =============================================
COPY . .

# =============================================
# 创建必要的目录
# =============================================
RUN mkdir -p /app/logs \
             /app/logs/screenshots \
             /app/data/orders \
             /app/data/refunds \
             /app/data/print_tasks \
             /app/data/sessions \
             /app/downloads \
             /app/scripts

# 设置目录权限
RUN chmod +x /app/scripts/*.sh 2>/dev/null || true

# =============================================
# 设置环境变量
# =============================================
ENV CONFIG_PATH=/app/config.yaml

# =============================================
# 【修复】真正的健康检查
# 检查进程、浏览器、日志更新
# =============================================
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python /app/scripts/healthcheck.py || exit 1

# =============================================
# 启动命令
# =============================================
CMD ["python", "main.py"]
