# backend.py
import os
import re
import uuid
import json
import time
import logging
import threading
import platform
import zipfile # <--- 添加导入
import shutil  # <--- 添加导入
import io      # <--- 添加导入
import socket  # <--- 新增导入
import hashlib # <--- 新增导入，用于缓存key生成
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Dict, List, Optional, Tuple

# 用于时区检测
import tzlocal
try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError # Python 3.9+
except ImportError:
    logging.warning("zoneinfo module not found, timezones might not work correctly on Python < 3.9 without backports.zoneinfo or pytz.")
    ZoneInfo = None
    ZoneInfoNotFoundError = Exception

import yaml
import requests
from flask import Flask, request, jsonify, Response
from filelock import FileLock, Timeout

# --- 导入共享工具 --- #
from utils import (
    read_json_with_lock, write_json_with_lock,
    read_yaml_with_lock,
    setup_logging,
    normalize_username,
    parse_link,
    PROJECT_ROOT # 使用 utils 中定义的项目根目录
)

# --- 配置基础路径 --- #
# SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# SERVER_DIR = os.path.dirname(SCRIPT_DIR) # 使用 PROJECT_ROOT 代替
SERVER_DIR = PROJECT_ROOT # server/python 的父目录，即 amdl 目录

# --- 全局变量 (将在启动时填充) ---
CONFIG = {}
USERS_DATA = {}
TOKEN_MANAGER = None
SEARCH_CACHE_MANAGER = None  # 新增：搜索缓存管理器
TASK_QUEUE_FILEPATH = None
TASK_QUEUE_LOCK_FILEPATH = None
API_TOKEN_LOCK_FILEPATH = None
APP_LOCKS = {"task_queue": None, "api_token": None}
LOCAL_TZ = None # 将存储本地时区对象

# --- 长轮询支持 ---
class TaskQueueNotifier:
    """任务队列变化通知器，支持长轮询 (使用 threading.Condition)"""
    def __init__(self):
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._change_pending = False # 标志是否有未处理的通知
        # 保留日志初始化，以便调试
        logging.debug("TaskQueueNotifier initialized with Condition.")

    def wait_for_change(self, timeout=30):
        """等待队列变化，返回 True 表示有变化，False 表示超时"""
        with self._lock: # self._condition 会使用这个锁
            logging.debug(f"wait_for_change: 开始等待，当前 change_pending: {self._change_pending}")
            start_time = time.time()
            
            # 循环以处理 spurious wakeups，并确保 _change_pending 为 True 时才退出
            while not self._change_pending:
                remaining_timeout = timeout - (time.time() - start_time)
                if remaining_timeout <= 0:
                    logging.debug("wait_for_change: 等待超时 (在进入wait之前或循环中计算)。")
                    return False # 在等待开始前或等待中已超时

                # wait() 会释放锁，直到被通知或超时
                # 如果被通知，wait() 返回 True (Python 3.2+); 如果超时，返回 False
                # 对于旧版本 Python, 超时时 wait() 不返回值 (None)，被通知时也不一定返回 True
                # 因此，最可靠的是在唤醒后重新检查 self._change_pending
                self._condition.wait(remaining_timeout) 
                
                # 无论 wait() 返回什么，或者是否是 spurious wakeup，
                # 我们都需要在重新获得锁后检查 _change_pending 的状态。
                if self._change_pending:
                    # 条件满足，跳出循环
                    break
                
                # 如果执行到这里，说明 self._change_pending 仍为 False
                # 这可能是因为 wait() 超时了，或者是一个 spurious wakeup 但条件仍未满足
                # 检查 remaining_timeout 以确定是否是真的超时
                if (time.time() - start_time) >= timeout:
                    logging.debug("wait_for_change: 确认等待超时 (循环内检查)。")
                    return False # 确实超时了
                
                # 否则，是 spurious wakeup 且条件未满足，继续循环等待
                logging.debug("wait_for_change: Spurious wakeup 或条件仍未满足，继续等待。")

            # 到这里时，self._change_pending 肯定是 True
            logging.debug("wait_for_change: 检测到变化，消耗通知。")
            self._change_pending = False # 消耗这个通知
            return True # 表示有变化

    def notify_change(self):
        """通知队列发生变化"""
        with self._lock: # self._condition 会使用这个锁
            logging.debug("notify_change: 设置 change_pending 为 True 并通知所有等待者。")
            self._change_pending = True # 确保状态被设置
            self._condition.notify_all() # 唤醒所有等待的线程

# 全局通知器实例
QUEUE_NOTIFIER = TaskQueueNotifier()

app = Flask(__name__)

# --- 日志配置 ---
# def setup_logging(config): ... # 移动到 utils.py

# --- 配置加载 --- #
# def load_yaml(file_path): ... # 使用 utils.read_yaml_with_lock 代替

def validate_config(config_data):
    """检查关键配置项是否存在"""
    required_keys = [
        "max_retries", "retry_delay_seconds", "token_validity_hours",
        "token_file_path", "task_queue_file_path", "users_file_path",
        "log_level", # log_format 不再是必需，因为它由 utils.setup_logging 控制
        # "log_format", 
        "log_file_path", 
        "storefront_language_map", "apple_music_api_base_url",
        "token_fetch_url", "token_fetch_js_regex", "token_fetch_token_regex",
        "user_agent",
        "go_source_dir",
        "compile_server_url",
        "compiled_binary_output_dir",
        "compiled_binary_name",
        "compile_timeout_seconds",
        "SCHEDULER_SIGNAL_PORT" # 确保这个也在 config.yaml 中
    ]
    missing_keys = [key for key in required_keys if key not in config_data]
    if missing_keys:
        logging.critical(f"配置文件 config.yaml 缺少关键键: {', '.join(missing_keys)}。服务器无法启动。")
        exit(1)

    # --- 验证搜索缓存配置 ---
    search_cache_config = config_data.get("search_cache", {})
    if search_cache_config.get("enabled", True):
        cache_dir = search_cache_config.get("cache_dir", "cache/search")
        if not cache_dir:
            logging.warning("搜索缓存已启用但未配置cache_dir，将使用默认值。")
        cache_lifetime = search_cache_config.get("cache_lifetime_hours", 24)
        if not isinstance(cache_lifetime, (int, float)) or cache_lifetime <= 0:
            logging.warning("搜索缓存生命周期配置无效，将使用默认值24小时。")
        max_cache_size = search_cache_config.get("max_cache_size_mb", 100)
        if not isinstance(max_cache_size, (int, float)) or max_cache_size <= 0:
            logging.warning("搜索缓存最大大小配置无效，将使用默认值100MB。")

    # --- 使用 utils.resolve_paths 解析路径 (需要调整) ---
    # 注意：resolve_paths 需要 default_paths，这里可能需要定义 backend 特定的默认值或共享 main.py 的
    # 暂时手动解析，未来可以考虑改进 resolve_paths 或共享默认值
    paths_to_resolve = [
        "token_file_path", "task_queue_file_path", "users_file_path",
        "log_file_path", "go_source_dir", "compiled_binary_output_dir"
    ]
    for key in paths_to_resolve:
        original_path = config_data.get(key)
        if original_path and not os.path.isabs(original_path):
             config_data[key] = os.path.normpath(os.path.join(SERVER_DIR, original_path))
             logging.debug(f"Backend validated and resolved path '{key}': {config_data[key]}")
        elif not original_path and key in required_keys: # 如果是必需路径但值为空
             logging.critical(f"必需的路径配置 '{key}' 在 config.yaml 中值为空。")
             exit(1)

    return config_data

# --- JSON 文件读写 (带锁) --- #
# def read_json_file(filepath, lock, default=None): ... # 使用 utils.read_json_with_lock
# def write_json_file(filepath, lock, data): ... # 使用 utils.write_json_with_lock

# --- 用户名和链接处理 --- #
# def normalize_username(submitted_username, users_mapping): ... # 移动到 utils.py
# ALBUM_REGEX = ... # 移动到 utils.py
# MV_REGEX = ... # 移动到 utils.py
# SONG_REGEX = ... # 移动到 utils.py
# PLAYLIST_REGEX = ... # 移动到 utils.py
# REGEX_MAP = ... # 移动到 utils.py
# def parse_link(link_url, allowed_storefronts): ... # 移动到 utils.py

