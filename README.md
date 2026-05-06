# 电商自动化 Agent

基于 Python + Playwright 的电商后台自动化运营系统，支持自动处理退货退款、订单抓取、快递单打印等功能。

## 功能特性

- ✅ **自动处理退货退款**：根据配置规则自动审批/拒绝退款申请
- ✅ **自动抓取订单**：定时抓取新订单数据，保存为结构化数据
- ✅ **自动打印快递单**：支持多种打印方案（本地服务、API对接、文件生成）
- ✅ **稳定不掉线**：心跳检测、会话保活机制
- ✅ **异常自动重连**：网络断开、页面崩溃等异常时自动恢复
- ✅ **后台挂机运行**：支持 headless 模式长期运行
- ✅ **多店铺支持**：可同时管理多个电商平台店铺
- ✅ **Docker 部署**：支持 Docker 一键部署

## 目录结构

```
电商自动化Agent/
├── main.py                    # 主入口，任务调度与守护
├── config.yaml                # 配置文件
├── requirements.txt           # Python 依赖
├── README.md                  # 使用文档
├── Dockerfile                 # Docker 构建文件
├── docker-compose.yaml        # Docker Compose 配置
│
├── core/                      # 核心模块
│   ├── __init__.py
│   ├── browser_manager.py     # 浏览器生命周期管理
│   ├── refund_handler.py       # 退货退款自动处理
│   ├── order_fetcher.py       # 订单抓取
│   ├── label_printer.py       # 快递单打印
│   ├── exception_handler.py   # 异常处理与自动重连
│   └── logger.py              # 日志模块
│
└── utils/                     # 工具函数
    ├── __init__.py
    ├── config_loader.py       # 配置文件加载器
    └── helpers.py             # 辅助函数
```

## 环境准备

### 系统要求

- Python 3.9+
- Linux / macOS / Windows
- Docker (可选，用于容器化部署)

### 安装 Playwright 浏览器

```bash
# 安装依赖
playwright install chromium
# 或者安装所有浏览器
playwright install
```

### 安装 Python 依赖

```bash
# 推荐使用虚拟环境
python -m venv venv
source venv/bin/activate  # Linux/macOS
# or
.\venv\Scripts\activate  # Windows

# 安装依赖
pip install -r requirements.txt
```

## 快速开始

### 1. 配置店铺信息

编辑 `config.yaml` 文件，配置你的店铺信息：

```yaml
shops:
  - name: "我的天猫店"
    platform: tmall
    login_url: "https://seller.tmall.com/login.htm"
    credentials:
      username: "your_username"
      password: "your_password"
    selectors:
      login:
        username_input: "#username"
        password_input: "#password"
        submit_button: "#login-btn"
      order:
        list_url: "https://trade.tmall.com/order/list.htm"
        order_item: ".order-item"
      refund:
        list_url: "https://Refund.tmall.com/refund_list.htm"
        refund_item: ".refund-item"
```

### 2. 配置退货退款规则

```yaml
refund_rules:
  enabled: true
  auto_approve:
    enabled: true
    max_amount: 50           # 50元以下自动同意
    reasons:
      - "不想要了"
      - "七天无理由"
  auto_reject:
    enabled: true
    reject_reasons:
      - "已使用影响二次销售"
  require_manual:
    amount_threshold: 200   # 200元以上需人工审核
    keywords:
      - "质量问题"
      - "假货"
  time_window:
    enabled: true
    start_hour: 9
    end_hour: 22
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

## 配置说明

### 全局配置 (global)

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| debug | bool | false | 调试模式，会保存更多截图 |
| shop_rest_interval | int | 10 | 店铺任务间休息间隔（秒） |
| max_retry | int | 3 | 失败重试次数 |
| retry_interval | int | 30 | 重试间隔（秒） |
| page_timeout | int | 30000 | 页面加载超时（毫秒） |
| element_timeout | int | 10 | 元素等待超时（秒） |

### 浏览器配置 (browser)

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| type | str | chromium | 浏览器类型 (chromium/firefox/webkit) |
| headless | bool | true | 是否使用无头模式 |
| disable_images | bool | false | 是否禁用图片加载 |
| viewport.width | int | 1920 | 视口宽度 |
| viewport.height | int | 1080 | 视口高度 |

### 快递配置 (express)

| 配置项 | 类型 | 说明 |
|--------|------|------|
| default_courier | str | 默认快递公司代码 |
| api_url | str | 本地打印服务 API 地址 |
| api_key | str | API 密钥 |
| month_code | str | 电子面单月结账号 |

### 订单抓取配置 (order_fetch)

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| interval | int | 60 | 抓取间隔（秒） |
| status_filter | str | wait_send | 订单状态筛选 |
| max_orders | int | 100 | 每次最大抓取数 |
| save_path | str | ./data/orders | 保存路径 |

## 运行方式

### 直接运行

```bash
# 前台运行
python main.py

