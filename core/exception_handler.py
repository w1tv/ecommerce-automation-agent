"""
异常处理与自动恢复模块
提供统一的异常处理、重试机制和自动恢复功能
"""
import time
import traceback
from typing import Optional, Callable, Any, Dict
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime

from loguru import logger

from utils.config_loader import get_config
from utils.helpers import format_time, retry_on_exception


class ErrorType(Enum):
    """错误类型枚举"""
    NETWORK_ERROR = "network_error"           # 网络错误
    PAGE_CRASH = "page_crash"                  # 页面崩溃
    SESSION_EXPIRED = "session_expired"        # 会话过期
    ELEMENT_NOT_FOUND = "element_not_found"    # 元素未找到
    TIMEOUT = "timeout"                        # 超时
    LOGIN_FAILED = "login_failed"              # 登录失败
    API_ERROR = "api_error"                    # API 错误
    UNKNOWN = "unknown"                        # 未知错误


@dataclass
class ErrorInfo:
    """错误信息"""
    error_type: ErrorType
    message: str
    timestamp: float = field(default_factory=time.time)
    details: Optional[str] = None
    recoverable: bool = True
    context: Dict[str, Any] = field(default_factory=dict)
    
    def __str__(self):
        time_str = format_time(self.timestamp)
        return f"[{time_str}] {self.error_type.value}: {self.message}"


class ExceptionHandler:
    """
    异常处理器
    统一处理各种异常，提供分类、重试建议等功能
    """
    
    def __init__(self):
        self.config = get_config()
        self.error_history: list[ErrorInfo] = []
        self.max_history = 100
    
    def handle(
        self,
        error: Exception,
        context: Optional[Dict[str, Any]] = None
    ) -> ErrorInfo:
        """
        处理异常并分类
        
        Args:
            error: 异常对象
            context: 上下文信息
            
        Returns:
            ErrorInfo 对象
        """
        error_info = self._classify_error(error, context)
        self._record_error(error_info)
        
        # 记录日志
        log_func = logger.error if error_info.recoverable else logger.critical
        log_func(f"异常: {error_info.message}")
        
        if self.config.get('global.debug') and error_info.details:
            logger.debug(error_info.details)
        
        return error_info
    
    def _classify_error(self, error: Exception, context: Optional[Dict]) -> ErrorInfo:
        """
        分类错误类型
        
        【修复说明】
        1. session/cookie 过期应该归类为 SESSION_EXPIRED，是可恢复的（可以重新登录）
        2. 只有真正的登录失败（如账号密码错误）才是不可恢复的 LOGIN_FAILED
        """
        error_msg = str(error).lower()
        error_type = ErrorType.UNKNOWN
        recoverable = True
        
        # 【修复】先检查 session/cookie 相关错误（可恢复）
        if any(keyword in error_msg for keyword in ['session', 'cookie']):
            if any(keyword in error_msg for keyword in ['expired', 'invalid', '过期', '失效', '无效']):
                # session/cookie 过期是可恢复的
                error_type = ErrorType.SESSION_EXPIRED
                recoverable = True
            else:
                # 单独的 session 关键词，可能是临时性问题
                error_type = ErrorType.SESSION_EXPIRED
                recoverable = True
        
        # 根据错误消息分类
        elif any(keyword in error_msg for keyword in ['network', 'net::', 'connection', '断开']):
            error_type = ErrorType.NETWORK_ERROR
        elif any(keyword in error_msg for keyword in ['crash', 'crashed', '崩溃']):
            error_type = ErrorType.PAGE_CRASH
        elif any(keyword in error_msg for keyword in ['timeout', '超时', 'timed out']):
            error_type = ErrorType.TIMEOUT
        elif any(keyword in error_msg for keyword in ['element', 'selector', 'not found', '未找到']):
            error_type = ErrorType.ELEMENT_NOT_FOUND
        # 【修复】只有明确的登录失败才是不可恢复的
        elif any(keyword in error_msg for keyword in ['login failed', '登录失败', 'password incorrect', '密码错误', '账号不存在']):
            error_type = ErrorType.LOGIN_FAILED
            recoverable = False  # 账号密码错误，不可恢复
        elif any(keyword in error_msg for keyword in ['api', 'http', 'status code']):
            error_type = ErrorType.API_ERROR
        
        return ErrorInfo(
            error_type=error_type,
            message=str(error),
            details=traceback.format_exc(),
            recoverable=recoverable,
            context=context or {}
        )
    
    def _record_error(self, error_info: ErrorInfo) -> None:
        """记录错误到历史"""
        self.error_history.append(error_info)
        
        # 保持历史记录数量限制
        if len(self.error_history) > self.max_history:
            self.error_history = self.error_history[-self.max_history:]
    
    def get_recent_errors(self, count: int = 10, error_type: Optional[ErrorType] = None) -> list:
        """
        获取最近的错误记录
        
        Args:
            count: 返回数量
            error_type: 筛选错误类型
            
        Returns:
            错误列表
        """
        errors = self.error_history
        
        if error_type:
            errors = [e for e in errors if e.error_type == error_type]
        
        return errors[-count:]
    
    def should_retry(self, error_info: ErrorInfo, retry_count: int) -> bool:
        """
        判断是否应该重试
        
        Args:
            error_info: 错误信息
            retry_count: 当前重试次数
            
        Returns:
            是否应该重试
        """
        if not error_info.recoverable:
            return False
        
        max_retry = self.config.get('global.max_retry', 3)
        return retry_count < max_retry


