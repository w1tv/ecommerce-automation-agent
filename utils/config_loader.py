"""
配置文件加载器
支持 YAML 格式配置文件，提供热更新能力

【修复内容】
1. 增加 ${ENV_VAR} 环境变量替换支持
2. 修复超时时间单位（秒而非毫秒）
"""
import os
import re
import yaml
from pathlib import Path
from typing import Any, Dict, Optional
from loguru import logger


class ConfigLoader:
    """配置文件加载器（单例模式）"""
    
    _instance: Optional['ConfigLoader'] = None
    _config: Dict[str, Any] = {}
    _config_path: str = ""
    
    def __new__(cls):
        """单例模式，确保配置只加载一次"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        """初始化配置加载器"""
        if not self._config:
            self._load_default_config()
    
    def _load_default_config(self):
        """加载默认配置"""
        self._config = {
            "global": {
                "debug": False,
                "shop_rest_interval": 10,
                "max_retry": 3,
                "retry_interval": 30,
                "page_timeout": 30,  # 【修复】改为秒（原来是30000毫秒）
                "element_timeout": 10
            },
            "browser": {
                "type": "chromium",
                "headless": True,
                "disable_images": False,
                "viewport": {"width": 1920, "height": 1080},
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "download_path": "./downloads",
                "stealth": {
                    "enabled": True,
                    "random_delay_min": 100,
                    "random_delay_max": 500
                }
            },
            "schedule": {
                "enabled": True,
                "order_fetch_interval": 5,
                "refund_process_interval": 10,
                "heartbeat_interval": 60,
                "run_on_startup": True
            },
            "session": {
                "enabled": True,
                "storage_path": "./data/sessions",
                "cookie_max_age_days": 7
            },
            "captcha": {
                "detect_enabled": True,
                "selectors": [".captcha-container", "#captcha", ".geetest_panel"],
                "action": "wait",
                "wait_timeout": 300
            },
            "alerter": {
                "enabled": False,
                "channels": {},
                "events": {}
            },
            "express": {
                "api_enabled": False,
                "api_url": "",
                "api_key": "",
                "default_courier": "SF"
            },
            "shops": [],
            "heartbeat": {
                "enabled": True,
                "interval": 60,
                "failure_threshold": 3
            },
            "recovery": {
                "network_retry": 5,
                "page_crash_retry": 3
            },
            "logging": {
                "level": "INFO",
                "console": True,
                "file": True,
                "file_path": "./logs/ecommerce_agent.log"
            },
            "storage": {
                "orders": {"type": "json", "path": "./data/orders"},
                "refunds": {"type": "json", "path": "./data/refunds"}
            }
        }
        logger.debug("已加载默认配置")
    
    def _replace_env_vars(self, obj: Any) -> Any:
        """
        【新增】递归替换配置中的环境变量
        支持 ${ENV_VAR} 语法
        
        Args:
            obj: 配置对象（dict, list, str 等）
            
        Returns:
            替换后的对象
        """
        if isinstance(obj, str):
            # 匹配 ${VAR_NAME} 格式
            pattern = r'\$\{([^}]+)\}'
            matches = re.findall(pattern, obj)
            
            for var_name in matches:
                env_value = os.environ.get(var_name, '')
                if env_value:
                    obj = obj.replace(f'${{{var_name}}}', env_value)
                else:
                    # 环境变量不存在，替换为空字符串
                    obj = obj.replace(f'${{{var_name}}}', '')
            
            return obj
            
        elif isinstance(obj, dict):
            return {k: self._replace_env_vars(v) for k, v in obj.items()}
            
        elif isinstance(obj, list):
            return [self._replace_env_vars(item) for item in obj]
            
        return obj
    
    def load(self, config_path: str = "config.yaml") -> Dict[str, Any]:
        """
        从文件加载配置
        
        Args:
            config_path: 配置文件路径
            
        Returns:
            配置字典
        """
        self._config_path = config_path
        
        if not os.path.exists(config_path):
            logger.warning(f"配置文件不存在: {config_path}，使用默认配置")
            return self._config
        
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                loaded_config = yaml.safe_load(f)
            
            if loaded_config:
                # 【新增】先替换环境变量
                loaded_config = self._replace_env_vars(loaded_config)
                
                # 深度合并配置
                self._config = self._deep_merge(self._config, loaded_config)
                logger.info(f"成功加载配置文件: {config_path}")
            else:
                logger.warning(f"配置文件为空: {config_path}")
                
        except yaml.YAMLError as e:
            logger.error(f"配置文件解析失败: {e}")
            raise
        except Exception as e:
            logger.error(f"加载配置文件时发生错误: {e}")
            raise
        
        return self._config
    
    def reload(self) -> Dict[str, Any]:
        """重新加载配置（热更新）"""
        if self._config_path:
            logger.info("正在热更新配置...")
            return self.load(self._config_path)
        else:
            logger.warning("未指定配置文件路径，无法热更新")
            return self._config
    
    def get(self, key: str, default: Any = None) -> Any:
        """
        获取配置值，支持点号分隔的路径
        
        Args:
            key: 配置键，如 "global.debug" 或 "browser.headless"
            default: 默认值
            
        Returns:
            配置值
        """
        keys = key.split('.')
        value = self._config
        
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        
        return value
    
    def set(self, key: str, value: Any):
        """
        设置配置值，支持点号分隔的路径
        
        Args:
            key: 配置键，如 "global.debug"
            value: 配置值
        """
        keys = key.split('.')
        config = self._config
        
        for k in keys[:-1]:
            if k not in config:
                config[k] = {}
            config = config[k]
        
        config[keys[-1]] = value
        logger.debug(f"配置已更新: {key} = {value}")
    
    def get_shop_config(self, shop_name: str) -> Optional[Dict[str, Any]]:
        """
        获取指定店铺的配置
        
        Args:
            shop_name: 店铺名称
            
        Returns:
            店铺配置字典
        """
        shops = self._config.get('shops', [])
        for shop in shops:
            if shop.get('name') == shop_name:
                return shop
        return None
    
    @property
    def all_shops(self) -> list:
        """获取所有店铺配置"""
        return self._config.get('shops', [])
    
    @property
    def config(self) -> Dict[str, Any]:
        """获取完整配置"""
        return self._config
    
    @staticmethod
    def _deep_merge(base: Dict, override: Dict) -> Dict:
        """深度合并两个字典"""
        result = base.copy()
        
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = ConfigLoader._deep_merge(result[key], value)
            else:
                result[key] = value
        
        return result


# 全局配置实例
_config_loader: Optional[ConfigLoader] = None


def get_config() -> ConfigLoader:
    """获取全局配置加载器实例"""
    global _config_loader
    if _config_loader is None:
        _config_loader = ConfigLoader()
    return _config_loader


# 便捷函数
def load_config(path: str = "config.yaml") -> Dict[str, Any]:
    """加载配置文件"""
    return get_config().load(path)


def get_config_value(key: str, default: Any = None) -> Any:
    """获取配置值"""
    return get_config().get(key, default)
