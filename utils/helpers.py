"""
通用辅助函数
提供日志、时间、文件、重试等工具函数
"""
import os
import re
import time
import hashlib
import functools
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar, Union

from loguru import logger

# 类型变量，用于泛型函数
T = TypeVar('T')


def format_time(timestamp: Optional[float] = None, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """
    格式化时间戳为字符串
    
    Args:
        timestamp: Unix 时间戳，默认当前时间
        fmt: 时间格式
        
    Returns:
        格式化后的时间字符串
    """
    if timestamp is None:
        timestamp = time.time()
    return datetime.fromtimestamp(timestamp).strftime(fmt)


def ensure_dir(path: Union[str, Path], is_file: bool = False) -> Path:
    """
    确保目录存在，如果不存在则创建
    
    Args:
        path: 目录或文件路径
        is_file: 是否为文件路径
        
    Returns:
        Path 对象
    """
    path = Path(path)
    
    if is_file:
        path.parent.mkdir(parents=True, exist_ok=True)
    else:
        path.mkdir(parents=True, exist_ok=True)
    
    return path


def safe_get(data: dict, *keys, default: Any = None) -> Any:
    """
    安全地从嵌套字典中获取值
    
    Args:
        data: 字典数据
        *keys: 嵌套的键路径
        default: 默认值
        
    Returns:
        获取到的值或默认值
    """
    result = data
    for key in keys:
        if isinstance(result, dict):
            result = result.get(key)
        elif isinstance(result, list) and isinstance(key, int):
            if -len(result) <= key < len(result):
                result = result[key]
            else:
                return default
        else:
            return default
        
        if result is None:
            return default
    
    return result


def retry_on_exception(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple = (Exception,),
    on_retry: Optional[Callable[[Exception, int], None]] = None
) -> Callable:
    """
    装饰器：自动重试失败的函数
    
    Args:
        max_attempts: 最大尝试次数
        delay: 初始延迟（秒）
        backoff: 延迟倍增因子
        exceptions: 需要重试的异常类型元组
        on_retry: 重试时的回调函数
        
    Returns:
        装饰器函数
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> T:
            last_exception = None
            current_delay = delay
            
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    
                    if attempt == max_attempts:
                        logger.error(f"{func.__name__} 达到最大重试次数 ({max_attempts})")
                        raise
                    
                    logger.warning(
                        f"{func.__name__} 第 {attempt} 次尝试失败: {e}，"
                        f"{current_delay:.1f}秒后重试..."
                    )
                    
                    if on_retry:
                        on_retry(e, attempt)
                    
                    time.sleep(current_delay)
                    current_delay *= backoff
            
            if last_exception:
                raise last_exception
        
        return wrapper
    return decorator


def sanitize_filename(filename: str, replacement: str = "_") -> str:
    """
    清理文件名，移除非法字符
    
    Args:
        filename: 原始文件名
        replacement: 非法字符的替换字符
        
    Returns:
        清理后的文件名
    """
    # Windows 非法字符
    illegal_chars = r'[<>:"/\\|?*]'
    cleaned = re.sub(illegal_chars, replacement, filename)
    
    # 移除前后空白和点
    cleaned = cleaned.strip('. ')
    
    # 确保不为空
    if not cleaned:
        cleaned = "unnamed"
    
    return cleaned


def calculate_hash(data: Union[str, bytes], algorithm: str = "md5") -> str:
    """
    计算数据的哈希值
    
    Args:
        data: 待哈希的数据
        algorithm: 哈希算法 (md5, sha1, sha256)
        
    Returns:
        十六进制哈希字符串
    """
    if isinstance(data, str):
        data = data.encode('utf-8')
    
    if algorithm == "md5":
        return hashlib.md5(data).hexdigest()
    elif algorithm == "sha1":
        return hashlib.sha1(data).hexdigest()
    elif algorithm == "sha256":
        return hashlib.sha256(data).hexdigest()
    else:
        raise ValueError(f"不支持的哈希算法: {algorithm}")


def get_timestamp_filename(prefix: str = "", suffix: str = "", ext: str = "") -> str:
    """
    生成带时间戳的文件名
    
    Args:
        prefix: 文件名前缀
        suffix: 文件名后缀
        ext: 文件扩展名
        
    Returns:
        文件名字符串
    """
    now = datetime.now()
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    
    parts = [prefix, timestamp, suffix]
    filename = "_".join(p for p in parts if p)
    
    if ext:
        if not ext.startswith('.'):
            ext = '.' + ext
        filename += ext
    
    return filename


def format_size(size_bytes: int) -> str:
    """
    格式化字节大小为人类可读格式
    
    Args:
        size_bytes: 字节数
        
    Returns:
        格式化后的大小字符串
    """
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} PB"


def parse_price(price_str: str) -> float:
    """
    解析价格字符串为浮点数
    
    Args:
        price_str: 价格字符串，如 "¥99.00" 或 "99.00元"
        
    Returns:
        价格浮点数
    """
    if isinstance(price_str, (int, float)):
        return float(price_str)
    
    # 移除非数字字符
    cleaned = re.sub(r'[^\d.]', '', price_str)
    
    try:
        return float(cleaned)
    except ValueError:
        logger.warning(f"无法解析价格: {price_str}，返回 0.0")
        return 0.0


def extract_numbers(text: str) -> list:
    """
    从文本中提取所有数字
    
    Args:
        text: 输入文本
        
    Returns:
        数字列表
    """
    return [float(n) for n in re.findall(r'-?\d+\.?\d*', text)]


def truncate_text(text: str, max_length: int = 100, suffix: str = "...") -> str:
    """
    截断过长的文本
    
    Args:
        text: 输入文本
        max_length: 最大长度
        suffix: 截断后缀
        
    Returns:
        截断后的文本
    """
    if len(text) <= max_length:
        return text
    return text[:max_length - len(suffix)] + suffix


class Timer:
    """简单的计时器上下文管理器"""
    
    def __init__(self, name: str = "操作", logger_func=None):
        self.name = name
        self.logger_func = logger_func or logger.info
        self.start_time = None
        self.elapsed = None
    
    def __enter__(self):
        self.start_time = time.time()
        self.logger_func(f"{self.name} 开始...")
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.elapsed = time.time() - self.start_time
        if exc_type is None:
            self.logger_func(f"{self.name} 完成，耗时: {self.elapsed:.2f}秒")
        else:
            self.logger_func(f"{self.name} 失败，耗时: {self.elapsed:.2f}秒")
        return False


def is_within_time_window(
    start_hour: int = 9,
    end_hour: int = 22,
    timezone: str = "Asia/Shanghai"
) -> bool:
    """
    检查当前时间是否在指定时间窗口内
    
    Args:
        start_hour: 开始小时
        end_hour: 结束小时
        timezone: 时区
        
    Returns:
        是否在时间窗口内
    """
    try:
        import pytz
        tz = pytz.timezone(timezone)
        now = datetime.now(tz)
        current_hour = now.hour
        return start_hour <= current_hour <= end_hour
    except ImportError:
        # 如果没有 pytz，使用本地时间
        current_hour = datetime.now().hour
        return start_hour <= current_hour <= end_hour


def wait_until(
    condition: Callable[[], bool],
    timeout: float = 30,
    poll_interval: float = 0.5,
    error_message: str = "条件未在超时时间内满足"
) -> bool:
    """
    等待条件满足
    
    Args:
        condition: 条件函数
        timeout: 超时时间（秒）
        poll_interval: 轮询间隔（秒）
        error_message: 超时时显示的消息
        
    Returns:
        是否成功满足条件
        
    Raises:
        TimeoutError: 超时时抛出
    """
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        try:
            if condition():
                return True
        except Exception:
            pass
        
        time.sleep(poll_interval)
    
    raise TimeoutError(error_message)
