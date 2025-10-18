import os
import base64
import json
import requests
import logging
import sys
import argparse
import urllib.parse  # 新增导入
import imagehash  # 新增导入：用于图片相似度检查
from PIL import Image
import io
from filelock import FileLock

# 导入 utils.py 中的工具函数
from utils import read_yaml_with_lock, setup_logging, PROJECT_ROOT

# 配置文件路径 - 使用相对路径
CONFIG_FILE_PATH = "config/config.yaml"
CONFIG_LOCK = FileLock(CONFIG_FILE_PATH + ".lock")

# 读取配置文件
config = read_yaml_with_lock(CONFIG_FILE_PATH, CONFIG_LOCK)

# 创建专用的logger
logger = logging.getLogger('image_segmentation')
logger.setLevel(logging.INFO)

# 确保不重复添加处理器
if not logger.handlers:
    # 配置日志格式
    log_format = "%(asctime)s - 图片切分服务 - %(levelname)s - %(message)s"
    date_format = "%m.%d %H:%M:%S"
    formatter = logging.Formatter(log_format, datefmt=date_format)
    
    # 添加文件处理器 - 使用相对路径
    file_handler = logging.FileHandler('logs.log', mode='a', encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    # 添加控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # 禁用传播到根logger，避免重复日志
    logger.propagate = False

# 从配置文件中获取 Gemini 相关配置
gemini_config = config.get("gemini", {})
API_KEY = gemini_config.get("api_key")
# 硬编码测试key
if not API_KEY:
    API_KEY = "AIzaSyCviNqtNn9elDXFi-y67xlGmVhbHjCt7q8"
MODEL = gemini_config.get("model", "gemini-2.5-flash-preview-04-17")
API_BASE_URL = gemini_config.get("api_base_url", "https://lyjw131-gemini-play.deno.dev")

# 构建完整的API URL
API_URL = f"{API_BASE_URL}/chat/completions"

# Backend服务器配置
BACKEND_BASE_URL = "http://localhost:50000"  # 新增：Backend服务器地址

logger.info(f"Gemini API 配置已加载: 模型={MODEL}, 基础URL={API_BASE_URL}")
logger.info(f"Backend 服务器配置: {BACKEND_BASE_URL}")

# 专辑切分的提示词 - 使用新的规范模板
SEGMENTATION_PROMPT = """Analyze the provided image and identify the bounding box coordinates for each album unit. An album unit encompasses the album cover, title, and artist information. The image dimensions are provided as a JSON object: {{"length": "{height}", "width": "{width}"}}.

Output the coordinates for each album unit as a JSON array of objects, where each object represents an album unit and contains:
1. Album title (exact text from the image)
2. Artist name (exact text from the image)  
3. Coordinates of its four corners: top-left, top-right, bottom-left, and bottom-right
4. The coordinates should be relative to the image dimensions

JSON format:
[
  {{
    "album_title": "Exact Album Name",
    "artist_name": "Exact Artist Name",
    "top_left": [x, y],
    "top_right": [x, y], 
    "bottom_left": [x, y],
    "bottom_right": [x, y]
  }}
]

Important: Ensure coordinates capture the complete album unit including cover art and text labels."""

# 允许的文件类型
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}

def allowed_file(filename):
    """检查文件扩展名是否允许"""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_image_dimensions(image_path):
    """获取图片尺寸"""
    try:
        with Image.open(image_path) as img:
            width, height = img.size
            return {"width": str(width), "length": str(height)}
    except Exception as e:
        logger.error(f"获取图片尺寸失败: {e}")
        return None

def clean_json_text(text):
    """清理和修复JSON文本"""
    import re
    
    # 移除JSON外的任何文本（仅保留JSON数组部分）
    json_match = re.search(r'\[.*\]', text, flags=re.DOTALL)
    if json_match:
        text = json_match.group(0)
    
    # 移除可能导致JSON解析错误的字符
    text = re.sub(r'[\x00-\x1F\x7F-\x9F]', '', text)
    
    # 确保JSON数组格式正确
    text = text.strip()
    
    # 尝试解析和重新格式化JSON以确保有效性
    try:
        parsed = json.loads(text)
        return parsed
    except json.JSONDecodeError:
        logger.error(f"JSON解析失败: {text}")
        return None