# --- 搜索缓存管理器 ---
class SearchCacheManager:
    """搜索结果缓存管理器"""
    def __init__(self, config):
        self.config = config
        cache_config = config.get("search_cache", {})
        self.enabled = cache_config.get("enabled", True)
        self.cache_dir = cache_config.get("cache_dir", "cache/search")
        self.cache_lifetime_hours = cache_config.get("cache_lifetime_hours", 24)
        self.clear_on_startup = cache_config.get("clear_on_startup", True)
        self.max_cache_size_mb = cache_config.get("max_cache_size_mb", 100)
        
        # 确保缓存目录使用绝对路径
        if not os.path.isabs(self.cache_dir):
            self.cache_dir = os.path.normpath(os.path.join(SERVER_DIR, self.cache_dir))
        
        # 创建缓存目录
        os.makedirs(self.cache_dir, exist_ok=True)
        
        # 启动时清除缓存
        if self.clear_on_startup:
            self.clear_cache()
        
        logging.info(f"搜索缓存管理器初始化完成 - 启用: {self.enabled}, 目录: {self.cache_dir}, 生命周期: {self.cache_lifetime_hours}小时")
    
    def _generate_cache_key(self, storefront, query_params):
        """生成缓存键"""
        # 创建包含storefront和所有查询参数的字符串
        cache_data = f"{storefront}:{json.dumps(query_params, sort_keys=True)}"
        # 使用MD5生成短的文件名
        return hashlib.md5(cache_data.encode('utf-8')).hexdigest()
    
    def _get_cache_filepath(self, cache_key):
        """获取缓存文件路径"""
        return os.path.join(self.cache_dir, f"{cache_key}.json")
    
    def get_cached_result(self, storefront, query_params):
        """获取缓存的搜索结果"""
        if not self.enabled:
            return None
        
        cache_key = self._generate_cache_key(storefront, query_params)
        cache_filepath = self._get_cache_filepath(cache_key)
        
        if not os.path.exists(cache_filepath):
            return None
        
        try:
            # 检查缓存是否过期
            file_mtime = os.path.getmtime(cache_filepath)
            cache_age_hours = (time.time() - file_mtime) / 3600
            
            if cache_age_hours > self.cache_lifetime_hours:
                logging.debug(f"缓存已过期 ({cache_age_hours:.1f}小时 > {self.cache_lifetime_hours}小时): {cache_key}")
                os.remove(cache_filepath)
                return None
            
            # 读取缓存内容
            with open(cache_filepath, 'r', encoding='utf-8') as f:
                cached_data = json.load(f)
            
            logging.info(f"搜索缓存命中: {cache_key} (缓存年龄: {cache_age_hours:.1f}小时)")
            return cached_data
            
        except (json.JSONDecodeError, OSError) as e:
            logging.warning(f"读取搜索缓存失败: {cache_key}, 错误: {e}")
            try:
                os.remove(cache_filepath)
            except OSError:
                pass
            return None
    
    def cache_result(self, storefront, query_params, result_data):
        """缓存搜索结果"""
        if not self.enabled:
            return
        
        cache_key = self._generate_cache_key(storefront, query_params)
        cache_filepath = self._get_cache_filepath(cache_key)
        
        try:
            # 检查缓存目录大小
            self._cleanup_cache_if_needed()
            
            # 写入缓存
            with open(cache_filepath, 'w', encoding='utf-8') as f:
                json.dump(result_data, f, ensure_ascii=False, separators=(',', ':'))
            
            logging.debug(f"搜索结果已缓存: {cache_key}")
            
        except Exception as e:
            logging.warning(f"缓存搜索结果失败: {cache_key}, 错误: {e}")
    
    def _cleanup_cache_if_needed(self):
        """如果缓存目录大小超限，清理旧文件"""
        try:
            total_size = 0
            cache_files = []
            
            for filename in os.listdir(self.cache_dir):
                filepath = os.path.join(self.cache_dir, filename)
                if os.path.isfile(filepath) and filename.endswith('.json'):
                    size = os.path.getsize(filepath)
                    mtime = os.path.getmtime(filepath)
                    cache_files.append((filepath, size, mtime))
                    total_size += size
            
            # 检查总大小是否超限
            total_size_mb = total_size / (1024 * 1024)
            if total_size_mb > self.max_cache_size_mb:
                logging.info(f"缓存目录大小 ({total_size_mb:.1f}MB) 超过限制 ({self.max_cache_size_mb}MB)，开始清理...")
                
                # 按修改时间排序，删除最旧的文件
                cache_files.sort(key=lambda x: x[2])  # 按mtime排序
                
                for filepath, size, mtime in cache_files:
                    try:
                        os.remove(filepath)
                        total_size -= size
                        total_size_mb = total_size / (1024 * 1024)
                        logging.debug(f"删除旧缓存文件: {os.path.basename(filepath)}")
                        
                        if total_size_mb <= self.max_cache_size_mb * 0.8:  # 清理到80%
                            break
                    except OSError as e:
                        logging.warning(f"删除缓存文件失败: {filepath}, 错误: {e}")
                
                logging.info(f"缓存清理完成，当前大小: {total_size_mb:.1f}MB")
        
        except Exception as e:
            logging.warning(f"缓存清理检查失败: {e}")
    
    def clear_cache(self):
        """清除所有缓存"""
        try:
            if os.path.exists(self.cache_dir):
                file_count = 0
                for filename in os.listdir(self.cache_dir):
                    filepath = os.path.join(self.cache_dir, filename)
                    if os.path.isfile(filepath) and filename.endswith('.json'):
                        os.remove(filepath)
                        file_count += 1
                logging.info(f"已清除 {file_count} 个搜索缓存文件")
            else:
                logging.info("缓存目录不存在，无需清除")
        except Exception as e:
            logging.error(f"清除搜索缓存失败: {e}")

