import os
import sys
import time
import logging
import shutil
from datetime import datetime, timedelta
from filelock import FileLock

# 导入共享工具
# 假设 utils.py 与 logs_manager.py 在同一目录下 (python/)
SCRIPT_DIR_LM = os.path.dirname(os.path.abspath(__file__)) # _LM for LogManager
PROJECT_ROOT_LM = os.path.dirname(SCRIPT_DIR_LM)

# 将项目根目录添加到 sys.path，以便导入 utils
if PROJECT_ROOT_LM not in sys.path:
    sys.path.insert(0, PROJECT_ROOT_LM)

from python.utils import (
    read_yaml_with_lock,
    setup_logging,
    PROJECT_ROOT # 使用 utils 中定义的 PROJECT_ROOT
)

# --- 全局配置变量 ---
CONFIG_LM = {}
LOG_FILE_PATH_LM = None
MAX_LOG_SIZE_MB_LM = 100  # 默认最大日志文件大小 (MB)
LOG_RETENTION_DAYS_LM = 7   # 默认日志保留天数
CHECK_INTERVAL_SECONDS_LM = 3600  # 默认检查间隔 (1小时)
LOGS_DIR_LM = None # 日志文件所在目录
ARCHIVE_DIR_LM = None # 日志归档目录

def load_log_manager_config():
    """加载日志管理器所需的配置。"""
    global CONFIG_LM, LOG_FILE_PATH_LM, MAX_LOG_SIZE_MB_LM, LOG_RETENTION_DAYS_LM, CHECK_INTERVAL_SECONDS_LM, LOGS_DIR_LM, ARCHIVE_DIR_LM

    config_path = os.path.join(PROJECT_ROOT, "config", "config.yaml")
    config_lock_path = f"{config_path}.lock"
    config_lock = FileLock(config_lock_path)

    config_data_local = read_yaml_with_lock(config_path, config_lock)

    if not isinstance(config_data_local, dict) or not config_data_local:
        # 如果 utils.setup_logging 还未被调用，这里的 logging 可能不起作用
        # 因此使用 print 输出到 stderr
        print(f"CRITICAL: [LogManager] 无法加载有效的配置文件: {config_path}。将使用默认设置运行。", file=sys.stderr)
        CONFIG_LM = {} # 确保 CONFIG_LM 是字典
    else:
        CONFIG_LM = config_data_local

    # 设置日志 (使用 utils 函数，并传递中文名)
    # 注意：logs_manager 自身的日志也会记录到主日志文件，直到它被分割
    setup_logging(CONFIG_LM, script_chinese_name="日志管理器")

    # 从配置中获取日志管理器特定设置
    log_manager_conf = CONFIG_LM.get('logs_manager', {})
    MAX_LOG_SIZE_MB_LM = log_manager_conf.get('max_log_size_mb', MAX_LOG_SIZE_MB_LM)
    LOG_RETENTION_DAYS_LM = log_manager_conf.get('log_retention_days', LOG_RETENTION_DAYS_LM)
    CHECK_INTERVAL_SECONDS_LM = log_manager_conf.get('check_interval_seconds', CHECK_INTERVAL_SECONDS_LM)
    
    # 确定主日志文件路径 (与 utils.setup_logging 逻辑类似，但这里是为了管理它)
    log_file_from_config = CONFIG_LM.get('log_file_path') # key in config for backend
    if not log_file_from_config:
        paths_conf = CONFIG_LM.get('paths', {})
        log_file_from_config = paths_conf.get('logs') # key in config.paths for main/email_checker
        if log_file_from_config:
            if not os.path.isabs(log_file_from_config):
                LOG_FILE_PATH_LM = os.path.join(PROJECT_ROOT, log_file_from_config)
            else:
                LOG_FILE_PATH_LM = log_file_from_config
        else: # 默认路径
            LOG_FILE_PATH_LM = os.path.join(PROJECT_ROOT, "logs", "logs.log")
    else:
        if not os.path.isabs(log_file_from_config):
            LOG_FILE_PATH_LM = os.path.join(PROJECT_ROOT, log_file_from_config)
        else:
            LOG_FILE_PATH_LM = log_file_from_config
    
    LOG_FILE_PATH_LM = os.path.normpath(LOG_FILE_PATH_LM)
    LOGS_DIR_LM = os.path.dirname(LOG_FILE_PATH_LM)
    # 归档目录默认为日志目录下的 'archive' 子文件夹
    ARCHIVE_DIR_LM = log_manager_conf.get('archive_directory')
    if ARCHIVE_DIR_LM:
        if not os.path.isabs(ARCHIVE_DIR_LM):
            ARCHIVE_DIR_LM = os.path.join(PROJECT_ROOT, ARCHIVE_DIR_LM)
    else:
        ARCHIVE_DIR_LM = os.path.join(LOGS_DIR_LM, "archive")
    ARCHIVE_DIR_LM = os.path.normpath(ARCHIVE_DIR_LM)

    # 确保归档目录存在
    try:
        os.makedirs(ARCHIVE_DIR_LM, exist_ok=True)
    except OSError as e:
        logging.error(f"创建日志归档目录 '{ARCHIVE_DIR_LM}' 失败: {e}。将尝试在主日志目录下归档。")
        ARCHIVE_DIR_LM = LOGS_DIR_LM # 备选方案：如果创建失败，则归档到主日志目录
        os.makedirs(ARCHIVE_DIR_LM, exist_ok=True) # 再次尝试创建，如果主日志目录也不存在

    logging.info("日志管理器配置加载完毕:")
    logging.info(f"  - 主日志文件: {LOG_FILE_PATH_LM}")
    logging.info(f"  - 日志归档目录: {ARCHIVE_DIR_LM}")
    logging.info(f"  - 最大文件大小: {MAX_LOG_SIZE_MB_LM} MB")
    logging.info(f"  - 日志保留天数: {LOG_RETENTION_DAYS_LM} 天")
    logging.info(f"  - 检查间隔: {CHECK_INTERVAL_SECONDS_LM} 秒")

