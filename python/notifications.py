# -*- coding: utf-8 -*-

import requests
import smtplib
import urllib.parse
import logging
import socket
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr
import json
import os
import filelock
import time

# --- 导入共享工具 ---
from utils import (
    get_task_display_info, 
    TASK_TYPE_MAP,
    PROJECT_ROOT,
    read_yaml_with_lock
)

# 任务类型映射
# TASK_TYPE_MAP = { ... } # 移至 utils.py

# def get_task_display_info(task_data): ... # 移至 utils.py

def query_emby_album_id(emby_url, emby_api_key, album_name, max_retries=120, retry_interval=0.5):
    """查询 Emby 获取专辑 ID。
    
    Args:
        emby_url: Emby 服务器基础 URL
        emby_api_key: Emby API Key
        album_name: 专辑名称
        max_retries: 最大重试次数
        retry_interval: 重试间隔（秒）
    
    Returns:
        str: 专辑 ID，如果未找到则返回 None
    """
    if not album_name:
        logging.warning("专辑名为空，无法查询 Emby")
        return None
        
    logging.info(f"开始查询 Emby 专辑 ID，专辑名: {album_name}")
    
    # 构建查询 URL
    base_url = emby_url.rstrip('/')
    search_url = f"{base_url}/emby/Users/a4a7aebebf884933aece0f5c1c2581c5/Items"
    
    # 构建查询参数
    params = {
        'SearchTerm': album_name,  # 直接使用原始专辑名，让 requests 处理编码
        'IncludeItemTypes': 'Audio',
        'Recursive': 'true',
        'Fields': 'Id,Name,AlbumId'
    }
    
    headers = {
        'X-Emby-Token': emby_api_key,
        'Accept': 'application/json'
    }
    
    for attempt in range(max_retries):
        try:
            logging.info(f"尝试查询 Emby (第 {attempt + 1}/{max_retries} 次)")
            
            # 构建完整的查询 URL（包含所有参数）
            full_url = f"{search_url}?{urllib.parse.urlencode(params)}"
            logging.info(f"完整查询 URL: {full_url}")
            
            response = requests.get(search_url, params=params, headers=headers, timeout=5)
            response.raise_for_status()
            
            data = response.json()
            items = data.get('Items', [])
            
            # 记录找到的所有项目
            if items:
                logging.info(f"Emby 返回了 {len(items)} 个项目:")
                for item in items:
                    logging.info(f"  - {item.get('Name')} (ID: {item.get('Id')}, AlbumId: {item.get('AlbumId')})")
            else:
                logging.info("Emby 未返回任何项目")
            
            # 查找匹配的专辑
            for item in items:
                if item.get('Album') == album_name:
                    album_id = item.get('AlbumId')
                    if album_id:
                        logging.info(f"找到匹配的专辑 ID: {album_id}")
                        return album_id
            
            # 如果没找到，等待后重试
            if attempt < max_retries - 1:
                logging.info(f"未找到匹配的专辑，等待 {retry_interval} 秒后重试...")
                time.sleep(retry_interval)
                
        except requests.exceptions.RequestException as e:
            logging.warning(f"查询 Emby 专辑 ID 时发生错误 (尝试 {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(retry_interval)
    
    logging.warning(f"在 {max_retries} 次尝试后仍未找到专辑 ID: {album_name}")
    return None

def send_emby_refresh(user, task_uuid):
    """向指定的 Emby URL 发送 POST 请求以触发刷新。"""
    # 从 users.yaml 读取用户配置
    users_path = os.path.join(PROJECT_ROOT, "config", "users.yaml")
    users_lock = filelock.FileLock(f"{users_path}.lock")
    users_data = read_yaml_with_lock(users_path, users_lock)
    
    # 获取用户配置
    user_config = users_data.get(user, {})
    emby_url = user_config.get('emby_url')
    emby_api_key = user_config.get('emby_api_key')
    
    if not emby_url or not emby_api_key:
        logging.warning(f"用户 {user} 未配置 Emby URL 或 API Key，跳过任务 {task_uuid} 的通知。")
        return

    # 构建完整的刷新 URL
    refresh_url = f"{emby_url.rstrip('/')}/emby/Library/Refresh"
    logging.info(f"任务 {task_uuid}: 正在为用户 {user} 触发 Emby 刷新: {refresh_url}")
    try:
        headers = {
            'X-Emby-Token': emby_api_key,
            'X-Emby-Authorization': f'MediaBrowser Client="AMDL", Device="AMDL", DeviceId="AMDL", Version="1.0.0"',
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        response = requests.post(refresh_url, headers=headers, timeout=15) # 设置超时
        response.raise_for_status() # 检查 HTTP 错误状态 (4xx, 5xx)
        
        # 尝试解析响应内容
        try:
            response_json = response.json()
            logging.info(f"任务 {task_uuid}: Emby 刷新请求成功发送给 {user}。响应: {response_json}")
        except json.JSONDecodeError:
            logging.info(f"任务 {task_uuid}: Emby 刷新请求成功发送给 {user}。响应状态码: {response.status_code}")
            
    except requests.exceptions.Timeout:
        logging.error(f"任务 {task_uuid}: 触发用户 {user} 的 Emby 刷新时发生超时: {refresh_url}")
    except requests.exceptions.RequestException as e:
        logging.error(f"任务 {task_uuid}: 触发用户 {user} 的 Emby 刷新时发生错误: {e} (URL: {refresh_url})")
        if hasattr(e.response, 'text'):
            try:
                error_json = e.response.json()
                logging.error(f"任务 {task_uuid}: Emby 错误响应: {error_json}")
            except:
                logging.error(f"任务 {task_uuid}: Emby 错误响应文本: {e.response.text[:200]}")
    except Exception as e:
        logging.error(f"任务 {task_uuid}: 触发用户 {user} 的 Emby 刷新时发生意外错误: {e}", exc_info=True)

def send_bark_notification(bark_server_url, click_url_template, user, task_data, task_success, emby_album_id=None):
    """发送 Bark 通知。"""
    if not bark_server_url:
        return

    uuid = task_data.get("uuid", "未知")
    task_name, task_type_zh, task_type = get_task_display_info(task_data)

    # 构建通知内容
    status_text = "下载成功" if task_success else "下载失败"
    info_string = f"{task_type_zh}「{task_name}」{status_text}" # 单任务格式

    try:
        # 读取配置文件获取 bark_notification 配置
        config_path = os.path.join(PROJECT_ROOT, "config", "config.yaml")
        config_lock = filelock.FileLock(f"{config_path}.lock")
        config_data = read_yaml_with_lock(config_path, config_lock)
        bark_config = config_data.get('bark_notification', {})
        
        # 获取通知路径模板，默认为 "/Apple-Music-Downloader/{info}"
        path_template = bark_config.get('path', '/Apple-Music-Downloader/{info}')
        
        # 获取 Emby 配置 (如果需要的话，但 click_url_template 已直接传入)
        users_path = os.path.join(PROJECT_ROOT, "config", "users.yaml")
        users_lock = filelock.FileLock(f"{users_path}.lock")
        users_data = read_yaml_with_lock(users_path, users_lock)
        user_config = users_data.get(user, {})
        
        # 构建点击跳转的 URL
        actual_click_url = ""
        if click_url_template: # <--- 使用传入的模板
            if task_type == 'album' and task_success:
                # --- 使用传入的 emby_album_id (如果存在) ---
                if emby_album_id:
                    actual_click_url = click_url_template.replace("{id}", emby_album_id)
                else: # 否则，按原逻辑查询
                    emby_url = user_config.get('emby_url')
                    emby_api_key = user_config.get('emby_api_key')
                    
                    if emby_url and emby_api_key:
                        album_id_from_emby = query_emby_album_id(emby_url, emby_api_key, task_name)
                        if album_id_from_emby:
                            actual_click_url = click_url_template.replace("{id}", album_id_from_emby)
            # 如果不是成功下载的专辑，或者没有有效的 click_url_template，则 actual_click_url 会为空
            # 或者可以设置一个默认的跳转链接，例如主页
            if not actual_click_url: # 如果前面没有成功构建 specific click url
                 # 尝试从 task_data 的 metadata 中获取链接
                 metadata_url = task_data.get("metadata", {}).get("url") or task_data.get("link_info", {}).get("original_url")
                 if metadata_url:
                      actual_click_url = metadata_url
                 else: # 默认回退到 amdl 网站
                      actual_click_url = "https://amdl.lyjw131.com"
        else: # 如果用户根本没有配置 click_url_template
            # 尝试从 task_data 的 metadata 中获取链接
            metadata_url = task_data.get("metadata", {}).get("url") or task_data.get("link_info", {}).get("original_url")
            if metadata_url:
                actual_click_url = metadata_url
            else: # 默认回退到 amdl 网站
                actual_click_url = "https://amdl.lyjw131.com"

        # 确保 bark_server_url 末尾有 /
        base_url = bark_server_url.rstrip('/')
        
        # 使用配置的路径模板，将 {info} 替换为通知内容
        notification_path = path_template.replace('{info}', urllib.parse.quote(info_string))
        
        # 构建完整的 Bark URL（包含通知标题）
        final_url_with_info = f"{base_url}{notification_path}"
        
        # 添加 icon 和 url 参数 (如果 actual_click_url 有效)
        query_params = []
        # 使用配置中的图标，如果没有则使用默认图标
        icon_url = bark_config.get('icon', 'https://music.apple.com/assets/favicon/favicon-180.png')
        query_params.append(f"icon={urllib.parse.quote(icon_url)}")
        if actual_click_url:
            query_params.append(f"url={urllib.parse.quote(actual_click_url)}")
        
        if query_params:
            final_url_with_params = f"{final_url_with_info}?{'&'.join(query_params)}"
        else:
            final_url_with_params = final_url_with_info

        # 打印完整的 Bark 链接
        logging.info(f"任务 {uuid}: 完整的 Bark 通知链接: {final_url_with_params}")

        logging.info(f"任务 {uuid}: 正在为用户 {user} 发送 Bark 通知 ({status_text})")
        response = requests.post(final_url_with_params, timeout=10) # <--- 使用带参数的 URL
        response.raise_for_status()
        logging.info(f"任务 {uuid}: Bark 通知成功发送给 {user}。")
        
    except requests.exceptions.Timeout:
        logging.error(f"任务 {uuid}: 发送 Bark 通知给用户 {user} 时发生超时: {final_url_with_params if 'final_url_with_params' in locals() else bark_server_url}") # <--- 更新日志中的 URL
    except requests.exceptions.RequestException as e:
        logging.error(f"任务 {uuid}: 发送 Bark 通知给用户 {user} 时发生错误: {e} (URL: {final_url_with_params if 'final_url_with_params' in locals() else bark_server_url})") # <--- 更新日志中的 URL
    except Exception as e:
        logging.error(f"任务 {uuid}: 发送 Bark 通知给用户 {user} 时发生意外错误: {e}", exc_info=True)

def send_summary_email(user_email, subject, body, user_config, smtp_config):
    """使用配置的 SMTP 服务器发送邮件。
    
    Args:
        user_email: 收件人邮箱
        subject: 邮件主题
        body: 邮件正文
        user_config: 用户配置
        smtp_config: SMTP 服务器配置
    """
    # 检查用户是否启用了邮件通知
    if not user_config.get('enable_email_notification', False):
        logging.info(f"用户 {user_config.get('other_name', ['unknown'])[0]} 已禁用邮件通知，跳过发送。")
        return

    smtp_server = smtp_config.get('smtp_server')
    smtp_port = smtp_config.get('smtp_port')
    sender_email = smtp_config.get('username')
    sender_password = smtp_config.get('password')

    if not all([smtp_server, smtp_port, sender_email, sender_password, user_email]):
        logging.error(f"无法发送邮件给 {user_email}: SMTP 配置不完整或收件人邮箱缺失。")
        return

    message = MIMEText(body, 'plain', 'utf-8')
    message['From'] = formataddr(("AMDL下载通知", sender_email)) # 可以加个昵称 Header("发件人昵称 <" + sender_email + ">", 'utf-8')
    message['To'] = Header(user_email, 'utf-8')
    message['Subject'] = Header(subject, 'utf-8')

    logging.info(f"准备发送邮件到: {user_email}, 主题: {subject}")
    server = None # 初始化 server 变量
    try:
        # 根据端口判断是 SSL 还是 STARTTLS (常见情况)
        if smtp_port == 465:
            server = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=20)
        elif smtp_port == 587:
            server = smtplib.SMTP(smtp_server, smtp_port, timeout=20)
            server.starttls()
        else: # 其他端口尝试普通 SMTP
            server = smtplib.SMTP(smtp_server, smtp_port, timeout=20)
            # 可根据需要尝试 server.starttls()

        # server.set_debuglevel(1) # 开启调试信息
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, [user_email], message.as_string())
        logging.info(f"邮件已成功发送至 {user_email}。")
    except smtplib.SMTPAuthenticationError as e:
        logging.error(f"发送邮件到 {user_email} 时 SMTP 认证失败: {e} (请检查用户名和密码/授权码)")
    except (smtplib.SMTPException, socket.gaierror, socket.error, TimeoutError) as e:
        logging.error(f"发送邮件到 {user_email} 时发生 SMTP 或网络错误: {e}")
    except Exception as e:
        logging.error(f"发送邮件到 {user_email} 时发生意外错误: {e}", exc_info=True)
    finally:
        if server:
            try:
                server.quit()
            except Exception:
                pass # 退出时可能出错，忽略
