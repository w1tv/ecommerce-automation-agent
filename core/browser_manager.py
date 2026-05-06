"""
浏览器生命周期管理模块
负责 Playwright 浏览器的启动、复用、保活和异常恢复

【修复内容】
1. 增加 playwright-stealth 反检测支持
2. 增加随机操作延迟（模拟人类操作）
3. 增加 Cookie/Session 持久化
4. 增加更多反检测参数
"""
import os
import time
import asyncio
import random
import json
from typing import Optional, Dict, Any, Callable
from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import (
    sync_playwright,
    Browser, BrowserContext, Page, Playwright,
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeoutError
)
from loguru import logger

from utils.config_loader import get_config
from utils.helpers import retry_on_exception, ensure_dir, format_time


@dataclass
class BrowserState:
    """浏览器状态信息"""
    browser: Optional[Browser] = None
    context: Optional[BrowserContext] = None
    page: Optional[Page] = None
    is_alive: bool = False
    last_heartbeat: float = field(default_factory=time.time)
    failure_count: int = 0
    session_id: Optional[str] = None


class BrowserManager:
    """
    浏览器生命周期管理器
    负责：启动浏览器、创建上下文、页面导航、心跳检测、自动重连
    
    【修复】增加了：
    - stealth 反检测补丁
    - 随机延迟方法
    - Cookie 持久化
    """
    
    def __init__(self, shop_config: Dict[str, Any]):
        """
        初始化浏览器管理器
        
        Args:
            shop_config: 店铺配置字典
        """
        self.shop_config = shop_config
        self.config = get_config()
        
        # Playwright 对象
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        
        # 浏览器状态
        self.state = BrowserState()
        
        # 配置参数
        self.browser_type = self.config.get('browser.type', 'chromium')
        self.headless = self.config.get('browser.headless', True)
        self.page_timeout = self.config.get('global.page_timeout', 30) * 1000  # 转换为毫秒
        self.element_timeout = self.config.get('global.element_timeout', 10) * 1000
        
        # 调试配置
        self.debug = self.config.get('global.debug', False)
        self.download_path = self.config.get('browser.download_path', './downloads')
        
        # 【新增】反检测配置
        self.stealth_config = self.config.get('browser.stealth', {})
        self.stealth_enabled = self.stealth_config.get('enabled', True)
        
        # 【新增】随机延迟配置
        self.delay_min = self.stealth_config.get('random_delay_min', 100)
        self.delay_max = self.stealth_config.get('random_delay_max', 500)
        
        # 【新增】Session 持久化配置
        self.session_enabled = self.config.get('session.enabled', True)
        self.session_storage_path = ensure_dir(
            self.config.get('session.storage_path', './data/sessions')
        )
        
        # 截图保存目录
        self.screenshot_dir = ensure_dir("./logs/screenshots")
        
        # 【新增】标记是否已应用 stealth
        self._stealth_applied = False
        
        logger.info(f"BrowserManager 初始化完成 (店铺: {shop_config.get('name')})")
    
    def start(self) -> bool:
        """
        启动浏览器
        
        Returns:
            是否启动成功
        """
        try:
            logger.info("正在启动浏览器...")
            
            # 启动 Playwright
            if self.playwright is None:
                self.playwright = sync_playwright().start()
            
            # 【新增】尝试导入并应用 stealth
            self._apply_stealth()
            
            # 创建浏览器实例
            browser_args = self._get_browser_args()
            self.browser = self.playwright.chromium.launch(
                headless=self.headless,
                args=browser_args
            )
            
            # 创建浏览器上下文（会话）
            self.state.context = self._create_context()
            
            # 【修复】应用 stealth 到 context
            if self.stealth_enabled and not self._stealth_applied:
                self._apply_stealth_to_context()
            
            # 创建页面
            self.state.page = self.state.context.new_page()
            
            # 设置默认超时
            self.state.page.set_default_timeout(self.page_timeout)
            
            # 设置下载路径
            download_dir = ensure_dir(self.download_path)
            self.state.context.set_default_download_directory(str(download_dir))
            
            self.state.is_alive = True
            self.state.last_heartbeat = time.time()
            
            logger.info("浏览器启动成功")
            return True
            
        except Exception as e:
            logger.error(f"浏览器启动失败: {e}")
            self._handle_error(e)
            return False
    
    def _apply_stealth(self) -> None:
        """
        【新增】应用 stealth 补丁
        用于隐藏 Playwright 的自动化特征
        """
        if not self.stealth_enabled:
            return
        
        try:
            # 尝试导入 stealth 库
            from playwright_stealth import stealth_sync
            
            # 标记已应用
            self._stealth_applied = True
            logger.debug("playwright-stealth 补丁已加载")
            
        except ImportError:
            logger.warning(
                "playwright-stealth 未安装，自动化特征将不被隐藏。"
                "请运行: pip install playwright-stealth"
            )
            self._stealth_applied = False
        except Exception as e:
            logger.warning(f"stealth 补丁应用失败: {e}")
            self._stealth_applied = False
    
    def _apply_stealth_to_context(self) -> None:
        """
        【新增】将 stealth 应用到浏览器上下文
        """
        if not self._stealth_applied or not self.state.context:
            return
        
        try:
            from playwright_stealth import stealth_sync
            
            # 应用 stealth 到所有新页面
            # stealth_sync 会在页面创建时自动应用
            # 我们需要手动应用到已创建的页面
            if self.state.page:
                stealth_sync(self.state.page)
                logger.debug("Stealth 已应用到当前页面")
                
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"应用 stealth 到页面失败: {e}")
    
    def _get_browser_args(self) -> list:
        """获取浏览器启动参数"""
        # 【修复】增加更多反检测参数
        args = [
            '--disable-blink-features=AutomationControlled',  # 隐藏自动化特征
            '--disable-dev-shm-usage',  # 解决 Docker 环境下的崩溃问题
            '--no-sandbox',  # Docker 环境必需
            '--disable-setuid-sandbox',
            '--disable-web-security',  # 允许跨域
            '--disable-features=IsolateOrigins,site-per-process',
            # 【新增】反检测参数
            '--disable-infobars',
            '--disable-browser-side-navigation',
            '--disable-diagnostics',
            '--disable-extensions',
            '--disable-hang-monitor',
            '--disable-prompt-on-repost',
            '--disable-popup-blocking',
            '--disable-sync',
            '--disable-translate',
            '--metrics-recording-only',
            '--no-first-run',
            '--safebrowsing-disable-auto-update',
        ]
        
        # 无头模式下添加额外参数
        if self.headless:
            args.extend([
                '--disable-gpu',
                '--disable-software-rasterizer',
                '--disable-webgl',
                '--disable-raf-based-throttling',
            ])
        
        return args
    
    def _create_context(self) -> BrowserContext:
        """创建浏览器上下文"""
        viewport = self.config.get('browser.viewport', {'width': 1920, 'height': 1080})
        user_agent = self.config.get('browser.user_agent')
        
        context_options = {
            'viewport': viewport,
            'ignore_https_errors': True,
            'java_script_enabled': True,
            'locale': 'zh-CN',
            'timezone_id': 'Asia/Shanghai',
            'geolocation': {'longitude': 116.4, 'latitude': 39.9},
            'permissions': ['geolocation'],
            # 【新增】更多浏览器指纹参数
            'color_scheme': 'light',  # 固定颜色方案，避免指纹
            'device_scale_factor': 1,  # 固定设备比例因子
        }
        
        if user_agent:
            context_options['user_agent'] = user_agent
        
        # 禁用图片加载以提升速度
        if self.config.get('browser.disable_images', False):
            # 注意：这里不能直接禁用图片，否则会影响某些平台
            pass
        
        return self.browser.new_context(**context_options)
    
    def navigate(self, url: str, wait_until: str = "networkidle") -> bool:
        """
        导航到指定 URL
        
        Args:
            url: 目标 URL
            wait_until: 等待条件 (load, domcontentloaded, networkidle)
            
        Returns:
            是否导航成功
        """
        if not self.state.page:
            logger.error("页面未初始化")
            return False
        
        try:
            logger.debug(f"正在导航到: {url}")
            
            # 【新增】随机延迟模拟人类行为
            self.random_delay()
            
            self.state.page.goto(url, wait_until=wait_until, timeout=self.page_timeout)
            self.state.last_heartbeat = time.time()
            
            # 【新增】导航后应用 stealth
            if self._stealth_applied:
                try:
                    from playwright_stealth import stealth_sync
                    stealth_sync(self.state.page)
                except:
                    pass
            
            if self.debug:
                self.save_screenshot(f"navigate_{self._get_url_hash(url)}")
            
            return True
            
        except PlaywrightTimeoutError:
            logger.warning(f"页面加载超时: {url}")
            return False
        except Exception as e:
            logger.error(f"导航失败: {e}")
            return False
    
    def random_delay(self, min_ms: int = None, max_ms: int = None) -> None:
        """
        【新增】随机延迟，模拟人类操作间隔
        
        Args:
            min_ms: 最小延迟（毫秒），默认使用配置值
            max_ms: 最大延迟（毫秒），默认使用配置值
        """
        min_val = min_ms if min_ms is not None else self.delay_min
        max_val = max_ms if max_ms is not None else self.delay_max
        
        delay = random.randint(min_val, max_val) / 1000.0  # 转换为秒
        time.sleep(delay)
    
    def wait_for_selector(
        self,
        selector: str,
        state: str = "visible",
        timeout: Optional[int] = None
    ) -> bool:
        """
        等待元素出现
        
        Args:
            selector: CSS 选择器
            state: 等待状态 (attached, detached, visible, hidden)
            timeout: 超时时间（毫秒）
            
        Returns:
            元素是否出现
        """
        if not self.state.page:
            return False
        
        timeout = timeout or (self.element_timeout * 1000)
        
        try:
            self.state.page.wait_for_selector(
                selector,
                state=state,
                timeout=timeout
            )
            return True
        except PlaywrightTimeoutError:
            logger.debug(f"等待元素超时: {selector}")
            return False
    
    def click(self, selector: str, timeout: int = 10000) -> bool:
        """
        点击元素
        
        Args:
            selector: CSS 选择器
            timeout: 超时时间
            
        Returns:
            是否点击成功
        """
        if not self.state.page:
            return False
        
        try:
            # 【新增】点击前随机延迟
            self.random_delay()
            
            self.state.page.click(selector, timeout=timeout)
            self.state.last_heartbeat = time.time()
            return True
        except Exception as e:
            logger.error(f"点击失败 [{selector}]: {e}")
            return False
    
    def fill(self, selector: str, value: str, timeout: int = 10000) -> bool:
        """
        填写输入框
        
        Args:
            selector: CSS 选择器
            value: 填写内容
            timeout: 超时时间
            
        Returns:
            是否填写成功
        """
        if not self.state.page:
            return False
        
        try:
            # 【新增】填写前随机延迟
            self.random_delay()
            
            self.state.page.fill(selector, value, timeout=timeout)
            return True
        except Exception as e:
            logger.error(f"填写失败 [{selector}]: {e}")
            return False
    
    def get_text(self, selector: str, timeout: int = 10000) -> Optional[str]:
        """
        获取元素文本内容
        
        Args:
            selector: CSS 选择器
            timeout: 超时时间
            
        Returns:
            文本内容
        """
        if not self.state.page:
            return None
        
        try:
            element = self.state.page.wait_for_selector(selector, timeout=timeout)
            return element.text_content() if element else None
        except Exception as e:
            logger.debug(f"获取文本失败 [{selector}]: {e}")
            return None
    
    def get_attribute(self, selector: str, attr: str, timeout: int = 10000) -> Optional[str]:
        """
        获取元素属性
        
        Args:
            selector: CSS 选择器
            attr: 属性名
            timeout: 超时时间
            
        Returns:
            属性值
        """
        if not self.state.page:
            return None
        
        try:
            element = self.state.page.wait_for_selector(selector, timeout=timeout)
            return element.get_attribute(attr) if element else None
        except Exception:
            return None
    
    def save_screenshot(self, name: str = None, full_page: bool = False) -> Optional[str]:
        """
        保存页面截图
        
        Args:
            name: 文件名（不含扩展名）
            full_page: 是否截取整个页面
            
        Returns:
            截图文件路径
        """
        if not self.state.page:
            return None
        
        if name is None:
            name = f"screenshot_{int(time.time())}"
        
        filepath = self.screenshot_dir / f"{name}.png"
        
        try:
            self.state.page.screenshot(path=str(filepath), full_page=full_page)
            logger.debug(f"截图已保存: {filepath}")
            return str(filepath)
        except Exception as e:
            logger.error(f"截图保存失败: {e}")
            return None
    
    def save_cookies(self, shop_name: str) -> bool:
        """
        【新增】保存 Cookie 到文件
        
        Args:
            shop_name: 店铺名称
            
        Returns:
            是否保存成功
        """
        if not self.session_enabled:
            return False
        
        if not self.state.context:
            return False
        
        try:
            cookies = self.state.context.cookies()
            
            # 保存到文件
            cookie_file = self.session_storage_path / f"{shop_name}_cookies.json"
            
            with open(cookie_file, 'w', encoding='utf-8') as f:
                json.dump({
                    'shop_name': shop_name,
                    'saved_at': format_time(),
                    'cookies': cookies
                }, f, ensure_ascii=False, indent=2)
            
            logger.info(f"Cookie 已保存: {cookie_file}")
            return True
            
        except Exception as e:
            logger.error(f"保存 Cookie 失败: {e}")
            return False
    
    def load_cookies(self, shop_name: str) -> bool:
        """
        【新增】从文件加载 Cookie
        
        Args:
            shop_name: 店铺名称
            
        Returns:
            是否加载成功
        """
        if not self.session_enabled:
            return False
        
        if not self.state.context:
            return False
        
        try:
            cookie_file = self.session_storage_path / f"{shop_name}_cookies.json"
            
            if not cookie_file.exists():
                logger.debug(f"Cookie 文件不存在: {cookie_file}")
                return False
            
            with open(cookie_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            cookies = data.get('cookies', [])
            saved_at = data.get('saved_at', '')
            
            if not cookies:
                logger.debug("Cookie 文件为空")
                return False
            
            # 检查 Cookie 是否过期
            max_age_days = self.config.get('session.cookie_max_age_days', 7)
            if saved_at:
                from datetime import datetime, timedelta
                try:
                    saved_time = datetime.strptime(saved_at, '%Y-%m-%d %H:%M:%S')
                    if datetime.now() - saved_time > timedelta(days=max_age_days):
                        logger.info(f"Cookie 已过期（超过 {max_age_days} 天）")
                        return False
                except:
                    pass
            
            # 加载 Cookie
            self.state.context.add_cookies(cookies)
            logger.info(f"已加载 {len(cookies)} 个 Cookie")
            return True
            
        except Exception as e:
            logger.warning(f"加载 Cookie 失败: {e}")
            return False
    
    def check_alive(self) -> bool:
        """
        检查浏览器是否存活
        
        Returns:
            是否存活
        """
        if not self.state.is_alive:
            return False
        
        try:
            # 尝试执行简单脚本检测页面是否响应
            if self.state.page:
                self.state.page.evaluate("() => document.readyState")
                self.state.last_heartbeat = time.time()
                return True
        except Exception:
            pass
        
        self.state.failure_count += 1
        logger.warning(f"浏览器心跳检测失败 ({self.state.failure_count}次)")
        return False
    
    def restart(self) -> bool:
        """
        重启浏览器
        
        Returns:
            是否重启成功
        """
        logger.info("正在重启浏览器...")
        
        self.close()
        time.sleep(2)
        
        return self.start()
    
    def close(self) -> None:
        """关闭浏览器"""
        try:
            if self.state.page and not self.state.page.is_closed():
                self.state.page.close()
            
            if self.state.context:
                self.state.context.close()
            
            if self.browser:
                self.browser.close()
            
            if self.playwright:
                self.playwright.stop()
            
            self.state.is_alive = False
            logger.info("浏览器已关闭")
            
        except Exception as e:
            logger.error(f"关闭浏览器时出错: {e}")
        finally:
            # 重置状态
            self.playwright = None
            self.browser = None
            self.state = BrowserState()
    
    def _handle_error(self, error: Exception) -> None:
        """处理错误"""
        self.state.failure_count += 1
        
        if self.debug:
            self.save_screenshot(f"error_{int(time.time())}")
    
    def _get_url_hash(self, url: str) -> str:
        """获取 URL 的短哈希"""
        import hashlib
        return hashlib.md5(url.encode()).hexdigest()[:8]
    
    def __enter__(self):
        """上下文管理器入口"""
        self.start()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口"""
        self.close()
        return False
    
    def __del__(self):
        """析构函数"""
        self.close()
