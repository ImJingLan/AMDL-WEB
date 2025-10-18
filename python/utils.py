# -*- coding: utf-8 -*-
# utils.py - Shared utilities for backend and main scheduler

import os
import json
import yaml
import logging
import re
from filelock import FileLock, Timeout
import sys

# --- Constants ---
# Define project root relative to this utils.py file
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR) # Assuming utils.py is in server/python

# Default relative paths (originally from main.py)
DEFAULT_PATHS_RELATIVE_TO_ROOT = {
    'task_queue': 'info/task_queue.json',
    'errors': 'info/errors.json',
    'users': 'config/users.yaml',
    'source': 'config/source.yaml',
    'go_main': 'go/main.go',            # Keep for consistency? main.py uses it.
    'go_metadata': 'go/get_metadata.go', # Keep for consistency? main.py uses it.
    'logs': 'logs.log'                   # Keep for consistency? main.py uses it.
    # Add paths used by backend.py if they differ or are needed commonly
    # 'token_file_path': 'config/api_token.json', # Example if needed
}

# Link Parsing Regex (originally from backend.py)
ALBUM_REGEX = re.compile(r"^(?:https?://(?:beta\.music|music)\.apple\.com/)(?P<storefront>\w{2})(?:/album)(?:/.+)?/(?P<id>\d+)(?:$|\?)")
MV_REGEX = re.compile(r"^(?:https?://(?:beta\.music|music)\.apple\.com/)(?P<storefront>\w{2})(?:/music-video)(?:/.+)?/(?P<id>\d+)(?:$|\?)")
SONG_REGEX = re.compile(r"^(?:https?://(?:beta\.music|music)\.apple\.com/)(?P<storefront>\w{2})(?:/song)(?:/.+)?/(?P<id>\d+)(?:$|\?)")
PLAYLIST_REGEX = re.compile(r"^(?:https?://(?:beta\.music|music)\.apple\.com/)(?P<storefront>\w{2})(?:/playlist)(?:/.+)?/(?P<id>pl\.[\w-]+)(?:$|\?)")

REGEX_MAP = {
    "album": ALBUM_REGEX,
    "music-video": MV_REGEX,
    "song": SONG_REGEX,
    "playlist": PLAYLIST_REGEX,
}

# Task Type Map (originally from notifications.py)
TASK_TYPE_MAP = {
    "album": "专辑",
    "playlist": "播放列表",
    "music-video": "MV",
    # 可以根据需要添加更多类型
}

# --- Utility Functions ---

def read_json_with_lock(filepath, lock, default=None):
    """安全地读取 JSON 文件 (带锁)。源自 backend.py """
    try:
        # 使用非阻塞锁避免长时间等待，如果锁已被占用则快速失败或返回默认值
        with lock.acquire(timeout=0.1): # 短暂尝试获取锁
            if not os.path.exists(filepath):
                return default
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    return default
                return json.loads(content)
    except Timeout:
        logging.warning(f"获取文件锁超时 (非阻塞): {lock.lock_file}. 返回默认值或上次缓存值（如果适用）。")
        return default # 或者根据策略返回 None 或上次的缓存
    except json.JSONDecodeError as e:
        logging.error(f"解析 JSON 文件失败: {filepath}, 错误: {e}")
        return None # 返回 None 以区分空文件和错误
    except Exception as e:
        logging.error(f"读取文件时发生错误: {filepath}, 错误: {e}", exc_info=True)
        return None

