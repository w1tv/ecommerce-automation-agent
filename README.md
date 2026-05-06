# 🤖 电商自动化 Agent

[![Python 3.9+](https://img.shields.io/badge/Python-3.9+-blue.svg)](https://www.python.org/)
[![Playwright](https://img.shields.io/badge/Playwright-1.x-green.svg)](https://playwright.dev/)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED.svg)](https://www.docker.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

基于 **Python + Playwright** 的电商后台自动化运营系统，支持自动处理退货退款、订单抓取、快递单打印等功能。适用于淘宝/天猫、京东、拼多多等主流电商平台的店铺日常运营自动化。

---

## ✨ 功能特性

| 功能 | 说明 |
|------|------|
| 🔄 **退货退款自动处理** | 根据配置规则自动审批/拒绝退款申请，支持金额阈值、关键词匹配、时间窗口 |
| 📦 **订单自动抓取** | 定时抓取新订单数据，保存为结构化 JSON，支持多状态筛选 |
| 🖨️ **快递单自动打印** | 支持本地打印服务、快递公司 API、HTML 文件生成三种方案 |
| 🌐 **多店铺管理** | 同时管理多个电商平台店铺，独立配置、独立运行 |
| 🛡️ **验证码检测** | 自动检测验证码并暂停处理，等待人工介入 |
| 🍪 **Cookie 持久化** | 登录状态自动保存，重启后无需重新登录 |
| 💓 **心跳检测** | 进程健康监控，异常自动重连 |
| 🐳 **Docker 部署** | 一键容器化部署，支持 Docker Compose |
| 📢 **多渠道告警** | 支持钉钉、企业微信、飞书 Webhook 告警通知 |

---

## 🏗️ 架构设计

```
┌─────────────────────────────────────────────────┐
│                 EcommerceAgent                   │
│  ┌───────────┐  ┌──────────┐  ┌──────────────┐ │
│  │ Scheduler │  │ Heartbeat│  │ Alert Manager│ │
│  │ (APSched) │  │  Monitor │  │ (Ding/WeChat)│ │
│  └─────┬─────┘  └────┬─────┘  └──────┬───────┘ │
│        │              │               │          │
│  ┌─────▼──────────────▼───────────────▼───────┐ │
│  │           Shop Handler Pool                │ │
│  │  ┌─────────┐ ┌─────────┐ ┌─────────┐     │ │
│  │  │ Shop A  │ │ Shop B  │ │ Shop C  │ ... │ │
│  │  │(Tmall)  │ │  (JD)   │ │ (PDD)   │     │ │
│  │  └────┬────┘ └────┬────┘ └────┬────┘     │ │
│  └───────┼───────────┼───────────┼───────────┘ │
│          │           │           │              │
│  ┌───────▼───────────▼───────────▼───────────┐ │
│  │          Browser Manager (Playwright)      │ │
│  │  ┌──────────┐ ┌──────────┐ ┌───────────┐ │ │
│  │  │ Chromium │ │ Firefox  │ │  WebKit   │ │ │
│  │  └──────────┘ └──────────┘ └───────────┘ │ │
│  └───────────────────────────────────────────┘ │
└─────────────────────────────────────────────────┘
```

---

## 🚀 快速开始

### 环境要求

- Python 3.9+
- Linux / macOS / Windows
- Docker（可选）

### 1. 安装依赖

```bash
# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Linux/macOS

# 安装 Python 依赖
pip install -r requirements.txt

# 安装 Playwright 浏览器
playwright install chromium
```

### 2. 配置店铺

编辑 `config.yaml` 文件，配置你的店铺信息：

```yaml
shops:
  - name: "我的天猫店"
    platform: tmall
    login_url: "https://seller.tmall.com/login.htm"
    credentials:
      username: "your_username"
      password: "your_password"
```

### 3. 启动运行

```bash
# 基本运行
python main.py

# 启用调试模式
python main.py --debug

# 单次执行（用于测试）
python main.py --once

# 指定配置文件
python main.py -c my_config.yaml
```

### 4. Docker 部署

```bash
# 构建镜像
docker build -t ecommerce-agent .

# 使用 Docker Compose 启动
docker-compose up -d

# 查看日志
docker-compose logs -f
```

---

## 📁 目录结构

```
ecommerce-automation-agent/
├── main.py                    # 主入口，任务调度与守护
├── config.yaml                # 配置文件模板
├── requirements.txt           # Python 依赖
├── Dockerfile                 # Docker 构建文件
├── docker-compose.yaml        # Docker Compose 配置
│
├── core/                      # 核心业务模块
│   ├── browser_manager.py     # 浏览器生命周期管理
│   ├── refund_handler.py      # 退货退款自动处理
│   ├── order_fetcher.py       # 订单抓取
│   ├── label_printer.py       # 快递单打印
│   ├── exception_handler.py   # 异常处理与自动重连
│   ├── alerter.py             # 多渠道告警通知
│   └── logger.py              # 日志模块
│
├── utils/                     # 工具函数
│   ├── config_loader.py       # 配置文件加载器
│   └── helpers.py             # 辅助函数
│
└── scripts/                   # 运维脚本
    ├── setup.sh               # 环境初始化脚本
    └── healthcheck.py         # 健康检查脚本
```

---

## ⚙️ 配置说明

### 退款规则配置

```yaml
refund_rules:
  enabled: true

  # 自动同意规则
  auto_approve:
    enabled: true
    max_amount: 50                    # 50元以下自动通过
    refund_only: true                 # 仅退款自动通过
    auto_approve_reasons:             # 特定原因自动通过
      - "缺货"
      - "不想要了"
      - "拍多了"

  # 自动拒绝规则
  auto_reject:
    enabled: false
    reasons: []

  # 需要人工审核
  require_manual:
    enabled: true
    amount_threshold: 200            # 200元以上必须人工
    keywords:
      - "质量问题"
      - "假货"

  # 处理时间窗口
  time_window:
    enabled: true
    start_hour: 9
    end_hour: 22
```

### 定时任务调度

```yaml
schedule:
  enabled: true
  order_fetch_interval: 5       # 订单抓取间隔（分钟）
  refund_process_interval: 10   # 退款处理间隔（分钟）
  heartbeat_interval: 60        # 心跳检测间隔（秒）
  run_on_startup: true          # 启动时立即执行
```

---

## 🐳 Docker 部署

### 构建镜像

```bash
docker build -t ecommerce-agent .
```

### 使用 Docker Compose 启动

```bash
# 启动服务
docker-compose up -d

# 查看日志
docker-compose logs -f

# 停止服务
docker-compose down
```

### 单独使用 Docker

```bash
# 运行容器
docker run -d \
  --name ecommerce-agent \
  -v $(pwd)/config.yaml:/app/config.yaml \
  -v $(pwd)/data:/app/data \
  ecommerce-agent
```

---

## 🔧 技术细节

### 浏览器管理
- 基于 Playwright 实现跨浏览器支持（Chromium / Firefox / WebKit）
- Stealth 模式规避反检测
- 随机操作延迟模拟人类行为
- Cookie 持久化实现断点续登

### 任务调度
- 基于 APScheduler 实现精确定时调度
- 支持 Interval 和 Cron 两种触发模式
- 优雅关闭：等待当前任务完成后再退出

### 异常恢复
- 网络断开自动重连
- 页面崩溃自动重启浏览器
- 连续失败自动降级并告警

### 安全设计
- 敏感信息通过环境变量注入（`${ENV_VAR}` 语法）
- Cookie 加密存储
- 操作日志完整记录，支持审计

---

## 📊 性能指标

| 指标 | 数值 |
|------|------|
| 订单抓取速度 | ~100 单/分钟 |
| 退款处理速度 | ~50 单/分钟 |
| 内存占用 | < 500MB（headless 模式） |
| 支持并发店铺数 | 10+ |

---

## 🛣️ 路线图

- [ ] 支持更多电商平台（抖音、快手）
- [ ] 接入 AI 模型智能判断退款申请
- [ ] Web 管理面板
- [ ] 分布式部署支持
- [ ] 更多快递公司 API 对接

---

## 📄 许可证

MIT License

---

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！