def rotate_log():
    """检查主日志文件大小，如果超过阈值则进行分割归档。"""
    if not LOG_FILE_PATH_LM or not os.path.exists(LOG_FILE_PATH_LM):
        logging.warning(f"主日志文件 '{LOG_FILE_PATH_LM}' 不存在，跳过分割检查。")
        return

    try:
        log_size_bytes = os.path.getsize(LOG_FILE_PATH_LM)
        max_size_bytes = MAX_LOG_SIZE_MB_LM * 1024 * 1024
        logging.debug(f"当前日志文件大小: {log_size_bytes / (1024*1024):.2f} MB，阈值: {MAX_LOG_SIZE_MB_LM} MB")

        if log_size_bytes >= max_size_bytes:
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            archive_filename = f"{os.path.basename(LOG_FILE_PATH_LM)}.{timestamp}"
            archive_filepath = os.path.join(ARCHIVE_DIR_LM, archive_filename)
            
            logging.info(f"日志文件 '{LOG_FILE_PATH_LM}' 大小 ({log_size_bytes / (1024*1024):.2f} MB) 已达到阈值。正在归档为 '{archive_filepath}'")
            
            # 重命名前先关闭当前的日志处理器，以释放文件句柄
            # 获取根 logger
            logger = logging.getLogger()
            handlers_to_reopen = []
            for handler in logger.handlers[:]:
                if isinstance(handler, logging.FileHandler) and handler.baseFilename == LOG_FILE_PATH_LM:
                    handlers_to_reopen.append(handler) # 记录下文件处理器信息
                    handler.close()
                    logger.removeHandler(handler)
                # 对于控制台处理器，不需要关闭和重新打开，但如果setup_logging每次都清空并重建，则这里不需要特别处理

            # 移动文件
            shutil.move(LOG_FILE_PATH_LM, archive_filepath)
            logging.info(f"日志文件已成功归档到: {archive_filepath}")

            # 重新初始化日志，让其创建新的 logs.log 文件
            # 这会创建新的 FileHandler 指向新的 (空的) LOG_FILE_PATH_LM
            # 并重新添加控制台处理器等
            setup_logging(CONFIG_LM, script_chinese_name="日志管理器")
            logging.info("日志系统已在新的主日志文件上重新初始化。")
            
            # 可选：如果之前有其他非文件、非控制台的处理器，这里需要逻辑来重新添加它们
            # 但对于当前简单的 FileHandler + StreamHandler 结构，setup_logging 重建即可

    except FileNotFoundError:
        logging.warning(f"尝试分割日志时，主日志文件 '{LOG_FILE_PATH_LM}' 未找到。可能是并发操作导致。")
    except Exception as e:
        logging.error(f"分割日志文件 '{LOG_FILE_PATH_LM}' 时发生错误: {e}", exc_info=True)