def call_gemini_api(image_path, dimensions):
    """调用Gemini API获取专辑边界框坐标"""
    if not API_KEY:
        logger.error("API 密钥未配置，请检查 config.yaml 中的 gemini.api_key 配置")
        return None
    
    try:
        # 读取图片并转换为base64
        with open(image_path, 'rb') as image_file:
            image_bytes = image_file.read()
            base64_image = base64.b64encode(image_bytes).decode('utf-8')
        
        # 确定MIME类型
        mime_type = "image/jpeg"  # 默认
        if '.' in image_path:
            ext = image_path.rsplit('.', 1)[1].lower()
            if ext == 'png':
                mime_type = "image/png"
            elif ext == 'webp':
                mime_type = "image/webp"
        
        # 构建发送给 API 的请求体
        prompt_text = SEGMENTATION_PROMPT.format(width=dimensions['width'], height=dimensions['length'])
        
        payload = {
            "model": MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": prompt_text
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

        logger.info(f"正在调用 Gemini API 进行图片分析...")
        response = requests.post(API_URL, headers=headers, json=payload)
        response.raise_for_status()

        api_response_json = response.json()

        # 提取 API 返回的内容
        if (api_response_json.get("choices") and
            api_response_json["choices"][0].get("message") and
            api_response_json["choices"][0]["message"].get("content")):
            
            generated_text = api_response_json["choices"][0]["message"]["content"]
            logger.info(f"Gemini API 响应: {generated_text}")
            
            # 清理并解析JSON
            album_coordinates = clean_json_text(generated_text)
            return album_coordinates
        else:
            logger.error(f"API 响应结构不符合预期: {api_response_json}")
            return None

    except requests.exceptions.RequestException as e:
        logger.error(f"调用 API 时发生网络错误: {e}")
        return None
    except Exception as e:
        logger.error(f"调用 Gemini API 时发生错误: {e}")
        return None

def calculate_bounding_box_from_corners(album_info):
    """从四个角点坐标计算边界框"""
    try:
        # 获取四个角点坐标
        top_left = album_info.get('top_left', [])
        top_right = album_info.get('top_right', [])
        bottom_left = album_info.get('bottom_left', [])
        bottom_right = album_info.get('bottom_right', [])
        
        # 向后兼容：如果没有四个角点，尝试使用旧格式
        if not all([top_left, top_right, bottom_left, bottom_right]):
            old_top_left = album_info.get('top-left', [])
            old_bottom_right = album_info.get('bottom-right', [])
            if len(old_top_left) == 2 and len(old_bottom_right) == 2:
                return old_top_left[0], old_top_left[1], old_bottom_right[0], old_bottom_right[1]
            else:
                return None
        
        # 检查所有坐标是否完整
        all_corners = [top_left, top_right, bottom_left, bottom_right]
        if not all(len(corner) == 2 for corner in all_corners):
            return None
        
        # 从四个角点计算边界框
        x_coords = [top_left[0], top_right[0], bottom_left[0], bottom_right[0]]
        y_coords = [top_left[1], top_right[1], bottom_left[1], bottom_right[1]]
        
        min_x = min(x_coords)
        max_x = max(x_coords)
        min_y = min(y_coords)
        max_y = max(y_coords)
        
        return min_x, min_y, max_x, max_y
        
    except Exception as e:
        logger.error(f"计算边界框失败: {e}")
        return None

def crop_albums(image_path, coordinates, output_dir):
    """根据坐标切分专辑图片，并搜索获取封面URL"""
    if not coordinates or not isinstance(coordinates, list):
        logger.warning("没有有效的坐标数据进行切分")
        return []
    
    try:
        # 打开原始图片
        with Image.open(image_path) as original_img:
            cropped_files = []
            album_results = []  # 存储专辑信息和封面URL
            
            for i, album_info in enumerate(coordinates):
                if not isinstance(album_info, dict):
                    continue
                
                # 获取专辑信息
                album_title = album_info.get('album_title', '').strip()
                artist_name = album_info.get('artist_name', '').strip()
                
                # 生成专辑名称用于文件名
                if album_title and artist_name:
                    album_name = f"{album_title} - {artist_name}"
                elif album_title:
                    album_name = album_title
                elif artist_name:
                    album_name = artist_name
                else:
                    # 向后兼容旧格式
                    album_name = album_info.get('album', f'album_{i+1}')
                
                # 从四个角点或两个角点计算边界框
                bbox_coords = calculate_bounding_box_from_corners(album_info)
                if not bbox_coords:
                    logger.warning(f"专辑 '{album_name}' 坐标格式不正确，跳过")
                    continue
                
                x1, y1, x2, y2 = bbox_coords
                
                # 确保坐标为整数
                x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
                
                # 确保坐标顺序正确
                if x1 >= x2 or y1 >= y2:
                    logger.warning(f"专辑 '{album_name}' 坐标顺序不正确，跳过")
                    continue
                
                # 计算当前宽高
                width = x2 - x1
                height = y2 - y1
                
                # 确保切分区域是合理的正方形/矩形（最小尺寸检查）
                if width < 50 or height < 50:
                    logger.warning(f"专辑 '{album_name}' 切分区域太小 ({width}x{height})，跳过")
                    continue
                
                # 轻微扩展边界以确保完整性（增加5像素的容错）
                padding = 5
                x1 = max(0, x1 - padding)
                y1 = max(0, y1 - padding)
                x2 = min(original_img.width, x2 + padding)
                y2 = min(original_img.height, y2 + padding)
                
                # 最终确保坐标在图片范围内
                x1 = max(0, min(x1, original_img.width))
                y1 = max(0, min(y1, original_img.height))
                x2 = max(0, min(x2, original_img.width))
                y2 = max(0, min(y2, original_img.height))
                
                # 再次检查调整后的坐标
                if x1 >= x2 or y1 >= y2:
                    logger.warning(f"专辑 '{album_name}' 调整后坐标无效，跳过")
                    continue
                
                # 切分图片
                cropped_img = original_img.crop((x1, y1, x2, y2))
                
                # 生成安全的文件名
                safe_album_name = "".join(c for c in album_name if c.isalnum() or c in (' ', '-', '_')).rstrip()
                safe_album_name = safe_album_name[:50]  # 限制文件名长度
                if not safe_album_name:
                    safe_album_name = f'album_{i+1}'
                
                # 保存切分后的图片
                output_filename = f"{safe_album_name}.png"
                output_path = os.path.join(output_dir, output_filename)
                
                # 避免文件名冲突
                counter = 1
                while os.path.exists(output_path):
                    output_filename = f"{safe_album_name}_{counter}.png"
                    output_path = os.path.join(output_dir, output_filename)
                    counter += 1
                
                cropped_img.save(output_path, 'PNG')
                cropped_files.append(output_path)
                
                # 搜索专辑封面URL
                cover_url = None
                cover_downloaded = False
                cover_path = None
                if album_title and artist_name:
                    logger.info(f"正在搜索专辑封面: {album_title} - {artist_name}")
                    cover_url = search_album_cover(album_title, artist_name)
                    
                    # 下载封面图片
                    if cover_url:
                        # 生成封面文件名
                        cover_filename = f"{safe_album_name}_cover.jpg"
                        cover_path = os.path.join(output_dir, cover_filename)
                        
                        # 避免文件名冲突
                        cover_counter = 1
                        while os.path.exists(cover_path):
                            cover_filename = f"{safe_album_name}_cover_{cover_counter}.jpg"
                            cover_path = os.path.join(output_dir, cover_filename)
                            cover_counter += 1
                        
                        # 下载封面
                        cover_downloaded = download_cover_image(cover_url, cover_path)
                        if cover_downloaded:
                            logger.info(f"封面已下载: {cover_filename}")
                        else:
                            logger.warning(f"封面下载失败: {album_title} - {artist_name}")
                            cover_path = None
                else:
                    logger.warning(f"专辑信息不完整，跳过封面搜索: title='{album_title}', artist='{artist_name}'")
                
                # 记录专辑结果
                album_result = {
                    'file_path': output_path,
                    'album_title': album_title,
                    'artist_name': artist_name,
                    'cover_url': cover_url,
                    'cover_downloaded': cover_downloaded,
                    'cover_path': cover_path,
                    'coordinates': {'top_left': [x1, y1], 'bottom_right': [x2, y2]}
                }
                album_results.append(album_result)
                
                # 更详细的日志输出
                if album_title and artist_name:
                    logger.info(f"已保存切分图片: {output_path}")
                    logger.info(f"  专辑: {album_title} | 艺术家: {artist_name} | 坐标: ({x1},{y1}) -> ({x2},{y2})")
                    if cover_url:
                        logger.info(f"  封面URL: {cover_url}")
                        if cover_downloaded:
                            logger.info(f"  封面文件: {os.path.basename(cover_path)}")
                    else:
                        logger.warning(f"  未找到封面URL")
                else:
                    logger.info(f"已保存切分图片: {output_path} (坐标: {x1},{y1} -> {x2},{y2})")
            
            # 保存专辑信息和封面URL到JSON文件
            if album_results:
                json_output_path = os.path.join(output_dir, "album_info.json")
                try:
                    with open(json_output_path, 'w', encoding='utf-8') as json_file:
                        json.dump(album_results, json_file, ensure_ascii=False, indent=2)
                    logger.info(f"专辑信息已保存到: {json_output_path}")
                except Exception as e:
                    logger.error(f"保存专辑信息JSON文件失败: {e}")
            
            return cropped_files
    
    except Exception as e:
        logger.error(f"切分图片时发生错误: {e}")
        return []

def process_image(image_path, output_dir=None):
    """处理单张图片的完整流程"""
    if not os.path.exists(image_path):
        logger.error(f"图片文件不存在: {image_path}")
        return False
    
    if not allowed_file(image_path):
        logger.error(f"不支持的图片格式: {image_path}")
        return False
    
    # 创建输出目录
    if output_dir is None:
        output_dir = os.path.join(os.path.dirname(image_path), "cropped_albums")
    
    os.makedirs(output_dir, exist_ok=True)
    
    logger.info(f"开始处理图片: {image_path}")
    
    # 获取图片尺寸
    dimensions = get_image_dimensions(image_path)
    if not dimensions:
        return False
    
    logger.info(f"图片尺寸: {dimensions}")
    
    # 调用Gemini API获取坐标
    coordinates = call_gemini_api(image_path, dimensions)
    if not coordinates:
        logger.error("获取专辑坐标失败")
        return False
    
    logger.info(f"识别到 {len(coordinates)} 个专辑单元")
    
    # 显示识别到的专辑信息
    for i, album_info in enumerate(coordinates):
        if isinstance(album_info, dict):
            album_title = album_info.get('album_title', '')
            artist_name = album_info.get('artist_name', '')
            if album_title and artist_name:
                logger.info(f"  {i+1}. {album_title} - {artist_name}")
            elif album_title or artist_name:
                logger.info(f"  {i+1}. {album_title or artist_name}")
            else:
                logger.info(f"  {i+1}. 未识别的专辑")
    
    # 切分图片并搜索封面
    cropped_files = crop_albums(image_path, coordinates, output_dir)
    
    if cropped_files:
        logger.info(f"图片处理完成，共切分出 {len(cropped_files)} 张图片")
        logger.info(f"输出目录: {output_dir}")
        
        # 读取并显示专辑信息统计
        json_output_path = os.path.join(output_dir, "album_info.json")
        if os.path.exists(json_output_path):
            try:
                with open(json_output_path, 'r', encoding='utf-8') as json_file:
                    album_results = json.load(json_file)
                
                # 统计封面URL获取情况
                total_albums = len(album_results)
                found_covers = sum(1 for result in album_results if result.get('cover_url'))
                downloaded_covers = sum(1 for result in album_results if result.get('cover_downloaded'))
                
                logger.info(f"封面搜索统计: {found_covers}/{total_albums} 个专辑找到了封面URL")
                logger.info(f"封面下载统计: {downloaded_covers}/{found_covers} 个封面成功下载")
                
                # 显示详细结果
                for result in album_results:
                    filename = os.path.basename(result['file_path'])
                    album_title = result.get('album_title', '')
                    artist_name = result.get('artist_name', '')
                    cover_url = result.get('cover_url', '')
                    cover_downloaded = result.get('cover_downloaded', False)
                    cover_path = result.get('cover_path', '')
                    
                    if album_title and artist_name:
                        if cover_url:
                            if cover_downloaded and cover_path:
                                cover_filename = os.path.basename(cover_path)
                                logger.info(f"  ✓ {filename} | {album_title} - {artist_name}")
                                logger.info(f"    封面: {cover_url}")
                                logger.info(f"    已下载: {cover_filename}")
                            else:
                                logger.info(f"  ⚠ {filename} | {album_title} - {artist_name}")
                                logger.info(f"    封面: {cover_url} (下载失败)")
                        else:
                            logger.info(f"  ✗ {filename} | {album_title} - {artist_name} (未找到封面)")
                    else:
                        logger.info(f"  ? {filename} (信息不完整)")
                
                # 进行图片相似度检查
                logger.info("=" * 60)
                similarity_results = compare_album_similarities(album_results)
                
                # 保存相似度结果到JSON文件
                similarity_json_path = os.path.join(output_dir, "similarity_results.json")
                try:
                    with open(similarity_json_path, 'w', encoding='utf-8') as f:
                        json.dump(similarity_results, f, ensure_ascii=False, indent=2)
                    logger.info(f"相似度检查结果已保存到: {similarity_json_path}")
                except Exception as e:
                    logger.error(f"保存相似度结果失败: {e}")
                
                # 输出相似度统计摘要
                successful_comparisons = [r for r in similarity_results if r['status'] == 'success']
                if successful_comparisons:
                    high_similarity_count = sum(1 for r in successful_comparisons 
                                              if r['similarity']['similarity_percentage'] >= 80)
                    medium_similarity_count = sum(1 for r in successful_comparisons 
                                                if 60 <= r['similarity']['similarity_percentage'] < 80)
                    low_similarity_count = sum(1 for r in successful_comparisons 
                                             if r['similarity']['similarity_percentage'] < 60)
                    
                    avg_similarity = sum(r['similarity']['similarity_percentage'] for r in successful_comparisons) / len(successful_comparisons)
                    
                    logger.info("=" * 60)
                    logger.info("相似度检查摘要:")
                    logger.info(f"  检查总数: {len(similarity_results)}")
                    logger.info(f"  成功比较: {len(successful_comparisons)}")
                    logger.info(f"  高相似度 (≥80%): {high_similarity_count}")
                    logger.info(f"  中等相似度 (60%-79%): {medium_similarity_count}")
                    logger.info(f"  低相似度 (<60%): {low_similarity_count}")
                    logger.info(f"  平均相似度: {avg_similarity:.2f}%")
                    logger.info("=" * 60)
                        
            except Exception as e:
                logger.error(f"读取专辑信息文件失败: {e}")
        
        for file_path in cropped_files:
            logger.info(f"  - {os.path.basename(file_path)}")
        return True
    else:
        logger.error("没有成功切分任何图片")
        return False

def search_album_cover(album_title, artist_name):
    """搜索专辑并获取封面URL"""
    if not album_title or not artist_name:
        logger.warning("专辑名或艺术家名为空，跳过搜索")
        return None
    
    try:
        # 构造搜索词：专辑名 空格 歌手名
        search_term = f"{album_title} {artist_name}"
        
        # URL编码搜索词
        encoded_term = urllib.parse.quote(search_term)
        
        # 构造搜索URL
        search_url = f"{BACKEND_BASE_URL}/search"
        params = {
            'term': search_term,
            'types': 'albums',
            'limit': 1  # 只获取第一个结果
        }
        
        # 设置请求头
        headers = {
            'X-Storefront': 'cn',  # 使用中国区
            'X-Use-Cache': 'true'  # 启用缓存
        }
        
        logger.info(f"搜索专辑: {search_term}")
        
        # 发送搜索请求
        response = requests.get(search_url, params=params, headers=headers, timeout=10)
        
        if response.status_code == 200:
            search_data = response.json()
            
            # 从搜索结果中提取专辑信息
            if ('results' in search_data and 
                'albums' in search_data['results'] and 
                'data' in search_data['results']['albums'] and 
                len(search_data['results']['albums']['data']) > 0):
                
                # 获取第一个专辑结果
                first_album = search_data['results']['albums']['data'][0]
                attributes = first_album.get('attributes', {})
                artwork = attributes.get('artwork', {})
                
                if 'url' in artwork:
                    # Apple Music 封面URL模板，替换为200x200
                    cover_url_template = artwork['url']
                    cover_url_200 = cover_url_template.replace('{w}', '200').replace('{h}', '200')
                    
                    logger.info(f"找到专辑封面: {cover_url_200}")
                    return cover_url_200
                else:
                    logger.warning(f"搜索结果中没有找到封面URL: {album_title} - {artist_name}")
                    return None
            else:
                logger.warning(f"搜索无结果: {album_title} - {artist_name}")
                return None
        else:
            logger.error(f"搜索请求失败，状态码: {response.status_code}, 专辑: {album_title} - {artist_name}")
            return None
            
    except requests.exceptions.Timeout:
        logger.error(f"搜索请求超时: {album_title} - {artist_name}")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"搜索请求网络错误: {e}, 专辑: {album_title} - {artist_name}")
        return None
    except Exception as e:
        logger.error(f"搜索专辑封面时发生错误: {e}, 专辑: {album_title} - {artist_name}", exc_info=True)
        return None

