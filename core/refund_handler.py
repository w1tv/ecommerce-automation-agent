"""
退货退款自动处理模块
自动检测和处理退货退款申请
"""
import time
import json
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, asdict
from datetime import datetime

from loguru import logger

from utils.config_loader import get_config
from utils.helpers import format_time, parse_price, ensure_dir, safe_get
from core.browser_manager import BrowserManager
from core.exception_handler import ExceptionHandler, RecoveryManager, ActionWithRecovery


@dataclass
class RefundInfo:
    """退款信息"""
    refund_id: str
    order_id: str
    amount: float
    reason: str
    reason_detail: str
    status: str
    buyer_name: str
    apply_time: str
    images: List[str] = None
    processed: bool = False
    process_result: str = ""
    process_time: Optional[str] = None
    
    def __post_init__(self):
        if self.images is None:
            self.images = []


class RefundHandler:
    """
    退货退款处理器
    自动处理退货退款申请，支持可配置的审批规则
    """
    
    def __init__(
        self,
        browser_manager: BrowserManager,
        shop_config: Dict[str, Any]
    ):
        """
        初始化退货退款处理器
        
        Args:
            browser_manager: 浏览器管理器
            shop_config: 店铺配置
        """
        self.browser = browser_manager
        self.shop_config = shop_config
        self.config = get_config()
        
        # 选择器配置
        self.selectors = shop_config.get('selectors', {}).get('refund', {})
        
        # 退款规则配置
        self.rules = shop_config.get('refund_rules', {})
        
        # 异常处理
        self.exception_handler = ExceptionHandler()
        self.recovery_manager = RecoveryManager(browser_manager)
        self.action_executor = ActionWithRecovery(
            self.recovery_manager,
            action_name="退款处理"
        )
        
        # 数据存储
        self.storage_path = ensure_dir(
            self.config.get('storage.refunds.path', './data/refunds')
        )
        
        # 已处理记录（防止重复处理）
        self.processed_refunds = self._load_processed_refunds()
    
    def process(self) -> Dict[str, Any]:
        """
        处理当前店铺的退款申请
        
        Returns:
            处理结果统计
        """
        result = {
            'total': 0,
            'approved': 0,
            'rejected': 0,
            'skipped': 0,
            'errors': 0,
            'details': []
        }
        
        logger.info(f"开始处理退款申请 (店铺: {self.shop_config.get('name')})")
        
        # 检查时间窗口
        if not self._is_within_time_window():
            logger.info("当前不在处理时间窗口内，跳过")
            return result
        
        try:
            # 导航到退款列表页面
            refund_url = self.selectors.get('list_url') or self.shop_config.get('refund', {}).get('list_url')
            if not refund_url:
                logger.error("未配置退款列表页面URL")
                return result
            
            if not self.browser.navigate(refund_url):
                logger.error("无法导航到退款列表页面")
                return result
            
            # 等待页面加载
            time.sleep(2)
            
            # 获取退款列表
            refunds = self._fetch_refund_list()
            result['total'] = len(refunds)
            
            logger.info(f"发现 {len(refunds)} 条退款申请")
            
            # 处理每条退款
            for refund in refunds:
                try:
                    process_result = self._process_single_refund(refund)
                    
                    if process_result == 'approved':
                        result['approved'] += 1
                    elif process_result == 'rejected':
                        result['rejected'] += 1
                    elif process_result == 'skipped':
                        result['skipped'] += 1
                    else:
                        result['errors'] += 1
                    
                    result['details'].append(asdict(refund))
                    
                except Exception as e:
                    logger.error(f"处理退款失败: {e}")
                    result['errors'] += 1
                    
                    # 保存截图便于调试
                    self.browser.save_screenshot(f"refund_error_{refund.refund_id}")
            
            # 保存处理记录
            self._save_processed_refunds()
            
        except Exception as e:
            logger.error(f"退款处理流程异常: {e}")
            self.exception_handler.handle(e, {'shop': self.shop_config.get('name')})
        
        logger.info(
            f"退款处理完成: 共{result['total']}条，"
            f"同意{result['approved']}条，拒绝{result['rejected']}条，"
            f"跳过{result['skipped']}条，失败{result['errors']}条"
        )
        
        return result
    
    def _fetch_refund_list(self) -> List[RefundInfo]:
        """
        获取退款列表
        
        Returns:
            退款信息列表
        """
        refunds = []
        
        item_selector = self.selectors.get('refund_item', '.refund-item')
        
        try:
            # 等待列表加载
            if not self.browser.wait_for_selector(item_selector, timeout=10000):
                logger.warning("未找到退款列表项")
                return refunds
            
            # 获取退款项数量
            items = self.browser.state.page.query_selector_all(item_selector)
            
            for item in items:
                try:
                    refund_info = self._parse_refund_item(item)
                    if refund_info and not self._is_already_processed(refund_info.refund_id):
                        refunds.append(refund_info)
                except Exception as e:
                    logger.debug(f"解析退款项失败: {e}")
                    continue
            
        except Exception as e:
            logger.error(f"获取退款列表失败: {e}")
        
        return refunds
    
    def _parse_refund_item(self, item) -> Optional[RefundInfo]:
        """
        解析单个退款项
        
        Args:
            item: 退款项元素
            
        Returns:
            RefundInfo 对象
        """
        try:
            # 获取退款ID
            refund_id_elem = item.query_selector(
                self.selectors.get('refund_id', '.refund-id')
            )
            refund_id = refund_id_elem.text_content().strip() if refund_id_elem else ""
            
            # 获取订单ID
            order_id_elem = item.query_selector(
                self.selectors.get('order_id', '.order-id') or 
                self.selectors.get('order_id', '.order-no')
            )
            order_id = order_id_elem.text_content().strip() if order_id_elem else ""
            
            # 获取退款原因
            reason_elem = item.query_selector(
                self.selectors.get('reason', '.refund-reason')
            )
            reason = reason_elem.text_content().strip() if reason_elem else ""
            
            # 获取退款金额
            amount_elem = item.query_selector(
                self.selectors.get('amount', '.refund-amount')
            )
            amount_text = amount_elem.text_content().strip() if amount_elem else "0"
            amount = parse_price(amount_text)
            
            # 获取状态
            status_elem = item.query_selector(
                self.selectors.get('status', '.refund-status')
            )
            status = status_elem.text_content().strip() if status_elem else ""
            
            # 获取买家名称
            buyer_elem = item.query_selector(
                self.selectors.get('buyer_name', '.buyer-name')
            )
            buyer_name = buyer_elem.text_content().strip() if buyer_elem else ""
            
            # 获取详细原因
            detail_elem = item.query_selector(
                self.selectors.get('reason_detail', '.reason-detail')
            )
            reason_detail = detail_elem.text_content().strip() if detail_elem else ""
            
            # 获取凭证图片
            images = []
            image_elems = item.query_selector_all(
                self.selectors.get('images', '.evidence-images img')
            )
            for img in image_elems:
                src = img.get_attribute('src')
                if src:
                    images.append(src)
            
            return RefundInfo(
                refund_id=refund_id,
                order_id=order_id,
                amount=amount,
                reason=reason,
                reason_detail=reason_detail,
                status=status,
                buyer_name=buyer_name,
                apply_time=format_time(),
                images=images
            )
            
        except Exception as e:
            logger.debug(f"解析退款项详情失败: {e}")
            return None
    
    def _process_single_refund(self, refund: RefundInfo) -> str:
        """
        处理单条退款申请
        
        Args:
            refund: 退款信息
            
        Returns:
            处理结果: approved, rejected, skipped, error
        """
        logger.info(f"处理退款: {refund.refund_id}, 金额: ¥{refund.amount}, 原因: {refund.reason}")
        
        # 根据规则判断处理方式
        action = self._decide_action(refund)
        
        if action == 'approve':
            return self._approve_refund(refund)
        elif action == 'reject':
            return self._reject_refund(refund)
        elif action == 'manual':
            return self._mark_for_manual(refund)
        else:
            return self._skip_refund(refund)
    
    def _decide_action(self, refund: RefundInfo) -> str:
        """
        根据规则决定处理动作
        
        Args:
            refund: 退款信息
            
        Returns:
            动作: approve, reject, manual, skip
        """
        # 检查是否启用自动处理
        if not self.rules.get('enabled', True):
            return 'skip'
        
        # 获取规则配置
        auto_approve_rules = self.rules.get('auto_approve', {})
        auto_reject_rules = self.rules.get('auto_reject', {})
        manual_rules = self.rules.get('require_manual', {})
        silent_mode = self.rules.get('silent_mode', False)
        
        # 静默模式：只记录不处理
        if silent_mode:
            logger.info(f"静默模式: 标记退款 {refund.refund_id} 为待审核")
            return 'manual'
        
        # 检查是否需要人工审核
        if manual_rules.get('enabled', True):
            amount_threshold = manual_rules.get('amount_threshold', 200)
            keywords = manual_rules.get('keywords', [])
            
            if refund.amount > amount_threshold:
                logger.info(f"金额 {refund.amount} 超过阈值 {amount_threshold}，需人工审核")
                return 'manual'
            
            for keyword in keywords:
                if keyword in refund.reason or keyword in refund.reason_detail:
                    logger.info(f"检测到关键词 '{keyword}'，需人工审核")
                    return 'manual'
        
        # 检查是否自动同意
        if auto_approve_rules.get('enabled', True):
            max_amount = auto_approve_rules.get('max_amount', 50)
            allowed_reasons = auto_approve_rules.get('reasons', [])
            
            if refund.amount <= max_amount:
                # 检查是否在允许的原因列表中
                if not allowed_reasons:
                    return 'approve'
                
                for reason in allowed_reasons:
                    if reason in refund.reason:
                        logger.info(f"退款原因 '{refund.reason}' 在自动同意列表中")
                        return 'approve'
        
        # 检查是否自动拒绝
        if auto_reject_rules.get('enabled', True):
            reject_reasons = auto_reject_rules.get('reject_reasons', [])
            min_amount = auto_reject_rules.get('min_amount_threshold', 500)
            
            # 检查拒绝原因
            for reject_reason in reject_reasons:
                if reject_reason in refund.reason or reject_reason in refund.reason_detail:
                    if refund.amount <= min_amount:
                        logger.info(f"退款原因 '{reject_reason}' 在自动拒绝列表中")
                        return 'reject'
            
            # 超过金额下限不自动拒绝
            if refund.amount > min_amount:
                return 'manual'
        
        # 默认跳过（待人工处理）
        return 'skip'
    
    def _approve_refund(self, refund: RefundInfo) -> str:
        """同意退款"""
        try:
            approve_button = self.selectors.get('approve_button', '.btn-approve')
            
            logger.info(f"同意退款: {refund.refund_id}")
            
            # 点击同意按钮
            if self.browser.click(approve_button):
                time.sleep(1)
                
                # 处理可能出现的确认对话框
                self._handle_confirm_dialog()
                
                refund.processed = True
                refund.process_result = 'approved'
                refund.process_time = format_time()
                
                self._mark_as_processed(refund.refund_id)
                return 'approved'
            else:
                logger.error(f"点击同意按钮失败: {refund.refund_id}")
                return 'error'
                
        except Exception as e:
            logger.error(f"同意退款失败: {e}")
            return 'error'
    
    def _reject_refund(self, refund: RefundInfo) -> str:
        """拒绝退款"""
        try:
            reject_button = self.selectors.get('reject_button', '.btn-reject')
            
            logger.info(f"拒绝退款: {refund.refund_id}")
            
            # 点击拒绝按钮
            if self.browser.click(reject_button):
                time.sleep(1)
                
                # 处理可能出现的拒绝原因输入
                self._handle_reject_reason_dialog()
                
                refund.processed = True
                refund.process_result = 'rejected'
                refund.process_time = format_time()
                
                self._mark_as_processed(refund.refund_id)
                return 'rejected'
            else:
                logger.error(f"点击拒绝按钮失败: {refund.refund_id}")
                return 'error'
                
        except Exception as e:
            logger.error(f"拒绝退款失败: {e}")
            return 'error'
    
    def _mark_for_manual(self, refund: RefundInfo) -> str:
        """标记为待人工处理"""
        refund.processed = False
        refund.process_result = 'manual'
        refund.process_time = format_time()
        
        logger.info(f"退款 {refund.refund_id} 已标记为待人工处理")
        return 'manual'
    
    def _skip_refund(self, refund: RefundInfo) -> str:
        """跳过该退款"""
        refund.processed = False
        refund.process_result = 'skipped'
        
        logger.info(f"跳过退款: {refund.refund_id}")
        return 'skipped'
    
    def _handle_confirm_dialog(self) -> None:
        """处理确认对话框"""
        try:
            # 常见的确认按钮选择器
            confirm_selectors = [
                '.confirm-btn',
                '.btn-confirm',
                'button.confirm',
                '[class*="confirm"]'
            ]
            
            for selector in confirm_selectors:
                if self.browser.wait_for_selector(selector, timeout=2000):
                    self.browser.click(selector)
                    break
            
            time.sleep(1)
            
        except Exception:
            pass
    
    def _handle_reject_reason_dialog(self) -> None:
        """处理拒绝原因对话框"""
        try:
            # 输入拒绝原因
            reason_input = '.reject-reason-input, #reject-reason'
            if self.browser.wait_for_selector(reason_input, timeout=2000):
                self.browser.fill(reason_input, "不符合退款条件")
            
            # 点击确认
            submit_button = '.btn-submit, .confirm-btn'
            if self.browser.wait_for_selector(submit_button, timeout=2000):
                self.browser.click(submit_button)
            
            time.sleep(1)
            
        except Exception:
            pass
    
    def _is_within_time_window(self) -> bool:
        """检查是否在允许的处理时间窗口内"""
        if not self.rules.get('time_window', {}).get('enabled', True):
            return True
        
        time_window = self.rules.get('time_window', {})
        start_hour = time_window.get('start_hour', 9)
        end_hour = time_window.get('end_hour', 22)
        
        from utils.helpers import is_within_time_window
        return is_within_time_window(start_hour, end_hour)
    
    def _is_already_processed(self, refund_id: str) -> bool:
        """检查退款是否已处理"""
        return refund_id in self.processed_refunds
    
    def _mark_as_processed(self, refund_id: str) -> None:
        """标记退款为已处理"""
        self.processed_refunds[refund_id] = format_time()
    
    def _load_processed_refunds(self) -> Dict[str, str]:
        """加载已处理的退款记录"""
        shop_name = self.shop_config.get('name', 'unknown')
        filepath = self.storage_path / f"processed_{shop_name}.json"
        
        if filepath.exists():
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        
        return {}
    
    def _save_processed_refunds(self) -> None:
        """保存已处理的退款记录"""
        shop_name = self.shop_config.get('name', 'unknown')
        filepath = self.storage_path / f"processed_{shop_name}.json"
        
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(self.processed_refunds, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存处理记录失败: {e}")
    
    def save_refund_records(self, refunds: List[RefundInfo]) -> str:
        """
        保存退款记录到文件
        
        Args:
            refunds: 退款信息列表
            
        Returns:
            保存的文件路径
        """
        shop_name = self.shop_config.get('name', 'unknown')
        date_str = datetime.now().strftime("%Y%m%d")
        filepath = self.storage_path / f"refunds_{shop_name}_{date_str}.json"
        
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump([asdict(r) for r in refunds], f, ensure_ascii=False, indent=2)
            
            logger.info(f"退款记录已保存: {filepath}")
            return str(filepath)
            
        except Exception as e:
            logger.error(f"保存退款记录失败: {e}")
            return ""