def cleanup_old_logs():
    """清理归档目录中超过保留期限的日志文件。"""
    if not ARCHIVE_DIR_LM or not os.path.isdir(ARCHIVE_DIR_LM):
        logging.warning(f"日志归档目录 '{ARCHIVE_DIR_LM}' 无效或不存在，跳过清理。")
        return

    logging.info(f"开始清理归档目录 '{ARCHIVE_DIR_LM}' 中超过 {LOG_RETENTION_DAYS_LM} 天的日志...")
    now = datetime.now()
    cutoff_date = now - timedelta(days=LOG_RETENTION_DAYS_LM)
    files_deleted_count = 0
    files_kept_count = 0

    try:
        for filename in os.listdir(ARCHIVE_DIR_LM):
            filepath = os.path.join(ARCHIVE_DIR_LM, filename)
            if os.path.isfile(filepath):
                try:
                    # 尝试从文件名解析日期，假设格式为 logs.log.YYYY-MM-DD_HH-MM-SS
                    # 或者其他可识别的日期格式
                    # 更稳健的方式是检查文件的修改时间
                    file_mod_time_stamp = os.path.getmtime(filepath)
                    file_mod_date = datetime.fromtimestamp(file_mod_time_stamp)

                    if file_mod_date < cutoff_date:
                        os.remove(filepath)
                        logging.info(f"已删除旧日志文件: {filepath} (修改日期: {file_mod_date.strftime('%Y-%m-%d')})")
                        files_deleted_count += 1
                    else:
                        files_kept_count += 1
                except ValueError:
                    logging.warning(f"无法从文件名解析日期或获取修改时间: {filename}，跳过此文件。")
                except OSError as e:
                    logging.error(f"删除文件 '{filepath}' 时出错: {e}")
                except Exception as e:
                    logging.error(f"处理归档文件 '{filepath}' 时发生未知错误: {e}", exc_info=True)
        
        logging.info(f"旧日志清理完成。删除了 {files_deleted_count} 个文件，保留了 {files_kept_count} 个文件。")

    except Exception as e:
        logging.error(f"列出归档目录 '{ARCHIVE_DIR_LM}' 内容时出错: {e}", exc_info=True)

def manage_logs():
    """执行一次日志管理操作：分割和清理。"""
    logging.info("开始执行日志管理周期...")
    rotate_log()
    cleanup_old_logs()
    logging.info("日志管理周期结束。")

if __name__ == "__main__":
    load_log_manager_config() # 加载配置并初始化日志
    logging.info("日志管理器脚本启动。")

    # 首次运行时立即执行一次管理
    manage_logs()

    logging.info(f"将每隔 {CHECK_INTERVAL_SECONDS_LM} 秒执行一次日志管理。")
    while True:
        try:
            time.sleep(CHECK_INTERVAL_SECONDS_LM)
            manage_logs()
        except KeyboardInterrupt:
            logging.info("日志管理器收到中断信号，正在退出...")
            break
        except Exception as e:
            logging.error(f"日志管理器主循环发生意外错误: {e}", exc_info=True)
            # 发生错误后，可以等待短一点时间再重试，或者按原定间隔
            logging.info(f"发生错误，将在 {CHECK_INTERVAL_SECONDS_LM // 2} 秒后尝试下一次管理周期。")
            time.sleep(CHECK_INTERVAL_SECONDS_LM // 2) # 例如，错误后等待一半的常规间隔
