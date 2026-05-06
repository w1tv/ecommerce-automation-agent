"""
订单抓取模块
定时抓取电商平台的新订单数据
"""
import time
import json
import hashlib
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path

from loguru import logger

from utils.config_loader import get_config
from utils.helpers import format_time, parse_price, ensure_dir, get_timestamp_filename
from core.browser_manager import BrowserManager
from core.exception_handler import ExceptionHandler, RecoveryManager, ActionWithRecovery


@dataclass
class OrderInfo:
    """订单信息"""
    order_id: str
    status: str
    buyer_name: str
    total_amount: float
    item_count: int
    item_names: List[str]
    address: str
    phone: str
    create_time: str
    shipping_method: str
    express_no: str = ""
    is_new: bool = True
    fetched_time: str = ""
    
    def __post_init__(self):
        if not self.fetched_time:
            self.fetched_time = format_time()


class OrderFetcher:
    """
    订单抓取器
    从电商平台抓取订单数据，支持定时任务
    """
    
    def __init__(
        self,
        browser_manager: BrowserManager,
        shop_config: Dict[str, Any]
    ):
        """
        初始化订单抓取器
        
        Args:
            browser_manager: 浏览器管理器
            shop_config: 店铺配置
        """
        self.browser = browser_manager
        self.shop_config = shop_config
        self.config = get_config()
        
        # 选择器配置
        self.selectors = shop_config.get('selectors', {}).get('order', {})
        
        # 订单抓取配置
        self.fetch_config = shop_config.get('order_fetch', {})
        
        # 异常处理
        self.exception_handler = ExceptionHandler()
        self.recovery_manager = RecoveryManager(browser_manager)
        self.action_executor = ActionWithRecovery(
            self.recovery_manager,
            action_name="订单抓取"
        )
        
        # 数据存储
        self.storage_path = ensure_dir(
            self.fetch_config.get('save_path', './data/orders')
        )
        
        # 已抓取的订单（用于去重）
        self.fetched_orders = self._load_fetched_orders()
        
        # 新订单回调
        self.on_new_order: Optional[callable] = None
    
    def fetch(
        self,
        status_filter: Optional[str] = None,
        max_orders: Optional[int] = None
    ) -> List[OrderInfo]:
        """
        抓取订单
        
        Args:
            status_filter: 订单状态筛选 (wait_pay, wait_send, wait_receive, completed)
            max_orders: 最大抓取数量
            
        Returns:
            订单信息列表
        """
        status_filter = status_filter or self.fetch_config.get('status_filter', 'wait_send')
        max_orders = max_orders or self.fetch_config.get('max_orders', 100)
        
        logger.info(f"开始抓取订单 (店铺: {self.shop_config.get('name')}, 状态: {status_filter})")
        
        orders = []
        
        try:
            # 构建订单列表URL
            order_url = self._build_order_url(status_filter)
            
            # 导航到订单列表页面
            if not self.browser.navigate(order_url):
                logger.error("无法导航到订单列表页面")
                return orders
            
            # 等待页面加载
            time.sleep(2)
            
            # 抓取多页订单
            page_count = 0
            max_pages = (max_orders // 20) + 1  # 假设每页20条
            
            while page_count < max_pages:
                page_orders = self._fetch_page_orders()
                orders.extend(page_orders)
                
                logger.info(f"第 {page_count + 1} 页: 获取 {len(page_orders)} 条订单")
                
                # 检查是否还有下一页
                if not self._go_to_next_page():
                    break
                
                page_count += 1
                time.sleep(1)  # 避免请求过快
            
            # 去重处理
            new_orders = self._filter_new_orders(orders)
            
            # 过滤数量
            if len(new_orders) > max_orders:
                new_orders = new_orders[:max_orders]
            
            # 标记为已抓取
            for order in new_orders:
                self._mark_as_fetched(order.order_id)
            
            # 保存抓取记录
            self._save_fetched_orders()
            
            # 保存订单数据
            if new_orders:
                self._save_orders(new_orders)
            
            logger.info(f"订单抓取完成: 共 {len(orders)} 条，新订单 {len(new_orders)} 条")
            
        except Exception as e:
            logger.error(f"订单抓取异常: {e}")
            self.exception_handler.handle(e, {'shop': self.shop_config.get('name')})
        
        return orders
    
    def _build_order_url(self, status_filter: str) -> str:
        """
        构建订单列表URL
        
        Args:
            status_filter: 订单状态筛选
            
        Returns:
            URL 字符串
        """
        base_url = self.selectors.get('list_url')
        
        if not base_url:
            # 根据平台构建URL
            platform = self.shop_config.get('platform', 'taobao')
            urls = {
                'taobao': 'https://trade.taobao.com/trade/itemlist/listBoughtOrders.htm',
                'tmall': 'https://trade.tmall.com/order/list.htm',
                'jd': 'https://order.jd.com/center/list.action',
                'pdd': 'https://mms.pinduoduo.com/order/list',
                'douyin': 'https://partner.douyin.com/order/list'
            }
            base_url = urls.get(platform, '')
        
        # 添加状态参数
        status_map = {
            'all': '',
            'wait_pay': '&status=wait_buyer_pay',
            'wait_send': '&status=wait_seller_send',
            'wait_receive': '&status=wait_buyer_receive',
            'completed': '&status=completed'
        }
        
        status_param = status_map.get(status_filter, '')
        return base_url + status_param
    
    def _fetch_page_orders(self) -> List[OrderInfo]:
        """
        抓取单页订单
        
        Returns:
            订单信息列表
        """
        orders = []
        
        item_selector = self.selectors.get('order_item', '.order-item')
        
        try:
            # 等待列表加载
            if not self.browser.wait_for_selector(item_selector, timeout=10000):
                logger.warning("未找到订单列表项")
                return orders
            
            # 获取订单项
            items = self.browser.state.page.query_selector_all(item_selector)
            
            for item in items:
                try:
                    order_info = self._parse_order_item(item)
                    if order_info:
                        orders.append(order_info)
                except Exception as e:
                    logger.debug(f"解析订单项失败: {e}")
                    continue
                    
        except Exception as e:
            logger.error(f"抓取页面订单失败: {e}")
        
        return orders
    
    def _parse_order_item(self, item) -> Optional[OrderInfo]:
        """
        解析单个订单
        
        Args:
            item: 订单元素
            
        Returns:
            OrderInfo 对象
        """
        try:
            # 获取订单ID
            order_id = self._get_element_text(item, 'order_id') or \
                       self._get_element_attr(item, 'order_id', 'data-id') or \
                       self._extract_order_id_from_url(item)
            
            if not order_id:
                return None
            
            # 获取订单状态
            status = self._get_element_text(item, 'status') or "未知"
            
            # 获取买家名称
            buyer_name = self._get_element_text(item, 'buyer_name') or "匿名买家"
            
            # 获取订单金额
            amount_text = self._get_element_text(item, 'total_amount') or "0"
            total_amount = parse_price(amount_text)
            
            # 获取商品信息
            item_names = self._get_item_names(item)
            item_count = len(item_names)
            
            # 获取收货地址和电话
            address = self._get_element_text(item, 'address') or ""
            phone = self._get_element_text(item, 'phone') or ""
            
            # 获取下单时间
            create_time = self._get_element_text(item, 'create_time') or format_time()
            
            # 获取配送方式
            shipping_method = self._get_element_text(item, 'shipping_method') or "快递"
            
            return OrderInfo(
                order_id=order_id,
                status=status,
                buyer_name=buyer_name,
                total_amount=total_amount,
                item_count=item_count,
                item_names=item_names,
                address=address,
                phone=phone,
                create_time=create_time,
                shipping_method=shipping_method
            )
            
        except Exception as e:
            logger.debug(f"解析订单详情失败: {e}")
            return None
    
    def _get_element_text(self, parent, key: str) -> Optional[str]:
        """获取元素文本"""
        selector = self.selectors.get(key)
        if not selector:
            return None
        
        try:
            elem = parent.query_selector(selector)
            return elem.text_content().strip() if elem else None
        except Exception:
            return None
    
    def _get_element_attr(self, parent, key: str, attr: str) -> Optional[str]:
        """获取元素属性"""
        selector = self.selectors.get(key)
        if not selector:
            return None
        
        try:
            elem = parent.query_selector(selector)
            return elem.get_attribute(attr) if elem else None
        except Exception:
            return None
    
    def _get_item_names(self, item) -> List[str]:
        """获取商品名称列表"""
        names = []
        
        # 尝试不同的选择器
        item_selectors = [
            '.item-name',
            '.product-title',
            '.item-title',
            '.goods-name'
        ]
        
        for selector in item_selectors:
            try:
                items = item.query_selector_all(selector)
                if items:
                    names = [i.text_content().strip() for i in items if i.text_content()]
                    break
            except Exception:
                continue
        
        return names
    
    def _extract_order_id_from_url(self, item) -> Optional[str]:
        """从元素中提取订单ID"""
        try:
            # 尝试获取订单详情链接
            link = item.query_selector('a')
            if link:
                href = link.get_attribute('href')
                if href:
                    # 从URL中提取订单ID
                    import re
                    match = re.search(r'(\d{10,20})', href)
                    if match:
                        return match.group(1)
        except Exception:
            pass
        return None
    
    def _go_to_next_page(self) -> bool:
        """
        跳转到下一页
        
        Returns:
            是否成功跳转
        """
        next_selectors = [
            self.selectors.get('next_page', '.pagination-next'),
            '.pagination-next:not([disabled])',
            '.next-page',
            '[class*="next"]'
        ]
        
        for selector in next_selectors:
            try:
                # 检查按钮是否存在且可用
                btn = self.browser.state.page.query_selector(selector)
                if btn and not btn.get_attribute('disabled'):
                    # 检查是否还有下一页
                    if 'disabled' in (btn.get_attribute('class') or ''):
                        return False
                    
                    btn.click()
                    time.sleep(2)
                    return True
            except Exception:
                continue
        
        return False
    
    def _filter_new_orders(self, orders: List[OrderInfo]) -> List[OrderInfo]:
        """
        过滤出新订单
        
        Args:
            orders: 订单列表
            
        Returns:
            新订单列表
        """
        new_orders = []
        
        for order in orders:
            order_id_hash = self._hash_order_id(order.order_id)
            
            if order_id_hash not in self.fetched_orders:
                order.is_new = True
                new_orders.append(order)
                
                # 触发新订单回调
                if self.on_new_order and callable(self.on_new_order):
                    try:
                        self.on_new_order(order)
                    except Exception as e:
                        logger.error(f"新订单回调执行失败: {e}")
            else:
                order.is_new = False
        
        return new_orders
    
    def _hash_order_id(self, order_id: str) -> str:
        """生成订单ID的哈希值"""
        return hashlib.md5(order_id.encode()).hexdigest()
    
    def _mark_as_fetched(self, order_id: str) -> None:
        """标记订单为已抓取"""
        order_hash = self._hash_order_id(order_id)
        self.fetched_orders[order_hash] = format_time()
    
    def _load_fetched_orders(self) -> Dict[str, str]:
        """加载已抓取的订单记录"""
        shop_name = self.shop_config.get('name', 'unknown')
        filepath = self.storage_path / f"fetched_{shop_name}.json"
        
        if filepath.exists():
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # 清理过期的记录（保留7天）
                    cutoff = format_time(time.time() - 7 * 24 * 3600)
                    return {k: v for k, v in data.items() if v > cutoff}
            except Exception:
                pass
        
        return {}
    
    def _save_fetched_orders(self) -> None:
        """保存已抓取的订单记录"""
        shop_name = self.shop_config.get('name', 'unknown')
        filepath = self.storage_path / f"fetched_{shop_name}.json"
        
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(self.fetched_orders, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存抓取记录失败: {e}")
    
    def _save_orders(self, orders: List[OrderInfo]) -> str:
        """
        保存订单数据
        
        Args:
            orders: 订单列表
            
        Returns:
            保存的文件路径
        """
        shop_name = self.shop_config.get('name', 'unknown')
        date_str = datetime.now().strftime("%Y%m%d")
        filename_format = self.fetch_config.get(
            'filename_format',
            'orders_{date}.json'
        )
        filename = filename_format.replace('{date}', date_str)
        filepath = self.storage_path / f"{shop_name}_{filename}"
        
        try:
            # 读取现有数据
            existing_orders = []
            if filepath.exists():
                with open(filepath, 'r', encoding='utf-8') as f:
                    existing_orders = json.load(f)
            
            # 追加新订单
            existing_orders.extend([asdict(o) for o in orders])
            
            # 保存
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(existing_orders, f, ensure_ascii=False, indent=2)
            
            logger.info(f"订单数据已保存: {filepath}")
            return str(filepath)
            
        except Exception as e:
            logger.error(f"保存订单数据失败: {e}")
            return ""
    
    def set_new_order_callback(self, callback: callable) -> None:
        """
        设置新订单回调函数
        
        Args:
            callback: 回调函数，接收 OrderInfo 参数
        """
        self.on_new_order = callback
        logger.debug("新订单回调已设置")
    
    def get_unprinted_orders(self) -> List[OrderInfo]:
        """
        获取未打印的订单
        
        Returns:
            未打印的订单列表
        """
        # 这个功能需要配合打印模块使用
        # 可以从已保存的订单中筛选未打印的
        pass
    
    def mark_orders_printed(self, order_ids: List[str]) -> None:
        """
        标记订单为已打印
        
        Args:
            order_ids: 订单ID列表
        """
        pass
