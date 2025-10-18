# coding: utf-8

import imaplib
import email
from email.header import decode_header
import yaml
import json
import time
import re
import logging
import os
import sys
import requests
from filelock import FileLock
from utils import (
    read_yaml_with_lock,
    resolve_paths,
    setup_logging,
    PROJECT_ROOT,
    DEFAULT_PATHS_RELATIVE_TO_ROOT
)

# --- 配置加载 ---
config_path = os.path.join(PROJECT_ROOT, "config", "config.yaml")
config_lock = FileLock(f"{config_path}.lock")

# 使用 utils 的 YAML 读取
config = read_yaml_with_lock(config_path, config_lock)
if not config:
    # 在 setup_logging 前记录关键错误到 stderr
    print("CRITICAL: 邮件检查器无法加载配置文件 config.yaml", flush=True)
    sys.exit(1)

# --- 日志设置 (使用 utils.setup_logging) ---
setup_logging(config, script_chinese_name="邮件检查器") # 传递中文名

# 使用 utils 的路径解析 (需要在 setup_logging 之后，因为 resolve_paths 可能使用 logging)
# 注意: resolve_paths 本身也会调用 logging.debug，所以它应该在 setup_logging 之后
file_paths = resolve_paths(PROJECT_ROOT, config.get('paths', {}), DEFAULT_PATHS_RELATIVE_TO_ROOT)

# --- 获取特定配置 ---
email_conf = config.get('email_checker', {})
paths_conf = config.get('paths', {})

IMAP_SERVER = email_conf.get('imap_server', 'imap.qq.com')
USERNAME = email_conf.get('username')
PASSWORD = email_conf.get('password')
CHECK_INTERVAL = email_conf.get('check_interval_seconds', 30)
TARGET_SUBJECT = email_conf.get('target_subject', 'amdl')
# LINK_PREFIX = email_conf.get('link_must_contain', 'https://music.apple.com') # 不再用于查找
BACKEND_URL = email_conf.get('backend_url') # 获取后端服务 URL

# 确保关键配置项存在
if not USERNAME or not PASSWORD:
    logging.error("邮箱用户名或密码未在 config.yaml 的 'email_checker' 部分配置")
    sys.exit(1)
if not BACKEND_URL:
    logging.error("后端服务 URL (backend_url) 未在 config.yaml 的 'email_checker' 部分配置")
    sys.exit(1)

# --- 根据 PROJECT_ROOT 和 paths_conf 确定文件绝对路径 ---
# 定义 email_checker 需要用到的文件对应的 config keys 和默认相对路径
# 这些默认值是为了在 config.yaml 中没有对应键时使用
EMAIL_CHECKER_PATH_KEYS = {
    'log_file': ('logs', 'logs.log'),       # 内部变量名: (config key, 默认相对路径)
    'users_file': ('users', 'config/users.yaml'), # 内部变量名: (config key, 默认相对路径)
}

# 使用解析后的路径
LOG_FILE = file_paths.get('logs')
USERS_FILE = file_paths.get('users')

# 确保必要的文件路径已确定
if not LOG_FILE or not USERS_FILE:
    logging.error("日志或用户文件路径配置或计算失败。请检查脚本和配置文件。")
    sys.exit(1)

# --- 确保必要目录存在 ---
# 确保日志文件目录存在
log_dir = os.path.dirname(LOG_FILE)
if log_dir:
    os.makedirs(log_dir, exist_ok=True)
# 确保用户文件目录存在 (如果 users_file 在子目录)
users_dir = os.path.dirname(USERS_FILE)
if users_dir:
     os.makedirs(users_dir, exist_ok=True)

# --- 用户数据加载 ---
users_lock = FileLock(f"{USERS_FILE}.lock")
users_data = read_yaml_with_lock(USERS_FILE, users_lock) or {}

