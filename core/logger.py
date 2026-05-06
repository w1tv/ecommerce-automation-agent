"""
日志模块
配置全局日志记录器，支持文件和控制台输出
"""
import os
import sys
from pathlib import Path
from typing import Optional

from loguru import logger


def setup_logger(
    level: str = "INFO",
    file_path: str = "./logs/ecommerce_agent.log",
    rotation: str = "500 MB",
    retention: str = "30 days",
    console: bool = True,
    format_string: Optional[str] = None
) -> None:
    """
    配置全局日志记录器
    
    Args:
        level: 日志级别 (DEBUG, INFO, WARNING, ERROR)
        file_path: 日志文件路径
        rotation: 日志轮转大小
        retention: 日志保留时间
        console: 是否输出到控制台
        format_string: 自定义格式字符串
    """
    # 移除默认的日志处理器
    logger.remove()
    
    # 默认格式
    if format_string is None:
        format_string = (
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        )
    
    # 添加控制台处理器
    if console:
        logger.add(
            sys.stdout,
            level=level,
            format=format_string,
            colorize=True,
            backtrace=True,
            diagnose=True
        )
    
    # 确保日志目录存在
    if file_path:
        log_dir = Path(file_path).parent
        log_dir.mkdir(parents=True, exist_ok=True)
        
        # 添加文件处理器
        logger.add(
            file_path,
            level=level,
            format=format_string,
            rotation=rotation,
            retention=retention,
            compression="zip",
            backtrace=True,
            diagnose=True
        )
    
    # 配置错误日志单独记录
    error_log_path = str(file_path).replace(".log", "_error.log") if file_path else None
    if error_log_path:
        logger.add(
            error_log_path,
            level="ERROR",
            format=format_string,
            rotation="100 MB",
            retention="90 days",
            filter=lambda record: record["level"].name == "ERROR"
        )


def get_logger(name: str = None) -> logger:
    """
    获取日志记录器
    
    Args:
        name: 模块名称（可选）
        
    Returns:
        Loguru logger 实例
    """
    if name:
        return logger.bind(name=name)
    return logger


class LogContext:
    """日志上下文管理器，用于添加临时上下文信息"""
    
    def __init__(self, **context):
        self.context = context
        self.original_bindings = {}
    
    def __enter__(self):
        # 保存原始绑定
        for key in self.context:
            try:
                self.original_bindings[key] = logger.bind(**{key: None})._record.extra.get(key)
            except Exception:
                self.original_bindings[key] = None
        
        # 添加新的上下文绑定
        logger.configure(extra=self.context)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        # 恢复原始绑定
        logger.configure(extra=self.original_bindings)
        return False


class OperationLogger:
    """操作日志记录器，记录操作详情到专用文件"""
    
    def __init__(self, shop_name: str, operation: str):
        self.shop_name = shop_name
        self.operation = operation
        self.start_time = None
        self.details = []
    
    def log(self, message: str, level: str = "INFO"):
        """记录操作日志"""
        timestamp = self._format_time()
        log_entry = f"[{timestamp}] [{level}] {message}"
        self.details.append(log_entry)
        
        if level == "DEBUG":
            logger.debug(f"[{self.shop_name}] {self.operation}: {message}")
        elif level == "INFO":
            logger.info(f"[{self.shop_name}] {self.operation}: {message}")
        elif level == "WARNING":
            logger.warning(f"[{self.shop_name}] {self.operation}: {message}")
        elif level == "ERROR":
            logger.error(f"[{self.shop_name}] {self.operation}: {message}")
    
    def save(self):
        """保存操作日志到文件"""
        from utils.helpers import ensure_dir, get_timestamp_filename
        
        log_dir = ensure_dir(f"./logs/operations/{self.shop_name}")
        filename = get_timestamp_filename(
            prefix=f"{self.operation}",
            ext=".log"
        )
        filepath = log_dir / filename
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(f"操作: {self.operation}\n")
            f.write(f"店铺: {self.shop_name}\n")
            f.write(f"开始时间: {self.start_time}\n")
            f.write(f"结束时间: {self._format_time()}\n")
            f.write("-" * 50 + "\n")
            f.write("\n".join(self.details))
        
        return filepath
    
    def _format_time(self):
        from datetime import datetime
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    def __enter__(self):
        self.start_time = self._format_time()
        self.log("操作开始", "INFO")
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.log(f"操作失败: {exc_val}", "ERROR")
        else:
            self.log("操作完成", "INFO")
        self.save()
        return False


# 便捷函数
def log_operation(shop_name: str, operation: str):
    """创建操作日志记录器"""
    return OperationLogger(shop_name, operation)
