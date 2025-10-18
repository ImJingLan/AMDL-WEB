import os
import base64
import json
import requests
import logging
import sys
import threading
import urllib.parse
import time
from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename
from filelock import FileLock

# 导入 utils.py 中的工具函数
from utils import read_yaml_with_lock, setup_logging, PROJECT_ROOT

# 配置文件路径 - 修正为正确的路径
CONFIG_FILE_PATH = "/amdl/config/config.yaml"
CONFIG_LOCK = FileLock(CONFIG_FILE_PATH + ".lock")

# 读取配置文件
config = read_yaml_with_lock(CONFIG_FILE_PATH, CONFIG_LOCK)

# 创建专用的logger，避免与其他脚本的日志冲突
logger = logging.getLogger('gemini_service')
logger.setLevel(logging.INFO)

# 确保不重复添加处理器
if not logger.handlers:
    # 配置日志格式
    log_format = "%(asctime)s - Gemini接口服务 - %(levelname)s - %(message)s"
    date_format = "%m.%d %H:%M:%S"
    formatter = logging.Formatter(log_format, datefmt=date_format)
    
    # 添加文件处理器
    file_handler = logging.FileHandler('/amdl/logs.log', mode='a', encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    # 禁用传播到根logger，避免重复日志
    logger.propagate = False

# 创建Flask应用
app = Flask(__name__)

# 配置文件上传限制
app.config['MAX_CONTENT_LENGTH'] = 15 * 1024 * 1024  # 15MB 限制

# 彻底禁用Flask相关的所有日志输出
app.logger.disabled = True
app.logger.propagate = False
logging.getLogger('werkzeug').disabled = True
logging.getLogger('werkzeug').propagate = False
# 禁用Flask内部的其他日志记录器
for logger_name in ['flask.app', 'werkzeug._internal']:
    flask_logger = logging.getLogger(logger_name)
    flask_logger.disabled = True
    flask_logger.propagate = False

# 从配置文件中获取 Gemini 相关配置
gemini_config = config.get("gemini", {})
API_KEY = gemini_config.get("api_key")
MODEL = gemini_config.get("model", "gemini-2.5-flash")  # 恢复原模型版本（支持搜索）
API_BASE_URL = gemini_config.get("api_base_url", "https://lyjw131-gemini-play.deno.dev")

# 构建完整的API URL
API_URL = f"{API_BASE_URL}/chat/completions"

logger.info(f"Gemini API 配置已加载: 模型={MODEL}, 基础URL={API_BASE_URL}")

# 更新后的用户提示词 - 强制严格JSON输出
USER_PROMPT = """You are a JSON-only response system. You MUST respond with ONLY valid JSON and nothing else.

TASK: Identify album information from the provided image using Google Search verification.

MANDATORY OUTPUT FORMAT: Return ONLY this exact JSON structure with NO additional text:
[
  {
    "album_title": "Album Name Here",
    "artist_name": "Artist Name Here"
  }
]

CRITICAL CONSTRAINTS:
1. Use Google Search to verify each album exists on Apple Music
2. Prioritize Japanese titles for Japanese albums
3. Return empty array [] if nothing can be verified
4. ABSOLUTELY NO text outside the JSON array
5. ABSOLUTELY NO comments inside the JSON
6. ABSOLUTELY NO verification notes, confirmations, or explanations
7. ABSOLUTELY NO "I have verified", "Tapius:", "Search confirms", or similar text
8. Your entire response must be parseable as JSON

FORBIDDEN OUTPUTS:
- Any text before or after the JSON array
- Any comments like "// verified" 
- Any notes like "Tapius: I have verified..."
- Any explanations or confirmations
- Any text that is not part of the JSON structure

EXAMPLE CORRECT OUTPUT:
[{"album_title":"Example Album","artist_name":"Example Artist"}]

EXAMPLE FORBIDDEN OUTPUT:
[{"album_title":"Example Album","artist_name":"Example Artist"} I verified this exists]

Remember: Your response will be directly parsed as JSON. Any non-JSON text will cause a parsing error.
"""

# 允许上传的文件类型
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}

