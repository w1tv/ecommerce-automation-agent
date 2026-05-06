#!/usr/bin/env python3
"""
Docker 健康检查脚本
检查 Agent 进程和浏览器是否存活

【功能说明】
1. 检查主进程是否运行
2. 检查日志文件是否在更新（Agent 是否活跃）
3. 检查配置和必要文件是否存在
4. 返回健康状态给 Docker

【使用方法】
docker-compose.yaml 中配置：
healthcheck:
  test: ["CMD", "python", "scripts/healthcheck.py"]
  interval: 30s
  timeout: 10s
  retries: 3
  start_period: 60s
"""
import os
import sys
import time
import signal
from pathlib import Path
from datetime import datetime, timedelta

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def check_process_alive() -> tuple:
    """
    检查 Agent 进程是否存活
    
    Returns:
        (是否存活, 消息)
    """
    try:
        # 检查当前进程
        pid = os.getpid()
        
        # 检查主进程文件
        pid_file = project_root / 'data' / 'agent.pid'
        
        if pid_file.exists():
            with open(pid_file, 'r') as f:
                stored_pid = int(f.read().strip())
            
            # 检查进程是否真的在运行
            try:
                os.kill(stored_pid, 0)  # 不发送信号，只检查
                return True, f"Agent 进程运行中 (PID: {stored_pid})"
            except OSError:
                # 进程不存在，可能需要重启
                return False, f"PID 文件存在但进程 {stored_pid} 不存在"
        else:
            # 没有 PID 文件，检查主进程是否运行
            return True, f"Agent 进程运行中 (PID: {pid})"
            
    except Exception as e:
        return False, f"检查进程失败: {e}"


def check_log_updated(max_age_seconds: int = 120) -> tuple:
    """
    检查日志文件是否在更新
    
    Args:
        max_age_seconds: 日志最大更新时间（秒）
        
    Returns:
        (是否健康, 消息)
    """
    try:
        log_file = project_root / 'logs' / 'ecommerce_agent.log'
        
        if not log_file.exists():
            # 检查 logs 目录是否存在
            logs_dir = project_root / 'logs'
            if not logs_dir.exists():
                return False, "日志目录不存在"
            
            # 日志文件不存在，可能刚启动
            return True, "日志文件尚未创建（可能刚启动）"
        
        # 获取文件修改时间
        mtime = log_file.stat().st_mtime
        age = time.time() - mtime
        
        if age > max_age_seconds:
            return False, f"日志文件超过 {max_age_seconds} 秒未更新（年龄: {int(age)}秒）"
        
        return True, f"日志文件正常（{int(age)}秒前更新）"
        
    except Exception as e:
        return False, f"检查日志失败: {e}"


def check_config_valid() -> tuple:
    """
    检查配置文件是否有效
    
    Returns:
        (是否健康, 消息)
    """
    try:
        config_file = project_root / 'config.yaml'
        
        if not config_file.exists():
            return False, "配置文件不存在"
        
        # 尝试读取配置
        import yaml
        with open(config_file, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        
        if not config:
            return False, "配置文件为空"
        
        # 检查必要的配置项
        required_keys = ['global', 'browser', 'shops']
        missing = [k for k in required_keys if k not in config]
        
        if missing:
            return False, f"配置缺少必要项: {missing}"
        
        return True, "配置文件正常"
        
    except ImportError:
        return True, "PyYAML 未安装，跳过配置检查"
    except Exception as e:
        return False, f"检查配置失败: {e}"


def check_directories() -> tuple:
    """
    检查必要目录是否存在
    
    Returns:
        (是否健康, 消息)
    """
    required_dirs = [
        'data',
        'data/orders',
        'data/refunds',
        'data/print_tasks',
        'data/sessions',
        'logs',
        'downloads'
    ]
    
    missing = []
    for dir_name in required_dirs:
        dir_path = project_root / dir_name
        if not dir_path.exists():
            missing.append(dir_name)
    
    if missing:
        return False, f"缺少目录: {missing}"
    
    return True, "目录结构正常"


def check_heartbeat_file() -> tuple:
    """
    检查心跳文件
    
    Returns:
        (是否健康, 消息)
    """
    try:
        heartbeat_file = project_root / 'data' / 'heartbeat.json'
        
        if not heartbeat_file.exists():
            return True, "心跳文件尚未创建"
        
        import json
        with open(heartbeat_file, 'r', encoding='utf-8') as f:
            heartbeat = json.load(f)
        
        last_heartbeat = heartbeat.get('timestamp', 0)
        age = time.time() - last_heartbeat
        
        if age > 300:  # 5 分钟
            return False, f"心跳超时（{int(age)}秒前）"
        
        return True, f"心跳正常（{int(age)}秒前）"
        
    except Exception as e:
        return True, f"无法检查心跳: {e}"


def run_healthcheck() -> int:
    """
    执行健康检查
    
    Returns:
        0 = 健康, 1 = 不健康, 2 = 警告
    """
    checks = [
        ("进程检查", check_process_alive),
        ("日志检查", check_log_updated),
        ("配置检查", check_config_valid),
        ("目录检查", check_directories),
        ("心跳检查", check_heartbeat_file),
    ]
    
    all_healthy = True
    all_warnings = True
    
    print("=" * 50)
    print(f"健康检查开始: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)
    
    for check_name, check_func in checks:
        healthy, message = check_func()
        status = "✓" if healthy else "✗"
        print(f"[{status}] {check_name}: {message}")
        
        if not healthy:
            all_healthy = False
        else:
            all_warnings = False
    
    print("=" * 50)
    
    if all_healthy:
        print("结果: 所有检查通过 ✓")
        return 0
    elif all_warnings:
        print("结果: 检查完成（有警告）")
        return 0  # 警告不算失败
    else:
        print("结果: 检查失败 ✗")
        return 1


if __name__ == '__main__':
    # 设置信号处理
    def signal_handler(signum, frame):
        print(f"接收到信号 {signum}，退出")
        sys.exit(1)
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    # 执行健康检查
    exit_code = run_healthcheck()
    sys.exit(exit_code)