# --- API Token 管理 ---
class ApiTokenManager:
    def __init__(self, config):
        global LOCAL_TZ # 需要访问本地时区
        self.config = config
        self.token_file_path = config["token_file_path"]
        self.lock = APP_LOCKS["api_token"]
        self.token = None
        self.timestamp = None
        self._fetch_lock = threading.Lock()
        self._last_fetch_attempt_failed = False
        self._last_fetch_error_time = None
        self.local_tz = LOCAL_TZ # 存储检测到的本地时区
        # --- 新增: 后台刷新相关属性 ---
        self._background_refresh_active = False
        self._refresh_thread = None
        # 从配置读取，若无则使用默认值 (秒)
        self.background_refresh_interval_seconds = int(self.config.get("token_background_refresh_interval_seconds", 5 * 60)) # 默认5分钟检查一次
        self.background_refresh_threshold_seconds = int(self.config.get("token_background_refresh_threshold_seconds", 60 * 60)) # 默认提前1小时刷新
        # --- 结束新增 ---
        logging.info(f"初始化 API Token 管理器 (配置文件: {self.token_file_path}, 时区: {self.local_tz})")
        self._init_token()

    def _load_token(self):
        """从文件加载 Token"""
        logging.info(f"尝试从文件加载 API Token: {self.token_file_path}")
        # 使用 utils 函数
        token_info = read_json_with_lock(self.token_file_path, self.lock, default={})
        if token_info and "token" in token_info and "timestamp" in token_info:
            try:
                self.token = token_info["token"]
                loaded_dt = datetime.fromisoformat(token_info["timestamp"])
                if loaded_dt.tzinfo is None:
                     logging.warning(f"加载的 Token 时间戳 '{token_info['timestamp']}' 不包含时区信息，将假定为本地时区 {self.local_tz.key}")
                     self.timestamp = loaded_dt.replace(tzinfo=self.local_tz)
                else:
                     self.timestamp = loaded_dt
                logging.info(f"从文件成功加载 API Token (有效时间基于 {self.timestamp.tzname()}) - {self.token[:10]}...")
                # 添加有效期检查日志
                if self._is_valid():
                    remaining_time = (self.timestamp + timedelta(hours=self.config["token_validity_hours"])) - datetime.now(self.local_tz)
                    logging.info(f"当前 Token 仍然有效，剩余时间: {remaining_time}")
                else:
                    logging.warning("当前 Token 已过期，需要重新获取")
                return True
            except (ValueError, TypeError) as e:
                logging.error(f"解析 Token 文件中的时间戳失败: {e}", exc_info=True)
                self.token = None; self.timestamp = None
                return False
        else:
            # 如果 token_info 是 None (读取错误), 也算加载失败
            if token_info is None:
                 logging.error(f"无法从文件 {self.token_file_path} 读取 Token 信息 (读取错误)。")
            else:
                 logging.warning(f"未能从文件 {self.token_file_path} 加载有效 Token (内容无效或不完整)。")
            return False

    def _save_token(self):
        """将当前 Token 和时间戳保存到文件"""
        if self.token and self.timestamp:
            token_info = {
                "token": self.token,
                "timestamp": self.timestamp.isoformat()
            }
            logging.info(f"准备保存新的 API Token 到文件: {self.token_file_path}")
            logging.debug(f"Token 信息: 时间戳={self.timestamp.isoformat()}, Token 前10位={self.token[:10]}...")
            # 使用 utils 函数
            if write_json_with_lock(self.token_file_path, self.lock, token_info):
                logging.info(f"新 API Token 已成功保存到 {self.token_file_path}")
            else:
                logging.error(f"无法保存 API Token 到 {self.token_file_path}")

    def _is_valid(self):
        """检查当前 Token 是否有效 (使用本地时区)"""
        if not self.token or not self.timestamp:
            return False
        validity_duration = timedelta(hours=self.config["token_validity_hours"])
        # 确保 self.timestamp 是 aware 的（_load_token 已处理）
        # 与当前本地时间比较
        return datetime.now(self.local_tz) < (self.timestamp + validity_duration)

    def _fetch_token_from_website(self):
        """尝试从 Apple Music 网站抓取 Token (Python 实现)"""
        logging.info("开始从网页获取新的 Authorization Token...")
        session = requests.Session()
        session.headers.update({"User-Agent": self.config["user_agent"]})
        logging.debug(f"请求头设置: {dict(session.headers)}")
        
        try:
            # 获取主页
            logging.info(f"正在请求主页: {self.config['token_fetch_url']}")
            start_time = time.time()
            response_main = session.get(self.config["token_fetch_url"], timeout=15)
            request_time = time.time() - start_time
            logging.info(f"主页请求完成 - 状态码: {response_main.status_code}, 耗时: {request_time:.2f}秒")
            
            response_main.raise_for_status()
            html_content = response_main.text
            logging.debug(f"主页响应大小: {len(html_content)} 字节")
            
            # 查找 JS 文件
            js_uri_match = re.search(self.config["token_fetch_js_regex"], html_content)
            if not js_uri_match:
                logging.error("在主页 HTML 中未找到 index-legacy-*.js 文件 URI。")
                logging.debug(f"使用的正则表达式: {self.config['token_fetch_js_regex']}")
                return None
                
            js_uri = js_uri_match.group(0)
            js_full_url = self.config["token_fetch_url"].rstrip('/') + js_uri
            logging.info(f"找到 JS 文件 URL: {js_full_url}")
            
            # 获取 JS 文件
            logging.info("正在请求 JS 文件...")
            start_time = time.time()
            response_js = session.get(js_full_url, timeout=15)
            request_time = time.time() - start_time
            logging.info(f"JS 文件请求完成 - 状态码: {response_js.status_code}, 耗时: {request_time:.2f}秒")
            
            response_js.raise_for_status()
            js_content = response_js.text
            logging.debug(f"JS 文件大小: {len(js_content)} 字节")
            
            # 查找 Token
            logging.debug("开始在 JS 文件中搜索 Token...")
            token_match = re.search(self.config["token_fetch_token_regex"], js_content)
            if not token_match:
                 logging.warning("使用配置的正则表达式未找到 Token，尝试备用正则表达式...")
                 token_match = re.search(r"eyJ[a-zA-Z0-9+/_\-.]+", js_content)
                 
            if token_match:
                found_token = token_match.group(0).strip('"')
                logging.info("成功获取到新的 Authorization Token")
                logging.debug(f"Token 长度: {len(found_token)} 字符")
                self._last_fetch_attempt_failed = False
                self._last_fetch_error_time = None
                return found_token
            else:
                logging.error("在 JS 文件中未找到 Authorization Token。网站结构可能已改变。")
                logging.debug(f"使用的正则表达式: {self.config['token_fetch_token_regex']}")
                return None
                
        except requests.exceptions.Timeout as e:
            logging.error(f"获取 Token 时请求超时: {e}")
            return None
        except requests.exceptions.RequestException as e:
            logging.error(f"获取 Token 时网络请求失败: {e}", exc_info=True)
            if hasattr(e.response, 'status_code'):
                logging.error(f"HTTP 状态码: {e.response.status_code}")
            if hasattr(e.response, 'text'):
                logging.error(f"响应内容: {e.response.text[:200]}...")
            return None
        except Exception as e:
            logging.error(f"获取 Token 时发生未知错误: {e}", exc_info=True)
            return None

    def _refresh_token(self):
        """获取新 Token 的主要逻辑，带锁和重试 (使用本地时区)"""
        if not self._fetch_lock.acquire(blocking=False):
            logging.debug("另一线程已在尝试获取 Token，本线程将等待其结果或下次调用。")
            # 如果上次获取失败，且当前 token 无效，可以稍微等待一下，看其他线程是否能成功
            if self._last_fetch_attempt_failed and not self._is_valid():
                logging.debug("上次获取失败且当前 token 无效，等待1秒看其他线程结果。")
                time.sleep(1)
            return self.token

        logging.info("Token 无效或过期，开始获取新 Token...")
        try:
            retry_delay = 60
            if self._last_fetch_attempt_failed and self._last_fetch_error_time:
                # 确保 last_fetch_error_time 是 aware 的
                last_error_aware = self._last_fetch_error_time
                if last_error_aware.tzinfo is None:
                    last_error_aware = last_error_aware.replace(tzinfo=self.local_tz)

                time_since_last_error = datetime.now(self.local_tz) - last_error_aware
                if time_since_last_error < timedelta(seconds=retry_delay):
                    logging.warning(f"距离上次 Token 获取失败时间过短 ({time_since_last_error.total_seconds():.1f}秒)，暂时不重试。")
                    return self.token

            new_token = self._fetch_token_from_website()
            if new_token:
                self.token = new_token
                self.timestamp = datetime.now(self.local_tz)
                logging.info(f"成功获取新 Token，时间戳: {self.timestamp.isoformat()}")
                self._save_token()
                self._last_fetch_attempt_failed = False
            else:
                logging.error("获取新 Token 失败。")
                self._last_fetch_attempt_failed = True
                self._last_fetch_error_time = datetime.now(self.local_tz)
                logging.warning(f"记录获取失败时间: {self._last_fetch_error_time.isoformat()}")
        finally:
            self._fetch_lock.release()
            logging.debug("Token 获取锁已释放")
        return self.token

    def get_token(self):
        """供外部调用的获取 Token 方法"""
        if self._is_valid():
             if self._last_fetch_attempt_failed:
                 logging.warning("当前 Token 仍有效，但上次自动刷新尝试失败，请关注。")
             return self.token
        logging.info("Token 已过期，开始刷新...")
        return self._refresh_token()

    def invalidate_token(self):
        """API 调用返回 401/403 时，强制标记 Token 失效 (使用本地时区)"""
        logging.warning("API 返回 Token 失效信号，将强制刷新 Token。")
        old_timestamp = self.timestamp
        self.timestamp = datetime.now(self.local_tz) - timedelta(hours=self.config['token_validity_hours'] * 2)
        logging.info(f"Token 已标记为失效 - 原时间戳: {old_timestamp.isoformat() if old_timestamp else 'None'}, 新时间戳: {self.timestamp.isoformat()}")

    def _init_token(self):
        """应用启动时直接获取新的 Token"""
        logging.info("开始初始化 API Token...")
        # logging.info("启动时直接获取新的 Token...") # _refresh_token() 内部会记录获取行为
        self._refresh_token()
        if not self.token:
            logging.critical("警告: 服务器启动时未能获取到有效的 API Token。元数据获取功能可能受限，将稍后重试。")
        else:
            logging.info("成功获取初始 API Token。")
        # 初始化完成后启动后台刷新线程
        self.start_background_refresh()

    # --- 新增: 后台刷新方法 ---
    def _periodic_token_check_and_refresh(self):
        """后台线程函数，定期检查并刷新Token。"""
        logging.info(f"API Token 后台刷新线程已启动。检查间隔: {self.background_refresh_interval_seconds}s, 刷新阈值: {self.background_refresh_threshold_seconds}s。")
        while self._background_refresh_active:
            try:
                needs_refresh = False
                is_currently_valid = self._is_valid() # 先检查当前是否有效

                if self.token and self.timestamp:
                    # 确保时间戳是 aware 的，以便进行正确的时区比较
                    current_ts_aware = self.timestamp
                    if current_ts_aware.tzinfo is None: # 理论上 _load_token 或 _refresh_token 会设置好
                        current_ts_aware = current_ts_aware.replace(tzinfo=self.local_tz)

                    expiry_time = current_ts_aware + timedelta(hours=self.config["token_validity_hours"])
                    remaining_time_seconds = (expiry_time - datetime.now(self.local_tz)).total_seconds()

                    if remaining_time_seconds < self.background_refresh_threshold_seconds:
                        logging.info(f"后台检查：Token 剩余 {remaining_time_seconds:.0f} 秒 (阈值 {self.background_refresh_threshold_seconds}s)，需要刷新。")
                        needs_refresh = True
                    else:
                        logging.debug(f"后台检查：Token 剩余 {remaining_time_seconds:.0f} 秒，当前有效。下次检查在约 {self.background_refresh_interval_seconds} 秒后。")
                else:
                    logging.info("后台检查：当前无有效 Token 或时间戳，需要尝试获取。")
                    needs_refresh = True # 如果没有token，也需要刷新

                if needs_refresh:
                    # 如果是因为即将过期而刷新，但当前仍然有效，可以记录一下
                    if not is_currently_valid:
                         logging.info("后台刷新线程：当前 Token 无效，开始刷新...")
                    else: # 即将过期
                         logging.info("后台刷新线程：Token 即将过期，开始主动刷新...")
                    
                    self._refresh_token() # _refresh_token 会记录其自身的成功或失败

                # 等待指定间隔，但允许提前退出
                sleep_chunk = 1 # 每秒检查一次退出标志
                total_slept = 0
                while total_slept < self.background_refresh_interval_seconds and self._background_refresh_active:
                    time.sleep(sleep_chunk)
                    total_slept += sleep_chunk
                
                if not self._background_refresh_active: # 如果在睡眠期间被要求停止
                    break

            except Exception as e:
                logging.error(f"API Token 后台刷新线程发生错误: {e}", exc_info=True)
                # 发生错误后，也等待一个完整的间隔，避免错误情况下CPU空转
                time.sleep(self.background_refresh_interval_seconds)
        logging.info("API Token 后台刷新线程已停止。")

    def start_background_refresh(self):
        """启动后台 Token 刷新线程。"""
        if self._refresh_thread is None or not self._refresh_thread.is_alive():
            self._background_refresh_active = True
            self._refresh_thread = threading.Thread(target=self._periodic_token_check_and_refresh, daemon=True)
            self._refresh_thread.name = "ApiTokenRefreshThread" # 给线程命名方便调试
            self._refresh_thread.start()
            logging.info("API Token 后台刷新线程已请求启动。")
        else:
            logging.info("API Token 后台刷新线程已在运行中，无需重复启动。")

    def stop_background_refresh(self): # 虽然是daemon线程，但提供一个停止方法是好习惯
        """停止后台 Token 刷新线程。"""
        if self._refresh_thread and self._refresh_thread.is_alive():
            logging.info("请求停止 API Token 后台刷新线程...")
            self._background_refresh_active = False
            self._refresh_thread.join(timeout=10) # 等待线程结束，设置超时
            if self._refresh_thread.is_alive():
                logging.warning("API Token 后台刷新线程在超时后仍未停止。")
            else:
                logging.info("API Token 后台刷新线程已成功停止。")
            self._refresh_thread = None
        else:
            logging.info("API Token 后台刷新线程未运行或已停止。")
    # --- 结束新增 ---

# --- 元数据获取与处理 ---
def fetch_metadata(link_info, token_manager, config):
    """获取元数据，带重试逻辑"""
    resource_type_map = {"album": "albums", "song": "songs", "playlist": "playlists", "music-video": "music-videos"}
    resource_type = link_info["type"]
    api_resource = resource_type_map.get(resource_type)
    if not api_resource:
        logging.error(f"未知的资源类型 '{resource_type}' 无法构建 API 请求。")
        return None

    storefront = link_info["storefront"]
    resource_id = link_info["id"]
    lang_code = config["storefront_language_map"].get(storefront, "en-US")
    base_url = config["apple_music_api_base_url"].rstrip('/')
    api_url = f"{base_url}/{storefront}/{api_resource}/{resource_id}"
    params = {"l": lang_code}
    if resource_type == "album": params.update({"include": "tracks,artists", "include[songs]": "artists", "extend": "editorialVideo"})
    elif resource_type == "song": params.update({"extend": "extendedAssetUrls", "include": "albums"})
    elif resource_type == "playlist": params.update({"include": "tracks,curator", "include[songs]": "artists"})
    headers = {"User-Agent": config["user_agent"], "Origin": config["token_fetch_url"]}

    for attempt in range(config["max_retries"]):
        token = token_manager.get_token()
        if not token:
             logging.error(f"元数据获取失败 (Attempt {attempt + 1}/{config['max_retries']}): 未能获取有效 API Token。")
             break
        headers["Authorization"] = f"Bearer {token}"
        try:
            logging.info(f"开始获取元数据 (Attempt {attempt + 1}/{config['max_retries']}): {link_info['type']} {link_info['id']}")
            response = requests.get(api_url, headers=headers, params=params, timeout=20)
            if response.status_code == 200:
                logging.info(f"元数据获取成功: {link_info['type']} {link_info['id']}")
                return response.json()
            elif response.status_code in [401, 403]:
                 logging.warning(f"API 返回 {response.status_code} (Token 失效?), 作废当前 Token 并准备重试...")
                 token_manager.invalidate_token()
            elif response.status_code == 404:
                logging.error(f"元数据获取失败: {link_info['type']} {link_info['id']} - API 返回 404 Not Found。不再重试。")
                return None
            elif response.status_code == 429:
                 logging.warning(f"API 返回 429 Too Many Requests。等待重试...")
                 time.sleep(config["retry_delay_seconds"] * (attempt + 1) * 2)
                 continue
            elif 400 <= response.status_code < 500:
                logging.error(f"元数据获取失败 (Attempt {attempt + 1}/{config['max_retries']}): API 返回客户端错误 {response.status_code}. URL: {response.url}. Response: {response.text[:200]}...")
                if response.status_code == 400:
                    try:
                         err_data = response.json()
                         if err_data.get("errors") and isinstance(err_data["errors"], list):
                            first_error = err_data["errors"][0]
                            if first_error.get("code") == "40005" and first_error.get("source", {}).get("parameter") == "l":
                                logging.error(f"获取元数据失败：Apple Music API 返回无效语言标签 '{params.get('l')}'。不再重试。")
                                return None
                    except json.JSONDecodeError: pass
                logging.error(f"因客户端错误 {response.status_code} 放弃重试。")
                return None
            elif response.status_code >= 500:
                 logging.warning(f"API 返回服务器错误 {response.status_code}。将在 {config['retry_delay_seconds']} 秒后重试...")
            else:
                 logging.error(f"获取元数据时遇到未知状态码 {response.status_code}。将在 {config['retry_delay_seconds']} 秒后重试...")
        except requests.exceptions.Timeout:
             logging.warning(f"元数据获取超时 (Attempt {attempt + 1}/{config['max_retries']})。将在 {config['retry_delay_seconds']} 秒后重试...")
        except requests.exceptions.RequestException as e:
             logging.error(f"元数据获取时网络错误 (Attempt {attempt + 1}/{config['max_retries']}): {e}。将在 {config['retry_delay_seconds']} 秒后重试...")
        except Exception as e:
             logging.error(f"元数据获取时未知错误 (Attempt {attempt + 1}/{config['max_retries']}): {e}", exc_info=True)
        if attempt < config["max_retries"] - 1:
            time.sleep(config["retry_delay_seconds"])
    logging.error(f"尝试 {config['max_retries']} 次后元数据获取最终失败: {link_info['type']} {link_info['id']}")
    return None

