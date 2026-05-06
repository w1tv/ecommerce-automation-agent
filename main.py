#!/usr/bin/env python3
"""
电商自动化 Agent - 主入口
负责任务调度、进程守护和整体协调

【修复内容】
1. 引入 APScheduler 实现真正的定时任务调度
2. 登录验证：提交后检查页面变化，确认登录成功
3. 验证码检测：检测到验证码时暂停并告警
4. Cookie 持久化支持
5. 优雅关闭支持
"""
import os
import sys
import time
import signal
import argparse
import threading
from typing import Dict, Any, Optional, Callable
from datetime import datetime
from pathlib import Path
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

# 确保项目根目录在路径中
sys.path.insert(0, str(Path(__file__).parent))

from loguru import logger

from utils.config_loader import ConfigLoader, load_config, get_config
from utils.helpers import ensure_dir, format_time
from core.logger import setup_logger, log_operation
from core.browser_manager import BrowserManager
from core.exception_handler import ExceptionHandler, RecoveryManager, ActionWithRecovery
from core.refund_handler import RefundHandler
from core.order_fetcher import OrderFetcher
from core.label_printer import LabelPrinter

# 【新增】尝试导入告警模块
try:
    from core.alerter import Alerter
    ALERTER_AVAILABLE = True
except ImportError:
    ALERTER_AVAILABLE = False
    logger.warning("告警模块未找到，跳过告警功能")


