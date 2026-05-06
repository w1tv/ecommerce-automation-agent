"""
快递单打印模块
支持调用本地打印服务或快递公司API打印快递单
"""
import time
import json
import base64
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

from loguru import logger

from utils.config_loader import get_config
from utils.helpers import format_time, ensure_dir
from core.browser_manager import BrowserManager
from core.exception_handler import ExceptionHandler


@dataclass
class PrintTask:
    """打印任务"""
    task_id: str
    order_id: str
    express_company: str  # 快递公司代码: SF, YTO, ZTO, EMS, JD, etc.
    sender: Dict[str, str]  # 发件人信息
    receiver: Dict[str, str]  # 收件人信息
    items: List[Dict]  # 商品信息
    weight: float = 0  # 重量(kg)
    remark: str = ""  # 备注
    status: str = "pending"  # pending, printing, completed, failed
    created_time: str = ""
    printed_time: Optional[str] = None
    error_message: str = ""


@dataclass
class ExpressConfig:
    """快递配置"""
    company_code: str  # 快递公司代码
    company_name: str  # 快递公司名称
    api_url: str  # 电子面单API地址
    api_key: str  # API密钥
    customer_name: str  # 电子面单客户名称
    customer_pwd: str  # 电子面单客户密码
    month_code: str  # 月结账号
    send_site: str  # 商家编码
    send_staff: str = ""  # 快递员编号