email_to_user_map = {}
if users_data:
    for std_name, user_info in users_data.items():
        if not isinstance(std_name, str) or not std_name.strip() or not isinstance(user_info, dict):
            logging.warning(f"用户文件中存在无效的用户条目: {std_name}: {user_info}")
            continue

        user_emails = user_info.get('email', [])
        if not isinstance(user_emails, list):
            logging.warning(f"用户 '{std_name}' 的 'email' 字段不是列表。")
            user_emails = []

        for mail_addr in user_emails:
            if isinstance(mail_addr, str) and mail_addr.strip():
                email_to_user_map[mail_addr.strip().lower()] = std_name.strip()
            else:
                logging.warning(f"用户 '{std_name}' 的邮箱列表中存在无效邮箱地址: {mail_addr}")

logging.info(f"已为 {len(users_data)} 个用户加载 {len(email_to_user_map)} 个邮箱地址映射。")

# --- 辅助函数 ---

# read_json_list_safe 函数不再需要

def decode_str(s):
    """解码邮件头字符串"""
    if not isinstance(s, str):
        return str(s) # 不是字符串，尝试转为字符串
    try:
        # decode_header 返回列表，如 [('Subject', 'utf-8')] 或 [('=?UTF-8?B?...?=', None)]
        # 我们遍历所有部分进行拼接
        decoded_parts = decode_header(s)
        decoded_string = ""
        for value, charset in decoded_parts:
            if charset:
                try:
                    # value 是 bytes
                    decoded_string += value.decode(charset)
                except (UnicodeDecodeError, LookupError):
                    # 如果指定编码失败，尝试 UTF-8，如果再失败则用 replace
                    decoded_string += value.decode('utf-8', errors='replace')
            else:
                # 如果没有 charset，或者 value 是 bytes
                # 如果 value 是 bytes，尝试 utf-8 解码；如果是 string，直接添加
                decoded_string += value if isinstance(value, str) else (value.decode('utf-8', errors='replace') if isinstance(value, bytes) else str(value))
        return decoded_string.strip() # 返回解码并去除空白符的字符串

    except Exception as e:
        logging.debug(f"解码字符串 '{s}' 时出错: {e}")
        return str(s).strip() # 解码失败返回原字符串的字符串表示


def get_body_from_msg(msg):
    """从 email.message.Message 中提取文本正文"""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition"))
            # 跳过附件
            if "attachment" in content_disposition:
                continue

            try:
                # 优先纯文本
                if content_type == "text/plain":
                    charset = part.get_content_charset() or 'utf-8'
                    payload = part.get_payload(decode=True)
                    body = payload.decode(charset, errors='replace')
                    # 如果找到了纯文本，就优先使用它并停止查找
                    break
                # 备用 HTML
                elif content_type == "text/html":
                    charset = part.get_content_charset() or 'utf-8'
                    payload = part.get_payload(decode=True)
                    html_body = payload.decode(charset, errors='replace')
                    # 如果还没有找到正文，使用 HTML 作为备用
                    if not body:
                         body = html_body
            except Exception as e:
                logging.warning(f"无法解码邮件部分 (类型: {content_type}): {e}")
    else:
        # 非 multipart 邮件
        content_type = msg.get_content_type()
        try:
             if content_type == "text/plain":
                  charset = msg.get_content_charset() or 'utf-8'
                  payload = msg.get_payload(decode=True)
                  body = payload.decode(charset, errors='replace')
             elif content_type == "text/html":
                  charset = msg.get_content_charset() or 'utf-8'
                  payload = msg.get_payload(decode=True)
                  body = payload.decode(charset, errors='replace')
        except Exception as e:
             logging.warning(f"无法解码非 multipart 邮件正文 (类型: {content_type}): {e}")

    return body.strip() # 返回去除首尾空白符的正文