def allowed_file(filename):
    """检查文件扩展名是否允许"""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def warm_search_cache(album_data):
    """为识别的专辑结果预热搜索缓存"""
    if not album_data or not isinstance(album_data, list):
        return
    
    def send_cache_request(album_info):
        """在后台线程中发送缓存请求"""
        try:
            album_title = album_info.get('album_title', '').strip()
            artist_name = album_info.get('artist_name', '').strip()
            
            if not album_title or not artist_name:
                return
            
            # 构造搜索词：专辑名 空格 歌手名
            search_term = f"{album_title} {artist_name}"
            
            # URL编码搜索词
            encoded_term = urllib.parse.quote(search_term)
            
            # 构造请求URL
            cache_url = f"http://localhost:5000/search?term={encoded_term}&types=albums&limit=1"
            
            # 发送请求（设置较短的超时时间，避免影响主响应）
            response = requests.get(cache_url, timeout=5)
            
            if response.status_code == 200:
                logger.debug(f"缓存预热成功: {search_term}")
            else:
                logger.debug(f"缓存预热失败: {search_term}, 状态码: {response.status_code}")
                
        except Exception as e:
            logger.debug(f"缓存预热请求异常: {e}")
    
    # 为每个识别结果启动后台线程进行缓存预热
    for album_info in album_data:
        if isinstance(album_info, dict):
            thread = threading.Thread(
                target=send_cache_request, 
                args=(album_info,), 
                daemon=True,
                name=f"CacheWarming-{album_info.get('album_title', 'Unknown')[:10]}"
            )
            thread.start()
    
    logger.info(f"已启动 {len(album_data)} 个缓存预热任务")

def clean_json_text(text):
    """清理和修复JSON文本"""
    import re
    import json
    
    # 移除JSON外的任何文本（仅保留JSON数组部分）
    json_match = re.search(r'\[.*\]', text, flags=re.DOTALL)
    if json_match:
        text = json_match.group(0)
    
    # 处理特殊字符和引号
    text = text.replace('"', '\\"')  # 转义所有引号
    text = text.replace('\\"', '"')  # 恢复正常的JSON引号
    text = text.replace('""', '"')   # 修复可能的双引号
    
    # 移除可能导致JSON解析错误的字符
    text = re.sub(r'[\x00-\x1F\x7F-\x9F]', '', text)
    
    # 确保JSON数组格式正确
    text = text.strip()
    
    # 尝试解析和重新格式化JSON以确保有效性
    try:
        parsed = json.loads(text)
        return json.dumps(parsed, ensure_ascii=False)
    except json.JSONDecodeError:
        # 如果解析失败，返回原始文本
        return text