# --- 元数据过滤函数 (用户提供) ---
def filter_metadata(link_info, metadata_dict):
    """根据链接类型过滤原始元数据字典，只保留指定的字段，并为专辑/播放列表的音轨添加 song_id。"""
    link_type = link_info.get("type")
    filtered = {}
    try:
        if not isinstance(metadata_dict, dict) or "data" not in metadata_dict or not isinstance(metadata_dict["data"], list) or not metadata_dict["data"]:
            logging.error(f"过滤元数据失败：原始元数据格式无效或为空 (Link Info: {link_info})。")
            return None

        primary_item = metadata_dict["data"][0]
        if not isinstance(primary_item, dict):
            logging.error(f"过滤元数据失败：原始元数据中的主要项目无效 (Link Info: {link_info})。")
            return None

        attributes = primary_item.get("attributes", {})
        relationships = primary_item.get("relationships", {})
        artwork_data = attributes.get("artwork", {})
        artwork_url_template = artwork_data.get("url") if isinstance(artwork_data, dict) else None

        # --- 处理专辑 ---
        if link_type == "album":
            tracks_relationship = relationships.get("tracks", {})
            tracks_data = tracks_relationship.get("data", []) if isinstance(tracks_relationship, dict) else []
            filtered = {
                "name": attributes.get("name"), "artistName": attributes.get("artistName"),
                "id": primary_item.get("id"), "trackCount": attributes.get("trackCount"),
                "artwork_url": artwork_url_template, "tracks": []
            }
            if isinstance(tracks_data, list):
                logging.debug(f"[FilterMetadata-Album:{primary_item.get('id')}] - 找到 {len(tracks_data)} 个原始音轨数据。")
                for song_item in tracks_data:
                    if isinstance(song_item, dict) and song_item.get("type") == "songs" and "id" in song_item and "attributes" in song_item:
                        song_attributes = song_item["attributes"]
                        song_id = song_item.get("id")
                        track_number = song_attributes.get("trackNumber")
                        song_name = song_attributes.get("name")
                        has_lyrics = song_attributes.get("hasLyrics", False)
                        song_url = song_attributes.get("url")
                        disc_number = song_attributes.get("discNumber", 1)
                        
                        if track_number is not None and song_name and song_id:
                            # 计算最大碟片号
                            max_disc_number = max(t.get("attributes", {}).get("discNumber", 1) for t in tracks_data if isinstance(t, dict))
                            filtered["tracks"].append({
                                "track_number": track_number, 
                                "name": song_name, 
                                "hasLyrics": has_lyrics, 
                                "song_id": song_id,
                                "url": song_url,
                                "disc_number": disc_number if max_disc_number > 1 else None,
                                "disc_total": max_disc_number if max_disc_number > 1 else None
                            })
                        else: logging.warning(f"[FilterMetadata-Album:{primary_item.get('id')}] - 跳过音轨，缺少关键信息 (ID: {song_id}, Name: {song_name}, Number: {track_number})。")
                    else: logging.warning(f"[FilterMetadata-Album:{primary_item.get('id')}] - 跳过无效或非歌曲类型的音轨条目: {type(song_item)}")
            else: logging.warning(f"[FilterMetadata-Album:{primary_item.get('id')}] - 未找到有效的音轨关系数据。")

        # --- 处理音乐视频 ---
        elif link_type == "music-video":
             aw_width = artwork_data.get("width") if isinstance(artwork_data, dict) else None
             aw_height = artwork_data.get("height") if isinstance(artwork_data, dict) else None
             filtered = {"name": attributes.get("name"), "artistName": attributes.get("artistName"), "id": primary_item.get("id"), "durationInMillis": attributes.get("durationInMillis"), "width": aw_width, "height": aw_height, "artwork_url": artwork_url_template}

        # --- 处理播放列表 ---
        elif link_type == "playlist":
             tracks_relationship = relationships.get("tracks", {})
             tracks_data = tracks_relationship.get("data", []) if isinstance(tracks_relationship, dict) else []
             curator_name = attributes.get("curatorName")
             if not curator_name and "curator" in relationships:
                  curator_data = relationships["curator"].get("data")
                  if isinstance(curator_data, list) and curator_data:
                       curator_attributes = curator_data[0].get("attributes", {})
                       curator_name = curator_attributes.get("name")
             filtered = {"name": attributes.get("name"), "curatorName": curator_name, "id": primary_item.get("id"), "lastModifiedDate": attributes.get("lastModifiedDate"), "trackCount": 0, "artwork_url": artwork_url_template, "tracks": []}
             if isinstance(tracks_data, list):
                 logging.debug(f"[FilterMetadata-Playlist:{primary_item.get('id')}] - 找到 {len(tracks_data)} 个原始音轨数据。")
                 for index, song_item in enumerate(tracks_data):
                    if isinstance(song_item, dict) and song_item.get("type") == "songs" and "id" in song_item and "attributes" in song_item:
                        song_attributes = song_item["attributes"]
                        song_id = song_item.get("id"); song_name = song_attributes.get("name"); has_lyrics = song_attributes.get("hasLyrics", False)
                        song_url = song_attributes.get("url")
                        if song_name and song_id:
                            filtered["tracks"].append({
                                "track_number": index + 1, 
                                "name": song_name, 
                                "hasLyrics": has_lyrics, 
                                "song_id": song_id,
                                "url": song_url
                            })
                        else: logging.warning(f"[FilterMetadata-Playlist:{primary_item.get('id')}] - 跳过音轨索引 {index}，缺少关键信息 (ID: {song_id}, Name: {song_name})。")
                    else: logging.warning(f"[FilterMetadata-Playlist:{primary_item.get('id')}] - 跳过无效或非歌曲类型的音轨条目索引 {index}: 类型 '{song_item.get('type', 'N/A')}'")
                 filtered["trackCount"] = len(filtered["tracks"])
             else: logging.warning(f"[FilterMetadata-Playlist:{primary_item.get('id')}] - 未找到有效的音轨关系数据。")

        # --- 处理单曲 ---
        elif link_type == "song":
             # 从relationships中获取专辑信息
             albums_relationship = relationships.get("albums", {})
             albums_data = albums_relationship.get("data", []) if isinstance(albums_relationship, dict) else []
             album_url = None
             if isinstance(albums_data, list) and albums_data:
                 album_attributes = albums_data[0].get("attributes", {})
                 album_url = album_attributes.get("url")
             
             filtered = {
                 "name": attributes.get("name"), 
                 "artistName": attributes.get("artistName"), 
                 "id": primary_item.get("id"), 
                 "hasLyrics": attributes.get("hasLyrics", False), 
                 "artwork_url": artwork_url_template,
                 "album_url": album_url  # 添加专辑链接
             }

        # --- 有效性检查 ---
        if link_type in ["album", "music-video", "playlist", "song"]:
             if not filtered.get("id") or not filtered.get("name"):
                  logging.error(f"过滤后的元数据不完整 (缺少 ID 或 Name): 类型 '{link_type}', Link Info: {link_info}。")
                  return None
        else:
             logging.error(f"未知的链接类型 '{link_type}' 无法过滤。Link Info: {link_info}")
             return None
        logging.info(f"元数据过滤成功: {link_type} {primary_item.get('id')}")
        return filtered
    except (IndexError, KeyError, TypeError, AttributeError) as e:
        logging.error(f"过滤元数据时发生错误 (Link Info: {link_info}): {e}", exc_info=True)
        return None
    except Exception as e:
        logging.error(f"过滤元数据时发生未预料的错误 (Link Info: {link_info}): {e}", exc_info=True)
        return None


