# -*- coding: utf-8 -*- # 指定编码

import yaml
import json
import subprocess
import time
import os
import threading
import logging
import re
import traceback
import filelock
from filelock import Timeout # <-- 添加 Timeout 导入
import sys
import socket
import select
import concurrent.futures
# import random # 移除未使用的导入
from datetime import datetime, timezone
from flask import Flask, Response, request, jsonify
from queue import Queue, Empty
import uuid as uuid_lib
import queue
import requests

# --- 导入通知模块 --- #
from notifications import (
    send_emby_refresh,
    send_bark_notification,
    send_summary_email
)
# --- 导入共享工具 --- #
from utils import (
    PROJECT_ROOT, DEFAULT_PATHS_RELATIVE_TO_ROOT, # 路径常量
    read_json_with_lock, write_json_with_lock,   # JSON 读写
    read_yaml_with_lock,                          # YAML 读写
    resolve_paths,                                # 路径解析
    get_task_display_info,                       # 任务助手
    setup_logging # Import setup_logging from utils
)

# --- 创建Flask应用，用于SSE服务 --- #
app = Flask(__name__)

# --- 存储任务进度信息的字典 --- #
# 格式: {uuid: {song_id: {"current": bytes, "total": bytes, "percent": float}}}
task_progress = {}
task_progress_lock = threading.Lock()

# --- SSE客户端连接队列，按任务UUID索引 --- #
sse_clients = {}
sse_clients_lock = threading.Lock()

# --- 新增：通知SSE客户端连接队列 --- #
notice_clients = []
notice_clients_lock = threading.Lock()

# --- SSE连接数限制 --- #
current_sse_connections = 0
max_sse_connections = 50  # 默认值，将从配置中读取
sse_connection_count_lock = threading.Lock()

