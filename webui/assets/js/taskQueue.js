import * as State from './state.js';
import * as UI from './ui.js';   // 需要 updateScrollButtons

// 创建或更新封面占位符和图片元素
async function createOrUpdateCoverElement(task) {
    if (!task || !task.uuid) {
        console.warn("createOrUpdateCoverElement: 无效的任务数据或缺少 UUID", task);
        return null;
    }
    const container = State.domElements.taskQueueCoversContainer;
    if (!container) return null;

    const uuid = task.uuid;
    let placeholderDiv = container.querySelector(`div.cover-placeholder[data-uuid="${uuid}"]`);
    let img = placeholderDiv ? placeholderDiv.querySelector('img') : null;

    // 确定状态对应的 CSS 类
    let statusClass = `status-${task.status || 'unknown'}`; // 例如 status-running, status-ready 等

    // 获取元数据
    const metadata = task.metadata || {};
    const title = metadata.name || '处理中或信息缺失';
    const artworkUrlTemplate = metadata.artwork_url || null;
    const displayUser = State.linkUserCache.get(uuid) || task.user || '未知';
    const curatorName = metadata.curatorName || null; // 添加创建者信息

    // 处理封面图 URL
    let artworkSrc = '';
    let highResArtworkSrc = '';
    if (artworkUrlTemplate) {
        try {
            console.log(`[URLProcess] 处理封面URL模板 UUID: ${uuid}`);
            console.log(`[URLProcess] 原始模板: ${artworkUrlTemplate}`);
            
            // 低分辨率预览图 (用于占位或快速显示)
            artworkSrc = artworkUrlTemplate
                .replace('{w}', '150')
                .replace('{h}', '150')
                .replace('{f}', 'jpg')
                .replace('{c}', 'bf')
                .replace('{q}', '90');

            // ===== 修复：根据任务类型确定高分辨率尺寸 =====
            const linkInfo = task.link_info || {};
            const isMV = linkInfo.type === 'music-video';
            
            if (isMV) {
                // MV类型使用1920x1080（与Modal一致）
                highResArtworkSrc = artworkUrlTemplate
                    .replace('{w}', '1920')
                    .replace('{h}', '1080')
                    .replace('{f}', 'jpg')
                    .replace('{c}', 'bf')
                    .replace('{q}', '90');
                console.log(`[URLProcess] MV类型，使用1920x1080尺寸`);
            } else {
                // 音乐类型使用1200x1200（与Modal一致）
                highResArtworkSrc = artworkUrlTemplate
                    .replace('{w}', '1200')
                    .replace('{h}', '1200')
                    .replace('{f}', 'jpg')
                    .replace('{c}', 'bf')
                    .replace('{q}', '90');
                console.log(`[URLProcess] 音乐类型，使用1200x1200尺寸`);
            }

            console.log(`[URLProcess] 低分辨率URL: ${artworkSrc}`);
            console.log(`[URLProcess] 高分辨率URL: ${highResArtworkSrc}`);

            // ===== 增强缓存检查日志 =====
            console.log(`[CacheCheck] 检查缓存状态 UUID: ${uuid}`);
            console.log(`[CacheCheck] 缓存初始化状态: ${State.imageCache ? '已初始化' : '未初始化'}`);
            console.log(`[CacheCheck] 总缓存大小: ${State.imageCache ? State.imageCache.size : 0}`);
            console.log(`[CacheCheck] 缓存中的所有UUID: ${State.imageCache ? Array.from(State.imageCache.keys()).join(', ') : '无'}`);
            
            const hasCachedImage = State.imageCache && State.imageCache.has(uuid);
            console.log(`[CacheCheck] UUID ${uuid} 缓存命中: ${hasCachedImage ? '是' : '否'}`);
            
            if (hasCachedImage) {
                const cachedImage = State.imageCache.get(uuid);
                console.log(`[CacheHit] 缓存命中详情 UUID: ${uuid}`);
                console.log(`[CacheHit] 缓存图片URL: ${cachedImage.src}`);
                console.log(`[CacheHit] 缓存图片尺寸: ${cachedImage.naturalWidth}x${cachedImage.naturalHeight}`);
                console.log(`[CacheHit] 缓存图片完整状态: ${cachedImage.complete}`);
                console.log(`[CacheHit] 预期高分辨率URL: ${highResArtworkSrc}`);
                console.log(`[CacheHit] URL匹配状态: ${cachedImage.src === highResArtworkSrc ? '匹配' : '不匹配'}`);
                
                // ===== 新增：如果URL不匹配，说明任务类型可能发生了变化 =====
                if (cachedImage.src !== highResArtworkSrc) {
                    console.log(`[CacheHit] URL不匹配，可能是任务类型变化，清除旧缓存`);
                    State.imageCache.delete(uuid);
                    console.log(`[CacheHit] 已清除不匹配的缓存，当前缓存大小: ${State.imageCache.size}`);
                }
            } else {
                console.log(`[CacheMiss] 缓存未命中 UUID: ${uuid}, 需要预加载`);
            }

            // 预加载高分辨率图片并缓存（重新检查缓存状态）
            const needsPreload = !State.imageCache.has(uuid);
            if (needsPreload) {
                console.log(`[ImageCache] 开始预加载高分辨率图片: ${uuid}`);
                console.log(`[ImageCache] 高分辨率URL: ${highResArtworkSrc}`);
                console.log(`[ImageCache] 当前缓存大小: ${State.imageCache.size}`);
                
                const preloadImage = new Image();
                preloadImage.onload = () => {
                    console.log(`[ImageCache] 高分辨率图片加载完成: ${uuid}`);
                    console.log(`[ImageCache] 图片尺寸: ${preloadImage.naturalWidth}x${preloadImage.naturalHeight}`);
                    State.imageCache.set(uuid, preloadImage);
                    console.log(`[ImageCache] 缓存已更新，当前大小: ${State.imageCache.size}`);
                    console.log(`[ImageCache] ===== 这张图片现在可以在Modal中直接使用 =====`);
                };
                preloadImage.onerror = (e) => {
                    console.error(`[ImageCache] 高分辨率图片加载失败: ${uuid}`, e);
                };
                preloadImage.src = highResArtworkSrc;
            } else {
                console.log(`[ImageCache] 高分辨率图片已缓存: ${uuid}`);
            }

        } catch (e) {
            console.error(`处理封面 URL 模板时出错 (UUID: ${uuid}, URL: ${artworkUrlTemplate}):`, e);
            artworkSrc = '';
            highResArtworkSrc = '';
        }
    } else {
        console.log(`[URLProcess] 无封面URL模板 UUID: ${uuid}`);
        console.log(`[CacheCheck] 跳过缓存检查，因为无URL模板 UUID: ${uuid}`);
    }

    // 设置 alt 和 title 文本 (title 现在用于占位符 div)
    const statusTextMap = {
        'running': '运行中',
        'ready': '等待中',
        'finish': '已完成',
        'error': '错误',
        'unknown': '未知状态'
    };
    const statusText = statusTextMap[task.status] || task.status;
    const altText = `${title}${curatorName ? ` - ${curatorName}` : ''} (${statusText})`; // img 的 alt
    const titleText = `${title}${curatorName ? `\n创建者: ${curatorName}` : ''}\n状态: ${statusText}\n用户: ${displayUser}`; // div 的 title

    if (!placeholderDiv) { // 创建新占位符和图片
        console.log(`[ImageCreate] 创建新封面元素 UUID: ${uuid}`);
        placeholderDiv = document.createElement('div');
        placeholderDiv.className = 'cover-placeholder'; // 新的 CSS 类
        placeholderDiv.dataset.uuid = uuid;
        placeholderDiv.dataset.tooltipText = titleText; // --- 新增：将 tooltip 文本存入 dataset ---
        placeholderDiv.addEventListener('click', () => UI.handleCoverClick(placeholderDiv)); // 点击事件绑定到占位符

        img = document.createElement('img');
        img.loading = 'lazy';
        img.alt = ''; // 移除默认alt文本
        img.style.opacity = '0'; // 初始设置为透明
        img.className = statusClass; // 状态类仍在 img 上，用于边框

        // 图片加载完成后显示
        img.onload = () => {
            img.style.opacity = '1';
        };

        // ===== 增强新元素的缓存使用日志 =====
        console.log(`[ImageCreate] 设置新元素图片源 UUID: ${uuid}`);
        const hasCachedForNew = State.imageCache && State.imageCache.has(uuid);
        console.log(`[ImageCreate] 新元素缓存检查结果: ${hasCachedForNew ? '有缓存' : '无缓存'}`);
        
        if (hasCachedForNew) {
            const cachedImage = State.imageCache.get(uuid);
            console.log(`[ImageCreate] 新元素使用缓存图片 UUID: ${uuid}`);
            console.log(`[ImageCreate] 缓存图片URL: ${cachedImage.src}`);
            console.log(`[ImageCreate] 应用缓存到新元素`);
            img.src = cachedImage.src;
        } else if (artworkSrc) {
            console.log(`[ImageCreate] 新元素使用低分辨率图片 UUID: ${uuid}`);
            console.log(`[ImageCreate] 低分辨率URL: ${artworkSrc}`);
            img.src = artworkSrc;
        }

        placeholderDiv.appendChild(img);

        // 添加高分辨率图片加载逻辑
        if (highResArtworkSrc && !hasCachedForNew) {
            console.log(`[ImageCreate] 开始为新元素加载高分辨率图片 UUID: ${uuid}`);
            const highResImg = new Image();
            highResImg.onload = () => {
                // 检查 img 是否还在 DOM 中 (通过检查 placeholderDiv 是否还在)
                if (placeholderDiv.isConnected && img.parentNode === placeholderDiv) {
                    console.log(`[ImageCreate] 新元素高分辨率图片加载完成，更新显示 UUID: ${uuid}`);
                    img.src = highResArtworkSrc;
                    
                    // 缓存高分辨率图片
                    if (!State.imageCache) {
                        State.imageCache = new Map();
                        console.log(`[ImageCreate] 初始化图片缓存`);
                    }
                    State.imageCache.set(uuid, highResImg);
                    console.log(`[ImageCreate] 新元素图片已缓存 UUID: ${uuid}, 缓存大小: ${State.imageCache.size}`);
                } else {
                    console.warn(`[ImageCreate] 新元素已不在DOM中，跳过更新 UUID: ${uuid}`);
                }
            };
            highResImg.onerror = (e) => {
                console.error(`[ImageCreate] 新元素高分辨率图片加载失败 UUID: ${uuid}`, e);
            };
            highResImg.src = highResArtworkSrc;
        }

        return placeholderDiv; // 返回占位符 div
    }

    // --- 更新现有占位符和图片 ---
    console.log(`[ImageUpdate] 更新现有封面元素 UUID: ${uuid}`);

    // 更新占位符的 Title (现在是 dataset)
    if (placeholderDiv.dataset.tooltipText !== titleText) {
        placeholderDiv.dataset.tooltipText = titleText; // --- 修改：更新 dataset 中的 tooltip 文本 ---
    }

    // 更新图片属性
    const currentSrc = img.getAttribute('src');
    const currentClass = img.className;

    // 更新状态类 (只在 img 上)
    if (currentClass !== statusClass) {
        img.className = statusClass;
    }

    // 更新 alt 文本 (只在 img 上)
    if (img.alt !== altText) {
        img.alt = altText;
    }

    // 更新图片源 (只在需要时)
    if (currentSrc !== artworkSrc && currentSrc !== highResArtworkSrc) {
        console.log(`[ImageUpdate] 需要更新图片源 UUID: ${uuid}`);
        console.log(`[ImageUpdate] 当前源: ${currentSrc}`);
        console.log(`[ImageUpdate] 低分辨率源: ${artworkSrc}`);
        console.log(`[ImageUpdate] 高分辨率源: ${highResArtworkSrc}`);
        
        img.src = artworkSrc; // 先设置为低分辨率

        // ===== 增强更新时的缓存使用日志 =====
        const hasCachedForUpdate = State.imageCache && State.imageCache.has(uuid);
        console.log(`[ImageUpdate] 更新时缓存检查结果: ${hasCachedForUpdate ? '有缓存' : '无缓存'}`);
        
        // 检查是否有缓存的高分辨率图片
        if (hasCachedForUpdate) {
            const cachedImage = State.imageCache.get(uuid);
            console.log(`[ImageUpdate] 发现缓存图片 UUID: ${uuid}`);
            console.log(`[ImageUpdate] 缓存图片URL: ${cachedImage.src}`);
            console.log(`[ImageUpdate] 预期高分辨率URL: ${highResArtworkSrc}`);
            console.log(`[ImageUpdate] URL匹配: ${cachedImage.src === highResArtworkSrc ? '是' : '否'}`);
            console.log(`[ImageUpdate] 应用缓存图片到显示元素`);
            img.src = cachedImage.src;
        } else if (highResArtworkSrc) {
            console.log(`[ImageUpdate] 缓存中无图片，开始加载高分辨率 UUID: ${uuid}`);
            // 如果没有缓存，则加载高分辨率图片
            const highResImg = new Image();
            highResImg.onload = () => {
                if (placeholderDiv.isConnected && img.parentNode === placeholderDiv) {
                    console.log(`[ImageUpdate] 高分辨率图片加载完成，更新显示 UUID: ${uuid}`);
                    img.src = highResArtworkSrc;
                    // 缓存高分辨率图片
                    if (!State.imageCache) {
                        State.imageCache = new Map();
                        console.log(`[ImageUpdate] 初始化图片缓存`);
                    }
                    State.imageCache.set(uuid, highResImg);
                    console.log(`[ImageUpdate] 图片已缓存 UUID: ${uuid}, 缓存大小: ${State.imageCache.size}`);
                } else {
                    console.warn(`[ImageUpdate] 元素已不在DOM中，跳过更新 UUID: ${uuid}`);
                }
            };
            highResImg.onerror = (e) => {
                console.error(`[ImageUpdate] 高分辨率图片加载失败 UUID: ${uuid}`, e);
            };
            highResImg.src = highResArtworkSrc;
        }
    } else {
        console.log(`[ImageUpdate] 图片源无需更新 UUID: ${uuid}, 当前源: ${currentSrc}`);
        
        // ===== 即使不需要更新，也记录缓存状态 =====
        const hasCachedForNoUpdate = State.imageCache && State.imageCache.has(uuid);
        console.log(`[ImageUpdate] 无需更新时缓存状态: ${hasCachedForNoUpdate ? '有缓存' : '无缓存'}`);
        if (hasCachedForNoUpdate) {
            const cachedImage = State.imageCache.get(uuid);
            console.log(`[ImageUpdate] 当前使用缓存图片 UUID: ${uuid}, URL: ${cachedImage.src}`);
        }
    }

    // 确保点击监听器存在 (理论上创建时已添加，但以防万一)
    if (!placeholderDiv.onclick) {
         placeholderDiv.addEventListener('click', () => UI.handleCoverClick(placeholderDiv));
    }

    return placeholderDiv; // 返回更新后的占位符 div
}