@app.route('/gemini', methods=['POST'])
def gemini_album_info():
    """
    接收图片文件，调用 API 获取专辑信息 (使用 OpenAI 格式)，并返回 JSON 结果。
    """
    if not API_KEY:
        logger.error("API 密钥未配置，请检查 config.yaml 中的 gemini.api_key 配置")
        return jsonify({"error": "API 密钥未设置。请检查配置文件。"}), 500

    if 'file' not in request.files:
        logger.warning("请求中未找到文件部分")
        return jsonify({"error": "请求中未找到文件部分"}), 400
    
    file = request.files['file']

    if file.filename == '':
        logger.warning("未选择文件")
        return jsonify({"error": "未选择文件"}), 400

    if file and allowed_file(file.filename):
        try:
            logger.info(f"开始处理文件: {file.filename}")
            image_bytes = file.read()
            base64_image = base64.b64encode(image_bytes).decode('utf-8')
            
            mime_type = "image/jpeg" # 默认MIME类型
            if '.' in file.filename:
                ext = file.filename.rsplit('.', 1)[1].lower()
                if ext == 'png':
                    mime_type = "image/png"
                elif ext == 'webp':
                    mime_type = "image/webp"
            
            # 构建发送给 API 的请求体 (OpenAI 格式，添加搜索工具)
            payload = {
                "model": MODEL,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": USER_PROMPT
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{mime_type};base64,{base64_image}"
                                }
                            }
                        ]
                    }
                ],
                "tools": [
                    {
                        "type": "google_search"
                    }
                ],
                "response_format": {
                    "type": "json_object"
                }
            }

            headers = {
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {API_KEY}'
            }

            logger.info(f"正在调用 Gemini API，模型: {MODEL}")
            # 调用 API（设置连接超时30秒，读取超时60秒）
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    response = requests.post(API_URL, headers=headers, json=payload, timeout=(30, 60))
                    response.raise_for_status()
                    break
                except requests.exceptions.ConnectTimeout:
                    logger.warning(f"连接超时，尝试次数 {attempt + 1}/{max_retries}")
                    if attempt == max_retries - 1:
                        raise
                    time.sleep(2 ** attempt)  # 指数退避
                except requests.exceptions.Timeout:
                    logger.warning(f"请求超时，尝试次数 {attempt + 1}/{max_retries}")
                    if attempt == max_retries - 1:
                        raise
                    time.sleep(2 ** attempt)  # 指数退避 

            api_response_json = response.json()

            # 提取 API 返回的内容 (OpenAI 格式)
            if (api_response_json.get("choices") and
                api_response_json["choices"][0].get("message") and
                api_response_json["choices"][0]["message"].get("content")):
                
                generated_text = api_response_json["choices"][0]["message"]["content"]
                
                # 打印模型的完整输出
                logger.info(f"Gemini 模型完整输出: {generated_text}")
                
                # 清理JSON文本，移除可能的格式问题
                cleaned_text = clean_json_text(generated_text)
                
                try:
                    # 解析清理后的JSON文本
                    album_data = json.loads(cleaned_text)
                    logger.info(f"成功识别专辑信息: {file.filename}")
                    
                    # 为识别结果建立搜索缓存
                    warm_search_cache(album_data)
                    
                    return jsonify(album_data), 200
                except json.JSONDecodeError as e:
                    logger.error(f"清理后的JSON仍无法解析: {cleaned_text}, 错误: {e}")
                    # 如果模型无法识别，它应该返回空数组
                    if cleaned_text.strip() == "[]":
                         logger.info(f"模型无法识别专辑: {file.filename}")
                         return jsonify([]), 200
                    return jsonify({"error": "API 返回的文本无法解析为 JSON", "details": cleaned_text}), 500
            else:
                logger.error(f"API 响应结构不符合预期: {api_response_json}")
                return jsonify({"error": "未能从 API 响应中提取有效内容", "details": api_response_json}), 500

        except requests.exceptions.RequestException as e:
            logger.error(f"调用 API 时发生网络错误: {e}")
            return jsonify({"error": f"调用 API 失败: {e}"}), 500
        except Exception as e:
            logger.error(f"处理请求时发生未知错误: {e}", exc_info=True)
            return jsonify({"error": f"服务器内部错误: {e}"}), 500
    else:
        logger.warning(f"不允许的文件类型: {file.filename}")
        return jsonify({"error": "不允许的文件类型。请上传 'png', 'jpg', 'jpeg', 'webp' 格式的图片。"}), 400

@app.errorhandler(413)
def file_too_large(error):
    """处理文件过大错误"""
    logger.warning("上传文件过大，超过15MB限制")
    return jsonify({"error": "文件过大。请上传小于15MB的图片。"}), 413

if __name__ == '__main__':
    logger.info("Gemini接口服务启动，端口: 5002")
    app.run(host='0.0.0.0', port=5002, debug=False)