class RecoveryManager:
    """
    恢复管理器
    负责检测异常并执行相应的恢复策略
    """
    
    def __init__(self, browser_manager):
        """
        初始化恢复管理器
        
        Args:
            browser_manager: BrowserManager 实例
        """
        self.browser_manager = browser_manager
        self.config = get_config()
        self.exception_handler = ExceptionHandler()
        
        # 恢复策略配置
        self.network_retry = self.config.get('recovery.network_retry', 5)
        self.page_crash_retry = self.config.get('recovery.page_crash_retry', 3)
        
        # 状态追踪
        self.consecutive_failures = 0
        self.last_recovery_time = 0
    
    def check_and_recover(self, error: Optional[Exception] = None) -> bool:
        """
        检测问题并执行恢复
        
        Args:
            error: 可选的异常对象
            
        Returns:
            恢复是否成功
        """
        error_info = None
        
        if error:
            error_info = self.exception_handler.handle(error)
        
        # 检查浏览器是否存活
        if not self.browser_manager.check_alive():
            logger.warning("浏览器心跳检测失败，尝试恢复...")
            return self._recover_browser()
        
        # 根据错误类型执行恢复
        if error_info:
            return self._recover_by_error_type(error_info)
        
        return True
    
    def _recover_by_error_type(self, error_info: ErrorInfo) -> bool:
        """根据错误类型执行恢复"""
        recovery_funcs = {
            ErrorType.NETWORK_ERROR: self._recover_network,
            ErrorType.PAGE_CRASH: self._recover_page_crash,
            ErrorType.SESSION_EXPIRED: self._recover_session,
            ErrorType.TIMEOUT: self._recover_timeout,
            ErrorType.ELEMENT_NOT_FOUND: self._recover_element,
            ErrorType.LOGIN_FAILED: self._recover_login,
        }
        
        func = recovery_funcs.get(error_info.error_type)
        if func:
            return func()
        
        return True
    
    def _recover_browser(self) -> bool:
        """恢复浏览器会话"""
        try:
            logger.info("正在恢复浏览器会话...")
            
            # 尝试刷新页面
            if self.browser_manager.state.page:
                try:
                    self.browser_manager.state.page.reload()
                    time.sleep(2)
                    
                    if self.browser_manager.check_alive():
                        logger.info("浏览器会话恢复成功")
                        return True
                except Exception:
                    pass
            
            # 刷新失败，重启浏览器
            logger.warning("刷新失败，尝试重启浏览器...")
            return self.browser_manager.restart()
            
        except Exception as e:
            logger.error(f"浏览器恢复失败: {e}")
            return False
    
    def _recover_network(self) -> bool:
        """网络错误恢复"""
        for attempt in range(self.network_retry):
            logger.info(f"网络恢复尝试 ({attempt + 1}/{self.network_retry})...")
            
            try:
                time.sleep(5 * (attempt + 1))  # 递增等待时间
                
                # 尝试访问简单页面测试网络
                if self.browser_manager.navigate("https://www.baidu.com"):
                    logger.info("网络恢复成功")
                    return True
                    
            except Exception as e:
                logger.warning(f"网络恢复尝试失败: {e}")
        
        logger.error("网络恢复失败")
        return False
    
    def _recover_page_crash(self) -> bool:
        """页面崩溃恢复"""
        logger.warning("检测到页面崩溃，正在恢复...")
        
        for attempt in range(self.page_crash_retry):
            try:
                logger.info(f"页面恢复尝试 ({attempt + 1}/{self.page_crash_retry})...")
                
                # 关闭可能存在的崩溃页面
                if self.browser_manager.state.page:
                    try:
                        self.browser_manager.state.page.close()
                    except Exception:
                        pass
                
                # 创建新页面
                if self.browser_manager.state.context:
                    self.browser_manager.state.page = self.browser_manager.state.context.new_page()
                    self.browser_manager.state.page.set_default_timeout(
                        self.config.get('global.page_timeout', 30000)
                    )
                    
                    if self.browser_manager.check_alive():
                        logger.info("页面恢复成功")
                        return True
                
            except Exception as e:
                logger.warning(f"页面恢复尝试失败: {e}")
        
        # 页面恢复失败，重启浏览器
        logger.warning("页面恢复失败，尝试重启浏览器...")
        return self.browser_manager.restart()
    
    def _recover_session(self) -> bool:
        """会话过期恢复"""
        logger.warning("检测到会话过期，需要重新登录")
        
        try:
            # 访问登录页面
            login_url = self.browser_manager.shop_config.get('login_url')
            if login_url:
                self.browser_manager.navigate(login_url)
                return True
            
        except Exception as e:
            logger.error(f"会话恢复失败: {e}")
        
        return False
    
    def _recover_timeout(self) -> bool:
        """超时恢复"""
        logger.warning("操作超时，尝试恢复...")
        
        try:
            # 刷新当前页面
            if self.browser_manager.state.page:
                self.browser_manager.state.page.reload()
                time.sleep(2)
                return True
        except Exception:
            pass
        
        return False
    
    def _recover_element(self) -> bool:
        """元素未找到恢复"""
        logger.warning("元素未找到，尝试刷新...")
        
        try:
            if self.browser_manager.state.page:
                self.browser_manager.state.page.reload()
                time.sleep(2)
                return True
        except Exception:
            pass
        
        return False
    
    def _recover_login(self) -> bool:
        """登录失败恢复"""
        logger.error("登录失败，需要检查凭据配置")
        # 登录失败通常需要人工介入
        return False
    
    def reset_failure_count(self) -> None:
        """重置失败计数"""
        self.consecutive_failures = 0
    
    def increment_failure(self) -> int:
        """增加失败计数"""
        self.consecutive_failures += 1
        return self.consecutive_failures