def write_json_with_lock(filepath, lock, data):
    """安全地写入 JSON 文件 (带锁，使用临时文件)。源自 backend.py """
    temp_filepath = filepath + ".tmp"
    try:
        # 对写操作使用更长的超时时间
        with lock.acquire(timeout=10):
            dir_path = os.path.dirname(filepath)
            if dir_path:
                os.makedirs(dir_path, exist_ok=True)
            with open(temp_filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            # 确保文件完全写入磁盘
            # f.flush() # with open 会在退出时自动 flush
            # os.fsync(f.fileno()) # 在 Windows 上可能不可靠或不必要

            os.replace(temp_filepath, filepath) # 原子替换
            logging.debug(f"成功写入 JSON 文件: {filepath}")
            return True
    except Timeout:
        logging.error(f"获取文件锁超时: {lock.lock_file}，无法写入 {filepath}")
        return False
    except Exception as e:
        logging.error(f"写入 JSON 文件时发生错误: {filepath}, 错误: {e}", exc_info=True)
        if os.path.exists(temp_filepath):
            try:
                os.remove(temp_filepath)
                logging.info(f"已删除临时文件: {temp_filepath}")
            except OSError as remove_err:
                logging.error(f"删除临时文件失败: {temp_filepath}, 错误: {remove_err}")
        return False

def read_yaml_with_lock(filepath, lock):
    """使用 filelock 文件锁从文件读取 YAML 数据。源自 main.py"""
    try:
        with lock.acquire(timeout=5): # 为读操作设置超时
            if not os.path.exists(filepath):
                 logging.warning(f"YAML 文件未找到，将返回空字典: {filepath}")
                 return {}
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
                if not content.strip():
                    logging.debug(f"YAML 文件为空: {filepath}")
                    return {}
                try:
                    data = yaml.safe_load(content)
                    return data if data is not None else {}
                except yaml.YAMLError as e:
                    logging.error(f"解析 YAML 文件 {filepath} 时出错: {e}")
                    return {} # 解析错误返回空字典
    except Timeout:
         logging.error(f"获取文件锁超时: {lock.lock_file}，无法读取 {filepath}")
         return {} # 超时返回空字典
    except Exception as e:
        logging.error(f"读取 YAML 文件 {filepath} 时发生意外错误: {e}", exc_info=True)
        return {} # 其他错误返回空字典

def resolve_paths(root_dir, relative_paths_config, defaults):
    """将相对路径配置解析为绝对路径，使用默认值作为后备。"""
    resolved = {}
    for key, default_rel_path in defaults.items():
        # 优先使用 relative_paths_config 中的路径
        rel_path = relative_paths_config.get(key, default_rel_path)
        # 确保 rel_path 不为空
        if rel_path:
            abs_path = os.path.abspath(os.path.join(root_dir, rel_path))
            resolved[key] = os.path.normpath(abs_path)
            logging.debug(f"已解析路径 '{key}': {resolved[key]}")
        else:
            logging.warning(f"路径键 '{key}' 的配置值为空或未找到，跳过解析。")
    return resolved

# --- Logging Setup (originally from backend.py) ---
class RequestLogFilter(logging.Filter):
    """过滤掉 Flask 请求相关的日志以及空行等。"""
    def filter(self, record):
        message = record.getMessage()
        # 过滤掉包含特定关键词的日志
        # 添加对空消息的过滤
        if not message.strip() or any(keyword in message for keyword in [
            "GET /task", "POST /task", "HTTP/1.",
            "process_request_thread", # 可能是 backend 特有的？暂时保留
            "werkzeug", # 过滤 werkzeug 自身日志
            # "DEBUG:urllib3.connectionpool", # 可选：过滤详细的 HTTP 请求日志
        ]):
            return False
        return True

def setup_logging(config, script_chinese_name="未知脚本"):
    """
    配置日志记录器。
    从配置中读取日志级别、格式和文件路径。
    """
    log_level_str = config.get("log_level", "INFO").upper()
    log_level = getattr(logging, log_level_str, logging.INFO)
    
    # 用户期望格式: "mm.dd HH:MM:SS - 脚本中文名 - 日志级别 - 信息"
    log_format_str = f"%(asctime)s - {script_chinese_name} - %(levelname)s - %(message)s"
    date_format_str = "%m.%d %H:%M:%S" # 无毫秒

    log_file_path_key = "log_file_path" # key in config for backend
    logs_path_key_in_paths = "logs"      # key in config.paths for main/email_checker

    # 从 config 中获取日志文件路径
    log_file = config.get(log_file_path_key) # 优先尝试 backend.py 的键
    if not log_file:
        # 尝试从 config['paths']['logs'] (如果存在)
        paths_config = config.get('paths', {})
        log_file_relative = paths_config.get(logs_path_key_in_paths)
        if log_file_relative:
            log_file = os.path.join(PROJECT_ROOT, log_file_relative)
        else:
            # 如果两者都没有，使用 DEFAULT_PATHS_RELATIVE_TO_ROOT 中的 'logs'
            log_file_relative_default = DEFAULT_PATHS_RELATIVE_TO_ROOT.get(logs_path_key_in_paths)
            if log_file_relative_default:
                 log_file = os.path.join(PROJECT_ROOT, log_file_relative_default)
            else: # 最后的备用，直接在项目根目录的 logs 文件夹下
                 log_file = os.path.join(PROJECT_ROOT, "logs", "logs.log")


    # 确保路径是绝对的
    if not os.path.isabs(log_file):
        log_file = os.path.join(PROJECT_ROOT, log_file) # 假设是相对于 PROJECT_ROOT
    
    # 确保日志目录存在
    log_dir = os.path.dirname(log_file)
    if log_dir: 
        os.makedirs(log_dir, exist_ok=True)

    # 获取根记录器
    logger = logging.getLogger()
    logger.setLevel(log_level) 

    # 移除所有现有的处理器，以避免重复添加
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()

    # 配置 FileHandler
    try:
        file_handler = logging.FileHandler(log_file, mode='a', encoding='utf-8')
        file_handler.setFormatter(logging.Formatter(log_format_str, datefmt=date_format_str))
        logger.addHandler(file_handler)
    except Exception as e:
        print(f"CRITICAL: 无法配置日志文件处理器 '{log_file}': {e}", flush=True)
        # 如果文件日志失败，则不应继续，因为这是核心功能
        # 但为了让 print 能起作用，暂时不 exit(1)


    # 配置 StreamHandler (控制台输出)
    console_handler = logging.StreamHandler(sys.stdout) # 显式使用 sys.stdout
    console_handler.setFormatter(logging.Formatter(log_format_str, datefmt=date_format_str))
    console_handler.setLevel(log_level) 
    logger.addHandler(console_handler)
    
    # 初始化后打印一条消息，这条消息会使用新的格式
    logging.info(f"日志系统已初始化。级别: {log_level_str}。文件: {log_file}")
    if script_chinese_name == "未知脚本":
        logging.warning("调用 setup_logging 时未提供 script_chinese_name，将使用默认值。")


# --- Task Helpers (originally from notifications.py) ---
def get_task_display_info(task_data):
    """从任务数据中提取显示名称和中文类型。"""
    name = "未知名称"
    type_key = "未知类型"
    type_zh = "未知类型"

    if not isinstance(task_data, dict):
        logging.warning("传递给 get_task_display_info 的 task_data 不是字典。")
        return name, type_zh, type_key

    try:
        metadata = task_data.get("metadata", {})
        link_info = task_data.get("link_info", {})

        # 优先从 metadata 获取名称
        if isinstance(metadata, dict) and metadata.get("name"):
            name = metadata.get("name")
        # 其次尝试从 link_info 获取 (虽然 link_info 通常没有 name)
        elif isinstance(link_info, dict) and link_info.get("name"): # 不太可能有
             name = link_info.get("name")
        # 最后回退到基于 ID (如果 metadata 有 ID)
        elif isinstance(metadata, dict) and metadata.get("id"):
             name = f"ID: {metadata.get('id')}"

        # 获取类型
        if isinstance(link_info, dict) and link_info.get("type"):
            type_key = link_info.get("type")
            type_zh = TASK_TYPE_MAP.get(type_key, f"类型({type_key})")

    except Exception as e:
        logging.warning(f"任务 {task_data.get('uuid', '未知UUID')}: 提取显示信息时出错: {e}")

    return name, type_zh, type_key # 返回 key 用于可能的逻辑判断


# --- Username & Link Processing (originally from backend.py) ---
def normalize_username(submitted_username, users_mapping):
    """将提交的用户名（忽略大小写）映射到标准用户名"""
    if not submitted_username or not isinstance(users_mapping, dict):
        return None

    submitted_lower = submitted_username.lower()
    for standard_user, data in users_mapping.items():
        # 检查标准用户名（忽略大小写）
        if standard_user.lower() == submitted_lower:
            return standard_user
        # 检查别名
        if isinstance(data, dict):
            other_names = data.get("other_name", [])
            if isinstance(other_names, list):
                for alias in other_names:
                     if isinstance(alias, str) and alias.lower() == submitted_lower:
                        return standard_user
            # else: # 仅在需要时记录警告
            #      logging.warning(f"用户 '{standard_user}' 的 other_name 格式无效，应为列表。")

    logging.warning(f"无法将用户名 '{submitted_username}' 映射到任何标准用户。")
    return None

def parse_link(link_url, allowed_storefronts):
    """解析 Apple Music 链接，返回类型、区域和ID"""
    if not link_url or not isinstance(allowed_storefronts, set):
        return None

    original_link = link_url # 保留原始链接用于日志
    # 如果是专辑链接且包含 ?i= 参数，先去掉这个参数
    if "/album/" in link_url and "?i=" in link_url:
        link_url = link_url.split("?i=")[0]
        logging.debug(f"检测到专辑链接包含 ?i= 参数，已规范化: {link_url}")

    for link_type, regex in REGEX_MAP.items():
        match = regex.match(link_url)
        if match:
            data = match.groupdict()
            storefront = data["storefront"].lower()
            if storefront in allowed_storefronts:
                logging.debug(f"链接解析成功: 类型={link_type}, 区域={storefront}, ID={data['id']}")
                return {
                    "type": link_type,
                    "storefront": storefront,
                    "id": data["id"]
                }
            else:
                logging.warning(f"链接 '{original_link}' 的区域代码 '{storefront}' 不在允许列表中: {allowed_storefronts}")
                return None # 区域不允许

    logging.warning(f"无法解析链接: {original_link}")
    return None # 未匹配任何模式 