// 渲染任务队列封面
export async function renderTaskQueueCovers(tasks) { // 接收所有状态的任务
    const container = State.domElements.taskQueueCoversContainer;
    if (!container) return;

    // --- 新增：获取 tooltip 元素，用于后续检查 ---
    const tooltipElement = State.domElements.customTooltip;

    // --- 添加日志：记录输入的任务 ---
    console.debug('[renderTaskQueueCovers] Input tasks:', JSON.stringify(tasks.map(t => ({ uuid: t.uuid, name: t.metadata?.name, order_index: t.order_index }))));


    // 1. 获取当前 DOM 中的元素 Map
    const domElementsMap = new Map();
    container.querySelectorAll('div.cover-placeholder[data-uuid]').forEach(div => {
        domElementsMap.set(div.dataset.uuid, div);
    });
    // --- 添加日志：记录 DOM 状态 ---
    console.debug(`[renderTaskQueueCovers] Found ${domElementsMap.size} existing elements in DOM:`, Array.from(domElementsMap.keys()));


    // 2. 创建新任务 UUID Set
    const newTaskUuids = new Set(tasks.map(t => t.uuid).filter(Boolean));
    // console.debug(`[renderTaskQueueCovers] Received ${newTaskUuids.size} tasks from API.`);

    // 3. 处理更新和创建 (异步)
    const elementPromises = tasks.map(async (task) => { // 确保这里是 async
        if (!task || !task.uuid) return Promise.resolve(null);
        const existingDiv = domElementsMap.get(task.uuid);
        let elementResult;
        if (existingDiv) {
            domElementsMap.delete(task.uuid); // 从待移除 Map 中删除
            // --- 添加日志：记录更新动作 ---
            console.debug(`[renderTaskQueueCovers] Updating element: ${task.uuid} (${task.metadata?.name})`);
            elementResult = await createOrUpdateCoverElement(task); // 假设 createOrUpdateCoverElement 返回更新后的元素
        } else {
             // --- 添加日志：记录创建动作 ---
            console.debug(`[renderTaskQueueCovers] Creating new element: ${task.uuid} (${task.metadata?.name})`);
            elementResult = await createOrUpdateCoverElement(task);
        }
         // --- 添加日志：记录单个元素处理结果 ---
        console.debug(`[renderTaskQueueCovers] Processed element for ${task.uuid}, result:`, elementResult ? 'OK' : 'NULL');
        return elementResult; // 返回处理结果
    });


    const updatedOrNewElements = await Promise.all(elementPromises);

    // Filter out any null results from failed promises/invalid tasks
    const validElements = updatedOrNewElements.filter(Boolean);
    const finalElementMap = new Map(validElements.map(el => [el.dataset.uuid, el]));
     // --- 添加日志：记录有效元素 ---
    console.debug(`[renderTaskQueueCovers] Processed ${finalElementMap.size} valid elements for rendering:`, Array.from(finalElementMap.keys()));


    // 4. 移除不再需要的旧元素 (仍在 domElementsMap 中的)
     // --- 添加日志：记录待移除元素 ---
    if (domElementsMap.size > 0) {
        console.debug(`[renderTaskQueueCovers] Elements to remove:`, Array.from(domElementsMap.keys()));

        // --- 修改：更简单的 tooltip 检查，基于 activeTooltipUuid ---
        if (tooltipElement && tooltipElement.style.display !== 'none' && window.activeTooltipUuid) {
            // 检查是否有任何要删除的元素与当前显示的 tooltip 有关
            if (domElementsMap.has(window.activeTooltipUuid)) {
                console.debug(`[renderTaskQueueCovers] Hiding tooltip for removed element ${window.activeTooltipUuid}`);
                tooltipElement.classList.remove('show');
                tooltipElement.style.display = 'none';
                window.activeTooltipUuid = null;
            }
        }

        // 现有的移除逻辑
        domElementsMap.forEach(divToRemove => {
            container.removeChild(divToRemove);
        });
    } else {
        // console.debug('[renderTaskQueueCovers] No old elements to remove.');
         console.debug('[renderTaskQueueCovers] No old elements to remove.');
    }

    // 5. 重新排序和插入新元素
    console.debug('[renderTaskQueueCovers] Starting reordering phase...');
    
    // --- 新逻辑：清空容器，然后按正确顺序追加 ---
    // 备份需要保留的元素（理论上 finalElementMap 包含了所有需要的）
    const elementsToReadd = [];
    tasks.forEach(task => {
        if (task && task.uuid) {
            const element = finalElementMap.get(task.uuid);
            if (element) {
                elementsToReadd.push(element);
            } else {
                // 如果 finalElementMap 中没有，这是一个错误，记录下来
                console.error(`[renderTaskQueueCovers] Reordering error: Element for task ${task.uuid} (${task.metadata?.name}) not found in finalElementMap during final append preparation!`);
            }
        }
    });

    // 清空容器
    while (container.firstChild) {
        container.removeChild(container.firstChild);
    }
    console.debug('[renderTaskQueueCovers] Container cleared.');

    // 按 tasks 数组的顺序追加元素
    elementsToReadd.forEach(element => {
        container.appendChild(element);
    });
    console.debug(`[renderTaskQueueCovers] Appended ${elementsToReadd.length} elements in order.`);
    // --- 新逻辑结束 ---

    /*  --- 旧的排序逻辑 (注释掉或删除) ---
    let previousElement = null; // Track the last correctly positioned element
    tasks.forEach((task, index) => {
        // ... (旧的检查和移动逻辑) ...
        previousElement = elementToPlace;
    });
    console.debug('[renderTaskQueueCovers] Reordering complete.');
    */

    // 6. 更新滚动按钮状态
    UI.updateScrollButtons();
    // console.debug('[renderTaskQueueCovers] Update finished.');
     console.debug('[renderTaskQueueCovers] Update finished.');

}