def download_cover_image(cover_url, output_path):
    """下载封面图片到指定路径"""
    if not cover_url:
        logger.warning("封面URL为空，跳过下载")
        return False
    
    try:
        logger.info(f"正在下载封面: {cover_url}")
        
        # 使用 subprocess 调用 curl 来下载，绕过 Python 的 SSL 问题
        import subprocess
        
        curl_command = [
            'curl',
            '-L',  # 跟随重定向
            '-s',  # 静默模式
            '--max-time', '15',  # 15秒超时
            '--user-agent', 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            '--referer', 'https://music.apple.com/',
            '-H', 'Accept: image/webp,image/apng,image/*,*/*;q=0.8',
            '--output', output_path,
            cover_url
        ]
        
        # 执行 curl 命令
        result = subprocess.run(curl_command, capture_output=True, text=True, timeout=20)
        
        if result.returncode == 0:
            # 检查文件是否成功下载
            if os.path.exists(output_path):
                file_size = os.path.getsize(output_path)
                if file_size >= 1024:  # 至少1KB
                    logger.info(f"封面下载成功: {output_path} ({file_size} bytes)")
                    return True
                else:
                    logger.warning(f"下载的封面文件太小: {file_size} bytes")
                    try:
                        os.remove(output_path)
                    except OSError:
                        pass
                    return False
            else:
                logger.error(f"封面文件下载后不存在: {output_path}")
                return False
        else:
            logger.error(f"curl 下载封面失败，返回码: {result.returncode}")
            if result.stderr:
                logger.error(f"curl 错误信息: {result.stderr}")
            return False
        
    except subprocess.TimeoutExpired:
        logger.error(f"下载封面超时: {cover_url}")
        return False
    except Exception as e:
        logger.error(f"下载封面时发生错误: {e}", exc_info=True)
        return False