# 后台运行 (Linux)
nohup python main.py > output.log 2>&1 &
```

### 使用 Supervisor 管理进程

```ini
[program:ecommerce_agent]
command = /path/to/venv/bin/python /path/to/main.py
directory = /path/to/project
autostart = true
autorestart = true
stderr_logfile = /var/log/ecommerce_agent.err.log
stdout_logfile = /var/log/ecommerce_agent.out.log
```

### 使用 Systemd (Linux)

创建服务文件 `/etc/systemd/system/ecommerce-agent.service`:

```ini
[Unit]
Description=Ecommerce Automation Agent
After=network.target

[Service]
Type=simple
User=your_user
WorkingDirectory=/path/to/project
ExecStart=/path/to/venv/bin/python main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

启动服务：

```bash
sudo systemctl daemon-reload
sudo systemctl enable ecommerce-agent
sudo systemctl start ecommerce-agent
```

## Docker 部署

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

## 退货退款规则配置说明

### 规则优先级

1. **人工审核规则** (require_manual) - 最高优先级
2. **自动拒绝规则** (auto_reject)
3. **自动同意规则** (auto_approve)

### 规则配置示例

```yaml
refund_rules:
  enabled: true
  
  # 自动同意规则
  auto_approve:
    enabled: true
    max_amount: 100          # 100元以下
    reasons:                 # 符合以下原因之一
      - "拍多了"
      - "拍错了"
      - "不想要了"
      - "七天无理由"
  
  # 自动拒绝规则
  auto_reject:
    enabled: true
    min_amount_threshold: 50  # 50元以下才会自动拒绝
    reject_reasons:
      - "已拆封影响二次销售"
      - "人为损坏"
  
  # 需要人工审核
  require_manual:
    enabled: true
    amount_threshold: 500    # 500元以上必须人工
    keywords:               # 包含这些关键词必须人工
      - "假货"
      - "严重质量问题"
      - "投诉"
  
  # 处理时间窗口
  time_window:
    enabled: true
    start_hour: 8           # 早上8点
    end_hour: 22            # 晚上10点
```

## 快递单打印对接说明

### 方案一：本地打印服务

启动本地打印服务，接收 API 请求：

1. 安装打印机驱动
2. 配置 `config.yaml` 中的 `express.api_url`
3. 实现打印服务接收端

### 方案二：对接快递公司 API

不同快递公司的电子面单 API：

- **顺丰**：https://open-sf.sf-express.com/
- **菜鸟**：https://open.alilogistics.com/
- **京东**：https://open.jd.com/

### 方案三：生成面单文件

系统会自动生成 HTML 格式的面单文件，保存到 `./data/print_tasks/` 目录，可手动打印。

## 常见问题与排查

### 1. 浏览器启动失败

```bash
# 重新安装 Playwright 浏览器
playwright install chromium
playwright install-deps chromium
```

### 2. 登录失败/验证码

- 检查网络连接
- 尝试使用有头模式 (`headless: false`) 手动登录一次
- 检查账号是否有异常

### 3. 页面元素找不到

- 使用调试模式 (`--debug`) 查看截图
- 检查 `config.yaml` 中的选择器配置是否正确
- 某些平台可能需要等待更长时间

### 4. 内存占用过高

- 定期重启浏览器进程
- 使用 headless 模式
- 减少同时运行的店铺数量

### 5. 进程意外退出

```bash
# 使用 systemd/supervisor 管理进程
# 查看日志排查原因
tail -f logs/ecommerce_agent.log
```

## 日志查看

```bash
# 实时查看日志
tail -f logs/ecommerce_agent.log

# 查看错误日志
tail -f logs/ecommerce_agent_error.log

# 查看特定店铺的操作日志
ls logs/operations/
```

## 性能优化建议

1. **合理设置抓取间隔**：避免过于频繁请求
2. **使用 headless 模式**：减少资源占用
3. **禁用图片加载**：加速页面加载
4. **定期重启浏览器**：释放内存
5. **错峰处理店铺**：避免同时处理多个店铺

## 安全注意事项

1. **妥善保管凭据**：不要将密码直接写在配置文件中
2. **使用环境变量**：可以通过环境变量传入敏感信息
3. **定期更新**：保持依赖库最新版本
4. **监控异常**：关注日志中的异常报警

## 许可证

MIT License

## 联系方式

如有问题或建议，请提交 Issue。