class ActionWithRecovery:
    """
    带恢复机制的动作执行器
    封装可能失败的操作，自动重试和恢复
    """
    
    def __init__(
        self,
        recovery_manager: RecoveryManager,
        action_name: str = "操作"
    ):
        self.recovery_manager = recovery_manager
        self.action_name = action_name
        self.config = get_config()
    
    def execute(
        self,
        action: Callable,
        *args,
        max_retry: Optional[int] = None,
        **kwargs
    ) -> Any:
        """
        执行动作，自动处理异常和恢复
        
        Args:
            action: 要执行的动作函数
            *args: 动作参数
            max_retry: 最大重试次数
            **kwargs: 动作关键字参数
            
        Returns:
            动作执行结果
        """
        max_retry = max_retry or self.config.get('global.max_retry', 3)
        retry_interval = self.config.get('global.retry_interval', 30)
        
        last_error = None
        
        for attempt in range(1, max_retry + 1):
            try:
                logger.debug(f"执行 {self.action_name} (尝试 {attempt}/{max_retry})...")
                result = action(*args, **kwargs)
                
                # 成功，重置失败计数
                self.recovery_manager.reset_failure_count()
                return result
                
            except Exception as e:
                last_error = e
                logger.warning(f"{self.action_name} 执行失败: {e}")
                
                # 检查是否应该重试
                error_info = self.recovery_manager.exception_handler.handle(e)
                
                if not self.recovery_manager.exception_handler.should_retry(error_info, attempt):
                    logger.error(f"{self.action_name} 不可恢复，终止重试")
                    break
                
                if attempt < max_retry:
                    # 尝试恢复
                    recovered = self.recovery_manager.check_and_recover(e)
                    
                    if recovered:
                        wait_time = retry_interval * (2 ** (attempt - 1))
                        logger.info(f"等待 {wait_time} 秒后重试...")
                        time.sleep(wait_time)
                    else:
                        break
        
        raise last_error if last_error else Exception(f"{self.action_name} 执行失败")