def calculate_image_similarity(image1_path, image2_path):
    """计算两张图片的相似度"""
    if not os.path.exists(image1_path) or not os.path.exists(image2_path):
        logger.warning(f"相似度检查失败：图片文件不存在")
        return None
    
    try:
        # 打开图片
        img1 = Image.open(image1_path)
        img2 = Image.open(image2_path)
        
        # 计算感知哈希值（pHash）
        hash1 = imagehash.phash(img1)
        hash2 = imagehash.phash(img2)
        
        # 计算哈希值的汉明距离（距离越小越相似）
        hamming_distance = hash1 - hash2
        
        # 计算相似度百分比（距离为0表示100%相似，距离为64表示完全不同）
        # 对于64位哈希，最大距离为64
        similarity_percentage = max(0, (64 - hamming_distance) / 64 * 100)
        
        return {
            'hamming_distance': hamming_distance,
            'similarity_percentage': round(similarity_percentage, 2),
            'hash1': str(hash1),
            'hash2': str(hash2)
        }
        
    except Exception as e:
        logger.error(f"计算图片相似度时发生错误: {e}")
        return None

def compare_album_similarities(album_results):
    """对所有专辑的切分图片和封面图片进行相似度比较"""
    logger.info("开始进行图片相似度检查...")
    
    similarity_results = []
    
    for i, album_result in enumerate(album_results):
        album_title = album_result.get('album_title', '未知专辑')
        artist_name = album_result.get('artist_name', '未知艺术家')
        cropped_path = album_result.get('file_path')
        cover_path = album_result.get('cover_path')
        cover_downloaded = album_result.get('cover_downloaded', False)
        
        logger.info(f"检查相似度 ({i+1}/{len(album_results)}): {album_title} - {artist_name}")
        
        if not cover_downloaded or not cover_path:
            logger.warning(f"  跳过：未下载封面图片")
            similarity_results.append({
                'album_title': album_title,
                'artist_name': artist_name,
                'cropped_path': cropped_path,
                'cover_path': cover_path,
                'similarity': None,
                'status': 'no_cover'
            })
            continue
        
        # 计算相似度
        similarity = calculate_image_similarity(cropped_path, cover_path)
        
        if similarity:
            similarity_results.append({
                'album_title': album_title,
                'artist_name': artist_name,
                'cropped_path': cropped_path,
                'cover_path': cover_path,
                'similarity': similarity,
                'status': 'success'
            })
            
            # 输出相似度结果
            hamming_dist = similarity['hamming_distance']
            similarity_pct = similarity['similarity_percentage']
            
            if similarity_pct >= 80:
                status_icon = "✅"
                status_desc = "高度相似"
            elif similarity_pct >= 60:
                status_icon = "⚠️"
                status_desc = "中等相似"
            else:
                status_icon = "❌"
                status_desc = "低相似度"
            
            logger.info(f"  {status_icon} 相似度: {similarity_pct}% (汉明距离: {hamming_dist}) - {status_desc}")
        else:
            logger.error(f"  计算相似度失败")
            similarity_results.append({
                'album_title': album_title,
                'artist_name': artist_name,
                'cropped_path': cropped_path,
                'cover_path': cover_path,
                'similarity': None,
                'status': 'error'
            })
    
    return similarity_results

def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='使用Gemini API切分专辑图片')
    parser.add_argument('image_path', help='输入图片路径')
    parser.add_argument('-o', '--output', help='输出目录路径（默认为输入图片同目录下的cropped_albums文件夹）')
    
    args = parser.parse_args()
    
    success = process_image(args.image_path, args.output)
    
    if success:
        print(f"✅ 图片切分完成！")
        sys.exit(0)
    else:
        print(f"❌ 图片切分失败，请查看日志了解详情")
        sys.exit(1)

if __name__ == '__main__':
    main()