class EcommerceAgent:
    """
    电商自动化 Agent 主类
    协调所有模块的工作，提供任务调度和进程守护功能
    
    【修复】使用 APScheduler 替代简陋的 time % 60 触发逻辑
    """
    
    def __init__(self, config_path: str = "config.yaml"):
        """
        初始化电商自动化 Agent
        
        Args:
            config_path: 配置文件路径
        """
        self.config_path = config_path
        self.config_loader = ConfigLoader()
        
        # 加载配置
        self.config = self.config_loader.load(config_path)
        
        # 【新增】环境变量替换
        self._apply_env_overrides()
        
        # 设置日志
        self._setup_logging()
        
        # 运行状态
        self.is_running = False
        self.should_stop = False
        
        # 【新增】优雅关闭锁
        self._shutdown_event = threading.Event()
        self._current_task_lock = threading.Lock()
        self._current_task = None
        
        # 店铺处理器映射
        self.shop_handlers: Dict[str, Dict[str, Any]] = {}
        
        # 心跳状态
        self.last_heartbeat = time.time()
        self.heartbeat_failure_count = 0
        
        # 【新增】APScheduler 调度器
        self.scheduler: Optional[BackgroundScheduler] = None
        
        # 【新增】告警器
        self.alerter: Optional[Alerter] = None
        if ALERTER_AVAILABLE and self.config.get('alerter.enabled', False):
            try:
                self.alerter = Alerter(self.config)
                logger.info("告警模块已初始化")
            except Exception as e:
                logger.warning(f"告警模块初始化失败: {e}")
        
        # 【新增】已处理的订单/退款缓存（带上限控制）
        self._processed_order_ids: set = set()
        self._processed_refund_ids: set = set()
        self._cache_max_size = 1000
        
        # 信号处理
        self._setup_signal_handlers()
        
        # 创建必要的目录
        self._create_directories()
        
        logger.info("=" * 60)
        logger.info("电商自动化 Agent 初始化完成")
        logger.info("=" * 60)
    
    def _apply_env_overrides(self) -> None:
        """【新增】应用环境变量覆盖敏感配置"""
        # 在配置中支持 ${ENV_VAR} 语法
        def replace_env_vars(obj):
            if isinstance(obj, str):
                # 匹配 ${VAR_NAME} 格式
                import re
                pattern = r'\$\{([^}]+)\}'
                matches = re.findall(pattern, obj)
                for var_name in matches:
                    env_value = os.environ.get(var_name, '')
                    if env_value:
                        obj = obj.replace(f'${{{var_name}}}', env_value)
                return obj
            elif isinstance(obj, dict):
                return {k: replace_env_vars(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [replace_env_vars(item) for item in obj]
            return obj
        
        # 递归替换所有配置值中的环境变量
        self.config = replace_env_vars(self.config)
        
        # 重新加载配置
        self.config_loader._config = self.config
    
    def _create_directories(self) -> None:
        """创建必要的目录结构"""
        ensure_dir('./logs')
        ensure_dir('./logs/screenshots')
        ensure_dir('./data/orders')
        ensure_dir('./data/refunds')
        ensure_dir('./data/print_tasks')
        ensure_dir('./data/sessions')  # 【新增】Session 存储目录
        ensure_dir('./downloads')
    
    def _setup_logging(self) -> None:
        """配置日志系统"""
        logging_config = self.config.get('logging', {})
        
        setup_logger(
            level=logging_config.get('level', 'INFO'),
            file_path=logging_config.get('file_path', './logs/ecommerce_agent.log'),
            rotation=logging_config.get('rotation', '500 MB'),
            retention=logging_config.get('retention', '30 days'),
            console=logging_config.get('console', True)
        )
    
    def _setup_signal_handlers(self) -> None:
        """设置信号处理器"""
        def signal_handler(signum, frame):
            signal_name = signal.Signals(signum).name
            logger.info(f"接收到信号: {signal_name}，准备停止...")
            self.stop()
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
    
    def start(self) -> bool:
        """
        启动 Agent
        
        Returns:
            是否启动成功
        """
        if self.is_running:
            logger.warning("Agent 已在运行中")
            return False
        
        logger.info("正在启动电商自动化 Agent...")
        
        try:
            # 验证配置
            if not self._validate_config():
                return False
            
            # 初始化所有店铺的处理器
            self._initialize_shop_handlers()
            
            # 启动主循环
            self.is_running = True
            self.should_stop = False
            
            # 【修复】启动 APScheduler 调度器
            self._start_scheduler()
            
            # 如果配置了启动时执行，则执行首次任务
            schedule_config = self.config.get('schedule', {})
            if schedule_config.get('run_on_startup', True):
                logger.info("执行启动任务...")
                self._run_tasks()
            
            # 主循环（主要做心跳检测和监控）
            self._main_loop()
            
            return True
            
        except Exception as e:
            logger.error(f"Agent 启动失败: {e}")
            return False
        finally:
            self._cleanup()
    
    def _validate_config(self) -> bool:
        """验证配置有效性"""
        shops = self.config_loader.all_shops
        
        if not shops:
            logger.error("配置中未找到任何店铺配置")
            return False
        
        logger.info(f"检测到 {len(shops)} 个店铺配置")
        
        for shop in shops:
            shop_name = shop.get('name', '未命名')
            
            if not shop.get('login_url'):
                logger.warning(f"店铺 '{shop_name}' 未配置登录URL")
            
            if not shop.get('credentials', {}).get('username'):
                logger.warning(f"店铺 '{shop_name}' 未配置用户名")
        
        return True
    
    def _initialize_shop_handlers(self) -> None:
        """初始化所有店铺的处理器"""
        shops = self.config_loader.all_shops
        
        for shop_config in shops:
            shop_name = shop_config.get('name', 'unknown')
            
            logger.info(f"初始化店铺处理器: {shop_name}")
            
            try:
                # 创建浏览器管理器
                browser_manager = BrowserManager(shop_config)
                
                # 创建异常处理器
                exception_handler = ExceptionHandler()
                recovery_manager = RecoveryManager(browser_manager)
                
                # 创建业务处理器
                refund_handler = RefundHandler(browser_manager, shop_config)
                order_fetcher = OrderFetcher(browser_manager, shop_config)
                label_printer = LabelPrinter(browser_manager, shop_config)
                
                # 【修复】设置新订单回调 - 确保参数匹配
                order_fetcher.set_new_order_callback(
                    lambda order, lp=label_printer: self._on_new_order(order, lp)
                )
                
                # 保存处理器
                self.shop_handlers[shop_name] = {
                    'browser': browser_manager,
                    'refund_handler': refund_handler,
                    'order_fetcher': order_fetcher,
                    'label_printer': label_printer,
                    'exception_handler': exception_handler,
                    'recovery_manager': recovery_manager,
                    'config': shop_config,
                    'paused': False  # 【新增】店铺暂停状态
                }
                
            except Exception as e:
                logger.error(f"初始化店铺 '{shop_name}' 处理器失败: {e}")
    
    def _start_scheduler(self) -> None:
        """
        【修复】启动 APScheduler 调度器
        替代原来简陋的 int(time.time()) % 60 == 0 触发逻辑
        """
        schedule_config = self.config.get('schedule', {})
        
        if not schedule_config.get('enabled', True):
            logger.info("定时调度已禁用")
            return
        
        self.scheduler = BackgroundScheduler(timezone='Asia/Shanghai')
        
        # 订单抓取定时任务
        order_interval = schedule_config.get('order_fetch_interval', 5)
        self.scheduler.add_job(
            self._scheduled_order_fetch,
            trigger=IntervalTrigger(minutes=order_interval),
            id='order_fetch',
            name='订单抓取任务',
            replace_existing=True
        )
        logger.info(f"订单抓取任务已调度（间隔: {order_interval}分钟）")
        
        # 退款处理定时任务
        refund_interval = schedule_config.get('refund_process_interval', 10)
        self.scheduler.add_job(
            self._scheduled_refund_process,
            trigger=IntervalTrigger(minutes=refund_interval),
            id='refund_process',
            name='退款处理任务',
            replace_existing=True
        )
        logger.info(f"退款处理任务已调度（间隔: {refund_interval}分钟）")
        
        # 心跳检测定时任务
        heartbeat_interval = schedule_config.get('heartbeat_interval', 60)
        self.scheduler.add_job(
            self._heartbeat_check,
            trigger=IntervalTrigger(seconds=heartbeat_interval),
            id='heartbeat',
            name='心跳检测',
            replace_existing=True
        )
        logger.info(f"心跳检测任务已调度（间隔: {heartbeat_interval}秒）")
        
        # 启动调度器
        self.scheduler.start()
        logger.info("APScheduler 定时任务调度器已启动")
    
    def _scheduled_order_fetch(self) -> None:
        """【新增】定时订单抓取任务"""
        if self.should_stop:
            return
        
        logger.info("[定时任务] 开始执行订单抓取...")
        
        for shop_name, handlers in self.shop_handlers.items():
            if self.should_stop:
                break
            
            # 检查店铺是否被暂停（如验证码检测）
            if handlers.get('paused', False):
                logger.info(f"店铺 '{shop_name}' 已暂停，跳过订单抓取")
                continue
            
            try:
                order_fetcher = handlers['order_fetcher']
                new_orders = order_fetcher.fetch()
                
                if new_orders:
                    logger.info(f"店铺 '{shop_name}' 发现 {len(new_orders)} 条新订单")
                    
                    # 【新增】缓存控制
                    self._processed_order_ids.update(o.order_id for o in new_orders)
                    self._trim_cache()
                
            except Exception as e:
                logger.error(f"店铺 '{shop_name}' 订单抓取失败: {e}")
                self._send_alert('order_fetch_error', {
                    'shop': shop_name,
                    'error': str(e)
                })
    
    def _scheduled_refund_process(self) -> None:
        """【新增】定时退款处理任务"""
        if self.should_stop:
            return
        
        logger.info("[定时任务] 开始执行退款处理...")
        
        for shop_name, handlers in self.shop_handlers.items():
            if self.should_stop:
                break
            
            if handlers.get('paused', False):
                logger.info(f"店铺 '{shop_name}' 已暂停，跳过退款处理")
                continue
            
            try:
                refund_handler = handlers['refund_handler']
                result = refund_handler.process()
                
                if result.get('errors', 0) > 0:
                    logger.warning(f"店铺 '{shop_name}' 退款处理有 {result['errors']} 个错误")
                    self._send_alert('refund_error', {
                        'shop': shop_name,
                        'result': result
                    })
                
                # 【新增】缓存控制
                if result.get('details'):
                    self._processed_refund_ids.update(
                        d.get('refund_id') for d in result['details']
                    )
                    self._trim_cache()
                
            except Exception as e:
                logger.error(f"店铺 '{shop_name}' 退款处理失败: {e}")
                self._send_alert('refund_error', {
                    'shop': shop_name,
                    'error': str(e)
                })
    
    def _trim_cache(self) -> None:
        """【新增】清理过期缓存，防止内存泄漏"""
        max_size = self._cache_max_size
        
        if len(self._processed_order_ids) > max_size:
            # 保留最近的
            self._processed_order_ids = set(
                list(self._processed_order_ids)[-max_size:]
            )
        
        if len(self._processed_refund_ids) > max_size:
            self._processed_refund_ids = set(
                list(self._processed_refund_ids)[-max_size:]
            )
    
    def _send_alert(self, event_type: str, data: Dict[str, Any]) -> None:
        """【新增】发送告警"""
        if not self.alerter:
            return
        
        try:
            self.alerter.send(event_type, data)
        except Exception as e:
            logger.warning(f"发送告警失败: {e}")
    
    def _run_tasks(self) -> None:
        """执行所有店铺的任务（手动触发或启动时）"""
        for shop_name, handlers in self.shop_handlers.items():
            if self.should_stop:
                break
            
            try:
                self._process_shop(shop_name, handlers)
                
                # 店铺之间休息
                rest_interval = self.config.get('global.shop_rest_interval', 10)
                logger.debug(f"休息 {rest_interval} 秒后处理下一个店铺...")
                time.sleep(rest_interval)
                
            except Exception as e:
                logger.error(f"处理店铺 '{shop_name}' 时出错: {e}")
    
    def _process_shop(self, shop_name: str, handlers: Dict[str, Any]) -> None:
        """
        处理单个店铺的任务
        
        Args:
            shop_name: 店铺名称
            handlers: 处理器字典
        """
        # 【新增】记录当前任务
        with self._current_task_lock:
            self._current_task = shop_name
        
        try:
            logger.info(f"开始处理店铺: {shop_name}")
            
            browser = handlers['browser']
            refund_handler = handlers['refund_handler']
            order_fetcher = handlers['order_fetcher']
            
            with log_operation(shop_name, "店铺任务处理") as op:
                try:
                    # 1. 确保浏览器连接
                    if not browser.state.is_alive:
                        logger.info(f"启动浏览器...")
                        if not browser.start():
                            logger.error("浏览器启动失败，跳过此店铺")
                            return
                    
                    # 【新增】尝试加载持久化的 Cookie
                    if self.config.get('session.enabled', True):
                        if browser.load_cookies(shop_name):
                            logger.info("已加载持久化的 Session")
                    
                    # 2. 检查会话是否有效（是否需要登录）
                    if not self._ensure_logged_in(handlers):
                        logger.error("登录失败，跳过此店铺")
                        return
                    
                    # 【新增】保存登录后的 Cookie
                    if self.config.get('session.enabled', True):
                        browser.save_cookies(shop_name)
                    
                    # 3. 处理退款
                    refund_config = handlers['config'].get('refund_rules', {})
                    if refund_config.get('enabled', True):
                        logger.info("开始处理退款申请...")
                        refund_result = refund_handler.process()
                        op.log(f"退款处理结果: {refund_result}")
                    
                    # 4. 抓取订单
                    logger.info("开始抓取订单...")
                    new_orders = order_fetcher.fetch()
                    
                    if new_orders:
                        op.log(f"发现 {len(new_orders)} 条新订单")
                        logger.info(f"新订单: {[o.order_id for o in new_orders]}")
                    
                except Exception as e:
                    logger.error(f"处理店铺 '{shop_name}' 异常: {e}")
                    raise
        finally:
            # 【新增】清理当前任务
            with self._current_task_lock:
                self._current_task = None
    
    def _detect_captcha(self, browser: BrowserManager, handlers: Dict[str, Any]) -> bool:
        """
        【新增】检测验证码
        
        Args:
            browser: 浏览器管理器
            handlers: 处理器字典
            
        Returns:
            是否检测到验证码
        """
        captcha_config = self.config.get('captcha', {})
        
        if not captcha_config.get('detect_enabled', True):
            return False
        
        selectors = captcha_config.get('selectors', [])
        
        for selector in selectors:
            if browser.wait_for_selector(selector, timeout=2000):
                logger.warning("检测到验证码！")
                
                # 保存截图
                screenshot_path = browser.save_screenshot(f"captcha_detected_{int(time.time())}")
                logger.info(f"验证码截图已保存: {screenshot_path}")
                
                # 发送告警
                self._send_alert('captcha_detected', {
                    'shop': handlers['config'].get('name'),
                    'screenshot': screenshot_path
                })
                
                # 根据配置处理
                action = captcha_config.get('action', 'wait')
                
                if action == 'wait':
                    # 暂停该店铺处理
                    handlers['paused'] = True
                    logger.warning(f"店铺 '{handlers['config'].get('name')}' 已暂停，等待人工处理...")
                    
                    # 等待超时或人工处理
                    timeout = captcha_config.get('wait_timeout', 300)
                    start_time = time.time()
                    
                    while time.time() - start_time < timeout:
                        # 定期检查验证码是否消失
                        if not browser.wait_for_selector(selector, timeout=5000):
                            logger.info("验证码已消失，恢复处理")
                            handlers['paused'] = False
                            return False
                        time.sleep(10)
                    
                    logger.warning("验证码等待超时，跳过该操作")
                    return True
                    
                elif action == 'alert':
                    # 只发送告警，继续执行
                    return True
                    
                elif action == 'skip':
                    # 跳过该操作
                    return True
        
        return False
    
    def _ensure_logged_in(self, handlers: Dict[str, Any]) -> bool:
        """
        确保已登录
        
        Args:
            handlers: 处理器字典
            
        Returns:
            是否已登录
        """
        browser = handlers['browser']
        config = handlers['config']
        credentials = config.get('credentials', {})
        
        try:
            # 访问店铺主页检查登录状态
            login_url = config.get('login_url')
            if not login_url:
                return True
            
            # 尝试导航
            browser.navigate(login_url)
            time.sleep(2)
            
            # 【新增】验证码检测
            if self._detect_captcha(browser, handlers):
                logger.warning("检测到验证码，无法继续登录流程")
                return False
            
            # 检查是否跳转到登录页面
            login_check_selectors = [
                '#login-form',
                '.login-container',
                '[class*="login"]',
                '#username',
                '.login-btn'
            ]
            
            for selector in login_check_selectors:
                if browser.wait_for_selector(selector, timeout=3000):
                    logger.info("检测到未登录，执行登录...")
                    return self._perform_login(handlers)
            
            logger.info("已登录")
            return True
            
        except Exception as e:
            logger.warning(f"登录状态检查异常: {e}")
            return False
    
    def _perform_login(self, handlers: Dict[str, Any]) -> bool:
        """
        【修复】执行登录并验证结果
        
        修复说明：
        1. 原版本提交后直接返回True，未验证登录是否成功
        2. 现在提交后等待页面变化，检查是否仍在登录页
        3. 增加验证码检测
        
        Args:
            handlers: 处理器字典
            
        Returns:
            是否登录成功
        """
        browser = handlers['browser']
        config = handlers['config']
        credentials = config.get('credentials', {})
        selectors = config.get('selectors', {}).get('login', {})
        
        username = credentials.get('username')
        password = credentials.get('password')
        
        if not username or not password:
            logger.error("未配置登录凭据")
            return False
        
        try:
            logger.info(f"开始登录: {username}")
            
            # 输入用户名
            username_input = selectors.get('username_input', '#username')
            if browser.fill(username_input, username):
                logger.debug("用户名已填写")
            
            # 【新增】随机延迟模拟人类操作
            browser.random_delay()
            
            # 输入密码
            password_input = selectors.get('password_input', '#password')
            if browser.fill(password_input, password):
                logger.debug("密码已填写")
            
            # 【新增】随机延迟
            browser.random_delay()
            
            # 点击登录按钮
            submit_button = selectors.get('submit_button', '#login-btn')
            browser.click(submit_button)
            
            # 【新增】验证码检测
            time.sleep(1)
            if self._detect_captcha(browser, handlers):
                logger.warning("登录时检测到验证码")
                return False
            
            # 【修复】等待登录结果验证
            logger.info("等待登录结果...")
            
            # 登录成功标志元素
            success_selectors = selectors.get('success_indicator', [
                '.seller-center',
                '#seller-home',
                '.main-content',
                '.header-user',
                '[class*="seller"]'
            ])
            
            # 检查是否跳转到登录页面（仍在登录页说明失败）
            login_page_indicators = [
                '#login-form',
                '.login-container',
                '#username',
                '.login-btn'
            ]
            
            max_wait = 15  # 最多等待15秒
            start_time = time.time()
            login_succeeded = False
            
            while time.time() - start_time < max_wait:
                time.sleep(1)
                
                # 【新增】验证码检测
                if self._detect_captcha(browser, handlers):
                    return False
                
                # 检查是否仍在登录页
                still_on_login_page = False
                for indicator in login_page_indicators:
                    if browser.wait_for_selector(indicator, timeout=1000):
                        still_on_login_page = True
                        break
                
                if still_on_login_page:
                    # 仍在登录页，继续等待
                    continue
                
                # 不在登录页了，检查是否登录成功
                for success_sel in success_selectors:
                    if browser.wait_for_selector(success_sel, timeout=2000):
                        logger.info("检测到登录成功标志")
                        login_succeeded = True
                        break
                
                if login_succeeded:
                    break
            
            # 保存截图
            browser.save_screenshot(f"after_login_{int(time.time())}")
            
            if login_succeeded:
                logger.info("登录验证成功")
                
                # 【新增】发送登录成功告警
                self._send_alert('login_success', {
                    'shop': config.get('name'),
                    'username': username
                })
                
                return True
            else:
                logger.error("登录验证失败：未检测到登录成功标志")
                
                # 【新增】发送登录失败告警
                self._send_alert('login_failed', {
                    'shop': config.get('name'),
                    'username': username,
                    'reason': '未检测到登录成功标志'
                })
                
                return False
            
        except Exception as e:
            logger.error(f"登录失败: {e}")
            browser.save_screenshot("login_error")
            
            # 【新增】发送登录失败告警
            self._send_alert('login_failed', {
                'shop': config.get('name'),
                'username': username,
                'error': str(e)
            })
            
            return False
    
    def _on_new_order(self, order, label_printer: LabelPrinter) -> None:
        """
        【修复】新订单回调
        
        修复说明：原版本参数不匹配 print_label 方法签名
        
        Args:
            order: OrderInfo 订单信息对象
            label_printer: LabelPrinter 打印处理器
        """
        logger.info(f"检测到新订单: {order.order_id}")
        
        try:
            # 【修复】构建打印任务，确保参数与 print_label 签名一致
            # print_label 签名: order_id, express_company, receiver_info, sender_info, items, weight, remark
            
            # 处理商品名称列表
            item_names = getattr(order, 'item_names', [])
            items = [{'name': name, 'count': 1} for name in item_names] if item_names else []
            
            # 构建收件人信息
            receiver_info = {
                'name': getattr(order, 'buyer_name', ''),
                'phone': getattr(order, 'phone', ''),
                'address': getattr(order, 'address', ''),
            }
            
            # 执行打印
            result = label_printer.print_label(
                order_id=order.order_id,
                express_company=None,  # 使用默认快递
                receiver_info=receiver_info,
                sender_info=None,  # 使用默认发件人
                items=items,
                weight=0,
                remark=getattr(order, 'remark', '') or ''
            )
            
            if result.status == 'completed':
                logger.info(f"快递单打印成功: {result.task_id}")
            else:
                logger.warning(f"快递单打印失败: {result.error_message}")
                
        except Exception as e:
            logger.error(f"处理新订单打印失败: {e}")
    
    def _main_loop(self) -> None:
        """【修复】主循环 - 主要做状态监控，不直接执行任务"""
        logger.info("主循环已启动，任务由 APScheduler 调度...")
        
        while not self.should_stop:
            try:
                # 更新心跳时间
                self.last_heartbeat = time.time()
                
                # 等待停止信号（带有超时以便定期检查状态）
                self._shutdown_event.wait(timeout=5)
                
            except KeyboardInterrupt:
                logger.info("接收到中断信号")
                break
            except Exception as e:
                logger.error(f"主循环异常: {e}")
                time.sleep(5)
    
    def _heartbeat_check(self) -> None:
        """心跳检测"""
        heartbeat_config = self.config.get('heartbeat', {})
        
        if not heartbeat_config.get('enabled', True):
            return
        
        interval = heartbeat_config.get('interval', 60)
        failure_threshold = heartbeat_config.get('failure_threshold', 3)
        
        current_time = time.time()
        
        if current_time - self.last_heartbeat > interval:
            logger.warning(f"心跳检测超时（间隔: {interval}秒）")
            self.heartbeat_failure_count += 1
            
            if self.heartbeat_failure_count >= failure_threshold:
                logger.error("连续心跳失败次数过多，尝试重启...")
                self._emergency_recovery()
                
                # 【新增】发送心跳超时告警
                self._send_alert('heartbeat_timeout', {
                    'failure_count': self.heartbeat_failure_count,
                    'interval': interval
                })
        else:
            self.heartbeat_failure_count = 0
        
        self.last_heartbeat = current_time
    
    def _emergency_recovery(self) -> None:
        """紧急恢复"""
        logger.warning("执行紧急恢复...")
        
        try:
            # 关闭所有浏览器
            for shop_name, handlers in self.shop_handlers.items():
                try:
                    handlers['browser'].close()
                except Exception:
                    pass
            
            # 重新初始化
            self._initialize_shop_handlers()
            
            logger.info("紧急恢复完成")
            
        except Exception as e:
            logger.error(f"紧急恢复失败: {e}")
    
    def _cleanup(self) -> None:
        """清理资源"""
        logger.info("正在清理资源...")
        
        self.is_running = False
        
        # 【新增】停止调度器
        if self.scheduler and self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("调度器已停止")
        
        # 关闭所有浏览器
        for shop_name, handlers in self.shop_handlers.items():
            try:
                # 【新增】保存最后的 Cookie
                if self.config.get('session.enabled', True):
                    handlers['browser'].save_cookies(shop_name)
                
                handlers['browser'].close()
                logger.info(f"已关闭店铺 '{shop_name}' 浏览器")
            except Exception as e:
                logger.warning(f"关闭浏览器时出错: {e}")
        
        self.shop_handlers.clear()
        logger.info("资源清理完成")
    
    def stop(self) -> None:
        """
        【修复】停止 Agent - 支持优雅关闭
        
        修复说明：
        1. 等待当前任务完成
        2. 保存运行状态
        """
        logger.info("正在停止电商自动化 Agent...")
        self.should_stop = True
        self.is_running = False
        
        # 【新增】通知关闭事件
        self._shutdown_event.set()
        
        # 【新增】等待当前任务完成（带超时）
        timeout = 30  # 最多等待30秒
        start_time = time.time()
        
        while self._current_task and time.time() - start_time < timeout:
            logger.info(f"等待当前任务完成: {self._current_task}")
            time.sleep(1)
        
        # 【新增】保存运行状态
        self._save_state()
    
    def _save_state(self) -> None:
        """【新增】保存运行状态"""
        try:
            state_file = Path('./data/agent_state.json')
            import json
            
            state = {
                'last_run': format_time(),
                'shops': list(self.shop_handlers.keys()),
                'processed_orders': len(self._processed_order_ids),
                'processed_refunds': len(self._processed_refund_ids)
            }
            
            with open(state_file, 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=2)
            
            logger.debug(f"运行状态已保存: {state_file}")
            
        except Exception as e:
            logger.warning(f"保存运行状态失败: {e}")


def parse_arguments():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description='电商自动化 Agent',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例用法:
  python main.py                           # 使用默认配置启动
  python main.py -c config.yaml            # 指定配置文件
  python main.py --debug                   # 启用调试模式
  python main.py --shop "店铺A"           # 只处理指定店铺
  python main.py --task refund             # 只执行退款处理任务
        """
    )
    
    parser.add_argument(
        '-c', '--config',
        default='config.yaml',
        help='配置文件路径 (默认: config.yaml)'
    )
    
    parser.add_argument(
        '--debug',
        action='store_true',
        help='启用调试模式'
    )
    
    parser.add_argument(
        '--shop',
        help='指定要处理的店铺名称'
    )
    
    parser.add_argument(
        '--task',
        choices=['all', 'refund', 'order'],
        default='all',
        help='指定要执行的任务类型 (默认: all)'
    )
    
    parser.add_argument(
        '--headless',
        action='store_true',
        help='使用无头模式运行浏览器'
    )
    
    parser.add_argument(
        '--once',
        action='store_true',
        help='只执行一次任务后退出（用于测试）'
    )
    
    return parser.parse_args()


def main():
    """主函数"""
    args = parse_arguments()
    
    # 【修复】创建必要的目录
    ensure_dir('./logs')
    ensure_dir('./logs/screenshots')
    ensure_dir('./data/orders')
    ensure_dir('./data/refunds')
    ensure_dir('./data/print_tasks')
    ensure_dir('./data/sessions')
    ensure_dir('./downloads')
    
    # 调试模式
    if args.debug:
        os.environ['DEBUG'] = '1'
        logger.info("调试模式已启用")
    
    # 创建 Agent 实例
    agent = EcommerceAgent(config_path=args.config)
    
    # 命令行参数覆盖配置
    if args.headless:
        agent.config['browser']['headless'] = True
        agent.config_loader.set('browser.headless', True)
    
    if args.debug:
        agent.config['global']['debug'] = True
        agent.config_loader.set('global.debug', True)
    
    try:
        # 启动 Agent
        success = agent.start()
        
        if success:
            logger.info("Agent 运行中，按 Ctrl+C 停止")
            # 主线程等待
            while agent.is_running:
                time.sleep(1)
        else:
            logger.error("Agent 启动失败")
            sys.exit(1)
            
    except KeyboardInterrupt:
        logger.info("接收到中断信号")
    finally:
        agent.stop()
        logger.info("Agent 已停止")


if __name__ == '__main__':
    main()
