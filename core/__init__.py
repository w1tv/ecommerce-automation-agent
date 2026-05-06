"""
核心模块

【模块列表】
- logger: 日志系统
- browser_manager: 浏览器生命周期管理
- exception_handler: 异常处理与恢复
- refund_handler: 退货退款处理
- order_fetcher: 订单抓取
- label_printer: 快递单打印
- alerter: 告警通知（新增）
"""
from .logger import setup_logger, get_logger
from .browser_manager import BrowserManager
from .exception_handler import ExceptionHandler, RecoveryManager, ActionWithRecovery, ErrorType
from .refund_handler import RefundHandler
from .order_fetcher import OrderFetcher
from .label_printer import LabelPrinter
from .alerter import Alerter, AlertEvent

__all__ = [
    # Logger
    'setup_logger',
    'get_logger',
    
    # Browser
    'BrowserManager',
    
    # Exception
    'ExceptionHandler',
    'RecoveryManager',
    'ActionWithRecovery',
    'ErrorType',
    
    # Business
    'RefundHandler',
    'OrderFetcher',
    'LabelPrinter',
    
    # Alerter
    'Alerter',
    'AlertEvent'
]
