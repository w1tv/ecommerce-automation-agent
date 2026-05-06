"""
告警模块
支持 webhook（钉钉/企业微信/飞书）告警

【功能说明】
- 关键事件触发告警：登录失败、连续异常、心跳超时、退款处理异常、验证码检测等
- 支持多种 webhook 渠道
- 异步发送，不阻塞主流程
"""
import os
import time
import json
import queue
import threading
from typing import Dict, Any, Optional
from datetime import datetime
from enum import Enum

from loguru import logger

from utils.helpers import format_time


class AlertEvent(Enum):
    """告警事件类型"""
    # 登录相关
    LOGIN_FAILED = "login_failed"
    LOGIN_SUCCESS = "login_success"
    
    # 验证码相关
    CAPTCHA_DETECTED = "captcha_detected"
    
    # 任务执行相关
    ORDER_FETCH_ERROR = "order_fetch_error"
    REFUND_ERROR = "refund_error"
    
    # 系统相关
    HEARTBEAT_TIMEOUT = "heartbeat_timeout"
    CONSECUTIVE_ERRORS = "consecutive_errors"
    BROWSER_CRASH = "browser_crash"
    
    # 新订单通知
    NEW_ORDER = "new_order"


class Alerter:
    """
    告警器
    支持多种 webhook 渠道发送告警消息
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        初始化告警器
        
        Args:
            config: 配置字典
        """
        self.config = config.get('alerter', {})
        self.enabled = self.config.get('enabled', True)
        
        # 事件配置
        self.events_config = self.config.get('events', {})
        
        # 渠道配置
        self.channels = self.config.get('channels', {})
        
        # 消息队列（异步发送）
        self.message_queue: queue.Queue = queue.Queue()
        self.worker_thread: Optional[threading.Thread] = None
        self.running = False
        
        # 统计
        self.stats = {
            'sent': 0,
            'failed': 0,
            'skipped': 0
        }
        
        # 启动异步工作线程
        if self.enabled:
            self._start_worker()
    
    def _start_worker(self) -> None:
        """启动异步消息发送工作线程"""
        if self.worker_thread and self.worker_thread.is_alive():
            return
        
        self.running = True
        self.worker_thread = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name="AlerterWorker"
        )
        self.worker_thread.start()
        logger.info("告警工作线程已启动")
    
    def _worker_loop(self) -> None:
        """工作线程主循环"""
        while self.running:
            try:
                # 获取消息，超时1秒
                event_type, data = self.message_queue.get(timeout=1)
                
                # 检查是否应该发送
                if not self._should_send(event_type):
                    self.stats['skipped'] += 1
                    continue
                
                # 发送消息
                success = self._send_message(event_type, data)
                
                if success:
                    self.stats['sent'] += 1
                else:
                    self.stats['failed'] += 1
                    
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"告警工作线程异常: {e}")
    
    def _should_send(self, event_type: str) -> bool:
        """检查是否应该发送此类型的告警"""
        event_map = {
            AlertEvent.LOGIN_FAILED: 'login_failed',
            AlertEvent.LOGIN_SUCCESS: 'login_success',
            AlertEvent.CAPTCHA_DETECTED: 'captcha_detected',
            AlertEvent.ORDER_FETCH_ERROR: 'order_fetch_error',
            AlertEvent.REFUND_ERROR: 'refund_error',
            AlertEvent.HEARTBEAT_TIMEOUT: 'heartbeat_timeout',
            AlertEvent.CONSECUTIVE_ERRORS: 'consecutive_errors',
            AlertEvent.BROWSER_CRASH: 'consecutive_errors',  # 复用配置
            AlertEvent.NEW_ORDER: 'new_order'
        }
        
        config_key = event_map.get(event_type, event_type)
        return self.events_config.get(config_key, True)
    
    def send(self, event_type: str, data: Dict[str, Any]) -> None:
        """
        发送告警（异步）
        
        Args:
            event_type: 事件类型
            data: 事件数据
        """
        if not self.enabled:
            return
        
        try:
            self.message_queue.put((event_type, data), block=False)
        except queue.Full:
            logger.warning("告警队列已满，跳过告警")
    
    def send_sync(self, event_type: str, data: Dict[str, Any]) -> bool:
        """
        同步发送告警
        
        Args:
            event_type: 事件类型
            data: 事件数据
            
        Returns:
            是否发送成功
        """
        if not self.enabled:
            return False
        
        if not self._should_send(event_type):
            self.stats['skipped'] += 1
            return True
        
        return self._send_message(event_type, data)
    
    def _send_message(self, event_type: str, data: Dict[str, Any]) -> bool:
        """
        发送消息到所有配置的渠道
        
        Args:
            event_type: 事件类型
            data: 事件数据
            
        Returns:
            是否至少有一个渠道发送成功
        """
        # 构建消息
        message = self._build_message(event_type, data)
        
        success = False
        
        # 发送到钉钉
        if self.channels.get('dingtalk', {}).get('enabled'):
            if self._send_dingtalk(message):
                success = True
        
        # 发送到企业微信
        if self.channels.get('wecom', {}).get('enabled'):
            if self._send_wecom(message):
                success = True
        
        # 发送到飞书
        if self.channels.get('feishu', {}).get('enabled'):
            if self._send_feishu(message):
                success = True
        
        if not success and self.enabled:
            logger.warning(f"告警发送失败（事件: {event_type}）")
        
        return success
    
    def _build_message(self, event_type: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """构建告警消息"""
        # 事件类型中文名
        event_names = {
            'login_failed': '登录失败',
            'login_success': '登录成功',
            'captcha_detected': '验证码检测',
            'order_fetch_error': '订单抓取异常',
            'refund_error': '退款处理异常',
            'heartbeat_timeout': '心跳超时',
            'consecutive_errors': '连续异常',
            'browser_crash': '浏览器崩溃',
            'new_order': '新订单'
        }
        
        event_name = event_names.get(event_type, event_type)
        
        # 获取当前时间
        timestamp = format_time()
        
        # 构建消息体
        message = {
            'msgtype': 'markdown',
            'event': event_type,
            'event_name': event_name,
            'timestamp': timestamp,
            'data': data
        }
        
        return message
    
    def _send_dingtalk(self, message: Dict[str, Any]) -> bool:
        """
        发送到钉钉 webhook
        
        Args:
            message: 消息体
            
        Returns:
            是否发送成功
        """
        try:
            import requests
            
            webhook_url = self.channels.get('dingtalk', {}).get('webhook_url', '')
            
            if not webhook_url:
                return False
            
            # 构建钉钉消息格式
            content = f"""## 🤖 电商自动化 Agent 告警

**事件**: {message['event_name']}
**时间**: {message['timestamp']}

**详情**:
```
{json.dumps(message['data'], ensure_ascii=False, indent=2)}
```
"""
            
            payload = {
                'msgtype': 'markdown',
                'markdown': {
                    'title': message['event_name'],
                    'text': content
                }
            }
            
            response = requests.post(
                webhook_url,
                json=payload,
                timeout=10
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get('errcode') == 0:
                    logger.debug(f"钉钉告警发送成功")
                    return True
                else:
                    logger.warning(f"钉钉告警发送失败: {result.get('errmsg')}")
            
            return False
            
        except ImportError:
            logger.warning("requests 库未安装，无法发送钉钉告警")
            return False
        except Exception as e:
            logger.error(f"钉钉告警发送异常: {e}")
            return False
    
    def _send_wecom(self, message: Dict[str, Any]) -> bool:
        """
        发送到企业微信 webhook
        
        Args:
            message: 消息体
            
        Returns:
            是否发送成功
        """
        try:
            import requests
            
            webhook_url = self.channels.get('wecom', {}).get('webhook_url', '')
            
            if not webhook_url:
                return False
            
            # 构建企业微信消息格式
            content = f"""🤖 电商自动化 Agent 告警

事件: {message['event_name']}
时间: {message['timestamp']}

详情:
{json.dumps(message['data'], ensure_ascii=False, indent=2)}
"""
            
            payload = {
                'msgtype': 'markdown',
                'markdown': {
                    'content': content
                }
            }
            
            response = requests.post(
                webhook_url,
                json=payload,
                timeout=10
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get('errcode') == 0:
                    logger.debug(f"企业微信告警发送成功")
                    return True
                else:
                    logger.warning(f"企业微信告警发送失败: {result.get('errmsg')}")
            
            return False
            
        except ImportError:
            logger.warning("requests 库未安装，无法发送企业微信告警")
            return False
        except Exception as e:
            logger.error(f"企业微信告警发送异常: {e}")
            return False
    
    def _send_feishu(self, message: Dict[str, Any]) -> bool:
        """
        发送到飞书 webhook
        
        Args:
            message: 消息体
            
        Returns:
            是否发送成功
        """
        try:
            import requests
            
            webhook_url = self.channels.get('feishu', {}).get('webhook_url', '')
            
            if not webhook_url:
                return False
            
            # 构建飞书消息格式
            content = f"""## 🤖 电商自动化 Agent 告警

**事件**: {message['event_name']}
**时间**: {message['timestamp']}

**详情**:
```
{json.dumps(message['data'], ensure_ascii=False, indent=2)}
```
"""
            
            payload = {
                'msg_type': 'interactive',
                'card': {
                    'header': {
                        'title': {
                            'tag': 'plain_text',
                            'content': f"🤖 {message['event_name']}"
                        },
                        'template': 'red'
                    },
                    'elements': [
                        {
                            'tag': 'markdown',
                            'content': content
                        }
                    ]
                }
            }
            
            response = requests.post(
                webhook_url,
                json=payload,
                timeout=10
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get('code') == 0 or result.get('StatusCode') == 0:
                    logger.debug(f"飞书告警发送成功")
                    return True
                else:
                    logger.warning(f"飞书告警发送失败: {result}")
            
            return False
            
        except ImportError:
            logger.warning("requests 库未安装，无法发送飞书告警")
            return False
        except Exception as e:
            logger.error(f"飞书告警发送异常: {e}")
            return False
    
    def stop(self) -> None:
        """停止告警器"""
        self.running = False
        
        if self.worker_thread:
            self.worker_thread.join(timeout=5)
        
        logger.info("告警器已停止")
    
    def get_stats(self) -> Dict[str, int]:
        """获取统计信息"""
        return self.stats.copy()