# --- 后台任务处理 ---
def process_task_background(task_uuid): # <--- 修改：只接收 UUID
    """后台线程处理单个任务 (使用本地时区)"""
    global TOKEN_MANAGER, CONFIG, TASK_QUEUE_FILEPATH, APP_LOCKS, LOCAL_TZ
    # --- 从队列中获取任务信息 ---
    task_queue_lock = APP_LOCKS["task_queue"]
    link_info = None
    standard_user = None
    link = None
    log_prefix = f"[Task {task_uuid[:8]}]" # 简化日志前缀

    try:
        with task_queue_lock.acquire(timeout=5): # 短暂获取锁读取信息
            current_tasks = read_json_with_lock(TASK_QUEUE_FILEPATH, task_queue_lock, default=[])
            task_data = next((task for task in current_tasks if task.get("uuid") == task_uuid), None)
            if task_data:
                link_info = task_data.get("link_info")
                standard_user = task_data.get("user")
                link = task_data.get("link")
                log_prefix = f"[Task {task_uuid} | {link_info.get('type', 'unknown')} {link_info.get('id', 'unknown')}]" # 更新日志前缀
                logging.info(f"{log_prefix} 后台任务启动。用户: {standard_user}, 链接: {link}")
            else:
                logging.error(f"[Task {task_uuid}] 后台任务启动失败：在队列中未找到对应的任务占位符。")
                return # 找不到任务，无法继续
    except Timeout:
        logging.error(f"[Task {task_uuid}] 后台任务启动失败：获取任务队列锁超时（读取阶段）。")
        return
    except Exception as e:
         logging.error(f"[Task {task_uuid}] 后台任务启动失败：读取任务队列时发生错误: {e}", exc_info=True)
         return

    if not link_info or not standard_user or not link:
         logging.error(f"{log_prefix} 后台任务中止：缺少必要的任务信息（link_info, user, or link）。")
         return # 必要信息缺失

    # --- 处理单曲链接转换 ---
    if link_info.get("type") == "song":
        logging.info(f"{log_prefix} 检测到单曲任务，开始获取元数据以转换为专辑链接...")
        metadata_raw = fetch_metadata(link_info, TOKEN_MANAGER, CONFIG)
        if metadata_raw:
            metadata_filtered = filter_metadata(link_info, metadata_raw)
            if metadata_filtered and metadata_filtered.get("album_url"):
                album_url = metadata_filtered["album_url"]
                logging.info(f"{log_prefix} 成功获取专辑链接: {album_url}")
                
                # 解析新的专辑链接信息
                allowed_storefronts = set(CONFIG.get("storefront_language_map", {}).keys())
                new_link_info = parse_link(album_url, allowed_storefronts)
                if new_link_info:
                    # 检查队列中是否已存在相同的专辑任务
                    try:
                        with task_queue_lock.acquire(timeout=10):
                            current_tasks = read_json_with_lock(TASK_QUEUE_FILEPATH, task_queue_lock, default=[])
                            # 查找是否存在相同用户的相同专辑任务
                            existing_task = next((t for t in current_tasks 
                                                if t.get("user") == standard_user 
                                                and t.get("link_info", {}).get("type") == "album"
                                                and t.get("link_info", {}).get("id") == new_link_info["id"]), None)
                            
                            if existing_task:
                                logging.info(f"{log_prefix} 发现重复的专辑任务 (UUID: {existing_task.get('uuid')})，将删除当前单曲任务。")
                                # 从队列中删除当前单曲任务
                                current_tasks = [t for t in current_tasks if t.get("uuid") != task_uuid]
                                if write_json_with_lock(TASK_QUEUE_FILEPATH, task_queue_lock, current_tasks):
                                    logging.info(f"{log_prefix} 成功删除重复的单曲任务。")
                                    # 通知长轮询等待的请求
                                    QUEUE_NOTIFIER.notify_change()
                                return
                            else:
                                # 更新当前任务为专辑任务
                                for t in current_tasks:
                                    if t.get("uuid") == task_uuid:
                                        t["link"] = album_url
                                        t["link_info"] = new_link_info
                                        break
                                if write_json_with_lock(TASK_QUEUE_FILEPATH, task_queue_lock, current_tasks):
                                    logging.info(f"{log_prefix} 成功将单曲任务更新为专辑任务。")
                                    # 通知长轮询等待的请求
                                    QUEUE_NOTIFIER.notify_change()
                                    link_info = new_link_info
                                    link = album_url
                                else:
                                    logging.error(f"{log_prefix} 更新任务为专辑任务失败。")
                    except Timeout:
                        logging.error(f"{log_prefix} 获取任务队列锁超时（专辑转换阶段）。")
                    except Exception as e:
                        logging.error(f"{log_prefix} 处理专辑转换时发生错误: {e}", exc_info=True)
                else:
                    logging.error(f"{log_prefix} 无法解析转换后的专辑链接: {album_url}")
            else:
                logging.error(f"{log_prefix} 未能从单曲元数据中提取专辑链接。")
        else:
            logging.error(f"{log_prefix} 获取单曲元数据失败，无法进行链接转换。")

    # --- 获取元数据 ---
    metadata_raw = fetch_metadata(link_info, TOKEN_MANAGER, CONFIG)
    status = "error"; metadata_filtered = None
    error_reason = "元数据获取失败" # 默认错误原因
    if metadata_raw:
        logging.info(f"{log_prefix} 原始元数据获取成功，开始过滤...")
        metadata_filtered = filter_metadata(link_info, metadata_raw)
        if metadata_filtered:
            status = "ready"
            error_reason = None # 成功则清除错误原因
            logging.info(f"{log_prefix} 元数据过滤成功。")
        else:
            status = "error"
            error_reason = "元数据过滤失败或格式无效"
            logging.error(f"{log_prefix} 元数据过滤失败。")
    else:
        status = "error"
        error_reason = "元数据获取失败 (API 未返回有效数据)"
        logging.error(f"{log_prefix} 元数据获取失败。")

    # --- 更新任务队列中的条目 ---
    update_success = False
    try:
        # 使用 utils 函数
        with task_queue_lock.acquire(timeout=20):
            current_tasks = read_json_with_lock(TASK_QUEUE_FILEPATH, task_queue_lock, default=None)
            if current_tasks is None:
                logging.error(f"{log_prefix} 更新任务失败：无法读取现有的 task_queue.json。")
                return

            task_found = False
            for i, task in enumerate(current_tasks):
                if task.get("uuid") == task_uuid:
                    # 更新找到的任务
                    current_tasks[i]["status"] = status
                    current_tasks[i]["metadata"] = metadata_filtered
                    if status == "error" and error_reason:
                         current_tasks[i]["error_reason"] = error_reason
                    else:
                         # 如果之前有错误原因，成功后可以清除它
                         current_tasks[i].pop("error_reason", None)
                    task_found = True
                    break # 找到并更新后退出循环

            if not task_found:
                 logging.error(f"{log_prefix} 更新任务失败：在写入阶段未找到任务 UUID {task_uuid}。")
                 return

            # 写回整个列表
            # 使用 utils 函数
            if write_json_with_lock(TASK_QUEUE_FILEPATH, task_queue_lock, current_tasks):
                 logging.info(f"{log_prefix} 任务成功处理并已更新到 task_queue.json。新状态: {status}")
                 update_success = True
                 
                 # 通知长轮询等待的请求
                 QUEUE_NOTIFIER.notify_change()

                 # --- 如果状态更新为 ready，发送 UDP 信号给 main.py ---
                 if status == "ready":
                     signal_port = CONFIG.get('SCHEDULER_SIGNAL_PORT') # 从全局 CONFIG 获取端口
                     if signal_port:
                         try:
                             # 创建临时 UDP 套接字发送信号
                             with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                                 # 发送到 localhost 的指定端口
                                 sock.sendto(b'check_queue', ("localhost", signal_port))
                                 logging.info(f"{log_prefix} 已向主调度器 (localhost:{signal_port}) 发送唤醒信号。")
                         except socket.error as send_err:
                             logging.warning(f"{log_prefix} 发送 UDP 唤醒信号到 localhost:{signal_port} 失败: {send_err}")
                         except Exception as send_e:
                             logging.error(f"{log_prefix} 发送 UDP 唤醒信号时发生未知错误: {send_e}", exc_info=True)
                     else:
                         logging.warning(f"{log_prefix} 未在配置中找到 SCHEDULER_SIGNAL_PORT，无法发送唤醒信号。")
                 # --- 发送信号结束 ---

            else:
                 logging.error(f"{log_prefix} 更新任务失败：无法写回 task_queue.json。")

    except Timeout:
        logging.error(f"{log_prefix} 获取 task_queue 文件锁超时（写入阶段），任务更新失败。")
    except Exception as e:
        logging.error(f"{log_prefix} 在更新任务队列时发生错误: {e}", exc_info=True)

    if not update_success and status == "ready":
         logging.critical(f"{log_prefix} 任务处理成功但更新队列失败！任务状态可能不正确。")


# --- 远程编译触发 (修正后) ---
def trigger_remote_compilation_upload(config):
    """打包 Go 源码，发送到远程服务器编译，并接收二进制文件"""
    compile_url = config.get("compile_server_url")
    source_dir = config.get("go_source_dir")
    # --- 将输出路径相关配置提前检查 ---
    output_dir = config.get("compiled_binary_output_dir")
    output_filename = config.get("compiled_binary_name")
    timeout_seconds = config.get("compile_timeout_seconds", 300)

    if not all([compile_url, source_dir, output_dir, output_filename]):
        logging.error("远程编译配置不完整 (compile_server_url, go_source_dir, compiled_binary_output_dir, compiled_binary_name)。")
        return False

    # --- 在进行任何网络操作前计算最终输出路径 ---
    # 确保目录和文件名是字符串类型再 join
    if not isinstance(output_dir, str) or not isinstance(output_filename, str):
        logging.error("配置中的 compiled_binary_output_dir 或 compiled_binary_name 不是有效的字符串。")
        return False
    output_path = os.path.join(output_dir, output_filename)
    logging.info(f"最终二进制文件将保存到: {output_path}") # 提前打印确认

    if not os.path.isdir(source_dir):
        logging.error(f"本地 Go 源码目录不存在: {source_dir}")
        return False

    # --- 获取目标平台 ---
    target_goos = config.get("target_goos")
    target_goarch = config.get("target_goarch")

    if not target_goos or not target_goarch:
         logging.warning("未在 config.yaml 中配置 target_goos 或 target_goarch，尝试自动检测...")
         system_map = {"linux": "linux", "windows": "windows", "darwin": "darwin"}
         target_goos = system_map.get(platform.system().lower())
         arch_map = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}
         target_goarch = arch_map.get(platform.machine().lower())
         if not target_goos or not target_goarch:
             logging.error("无法自动检测目标平台，且未在 config.yaml 中配置。请配置 target_goos 和 target_goarch。")
             return False
         logging.info(f"自动检测到目标平台: GOOS={target_goos}, GOARCH={target_goarch}")
    else:
        logging.info(f"使用配置的目标平台: GOOS={target_goos}, GOARCH={target_goarch}")

    payload_data = {'target_goos': target_goos, 'target_goarch': target_goarch}

    # --- 源码打包 Zip ---
    zip_buffer = io.BytesIO()
    try:
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(source_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, source_dir)
                    zipf.write(file_path, arcname=arcname)
                    logging.debug(f"已添加 {arcname} 到压缩包")
        zip_buffer.seek(0)
        logging.info(f"源码打包完成 (大小: {zip_buffer.getbuffer().nbytes} bytes)")
    except Exception as e:
        logging.error(f"打包源码时出错: {e}", exc_info=True)
        zip_buffer.close() # 确保关闭
        return False
    # 不要在这里关闭 zip_buffer，发送请求时还需要它

    # --- 发送请求 ---
    logging.info(f"发送源码到编译服务器: {compile_url}")
    files_data = {'source_archive': ('go_source.zip', zip_buffer, 'application/zip')}
    try:
        response = requests.post(compile_url, files=files_data, data=payload_data, timeout=timeout_seconds)

        # --- 处理响应 ---
        if response.status_code == 200:
            logging.info("远程编译成功完成，正在接收二进制文件...")
            try:
                # 再次确保输出目录存在
                # output_dir 应该在函数开始时已经验证过是字符串
                os.makedirs(output_dir, exist_ok=True)

                # --- 现在尝试写入，output_path 应该已在函数开始时定义 ---
                logging.debug(f"准备写入文件到: {output_path}")
                # 检查 output_path 是否有效 (虽然理论上开头已检查，多一步无妨)
                if not output_path:
                     logging.error("内部错误：output_path 变量为空或无效。")
                     return False

                with open(output_path, 'wb') as f:
                    f.write(response.content)
                logging.info(f"编译后的二进制文件已保存到: {output_path}")

                # 设置权限
                if os.name == 'posix':
                    try:
                        os.chmod(output_path, 0o755)
                        logging.info(f"已设置文件可执行权限: {output_path}")
                    except OSError as chmod_err:
                        logging.warning(f"设置可执行权限失败: {chmod_err}")
                return True # <--- 明确返回 True 表示成功

            except OSError as e:
                logging.error(f"保存编译后的二进制文件到 '{output_path}' 时发生 OS 错误: {e}", exc_info=True)
                return False
            except Exception as e:
                 logging.error(f"处理编译成功的响应时发生未知错误 (写入路径: '{output_path}'): {e}", exc_info=True)
                 logging.error(f"发生错误时 output_path 的值是: {output_path}")
                 return False

        else: # 处理编译失败或服务器错误的响应
            logging.error(f"远程编译服务器返回错误状态码: {response.status_code}")
            try:
                error_data = response.json()
                logging.error(f"编译失败详情: {error_data.get('error', '无详细信息')}")
                if 'output' in error_data:
                    logging.error(f"编译输出:\n{error_data['output']}")
            except json.JSONDecodeError:
                logging.error(f"无法解析错误响应 (非 JSON): {response.text[:500]}...")
            return False # 明确返回 False

    except requests.exceptions.Timeout:
        logging.error(f"连接或等待远程编译服务器响应超时 ({timeout_seconds} 秒)。")
        return False
    except requests.exceptions.RequestException as e:
        logging.error(f"调用远程编译服务器时发生网络或HTTP错误: {e}", exc_info=True)
        return False
    except Exception as e:
        logging.error(f"发送编译请求或接收响应时发生未知错误: {e}", exc_info=True)
        return False
    finally:
         zip_buffer.close() # 确保在 finally 中关闭 BytesIO buffer