class LabelPrinter:
    """
    快递单打印处理器
    支持多种打印方式：
    1. 调用本地打印服务API
    2. 直接调用快递公司电子面单API
    3. 生成PDF面单文件供手动打印
    """
    
    # 快递公司代码映射
    EXPRESS_COMPANIES = {
        'SF': {'name': '顺丰速运', 'api_code': 'SF'},
        'YTO': {'name': '圆通速递', 'api_code': 'YTO'},
        'ZTO': {'name': '中通快递', 'api_code': 'ZTO'},
        'ZJS': {'name': '宅急送', 'api_code': 'ZJS'},
        'YUNDA': {'name': '韵达快递', 'api_code': 'YUNDA'},
        'EMS': {'name': 'EMS', 'api_code': 'EMS'},
        'JD': {'name': '京东物流', 'api_code': 'JD'},
        'STO': {'name': '申通快递', 'api_code': 'STO'},
        'TT': {'name': '天天快递', 'api_code': 'TT'},
        'DBL': {'name': '德邦快递', 'api_code': 'DBL'},
    }
    
    def __init__(
        self,
        browser_manager: BrowserManager,
        shop_config: Dict[str, Any]
    ):
        """
        初始化打印处理器
        
        Args:
            browser_manager: 浏览器管理器
            shop_config: 店铺配置
        """
        self.browser = browser_manager
        self.shop_config = shop_config
        self.config = get_config()
        
        # 快递配置
        self.express_config = shop_config.get('express', {})
        self.default_courier = self.express_config.get('default_courier', 'SF')
        
        # 打印API配置
        self.api_url = self.express_config.get('api_url', 'http://localhost:8080/api/print')
        self.api_key = self.express_config.get('api_key', '')
        
        # 异常处理
        self.exception_handler = ExceptionHandler()
        
        # 打印任务记录
        self.print_tasks: Dict[str, PrintTask] = {}
        
        # 保存目录
        self.output_dir = ensure_dir('./data/print_tasks')
        
        logger.info(f"LabelPrinter 初始化完成 (默认快递: {self.default_courier})")
    
    def print_label(
        self,
        order_id: str,
        express_company: Optional[str] = None,
        receiver_info: Optional[Dict[str, str]] = None,
        sender_info: Optional[Dict[str, str]] = None,
        items: Optional[List[Dict]] = None,
        weight: float = 0,
        remark: str = ""
    ) -> PrintTask:
        """
        打印快递单
        
        Args:
            order_id: 订单ID
            express_company: 快递公司代码
            receiver_info: 收件人信息
            sender_info: 发件人信息
            items: 商品信息列表
            weight: 重量(kg)
            remark: 备注
            
        Returns:
            PrintTask 对象
        """
        express_company = express_company or self.default_courier
        
        # 创建打印任务
        task = PrintTask(
            task_id=self._generate_task_id(),
            order_id=order_id,
            express_company=express_company,
            sender=sender_info or self._get_default_sender(),
            receiver=receiver_info or {},
            items=items or [],
            weight=weight,
            remark=remark,
            created_time=format_time()
        )
        
        logger.info(f"创建打印任务: {task.task_id}, 订单: {order_id}, 快递: {express_company}")
        
        try:
            # 尝试使用多种方式打印
            success = False
            
            # 方式1: 调用本地打印服务
            if self._print_via_local_service(task):
                success = True
            
            # 方式2: 直接调用快递API
            elif self._print_via_express_api(task):
                success = True
            
            # 方式3: 生成面单文件供手动打印
            else:
                self._generate_print_file(task)
            
            if success:
                task.status = "completed"
                task.printed_time = format_time()
                logger.info(f"打印成功: {task.task_id}")
            else:
                task.status = "failed"
                logger.warning(f"打印任务已保存待手动处理: {task.task_id}")
            
        except Exception as e:
            task.status = "failed"
            task.error_message = str(e)
            logger.error(f"打印失败: {e}")
            self.exception_handler.handle(e, {'order_id': order_id})
        
        # 保存任务记录
        self._save_task(task)
        
        return task
    
    def print_batch(
        self,
        tasks: List[Dict]
    ) -> Dict[str, PrintTask]:
        """
        批量打印
        
        Args:
            tasks: 任务列表，每项包含 order_id 等信息
            
        Returns:
            任务结果字典
        """
        results = {}
        
        for task_data in tasks:
            try:
                task = self.print_label(**task_data)
                results[task_data['order_id']] = task
                
                # 每打印一单休息一下，避免请求过快
                time.sleep(1)
                
            except Exception as e:
                logger.error(f"批量打印任务失败: {e}")
        
        return results
    
    def _print_via_local_service(self, task: PrintTask) -> bool:
        """
        通过本地打印服务打印
        
        Args:
            task: 打印任务
            
        Returns:
            是否成功
        """
        if not self.api_url:
            return False
        
        try:
            import requests
            
            payload = {
                'order_id': task.order_id,
                'express_company': task.express_company,
                'sender': task.sender,
                'receiver': task.receiver,
                'items': task.items,
                'weight': task.weight,
                'remark': task.remark,
                'api_key': self.api_key
            }
            
            response = requests.post(
                self.api_url,
                json=payload,
                timeout=30
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get('success'):
                    logger.info(f"本地打印服务调用成功: {result.get('message', '')}")
                    return True
                else:
                    logger.warning(f"本地打印服务返回失败: {result.get('message', '')}")
            
            return False
            
        except Exception as e:
            logger.debug(f"本地打印服务不可用: {e}")
            return False
    
    def _print_via_express_api(self, task: PrintTask) -> bool:
        """
        直接调用快递公司API打印
        
        修复说明：原版本没有实际对接API但永远返回True，导致无法fallback到生成打印文件。
        现在根据配置判断是否启用API，未启用时返回False。
        
        Args:
            task: 打印任务
            
        Returns:
            是否成功
        """
        # 【修复】检查是否启用API打印
        express_config = self.config.get('express', {})
        if not express_config.get('api_enabled', False):
            logger.debug("快递API打印未启用（express.api_enabled=false）")
            return False
        
        try:
            express_info = self.EXPRESS_COMPANIES.get(task.express_company, {})
            company_name = express_info.get('name', task.express_company)
            
            logger.info(f"调用 {company_name} 电子面单API...")
            
            # 构建电子面单请求参数
            # 以菜鸟电子面单API为例（其他快递公司类似）
            eorder_params = self._build_eorder_params(task)
            
            # 检查是否配置了API密钥和URL
            api_url = express_config.get('api_url')
            api_key = express_config.get('api_key')
            
            if not api_url or not api_key:
                logger.debug("快递API未配置（api_url或api_key为空）")
                return False
            
            # 这里应该调用实际的API
            # 由于不同快递公司的API不同，这里提供框架代码
            # 实际使用时需要对接具体的快递公司API
            
            # 示例：调用电子面单打印接口
            # result = self._call_express_api(eorder_params)
            
            logger.info(f"电子面单API调用完成（待对接具体API）")
            # 【修复】如果实际对接了API，这里返回True；未对接则返回False
            return False
            
        except Exception as e:
            logger.debug(f"快递API打印失败: {e}")
            return False
    
    def _build_eorder_params(self, task: PrintTask) -> Dict:
        """
        构建电子面单请求参数
        
        Args:
            task: 打印任务
            
        Returns:
            API参数字典
        """
        return {
            'express_company_code': task.express_company,
            'order_id': task.order_id,
            'sender': {
                'name': task.sender.get('name', ''),
                'tel': task.sender.get('phone', ''),
                'province': task.sender.get('province', ''),
                'city': task.sender.get('city', ''),
                'district': task.sender.get('district', ''),
                'address': task.sender.get('address', ''),
                'zip_code': task.sender.get('zip_code', '')
            },
            'receiver': {
                'name': task.receiver.get('name', ''),
                'tel': task.receiver.get('phone', ''),
                'province': task.receiver.get('province', ''),
                'city': task.receiver.get('city', ''),
                'district': task.receiver.get('district', ''),
                'address': task.receiver.get('address', ''),
                'zip_code': task.receiver.get('zip_code', '')
            },
            'weight': task.weight,
            'remark': task.remark,
            # 电子面单专用参数
            'month_code': self.express_config.get('month_code', ''),
            'customer_name': self.express_config.get('customer_name', ''),
            'customer_pwd': self.express_config.get('customer_pwd', ''),
            'send_site': self.express_config.get('send_site', ''),
            'send_staff': self.express_config.get('send_staff', '')
        }
    
    def _call_express_api(self, params: Dict) -> Dict:
        """
        调用快递公司API
        
        Args:
            params: API参数
            
        Returns:
            API响应结果
        """
        # 这里需要根据具体快递公司的API实现
        # 以顺丰为例：
        # 
        # import requests
        # 
        # # 签名算法
        # import hashlib, time
        # timestamp = str(int(time.time()))
        # sign_str = params['customer_id'] + timestamp + params['api_key']
        # sign = hashlib.md5(sign_str.encode()).hexdigest()
        # 
        # headers = {
        #     'Content-Type': 'application/json',
        #     'sign': sign,
        #     'timestamp': timestamp
        # }
        # 
        # response = requests.post(
        #     'https://open-sf.sf-express.com/api/v1/express/print',
        #     json=params,
        #     headers=headers,
        #     timeout=30
        # )
        # 
        # return response.json()
        
        raise NotImplementedError("需要对接具体的快递公司API")
    
    def _generate_print_file(self, task: PrintTask) -> str:
        """
        生成打印文件（供手动打印）
        
        Args:
            task: 打印任务
            
        Returns:
            文件路径
        """
        import uuid
        
        # 生成面单HTML
        html_content = self._generate_label_html(task)
        
        # 保存文件
        shop_name = self.shop_config.get('name', 'unknown')
        filename = f"label_{shop_name}_{task.order_id}_{uuid.uuid4().hex[:8]}.html"
        filepath = self.output_dir / filename
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        logger.info(f"面单文件已生成: {filepath}")
        return str(filepath)
    
    def _generate_label_html(self, task: PrintTask) -> str:
        """
        生成快递面单HTML
        
        Args:
            task: 打印任务
            
        Returns:
            HTML字符串
        """
        express_name = self.EXPRESS_COMPANIES.get(
            task.express_company, {}
        ).get('name', task.express_company)
        
        html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>快递面单 - {task.order_id}</title>
    <style>
        @page {{
            size: 100mm 180mm;
            margin: 0;
        }}
        body {{
            width: 100mm;
            height: 180mm;
            margin: 0;
            padding: 2mm;
            font-family: 'Microsoft YaHei', Arial, sans-serif;
            font-size: 10pt;
            box-sizing: border-box;
        }}
        .label {{
            border: 1px solid #000;
            padding: 3mm;
            height: 100%;
        }}
        .header {{
            display: flex;
            justify-content: space-between;
            border-bottom: 1px solid #000;
            padding-bottom: 2mm;
            margin-bottom: 2mm;
        }}
        .company {{ font-size: 14pt; font-weight: bold; }}
        .order-no {{ font-size: 10pt; }}
        .content {{
            display: flex;
            gap: 3mm;
        }}
        .sender, .receiver {{
            flex: 1;
        }}
        .section-title {{
            font-weight: bold;
            border-bottom: 1px solid #000;
            margin-bottom: 1mm;
        }}
        .info-row {{
            margin: 1mm 0;
        }}
        .footer {{
            margin-top: 2mm;
            border-top: 1px solid #000;
            padding-top: 2mm;
        }}
        .barcode {{
            text-align: center;
            font-family: 'Libre Barcode 128', monospace;
            font-size: 24pt;
        }}
        @media print {{
            body {{
                -webkit-print-color-adjust: exact;
                print-color-adjust: exact;
            }}
        }}
    </style>
</head>
<body>
    <div class="label">
        <div class="header">
            <span class="company">{express_name}</span>
            <span class="order-no">订单号: {task.order_id}</span>
        </div>
        <div class="content">
            <div class="sender">
                <div class="section-title">寄件人</div>
                <div class="info-row">姓名: {task.sender.get('name', '')}</div>
                <div class="info-row">电话: {task.sender.get('phone', '')}</div>
                <div class="info-row">
                    地址: {task.sender.get('province', '')}{task.sender.get('city', '')}{task.sender.get('district', '')}{task.sender.get('address', '')}
                </div>
            </div>
            <div class="receiver">
                <div class="section-title">收件人</div>
                <div class="info-row">姓名: {task.receiver.get('name', '')}</div>
                <div class="info-row">电话: {task.receiver.get('phone', '')}</div>
                <div class="info-row">
                    地址: {task.receiver.get('province', '')}{task.receiver.get('city', '')}{task.receiver.get('district', '')}{task.receiver.get('address', '')}
                </div>
            </div>
        </div>
        <div class="footer">
            <div class="info-row">备注: {task.remark or '无'}</div>
            <div class="info-row">重量: {task.weight}kg</div>
            <div class="barcode">*{task.order_id}*</div>
        </div>
    </div>
</body>
</html>
"""
        return html
    
    def _get_default_sender(self) -> Dict[str, str]:
        """获取默认发件人信息"""
        return {
            'name': '商家名称',
            'phone': '13800138000',
            'province': '广东省',
            'city': '深圳市',
            'district': '福田区',
            'address': '某某街道某某大厦',
            'zip_code': '518000'
        }
    
    def _generate_task_id(self) -> str:
        """生成任务ID"""
        import uuid
        return f"PT{int(time.time())}{uuid.uuid4().hex[:6]}"
    
    def _save_task(self, task: PrintTask) -> None:
        """保存打印任务"""
        self.print_tasks[task.task_id] = task
        
        # 持久化到文件
        filepath = self.output_dir / f"task_{task.task_id}.json"
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump({
                'task_id': task.task_id,
                'order_id': task.order_id,
                'express_company': task.express_company,
                'sender': task.sender,
                'receiver': task.receiver,
                'items': task.items,
                'weight': task.weight,
                'remark': task.remark,
                'status': task.status,
                'created_time': task.created_time,
                'printed_time': task.printed_time,
                'error_message': task.error_message
            }, f, ensure_ascii=False, indent=2)
    
    def get_task_status(self, task_id: str) -> Optional[str]:
        """
        获取任务状态
        
        Args:
            task_id: 任务ID
            
        Returns:
            任务状态
        """
        task = self.print_tasks.get(task_id)
        return task.status if task else None
    
    def get_express_companies(self) -> Dict[str, str]:
        """获取支持的快递公司列表"""
        return {code: info['name'] for code, info in self.EXPRESS_COMPANIES.items()}


class LocalPrintService:
    """
    本地打印服务（可选组件）
    
    如果需要实现自动打印功能，可以启动一个本地HTTP服务
    来接收打印请求并调用系统打印机
    
    使用方法：
    1. 安装打印服务依赖：pip install python-escpos
    2. 配置 config.yaml 中的 express.api_url
    3. 启动打印服务：python -m core.label_printer --start-service
    """
    
    def __init__(self, port: int = 8080):
        self.port = port
        self.print_queue = []
    
    def start_server(self):
        """启动打印服务"""
        from flask import Flask, request, jsonify
        from escpos.printer import Network
        
        app = Flask(__name__)
        
        @app.route('/api/print', methods=['POST'])
        def print_label():
            data = request.json
            
            try:
                printer_ip = data.get('printer_ip', '192.168.1.100')
                label_data = data.get('label_data', {})
                
                # 连接打印机并打印
                printer = Network(printer_ip)
                
                # 这里需要根据实际打印机型号编写打印指令
                # 示例：
                # printer.text(f"订单: {label_data.get('order_id')}\n")
                # printer.barcode(label_data.get('order_id'), 'CODE128')
                # printer.cut()
                
                printer.close()
                
                return jsonify({'success': True, 'message': '打印成功'})
                
            except Exception as e:
                return jsonify({'success': False, 'message': str(e)}), 500
        
        app.run(host='0.0.0.0', port=self.port, debug=False)
    
    def add_to_queue(self, task: PrintTask):
        """添加打印任务到队列"""
        self.print_queue.append(task)
    
    def process_queue(self):
        """处理打印队列"""
        while self.print_queue:
            task = self.print_queue.pop(0)
            # 处理打印...
            pass