# --- 精确匹配 Apple Music 链接的原始正则表达式字符串 (来自用户提供) ---
# 注意：这些表达式定义时带有行首^和行尾$|\?的锚点
album_regexp_str = r'^(?:https?:\/\/(?:beta\.music|music)\.apple\.com\/(\w{2})(?:\/album|\/album\/.+))\/(?:id)?(\d[^\D]+)(?:$|\?)'
mv_regexp_str = r'^(?:https?:\/\/(?:beta\.music|music)\.apple\.com\/(\w{2})(?:\/music-video|\/music-video\/.+))\/(?:id)?(\d[^\D]+)(?:$|\?)'
song_regexp_str = r'^(?:https?:\/\/(?:beta\.music|music)\.apple\.com\/(\w{2})(?:\/song|\/song\/.+))\/(?:id)?(\d[^\D]+)(?:$|\?)'
playlist_regexp_str = r'^(?:https?:\/\/(?:beta\.music|music)\.apple\.com\/(\w{2})(?:\/playlist|\/playlist\/.+))\/(?:id)?(pl\.[\w-]+)(?:$|\?)'

# 将原始正则表达式字符串和对应的内部名称存储在一个列表中，方便处理
APPLE_MUSIC_REGEXES_STR = [
    ("album", album_regexp_str),
    ("mv", mv_regexp_str),
    ("song", song_regexp_str),
    ("playlist", playlist_regexp_str),
]

# 编译用于在文本中查找的正则表达式列表
# 从原始表达式字符串中移除首尾锚点，并用一个外部捕获组包裹，以便 findall 返回完整匹配的字符串
REGEXES_FOR_FINDING = []
trailing_anchor_str = '(?:$|\\?)' # 定义需要移除的特定结尾锚点字符串
for name, pattern_string in APPLE_MUSIC_REGEXES_STR:
    modified_pattern = pattern_string
    # 移除开头的 ^
    if modified_pattern.startswith('^'):
        modified_pattern = modified_pattern[1:]

    # 移除结尾的 (?:$|\?)
    if modified_pattern.endswith(trailing_anchor_str):
        modified_pattern = modified_pattern[:-len(trailing_anchor_str)]
    else:
         logging.warning(f"Regex '{name}' does not end with expected anchor '{trailing_anchor_str}'. Using pattern as is for finding.")


    # 用一个外部捕获组包裹修改后的模式，并编译
    find_pattern = r'(' + modified_pattern + r')'
    try:
        REGEXES_FOR_FINDING.append(re.compile(find_pattern, re.IGNORECASE))
    except re.error as e:
         logging.error(f"Failed to compile find regex from '{pattern_string}'. Pattern: '{find_pattern}'. Error: {e}")
         # 如果正则表达式编译失败，跳过这个模式


def find_links(text):
    """
    在文本中查找所有符合 Apple Music 链接精确正则表达式的URL。
    使用编译好的不带锚点的正则表达式进行查找。
    参数:
        text (str): 邮件正文文本。
    返回:
        list: 找到的符合精确正则表达式的链接字符串列表 (去重)。
    """
    if not isinstance(text, str) or not text.strip():
        return [] # 文本为空或不是字符串

    found_links = set() # 使用 set 存储，自动去重

    # 遍历编译好的用于查找的正则表达式列表
    for regex_find in REGEXES_FOR_FINDING:
        matches = regex_find.findall(text)
        for match_tuple in matches:
            if match_tuple and len(match_tuple) > 0:
                 # match_tuple 的第一个元素就是外部捕获组匹配到的整个链接字符串
                 full_link = match_tuple[0]

                 # 清理链接末尾的常见标点符号或括号 (即使正则已经比较精确，额外清理更保险)
                 cleaned_link = full_link
                 trailing_chars = '.,)]}!?\'"' # 需要移除的末尾字符
                 while cleaned_link and cleaned_link[-1] in trailing_chars:
                      cleaned_link = cleaned_link[:-1]
                 if cleaned_link: # 确保清理后链接不为空
                    found_links.add(cleaned_link) # 添加到 set 中，自动去重

    return list(found_links) # 返回去重后的链接列表


# append_tasks_to_queue 函数不再需要