# --- 速率限制实现 ---
# 认证和速率限制已移至 Nginx 层

# --- Flask 路由 ---
@app.route("/task", methods=["POST"])
def submit_task():
    """接收用户提交的任务，添加占位符，然后启动后台处理"""
    global USERS_DATA, CONFIG, TASK_QUEUE_FILEPATH, APP_LOCKS, LOCAL_TZ
    
    # --- 打印 HTTP 请求头 ---
    logging.info("=" * 60)
    logging.info("收到 POST /task 请求 - HTTP 请求头信息:")
    logging.info("=" * 60)
    
    # 获取所有请求头并格式化打印
    headers_dict = dict(request.headers)
    
    # 打印到日志和控制台
    for header_name, header_value in headers_dict.items():
        header_line = f"{header_name}: {header_value}"
        logging.info(f"  {header_line}")
        print(f"  {header_line}")  # 同时打印到控制台
    
    logging.info("=" * 60)
    print("=" * 60)
    # --- 请求头打印结束 ---
    
    if not request.is_json: return jsonify({"status": "failure", "message": "请求体必须是 JSON 格式。"}), 400
    tasks_in_request = request.json
    if not isinstance(tasks_in_request, list): return jsonify({"status": "failure", "message": "请求体 JSON 必须是一个列表。"}), 400
    logging.info(f"收到 POST /task 请求，包含 {len(tasks_in_request)} 个任务。")

    newly_accepted_tasks = [] # 存储将要添加的占位符任务
    validation_failures = []
    request_task_identifiers_seen = set()
    allowed_storefronts = set(CONFIG.get("storefront_language_map", {}).keys())
    task_queue_lock = APP_LOCKS["task_queue"]
    existing_task_identifiers = set()

    # --- 从 HTTP 头获取默认用户名 ---
    x_user_header = request.headers.get("X-User")
    if x_user_header:
        logging.info(f"检测到 X-User 头: {x_user_header}")
    else:
        logging.error("缺少必需的 X-User 头")
        return jsonify({"status": "failure", "message": "缺少必需的 X-User HTTP 头。"}), 400
    
    # --- 步骤 1: 验证输入并准备占位符任务 ---
    submit_time_iso = datetime.now(LOCAL_TZ).isoformat() # 统一提交时间
    for index, task_input in enumerate(tasks_in_request):
        # 检查基本格式和必需的 link 字段
        if not isinstance(task_input, dict) or "link" not in task_input:
             validation_failures.append({"input_index": index, "input_task": task_input, "reason": "格式错误或缺少链接"}); continue
        
        # 只从 X-User 头获取用户名
        submitted_user = x_user_header
        logging.debug(f"任务 {index}: 从 X-User 头获取用户名: {submitted_user}")
        
        submitted_link = task_input["link"]
        standard_user = normalize_username(submitted_user, USERS_DATA)
        if not standard_user: validation_failures.append({"input_index": index, "input_task": task_input, "reason": "用户无效"}); continue
        
        # 处理链接，去掉 ?i= 参数
        processed_link = submitted_link
        if "/album/" in submitted_link and "?i=" in submitted_link:
            processed_link = submitted_link.split("?i=")[0]
            logging.debug(f"检测到专辑链接包含 ?i= 参数，已规范化: {processed_link}")
        elif "/song/" in submitted_link:
            logging.info(f"检测到单曲链接: {submitted_link}，将在后台处理时转换为专辑链接。")
            # 不再立即获取元数据，直接使用原始链接
            processed_link = submitted_link
            
        link_info = parse_link(processed_link, allowed_storefronts)
        if not link_info: validation_failures.append({"input_index": index, "input_task": task_input, "reason": "链接无效"}); continue
        task_identifier = (standard_user, processed_link)
        if task_identifier in request_task_identifiers_seen: validation_failures.append({"input_index": index, "input_task": task_input, "reason": "请求内重复"}); continue

        request_task_identifiers_seen.add(task_identifier)

        # 生成 UUID 并创建占位符
        task_id = str(uuid.uuid4())
        placeholder_task = {
            "uuid": task_id,
            "user": standard_user,
            "link": processed_link,  # 使用处理后的链接
            "link_info": link_info,
            "status": "pending_meta", # 初始状态
            "metadata": None,
            "submit_time": submit_time_iso,
            "order_index": index, # 保留原始顺序
            "skip_check": task_input.get("skip_check", False) # 添加 skip_check 参数，默认为 False
        }
        newly_accepted_tasks.append(placeholder_task)

    # --- 步骤 2: 检查队列中是否已存在，并添加占位符 ---
    tasks_to_start_processing = [] # 实际需要启动后台线程的任务
    if newly_accepted_tasks:
        try:
            with task_queue_lock.acquire(timeout=10): # 需要足够时间读写
                current_tasks = read_json_with_lock(TASK_QUEUE_FILEPATH, task_queue_lock, default=None)
                if current_tasks is None:
                     logging.error("添加任务失败：无法读取现有的 task_queue.json。")
                     for task in newly_accepted_tasks:
                         validation_failures.append({"input_index": task['order_index'], "input_task": {"user": task['user'], "link": task['link']}, "reason": "服务器内部错误(队列读失败)"})
                     newly_accepted_tasks.clear()
                     raise Exception("无法读取任务队列")

                existing_task_identifiers = set((t.get('user'), t.get('link')) for t in current_tasks if t.get('user') and t.get('link'))

                added_count = 0
                for placeholder in newly_accepted_tasks:
                    task_identifier = (placeholder['user'], placeholder['link'])
                    if task_identifier in existing_task_identifiers:
                        validation_failures.append({"input_index": placeholder['order_index'], "input_task": {"user": placeholder['user'], "link": placeholder['link']}, "reason": "队列中已存在"})
                    else:
                        current_tasks.append(placeholder) # 按顺序追加到列表末尾
                        tasks_to_start_processing.append(placeholder['uuid']) # 记录需要启动线程的 UUID
                        added_count += 1

                # 如果有新任务添加，则写回文件
                if added_count > 0:
                    # 使用 utils 函数
                    if write_json_with_lock(TASK_QUEUE_FILEPATH, task_queue_lock, current_tasks):
                         logging.info(f"成功将 {added_count} 个新任务占位符写入 task_queue.json。")
                         # 通知长轮询等待的请求
                         QUEUE_NOTIFIER.notify_change()
                    else:
                         logging.error("写入任务占位符到 task_queue.json 失败！")
                         # 标记刚才添加的任务为失败
                         for task_uuid_to_fail in tasks_to_start_processing:
                              task_data_orig = next((t for t in newly_accepted_tasks if t['uuid'] == task_uuid_to_fail), None)
                              if task_data_orig:
                                   validation_failures.append({"input_index": task_data_orig['order_index'], "input_task": {"user": task_data_orig['user'], "link": task_data_orig['link']}, "reason": "服务器内部错误(队列写失败)"})
                         tasks_to_start_processing.clear() # 清空，因为写入失败

        except Timeout:
            logging.error("获取任务队列锁超时，无法添加新任务。")
            # 标记所有尝试添加的任务为失败
            for task in newly_accepted_tasks:
                 validation_failures.append({"input_index": task['order_index'], "input_task": {"user": task['user'], "link": task['link']}, "reason": "服务器内部错误(队列锁超时)"})
            tasks_to_start_processing.clear() # 清空
        except Exception as e:
             logging.error(f"添加任务到队列时发生错误: {e}", exc_info=True)
             # 标记所有尝试添加的任务为失败 (除非已在上面处理)
             if not tasks_to_start_processing: # 如果上面已经处理过失败，这里就不再重复添加失败记录
                 pass
             else:
                  for task in newly_accepted_tasks:
                       # 避免重复添加失败记录
                       if not any(vf['input_index'] == task['order_index'] for vf in validation_failures):
                            validation_failures.append({"input_index": task['order_index'], "input_task": {"user": task['user'], "link": task['link']}, "reason": "服务器内部错误(队列处理异常)"})
                  tasks_to_start_processing.clear() # 清空


    # --- 步骤 3: 启动后台线程 ---
    threads = []
    for task_uuid in tasks_to_start_processing:
        thread = threading.Thread(target=process_task_background, args=(task_uuid,), name=f"TaskWorker-{task_uuid[:8]}")
        thread.start(); threads.append(thread)
    logging.info(f"已为 {len(threads)} 个新任务启动后台处理线程。")

    # --- 步骤 4: 准备并返回响应 ---
    accepted_count = len(tasks_to_start_processing)
    # 重新计算失败数，基于最终的 validation_failures 列表
    failed_count = len(validation_failures)
    failure_summary = Counter(f['reason'] for f in validation_failures)

    # 确定最终状态和消息
    if accepted_count > 0 and failed_count == 0:
        status = "success"
        message = f"成功接受 {accepted_count} 个任务。"
    elif accepted_count > 0 and failed_count > 0:
        status = "partial_success"
        message = f"接受 {accepted_count} 个任务，{failed_count} 个被拒绝。"
    elif accepted_count == 0 and failed_count > 0:
        status = "failure"
        # 从 validation_failures 中统计原始请求数
        original_request_count = len(set(vf['input_index'] for vf in validation_failures))
        message = f"所有 {original_request_count} 个任务均未能通过验证或添加。"
    elif accepted_count == 0 and failed_count == 0 and len(tasks_in_request) == 0: # 处理空请求的情况
         status = "success"
         message = "请求为空，未处理任何任务。"
         failed_count = 0
    else: # 接受0，失败0，但请求不为空 (例如全部是请求内重复，但没进队列检查失败) - 修正逻辑
         status = "failure" # 算作失败，因为没有任务被接受
         message = f"所有 {len(tasks_in_request)} 个任务均未能通过验证或添加。"
         failed_count = len(tasks_in_request) # 失败数应等于请求数

    response_data = {"status": status, "message": message, "accepted_count": accepted_count, "failed_count": failed_count, "failure_summary": dict(failure_summary) if failure_summary else {}}
    return jsonify(response_data), 200