# --- SSE端点函数 --- #
@app.route('/api/progress/<uuid>', methods=['GET'])
def stream_progress(uuid):
    """为特定任务创建SSE流，实时推送下载进度"""
    global current_sse_connections, max_sse_connections
    
    # 检查连接数限制
    with sse_connection_count_lock:
        if current_sse_connections >= max_sse_connections:
            logging.warning(f"SSE连接数已达限制 ({current_sse_connections}/{max_sse_connections})，拒绝新连接 (任务: {uuid})")
            return Response(
                "SSE连接数已达最大限制",
                status=503,
                headers={"Retry-After": "10"}
            )
        current_sse_connections += 1
        logging.info(f"新SSE连接建立，当前连接数: {current_sse_connections}/{max_sse_connections} (任务: {uuid})")
    
    def generate(task_uuid):
        global current_sse_connections, max_sse_connections
        client_id = None
        try:
            # 为这个客户端创建一个消息队列
            client_queue = Queue()
            
            # 注册这个客户端的队列
            with sse_clients_lock:
                if task_uuid not in sse_clients:
                    sse_clients[task_uuid] = []
                client_id = str(uuid_lib.uuid4())
                sse_clients[task_uuid].append((client_id, client_queue))
            
            # 立即发送当前进度（如果有）
            with task_progress_lock:
                if task_uuid in task_progress:
                    for song_id, progress_data in task_progress[task_uuid].items():
                        progress_json = json.dumps({
                            "song_id": song_id,
                            "progress": progress_data
                        })
                        yield f"data: {progress_json}\n\n"
            
            # 发送一个初始连接成功事件
            yield f"data: {json.dumps({'event': 'connected', 'uuid': task_uuid})}\n\n"
            
            # 持续从队列获取新的进度更新并发送
            while True:
                try:
                    # 不阻塞，定期检查客户端是否断开连接
                    message = client_queue.get(timeout=1)
                    yield f"data: {message}\n\n"
                except Empty:
                    # 发送心跳保持连接
                    yield f": heartbeat\n\n"
                    
        except GeneratorExit:
            # 客户端断开连接，清理资源
            logging.info(f"SSE 客户端 {client_id} 断开与任务 {task_uuid} 的连接")
        finally:
            # 清理客户端队列
            if client_id:
                with sse_clients_lock:
                    if task_uuid in sse_clients:
                        sse_clients[task_uuid] = [(cid, q) for cid, q in sse_clients[task_uuid] if cid != client_id]
                        if not sse_clients[task_uuid]:
                            del sse_clients[task_uuid]
            
            # 减少连接计数
            with sse_connection_count_lock:
                current_sse_connections -= 1
                logging.info(f"SSE连接断开，当前连接数: {current_sse_connections}/{max_sse_connections} (任务: {task_uuid})")
    
    # 设置SSE响应头
    return Response(generate(uuid), mimetype="text/event-stream", 
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

# --- 新增：通知SSE端点 --- #
@app.route('/api/progress/notice', methods=['GET'])
def stream_notice():
    """为前端创建通知SSE流，实时推送任务完成通知"""
    global current_sse_connections, max_sse_connections, notice_clients
    
    # 检查连接数限制
    with sse_connection_count_lock:
        if current_sse_connections >= max_sse_connections:
            logging.warning(f"通知SSE连接数已达限制 ({current_sse_connections}/{max_sse_connections})，拒绝新连接")
            return Response(
                "SSE连接数已达最大限制",
                status=503,
                headers={"Retry-After": "10"}
            )
        current_sse_connections += 1
        logging.info(f"新通知SSE连接建立，当前连接数: {current_sse_connections}/{max_sse_connections}")
    
    def generate_notice():
        global current_sse_connections, notice_clients
        client_id = None
        try:
            # 为这个客户端创建一个消息队列
            client_queue = Queue()
            
            # 注册这个客户端的队列
            with notice_clients_lock:
                client_id = str(uuid_lib.uuid4())
                notice_clients.append((client_id, client_queue))
                logging.info(f"通知客户端 {client_id} 已注册，当前通知客户端数: {len(notice_clients)}")
            
            # 发送一个初始连接成功事件
            yield f"data: {json.dumps({'event': 'connected', 'type': 'notice'})}\n\n"
            
            # 持续从队列获取新的通知并发送
            while True:
                try:
                    # 不阻塞，定期检查客户端是否断开连接
                    message = client_queue.get(timeout=1)
                    yield f"data: {message}\n\n"
                except Empty:
                    # 发送心跳保持连接
                    yield f": heartbeat\n\n"
                    
        except GeneratorExit:
            # 客户端断开连接，清理资源
            logging.info(f"通知SSE客户端 {client_id} 断开连接")
        finally:
            # 清理客户端队列
            if client_id:
                with notice_clients_lock:
                    notice_clients = [(cid, q) for cid, q in notice_clients if cid != client_id]
                    logging.info(f"通知客户端 {client_id} 已移除，当前通知客户端数: {len(notice_clients)}")
            
            # 减少连接计数
            with sse_connection_count_lock:
                current_sse_connections -= 1
                logging.info(f"通知SSE连接断开，当前连接数: {current_sse_connections}/{max_sse_connections}")
    
    # 设置SSE响应头
    return Response(generate_notice(), mimetype="text/event-stream", 
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.route('/api/sse/status', methods=['GET'])
def sse_status():
    """获取SSE连接状态信息"""
    global current_sse_connections, max_sse_connections
    
    with sse_connection_count_lock:
        current_count = current_sse_connections
    
    with sse_clients_lock:
        # 统计每个任务的连接数
        task_connections = {}
        total_clients = 0
        for task_uuid, clients in sse_clients.items():
            client_count = len(clients)
            task_connections[task_uuid] = client_count
            total_clients += client_count
    
    with notice_clients_lock:
        notice_client_count = len(notice_clients)
    
    status_info = {
        "current_connections": current_count,
        "max_connections": max_sse_connections,
        "usage_percentage": round((current_count / max_sse_connections) * 100, 2) if max_sse_connections > 0 else 0,
        "task_connections": task_connections,
        "total_task_clients": total_clients,
        "notice_clients": notice_client_count
    }
    
    return jsonify(status_info)

# --- 新增：发送通知消息到所有通知客户端 --- #
def send_notice_to_clients(notice_data):
    """向所有连接的通知客户端发送通知消息"""
    if not notice_data:
        return
    
    message = json.dumps(notice_data)
    sent_count = 0
    failed_count = 0
    
    with notice_clients_lock:
        # 复制列表以避免在迭代时修改
        clients_to_notify = notice_clients.copy()
    
    for client_id, client_queue in clients_to_notify:
        try:
            # 使用非阻塞方式放入队列，避免阻塞主线程
            client_queue.put_nowait(message)
            sent_count += 1
        except queue.Full:
            logging.warning(f"通知客户端 {client_id} 队列已满，跳过此次通知")
            failed_count += 1
        except Exception as e:
            logging.error(f"向通知客户端 {client_id} 发送消息时发生错误: {e}")
            failed_count += 1
    
    if sent_count > 0:
        logging.info(f"通知已发送到 {sent_count} 个客户端，失败 {failed_count} 个")
    else:
        logging.debug("当前无通知客户端连接，跳过通知发送")

# --- 启动Flask服务器的函数 --- #
def start_sse_server():
    """在后台线程中启动Flask SSE服务器"""
    global max_sse_connections
    
    # 从配置中读取SSE设置
    sse_config = config_data.get('sse', {})
    sse_port = sse_config.get('port', 5001)
    max_sse_connections = sse_config.get('max_connections', 50)
    
    logging.info(f"SSE服务器配置: 端口={sse_port}, 最大连接数={max_sse_connections}")
    
    threading.Thread(target=lambda: app.run(
        host='0.0.0.0', 
        port=sse_port,
        threaded=True, 
        debug=False,
        use_reloader=False
    ), daemon=True).start()
    logging.info(f"SSE进度推送服务器已在端口{sse_port}启动，最大连接数限制: {max_sse_connections}")


# --- 确定脚本自身位置和项目根目录 --- #
# SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__)) # 使用 utils.PROJECT_ROOT
# PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)


# --- 日志配置 (移除自定义，将在 load_config_and_paths 中调用 utils.setup_logging) ---
# logger = logging.getLogger() # 会被 utils.setup_logging 配置
# formatter = logging.Formatter(...) # 会被 utils.setup_logging 配置
# console_handler = logging.StreamHandler(sys.stdout) # 会被 utils.setup_logging 配置
# ... (其他自定义日志代码移除)

# --- Go 输出的模式匹配 ---
# (保持不变)
WARNING_PATTERN = re.compile(r'W:(\d+)')
ERROR_PATTERN = re.compile(r'E:(\d+)')
GO_TOKEN_FAILURE_STRING = "Detected token failure"
GO_GET_EOF_PATTERN = re.compile(r'Get .*? EOF')
# --- 修改 TRACK_PROGRESS_PATTERN 以捕获 song_id (假设格式如 Track <song_id>:) ---
# TRACK_PROGRESS_PATTERN = re.compile(r'^Track (\\d+) of (\\d+):?\\s*$', re.IGNORECASE)
# 假设 song_id 是字母、数字、点、下划线、连字符的组合
TRACK_PROGRESS_PATTERN = re.compile(r'^Track\s+([\w.-]+):?\s*$', re.IGNORECASE)
# 添加进度信息匹配模式
DOWNLOAD_PROGRESS_PATTERN = re.compile(r'^DL_PROGRESS:(\d+)/(\d+)$')
DOWNLOADED_PATTERN = re.compile(r'^\s*Downloaded\s*$', re.IGNORECASE)
DECRYPTED_PATTERN = re.compile(r'^\s*Decrypted\s*$', re.IGNORECASE)
LYRICS_FAILURE_PATTERN = re.compile(r'^\s*Failed to get lyrics\s*$', re.IGNORECASE)
AUDIO_QUALITY_PATTERN = re.compile(r'^\s*(\d+)-bit / (\d+)\s+Hz\s*$', re.IGNORECASE)
TRACK_EXISTS_PATTERN = re.compile(r'^\s*Track already exists locally\.\s*$', re.IGNORECASE)
GO_CONNECT_ERROR_PATTERN = re.compile(r'^\s*Error connecting to device:', re.IGNORECASE)
GO_FILTER_PATTERNS = [
    re.compile(r'Downloading', re.IGNORECASE),
    re.compile(r'Decrypting', re.IGNORECASE),
    re.compile(r'\d+(\.\d+)?%.*of.*\d+(\.\d+)?.B'),
    re.compile(r'^\s*$'),
    re.compile(r'SPECIFIC_LYRICS_FAILURE:.*$')  # 用于过滤特定歌词获取失败的日志
]


# --- 默认配置值和默认文件路径 --- #
DEFAULT_MAX_PARALLEL = 5
DEFAULT_MAX_RETRIES = 2
DEFAULT_RETRY_DELAY = 10
# DEFAULT_SLEEP_INTERVAL = 5 # 移除未使用的默认值
DEFAULT_SCHEDULER_LONG_POLL_INTERVAL = 60
DEFAULT_SCHEDULER_SIGNAL_PORT = 51234
# DEFAULT_PATHS_RELATIVE_TO_ROOT = { ... } # 移动到 utils.py


# --- 全局变量 ---
config_data = {} # 存储 config.yaml 内容
users_data = {}  # 存储 users.yaml 内容
file_paths = {} # 存储绝对文件路径
file_locks = {} # 存储 filelock 对象

# --- 全局 Go 进程数限制 --- #
max_global_go_processes = 10  # 默认最大并发数，可通过 config.yaml 配置
current_global_go_processes = 0
global_go_processes_lock = threading.Lock()
global_go_processes_condition = threading.Condition(global_go_processes_lock)

# --- 运行中的任务 UUID 集合及其锁 ---
running_task_uuids = set()
running_set_lock = threading.Lock()

# --- 轮询状态管理 ---
poll_interval_lock = threading.Lock()
current_poll_interval = None
fast_poll_mode = False

# --- 校验写入请求队列 ---
check_write_queue = queue.Queue()

# --- 主线程定期处理校验写入请求 ---
def process_check_write_queue():
    while True:
        try:
            req = check_write_queue.get(timeout=0.5)
            if not isinstance(req, dict):
                continue
            uuid = req.get('uuid')
            global_track_number = req.get('global_track_number')
            update_data = req.get('update_data')
            logging.info(f"[主线程] 处理校验写入: uuid={uuid}, global_track_number={global_track_number}, update_data={update_data}")
            update_track_by_global_number_in_file(uuid, global_track_number, update_data)
        except queue.Empty:
            break
        except Exception as e:
            logging.error(f"[主线程] 处理校验写入队列异常: {e}", exc_info=True)


# --- Helper 函数 (read/write yaml/json with lock) --- #
# def read_yaml_with_lock(filepath, lock_obj): ... # 使用 utils.read_yaml_with_lock
# def read_json_with_lock(filepath, lock_obj): ... # 使用 utils.read_json_with_lock
# def write_json_with_lock(filepath, data, lock_obj): ... # 使用 utils.write_json_with_lock


# --- 音轨状态更新函数 ---
def calculate_global_track_number(tracks, disc_number, track_number):
    """计算多光盘专辑中音轨的总体音轨号。"""
    if not tracks or not isinstance(tracks, list):
        logging.warning("calculate_global_track_number: 输入的 tracks 无效或不是列表。")
        return None
    if not isinstance(track_number, int) or track_number <= 0:
        logging.warning(f"calculate_global_track_number: 输入的 track_number 无效: {track_number}")
        return None
    disc_track_counts = {}
    for track in tracks:
        if not isinstance(track, dict):
            continue
        disc_num = track.get('disc_number')
        disc_num = 1 if disc_num is None or not isinstance(disc_num, int) or disc_num <= 0 else disc_num
        disc_track_counts[disc_num] = disc_track_counts.get(disc_num, 0) + 1
    if not disc_track_counts:
        logging.warning("calculate_global_track_number: 未能从 tracks 列表中统计出有效的光盘音轨数。")
        return None
    global_track_number = 0
    target_disc_number = 1 if disc_number is None or not isinstance(disc_number, int) or disc_number <= 0 else disc_number
    if track_number > disc_track_counts.get(target_disc_number, 0):
         logging.warning(f"calculate_global_track_number: 目标音轨号 {track_number} 超出了光盘 {target_disc_number} 的总音轨数 {disc_track_counts.get(target_disc_number, 0)}。")
         return None
    for disc_num in sorted(disc_track_counts.keys()):
        if disc_num < target_disc_number:
            global_track_number += disc_track_counts[disc_num]
        elif disc_num == target_disc_number:
            global_track_number += track_number
            return global_track_number
        else:
            break
    logging.warning(f"calculate_global_track_number: 未能在排序的光盘列表中找到目标光盘号 {target_disc_number}。")
    return None

# --- 校验任务专用音轨状态更新 ---
def update_track_status_for_check(tracks, global_track_number, update_data):
    current = 0
    for track in sorted(tracks, key=lambda t: (t.get('disc_number', 1), t.get('track_number', 1))):
        disc = track.get('disc_number', 1)
        tnum = track.get('track_number', 1)
        current += 1
        if current == global_track_number:
            if 'check_success' in update_data:
                track['check_success'] = True
            if 'download_progress' in update_data:
                track['download_progress'] = update_data['download_progress']
            break

# --- 校验任务：通过全局音轨号实时写回 task_queue.json ---
def update_track_by_global_number_in_file(uuid, global_track_number, update_data):
    logging.info(f"准备写入 task_queue.json: uuid={uuid}, global_track_number={global_track_number}, update_data={update_data}")
    task_queue_path = file_paths.get('task_queue')
    task_queue_lock_obj = file_locks.get('task_queue')
    if not task_queue_path or not task_queue_lock_obj:
        logging.error(f"任务 {uuid}: 无法更新状态，任务队列文件路径或锁未配置。")
        return
    try:
        with task_queue_lock_obj.acquire(timeout=10):
            current_tasks = read_json_with_lock(task_queue_path, task_queue_lock_obj, default=None)
            if current_tasks is None or not isinstance(current_tasks, list):
                logging.error(f"任务 {uuid}: 读取 task_queue.json 失败。")
                return
            task_found = None
            for task in current_tasks:
                if task.get("uuid") == uuid:
                    task_found = task
                    break
            if not task_found:
                logging.warning(f"任务 {uuid}: 未找到，无法全局号更新。")
                return
            tracks = task_found.get("metadata", {}).get("tracks", [])
            current = 0
            for track in sorted(tracks, key=lambda t: (t.get('disc_number', 1), t.get('track_number', 1))):
                current += 1
                if current == global_track_number:
                    # 获取当前音轨的下载进度信息
                    current_progress = track.get('download_progress', {})
                    total_bytes = current_progress.get('total', 1)  # 如果没有总字节数，默认使用1
                    
                    # 检查是否需要设置完整成功状态
                    if "Track already exists locally." in str(update_data) or "Decrypted" in str(update_data):
                        complete_status = {
                            "check_success": True,
                            "download_status": "success",
                            "decryption_status": "success",
                            "connection_status": "success",
                            "download_progress": {
                                "current": total_bytes,
                                "total": total_bytes,
                                "percent": 100
                            }
                        }
                        track.update(complete_status)
                        logging.info(f"校验任务: 音轨已存在或解密完成，设置完整成功状态: {complete_status}")
                    else:
                        track.update(update_data)
                        logging.info(f"校验任务: 已实时写入 task_queue.json 全局音轨号 {global_track_number} 的状态: {update_data}")
                    break
            if not write_json_with_lock(task_queue_path, task_queue_lock_obj, current_tasks):
                logging.error(f"任务 {uuid}: 写回 task_queue.json 失败。")
    except Timeout:
        logging.error(f"任务 {uuid}: 更新状态时获取文件锁超时: {task_queue_lock_obj.lock_file}")
    except Exception as e:
        logging.error(f"任务 {uuid}: 校验全局号更新 task_queue.json 时异常: {e}", exc_info=True)

# --- 更新任务整体状态函数 ---
def update_task_status_in_file(uuid, status, error_reason=None, error_log=None, process_complete_time_iso=None, process_start_time_iso=None, checking=None):
    """使用 filelock 更新 task_queue.json 中特定任务的状态。"""
    task_queue_path = file_paths.get('task_queue')
    task_queue_lock_obj = file_locks.get('task_queue')
    if not task_queue_path or not task_queue_lock_obj:
        logging.error(f"任务 {uuid}: 无法更新状态，任务队列文件路径或锁未配置。")
        return

    try:
        with task_queue_lock_obj.acquire(timeout=10): # 增加写锁超时
            current_tasks = read_json_with_lock(task_queue_path, task_queue_lock_obj, default=None)
            if current_tasks is None:
                logging.error(f"更新任务 {uuid} 状态失败：无法读取任务队列 {task_queue_path}。")
                return
            if not isinstance(current_tasks, list):
                logging.error(f"任务队列文件 {task_queue_path} 内容不是列表，无法更新任务 {uuid}。")
                return

            task_found = None
            task_index = -1
            for i, task in enumerate(current_tasks):
                if task.get("uuid") == uuid:
                    task_found = task
                    task_index = i
                    break

            if not task_found:
                logging.warning(f"任务 {uuid}: 在 task_queue.json 中未找到，无法更新状态。")
                return

            logging.info(f"任务 {uuid}: 正在更新状态为 {status}")
            task_found["status"] = status
            if error_reason is not None:
                task_found["error_reason"] = error_reason
            if error_log is not None:
                task_found["error_log"] = error_log
            if process_complete_time_iso is not None:
                task_found["process_complete_time_iso"] = process_complete_time_iso
            if process_start_time_iso is not None:
                task_found["process_start_time_iso"] = process_start_time_iso
            if checking is not None:
                task_found["checking"] = checking
            elif "checking" in task_found:
                del task_found["checking"]

            try:
                with open(task_queue_path, 'w', encoding='utf-8') as f_write:
                    json.dump(current_tasks, f_write, indent=4, ensure_ascii=False)
                    f_write.flush()
                    os.fsync(f_write.fileno())
            except Exception as write_e: logging.error(f"任务 {uuid}: 将更新后的任务列表写回 {task_queue_path} 时出错: {write_e}")
            logging.info(f"[{threading.current_thread().name}] 释放锁: {task_queue_lock_obj.lock_file}")
    except filelock.Timeout: logging.error(f"任务 {uuid}: 更新状态时获取文件锁超时: {task_queue_lock_obj.lock_file}")
    except Exception as e: logging.error(f"任务 {uuid}: 更新 task_queue.json 状态时发生意外错误: {e}", exc_info=True)

def update_track_progress_in_file(uuid, song_id, update_data):
    """使用 filelock 和 song_id 更新 task_queue.json 中特定任务特定音轨的状态。"""
    task_queue_path = file_paths.get('task_queue')
    task_queue_lock_obj = file_locks.get('task_queue')
    if not task_queue_path or not task_queue_lock_obj:
        logging.error(f"任务 {uuid}, Song ID {song_id}: 无法更新状态，任务队列文件路径或锁未配置。")
        return
    if not song_id:
        logging.warning(f"任务 {uuid}: 尝试更新音轨状态但 song_id 无效，跳过。")
        return

    # --- 发送进度更新到SSE客户端 --- #
    if "download_progress" in update_data:
        progress_data = update_data["download_progress"]
        with task_progress_lock:
            if uuid not in task_progress: task_progress[uuid] = {}
            task_progress[uuid][song_id] = progress_data
        message = json.dumps({"song_id": song_id, "progress": progress_data})
        with sse_clients_lock:
            if uuid in sse_clients:
                for _, client_q in sse_clients[uuid]:
                    try: client_q.put(message)
                    except: pass 

    # --- 文件操作和日志记录 --- #
    try:
        with task_queue_lock_obj.acquire(timeout=10): 
            current_tasks = read_json_with_lock(task_queue_path, task_queue_lock_obj, default=None)
            if current_tasks is None: logging.error(f"更新任务 {uuid} (Song ID: {song_id}) 状态失败：无法读取任务队列 {task_queue_path}。"); return
            if not isinstance(current_tasks, list): logging.error(f"任务队列文件 {task_queue_path} 内容不是列表，无法更新任务 {uuid} (Song ID: {song_id})。"); return

            task_found = None
            track_to_update = None # Initialize track_to_update to None

            for task_in_queue in current_tasks: # Renamed task to task_in_queue
                if task_in_queue.get("uuid") == uuid:
                    task_found = task_in_queue
                    metadata = task_found.get("metadata", {})
                    tracks = metadata.get("tracks", [])
                    if not isinstance(tracks, list):
                        logging.warning(f"任务 {uuid}: metadata.tracks 不是列表格式，无法更新 Song ID {song_id}。"); return
                    
                    for track_item in tracks: 
                        if isinstance(track_item, dict) and track_item.get('song_id') == song_id:
                            track_to_update = track_item
                            break # Found the track, no need to search further in this task
                    break # Found the task, no need to search further in current_tasks
            
            if not task_found:
                logging.warning(f"任务 {uuid}: 在 task_queue.json 中未找到，无法更新 Song ID {song_id} 的状态。"); return

            # --- 日志记录移到找到 track_to_update 之后 --- #
            if track_to_update: # Check if track_to_update was found and assigned
                log_track_num_local = track_to_update.get('track_number', '?') 
                log_disc_num_local = track_to_update.get('disc_number') 
                log_disc_info_local = f" (光盘 {log_disc_num_local})" if log_disc_num_local else "" 
                log_update_data_local = {k: v for k, v in update_data.items()} 

                if "download_progress" in log_update_data_local:
                    dp_local = log_update_data_local["download_progress"] 
                    logging.info(f"任务 {uuid}, 音轨 {log_track_num_local}{log_disc_info_local} (ID: {song_id}): 下载进度 {dp_local.get('current')}/{dp_local.get('total')}")
                else:
                    logging.info(f"任务 {uuid}, 音轨 {log_track_num_local}{log_disc_info_local} (ID: {song_id}): 正在更新音轨状态 {log_update_data_local}")
                
                track_to_update.update(update_data) # Update the actual track data
            else:
                # This case should ideally not be reached if song_id is valid and present, 
                # but as a safeguard:
                logging.warning(f"任务 {uuid}: 在元数据中未找到 Song ID {song_id} 对应的音轨，无法更新或记录其状态。")
                return # Do not proceed to write if track was not found

            if not write_json_with_lock(task_queue_path, task_queue_lock_obj, current_tasks):
                logging.error(f"将更新后的任务列表写回 {task_queue_path} 时失败 (UUID: {uuid}, Song ID: {song_id})。")

    except Timeout: logging.error(f"任务 {uuid}, Song ID {song_id}: 更新状态时获取文件锁超时: {task_queue_lock_obj.lock_file}")
    except Exception as e: logging.error(f"任务 {uuid}, Song ID {song_id}: 更新 task_queue.json 状态时发生意外错误: {e}", exc_info=True)


# --- 分析 Go 输出和日志函数 ---
# (基本保持不变)
def analyze_go_output(return_code, total_output):
    """分析 Go 进程的输出和返回码，判断整体是否成功，并给出失败原因。"""
    warnings_detected = 0
    errors_detected = 0
    token_failure_detected = False
    get_eof_failure_detected = False
    for line in total_output.splitlines():
        stripped_line = line.strip()
        if not stripped_line: continue
        if m := WARNING_PATTERN.search(stripped_line): warnings_detected = max(warnings_detected, int(m.group(1)))
        if m := ERROR_PATTERN.search(stripped_line): errors_detected = max(errors_detected, int(m.group(1)))
        if GO_TOKEN_FAILURE_STRING and GO_TOKEN_FAILURE_STRING in stripped_line: token_failure_detected = True
        if GO_GET_EOF_PATTERN.search(stripped_line): get_eof_failure_detected = True
    success = (return_code == 0 and errors_detected == 0 and not token_failure_detected and not get_eof_failure_detected)
    failure_reasons = []
    if return_code != 0: failure_reasons.append(f"返回码 {return_code} 非零")
    if errors_detected > 0: failure_reasons.append(f"检测到 {errors_detected} 个错误 (E:{errors_detected})")
    if token_failure_detected: failure_reasons.append(f"检测到令牌失败 ('{GO_TOKEN_FAILURE_STRING}')")
    if get_eof_failure_detected: failure_reasons.append(f"检测到网络错误 (Get/EOF)")
    reason = ', '.join(failure_reasons) if failure_reasons else "未知失败原因"
    if not success and not failure_reasons: reason = f"Go 进程失败 (返回码 {return_code})，未检测到特定错误模式"
    return success, reason

def log_go_output_line(line, uuid):
    """过滤 Go 进程的单行输出，并将未过滤的行记录到日志。"""
    # DL_PROGRESS 信息由 read_stream 单独处理并更新，不在此处重复记录原始行
    if line.strip().startswith("DL_PROGRESS:"):
        return
        
    filtered = False
    for pattern in GO_FILTER_PATTERNS:
         is_empty_line_pattern = pattern.pattern == r'^\s*$'
         line_is_empty = line.strip() == ''
         matches_empty = is_empty_line_pattern and line_is_empty
         matches_other = not is_empty_line_pattern and pattern.search(line)

         if matches_empty or matches_other:
            if matches_other:
                is_downloading = pattern.pattern.lower() == r'downloading'
                is_decrypting = pattern.pattern.lower() == r'decrypting'
                is_completion = (is_downloading and DOWNLOADED_PATTERN.search(line)) or \
                                (is_decrypting and DECRYPTED_PATTERN.search(line))
                if is_completion:
                    continue 
            filtered = True
            break
    if not filtered:
        # 所有的 Go 输出都通过 logging.info 记录，它会自动使用 utils.setup_logging 配置的格式
        logging.info(f"[Go输出][{uuid}] {line.rstrip()}")

def read_stream(stream, output_list, uuid, song_id_context, update_callback, retry_event=None):
    """从流中读取行，记录日志，解析状态更新行，并使用 song_id_context 调用回调。支持Go重试信号检测。"""
    stream_name = "stdout" if hasattr(stream, 'fileno') and stream.fileno() == sys.stdout.fileno() else "stderr"
    try:
        for line in iter(stream.readline, ''):
            output_list.append(line)

            # 检查Go重试信号
            if retry_event is not None and "Error detected, press Enter to try again..." in line:
                logging.warning(f"[Go_UUID='{uuid}'] 检测到Go重试信号，准备自动回车重试")
                retry_event.set()
                break

            # 检查是否是进度信息
            progress_match = DOWNLOAD_PROGRESS_PATTERN.match(line.strip())
            if progress_match:
                current_bytes = int(progress_match.group(1))
                total_bytes = int(progress_match.group(2))
                if update_callback is not None and song_id_context is not None:
                    update_data = {
                        "download_progress": {
                            "current": current_bytes,
                            "total": total_bytes,
                            "percent": round(current_bytes*100/total_bytes, 2)
                        }
                    }
                    update_callback(uuid, song_id_context, update_data)
                continue
            if "正在更新状态" in line and "download_progress" in line:
                continue
            log_go_output_line(line, uuid)
            can_update_status = update_callback is not None and song_id_context is not None
            if not can_update_status:
                continue
            stripped_line = line.strip()
            update_data = {}
            if GO_CONNECT_ERROR_PATTERN.search(stripped_line):
                update_data = {"connection_status": "failed"}
            # 新增：检测特定歌词获取失败标记
            elif stripped_line.startswith("SPECIFIC_LYRICS_FAILURE:"): 
                if song_id_context: # 确保 song_id_context 有效
                    update_data = {"lyrics_status": "failed", "connection_status": "success"} # 假设连接本身是成功的
                else:
                    logging.warning(f"任务 {uuid}: 检测到歌词获取失败，但 song_id_context 无效，无法更新状态。")
            elif "connected" in stripped_line.lower():
                update_data = {"connection_status": "success"}
            elif quality_match := AUDIO_QUALITY_PATTERN.match(stripped_line):
                try:
                    bit_depth = int(quality_match.group(1))
                    sample_rate = int(quality_match.group(2))
                    update_data = {"bit_depth": bit_depth, "sample_rate": sample_rate, "connection_status": "success"}
                except (ValueError, IndexError):
                    logging.warning(f"任务 {uuid}, Song ID {song_id_context}: 解析质量失败: {stripped_line}")
            elif DOWNLOADED_PATTERN.match(stripped_line):
                update_data = {"download_status": "success" ,"connection_status": "success"}
            elif DECRYPTED_PATTERN.match(stripped_line):
                update_data = {"decryption_status": "success" ,"connection_status": "success"}
            elif TRACK_EXISTS_PATTERN.match(stripped_line):
                update_data = {"download_status": "exists", "decryption_status": "exists", "connection_status": "success"}
            elif LYRICS_FAILURE_PATTERN.match(stripped_line):
                logging.warning(f"任务 {uuid}, Song ID {song_id_context}: Go 报告获取歌词失败。")
                pass
            if update_data:
                update_callback(uuid, song_id_context, update_data)
    except ValueError as ve:
        logging.warning(f"任务 {uuid}: 读取 {stream_name} 流时遇到值错误: {ve}")
    except Exception as e:
        logging.error(f"任务 {uuid}: 读取 {stream_name} 输出流时发生意外错误: {e}", exc_info=True)
    finally:
        try:
            stream.close()
        except Exception: pass


# --- 新增：通知相关函数 ---

# 任务类型映射
TASK_TYPE_MAP = {
    "album": "专辑",
    "playlist": "播放列表",
    "music-video": "MV",
    # 可以根据需要添加更多类型
}

def get_task_display_info(task_data):
    """从任务数据中提取显示名称和中文类型。"""
    name = "未知名称"
    type_key = "未知类型"
    type_zh = "未知类型"

    try:
        metadata = task_data.get("metadata", {})
        link_info = task_data.get("link_info", {})

        if isinstance(metadata, dict):
            name = metadata.get("name", name)

        if isinstance(link_info, dict):
            type_key = link_info.get("type", type_key)
            type_zh = TASK_TYPE_MAP.get(type_key, f"类型({type_key})" if type_key != "未知类型" else "未知类型")

    except Exception as e:
        logging.warning(f"任务 {task_data.get('uuid', '未知UUID')}: 提取显示信息时出错: {e}")

    return name, type_zh, type_key # 返回 key 用于可能的逻辑判断

# --- 添加全局变量用于轮询算法 ---
decrypt_port_counter = 0
get_port_counter = 0
port_counter_lock = threading.Lock()

def get_next_decryptor_port(source_data_dict):
    """从 source.yaml 中获取下一个解密器端口，实现轮询负载均衡"""
    global decrypt_port_counter, get_port_counter
    
    decrypt_m3u8_port = source_data_dict.get('decrypt-m3u8-port', '')
    get_m3u8_port = source_data_dict.get('get-m3u8-port', '')
    
    # 检查是否为列表格式
    if isinstance(decrypt_m3u8_port, list) and decrypt_m3u8_port:
        with port_counter_lock:
            decrypt_port_counter = (decrypt_port_counter + 1) % len(decrypt_m3u8_port)
            decrypt_m3u8_port = decrypt_m3u8_port[decrypt_port_counter]
    
    if isinstance(get_m3u8_port, list) and get_m3u8_port:
        with port_counter_lock:
            get_port_counter = (get_port_counter + 1) % len(get_m3u8_port)
            get_m3u8_port = get_m3u8_port[get_port_counter]
    
    return decrypt_m3u8_port, get_m3u8_port

def execute_single_track(task_data, track, user_notification_config, max_retries, retry_delay, go_main_bin_path):
    """执行单个音轨的下载任务"""
    uuid = task_data.get("uuid")
    skip_check = task_data.get("skip_check", False)  # 获取 skip_check 参数，默认为 False
    
    # 构建基本命令
    cmd = [go_main_bin_path, "--uuid", uuid, "--song-id", track["song_id"]]
    
    # 如果是校验任务，永远不使用 skip_check
    if task_data.get("status") == "checking":
        skip_check = False
    
    # 如果 skip_check 为 True，添加 --skip-check 参数
    if skip_check:
        cmd.append("--skip-check")
    
    # 添加其他必要的参数
    if user_notification_config:
        # 获取第一个可用的通知URL
        bark_urls = user_notification_config.get("bark_urls", [])
        if bark_urls and isinstance(bark_urls, list) and len(bark_urls) > 0:
            cmd.extend(["--notify-url", bark_urls[0]])
            if user_notification_config.get("token"):
                cmd.extend(["--notify-token", user_notification_config["token"]])
    
    user = task_data.get("user", "未知用户")
    track_number = track.get('track_number')
    track_url = track.get('url')
    track_name = track.get('name', f"音轨 {track_number}")
    song_id = track.get('song_id')
    disc_number = track.get('disc_number')
    log_disc_info = f" (光盘 {disc_number})" if disc_number else ""
    if not track_url:
        log_msg = f"任务 {uuid}, 音轨 {track_number}{log_disc_info}" + (f" (Song ID {song_id})" if song_id else "") + ": 缺少URL信息。"
        logging.error(log_msg)
        return False, f"音轨 {track_number} 缺少URL信息", ""
    log_prefix = f"任务 {uuid}, 音轨 {track_number}{log_disc_info}" + (f" (Song ID {song_id})" if song_id else " (无 Song ID)")
    logging.info(f"{log_prefix}: 开始处理: {track_name}")
    track_success = False
    final_error_log = ""
    final_error_reason = ""
    is_check_task = (track_number == 0)
    for attempt in range(max_retries + 1):
        logging.info(f"{log_prefix}: 开始尝试 {attempt + 1}/{max_retries + 1}")
        process = None
        attempt_total_output = ""
        source_yaml_template_string = None
        # 校验任务不计入全局Go进程数
        if not is_check_task:
            global current_global_go_processes, max_global_go_processes, global_go_processes_condition
            with global_go_processes_condition:
                while current_global_go_processes >= max_global_go_processes:
                    logging.info(f"全局 Go 进程数已达上限({max_global_go_processes})，等待空闲...")
                    global_go_processes_condition.wait()
                current_global_go_processes += 1
        try:
            source_path = file_paths.get('source')
            source_lock = file_locks.get('source')
            if not source_path or not source_lock:
                msg = f"{log_prefix}: Source.yaml 路径或锁未配置。"
                logging.error(msg)
                final_error_reason = msg; final_error_log = msg
                break
            
            # --- 新增：从 backend.py 获取 API Token ---
            api_token_from_backend = None
            try:
                logging.info(f"{log_prefix}: 正在从 http://localhost:5000/token 获取 API Token。")
                token_response = requests.get("http://localhost:5000/token", timeout=5)
                token_response.raise_for_status()
                token_data = token_response.json()
                if token_data and "token" in token_data:
                    api_token_from_backend = token_data["token"]
                    logging.info(f"{log_prefix}: 成功获取 API Token。")
                else:
                    logging.warning(f"{log_prefix}: 获取 API Token 失败，响应中缺少 token 字段或数据无效。")
            except requests.exceptions.RequestException as req_err:
                logging.warning(f"{log_prefix}: 请求 /token 接口失败: {req_err}")
            except Exception as e_token:
                logging.warning(f"{log_prefix}: 获取 API Token 时发生未知错误: {e_token}")
            # --- API Token 获取结束 ---

            source_data_dict = read_yaml_with_lock(source_path, source_lock)
            if isinstance(source_data_dict, dict):
                # --- 新增：将获取到的 API Token 添加到 source_data_dict ---
                if api_token_from_backend:
                    source_data_dict["api_token"] = api_token_from_backend
                    logging.debug(f"{log_prefix}: 已将 API Token 添加到传递给 Go 的配置中。")
                else:
                    logging.warning(f"{log_prefix}: 未能获取 API Token，Go 程序将尝试自行获取。")
                # --- API Token 添加结束 ---
                try:
                    decrypt_m3u8_port, get_m3u8_port = get_next_decryptor_port(source_data_dict)
                    source_data_dict['decrypt-m3u8-port'] = decrypt_m3u8_port
                    source_data_dict['get-m3u8-port'] = get_m3u8_port
                    logging.info(f"{log_prefix}: 使用解密器端口 {decrypt_m3u8_port}, 获取端口 {get_m3u8_port}")
                    source_yaml_template_string = yaml.dump(source_data_dict, default_flow_style=False, allow_unicode=True)
                except Exception as e:
                    logging.error(f"{log_prefix}: source_data 转 YAML 失败: {e}")
                    source_yaml_template_string = ""
            else:
                logging.warning(f"{log_prefix}: {source_path} 内容无效")
                source_yaml_template_string = ""
            if not source_yaml_template_string:
                msg = f"{log_prefix}: 无法获取 source.yaml 内容。"
                logging.error(msg)
                final_error_reason = msg; final_error_log = msg
                break
            try:
                yaml_input_string = source_yaml_template_string.replace("{user}", user)
            except Exception as e:
                logging.error(f"{log_prefix}: 替换 {{user}} 失败: {e}")
                final_error_reason = "source.yaml 替换错误"; final_error_log = f"替换错误: {e}"
                break
            go_command = [go_main_bin_path, track_url]
            if track_number != 0:
                go_command.append("--song")
            # 修改这里：只有在非校验任务且 skip_check 为 True 时才添加 --skip-check
            if not is_check_task and skip_check:
                go_command.append("--skip-check")
            process_cwd = PROJECT_ROOT
            logging.info(f"{log_prefix}: 准备执行: {' '.join(go_command)}")
            process = subprocess.Popen(go_command, cwd=process_cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace')
            stdout_lines = []
            stderr_lines = []
            import threading
            retry_event = threading.Event()
            # 校验任务用特殊的stream处理
            if is_check_task:
                # 获取tracks列表
                tracks = task_data.get('metadata', {}).get('tracks', [])
                def check_read_stream(stream, output_list, uuid, update_callback, retry_event, tracks):
                    logging.info(f"check_read_stream已启动，等待Go输出...")
                    last_global_track_number = None
                    for line in iter(stream.readline, ''):
                        output_list.append(line)
                        m = re.search(r'Track (\d+) of', line)
                        if m:
                            last_global_track_number = int(m.group(1))
                            logging.info(f"检测到全局音轨号: {last_global_track_number}")
                            continue
                        logging.info(f"[Go校验输出] {line.strip()}")
                        if last_global_track_number:
                            # 通过全局音轨号查找对应的音轨
                            current = 0
                            target_track = None
                            for track in sorted(tracks, key=lambda t: (t.get('disc_number', 1), t.get('track_number', 1))):
                                current += 1
                                if current == last_global_track_number:
                                    target_track = track
                                    break
                            
                            if target_track:
                                song_id = target_track.get('song_id')
                                if song_id:
                                    if "Track already exists locally." in line:
                                        logging.info(f"即将写入: uuid={uuid}, global_track_number={last_global_track_number}, check_success=True")
                                        check_write_queue.put({'uuid': uuid, 'global_track_number': last_global_track_number, 'update_data': {'check_success': True}})
                                        # 推送SSE更新
                                        update_data = {
                                            "check_success": True,
                                            "download_progress": {
                                                "current": 1,
                                                "total": 1,
                                                "percent": 100
                                            }
                                        }
                                        update_track_progress_in_file(uuid, song_id, update_data)
                                        continue
                                    if any(x in line for x in ["Decrypted", "Downloaded", "connected"]):
                                        logging.info(f"即将写入: uuid={uuid}, global_track_number={last_global_track_number}, check_success=True, 100%")
                                        check_write_queue.put({'uuid': uuid, 'global_track_number': last_global_track_number, 'update_data': {
                                            'check_success': True,
                                            'download_progress': {
                                                'current': 1,
                                                'total': 1,
                                                'percent': 100
                                            }
                                        }})
                                        # 推送SSE更新
                                        update_data = {
                                            "check_success": True,
                                            "download_progress": {
                                                "current": 1,
                                                "total": 1,
                                                "percent": 100
                                            }
                                        }
                                        update_track_progress_in_file(uuid, song_id, update_data)
                                        continue
                        if retry_event is not None and "Error detected, press Enter to try again..." in line:
                            logging.warning(f"[Go_UUID='{uuid}'] 检测到Go重试信号，准备自动回车重试")
                            retry_event.set()
                            break
                stdout_thread = threading.Thread(target=check_read_stream, args=(process.stdout, stdout_lines, uuid, update_track_progress_in_file, retry_event, tracks), name=f"stdout-{uuid[:8]}")
            else:
                stdout_thread = threading.Thread(target=read_stream, args=(process.stdout, stdout_lines, uuid, song_id, update_track_progress_in_file, retry_event), name=f"stdout-{uuid[:8]}")
            stderr_thread = threading.Thread(target=read_stream, args=(process.stderr, stderr_lines, uuid, None, None), name=f"stderr-{uuid[:8]}")
            stdout_thread.start()
            stderr_thread.start()
            try:
                process.stdin.write(yaml_input_string)
                process.stdin.close()  # 必须关闭stdin，否则Go进程会卡住
            except (IOError, BrokenPipeError) as e:
                logging.warning(f"{log_prefix}: 写入 stdin 错误: {e}")
            except Exception as e:
                logging.error(f"{log_prefix}: 写入 stdin 未知错误: {e}")
            go_retry_triggered = False
            while process.poll() is None:
                if retry_event.is_set():
                    logging.warning(f"{log_prefix}: 检测到Go重试信号，终止本次Go进程，计入一次Python重试")
                    go_retry_triggered = True
                    process.kill()
                    break
                process_check_write_queue()
                time.sleep(0.1)
            stdout_thread.join(timeout=20)
            stderr_thread.join(timeout=20)
            process_check_write_queue()
            if stdout_thread.is_alive() or stderr_thread.is_alive():
                logging.warning(f"{log_prefix}: 输出读取线程超时未结束。")
            return_code = process.returncode
            attempt_total_output = "".join(stdout_lines + stderr_lines)
            if go_retry_triggered:
                logging.info(f"{log_prefix}: 本次Go进程因重试信号被终止，进入下一次Python重试。")
                if attempt < max_retries:
                    logging.info(f"{log_prefix}: {retry_delay} 秒后重试。")
                    time.sleep(retry_delay)
                    continue
                else:
                    logging.error(f"{log_prefix}: 达到最大次数尝试，任务失败。")
                    break
            attempt_success, attempt_reason = analyze_go_output(return_code, attempt_total_output)
            if attempt_success:
                logging.info(f"{log_prefix}: 尝试 {attempt + 1} 成功。")
                track_success = True
                break
            else:
                logging.warning(f"{log_prefix}: 尝试 {attempt + 1} 失败。原因: {attempt_reason}")
                final_error_log = attempt_total_output
                final_error_reason = attempt_reason
                if attempt < max_retries:
                    logging.info(f"{log_prefix}: {retry_delay} 秒后重试。")
                    time.sleep(retry_delay)
                else:
                    logging.error(f"{log_prefix}: 达到最大次数尝试，任务失败。")
                    break
        except Exception as e:
            msg = f"{log_prefix}: 执行尝试 {attempt + 1} 时发生意外错误: {e}"
            logging.error(msg, exc_info=True)
            track_success = False
            final_error_reason = f"意外错误: {e}"
            final_error_log = msg + "\n" + traceback.format_exc()
            if attempt < max_retries:
                logging.info(f"{log_prefix}: 因意外错误，{retry_delay} 秒后重试。")
                time.sleep(retry_delay)
            else:
                logging.error(f"{log_prefix}: 因意外错误达到最大次数，任务失败。")
                break
        finally:
            if process:
                try:
                    process.kill()
                except Exception:
                    pass
            if not is_check_task:
                with global_go_processes_condition:
                    current_global_go_processes -= 1
                    global_go_processes_condition.notify_all()
    return track_success, final_error_reason, final_error_log

def execute_task(task_data, user_notification_config):
    """通过运行 Go 二进制执行任务，并在完成后触发 Bark 和 Emby 通知。"""
    uuid = task_data.get("uuid", "未知UUID")
    user = task_data.get("user", "未知用户")
    link = task_data.get("link", "未知链接")
    threading.current_thread().name = f"任务-{uuid[:8]}"
    logging.info(f"任务开始执行。UUID: {uuid}, 用户: {user}")

    # 获取通知配置
    emby_url = user_notification_config.get('emby_url')
    # bark_urls 现在是一个对象列表，每个对象包含 server 和 click_url_template
    bark_configs = user_notification_config.get('bark_urls', []) 
    # bark_config = config_data.get('bark_notification', {}) # 不再需要全局 Bark 配置

    # 构建完整的 Bark URLs - 这部分逻辑需要大改，因为现在每个 bark_config 都有自己的 click_url_template
    # complete_bark_urls = [] # 不再构建这个列表，直接在发送时处理
    # for base_url in bark_urls:
    #     if base_url:
    #         # 构建完整的 URL，包含路径、图标和跳转链接
    #         complete_url = f"{base_url}{bark_config.get(\'path\', \'\')}?icon={bark_config.get(\'icon\', \'\')}&url={bark_config.get(\'url\', \'\')}"
    #         complete_bark_urls.append(complete_url)

    # 获取重试参数等 (保持不变)
    max_retries = config_data.get('MAX_RETRIES', DEFAULT_MAX_RETRIES)
    retry_delay = config_data.get('RETRY_DELAY', DEFAULT_RETRY_DELAY)
    go_main_bin_path = file_paths.get('go_main_bin')
    if not go_main_bin_path or not os.path.exists(go_main_bin_path) or not os.access(go_main_bin_path, os.X_OK):
        msg = f"任务 {uuid}: Go 二进制文件路径无效或不可执行: {go_main_bin_path}"
        logging.error(msg)
        update_task_status_in_file(uuid, "error", msg, msg)
        with running_set_lock:
            if uuid in running_task_uuids:
                running_task_uuids.remove(uuid)
        return
    # 检查任务类型
    task_type = task_data.get('link_info', {}).get('type', '')
    is_album_or_playlist = task_type in ['album', 'playlist']
    
    if is_album_or_playlist:
        tracks = task_data.get('metadata', {}).get('tracks', [])
        if not tracks:
            msg = f"任务 {uuid}: 专辑/播放列表任务没有音轨信息。"
            logging.error(msg)
            update_task_status_in_file(uuid, "error", msg, msg)
            with running_set_lock:
                if uuid in running_task_uuids:
                    running_task_uuids.remove(uuid)
            return
        
        # --- 移除按全局音轨号排序的逻辑 --- #
        # 可以选择按原始顺序或 disc/track 排序，如果需要的话
        # 简单的按 track_number (和 disc_number) 排序
        def get_sort_key(track):
            if not isinstance(track, dict): return (999, 999) # 无效 track 排最后
            disc = track.get('disc_number')
            track_num = track.get('track_number')
            # 处理 None 的情况，给一个默认大值
            disc = disc if disc is not None else 999
            track_num = track_num if track_num is not None else 999
            return (disc, track_num)
        try:
            tracks.sort(key=get_sort_key)
            logging.debug(f"任务 {uuid}: 音轨已按 disc/track number 排序。")
        except Exception as sort_e:
             logging.warning(f"任务 {uuid}: 按 disc/track number 排序时出错: {sort_e}，将按原始顺序处理。")
        # --- 排序逻辑结束 --- #

        # 获取最大并行任务数 (保持不变)
        max_workers = config_data.get('MAX_PARALLEL_TASKS', 10)
        
        # 创建线程池 (保持不变)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            # --- 提交所有音轨的下载任务 (传递 track 字典) --- #
            future_to_track = {
                executor.submit(
                    execute_single_track,
                    task_data, # 整个任务数据
                    track,     # 单个音轨字典 (包含 song_id)
                    user_notification_config,
                    max_retries,
                    retry_delay,
                    go_main_bin_path
                    # track_lookup_map 不再传递
                ): track # value 仍然是 track 字典，用于日志
                for track in tracks if isinstance(track, dict) # 确保 track 是字典
            }
            # --- 提交结束 --- #

            # 收集结果 (保持不变)
            success_count = 0
            failure_count = 0
            final_error_log = ""
            final_error_reason = ""

            for future in concurrent.futures.as_completed(future_to_track):
                track = future_to_track[future]
                log_track_num = track.get('track_number', '?')
                log_song_id = track.get('song_id', 'N/A')
                try:
                    track_success, error_reason, error_log = future.result()
                    if track_success:
                        success_count += 1
                    else:
                        failure_count += 1
                        # 记录第一个遇到的错误原因和日志
                        if not final_error_reason and error_reason:
                            final_error_reason = f"音轨 {log_track_num} (ID: {log_song_id}) 失败: {error_reason}"
                        if not final_error_log and error_log:
                            final_error_log = f"--- 音轨 {log_track_num} (ID: {log_song_id}) 错误日志 ---\n{error_log}"
                except Exception as e:
                    failure_count += 1
                    err_msg = f"音轨 {log_track_num} (ID: {log_song_id}) 执行失败: {e}"
                    logging.error(f"任务 {uuid}, {err_msg}", exc_info=True)
                    if not final_error_reason:
                         final_error_reason = err_msg
                    if not final_error_log:
                         final_error_log = traceback.format_exc()

        # 根据结果更新任务状态 (修改：添加专辑校验步骤)
        if failure_count == 0:
            # 如果是专辑类型，执行校验步骤
            if task_type == 'album':
                # --- NEW: Check if all tracks exist locally ---
                all_tracks_exist_locally = False # Default to false, proceed with check
                task_queue_path_check = file_paths.get('task_queue')
                task_queue_lock_obj_check = file_locks.get('task_queue')

                if task_queue_path_check and task_queue_lock_obj_check:
                    try:
                        current_tasks_for_check = read_json_with_lock(task_queue_path_check, task_queue_lock_obj_check, default=None)
                        if current_tasks_for_check and isinstance(current_tasks_for_check, list):
                            current_task_data_for_check = next((t for t in current_tasks_for_check if t.get("uuid") == uuid), None)
                            if current_task_data_for_check:
                                album_tracks = current_task_data_for_check.get('metadata', {}).get('tracks', [])
                                # Ensure album_tracks is a non-empty list of dicts
                                if album_tracks and isinstance(album_tracks, list) and all(isinstance(track_item, dict) for track_item in album_tracks):
                                    all_tracks_exist_locally = True # Assume true until a track doesn't meet criteria
                                    for track_item in album_tracks:
                                        if not (track_item.get('download_status') == 'exists' and \
                                                track_item.get('decryption_status') == 'exists'):
                                            all_tracks_exist_locally = False
                                            logging.debug(f"任务 {uuid}: 音轨 {track_item.get('track_number', '未知')} (ID: {track_item.get('song_id', '未知')}) 不满足 'exists' 状态。专辑校验将执行。")
                                            break
                                    if all_tracks_exist_locally:
                                        logging.info(f"任务 {uuid}: 所有音轨均已存在本地，将跳过专辑校验步骤。")
                                else: # album_tracks is empty or not a list of dicts
                                    logging.info(f"任务 {uuid}: 专辑元数据中无有效音轨信息或音轨列表为空，将执行专辑校验。")
                                    all_tracks_exist_locally = False # Ensure check runs
                            else:
                                logging.warning(f"任务 {uuid}: 在队列中未找到任务数据，无法检查音轨状态以跳过校验。将执行专辑校验。")
                        else:
                            logging.warning(f"任务 {uuid}: 读取任务队列失败或格式无效，无法检查音轨状态以跳过校验。将执行专辑校验。")
                    except Exception as e_read_q_check:
                        logging.error(f"任务 {uuid}: 检查音轨状态以跳过校验时读取任务队列异常: {e_read_q_check}，将执行专辑校验。", exc_info=True)
                else:
                    logging.error(f"任务 {uuid}: 任务队列路径或锁未配置，无法检查音轨状态以跳过校验。将执行专辑校验。")
                # --- END NEW CHECK ---

                if all_tracks_exist_locally:
                    logging.info(f"任务 {uuid}: 所有音轨已存在，标记任务完成。")
                    process_complete_time_iso = datetime.now(timezone.utc).isoformat()
                    update_task_status_in_file(uuid, "finish", None, None, process_complete_time_iso, checking=False)
                    if emby_url:
                        send_emby_refresh(user, uuid)
                    
                    emby_album_id_for_bark = None
                    if bark_configs:
                        task_name_for_emby_query, _, _ = get_task_display_info(task_data)
                        emby_url_for_query = user_notification_config.get('emby_url')
                        emby_api_key_for_query = user_notification_config.get('emby_api_key')
                        if emby_url_for_query and emby_api_key_for_query and task_name_for_emby_query:
                            from notifications import query_emby_album_id
                            emby_album_id_for_bark = query_emby_album_id(emby_url_for_query, emby_api_key_for_query, task_name_for_emby_query)
                            if emby_album_id_for_bark:
                                logging.info(f"任务 {uuid}: (全部已存在) 成功为 Bark 获取 Emby Album ID: {emby_album_id_for_bark}")
                            else:
                                logging.warning(f"任务 {uuid}: (全部已存在) 未能为 Bark 获取 Emby Album ID (专辑: {task_name_for_emby_query})")
                        else:
                            logging.warning(f"任务 {uuid}: (全部已存在) 无法为 Bark 查询 Emby Album ID，缺少 Emby URL/API Key/专辑名。")

                    for bark_config_item in bark_configs:
                        bark_server = bark_config_item.get('server')
                        click_template = bark_config_item.get('click_url_template')
                        if bark_server:
                            send_bark_notification(bark_server, click_template, user, task_data, True, emby_album_id=emby_album_id_for_bark)
                    # --- 新增：发送任务完成通知到前端 ---
                    task_name, task_type_zh, _ = get_task_display_info(task_data)
                    notice_data = {
                        "event": "task_completed",
                        "type": "success",
                        "uuid": uuid,
                        "user": user,
                        "task_name": task_name,
                        "task_type": task_type_zh,
                        "message": f"专辑《{task_name}》下载完成",
                        "timestamp": process_complete_time_iso
                    }
                    send_notice_to_clients(notice_data)
                else:
                    # Original logic: proceed with album check
                    logging.info(f"任务 {uuid}: 所有音轨下载完成，但并非所有音轨都已存在本地 (或无法确认)，开始执行专辑校验...")
                    # 标记校验开始
                    update_task_status_in_file(uuid, "running", checking=True)
                    # 创建一个简化的 track 字典用于校验
                    album_check_track = {
                        "track_number": 0,  # 使用0表示这是专辑校验
                        "url": link,
                        "name": task_data.get('metadata', {}).get('name', "专辑校验"),
                        "song_id": None
                    }
                    # 执行校验
                    check_success, check_reason, check_log = execute_single_track(
                        task_data,
                        album_check_track,
                        user_notification_config,
                        max_retries,
                        retry_delay,
                        go_main_bin_path
                    )
                    # 校验结束，移除checking
                    update_task_status_in_file(uuid, "running", checking=False)
                    if check_success:
                        logging.info(f"任务 {uuid}: 专辑校验成功，任务完成。")
                        process_complete_time_iso = datetime.now(timezone.utc).isoformat()
                        update_task_status_in_file(uuid, "finish", None, None, process_complete_time_iso, checking=False)
                        if emby_url:
                            send_emby_refresh(user, uuid)
                        
                        # --- 在发送 Bark 通知前获取 Emby Album ID ---
                        emby_album_id_for_bark = None
                        if bark_configs:
                            task_name_for_emby_query, _, _ = get_task_display_info(task_data) # 获取任务名
                            emby_url_for_query = user_notification_config.get('emby_url')
                            emby_api_key_for_query = user_notification_config.get('emby_api_key')
                            if emby_url_for_query and emby_api_key_for_query and task_name_for_emby_query:
                                logging.info(f"任务 {uuid}: 尝试为 Bark 通知获取 Emby Album ID (专辑: {task_name_for_emby_query})...")
                                # 需要从 notifications.py 导入 query_emby_album_id
                                # (确保此函数在 notifications.py 中是可导入的)
                                from notifications import query_emby_album_id 
                                emby_album_id_for_bark = query_emby_album_id(emby_url_for_query, emby_api_key_for_query, task_name_for_emby_query)
                                if emby_album_id_for_bark:
                                    logging.info(f"任务 {uuid}: 成功获取 Emby Album ID for Bark: {emby_album_id_for_bark}")
                                else:
                                    logging.warning(f"任务 {uuid}: 未能为 Bark 通知获取 Emby Album ID (专辑: {task_name_for_emby_query}).")
                            else:
                                logging.warning(f"任务 {uuid}: 无法为 Bark 通知查询 Emby Album ID，缺少 Emby URL, API Key 或专辑名。")
                        else:
                            logging.warning(f"任务 {uuid}: 无法为 Bark 通知查询 Emby Album ID，缺少 Emby URL, API Key 或专辑名。")

                        # 发送到所有配置的 Bark URLs
                        for bark_config_item in bark_configs: # <--- 迭代新的 bark_configs
                            bark_server = bark_config_item.get('server')
                            click_template = bark_config_item.get('click_url_template')
                            if bark_server:
                                send_bark_notification(bark_server, click_template, user, task_data, True, emby_album_id=emby_album_id_for_bark) # <--- 传递 emby_album_id
                        
                        # --- 新增：发送任务完成通知到前端 ---
                        task_name, task_type_zh, _ = get_task_display_info(task_data)
                        notice_data = {
                            "event": "task_completed",
                            "type": "success",
                            "uuid": uuid,
                            "user": user,
                            "task_name": task_name,
                            "task_type": task_type_zh,
                            "message": f"专辑《{task_name}》下载完成",
                            "timestamp": process_complete_time_iso
                        }
                        send_notice_to_clients(notice_data)
                    else:
                        logging.error(f"任务 {uuid}: 专辑校验失败。原因: {check_reason}")
                        process_complete_time_iso = datetime.now(timezone.utc).isoformat()
                        error_msg = f"专辑校验失败: {check_reason}"
                        update_task_status_in_file(uuid, "error", error_msg, check_log, process_complete_time_iso, checking=False)
                        if emby_url:
                            send_emby_refresh(user, uuid)
                        # 发送到所有配置的 Bark URLs
                        for bark_config_item in bark_configs: # <--- 迭代新的 bark_configs
                            bark_server = bark_config_item.get('server')
                            click_template = bark_config_item.get('click_url_template')
                            if bark_server:
                                send_bark_notification(bark_server, click_template, user, task_data, False)
                        # --- 新增：发送任务失败通知到前端 ---
                        task_name, task_type_zh, _ = get_task_display_info(task_data)
                        notice_data = {
                            "event": "task_completed",
                            "type": "error",
                            "uuid": uuid,
                            "user": user,
                            "task_name": task_name,
                            "task_type": task_type_zh,
                            "message": f"专辑《{task_name}》下载失败: {error_msg}",
                            "timestamp": process_complete_time_iso
                        }
                        send_notice_to_clients(notice_data)
            else:
                # 播放列表直接完成
                process_complete_time_iso = datetime.now(timezone.utc).isoformat()
                update_task_status_in_file(uuid, "finish", None, None, process_complete_time_iso)
                if emby_url:
                    send_emby_refresh(user, uuid)
                
                # --- 在发送 Bark 通知前获取 Emby Album ID (同上) ---
                emby_album_id_for_bark_playlist = None
                if bark_configs: # 仅当是专辑类型时才尝试获取，播放列表通常不直接对应单个Emby专辑ID
                    task_name_for_emby_query_pl, _, _ = get_task_display_info(task_data)
                    emby_url_for_query_pl = user_notification_config.get('emby_url')
                    emby_api_key_for_query_pl = user_notification_config.get('emby_api_key')
                    if emby_url_for_query_pl and emby_api_key_for_query_pl and task_name_for_emby_query_pl:
                        logging.info(f"任务 {uuid}: (播放列表场景下，如果包含专辑) 尝试为 Bark 通知获取 Emby Album ID (专辑: {task_name_for_emby_query_pl})...")
                        from notifications import query_emby_album_id
                        emby_album_id_for_bark_playlist = query_emby_album_id(emby_url_for_query_pl, emby_api_key_for_query_pl, task_name_for_emby_query_pl)
                        if emby_album_id_for_bark_playlist:
                            logging.info(f"任务 {uuid}: (播放列表场景) 成功获取 Emby Album ID for Bark: {emby_album_id_for_bark_playlist}")
                        else:
                            logging.warning(f"任务 {uuid}: (播放列表场景) 未能为 Bark 通知获取 Emby Album ID (专辑: {task_name_for_emby_query_pl}).")
                    else:
                        logging.warning(f"任务 {uuid}: (播放列表场景) 无法为 Bark 通知查询 Emby Album ID，缺少 Emby URL, API Key 或专辑名。")
                # --- 获取结束 ---

                # 发送到所有配置的 Bark URLs
                for bark_config_item in bark_configs: # <--- 迭代新的 bark_configs
                    bark_server = bark_config_item.get('server')
                    click_template = bark_config_item.get('click_url_template')
                    if bark_server:
                        # 播放列表任务成功时，如果之前获取了album_id (理论上仅针对专辑内单曲)，则传递，否则不传
                        send_bark_notification(bark_server, click_template, user, task_data, True, emby_album_id=emby_album_id_for_bark_playlist if task_type == 'album' else None)
                # --- 新增：发送任务完成通知到前端 ---
                task_name, task_type_zh, _ = get_task_display_info(task_data)
                notice_data = {
                    "event": "task_completed",
                    "type": "success",
                    "uuid": uuid,
                    "user": user,
                    "task_name": task_name,
                    "task_type": task_type_zh,
                    "message": f"播放列表《{task_name}》下载完成",
                    "timestamp": process_complete_time_iso
                }
                send_notice_to_clients(notice_data)
        else:
            process_complete_time_iso = datetime.now(timezone.utc).isoformat()
            error_msg = f"任务失败: {failure_count} 个音轨下载失败。首个错误: {final_error_reason}"
            update_task_status_in_file(uuid, "error", error_msg, final_error_log, process_complete_time_iso)
            if emby_url:
                send_emby_refresh(user, uuid)
            
            # --- 失败情况下也尝试获取 Album ID (如果配置了Bark) ---
            emby_album_id_for_bark_fail = None
            if task_type == 'album' and bark_configs: # 仅专辑类型
                task_name_for_emby_query_fail, _, _ = get_task_display_info(task_data)
                emby_url_for_query_fail = user_notification_config.get('emby_url')
                emby_api_key_for_query_fail = user_notification_config.get('emby_api_key')
                if emby_url_for_query_fail and emby_api_key_for_query_fail and task_name_for_emby_query_fail:
                    logging.info(f"任务 {uuid}: (失败情况) 尝试为 Bark 通知获取 Emby Album ID (专辑: {task_name_for_emby_query_fail})...")
                    from notifications import query_emby_album_id
                    emby_album_id_for_bark_fail = query_emby_album_id(emby_url_for_query_fail, emby_api_key_for_query_fail, task_name_for_emby_query_fail)
                    if emby_album_id_for_bark_fail:
                        logging.info(f"任务 {uuid}: (失败情况) 成功获取 Emby Album ID for Bark: {emby_album_id_for_bark_fail}")
                    else:
                        logging.warning(f"任务 {uuid}: (失败情况) 未能为 Bark 通知获取 Emby Album ID (专辑: {task_name_for_emby_query_fail}).")
                else:
                    logging.warning(f"任务 {uuid}: (失败情况) 无法为 Bark 通知查询 Emby Album ID，缺少 Emby URL, API Key 或专辑名。")
            # --- 获取结束 ---

            # 发送到所有配置的 Bark URLs
            for bark_config_item in bark_configs: # <--- 迭代新的 bark_configs
                bark_server = bark_config_item.get('server')
                click_template = bark_config_item.get('click_url_template')
                if bark_server:
                    send_bark_notification(bark_server, click_template, user, task_data, False, emby_album_id=emby_album_id_for_bark_fail) # <--- 传递 emby_album_id
            # --- 新增：发送任务失败通知到前端 ---
            task_name, task_type_zh, _ = get_task_display_info(task_data)
            notice_data = {
                "event": "task_completed",
                "type": "error",
                "uuid": uuid,
                "user": user,
                "task_name": task_name,
                "task_type": task_type_zh,
                "message": f"任务《{task_name}》下载失败: {error_msg}",
                "timestamp": process_complete_time_iso
            }
            send_notice_to_clients(notice_data)
    
    else:
        # 处理单个音轨/MV的情况
        # --- 创建一个简化的 track 字典，不包含 song_id --- #
        # 因为对于非专辑/播放列表，我们不期望有 song_id 或进行精细状态更新
        single_item_track_dict = {
            "track_number": 1, # 虚拟音轨号
            "url": link,
            "name": task_data.get('metadata', {}).get('name', "单项任务"), # 尝试用元数据名
            "song_id": None # 明确标记无 song_id
        }
        track_success, error_reason, error_log = execute_single_track(
            task_data,
            single_item_track_dict, # 传递简化字典
            user_notification_config,
            max_retries,
            retry_delay,
            go_main_bin_path
        )
        # --- 修改结束 --- #

        # 更新整体任务状态 (逻辑不变)
        if track_success:
            process_complete_time_iso = datetime.now(timezone.utc).isoformat()
            update_task_status_in_file(uuid, "finish", None, None, process_complete_time_iso)
            if emby_url:
                send_emby_refresh(user, uuid)
            # 发送到所有配置的 Bark URLs
            for bark_config_item in bark_configs: # <--- 迭代新的 bark_configs
                bark_server = bark_config_item.get('server')
                click_template = bark_config_item.get('click_url_template')
                if bark_server:
                    send_bark_notification(bark_server, click_template, user, task_data, True)
            # --- 新增：发送任务完成通知到前端 ---
            task_name, task_type_zh, _ = get_task_display_info(task_data)
            notice_data = {
                "event": "task_completed",
                "type": "success",
                "uuid": uuid,
                "user": user,
                "task_name": task_name,
                "task_type": task_type_zh,
                "message": f"任务《{task_name}》下载完成",
                "timestamp": process_complete_time_iso
            }
            send_notice_to_clients(notice_data)
        else:
            process_complete_time_iso = datetime.now(timezone.utc).isoformat()
            update_task_status_in_file(uuid, "error", error_reason, error_log, process_complete_time_iso)
            if emby_url:
                send_emby_refresh(user, uuid)
            # 发送到所有配置的 Bark URLs
            for bark_config_item in bark_configs: # <--- 迭代新的 bark_configs
                bark_server = bark_config_item.get('server')
                click_template = bark_config_item.get('click_url_template')
                if bark_server:
                    send_bark_notification(bark_server, click_template, user, task_data, False)
            # --- 新增：发送任务失败通知到前端 ---
            task_name, task_type_zh, _ = get_task_display_info(task_data)
            notice_data = {
                "event": "task_completed",
                "type": "error",
                "uuid": uuid,
                "user": user,
                "task_name": task_name,
                "task_type": task_type_zh,
                "message": f"任务《{task_name}》下载失败: {error_reason}",
                "timestamp": process_complete_time_iso
            }
            send_notice_to_clients(notice_data)
    
    # 清理运行集合 (保持不变)
    with running_set_lock:
        if uuid in running_task_uuids:
            running_task_uuids.remove(uuid)


# --- 更新任务整体状态函数 ---
def update_task_status_in_file(uuid, status, error_reason=None, error_log=None, process_complete_time_iso=None, process_start_time_iso=None, checking=None):
    """使用 filelock 更新 task_queue.json 中的任务整体状态。"""
    task_queue_path = file_paths.get('task_queue')
    task_queue_lock_obj = file_locks.get('task_queue')
    if not task_queue_path or not task_queue_lock_obj:
        logging.error(f"任务 {uuid}: 无法更新状态，任务队列文件路径或锁未配置。")
        return
    try:
        logging.info(f"[{threading.current_thread().name}] 尝试获取锁: {task_queue_lock_obj.lock_file}")
        with task_queue_lock_obj:
            logging.info(f"[{threading.current_thread().name}] 成功获取锁: {task_queue_lock_obj.lock_file}")
            current_tasks = []
            try:
                with open(task_queue_path, 'r', encoding='utf-8') as f_read:
                    content = f_read.read()
                    if content.strip():
                        data = json.loads(content)
                        if isinstance(data, list): current_tasks = data
                        else: logging.error(f"读取任务队列更新状态时，发现内容不是列表: {task_queue_path}"); return
            except FileNotFoundError: logging.error(f"任务 {uuid}: 更新状态时 task_queue.json 未找到。"); return
            except json.JSONDecodeError: logging.error(f"任务 {uuid}: 更新状态时解码 task_queue.json 失败。"); return

            task_found = None
            task_index = -1
            for i, task in enumerate(current_tasks):
                if task.get("uuid") == uuid: task_found = task; task_index = i; break
            if not task_found: logging.warning(f"任务 {uuid}: 在 task_queue.json 中未找到，无法更新状态。"); return

            # 更新任务状态
            task_found["status"] = status
            if error_reason: task_found["error_reason"] = error_reason
            if error_log: task_found["error_log"] = error_log
            if process_complete_time_iso: task_found["process_complete_time"] = process_complete_time_iso
            if process_start_time_iso: task_found["process_start_time"] = process_start_time_iso
            if checking is not None:
                task_found["checking"] = checking
            elif "checking" in task_found:
                del task_found["checking"]

            try:
                with open(task_queue_path, 'w', encoding='utf-8') as f_write:
                    json.dump(current_tasks, f_write, indent=4, ensure_ascii=False)
                    f_write.flush()
                    os.fsync(f_write.fileno())
            except Exception as write_e: logging.error(f"任务 {uuid}: 将更新后的任务列表写回 {task_queue_path} 时出错: {write_e}")
            logging.info(f"[{threading.current_thread().name}] 释放锁: {task_queue_lock_obj.lock_file}")
    except filelock.Timeout: logging.error(f"任务 {uuid}: 更新状态时获取文件锁超时: {task_queue_lock_obj.lock_file}")
    except Exception as e: logging.error(f"任务 {uuid}: 更新 task_queue.json 状态时发生意外错误: {e}", exc_info=True)

def update_track_progress_in_file(uuid, song_id, update_data):
    """使用 filelock 和 song_id 更新 task_queue.json 中特定任务特定音轨的状态。"""
    task_queue_path = file_paths.get('task_queue')
    task_queue_lock_obj = file_locks.get('task_queue')
    if not task_queue_path or not task_queue_lock_obj:
        logging.error(f"任务 {uuid}, Song ID {song_id}: 无法更新状态，任务队列文件路径或锁未配置。")
        return
    if not song_id:
        logging.warning(f"任务 {uuid}: 尝试更新音轨状态但 song_id 无效，跳过。")
        return

    # --- 发送进度更新到SSE客户端 --- #
    if "download_progress" in update_data:
        progress_data = update_data["download_progress"]
        with task_progress_lock:
            if uuid not in task_progress: task_progress[uuid] = {}
            task_progress[uuid][song_id] = progress_data
        message = json.dumps({"song_id": song_id, "progress": progress_data})
        with sse_clients_lock:
            if uuid in sse_clients:
                for _, client_q in sse_clients[uuid]:
                    try: client_q.put(message)
                    except: pass 

    # --- 文件操作和日志记录 --- #
    try:
        with task_queue_lock_obj.acquire(timeout=10): 
            current_tasks = read_json_with_lock(task_queue_path, task_queue_lock_obj, default=None)
            if current_tasks is None: logging.error(f"更新任务 {uuid} (Song ID: {song_id}) 状态失败：无法读取任务队列 {task_queue_path}。"); return
            if not isinstance(current_tasks, list): logging.error(f"任务队列文件 {task_queue_path} 内容不是列表，无法更新任务 {uuid} (Song ID: {song_id})。"); return

            task_found = None
            track_to_update = None # Initialize track_to_update to None

            for task_in_queue in current_tasks: # Renamed task to task_in_queue
                if task_in_queue.get("uuid") == uuid:
                    task_found = task_in_queue
                    metadata = task_found.get("metadata", {})
                    tracks = metadata.get("tracks", [])
                    if not isinstance(tracks, list):
                        logging.warning(f"任务 {uuid}: metadata.tracks 不是列表格式，无法更新 Song ID {song_id}。"); return
                    
                    for track_item in tracks: 
                        if isinstance(track_item, dict) and track_item.get('song_id') == song_id:
                            track_to_update = track_item
                            break # Found the track, no need to search further in this task
                    break # Found the task, no need to search further in current_tasks
            
            if not task_found:
                logging.warning(f"任务 {uuid}: 在 task_queue.json 中未找到，无法更新 Song ID {song_id} 的状态。"); return

            # --- 日志记录移到找到 track_to_update 之后 --- #
            if track_to_update: # Check if track_to_update was found and assigned
                log_track_num_local = track_to_update.get('track_number', '?') 
                log_disc_num_local = track_to_update.get('disc_number') 
                log_disc_info_local = f" (光盘 {log_disc_num_local})" if log_disc_num_local else "" 
                log_update_data_local = {k: v for k, v in update_data.items()} 

                if "download_progress" in log_update_data_local:
                    dp_local = log_update_data_local["download_progress"] 
                    logging.info(f"任务 {uuid}, 音轨 {log_track_num_local}{log_disc_info_local} (ID: {song_id}): 下载进度 {dp_local.get('current')}/{dp_local.get('total')}")
                else:
                    logging.info(f"任务 {uuid}, 音轨 {log_track_num_local}{log_disc_info_local} (ID: {song_id}): 正在更新音轨状态 {log_update_data_local}")
                
                track_to_update.update(update_data) # Update the actual track data
            else:
                # This case should ideally not be reached if song_id is valid and present, 
                # but as a safeguard:
                logging.warning(f"任务 {uuid}: 在元数据中未找到 Song ID {song_id} 对应的音轨，无法更新或记录其状态。")
                return # Do not proceed to write if track was not found

            if not write_json_with_lock(task_queue_path, task_queue_lock_obj, current_tasks):
                logging.error(f"将更新后的任务列表写回 {task_queue_path} 时失败 (UUID: {uuid}, Song ID: {song_id})。")

    except Timeout: logging.error(f"任务 {uuid}, Song ID {song_id}: 更新状态时获取文件锁超时: {task_queue_lock_obj.lock_file}")
    except Exception as e: logging.error(f"任务 {uuid}, Song ID {song_id}: 更新 task_queue.json 状态时发生意外错误: {e}", exc_info=True)


# --- 分析 Go 输出和日志函数 ---
# (基本保持不变)
def analyze_go_output(return_code, total_output):
    """分析 Go 进程的输出和返回码，判断整体是否成功，并给出失败原因。"""
    warnings_detected = 0
    errors_detected = 0
    token_failure_detected = False
    get_eof_failure_detected = False
    for line in total_output.splitlines():
        stripped_line = line.strip()
        if not stripped_line: continue
        if m := WARNING_PATTERN.search(stripped_line): warnings_detected = max(warnings_detected, int(m.group(1)))
        if m := ERROR_PATTERN.search(stripped_line): errors_detected = max(errors_detected, int(m.group(1)))
        if GO_TOKEN_FAILURE_STRING and GO_TOKEN_FAILURE_STRING in stripped_line: token_failure_detected = True
        if GO_GET_EOF_PATTERN.search(stripped_line): get_eof_failure_detected = True
    success = (return_code == 0 and errors_detected == 0 and not token_failure_detected and not get_eof_failure_detected)
    failure_reasons = []
    if return_code != 0: failure_reasons.append(f"返回码 {return_code} 非零")
    if errors_detected > 0: failure_reasons.append(f"检测到 {errors_detected} 个错误 (E:{errors_detected})")
    if token_failure_detected: failure_reasons.append(f"检测到令牌失败 ('{GO_TOKEN_FAILURE_STRING}')")
    if get_eof_failure_detected: failure_reasons.append(f"检测到网络错误 (Get/EOF)")
    reason = ', '.join(failure_reasons) if failure_reasons else "未知失败原因"
    if not success and not failure_reasons: reason = f"Go 进程失败 (返回码 {return_code})，未检测到特定错误模式"
    return success, reason

def log_go_output_line(line, uuid):
    """过滤 Go 进程的单行输出，并将未过滤的行记录到日志。"""
    # DL_PROGRESS 信息由 read_stream 单独处理并更新，不在此处重复记录原始行
    if line.strip().startswith("DL_PROGRESS:"):
        return
        
    filtered = False
    for pattern in GO_FILTER_PATTERNS:
         is_empty_line_pattern = pattern.pattern == r'^\s*$'
         line_is_empty = line.strip() == ''
         matches_empty = is_empty_line_pattern and line_is_empty
         matches_other = not is_empty_line_pattern and pattern.search(line)

         if matches_empty or matches_other:
            if matches_other:
                is_downloading = pattern.pattern.lower() == r'downloading'
                is_decrypting = pattern.pattern.lower() == r'decrypting'
                is_completion = (is_downloading and DOWNLOADED_PATTERN.search(line)) or \
                                (is_decrypting and DECRYPTED_PATTERN.search(line))
                if is_completion:
                    continue 
            filtered = True
            break
    if not filtered:
        # 所有的 Go 输出都通过 logging.info 记录，它会自动使用 utils.setup_logging 配置的格式
        logging.info(f"[Go输出][{uuid}] {line.rstrip()}")

def read_stream(stream, output_list, uuid, song_id_context, update_callback, retry_event=None):
    """从流中读取行，记录日志，解析状态更新行，并使用 song_id_context 调用回调。支持Go重试信号检测。"""
    stream_name = "stdout" if hasattr(stream, 'fileno') and stream.fileno() == sys.stdout.fileno() else "stderr"
    try:
        for line in iter(stream.readline, ''):
            output_list.append(line)

            # 检查Go重试信号
            if retry_event is not None and "Error detected, press Enter to try again..." in line:
                logging.warning(f"[Go_UUID='{uuid}'] 检测到Go重试信号，准备自动回车重试")
                retry_event.set()
                break

            # 检查是否是进度信息
            progress_match = DOWNLOAD_PROGRESS_PATTERN.match(line.strip())
            if progress_match:
                current_bytes = int(progress_match.group(1))
                total_bytes = int(progress_match.group(2))
                if update_callback is not None and song_id_context is not None:
                    update_data = {
                        "download_progress": {
                            "current": current_bytes,
                            "total": total_bytes,
                            "percent": round(current_bytes*100/total_bytes, 2)
                        }
                    }
                    update_callback(uuid, song_id_context, update_data)
                continue
            if "正在更新状态" in line and "download_progress" in line:
                continue
            log_go_output_line(line, uuid)
            can_update_status = update_callback is not None and song_id_context is not None
            if not can_update_status:
                continue
            stripped_line = line.strip()
            update_data = {}
            if GO_CONNECT_ERROR_PATTERN.search(stripped_line):
                update_data = {"connection_status": "failed"}
            # 新增：检测特定歌词获取失败标记
            elif stripped_line.startswith("SPECIFIC_LYRICS_FAILURE:"): 
                if song_id_context: # 确保 song_id_context 有效
                    update_data = {"lyrics_status": "failed", "connection_status": "success"} # 假设连接本身是成功的
                else:
                    logging.warning(f"任务 {uuid}: 检测到歌词获取失败，但 song_id_context 无效，无法更新状态。")
            elif "connected" in stripped_line.lower():
                update_data = {"connection_status": "success"}
            elif quality_match := AUDIO_QUALITY_PATTERN.match(stripped_line):
                try:
                    bit_depth = int(quality_match.group(1))
                    sample_rate = int(quality_match.group(2))
                    update_data = {"bit_depth": bit_depth, "sample_rate": sample_rate, "connection_status": "success"}
                except (ValueError, IndexError):
                    logging.warning(f"任务 {uuid}, Song ID {song_id_context}: 解析质量失败: {stripped_line}")
            elif DOWNLOADED_PATTERN.match(stripped_line):
                update_data = {"download_status": "success" ,"connection_status": "success"}
            elif DECRYPTED_PATTERN.match(stripped_line):
                update_data = {"decryption_status": "success" ,"connection_status": "success"}
            elif TRACK_EXISTS_PATTERN.match(stripped_line):
                update_data = {"download_status": "exists", "decryption_status": "exists", "connection_status": "success"}
            elif LYRICS_FAILURE_PATTERN.match(stripped_line):
                logging.warning(f"任务 {uuid}, Song ID {song_id_context}: Go 报告获取歌词失败。")
                pass
            if update_data:
                update_callback(uuid, song_id_context, update_data)
    except ValueError as ve:
        logging.warning(f"任务 {uuid}: 读取 {stream_name} 流时遇到值错误: {ve}")
    except Exception as e:
        logging.error(f"任务 {uuid}: 读取 {stream_name} 输出流时发生意外错误: {e}", exc_info=True)
    finally:
        try:
            stream.close()
        except Exception: pass


# --- 新增：通知相关函数 ---

# 任务类型映射
TASK_TYPE_MAP = {
    "album": "专辑",
    "playlist": "播放列表",
    "music-video": "MV",
    # 可以根据需要添加更多类型
}

def get_task_display_info(task_data):
    """从任务数据中提取显示名称和中文类型。"""
    name = "未知名称"
    type_key = "未知类型"
    type_zh = "未知类型"

    try:
        metadata = task_data.get("metadata", {})
        link_info = task_data.get("link_info", {})

        if isinstance(metadata, dict):
            name = metadata.get("name", name)

        if isinstance(link_info, dict):
            type_key = link_info.get("type", type_key)
            type_zh = TASK_TYPE_MAP.get(type_key, f"类型({type_key})" if type_key != "未知类型" else "未知类型")

    except Exception as e:
        logging.warning(f"任务 {task_data.get('uuid', '未知UUID')}: 提取显示信息时出错: {e}")

    return name, type_zh, type_key # 返回 key 用于可能的逻辑判断

# --- 添加全局变量用于轮询算法 ---
decrypt_port_counter = 0
get_port_counter = 0
port_counter_lock = threading.Lock()

def get_next_decryptor_port(source_data_dict):
    """从 source.yaml 中获取下一个解密器端口，实现轮询负载均衡"""
    global decrypt_port_counter, get_port_counter
    
    decrypt_m3u8_port = source_data_dict.get('decrypt-m3u8-port', '')
    get_m3u8_port = source_data_dict.get('get-m3u8-port', '')
    
    # 检查是否为列表格式
    if isinstance(decrypt_m3u8_port, list) and decrypt_m3u8_port:
        with port_counter_lock:
            decrypt_port_counter = (decrypt_port_counter + 1) % len(decrypt_m3u8_port)
            decrypt_m3u8_port = decrypt_m3u8_port[decrypt_port_counter]
    
    if isinstance(get_m3u8_port, list) and get_m3u8_port:
        with port_counter_lock:
            get_port_counter = (get_port_counter + 1) % len(get_m3u8_port)
            get_m3u8_port = get_m3u8_port[get_port_counter]
    
    return decrypt_m3u8_port, get_m3u8_port

def execute_single_track(task_data, track, user_notification_config, max_retries, retry_delay, go_main_bin_path):
    """执行单个音轨的下载任务"""
    uuid = task_data.get("uuid")
    skip_check = task_data.get("skip_check", False)  # 获取 skip_check 参数，默认为 False
    
    # 构建基本命令
    cmd = [go_main_bin_path, "--uuid", uuid, "--song-id", track["song_id"]]
    
    # 如果是校验任务，永远不使用 skip_check
    if task_data.get("status") == "checking":
        skip_check = False
    
    # 如果 skip_check 为 True，添加 --skip-check 参数
    if skip_check:
        cmd.append("--skip-check")
    
    # 添加其他必要的参数
    if user_notification_config:
        # 获取第一个可用的通知URL
        bark_urls = user_notification_config.get("bark_urls", [])
        if bark_urls and isinstance(bark_urls, list) and len(bark_urls) > 0:
            cmd.extend(["--notify-url", bark_urls[0]])
            if user_notification_config.get("token"):
                cmd.extend(["--notify-token", user_notification_config["token"]])
    
    user = task_data.get("user", "未知用户")
    track_number = track.get('track_number')
    track_url = track.get('url')
    track_name = track.get('name', f"音轨 {track_number}")
    song_id = track.get('song_id')
    disc_number = track.get('disc_number')
    log_disc_info = f" (光盘 {disc_number})" if disc_number else ""
    if not track_url:
        log_msg = f"任务 {uuid}, 音轨 {track_number}{log_disc_info}" + (f" (Song ID {song_id})" if song_id else "") + ": 缺少URL信息。"
        logging.error(log_msg)
        return False, f"音轨 {track_number} 缺少URL信息", ""
    log_prefix = f"任务 {uuid}, 音轨 {track_number}{log_disc_info}" + (f" (Song ID {song_id})" if song_id else " (无 Song ID)")
    logging.info(f"{log_prefix}: 开始处理: {track_name}")
    track_success = False
    final_error_log = ""
    final_error_reason = ""
    is_check_task = (track_number == 0)
    for attempt in range(max_retries + 1):
        logging.info(f"{log_prefix}: 开始尝试 {attempt + 1}/{max_retries + 1}")
        process = None
        attempt_total_output = ""
        source_yaml_template_string = None
        # 校验任务不计入全局Go进程数
        if not is_check_task:
            global current_global_go_processes, max_global_go_processes, global_go_processes_condition
            with global_go_processes_condition:
                while current_global_go_processes >= max_global_go_processes:
                    logging.info(f"全局 Go 进程数已达上限({max_global_go_processes})，等待空闲...")
                    global_go_processes_condition.wait()
                current_global_go_processes += 1
        try:
            source_path = file_paths.get('source')
            source_lock = file_locks.get('source')
            if not source_path or not source_lock:
                msg = f"{log_prefix}: Source.yaml 路径或锁未配置。"
                logging.error(msg)
                final_error_reason = msg; final_error_log = msg
                break
            
            # --- 新增：从 backend.py 获取 API Token ---
            api_token_from_backend = None
            try:
                logging.info(f"{log_prefix}: 正在从 http://localhost:5000/token 获取 API Token。")
                token_response = requests.get("http://localhost:5000/token", timeout=5)
                token_response.raise_for_status()
                token_data = token_response.json()
                if token_data and "token" in token_data:
                    api_token_from_backend = token_data["token"]
                    logging.info(f"{log_prefix}: 成功获取 API Token。")
                else:
                    logging.warning(f"{log_prefix}: 获取 API Token 失败，响应中缺少 token 字段或数据无效。")
            except requests.exceptions.RequestException as req_err:
                logging.warning(f"{log_prefix}: 请求 /token 接口失败: {req_err}")
            except Exception as e_token:
                logging.warning(f"{log_prefix}: 获取 API Token 时发生未知错误: {e_token}")
            # --- API Token 获取结束 ---

            source_data_dict = read_yaml_with_lock(source_path, source_lock)
            if isinstance(source_data_dict, dict):
                # --- 新增：将获取到的 API Token 添加到 source_data_dict ---
                if api_token_from_backend:
                    source_data_dict["api_token"] = api_token_from_backend
                    logging.debug(f"{log_prefix}: 已将 API Token 添加到传递给 Go 的配置中。")
                else:
                    logging.warning(f"{log_prefix}: 未能获取 API Token，Go 程序将尝试自行获取。")
                # --- API Token 添加结束 ---
                try:
                    decrypt_m3u8_port, get_m3u8_port = get_next_decryptor_port(source_data_dict)
                    source_data_dict['decrypt-m3u8-port'] = decrypt_m3u8_port
                    source_data_dict['get-m3u8-port'] = get_m3u8_port
                    logging.info(f"{log_prefix}: 使用解密器端口 {decrypt_m3u8_port}, 获取端口 {get_m3u8_port}")
                    source_yaml_template_string = yaml.dump(source_data_dict, default_flow_style=False, allow_unicode=True)
                except Exception as e:
                    logging.error(f"{log_prefix}: source_data 转 YAML 失败: {e}")
                    source_yaml_template_string = ""
            else:
                logging.warning(f"{log_prefix}: {source_path} 内容无效")
                source_yaml_template_string = ""
            if not source_yaml_template_string:
                msg = f"{log_prefix}: 无法获取 source.yaml 内容。"
                logging.error(msg)
                final_error_reason = msg; final_error_log = msg
                break
            try:
                yaml_input_string = source_yaml_template_string.replace("{user}", user)
            except Exception as e:
                logging.error(f"{log_prefix}: 替换 {{user}} 失败: {e}")
                final_error_reason = "source.yaml 替换错误"; final_error_log = f"替换错误: {e}"
                break
            go_command = [go_main_bin_path, track_url]
            if track_number != 0:
                go_command.append("--song")
            # 修改这里：只有在非校验任务且 skip_check 为 True 时才添加 --skip-check
            if not is_check_task and skip_check:
                go_command.append("--skip-check")
            process_cwd = PROJECT_ROOT
            logging.info(f"{log_prefix}: 准备执行: {' '.join(go_command)}")
            process = subprocess.Popen(go_command, cwd=process_cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace')
            stdout_lines = []
            stderr_lines = []
            import threading
            retry_event = threading.Event()
            # 校验任务用特殊的stream处理
            if is_check_task:
                # 获取tracks列表
                tracks = task_data.get('metadata', {}).get('tracks', [])
                def check_read_stream(stream, output_list, uuid, update_callback, retry_event, tracks):
                    logging.info(f"check_read_stream已启动，等待Go输出...")
                    last_global_track_number = None
                    for line in iter(stream.readline, ''):
                        output_list.append(line)
                        m = re.search(r'Track (\d+) of', line)
                        if m:
                            last_global_track_number = int(m.group(1))
                            logging.info(f"检测到全局音轨号: {last_global_track_number}")
                            continue
                        logging.info(f"[Go校验输出] {line.strip()}")
                        if last_global_track_number:
                            # 通过全局音轨号查找对应的音轨
                            current = 0
                            target_track = None
                            for track in sorted(tracks, key=lambda t: (t.get('disc_number', 1), t.get('track_number', 1))):
                                current += 1
                                if current == last_global_track_number:
                                    target_track = track
                                    break
                            
                            if target_track:
                                song_id = target_track.get('song_id')
                                if song_id:
                                    if "Track already exists locally." in line:
                                        logging.info(f"即将写入: uuid={uuid}, global_track_number={last_global_track_number}, check_success=True")
                                        check_write_queue.put({'uuid': uuid, 'global_track_number': last_global_track_number, 'update_data': {'check_success': True}})
                                        # 推送SSE更新
                                        update_data = {
                                            "check_success": True,
                                            "download_progress": {
                                                "current": 1,
                                                "total": 1,
                                                "percent": 100
                                            }
                                        }
                                        update_track_progress_in_file(uuid, song_id, update_data)
                                        continue
                                    if any(x in line for x in ["Decrypted", "Downloaded", "connected"]):
                                        logging.info(f"即将写入: uuid={uuid}, global_track_number={last_global_track_number}, check_success=True, 100%")
                                        check_write_queue.put({'uuid': uuid, 'global_track_number': last_global_track_number, 'update_data': {
                                            'check_success': True,
                                            'download_progress': {
                                                'current': 1,
                                                'total': 1,
                                                'percent': 100
                                            }
                                        }})
                                        # 推送SSE更新
                                        update_data = {
                                            "check_success": True,
                                            "download_progress": {
                                                "current": 1,
                                                "total": 1,
                                                "percent": 100
                                            }
                                        }
                                        update_track_progress_in_file(uuid, song_id, update_data)
                                        continue
                        if retry_event is not None and "Error detected, press Enter to try again..." in line:
                            logging.warning(f"[Go_UUID='{uuid}'] 检测到Go重试信号，准备自动回车重试")
                            retry_event.set()
                            break
                stdout_thread = threading.Thread(target=check_read_stream, args=(process.stdout, stdout_lines, uuid, update_track_progress_in_file, retry_event, tracks), name=f"stdout-{uuid[:8]}")
            else:
                stdout_thread = threading.Thread(target=read_stream, args=(process.stdout, stdout_lines, uuid, song_id, update_track_progress_in_file, retry_event), name=f"stdout-{uuid[:8]}")
            stderr_thread = threading.Thread(target=read_stream, args=(process.stderr, stderr_lines, uuid, None, None), name=f"stderr-{uuid[:8]}")
            stdout_thread.start()
            stderr_thread.start()
            try:
                process.stdin.write(yaml_input_string)
                process.stdin.close()  # 必须关闭stdin，否则Go进程会卡住
            except (IOError, BrokenPipeError) as e:
                logging.warning(f"{log_prefix}: 写入 stdin 错误: {e}")
            except Exception as e:
                logging.error(f"{log_prefix}: 写入 stdin 未知错误: {e}")
            go_retry_triggered = False
            while process.poll() is None:
                if retry_event.is_set():
                    logging.warning(f"{log_prefix}: 检测到Go重试信号，终止本次Go进程，计入一次Python重试")
                    go_retry_triggered = True
                    process.kill()
                    break
                process_check_write_queue()
                time.sleep(0.1)
            stdout_thread.join(timeout=20)
            stderr_thread.join(timeout=20)
            process_check_write_queue()
            if stdout_thread.is_alive() or stderr_thread.is_alive():
                logging.warning(f"{log_prefix}: 输出读取线程超时未结束。")
            return_code = process.returncode
            attempt_total_output = "".join(stdout_lines + stderr_lines)
            if go_retry_triggered:
                logging.info(f"{log_prefix}: 本次Go进程因重试信号被终止，进入下一次Python重试。")
                if attempt < max_retries:
                    logging.info(f"{log_prefix}: {retry_delay} 秒后重试。")
                    time.sleep(retry_delay)
                    continue
                else:
                    logging.error(f"{log_prefix}: 达到最大次数尝试，任务失败。")
                    break
            attempt_success, attempt_reason = analyze_go_output(return_code, attempt_total_output)
            if attempt_success:
                logging.info(f"{log_prefix}: 尝试 {attempt + 1} 成功。")
                track_success = True
                break
            else:
                logging.warning(f"{log_prefix}: 尝试 {attempt + 1} 失败。原因: {attempt_reason}")
                final_error_log = attempt_total_output
                final_error_reason = attempt_reason
                if attempt < max_retries:
                    logging.info(f"{log_prefix}: {retry_delay} 秒后重试。")
                    time.sleep(retry_delay)
                else:
                    logging.error(f"{log_prefix}: 达到最大次数尝试，任务失败。")
                    break
        except Exception as e:
            msg = f"{log_prefix}: 执行尝试 {attempt + 1} 时发生意外错误: {e}"
            logging.error(msg, exc_info=True)
            track_success = False
            final_error_reason = f"意外错误: {e}"
            final_error_log = msg + "\n" + traceback.format_exc()
            if attempt < max_retries:
                logging.info(f"{log_prefix}: 因意外错误，{retry_delay} 秒后重试。")
                time.sleep(retry_delay)
            else:
                logging.error(f"{log_prefix}: 因意外错误达到最大次数，任务失败。")
                break
        finally:
            if process:
                try:
                    process.kill()
                except Exception:
                    pass
            if not is_check_task:
                with global_go_processes_condition:
                    current_global_go_processes -= 1
                    global_go_processes_condition.notify_all()
    return track_success, final_error_reason, final_error_log

def execute_task(task_data, user_notification_config):
    """通过运行 Go 二进制执行任务，并在完成后触发 Bark 和 Emby 通知。"""
    uuid = task_data.get("uuid", "未知UUID")
    user = task_data.get("user", "未知用户")
    link = task_data.get("link", "未知链接")
    threading.current_thread().name = f"任务-{uuid[:8]}"
    logging.info(f"任务开始执行。UUID: {uuid}, 用户: {user}")

    # 获取通知配置
    emby_url = user_notification_config.get('emby_url')
    # bark_urls 现在是一个对象列表，每个对象包含 server 和 click_url_template
    bark_configs = user_notification_config.get('bark_urls', []) 
    # bark_config = config_data.get('bark_notification', {}) # 不再需要全局 Bark 配置

    # 构建完整的 Bark URLs - 这部分逻辑需要大改，因为现在每个 bark_config 都有自己的 click_url_template
    # complete_bark_urls = [] # 不再构建这个列表，直接在发送时处理
    # for base_url in bark_urls:
    #     if base_url:
    #         # 构建完整的 URL，包含路径、图标和跳转链接
    #         complete_url = f"{base_url}{bark_config.get(\'path\', \'\')}?icon={bark_config.get(\'icon\', \'\')}&url={bark_config.get(\'url\', \'\')}"
    #         complete_bark_urls.append(complete_url)

    # 获取重试参数等 (保持不变)
    max_retries = config_data.get('MAX_RETRIES', DEFAULT_MAX_RETRIES)
    retry_delay = config_data.get('RETRY_DELAY', DEFAULT_RETRY_DELAY)
    go_main_bin_path = file_paths.get('go_main_bin')
    if not go_main_bin_path or not os.path.exists(go_main_bin_path) or not os.access(go_main_bin_path, os.X_OK):
        msg = f"任务 {uuid}: Go 二进制文件路径无效或不可执行: {go_main_bin_path}"
        logging.error(msg)
        update_task_status_in_file(uuid, "error", msg, msg)
        with running_set_lock:
            if uuid in running_task_uuids:
                running_task_uuids.remove(uuid)
        return
    # 检查任务类型
    task_type = task_data.get('link_info', {}).get('type', '')
    is_album_or_playlist = task_type in ['album', 'playlist']
    
    if is_album_or_playlist:
        tracks = task_data.get('metadata', {}).get('tracks', [])
        if not tracks:
            msg = f"任务 {uuid}: 专辑/播放列表任务没有音轨信息。"
            logging.error(msg)
            update_task_status_in_file(uuid, "error", msg, msg)
            with running_set_lock:
                if uuid in running_task_uuids:
                    running_task_uuids.remove(uuid)
            return
        
        # --- 移除按全局音轨号排序的逻辑 --- #
        # 可以选择按原始顺序或 disc/track 排序，如果需要的话
        # 简单的按 track_number (和 disc_number) 排序
        def get_sort_key(track):
            if not isinstance(track, dict): return (999, 999) # 无效 track 排最后
            disc = track.get('disc_number')
            track_num = track.get('track_number')
            # 处理 None 的情况，给一个默认大值
            disc = disc if disc is not None else 999
            track_num = track_num if track_num is not None else 999
            return (disc, track_num)
        try:
            tracks.sort(key=get_sort_key)
            logging.debug(f"任务 {uuid}: 音轨已按 disc/track number 排序。")
        except Exception as sort_e:
             logging.warning(f"任务 {uuid}: 按 disc/track number 排序时出错: {sort_e}，将按原始顺序处理。")
        # --- 排序逻辑结束 --- #

        # 获取最大并行任务数 (保持不变)
        max_workers = config_data.get('MAX_PARALLEL_TASKS', 10)
        
        # 创建线程池 (保持不变)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            # --- 提交所有音轨的下载任务 (传递 track 字典) --- #
            future_to_track = {
                executor.submit(
                    execute_single_track,
                    task_data, # 整个任务数据
                    track,     # 单个音轨字典 (包含 song_id)
                    user_notification_config,
                    max_retries,
                    retry_delay,
                    go_main_bin_path
                    # track_lookup_map 不再传递
                ): track # value 仍然是 track 字典，用于日志
                for track in tracks if isinstance(track, dict) # 确保 track 是字典
            }
            # --- 提交结束 --- #

            # 收集结果 (保持不变)
            success_count = 0
            failure_count = 0
            final_error_log = ""
            final_error_reason = ""

            for future in concurrent.futures.as_completed(future_to_track):
                track = future_to_track[future]
                log_track_num = track.get('track_number', '?')
                log_song_id = track.get('song_id', 'N/A')
                try:
                    track_success, error_reason, error_log = future.result()
                    if track_success:
                        success_count += 1
                    else:
                        failure_count += 1
                        # 记录第一个遇到的错误原因和日志
                        if not final_error_reason and error_reason:
                            final_error_reason = f"音轨 {log_track_num} (ID: {log_song_id}) 失败: {error_reason}"
                        if not final_error_log and error_log:
                            final_error_log = f"--- 音轨 {log_track_num} (ID: {log_song_id}) 错误日志 ---\n{error_log}"
                except Exception as e:
                    failure_count += 1
                    err_msg = f"音轨 {log_track_num} (ID: {log_song_id}) 执行失败: {e}"
                    logging.error(f"任务 {uuid}, {err_msg}", exc_info=True)
                    if not final_error_reason:
                         final_error_reason = err_msg
                    if not final_error_log:
                         final_error_log = traceback.format_exc()

        # 根据结果更新任务状态 (修改：添加专辑校验步骤)
        if failure_count == 0:
            # 如果是专辑类型，执行校验步骤
            if task_type == 'album':
                # --- NEW: Check if all tracks exist locally ---
                all_tracks_exist_locally = False # Default to false, proceed with check
                task_queue_path_check = file_paths.get('task_queue')
                task_queue_lock_obj_check = file_locks.get('task_queue')

                if task_queue_path_check and task_queue_lock_obj_check:
                    try:
                        current_tasks_for_check = read_json_with_lock(task_queue_path_check, task_queue_lock_obj_check, default=None)
                        if current_tasks_for_check and isinstance(current_tasks_for_check, list):
                            current_task_data_for_check = next((t for t in current_tasks_for_check if t.get("uuid") == uuid), None)
                            if current_task_data_for_check:
                                album_tracks = current_task_data_for_check.get('metadata', {}).get('tracks', [])
                                # Ensure album_tracks is a non-empty list of dicts
                                if album_tracks and isinstance(album_tracks, list) and all(isinstance(track_item, dict) for track_item in album_tracks):
                                    all_tracks_exist_locally = True # Assume true until a track doesn't meet criteria
                                    for track_item in album_tracks:
                                        if not (track_item.get('download_status') == 'exists' and \
                                                track_item.get('decryption_status') == 'exists'):
                                            all_tracks_exist_locally = False
                                            logging.debug(f"任务 {uuid}: 音轨 {track_item.get('track_number', '未知')} (ID: {track_item.get('song_id', '未知')}) 不满足 'exists' 状态。专辑校验将执行。")
                                            break
                                    if all_tracks_exist_locally:
                                        logging.info(f"任务 {uuid}: 所有音轨均已存在本地，将跳过专辑校验步骤。")
                                else: # album_tracks is empty or not a list of dicts
                                    logging.info(f"任务 {uuid}: 专辑元数据中无有效音轨信息或音轨列表为空，将执行专辑校验。")
                                    all_tracks_exist_locally = False # Ensure check runs
                            else:
                                logging.warning(f"任务 {uuid}: 在队列中未找到任务数据，无法检查音轨状态以跳过校验。将执行专辑校验。")
                        else:
                            logging.warning(f"任务 {uuid}: 读取任务队列失败或格式无效，无法检查音轨状态以跳过校验。将执行专辑校验。")
                    except Exception as e_read_q_check:
                        logging.error(f"任务 {uuid}: 检查音轨状态以跳过校验时读取任务队列异常: {e_read_q_check}，将执行专辑校验。", exc_info=True)
                else:
                    logging.error(f"任务 {uuid}: 任务队列路径或锁未配置，无法检查音轨状态以跳过校验。将执行专辑校验。")
                # --- END NEW CHECK ---

                if all_tracks_exist_locally:
                    logging.info(f"任务 {uuid}: 所有音轨已存在，标记任务完成。")
                    process_complete_time_iso = datetime.now(timezone.utc).isoformat()
                    update_task_status_in_file(uuid, "finish", None, None, process_complete_time_iso, checking=False)
                    if emby_url:
                        send_emby_refresh(user, uuid)
                    
                    emby_album_id_for_bark = None
                    if bark_configs:
                        task_name_for_emby_query, _, _ = get_task_display_info(task_data)
                        emby_url_for_query = user_notification_config.get('emby_url')
                        emby_api_key_for_query = user_notification_config.get('emby_api_key')
                        if emby_url_for_query and emby_api_key_for_query and task_name_for_emby_query:
                            from notifications import query_emby_album_id
                            emby_album_id_for_bark = query_emby_album_id(emby_url_for_query, emby_api_key_for_query, task_name_for_emby_query)
                            if emby_album_id_for_bark:
                                logging.info(f"任务 {uuid}: (全部已存在) 成功为 Bark 获取 Emby Album ID: {emby_album_id_for_bark}")
                            else:
                                logging.warning(f"任务 {uuid}: (全部已存在) 未能为 Bark 获取 Emby Album ID (专辑: {task_name_for_emby_query})")
                        else:
                            logging.warning(f"任务 {uuid}: (全部已存在) 无法为 Bark 查询 Emby Album ID，缺少 Emby URL/API Key/专辑名。")

                    for bark_config_item in bark_configs:
                        bark_server = bark_config_item.get('server')
                        click_template = bark_config_item.get('click_url_template')
                        if bark_server:
                            send_bark_notification(bark_server, click_template, user, task_data, True, emby_album_id=emby_album_id_for_bark)
                    # --- 新增：发送任务完成通知到前端 ---
                    task_name, task_type_zh, _ = get_task_display_info(task_data)
                    notice_data = {
                        "event": "task_completed",
                        "type": "success",
                        "uuid": uuid,
                        "user": user,
                        "task_name": task_name,
                        "task_type": task_type_zh,
                        "message": f"专辑《{task_name}》下载完成",
                        "timestamp": process_complete_time_iso
                    }
                    send_notice_to_clients(notice_data)
                else:
                    # Original logic: proceed with album check
                    logging.info(f"任务 {uuid}: 所有音轨下载完成，但并非所有音轨都已存在本地 (或无法确认)，开始执行专辑校验...")
                    # 标记校验开始
                    update_task_status_in_file(uuid, "running", checking=True)
                    # 创建一个简化的 track 字典用于校验
                    album_check_track = {
                        "track_number": 0,  # 使用0表示这是专辑校验
                        "url": link,
                        "name": task_data.get('metadata', {}).get('name', "专辑校验"),
                        "song_id": None
                    }
                    # 执行校验
                    check_success, check_reason, check_log = execute_single_track(
                        task_data,
                        album_check_track,
                        user_notification_config,
                        max_retries,
                        retry_delay,
                        go_main_bin_path
                    )
                    # 校验结束，移除checking
                    update_task_status_in_file(uuid, "running", checking=False)
                    if check_success:
                        logging.info(f"任务 {uuid}: 专辑校验成功，任务完成。")
                        process_complete_time_iso = datetime.now(timezone.utc).isoformat()
                        update_task_status_in_file(uuid, "finish", None, None, process_complete_time_iso, checking=False)
                        if emby_url:
                            send_emby_refresh(user, uuid)
                        
                        # --- 在发送 Bark 通知前获取 Emby Album ID ---
                        emby_album_id_for_bark = None
                        if bark_configs:
                            task_name_for_emby_query, _, _ = get_task_display_info(task_data) # 获取任务名
                            emby_url_for_query = user_notification_config.get('emby_url')
                            emby_api_key_for_query = user_notification_config.get('emby_api_key')
                            if emby_url_for_query and emby_api_key_for_query and task_name_for_emby_query:
                                logging.info(f"任务 {uuid}: 尝试为 Bark 通知获取 Emby Album ID (专辑: {task_name_for_emby_query})...")
                                # 需要从 notifications.py 导入 query_emby_album_id
                                # (确保此函数在 notifications.py 中是可导入的)
                                from notifications import query_emby_album_id 
                                emby_album_id_for_bark = query_emby_album_id(emby_url_for_query, emby_api_key_for_query, task_name_for_emby_query)
                                if emby_album_id_for_bark:
                                    logging.info(f"任务 {uuid}: 成功获取 Emby Album ID for Bark: {emby_album_id_for_bark}")
                                else:
                                    logging.warning(f"任务 {uuid}: 未能为 Bark 通知获取 Emby Album ID (专辑: {task_name_for_emby_query}).")
                            else:
                                logging.warning(f"任务 {uuid}: 无法为 Bark 通知查询 Emby Album ID，缺少 Emby URL, API Key 或专辑名。")
                        else:
                            logging.warning(f"任务 {uuid}: 无法为 Bark 通知查询 Emby Album ID，缺少 Emby URL, API Key 或专辑名。")

                        # 发送到所有配置的 Bark URLs
                        for bark_config_item in bark_configs: # <--- 迭代新的 bark_configs
                            bark_server = bark_config_item.get('server')
                            click_template = bark_config_item.get('click_url_template')
                            if bark_server:
                                send_bark_notification(bark_server, click_template, user, task_data, True, emby_album_id=emby_album_id_for_bark) # <--- 传递 emby_album_id
                        
                        # --- 新增：发送任务完成通知到前端 ---
                        task_name, task_type_zh, _ = get_task_display_info(task_data)
                        notice_data = {
                            "event": "task_completed",
                            "type": "success",
                            "uuid": uuid,
                            "user": user,
                            "task_name": task_name,
                            "task_type": task_type_zh,
                            "message": f"专辑《{task_name}》下载完成",
                            "timestamp": process_complete_time_iso
                        }
                        send_notice_to_clients(notice_data)
                    else:
                        logging.error(f"任务 {uuid}: 专辑校验失败。原因: {check_reason}")
                        process_complete_time_iso = datetime.now(timezone.utc).isoformat()
                        error_msg = f"专辑校验失败: {check_reason}"
                        update_task_status_in_file(uuid, "error", error_msg, check_log, process_complete_time_iso, checking=False)
                        if emby_url:
                            send_emby_refresh(user, uuid)
                        # 发送到所有配置的 Bark URLs
                        for bark_config_item in bark_configs: # <--- 迭代新的 bark_configs
                            bark_server = bark_config_item.get('server')
                            click_template = bark_config_item.get('click_url_template')
                            if bark_server:
                                send_bark_notification(bark_server, click_template, user, task_data, False)
                        # --- 新增：发送任务失败通知到前端 ---
                        task_name, task_type_zh, _ = get_task_display_info(task_data)
                        notice_data = {
                            "event": "task_completed",
                            "type": "error",
                            "uuid": uuid,
                            "user": user,
                            "task_name": task_name,
                            "task_type": task_type_zh,
                            "message": f"专辑《{task_name}》下载失败: {error_msg}",
                            "timestamp": process_complete_time_iso
                        }
                        send_notice_to_clients(notice_data)
            else:
                # 播放列表直接完成
                process_complete_time_iso = datetime.now(timezone.utc).isoformat()
                update_task_status_in_file(uuid, "finish", None, None, process_complete_time_iso)
                if emby_url:
                    send_emby_refresh(user, uuid)
                
                # --- 在发送 Bark 通知前获取 Emby Album ID (同上) ---
                emby_album_id_for_bark_playlist = None
                if bark_configs: # 仅当是专辑类型时才尝试获取，播放列表通常不直接对应单个Emby专辑ID
                    task_name_for_emby_query_pl, _, _ = get_task_display_info(task_data)
                    emby_url_for_query_pl = user_notification_config.get('emby_url')
                    emby_api_key_for_query_pl = user_notification_config.get('emby_api_key')
                    if emby_url_for_query_pl and emby_api_key_for_query_pl and task_name_for_emby_query_pl:
                        logging.info(f"任务 {uuid}: (播放列表场景下，如果包含专辑) 尝试为 Bark 通知获取 Emby Album ID (专辑: {task_name_for_emby_query_pl})...")
                        from notifications import query_emby_album_id
                        emby_album_id_for_bark_playlist = query_emby_album_id(emby_url_for_query_pl, emby_api_key_for_query_pl, task_name_for_emby_query_pl)
                        if emby_album_id_for_bark_playlist:
                            logging.info(f"任务 {uuid}: (播放列表场景) 成功获取 Emby Album ID for Bark: {emby_album_id_for_bark_playlist}")
                        else:
                            logging.warning(f"任务 {uuid}: (播放列表场景) 未能为 Bark 通知获取 Emby Album ID (专辑: {task_name_for_emby_query_pl}).")
                    else:
                        logging.warning(f"任务 {uuid}: (播放列表场景) 无法为 Bark 通知查询 Emby Album ID，缺少 Emby URL, API Key 或专辑名。")
                # --- 获取结束 ---

                # 发送到所有配置的 Bark URLs
                for bark_config_item in bark_configs: # <--- 迭代新的 bark_configs
                    bark_server = bark_config_item.get('server')
                    click_template = bark_config_item.get('click_url_template')
                    if bark_server:
                        # 播放列表任务成功时，如果之前获取了album_id (理论上仅针对专辑内单曲)，则传递，否则不传
                        send_bark_notification(bark_server, click_template, user, task_data, True, emby_album_id=emby_album_id_for_bark_playlist if task_type == 'album' else None)
                # --- 新增：发送任务完成通知到前端 ---
                task_name, task_type_zh, _ = get_task_display_info(task_data)
                notice_data = {
                    "event": "task_completed",
                    "type": "success",
                    "uuid": uuid,
                    "user": user,
                    "task_name": task_name,
                    "task_type": task_type_zh,
                    "message": f"播放列表《{task_name}》下载完成",
                    "timestamp": process_complete_time_iso
                }
                send_notice_to_clients(notice_data)
        else:
            process_complete_time_iso = datetime.now(timezone.utc).isoformat()
            error_msg = f"任务失败: {failure_count} 个音轨下载失败。首个错误: {final_error_reason}"
            update_task_status_in_file(uuid, "error", error_msg, final_error_log, process_complete_time_iso)
            if emby_url:
                send_emby_refresh(user, uuid)
            
            # --- 失败情况下也尝试获取 Album ID (如果配置了Bark) ---
            emby_album_id_for_bark_fail = None
            if task_type == 'album' and bark_configs: # 仅专辑类型
                task_name_for_emby_query_fail, _, _ = get_task_display_info(task_data)
                emby_url_for_query_fail = user_notification_config.get('emby_url')
                emby_api_key_for_query_fail = user_notification_config.get('emby_api_key')
                if emby_url_for_query_fail and emby_api_key_for_query_fail and task_name_for_emby_query_fail:
                    logging.info(f"任务 {uuid}: (失败情况) 尝试为 Bark 通知获取 Emby Album ID (专辑: {task_name_for_emby_query_fail})...")
                    from notifications import query_emby_album_id
                    emby_album_id_for_bark_fail = query_emby_album_id(emby_url_for_query_fail, emby_api_key_for_query_fail, task_name_for_emby_query_fail)
                    if emby_album_id_for_bark_fail:
                        logging.info(f"任务 {uuid}: (失败情况) 成功获取 Emby Album ID for Bark: {emby_album_id_for_bark_fail}")
                    else:
                        logging.warning(f"任务 {uuid}: (失败情况) 未能为 Bark 通知获取 Emby Album ID (专辑: {task_name_for_emby_query_fail}).")
                else:
                    logging.warning(f"任务 {uuid}: (失败情况) 无法为 Bark 通知查询 Emby Album ID，缺少 Emby URL, API Key 或专辑名。")
            # --- 获取结束 ---

            # 发送到所有配置的 Bark URLs
            for bark_config_item in bark_configs: # <--- 迭代新的 bark_configs
                bark_server = bark_config_item.get('server')
                click_template = bark_config_item.get('click_url_template')
                if bark_server:
                    send_bark_notification(bark_server, click_template, user, task_data, False, emby_album_id=emby_album_id_for_bark_fail) # <--- 传递 emby_album_id
            # --- 新增：发送任务失败通知到前端 ---
            task_name, task_type_zh, _ = get_task_display_info(task_data)
            notice_data = {
                "event": "task_completed",
                "type": "error",
                "uuid": uuid,
                "user": user,
                "task_name": task_name,
                "task_type": task_type_zh,
                "message": f"任务《{task_name}》下载失败: {error_msg}",
                "timestamp": process_complete_time_iso
            }
            send_notice_to_clients(notice_data)
    
    else:
        # 处理单个音轨/MV的情况
        # --- 创建一个简化的 track 字典，不包含 song_id --- #
        # 因为对于非专辑/播放列表，我们不期望有 song_id 或进行精细状态更新
        single_item_track_dict = {
            "track_number": 1, # 虚拟音轨号
            "url": link,
            "name": task_data.get('metadata', {}).get('name', "单项任务"), # 尝试用元数据名
            "song_id": None # 明确标记无 song_id
        }
        track_success, error_reason, error_log = execute_single_track(
            task_data,
            single_item_track_dict, # 传递简化字典
            user_notification_config,
            max_retries,
            retry_delay,
            go_main_bin_path
        )
        # --- 修改结束 --- #

        # 更新整体任务状态 (逻辑不变)
        if track_success:
            process_complete_time_iso = datetime.now(timezone.utc).isoformat()
            update_task_status_in_file(uuid, "finish", None, None, process_complete_time_iso)
            if emby_url:
                send_emby_refresh(user, uuid)
            # 发送到所有配置的 Bark URLs
            for bark_config_item in bark_configs: # <--- 迭代新的 bark_configs
                bark_server = bark_config_item.get('server')
                click_template = bark_config_item.get('click_url_template')
                if bark_server:
                    send_bark_notification(bark_server, click_template, user, task_data, True)
            # --- 新增：发送任务完成通知到前端 ---
            task_name, task_type_zh, _ = get_task_display_info(task_data)
            notice_data = {
                "event": "task_completed",
                "type": "success",
                "uuid": uuid,
                "user": user,
                "task_name": task_name,
                "task_type": task_type_zh,
                "message": f"任务《{task_name}》下载完成",
                "timestamp": process_complete_time_iso
            }
            send_notice_to_clients(notice_data)
        else:
            process_complete_time_iso = datetime.now(timezone.utc).isoformat()
            update_task_status_in_file(uuid, "error", error_reason, error_log, process_complete_time_iso)
            if emby_url:
                send_emby_refresh(user, uuid)
            # 发送到所有配置的 Bark URLs
            for bark_config_item in bark_configs: # <--- 迭代新的 bark_configs
                bark_server = bark_config_item.get('server')
                click_template = bark_config_item.get('click_url_template')
                if bark_server:
                    send_bark_notification(bark_server, click_template, user, task_data, False)
            # --- 新增：发送任务失败通知到前端 ---
            task_name, task_type_zh, _ = get_task_display_info(task_data)
            notice_data = {
                "event": "task_completed",
                "type": "error",
                "uuid": uuid,
                "user": user,
                "task_name": task_name,
                "task_type": task_type_zh,
                "message": f"任务《{task_name}》下载失败: {error_reason}",
                "timestamp": process_complete_time_iso
            }
            send_notice_to_clients(notice_data)
    
    # 清理运行集合 (保持不变)
    with running_set_lock:
        if uuid in running_task_uuids:
            running_task_uuids.remove(uuid)


# --- 加载配置和文件路径 ---
def load_config_and_paths():
    """加载配置(config/users)，路径，锁，日志，检查 users.yaml。"""
    global config_data, users_data, file_paths, file_locks
    global max_global_go_processes

    # 1. 读取 config.yaml (使用 utils 函数)
    config_path = os.path.join(PROJECT_ROOT, "config", "config.yaml")
    config_lock_obj = filelock.FileLock(f"{config_path}.lock")
    config_data_local = read_yaml_with_lock(config_path, config_lock_obj)

    # 检查 config.yaml 是否成功读取且是字典
    if not isinstance(config_data_local, dict) or not config_data_local:
        # read_yaml_with_lock 内部会记录错误，这里直接退出
        logging.critical(f"无法加载有效的配置文件: {config_path}。请检查文件是否存在、格式是否正确以及 utils.py 中的错误日志。")
        sys.exit(1)

    config_data.update(config_data_local)
    # --- 调用 utils.setup_logging --- #
    # 需要在任何 logging 调用之前，并且在 config_data 加载之后
    setup_logging(config_data, script_chinese_name="任务调度器")

    # 现在可以安全地使用 logging 了
    logging.info(f"成功加载配置文件: {config_path}")

    max_global_go_processes = config_data.get('MAX_GLOBAL_GO_PROCESSES', 10)
    logging.info(f"全局最大 Go 进程数限制: {max_global_go_processes}")

    # --- 移除旧的日志级别更新逻辑，因为 setup_logging 已处理 ---
    # log_level_str = config_data.get("LOG_LEVEL", "INFO").upper()
    # ... (相关的 logger.setLevel 和 handler.setLevel 调用已移除)

    # 2. 确定文件路径 (使用 utils 函数)
    loaded_paths_config = config_data.get('PATHS', {})
    # 使用 utils 解析路径
    resolved_file_paths = resolve_paths(PROJECT_ROOT, loaded_paths_config, DEFAULT_PATHS_RELATIVE_TO_ROOT)

    # --- 动态计算 Go 二进制路径 --- #
    go_bin_parent_dir = os.path.join(PROJECT_ROOT, 'bin') # 假设在 bin 目录下
    # 尝试从 config 获取名字，否则默认为 main
    go_main_bin_name = config_data.get("GO_MAIN_BIN_NAME", "main")
    # 考虑 Windows 的 .exe 后缀
    if sys.platform == "win32" and not go_main_bin_name.endswith(".exe"):
        go_main_bin_name += ".exe"
    go_main_bin_absolute_path = os.path.abspath(os.path.join(go_bin_parent_dir, go_main_bin_name))
    resolved_file_paths['go_main_bin'] = go_main_bin_absolute_path
    logging.debug(f"已动态计算路径 'go_main_bin': {go_main_bin_absolute_path}")
    # --- Go 路径计算结束 --- #

    file_paths.update(resolved_file_paths)

    # 3. 创建文件锁 (保持不变，但使用解析后的路径)
    locks_to_create = {}
    keys_to_lock = ['task_queue', 'errors', 'users', 'source']
    for key in keys_to_lock:
        abs_path = file_paths.get(key)
        if abs_path:
            lock_file_path = f"{abs_path}.lock"
            locks_to_create[key] = filelock.FileLock(lock_file_path)
            logging.debug(f"已为 '{key}' ({abs_path}) 创建文件锁对象: {lock_file_path}")
            lock_dir = os.path.dirname(lock_file_path)
            if lock_dir: 
                os.makedirs(lock_dir, exist_ok=True)
        else:
            logging.critical(f"文件 '{key}' 的路径未配置或解析失败。")
            if key in ['task_queue', 'errors', 'source', 'users']: 
                sys.exit(1)
    file_locks.update(locks_to_create)

    # 4. 读取 users.yaml (使用 utils 函数)
    users_path = file_paths.get('users')
    users_lock_obj = file_locks.get('users')
    if users_path and users_lock_obj:
        logging.info(f"正在加载用户配置文件: {users_path}")
        # 使用 utils 函数读取
        loaded_users_data = read_yaml_with_lock(users_path, users_lock_obj)
        if isinstance(loaded_users_data, dict):
            users_data.update(loaded_users_data)
            logging.info(f"成功加载 {len(users_data)} 个用户配置。")
            for uname, uconfig in users_data.items():
                if not isinstance(uconfig, dict):
                    logging.warning(f"用户 '{uname}' 的配置不是字典格式，可能导致后续查找失败。")
        else:
            logging.warning(f"用户配置文件 {users_path} 内容无效或为空，将使用空的用户数据。通知功能可能受影响。")
    else:
        logging.critical(f"用户配置文件路径或锁对象未能正确加载。")
        sys.exit(1)

    # 5. 日志文件路径确认 (utils.setup_logging 已经处理了 FileHandler 的创建和配置)
    # utils.setup_logging 会根据配置确定日志文件路径并设置处理器。
    # 此处不再需要单独配置 FileHandler 或检查其存在性。
    # 仅确认日志文件路径已记录 (主要用于调试或参考)
    log_file_from_config = config_data.get('log_file_path') # backend style
    if not log_file_from_config:
        paths_conf = config_data.get('paths', {})
        log_file_from_config = paths_conf.get('logs') # main/email_checker style
        if log_file_from_config:
            if not os.path.isabs(log_file_from_config):
                log_file_from_config = os.path.join(PROJECT_ROOT, log_file_from_config)
        else:
            log_file_from_config = os.path.join(PROJECT_ROOT, DEFAULT_PATHS_RELATIVE_TO_ROOT.get('logs', 'logs.log'))
    
    # 确保路径规范化
    log_file_final_path = os.path.normpath(log_file_from_config)
    logging.info(f"日志文件路径已由 utils.setup_logging 配置为: {log_file_final_path}")
    # --- 旧的 FileHandler 配置代码段已被移除 (correct_file_handler_exists 等) ---


# --- 主调度循环 (修改) ---
def main_scheduler_loop():
    """主循环，调度任务，并在队列空闲时发送邮件汇总。"""
    threading.current_thread().name = "队列处理器"
    logging.info("任务调度器主循环启动。")

    max_parallel = config_data.get('MAX_PARALLEL', DEFAULT_MAX_PARALLEL)
    # 使用新的长轮询间隔，并提供默认值
    long_poll_interval = config_data.get('SCHEDULER_LONG_POLL_INTERVAL', DEFAULT_SCHEDULER_LONG_POLL_INTERVAL)
    fast_poll_interval = config_data.get('SCHEDULER_FAST_POLL_INTERVAL', 3)  # 默认3秒
    signal_port = config_data.get('SCHEDULER_SIGNAL_PORT', DEFAULT_SCHEDULER_SIGNAL_PORT)
    task_queue_path = file_paths.get('task_queue')
    task_queue_lock_obj = file_locks.get('task_queue')
    errors_path = file_paths.get('errors')
    errors_lock_obj = file_locks.get('errors')

    # 初始化轮询间隔
    global current_poll_interval, fast_poll_mode
    with poll_interval_lock:
        current_poll_interval = long_poll_interval
        fast_poll_mode = False

    logging.info(f"调度器配置: 最大并行={max_parallel}, 长轮询间隔={long_poll_interval}s, 快速轮询间隔={fast_poll_interval}s, 信号端口={signal_port}")
    logging.info(f"监控任务队列: {task_queue_path}")

    # --- 设置 UDP 监听套接字 ---
    udp_socket = None
    try:
        udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # 允许端口重用，以防上次未正常关闭
        udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        udp_socket.bind(("localhost", signal_port))
        udp_socket.setblocking(False) # 设置为非阻塞
        logging.info(f"已在 localhost:{signal_port} 启动 UDP 信号监听。")
    except OSError as e:
        logging.critical(f"无法绑定 UDP 监听端口 localhost:{signal_port}。错误: {e}。请检查端口是否被占用或权限问题。调度器将无法接收即时唤醒信号。")
        # 不退出，但记录严重错误，调度器将仅依赖长轮询
    except Exception as e:
        logging.error(f"设置 UDP 监听时发生未知错误: {e}", exc_info=True)
        # 同样不退出

    while True:
        ready_to_read = []
        try:
            # --- 检查任务队列和运行状态 ---
            # 读取任务队列 (使用 utils 函数)
            current_tasks = read_json_with_lock(task_queue_path, task_queue_lock_obj, default=None)
            if current_tasks is None:
                 logging.error("主循环无法读取任务队列，跳过本次检查。")
                 time.sleep(long_poll_interval) # 发生错误时等待长间隔
                 continue
            if not isinstance(current_tasks, list):
                 logging.error("任务队列文件内容不是列表格式，无法处理。请检查文件。")
                 # 尝试清空或重置？暂时跳过
                 time.sleep(long_poll_interval)
                 continue

            # === 新增：检测并处理 metadata 为 null 的任务 ===
            tasks_to_resend = []
            tasks_to_keep = []
            for task in current_tasks:
                if task.get("metadata") is None and task.get("status") != "pending_meta":
                    tasks_to_resend.append({"user": task.get("user"), "link": task.get("link")})
                else:
                    tasks_to_keep.append(task)
            if len(tasks_to_resend) > 0:
                # 写回移除后的队列
                if write_json_with_lock(task_queue_path, task_queue_lock_obj, tasks_to_keep):
                    logging.info(f"检测到 {len(tasks_to_resend)} 个 metadata=null 且状态不为pending_meta的任务，已从队列移除并准备重发。")
                    # 重新POST到backend
                    try:
                        resp = requests.post("http://127.0.0.1:5000/task", json=tasks_to_resend, timeout=10)
                        logging.info(f"已重新POST {len(tasks_to_resend)} 个任务到后端，返回: {resp.status_code} {resp.text[:200]}")
                    except Exception as e:
                        logging.error(f"重发任务到后端失败: {e}")
                else:
                    logging.error("写回移除metadata=null任务后的队列失败！")
            # === 新增逻辑结束 ===

            # 检查运行和就绪状态 (不变)
            with running_set_lock: is_any_running = len(running_task_uuids) > 0
            is_any_ready = any(task.get("status") == "ready" for task in current_tasks)

            # --- 队列空闲时的处理 (逻辑保持不变) ---
            if not is_any_running and not is_any_ready:
                logging.debug("检测到任务队列空闲 (无 running 或 ready 任务)。")
                
                # 如果处于快速轮询模式，切换回长轮询
                with poll_interval_lock:
                    if fast_poll_mode:
                        logging.info("队列空闲，从快速轮询模式切换回长轮询模式。")
                        current_poll_interval = long_poll_interval
                        fast_poll_mode = False

                # 1. 识别所有完成的任务 (状态为 finish 或 error)
                completed_tasks = [task for task in current_tasks if task.get("status") in ["finish", "error"]]

                if completed_tasks:
                    logging.info(f"发现 {len(completed_tasks)} 个已完成的任务，准备处理邮件汇总和清理。")

                    # 2. 按用户分组并准备邮件内容
                    user_email_summaries = {} # { user: {'success': [name1,...], 'failure': [name1,...]} }

                    for task in completed_tasks:
                        user = task.get("user")
                        status = task.get("status")
                        uuid = task.get("uuid", "未知")
                        if not user:
                            logging.warning(f"任务 {uuid} 状态为 {status} 但缺少 'user' 字段，无法进行邮件汇总。")
                            continue

                        task_name, _, _ = get_task_display_info(task) # 获取任务名

                        if user not in user_email_summaries:
                            user_email_summaries[user] = {'success': [], 'failure': []}

                        if status == "finish":
                            user_email_summaries[user]['success'].append(task_name)
                        elif status == "error":
                            user_email_summaries[user]['failure'].append(task_name)

                    # 3. 发送汇总邮件给每个相关用户
                    logging.info(f"准备为 {len(user_email_summaries)} 个用户发送邮件汇总...")
                    for user, summary in user_email_summaries.items():
                        user_config = users_data.get(user, {})
                        emails = user_config.get('email', [])
                        if not emails or not isinstance(emails, list) or not emails[0]:
                            logging.warning(f"用户 {user} 没有配置有效的邮箱地址，无法发送邮件汇总。")
                            continue

                        recipient_email = emails[0] # 使用第一个邮箱
                        email_subject = "下载完成通知"
                        email_body_lines = []
                        email_body_lines.append(f"你好 {user},")
                        email_body_lines.append("本次任务处理结果如下：")
                        email_body_lines.append("-" * 20)

                        if summary['success']:
                            email_body_lines.append("成功任务:")
                            for i, name in enumerate(summary['success']):
                                # 找到对应的任务，添加更严格的空值检查
                                task = next((t for t in completed_tasks if isinstance(t, dict) and 
                                           isinstance(t.get('metadata'), dict) and 
                                           t.get('metadata', {}).get('name') == name), None)
                                if task and isinstance(task, dict):
                                    task_name, task_type_zh, _ = get_task_display_info(task)
                                    # 添加时间戳检查
                                    process_start_time_str = task.get('process_start_time')
                                    process_complete_time_str = task.get('process_complete_time')
                                    if process_start_time_str and process_complete_time_str:
                                        try:
                                            process_start_time = datetime.fromisoformat(process_start_time_str)
                                            process_complete_time = datetime.fromisoformat(process_complete_time_str)
                                            processing_duration = process_complete_time - process_start_time
                                            # 格式化处理时间
                                            hours = processing_duration.seconds // 3600
                                            minutes = (processing_duration.seconds % 3600) // 60
                                            seconds = processing_duration.seconds % 60
                                            if hours > 0:
                                                duration_str = f"{hours}小时{minutes}分{seconds}秒"
                                            else:
                                                duration_str = f"{minutes}分{seconds}秒"
                                            email_body_lines.append(f"  {i+1}. [{task_type_zh}] {name}")
                                            email_body_lines.append(f"     处理时间: {duration_str}")
                                        except (ValueError, TypeError) as e:
                                            logging.warning(f"处理任务 {task.get('uuid', '未知')} 的时间戳时出错: {e}")
                                            email_body_lines.append(f"  {i+1}. [{task_type_zh}] {name}")
                                    else:
                                        email_body_lines.append(f"  {i+1}. [{task_type_zh}] {name}")
                                else:
                                    logging.warning(f"无法找到或处理任务信息，使用原始名称: {name}")
                                    email_body_lines.append(f"  {i+1}. {name}")
                        else:
                            email_body_lines.append("成功任务: 无")

                        email_body_lines.append("-" * 20)

                        if summary['failure']:
                            email_body_lines.append("失败任务:")
                            for i, name in enumerate(summary['failure']):
                                # 找到对应的任务，添加更严格的空值检查
                                task = next((t for t in completed_tasks if isinstance(t, dict) and 
                                           isinstance(t.get('metadata'), dict) and 
                                           t.get('metadata', {}).get('name') == name), None)
                                if task and isinstance(task, dict):
                                    task_name, task_type_zh, _ = get_task_display_info(task)
                                    # 添加时间戳检查
                                    process_start_time_str = task.get('process_start_time')
                                    process_complete_time_str = task.get('process_complete_time')
                                    if process_start_time_str and process_complete_time_str:
                                        try:
                                            process_start_time = datetime.fromisoformat(process_start_time_str)
                                            process_complete_time = datetime.fromisoformat(process_complete_time_str)
                                            processing_duration = process_complete_time - process_start_time
                                            # 格式化处理时间
                                            hours = processing_duration.seconds // 3600
                                            minutes = (processing_duration.seconds % 3600) // 60
                                            seconds = processing_duration.seconds % 60
                                            if hours > 0:
                                                duration_str = f"{hours}小时{minutes}分{seconds}秒"
                                            else:
                                                duration_str = f"{minutes}分{seconds}秒"
                                            email_body_lines.append(f"  {i+1}. [{task_type_zh}] {name}")
                                            email_body_lines.append(f"     处理时间: {duration_str}")
                                        except (ValueError, TypeError) as e:
                                            logging.warning(f"处理任务 {task.get('uuid', '未知')} 的时间戳时出错: {e}")
                                            email_body_lines.append(f"  {i+1}. [{task_type_zh}] {name}")
                                    else:
                                        email_body_lines.append(f"  {i+1}. [{task_type_zh}] {name}")
                                else:
                                    logging.warning(f"无法找到或处理任务信息，使用原始名称: {name}")
                                    email_body_lines.append(f"  {i+1}. {name}")

                        email_body_lines.append("-" * 20)
                        email_body = "\n".join(email_body_lines)

                        # 调用邮件发送函数
                        send_summary_email(recipient_email, email_subject, email_body, user_config, config_data.get('email_checker', {}))

                    logging.info("邮件汇总发送尝试完成。")

                    # 4. 归档错误任务
                    error_tasks_to_archive = [task for task in completed_tasks if task.get("status") == "error"]
                    if error_tasks_to_archive:
                        logging.info(f"准备归档 {len(error_tasks_to_archive)} 个 'error' 状态的任务到 {errors_path}。")
                        # 使用 utils 函数读写
                        existing_errors = read_json_with_lock(errors_path, errors_lock_obj, default=[])
                        if not isinstance(existing_errors, list):
                             logging.error(f"错误归档文件 {errors_path} 不是列表，无法归档。")
                        else:
                            existing_error_uuids = {err.get("uuid") for err in existing_errors if isinstance(err, dict) and "uuid" in err}
                            new_errors_to_add = [task for task in error_tasks_to_archive if task.get("uuid") not in existing_error_uuids]
                            if new_errors_to_add:
                                existing_errors.extend(new_errors_to_add)
                                # 使用 utils 函数写入
                                if write_json_with_lock(errors_path, errors_lock_obj, existing_errors):
                                     logging.info(f"{len(new_errors_to_add)} 个新的错误任务已归档。")
                                else:
                                     logging.error(f"归档错误任务到 {errors_path} 失败。")
                            else:
                                logging.info("没有新的错误任务需要归档。")

                    # 5. 清理任务队列：移除所有 'finish' 和 'error' 状态的任务
                    # 在清理前等待2秒
                    logging.info("等待2秒后清理任务队列...")
                    time.sleep(2)
                    
                    tasks_to_keep = [task for task in current_tasks if task.get("status") not in ["finish", "error"]]
                    tasks_removed_count = len(current_tasks) - len(tasks_to_keep)

                    if tasks_removed_count > 0:
                        logging.info(f"队列空闲，从 task_queue.json 中移除 {tasks_removed_count} 个已完成或错误的任务。")
                        # 使用 utils 函数写入
                        if write_json_with_lock(task_queue_path, task_queue_lock_obj, tasks_to_keep):
                             logging.info("task_queue.json 清理完毕。")
                        else:
                             logging.error(f"清理 task_queue.json 时发生错误。")
                    else:
                        logging.debug("task_queue.json 无需清理。")

                    # 邮件和清理完成后，进入等待
                    if udp_socket:
                        logging.debug(f"队列空闲处理完毕，等待 UDP 信号或 {current_poll_interval} 秒超时...")
                        ready_to_read, _, _ = select.select([udp_socket], [], [], current_poll_interval)
                    else: # 如果 socket 创建失败，则使用 time.sleep
                        logging.debug(f"队列空闲处理完毕 (无 UDP 监听)，休眠 {current_poll_interval} 秒。")
                        time.sleep(current_poll_interval)

                else:
                    # 队列空闲，但没有找到 finish/error 任务
                    logging.debug("队列空闲，且无已完成任务待处理。")
                    # 进入等待
                    if udp_socket:
                        logging.debug(f"队列空闲，等待 UDP 信号或 {current_poll_interval} 秒超时...")
                        ready_to_read, _, _ = select.select([udp_socket], [], [], current_poll_interval)
                    else:
                        logging.debug(f"队列空闲 (无 UDP 监听)，休眠 {current_poll_interval} 秒。")
                        time.sleep(current_poll_interval)

                # 如果收到信号，清空缓冲区并立即开始下一次循环检查
                if ready_to_read:
                    try:
                        logging.debug("收到 UDP 信号，立即检查队列。")
                        # 读取数据以清空缓冲区，但不关心内容
                        udp_socket.recvfrom(1024)
                        
                        # 切换到快速轮询模式
                        with poll_interval_lock:
                            if not fast_poll_mode:
                                logging.info("收到后端启动信号，切换到快速轮询模式。")
                                current_poll_interval = fast_poll_interval
                                fast_poll_mode = True
                    except socket.error as sock_err:
                        # 在非阻塞模式下，如果没有数据可读，可能会引发错误 (尽管 select 应该避免这种情况)
                        logging.warning(f"读取 UDP 信号时出错 (可能无数据): {sock_err}")
                    except Exception as e:
                        logging.error(f"处理 UDP 信号时发生未知错误: {e}", exc_info=True)
                else:
                    logging.debug("等待超时，按计划检查队列。")

                continue # 不论是收到信号还是超时，都开始下一次循环

            # --- 如果队列不空闲，则尝试启动新任务 (逻辑保持不变) ---

            # 查找 ready 任务 (不变)
            next_ready_task = None
            for task in current_tasks:
                if task.get("status") == "ready":
                    task_uuid_check = task.get("uuid")
                    with running_set_lock:
                        if task_uuid_check in running_task_uuids:
                            logging.warning(f"任务 {task_uuid_check}: 文件状态 'ready' 但内存记录运行中。尝试修正文件状态为 'running'。")
                            update_task_status_in_file(task_uuid_check, "running")
                            continue
                        else:
                            next_ready_task = task
                            break

            # 检查是否可启动 (不变)
            with running_set_lock: current_running_count = len(running_task_uuids)

            if next_ready_task and current_running_count < max_parallel:
                task_to_run = next_ready_task.copy()
                task_uuid = task_to_run.get("uuid")
                task_user = task_to_run.get("user")

                if not task_user:
                     logging.error(f"任务 {task_uuid} 状态为 'ready' 但缺少 'user' 字段，无法启动。标记为错误。")
                     update_task_status_in_file(task_uuid, "error", "任务缺少 user 字段", "")
                     continue # 处理下一个任务

                logging.info(f"找到可执行任务 {task_uuid} (用户: {task_user})。当前运行数: {current_running_count}/{max_parallel}。")

                # --- 新增：获取用户通知配置 ---
                user_config_from_yaml = users_data.get(task_user, {})
                if not user_config_from_yaml:
                     logging.warning(f"任务 {task_uuid}: 在 users.yaml 中未找到用户 '{task_user}' 的配置，将无法发送通知。")

                # 准备传递给线程的配置
                user_notification_config_for_thread = {
                    'emby_url': user_config_from_yaml.get('emby_url'),
                    'emby_api_key': user_config_from_yaml.get('emby_api_key'),
                    'bark_urls': user_config_from_yaml.get('bark_urls', [])  # 获取 Bark URLs 列表
                    # 注意：邮箱地址不需要传递给 execute_task
                }
                # --- 获取配置结束 ---

                # 更新状态为 running (使用 utils 函数)
                process_start_time_iso = datetime.now(timezone.utc).isoformat()
                update_task_status_in_file(task_uuid, "running", process_start_time_iso=process_start_time_iso)

                # 加入运行集合 (不变)
                with running_set_lock: running_task_uuids.add(task_uuid)

                # 启动线程 (修改：传递 user_notification_config_for_thread)
                logging.info(f"任务 {task_uuid}: 文件状态更新为 'running'。启动执行线程。")
                thread = threading.Thread(
                    target=execute_task,
                    args=(task_to_run, user_notification_config_for_thread), # 传递配置
                    name=f"任务-{task_uuid[:8]}"
                )
                thread.daemon = True
                thread.start()

                # 成功启动任务后，不需要等待，立即检查下一个
                continue
            else:
                # 没有任务可启动 或 达到并行上限
                wait_needed = True
                if next_ready_task:
                     logging.debug(f"无法启动新任务，已达最大并行数 ({current_running_count}/{max_parallel})。")
                else:
                     logging.debug("当前无 'ready' 状态的任务可启动。")

                # 进入等待
                if wait_needed:
                    if udp_socket:
                        logging.debug(f"无任务启动或已达上限，等待 UDP 信号或 {current_poll_interval} 秒超时...")
                        ready_to_read, _, _ = select.select([udp_socket], [], [], current_poll_interval)
                    else:
                        logging.debug(f"无任务启动或已达上限 (无 UDP 监听)，休眠 {current_poll_interval} 秒。")
                        time.sleep(current_poll_interval)

                    # 处理等待结果
                    if ready_to_read:
                        try:
                            logging.debug("收到 UDP 信号，立即检查队列。")
                            udp_socket.recvfrom(1024) # 清空缓冲区
                            
                            # 切换到快速轮询模式
                            with poll_interval_lock:
                                if not fast_poll_mode:
                                    logging.info("收到后端启动信号，切换到快速轮询模式。")
                                    current_poll_interval = fast_poll_interval
                                    fast_poll_mode = True
                        except socket.error as sock_err:
                             logging.warning(f"读取 UDP 信号时出错 (可能无数据): {sock_err}")
                        except Exception as e:
                            logging.error(f"处理 UDP 信号时发生未知错误: {e}", exc_info=True)
                    else:
                         logging.debug("等待超时，按计划检查队列。")

        except Exception as e:
            logging.error(f"主调度循环发生意外错误: {e}", exc_info=True)
            # 发生错误时，也等待一段时间再重试
            time.sleep(current_poll_interval) # 使用长轮询间隔作为错误后的等待时间
        finally:
             # 在循环的最后，确保即使有异常，也能继续下一次迭代或退出
             pass

    # --- 程序退出前关闭套接字 ---
    if udp_socket:
        try:
            udp_socket.close()
            logging.info("UDP 监听套接字已关闭。")
        except Exception as e:
            logging.error(f"关闭 UDP 套接字时出错: {e}")


# --- 主程序入口 (修改) ---
if __name__ == "__main__":
    try:
        # 步骤 1, 2, 3: 加载配置，设置路径/锁/日志 (已包含 users.yaml 加载)
        load_config_and_paths()

        # 清除任务队列文件
        task_queue_path_init = file_paths.get('task_queue') # Renamed
        task_queue_lock_obj_init = file_locks.get('task_queue') # Renamed
        if task_queue_path_init and task_queue_lock_obj_init:
            try:
                with task_queue_lock_obj_init:
                    if os.path.exists(task_queue_path_init):
                        logging.info(f"正在清除任务队列文件: {task_queue_path_init}")
                        write_json_with_lock(task_queue_path_init, task_queue_lock_obj_init, []) # Corrected arg order
                        logging.info("任务队列文件已清除")
                    else:
                        logging.info(f"任务队列文件不存在，无需清除: {task_queue_path_init}")
            except Exception as e_clear:
                logging.error(f"清除任务队列文件时发生错误: {e_clear}", exc_info=True)
                sys.exit(1)
        else:
            logging.critical("任务队列文件路径或锁未配置")
            sys.exit(1)

        # 步骤 4: 确保初始数据文件存在
        json_files_to_initialize_main = ['task_queue', 'errors'] # Renamed
        for key_json_init in json_files_to_initialize_main: # Renamed
             file_path_json_init = file_paths.get(key_json_init); lock_obj_json_init = file_locks.get(key_json_init) # Renamed
             if not file_path_json_init or not lock_obj_json_init: logging.critical(f"关键文件 '{key_json_init}' 路径或锁不可用。"); sys.exit(1)
             dir_path_json_init = os.path.dirname(file_path_json_init); os.makedirs(dir_path_json_init, exist_ok=True) # Renamed
             needs_initialization_main = False # Renamed
             if not os.path.exists(file_path_json_init): needs_initialization_main = True
             else:
                try:
                    with lock_obj_json_init:
                        if os.path.getsize(file_path_json_init) == 0: needs_initialization_main = True
                        else:
                             try:
                                  with open(file_path_json_init, 'r', encoding='utf-8') as f_check_main: content_main = f_check_main.read().strip() # Renamed
                                  if not content_main or content_main == '[]': needs_initialization_main = True
                                  else: json.loads(content_main) 
                             except (json.JSONDecodeError, UnicodeDecodeError): logging.warning(f"文件 {file_path_json_init} 无效，将重新初始化。"); needs_initialization_main = True
                             except Exception: needs_initialization_main = True 
                except filelock.Timeout: logging.critical(f"检查文件 {file_path_json_init} 时锁超时。"); sys.exit(1)
                except Exception: needs_initialization_main = True 
             if needs_initialization_main:
                  logging.warning(f"文件 '{key_json_init}' ({file_path_json_init}) 将初始化为空 JSON 数组 '[]'。")
                  try: write_json_with_lock(file_path_json_init, lock_obj_json_init, []) # Corrected arg order
                  except Exception as e_init_json: logging.critical(f"初始化文件失败: {file_path_json_init}: {e_init_json}"); sys.exit(1)

        # 确保其他目录存在
        for key_dir_create, path_dir_create in file_paths.items(): # Renamed
             is_likely_file_dir_create = '.' in os.path.basename(path_dir_create) or key_dir_create in ['go_main', 'go_metadata', 'logs', 'go_main_bin'] # Renamed
             target_dir_create = os.path.dirname(path_dir_create) if is_likely_file_dir_create else path_dir_create # Renamed
             if target_dir_create: os.makedirs(target_dir_create, exist_ok=True)

        # --- 检查 Go 二进制文件 ---
        go_main_bin_path_verify = file_paths.get('go_main_bin')
        if not go_main_bin_path_verify or not os.path.exists(go_main_bin_path_verify):
             logging.critical(f"Go 二进制文件未找到: {go_main_bin_path_verify}。请确保 backend.py 已完成编译。")
             sys.exit(1)
        if not os.access(go_main_bin_path_verify, os.X_OK):
             logging.critical(f"Go 二进制文件无执行权限: {go_main_bin_path_verify}。")
             sys.exit(1)
        logging.info(f"确认 Go 二进制文件存在且可执行: {go_main_bin_path_verify}")

        logging.info(f"程序初始化完成。项目根目录: {PROJECT_ROOT}")
        
        # 启动SSE服务器
        start_sse_server()

        # 步骤 5: 启动主调度循环
        main_scheduler_loop()

    except KeyboardInterrupt: logging.info("调度器收到中断信号，正在退出...")
    except SystemExit as e: logging.info(f"程序通过 sys.exit({e.code}) 退出。")
    except Exception as e: logging.critical(f"调度器遇到致命错误并退出: {e}", exc_info=True); sys.exit(1)
    finally:
        logging.info("调度器正在停止...")
        # 确保套接字在任何退出情况下都被尝试关闭 (虽然循环内部的finally也会关闭)
        # 注意：这里的 udp_socket 变量在 finally 块中可能不可见，
        # 需要更好的方式来处理，例如将其设为全局变量或通过类管理。
        # 但对于此脚本结构，循环内的关闭是主要的。
        logging.info("调度器已停止。")