# --- 主循环 ---
logging.info("邮件检查器启动中...")
while True:
    # --- 检查新邮件周期开始日志 ---
    logging.info("检查新邮件周期开始...")

    mail = None # 初始化 mail 变量为 None
    try:
        # --- 连接和认证 ---
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(USERNAME, PASSWORD)
        mail.select("inbox")

        # 搜索未读邮件且主题包含目标字符串
        search_criteria = f'(UNSEEN SUBJECT "{TARGET_SUBJECT}")'
        # result, data = mail.search(None, search_criteria) # 改用 UID 搜索更稳定
        result, data = mail.uid('search', None, search_criteria)


        tasks_to_post = [] # 初始化本周期要发送给后端的任务列表
        email_uids_to_process = [] # 存储需要处理的邮件 UID 列表

        if result == 'OK' and data and data[0]:
            email_uids_to_process = data[0].split() # 获取邮件 UID 列表 (字节串)
            if email_uids_to_process:
                logging.info(f"找到 {len(email_uids_to_process)} 封匹配的未读邮件。")
        else:
             logging.info("未找到匹配的未读邮件。")

        # --- 处理匹配邮件 ---
        for uid in email_uids_to_process:
            uid_str = uid.decode() # 获取 UID 的字符串形式方便日志记录
            try:
                result, msg_data = mail.uid('fetch', uid, '(RFC822)')
                if result == 'OK':
                    raw_email = msg_data[0][1]
                    email_message = email.message_from_bytes(raw_email)

                    # 获取发件人地址
                    sender_header = email_message.get('From', '') # 使用 .get() 避免 KeyError
                    # 使用 decode_str 解码发件人头，确保正确处理各种编码
                    decoded_sender_header = decode_str(sender_header)
                    # 从解码后的字符串中再次解析出地址
                    sender_name, sender_addr = email.utils.parseaddr(decoded_sender_header)
                    sender_addr = sender_addr.strip().lower() # 去除空白并转小写

                    logging.info(f"处理邮件 UID {uid_str} 来自 {sender_addr}")

                    standard_user = email_to_user_map.get(sender_addr)

                    if standard_user:
                        logging.info(f"发件人 {sender_addr} 已识别为授权用户: {standard_user}")
                        body = get_body_from_msg(email_message)
                        if not body:
                            logging.warning(f"无法从邮件 UID {uid_str} 提取文本正文。标记为已读。")
                            # 标记为已读，不删除
                            mail.uid('store', uid, '+FLAGS', '\\Seen')
                            continue # 继续处理下一个 UID

                        # 使用精确匹配的 find_links 函数查找链接
                        found_links = find_links(body)

                        if found_links:
                            logging.info(f"在来自用户 {standard_user} 的邮件 (UID {uid_str}) 中找到 {len(found_links)} 个符合 Apple Music 精确格式的链接。")
                            # 将找到的链接添加到本周期的任务列表中
                            for link_url_item in found_links: # 重命名以避免与外部 link 模块冲突
                                tasks_to_post.append({"user": standard_user, "link": link_url_item})
                                logging.debug(f"为用户 '{standard_user}' 添加链接 '{link_url_item}' 到待 POST 列表。")
                            # 邮件处理完毕，如果找到了符合精确格式的链接（意味着任务将被尝试提交给后端），标记为删除
                            logging.info(f"邮件 UID {uid_str} 处理完成，找到符合格式的链接，标记为删除。")
                            mail.uid('store', uid, '+FLAGS', '\\Deleted')

                        else:
                            # 找到了授权用户，但邮件中没有符合精确格式的链接
                            logging.warning(f"来自授权用户 {standard_user} 的邮件 (UID {uid_str}) 未包含符合 Apple Music 精确格式的链接。标记为已读。")
                            # 标记为已读，不删除
                            mail.uid('store', uid, '+FLAGS', '\\Seen')

                    else:
                        # 发件人不是授权用户
                        logging.warning(f"发件人 {sender_addr} (UID {uid_str}) 不是授权用户。标记为已读。")
                        # 标记为已读，不删除
                        mail.uid('store', uid, '+FLAGS', '\\Seen')

                else:
                    # 获取单个邮件失败
                    logging.error(f"获取邮件 UID {uid_str} 失败。保留为未读。")
                    # 获取失败，不标记已读或删除，下次可能能获取到

            except Exception as e:
                # 处理单个邮件过程中的其他意外错误
                logging.error(f"处理邮件 UID {uid_str} 时发生意外错误: {e}", exc_info=True)
                # 发生错误，标记为已读，避免重复处理导致循环出错
                try:
                    mail.uid('store', uid, '+FLAGS', '\\Seen')
                except Exception as inner_e:
                     logging.warning(f"标记邮件 UID {uid_str} 为已读时再次出错: {inner_e}")
                # 继续处理下一个 UID


        # --- 完成所有匹配邮件的本地处理后，清理邮箱 ---
        # 只有在成功连接并选择收件箱后，并且处理完邮件列表后才执行清理
        if mail: # 确保 mail 对象已成功创建和连接
            try:
                mail.expunge() # 删除所有标记为 \Deleted 的邮件
                logging.info(f"邮件清理完成。")
            except imaplib.IMAP4.error as expunge_err:
                logging.warning(f"执行 expunge 清理邮件时出错: {expunge_err}")
            except Exception as expunge_e:
                 logging.warning(f"执行 expunge 清理邮件时发生意外错误: {expunge_e}", exc_info=True)


        # --- 完成所有邮件的本地处理后，提交任务到后端 ---
        if tasks_to_post:
            logging.info(f"本周期共收集到 {len(tasks_to_post)} 个任务，准备发送到后端 {BACKEND_URL}")
            try:
                # 发送 POST 请求
                # requests 库发送 json=... 时会自动设置 Content-Type: application/json
                response = requests.post(BACKEND_URL, json=tasks_to_post, timeout=60) # 设置一个超时时间
                response.raise_for_status() # 对于 4xx 或 5xx 状态码抛出异常

                # 请求成功，记录后端的响应
                logging.info(f"任务成功提交到后端。状态码: {response.status_code}. 响应: {response.json()}")

            except requests.exceptions.Timeout:
                logging.error(f"向后端提交任务超时 ({60}s)。任务可能未能成功添加。")
            except requests.exceptions.RequestException as e:
                # 处理其他请求相关的错误 (连接错误, HTTP错误等)
                logging.error(f"向后端提交任务失败: {e}. 任务可能未能成功添加。")
                # 尝试记录后端返回的错误信息（如果能获取到）
                try:
                    if e.response is not None:
                        logging.error(f"后端响应内容 (如果存在): {e.response.text}")
                    else:
                         logging.error("无后端响应内容。")
                except Exception:
                    pass # 忽略记录响应内容时的错误
            except Exception as e:
                # 捕获 requests 库之外的意外错误
                logging.error(f"向后端提交任务时发生意外错误: {e}", exc_info=True)

        else: # 没有任务需要 POST
             logging.info("本周期没有收集到需要提交的任务。")

        # 确保关闭和登出
        if mail: # 确保 mail 对象已成功创建和连接
            try:
                mail.close()
            except imaplib.IMAP4.error as close_err:
                 logging.warning(f"关闭邮箱连接时出错: {close_err}")
            except Exception as close_e:
                 logging.warning(f"关闭邮箱连接时发生意外错误: {close_e}", exc_info=True)

            try:
                 mail.logout()
            except imaplib.IMAP4.error as logout_err:
                  logging.warning(f"登出邮箱时出错: {logout_err}")
            except Exception as logout_e:
                 logging.warning(f"登出邮箱时发生意外错误: {logout_e}", exc_info=True)


    except imaplib.IMAP4.error as e:
        # 处理连接、登录、选择文件夹等IMAP初始错误
        logging.error(f"IMAP 连接或认证错误: {e}")
        # 等待更长时间再重试
        time.sleep(CHECK_INTERVAL * 2)
    except Exception as e:
        # 捕获主循环中的其他意外错误
        logging.error(f"发生意外错误: {e}", exc_info=True)
        # 等待正常时间再重试
        time.sleep(CHECK_INTERVAL)

    # 等待下一次检查
    logging.info(f"等待 {CHECK_INTERVAL} 秒进行下一次检查周期...")
    time.sleep(CHECK_INTERVAL)