@app.route("/task", methods=["GET"])
def get_tasks():
    """获取任务队列中的所有任务，支持长轮询"""
    global TASK_QUEUE_FILEPATH, QUEUE_NOTIFIER
    
    # 获取长轮询参数
    wait = request.args.get('wait', 'false').lower() == 'true'
    timeout = min(int(request.args.get('timeout', '30')), 60)  # 最大60秒超时
    
    def read_current_tasks():
        """读取当前任务列表"""
        try:
            if not os.path.exists(TASK_QUEUE_FILEPATH):
                return []
            with open(TASK_QUEUE_FILEPATH, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    return []
                return json.loads(content)
        except json.JSONDecodeError as e:
            logging.error(f"GET /task: 解析任务队列 JSON 文件失败: {e}")
            return None  # 表示错误
        except Exception as e:
            logging.error(f"GET /task: 读取任务队列文件时出错: {e}", exc_info=True)
            return None  # 表示错误
    
    # 首次读取任务
    tasks = read_current_tasks()
    if tasks is None:
        return jsonify({"error": "无法读取任务队列。"}), 500
    
    # 如果不启用长轮询，或者有任务，直接返回
    if not wait or len(tasks) > 0:
        logging.info(f"GET /task: 返回 {len(tasks)} 个任务 (长轮询: {wait})")
        return jsonify(tasks)
    
    # 启用长轮询且当前无任务，等待变化
    logging.info(f"GET /task: 启用长轮询，等待最多 {timeout} 秒...")
    
    start_time = time.time()
    has_change = QUEUE_NOTIFIER.wait_for_change(timeout)
    wait_time = time.time() - start_time
    
    if has_change:
        # 有变化，重新读取任务
        tasks = read_current_tasks()
        if tasks is None:
            return jsonify({"error": "无法读取任务队列。"}), 500
        logging.info(f"GET /task: 长轮询检测到变化，等待 {wait_time:.1f}s，返回 {len(tasks)} 个任务")
        return jsonify(tasks)
    else:
        # 超时，返回空列表
        logging.info(f"GET /task: 长轮询超时 ({wait_time:.1f}s)，返回空列表")
        return jsonify([])

@app.route("/token", methods=["GET"])
def get_api_token():
    """获取当前有效的 API Token，如果即将过期则主动刷新"""
    global TOKEN_MANAGER
    try:
        # 检查当前 Token 状态
        current_token = TOKEN_MANAGER.token
        current_timestamp = TOKEN_MANAGER.timestamp
        
        # 计算当前 Token 的剩余有效期
        if current_timestamp:
            expiry_time = current_timestamp + timedelta(hours=CONFIG["token_validity_hours"])
            remaining_time = expiry_time - datetime.now(TOKEN_MANAGER.local_tz)
            remaining_seconds = int(remaining_time.total_seconds())
            
            # 如果剩余时间小于 30 分钟，主动刷新 Token
            if remaining_seconds < 1800:  # 30分钟 = 1800秒
                logging.info(f"当前 Token 剩余 {remaining_seconds} 秒，小于 30 分钟，主动刷新...")
                new_token = TOKEN_MANAGER._refresh_token()
                if new_token:
                    current_token = new_token
                    current_timestamp = TOKEN_MANAGER.timestamp
                    expiry_time = current_timestamp + timedelta(hours=CONFIG["token_validity_hours"])
                    remaining_time = expiry_time - datetime.now(TOKEN_MANAGER.local_tz)
                    remaining_seconds = int(remaining_time.total_seconds())
                    logging.info(f"Token 刷新成功，新 Token 有效期至: {expiry_time.isoformat()}")
                else:
                    logging.error("Token 刷新失败，将使用当前 Token")
        else:
            # 如果没有当前 Token，强制获取新 Token
            logging.info("没有有效的 Token，开始获取新 Token...")
            new_token = TOKEN_MANAGER._refresh_token()
            if new_token:
                current_token = new_token
                current_timestamp = TOKEN_MANAGER.timestamp
                expiry_time = current_timestamp + timedelta(hours=CONFIG["token_validity_hours"])
                remaining_time = expiry_time - datetime.now(TOKEN_MANAGER.local_tz)
                remaining_seconds = int(remaining_time.total_seconds())
                logging.info(f"成功获取新 Token，有效期至: {expiry_time.isoformat()}")
            else:
                logging.error("无法获取有效的 Token")
                return jsonify({"error": "无法获取有效的 API Token"}), 500

        if not current_token:
            logging.error("最终检查：Token 为空")
            return jsonify({"error": "无法获取有效的 API Token"}), 500

        # 再次确认 Token 有效性
        if not TOKEN_MANAGER._is_valid():
            logging.error("最终检查：Token 已失效")
            return jsonify({"error": "无法获取有效的 API Token"}), 500

        response_data = {
            "token": current_token,
            "expires_in": remaining_seconds,
            "expires_at": expiry_time.isoformat() if current_timestamp else None,
            "storefront": "cn",  # 添加默认 storefront
            "language": CONFIG["storefront_language_map"]["cn"]  # 添加对应的语言代码
        }
        
        logging.info(f"成功返回 API Token，剩余有效期: {remaining_seconds} 秒")
        return jsonify(response_data)
        
    except Exception as e:
        logging.error(f"获取 API Token 时发生错误: {e}", exc_info=True)
        return jsonify({"error": "服务器内部错误"}), 500

@app.route("/user/avatar", methods=["GET"])
def get_user_avatar():
    """根据传入的用户名返回标准用户名和头像链接"""
    global USERS_DATA
    
    # 从查询参数获取用户名
    submitted_username = request.args.get("username")
    if not submitted_username:
        return jsonify({"status": "failure", "message": "缺少必需的查询参数 'username'。"}), 400
    
    if not isinstance(submitted_username, str) or not submitted_username.strip():
        return jsonify({"status": "failure", "message": "用户名必须是非空字符串。"}), 400
    
    submitted_username = submitted_username.strip()
    logging.info(f"收到用户头像查询请求，用户名: {submitted_username}")
    
    try:
        # 使用现有的 normalize_username 函数标准化用户名
        standard_username = normalize_username(submitted_username, USERS_DATA)
        
        if not standard_username:
            logging.warning(f"用户头像查询失败：未找到用户 '{submitted_username}'")
            return jsonify({
                "status": "failure", 
                "message": f"未找到用户: {submitted_username}"
            }), 404
        
        # 获取用户配置
        user_config = USERS_DATA.get(standard_username, {})
        avatar_url = user_config.get("avatar")
        
        if not avatar_url:
            logging.warning(f"用户 '{standard_username}' 未配置头像链接")
            return jsonify({
                "status": "failure",
                "message": f"用户 {standard_username} 未配置头像链接"
            }), 404
        
        # 返回成功响应
        response_data = {
            "status": "success",
            "standard_username": standard_username,
            "avatar_url": avatar_url
        }
        
        logging.info(f"成功返回用户头像信息: {standard_username} -> {avatar_url}")
        return jsonify(response_data), 200
        
    except Exception as e:
        logging.error(f"查询用户头像时发生错误: {e}", exc_info=True)
        return jsonify({"status": "failure", "message": "服务器内部错误"}), 500

@app.route("/search", methods=["GET"])
def proxy_search():
    """代理搜索请求到Apple Music API"""
    global TOKEN_MANAGER, CONFIG, SEARCH_CACHE_MANAGER
    
    # 获取搜索地区代码，默认为cn
    storefront = request.headers.get("X-Storefront", "cn")
    if storefront not in CONFIG["storefront_language_map"]:
        logging.warning(f"收到无效的搜索地区代码: {storefront}，将使用默认值: cn")
        storefront = "cn"
    
    # 获取原始查询参数
    query_params = request.args.to_dict()
    
    # 检查是否使用缓存 (默认为true)
    use_cache = request.headers.get("X-Use-Cache", "true").lower() in ["true", "1", "yes"]
    
    # 检查缓存 (仅在启用缓存时)
    if use_cache and SEARCH_CACHE_MANAGER:
        cached_result = SEARCH_CACHE_MANAGER.get_cached_result(storefront, query_params)
        if cached_result:
            logging.info(f"搜索缓存命中，跳过API请求: {request.query_string.decode()}")
            return Response(
                json.dumps(cached_result, ensure_ascii=False, separators=(',', ':')),
                status=200,
                mimetype='application/json'
            )
    elif not use_cache:
        logging.info(f"请求头指定不使用缓存: X-Use-Cache={request.headers.get('X-Use-Cache')}")
    
    # 获取当前有效的API Token
    token = TOKEN_MANAGER.get_token()
    if not token:
        logging.error("无法获取有效的API Token")
        return jsonify({"error": "无法获取有效的API Token"}), 500
    
    # 构建目标URL
    base_url = CONFIG["apple_music_api_base_url"].rstrip('/')
    target_url = f"{base_url}/{storefront}/search"
    
    # 设置请求头
    headers = {
        "User-Agent": CONFIG["user_agent"],
        "Origin": CONFIG["token_fetch_url"],
        "Authorization": f"Bearer {token}"
    }
    
    try:
        # 发送请求到Apple Music API
        response = requests.get(
            target_url,
            params=query_params,
            headers=headers,
            timeout=20
        )
        
        # 检查响应状态
        if response.status_code == 200:
            # 确定缓存状态
            cache_status = "miss" if use_cache else "bypassed"
            
            logging.info(f"搜索请求成功 (缓存状态: {cache_status}): {target_url}?{request.query_string.decode()}")
            
            # 解析响应JSON用于缓存
            try:
                response_data = response.json()
                
                # 缓存结果 (即使不使用缓存读取，仍然可以写入缓存供后续使用)
                if SEARCH_CACHE_MANAGER:
                    SEARCH_CACHE_MANAGER.cache_result(storefront, query_params, response_data)
                
                return Response(
                    json.dumps(response_data, ensure_ascii=False, separators=(',', ':')),
                    status=200,
                    mimetype='application/json'
                )
            except json.JSONDecodeError:
                # 如果JSON解析失败，直接返回原始响应
                logging.warning("搜索响应JSON解析失败，直接返回原始响应")
                return Response(
                    response.content,
                    status=200,
                    mimetype='application/json'
                )
                
        elif response.status_code in [401, 403]:
            logging.warning(f"API返回{response.status_code}（Token失效?），作废当前Token并准备重试...")
            TOKEN_MANAGER.invalidate_token()
            return jsonify({"error": "API Token已失效，请重试"}), 401
        else:
            logging.error(f"搜索请求失败: HTTP {response.status_code}")
            return jsonify({"error": f"搜索请求失败: HTTP {response.status_code}"}), response.status_code
            
    except requests.exceptions.Timeout:
        logging.error("搜索请求超时")
        return jsonify({"error": "搜索请求超时"}), 504
    except requests.exceptions.RequestException as e:
        logging.error(f"搜索请求时发生网络错误: {e}")
        return jsonify({"error": "搜索请求时发生网络错误"}), 500
    except Exception as e:
        logging.error(f"搜索请求时发生未知错误: {e}", exc_info=True)
        return jsonify({"error": "服务器内部错误"}), 500

# --- 应用初始化 ---
def initialize_app():
    """加载配置、设置日志、初始化 Token 管理器、检测本地时区"""
    global CONFIG, USERS_DATA, TOKEN_MANAGER, SEARCH_CACHE_MANAGER, LOCAL_TZ
    global TASK_QUEUE_FILEPATH, TASK_QUEUE_LOCK_FILEPATH
    global API_TOKEN_LOCK_FILEPATH, APP_LOCKS

    # 1. 加载配置
    config_path = os.path.join(SERVER_DIR, "config", "config.yaml")
    try:
        with open(config_path, 'r', encoding='utf-8') as f_cfg:
            CONFIG = yaml.safe_load(f_cfg)
            if not isinstance(CONFIG, dict):
                 print(f"CRITICAL: Backend - Config file {config_path} is not a valid dictionary.", flush=True); exit(1)
    except FileNotFoundError: print(f"CRITICAL: Backend - Config file not found: {config_path}", flush=True); exit(1)
    except yaml.YAMLError as e: print(f"CRITICAL: Backend - Error parsing config file {config_path}: {e}", flush=True); exit(1)
    except Exception as e: print(f"CRITICAL: Backend - Error loading config file {config_path}: {e}", flush=True); exit(1)

    # 2. 设置日志 (使用 utils 函数，并传递中文名)
    # 确保在 validate_config 之前调用，因为 validate_config 内部会使用 logging
    setup_logging(CONFIG, script_chinese_name="后端服务器")
    
    # 校验配置 (现在可以安全使用 logging)
    validate_config(CONFIG)
    
    # 3. 检测本地时区
    try:
        # 优先使用 ZoneInfo (Python 3.9+)
        if ZoneInfo:
            try:
                tz_name = tzlocal.get_localzone_name()
                LOCAL_TZ = ZoneInfo(tz_name)
                logging.info(f"成功检测到本地时区 (zoneinfo): {LOCAL_TZ.key}")
            except (ZoneInfoNotFoundError, Exception) as zie:
                 logging.warning(f"使用 zoneinfo 检测本地时区失败: {zie}。尝试 tzlocal 直接返回对象...")
                 # 如果 zoneinfo 查找失败，尝试 tzlocal 直接返回的对象，并检查类型
                 local_tz_obj = tzlocal.get_localzone()
                 if hasattr(local_tz_obj, 'key') or hasattr(local_tz_obj, 'zone'): # 检查是否是 zoneinfo 或 pytz 风格的对象
                     LOCAL_TZ = local_tz_obj # 接受 tzlocal 返回的对象
                     logging.info(f"成功使用 tzlocal 获取本地时区对象: {getattr(local_tz_obj, 'key', getattr(local_tz_obj, 'zone', 'Unknown Name'))}")
                 else:
                      raise Exception("tzlocal 未能返回可识别的时区对象。")
        else: # Python < 3.9 无原生 zoneinfo
             local_tz_obj = tzlocal.get_localzone()
             # 这里需要依赖 pytz 或其他库，或者只记录警告
             if hasattr(local_tz_obj, 'zone'): # 检查 pytz 风格
                 LOCAL_TZ = local_tz_obj
                 logging.info(f"成功获取本地时区对象 (pytz?): {LOCAL_TZ.zone}")
             else:
                logging.warning("Python < 3.9 且未找到合适的时区对象。将回退到 UTC。")
                raise Exception("时区对象不兼容。")

    except Exception as e:
        logging.warning(f"无法自动检测本地时区: {e}。将使用 UTC 作为时区。请确保宿主机时区文件已挂载或 tzdata 已安装。", exc_info=False) # 减少噪点
        LOCAL_TZ = timezone.utc
    logging.info(f"应用程序将使用时区: {getattr(LOCAL_TZ, 'key', getattr(LOCAL_TZ, 'zone', 'UTC'))}")

    # 4. 加载用户数据
    users_path = CONFIG["users_file_path"]
    USERS_DATA = read_yaml_with_lock(users_path, FileLock(users_path + ".lock", timeout=1))

    if not isinstance(USERS_DATA, dict):
        logging.critical(f"用户文件 {users_path} 格式错误或无法读取。服务器无法启动。")
        exit(1)
    logging.info(f"成功加载 {len(USERS_DATA)} 个用户配置。")
    
    # 5. 初始化搜索缓存管理器
    try:
        SEARCH_CACHE_MANAGER = SearchCacheManager(CONFIG)
    except Exception as e:
        logging.error(f"初始化搜索缓存管理器失败: {e}，将禁用缓存功能。", exc_info=True)
        SEARCH_CACHE_MANAGER = None
    
    # 6. 初始化文件锁
    TASK_QUEUE_FILEPATH = CONFIG["task_queue_file_path"]; TASK_QUEUE_LOCK_FILEPATH = TASK_QUEUE_FILEPATH + ".lock"
    token_file_path = CONFIG["token_file_path"]; API_TOKEN_LOCK_FILEPATH = token_file_path + ".lock"
    APP_LOCKS["task_queue"] = FileLock(TASK_QUEUE_LOCK_FILEPATH, timeout=1)
    APP_LOCKS["api_token"] = FileLock(API_TOKEN_LOCK_FILEPATH, timeout=1)
    logging.info(f"任务队列文件: {TASK_QUEUE_FILEPATH}"); logging.info(f"任务队列锁: {TASK_QUEUE_LOCK_FILEPATH}")
    logging.info(f"API Token 文件: {token_file_path}"); logging.info(f"API Token 锁: {API_TOKEN_LOCK_FILEPATH}")
    os.makedirs(os.path.dirname(TASK_QUEUE_LOCK_FILEPATH), exist_ok=True)
    os.makedirs(os.path.dirname(API_TOKEN_LOCK_FILEPATH), exist_ok=True)
    # 7. 初始化 Token Manager
    try:
        TOKEN_MANAGER = ApiTokenManager(CONFIG)
    except Exception as e: logging.critical(f"初始化 API Token 管理器失败: {e}。", exc_info=True); exit(1)
    logging.info("Flask 应用初始化完成。")


# --- 主程序入口 ---
if __name__ == "__main__":
    initialize_app() # 先完成所有本地初始化

    # 检查是否需要触发远程编译
    should_trigger_compile = CONFIG.get("trigger_compile_on_startup", True)
    binary_path = os.path.join(CONFIG["compiled_binary_output_dir"], CONFIG["compiled_binary_name"])
    
    if should_trigger_compile:
        logging.info("配置要求启动时触发远程编译，开始编译过程...")
        if not trigger_remote_compilation_upload(CONFIG):
            logging.critical("远程编译失败或无法与编译服务器通信，后端服务启动中止。")
            exit(1) # 编译失败则退出
        logging.info("远程编译成功，准备启动 Flask 服务器...")
    else:
        logging.info("配置要求不触发远程编译，检查本地二进制文件...")
        if not os.path.exists(binary_path):
            logging.critical(f"本地二进制文件不存在: {binary_path}，且配置禁止自动编译。服务启动中止。")
            exit(1)
        if not os.access(binary_path, os.X_OK):
            logging.warning(f"本地二进制文件 {binary_path} 不可执行，尝试设置执行权限...")
            try:
                os.chmod(binary_path, 0o755)
                logging.info("已设置二进制文件执行权限。")
            except OSError as e:
                logging.critical(f"无法设置二进制文件执行权限: {e}。服务启动中止。")
                exit(1)
        logging.info("本地二进制文件检查通过，准备启动 Flask 服务器...")

    # 启动 Flask 应用
    is_debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=5000, debug=is_debug, use_reloader=False, threaded=True)