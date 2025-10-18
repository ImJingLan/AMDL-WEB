// modalHandler.js

import * as State from './state.js';

// 更新 Modal 基础信息 (封面、用户信息) - 移除封面 max-width
export async function updateLogModalInfo(taskData) {
    const uuid = taskData ? taskData.uuid : null;
    // console.debug(`尝试更新 Modal 基础信息，UUID: ${uuid || '无任务'}`);

    const logModalElement = State.domElements.logModalElement;
    if (!logModalElement) { console.error("Modal 元素未找到"); return; }
    const modalBody = logModalElement.querySelector('.modal-body');
    if (!modalBody) { console.error("Modal body 未找到"); return; }

    if (taskData && uuid) {
        let infoCol = modalBody.querySelector('#logModalInfoColumn');
        let contentCol = modalBody.querySelector('#logModalContentColumn');

        // 检查并可能创建布局
        if (!infoCol || !contentCol || modalBody.querySelector('.log-no-task')) {
            const linkInfo = taskData.link_info || {};
            const isMV = linkInfo.type === 'music-video';
            
            if (isMV) {
                // MV类型使用上下布局
                modalBody.innerHTML = `
                    <div class="row log-task-active">
                        <div class="col-12 text-center mb-3" id="logModalInfoColumn">
                            <div style="width: 100%; padding-top: 56.25%; position: relative; margin-bottom: 1rem;">
                                <img id="logModalCover" src="" alt="封面加载中..." class="img-fluid" 
                                    style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; object-fit: cover; opacity: 0; transition: opacity 0.4s ease-in-out;">
                            </div>
                            <p id="logModalUser" class="small text-muted mt-2">下载用户：查询中...</p>
                        </div>
                        <div class="col-12" id="logModalContentColumn">
                            <div id="formattedLogOutput">
                                <p class="text-muted text-center p-5">等待任务详情...</p>
                            </div>
                        </div>
                    </div>`;
            } else {
                // 其他类型保持原有左右布局
                modalBody.innerHTML = `
                    <div class="row log-task-active">
                        <div class="col-md-6 text-center d-flex flex-column justify-content-start" id="logModalInfoColumn">
                            <img id="logModalCover" src="" alt="封面加载中..." class="img-fluid mb-2" style="margin-left: auto; margin-right:auto; opacity: 0; transition: opacity 0.4s ease-in-out;">
                            <p id="logModalUser" class="small text-muted mt-1">下载用户：查询中...</p>
                        </div>
                        <div class="col-md-6" id="logModalContentColumn">
                            <div id="formattedLogOutput">
                                <p class="text-muted text-center p-5">等待任务详情...</p>
                            </div>
                        </div>
                    </div>`;
            }
            infoCol = modalBody.querySelector('#logModalInfoColumn');
            contentCol = modalBody.querySelector('#logModalContentColumn');
        }

        const modalCoverEl = infoCol ? infoCol.querySelector('#logModalCover') : null;
        const modalUserEl = infoCol ? infoCol.querySelector('#logModalUser') : null;

        // 更新用户信息
        const user = State.linkUserCache.get(uuid) || taskData.user || '未知用户';
        if (modalUserEl) {
             modalUserEl.textContent = `下载用户：${user}`;
        }

        // 获取元数据和处理封面 URL
        const metadata = taskData.metadata || {};
        const title = metadata.name || "未知标题";
        const artworkUrlTemplate = metadata.artwork_url || null;
        let newCoverUrl = null;

        if (artworkUrlTemplate) {
             try {
                 // 对于MV类型，使用更大的横屏尺寸
                 const linkInfo = taskData.link_info || {};
                 const isMV = linkInfo.type === 'music-video';
                 if (isMV) {
                     // 使用更宽的横屏比例 16:9
                     newCoverUrl = artworkUrlTemplate
                         .replace('{w}', '1920')
                         .replace('{h}', '1080')
                         .replace('{f}', 'jpg')
                         .replace('{c}', 'bf')
                         .replace('{q}', '90');
                 } else {
                     newCoverUrl = artworkUrlTemplate
                         .replace('{w}', '1200')
                         .replace('{h}', '1200')
                         .replace('{f}', 'jpg')
                         .replace('{c}', 'bf')
                         .replace('{q}', '90');
                 }
             } catch (e) { /* ... */ }
        }

        // 更新封面图片 - 优化后的版本
        if (modalCoverEl) {
            const targetSrc = newCoverUrl || "";
            modalCoverEl.alt = title || (targetSrc ? "任务封面" : "封面加载失败");
            const currentSrc = modalCoverEl.getAttribute('src');
            const linkInfo = taskData.link_info || {};
            const isMV = linkInfo.type === 'music-video';

            // 检查是否需要更新图片
            if (currentSrc !== targetSrc) {
                modalCoverEl.style.opacity = 0;

                // 简化的缓存检查逻辑
                const hasCachedImage = State.imageCache && State.imageCache.has(uuid);
                
                if (hasCachedImage) {
                    const cachedImage = State.imageCache.get(uuid);
                    
                    // 简化的缓存使用逻辑
                    let canUseCache = false;
                    
                    if (cachedImage.src === targetSrc) {
                        canUseCache = true;
                    } else if (isMV && cachedImage.src.includes('1920x1080') && targetSrc.includes('1920x1080')) {
                        canUseCache = true;
                    } else if (!isMV && cachedImage.src.includes('1200x1200') && targetSrc.includes('1200x1200')) {
                        canUseCache = true;
                    }
                    
                    if (canUseCache) {
                        // 使用缓存图片
                        modalCoverEl.src = cachedImage.src;
                        requestAnimationFrame(() => {
                            modalCoverEl.style.opacity = 1;
                        });
                        return;
                    }
                }
                
                // 没有合适缓存，加载新图片
                const newImage = new Image();
                newImage.onload = () => {
                    modalCoverEl.src = targetSrc;
                    // 只缓存大图
                    if (isMV && targetSrc.includes('1920x1080')) {
                        State.imageCache.set(uuid, newImage);
                    } else if (!isMV) {
                        State.imageCache.set(uuid, newImage);
                    }
                    requestAnimationFrame(() => {
                        modalCoverEl.style.opacity = 1;
                    });
                };
                newImage.onerror = (e) => {
                    console.error(`图片加载失败 UUID: ${uuid}`, e);
                    modalCoverEl.alt = "封面加载失败";
                    modalCoverEl.style.opacity = 1;
                };
                newImage.src = targetSrc;
            } else {
                modalCoverEl.style.opacity = 1;
            }
        }

        // 检查MV类型，动态加/去除mv-mode类
        const linkInfo = taskData.link_info || {};
        const isMV = linkInfo.type === 'music-video';
        if (isMV) {
            modalBody.classList.add('mv-mode');
        } else {
            modalBody.classList.remove('mv-mode');
        }

    } else {
        // 无任务数据
        modalBody.innerHTML = `<div class="log-no-task d-flex flex-column justify-content-center align-items-center h-100"><i class="bi bi-info-circle fs-1 text-muted mb-3"></i><p class="fs-5 text-muted">请选择一个任务以查看详情。</p></div>`;
    }
}


// 渲染 Modal 的主要内容区域 - 支持分批渲染优化
export function renderModalContentFromTaskData(taskData, options = {}) {
    const {
        fastRender = false,      // 是否快速渲染（只渲染前3个音轨）
        startFrom = 0,           // 从第几个音轨开始渲染
        batchSize = null         // 批次大小，null表示渲染全部
    } = options;

    const outputDiv = document.getElementById('formattedLogOutput');
    if (!outputDiv) {
        console.error("#formattedLogOutput 元素未找到，无法渲染内容。");
        return;
    }
    if (!taskData || !taskData.uuid) {
        console.warn(`Render Modal Content skipped: 无效的任务数据传入。`);
        outputDiv.innerHTML = '<p class="text-muted text-center p-5">任务数据无效或丢失。</p>';
        State.resetRenderingState();
        return;
    }

    // 防重复渲染检查
    if (!fastRender && startFrom === 0) {
        const lastData = State.getLastRenderTaskData();
        if (lastData && lastData.uuid === taskData.uuid) {
            // 检查数据是否真的有变化
            const tracksChanged = JSON.stringify(lastData.metadata?.tracks || []) !== 
                                 JSON.stringify(taskData.metadata?.tracks || []);
            const statusChanged = lastData.status !== taskData.status;
            const checkingChanged = !!lastData.checking !== !!taskData.checking;
            
            if (!tracksChanged && !statusChanged && !checkingChanged) {
                // console.debug(`跳过重复渲染 UUID: ${taskData.uuid}`);
                return;
            }
        }
        
        // 更新缓存数据
        State.setLastRenderTaskData({
            uuid: taskData.uuid,
            status: taskData.status,
            checking: taskData.checking,
            metadata: { tracks: taskData.metadata?.tracks || [] }
        });
    }

    let shouldAutoScroll = false;
    const scrollableArea = outputDiv.querySelector('.log-tracks-scrollable');
    if (scrollableArea) {
        const scrollThreshold = 30;
        shouldAutoScroll = scrollableArea.scrollHeight - scrollableArea.clientHeight <= scrollableArea.scrollTop + scrollThreshold;
    } else {
        shouldAutoScroll = true;
    }
    
    let headerDiv = outputDiv.querySelector('.log-header-fixed');
    let tracksScrollableDiv = outputDiv.querySelector('.log-tracks-scrollable');
    if (!headerDiv || !tracksScrollableDiv) {
        outputDiv.innerHTML = `
            <div class="log-header-fixed"></div>
            <div class="log-tracks-scrollable"></div>
        `;
        headerDiv = outputDiv.querySelector('.log-header-fixed');
        tracksScrollableDiv = outputDiv.querySelector('.log-tracks-scrollable');
        shouldAutoScroll = true;
    }
     if (!headerDiv || !tracksScrollableDiv) {
          console.error("无法创建或获取 Modal 内容区结构。");
          return;
     }

    // 只在首次渲染时更新header信息
    if (startFrom === 0) {
        let headerHtml = '';
        const metadata = taskData.metadata || {};
        const artist = metadata.artistName || '未知歌手';
        const album = metadata.name || '未知专辑/播放列表';
        const curatorName = metadata.curatorName || null;
        let overallStatusHtml = '';
        let overallStatusClass = `overall-status-custom`;
        
        // 优先判断校验状态
        let isChecking = !!taskData.checking;
        let statusText = '';
        let statusIcon = '';
        let statusBg = '';
        if (isChecking) {
            statusText = '任务校验中';
            statusIcon = '<i class="bi bi-hourglass-split me-2"></i>';
            statusBg = 'status-bg-blue';
        } else {
            switch (taskData.status) {
                case 'finish':
                    statusText = '任务成功完成';
                    statusIcon = '<i class="bi bi-check-circle-fill me-2"></i>';
                    statusBg = 'status-bg-success';
                    break;
                case 'error':
                    statusText = '任务失败';
                    statusIcon = '<i class="bi bi-x-octagon-fill me-2"></i>';
                    statusBg = 'status-bg-error';
                    break;
                case 'running':
                    statusText = '任务执行中';
                    statusIcon = '<i class="bi bi-hourglass-split me-2"></i>';
                    statusBg = 'status-bg-blue';
                    break;
                case 'ready':
                default:
                    statusText = '任务未执行';
                    statusIcon = '<i class="bi bi-pause-circle me-2"></i>';
                    statusBg = 'status-bg-gray';
                    break;
            }
        }
        overallStatusHtml = `<div class="${overallStatusClass} ${statusBg}"><span class="status-icon-text-align">${statusIcon}${statusText}</span></div>`;
        
        // 更新modal header
        const modal = document.getElementById('logModal');
        if (modal) {
            const modalHeader = modal.querySelector('.modal-header');
            if (modalHeader) {
                let statusContainer = modalHeader.querySelector('.status-container');
                let closeButton = modalHeader.querySelector('.close-button');

                if (!statusContainer || !closeButton) {
                    modalHeader.innerHTML = `
                        <div style="flex: 1; display: flex; align-items: center;">
                            <!-- SSE Status Element Removed -->
                        </div>
                        <div class="status-container" style="display:flex;justify-content:center;align-items:center;">
                            ${overallStatusHtml}
                        </div>
                        <div style="flex: 1; display: flex; justify-content: flex-end; align-items: center;">
                            <button type="button" class="close-button" data-bs-dismiss="modal" aria-label="Close">
                                <svg class="svg-icon" viewBox="0 0 384 512">
                                    <path d="M342.6 150.6c12.5-12.5 12.5-32.8 0-45.3s-32.8-12.5-45.3 0L192 210.7 86.6 105.4c-12.5-12.5-32.8-12.5-45.3 0s-12.5 32.8 0 45.3L146.7 256 41.4 361.4c-12.5 12.5-12.5 32.8 0 45.3s32.8 12.5 45.3 0L192 301.3 297.4 406.6c12.5 12.5 32.8 12.5 45.3 0s12.5-32.8 0-45.3L237.3 256 342.6 150.6z"/>
                                </svg>
                            </button>
                        </div>
                    `;
                } else {
                    const statusContainerInModal = modalHeader.querySelector('.status-container');
                    if (statusContainerInModal) {
                        statusContainerInModal.innerHTML = overallStatusHtml;
                    }
                }
            }
        }

        // 根据任务类型显示不同的信息
        const linkInfo = taskData.link_info || {};
        const isPlaylist = linkInfo.type === 'playlist';
        const isMV = linkInfo.type === 'music-video';

        if (isPlaylist) {
            if (curatorName) {
                headerHtml += `<p><strong>创建者：</strong> ${curatorName}</p>`;
            }
            headerHtml += `<p><strong>播放列表：</strong> ${album}</p>`;
        } else {
            headerHtml += `<p><strong>歌手：</strong> ${artist}</p>`;
            headerHtml += `<p><strong>标题：</strong> ${album}</p>`;
            if (metadata.width && metadata.height) {
                headerHtml += `<p><strong>分辨率：</strong> ${metadata.width}×${metadata.height}</p>`;
            }
        }
        
        if (isMV) {
            const user = State.linkUserCache.get(taskData.uuid) || taskData.user || '未知用户';
            headerHtml += `<p><strong>下载用户：</strong> ${user}</p>`;
        }
        
        if (headerDiv.innerHTML !== headerHtml) {
            headerDiv.innerHTML = headerHtml;
        }

        // 如果是MV类型，隐藏音轨列表
        if (isMV) {
            tracksScrollableDiv.innerHTML = '';
            tracksScrollableDiv.style.display = 'none';
            outputDiv.style.height = 'auto';
            const currentHeaderDiv = outputDiv.querySelector('.log-header-fixed');
            if (currentHeaderDiv) {
                currentHeaderDiv.style.borderBottom = 'none';
                currentHeaderDiv.style.marginBottom = '0';
            }
            return;
        } else {
            tracksScrollableDiv.style.display = 'block';
            const isMobile = window.matchMedia && window.matchMedia('(max-width: 600px)').matches;
            if (isMobile) {
                outputDiv.style.setProperty('height', 'auto', 'important');
                if (tracksScrollableDiv) {
                    tracksScrollableDiv.style.setProperty('height', 'auto', 'important');
                    tracksScrollableDiv.style.setProperty('overflow', 'visible', 'important');
                }
                let parent = outputDiv.parentElement;
                while (parent) {
                    if (
                        parent.classList.contains('modal-body') ||
                        parent.classList.contains('modal-content') ||
                        parent.classList.contains('modal-dialog') ||
                        parent.id === 'logModalContentColumn'
                    ) {
                        parent.style.setProperty('height', 'auto', 'important');
                        parent.style.setProperty('max-height', 'none', 'important');
                        parent.style.setProperty('overflow', 'visible', 'important');
                    }
                    parent = parent.parentElement;
                }
                const logModalContentCol = document.getElementById('logModalContentColumn');
                if (logModalContentCol) {
                    logModalContentCol.style.setProperty('height', 'auto', 'important');
                    logModalContentCol.style.setProperty('max-height', 'none', 'important');
                    logModalContentCol.style.setProperty('overflow', 'visible', 'important');
                }
            } else {
                outputDiv.style.height = '400px';
                if (tracksScrollableDiv) {
                    tracksScrollableDiv.style.height = '';
                    tracksScrollableDiv.style.overflow = '';
                }
                let parent = outputDiv.parentElement;
                while (parent) {
                    if (
                        parent.classList.contains('modal-body') ||
                        parent.classList.contains('modal-content') ||
                        parent.classList.contains('modal-dialog') ||
                        parent.id === 'logModalContentColumn'
                    ) {
                        parent.style.setProperty('height', '');
                        parent.style.setProperty('max-height', '');
                        parent.style.setProperty('overflow', '');
                    }
                    parent = parent.parentElement;
                }
                const logModalContentCol = document.getElementById('logModalContentColumn');
                if (logModalContentCol) {
                    logModalContentCol.style.setProperty('height', '');
                    logModalContentCol.style.setProperty('max-height', '');
                    logModalContentCol.style.setProperty('overflow', '');
                }
            }
        }
    }

    // 渲染音轨列表 - 支持分批渲染
    const tracks = taskData.metadata?.tracks || [];
    const totalTracks = tracks.length;
    
    // 更新渲染状态
    if (startFrom === 0) {
        State.setCurrentRenderingTaskUuid(taskData.uuid);
        State.setTotalTrackCount(totalTracks);
        State.setRenderedTrackCount(0);
    }

    // 确定实际渲染的音轨范围
    let tracksToRender = tracks;
    let actualBatchSize = totalTracks;
    
    if (fastRender && startFrom === 0) {
        // 快速渲染模式：只渲染前3个
        actualBatchSize = Math.min(3, totalTracks);
        tracksToRender = tracks.slice(0, actualBatchSize);
        State.setIsProgressiveRendering(totalTracks > 3);
        console.log(`[FastRender] 快速渲染前 ${actualBatchSize} 个音轨，总计 ${totalTracks} 个`);
    } else if (batchSize && startFrom > 0) {
        // 分批渲染模式：渲染指定范围
        const endIndex = Math.min(startFrom + batchSize, totalTracks);
        tracksToRender = tracks.slice(startFrom, endIndex);
        actualBatchSize = tracksToRender.length;
        console.log(`[BatchRender] 渲染音轨 ${startFrom}-${endIndex-1}，共 ${actualBatchSize} 个`);
    }

    if (Array.isArray(tracksToRender) && tracksToRender.length > 0) {
        // 排序音轨
        const sortedTracks = tracksToRender.slice().sort((a, b) => {
            const discA = a.disc_number || 1;
            const discB = b.disc_number || 1;
            const trackA = a.track_number || 0;
            const trackB = b.track_number || 0;
            const keyA = discA * 1000 + trackA;
            const keyB = discB * 1000 + trackB;
            return keyA - keyB;
        });

        // 计算每个光盘的轨道数（基于全部音轨）
        const discTrackCounts = new Map();
        tracks.forEach(track => {
            const discNum = track.disc_number || 1;
            if (!discTrackCounts.has(discNum)) {
                discTrackCounts.set(discNum, 0);
            }
            discTrackCounts.set(discNum, discTrackCounts.get(discNum) + 1);
        });

        // 获取现有的音轨元素
        const existingTrackElements = new Map();
        tracksScrollableDiv.querySelectorAll('.track-entry[data-track-id]').forEach(el => {
            existingTrackElements.set(el.dataset.trackId, el);
        });

        // 渲染音轨
        const processedTrackIds = new Set();
        sortedTracks.forEach((track, index) => {
            if (!track || typeof track !== 'object') { return; }
            const trackNumber = track.track_number;
            const discNumber = track.disc_number || 1;
            if (trackNumber === undefined || trackNumber === null) { return; }
            
            const trackId = `${discNumber}-${trackNumber}`;
            processedTrackIds.add(trackId);
            
            const trackName = track.name || '未知曲目';
            let connectionStatus = track.connection_status || 'waiting';
            let downloadStatus = track.download_status || 'waiting';
            let decryptionStatus = track.decryption_status || 'waiting';
            const hasLyrics = track.hasLyrics;
            const bitDepth = track.bit_depth;
            const sampleRate = track.sample_rate;
            const trackError = track.error_message;
            const downloadProgress = track.download_progress || null;
            
            let trackOverallStatus = 'waiting';
            if (downloadStatus === 'exists' || decryptionStatus === 'exists') trackOverallStatus = 'exists';
            else if (trackError || downloadStatus === 'failed' || decryptionStatus === 'failed' || connectionStatus === 'failed') trackOverallStatus = 'failed';
            else if (decryptionStatus === 'success' || track.check_success === true) trackOverallStatus = 'completed';
            else if (downloadStatus === 'success') trackOverallStatus = 'decrypting';
            else if (connectionStatus === 'success') {
                trackOverallStatus = 'downloading';
                downloadStatus = 'success';
                decryptionStatus = 'decrypting';
            }
            
            let trackEntryDiv = existingTrackElements.get(trackId);
            if (!trackEntryDiv) {
                trackEntryDiv = document.createElement('div');
                trackEntryDiv.classList.add('track-entry');
                trackEntryDiv.dataset.trackId = trackId;
                
                // 插入到正确位置
                const previousTrackId = `${discNumber}-${trackNumber-1}`;
                const previousElement = tracksScrollableDiv.querySelector(`.track-entry[data-track-id="${previousTrackId}"]`);
                if (previousElement && previousElement.nextSibling) {
                    tracksScrollableDiv.insertBefore(trackEntryDiv, previousElement.nextSibling);
                } else {
                    tracksScrollableDiv.appendChild(trackEntryDiv);
                }
                
                const rightStatusSpan = document.createElement('span');
                rightStatusSpan.className = 'right-status-text';
                rightStatusSpan.textContent = '';
                trackEntryDiv.appendChild(rightStatusSpan);
            } else {
                existingTrackElements.delete(trackId);
            }
            
            trackEntryDiv.className = `track-entry track-status-${trackOverallStatus}`;
            
            // 更新标题/编号
            const titleElement = trackEntryDiv.querySelector('.track-title');
            const discTotal = track.disc_total;
            const displayDiscTotal = discTotal || 1;
            const trackCountForDisc = discTrackCounts.get(discNumber) || tracks.length;
            const newTitleHtml = `<strong>[${displayDiscTotal <= 1 ? `${trackNumber}/${trackCountForDisc}` : `<span class=\"text-info\">${discNumber}/${displayDiscTotal}</span> - ${trackNumber}/${trackCountForDisc}`}] ${trackName}</strong>`;
            if (!titleElement) {
                const p = document.createElement('p');
                p.classList.add('track-title');
                p.innerHTML = newTitleHtml;
                trackEntryDiv.appendChild(p);
            } else if (titleElement.innerHTML !== newTitleHtml) {
                titleElement.innerHTML = newTitleHtml;
            }
            
            // 更新质量信息
            let qualityText;
            if (trackOverallStatus === 'exists') {
                qualityText = '已存在';
            } else if (trackOverallStatus === 'failed') {
                qualityText = '无法获取';
            } else if (bitDepth && sampleRate) {
                qualityText = `${bitDepth}bit / ${sampleRate}Hz`;
            } else if (trackOverallStatus === 'completed') { // 音轨已完成 (解密成功或校验成功) 但无质量信息
                qualityText = '信息缺失';
            } else { // 其他所有情况，如等待、下载中、解密中
                qualityText = '等待中...';
            }
            const qualityElement = trackEntryDiv.querySelector('.track-quality');
            const newQualityText = `<span><strong>质量：</strong> ${qualityText}</span><span class='track-lyrics-indicator' style='float:right; font-size:inherit;'></span>`;
            if (!qualityElement) {
                const p = document.createElement('p');
                p.classList.add('small', 'track-quality');
                p.innerHTML = newQualityText;
                trackEntryDiv.appendChild(p);
            } else if (qualityElement.innerHTML !== newQualityText) {
                qualityElement.innerHTML = newQualityText;
            }
            
            // 更新歌词指示
            const lyricsIndicator = trackEntryDiv.querySelector('.track-lyrics-indicator');
            let lyricsHtml = '';
            if (hasLyrics === true) lyricsHtml = '<i class="bi bi-music-note-list me-1"></i> 有歌词';
            else if (hasLyrics === false) lyricsHtml = '<i class="bi bi-music-note-list me-1" style="opacity: 0.5;"></i> 无歌词';
            if (lyricsIndicator) {
                lyricsIndicator.innerHTML = lyricsHtml;
                lyricsIndicator.style.fontSize = 'inherit';
                lyricsIndicator.style.color = '#222';
                lyricsIndicator.style.float = 'right';
            }
            
            const lyricsElement = trackEntryDiv.querySelector('.track-lyrics');
            if (lyricsElement) lyricsElement.remove();
            
            // 更新进度条
            const songId = track.song_id || '';
            let progressContainer = trackEntryDiv.querySelector('.track-progress-container');
            let progressBarClass = 'bg-light';
            let percent = 0;
            let progressText = '';
            
            if (downloadProgress && typeof downloadProgress.percent === 'number') {
                percent = downloadProgress.percent;
            }
            if (downloadProgress && downloadProgress.current && downloadProgress.total) {
                const current = (downloadProgress.current / (1024 * 1024)).toFixed(2);
                const total = (downloadProgress.total / (1024 * 1024)).toFixed(2);
                progressText = `${current}MB / ${total}MB (${percent.toFixed(1)}%)`;
            } else {
                progressText = '计算中...';
            }
            
            if (trackOverallStatus === 'downloading' || trackOverallStatus === 'decrypting') {
                progressBarClass = 'bg-primary';
            } else if (trackOverallStatus === 'completed' || trackOverallStatus === 'exists') {
                progressBarClass = 'bg-success';
                percent = 100;
            }
            
            if (!progressContainer) {
                const containerDiv = document.createElement('div');
                containerDiv.classList.add('track-progress-container');
                // 直接创建时就隐藏进度文本，避免闪烁
                containerDiv.innerHTML = `
                    <div class=\"progress mt-1 mb-2\" style=\"height: 15px; border:1px solid #bbb; border-radius:6px;\">\n                        <div class=\"progress-bar ${progressBarClass}\" role=\"progressbar\" \n                             style=\"width: ${percent}%;${progressBarClass==='bg-light'?'background-color:#e0e0e0;':''}\" aria-valuenow=\"${percent}\" \n                             aria-valuemin=\"0\" aria-valuemax=\"100\">\n                        </div>\n                    </div>\n                    <p class=\"small text-muted mb-2 track-progress-text\" style=\"display: none;\">${progressText}</p>\n                `;
                const qualityP = trackEntryDiv.querySelector('.track-quality');
                if (qualityP && qualityP.nextSibling) {
                    trackEntryDiv.insertBefore(containerDiv, qualityP.nextSibling);
                } else {
                    trackEntryDiv.appendChild(containerDiv);
                }
            } else {
                const progressBar = progressContainer.querySelector('.progress-bar');
                const progressTextElement = progressContainer.querySelector('.track-progress-text');
                if (progressBar) {
                    progressBar.style.width = `${percent}%`;
                    progressBar.setAttribute('aria-valuenow', percent);
                    progressBar.className = `progress-bar ${progressBarClass}`;
                    if(progressBarClass==='bg-light'){
                        progressBar.style.backgroundColor = '#e0e0e0';
                    }else{
                        progressBar.style.backgroundColor = '';
                    }
                }
                // 确保进度文本始终隐藏，避免闪烁
                if (progressTextElement) {
                    progressTextElement.textContent = progressText;
                    progressTextElement.style.display = 'none';
                }
            }

            // 更新状态图标
            let statusElement = trackEntryDiv.querySelector('.track-status-icons');
            if (!statusElement) {
                statusElement = document.createElement('p');
                statusElement.classList.add('small', 'track-status-icons');
                const refElement = trackEntryDiv.querySelector('.track-progress-container') || trackEntryDiv.querySelector('.track-quality');
                if(refElement && refElement.nextSibling) {
                    trackEntryDiv.insertBefore(statusElement, refElement.nextSibling);
                } else {
                    trackEntryDiv.appendChild(statusElement);
                }
            }
            
            // 更新图标函数
            const updateIcon = (selector, status, successClass, failedClass, waitingClass, spinnerClass = null, titlePrefix = '') => {
                let iconSpan = statusElement.querySelector(selector);
                if (!iconSpan) {
                    console.warn(`Icon span not found: ${selector}`);
                    return; 
                }
                let newClass = waitingClass;
                let newTitle = `等待${titlePrefix}`;
                let needsSpinner = false;

                if (status === 'success') { newClass = successClass; newTitle = `${titlePrefix}成功`; }
                else if (status === 'failed') { newClass = failedClass; newTitle = `${titlePrefix}失败`; }
                else if (status === 'exists') { newClass = 'text-info status-icon'; newTitle = `本地存在`; }
                else if (status === 'decrypting' && selector === '.icon-decrypt') {
                    iconSpan.className = spinnerClass || 'text-warning status-icon';
                    iconSpan.title = `${titlePrefix}中`;
                    iconSpan.textContent = '';
                    return;
                }
                
                const finalClass = needsSpinner ? `${newClass} ${selector.substring(1)}-spinner-active` : newClass;
                if (iconSpan.className !== finalClass) {
                    iconSpan.className = finalClass;
                }
                if (iconSpan.title !== newTitle) iconSpan.title = newTitle;

                const existingSpinner = iconSpan.querySelector('.spinner-border');
                const staticIconMap = {
                    success: '✅',
                    failed: '❌',
                    exists: '⚠️',
                    waiting: '🔄'
                };
                const expectedStaticIcon = staticIconMap[status] || '🔄'; 

                if (needsSpinner) {
                    if (!existingSpinner) {
                        iconSpan.textContent = '';
                        const spinnerElement = document.createElement('span');
                        spinnerElement.className = 'spinner-border spinner-border-sm';
                        spinnerElement.setAttribute('role', 'status');
                        spinnerElement.setAttribute('aria-hidden', 'true');
                        iconSpan.appendChild(spinnerElement);
                    }
                } else {
                    if (existingSpinner) {
                        iconSpan.removeChild(existingSpinner);
                    }
                    if (iconSpan.textContent !== expectedStaticIcon) {
                         iconSpan.textContent = expectedStaticIcon;
                    }
                }
            };
            
            if (!statusElement.querySelector('.icon-connect')) {
                statusElement.innerHTML = '';
                statusElement.appendChild(document.createTextNode('连接'));
                const connectSpan = document.createElement('span'); connectSpan.classList.add('icon-connect'); statusElement.appendChild(connectSpan);
                statusElement.appendChild(document.createTextNode('  任务进度'));
                const decryptSpan = document.createElement('span'); decryptSpan.classList.add('icon-decrypt'); statusElement.appendChild(decryptSpan);
            }

            updateIcon('.icon-connect', connectionStatus, 'text-success status-icon', 'text-danger status-icon', 'text-secondary status-icon', null, '连接');
            updateIcon('.icon-decrypt', 
                       trackOverallStatus === 'decrypting' ? 'decrypting' : decryptionStatus, 
                       'text-success status-icon', 'text-danger status-icon', 'text-secondary status-icon', 
                       'text-warning status-icon', '解密');

            // 更新错误信息
            const errorElement = trackEntryDiv.querySelector('.track-error');
            if (trackError) {
                const errorHtml = `<strong>错误：</strong> ${trackError}`;
                if (!errorElement) {
                    const p = document.createElement('p');
                    p.classList.add('text-danger', 'small', 'track-error');
                    p.innerHTML = errorHtml;
                    trackEntryDiv.appendChild(p);
                } else if (errorElement.innerHTML !== errorHtml) {
                    errorElement.innerHTML = errorHtml;
                }
            } else if (errorElement) {
                trackEntryDiv.removeChild(errorElement);
            }
            
            if (songId) {
                trackEntryDiv.dataset.songId = songId;
            }
            
            // 更新状态显示
            const statusElement2 = trackEntryDiv.querySelector('.track-status-icons');
            if (statusElement2) {
                statusElement2.style.display = 'flex';
                statusElement2.style.alignItems = 'center';
                statusElement2.style.justifyContent = 'flex-start';
                statusElement2.querySelectorAll('.decrypt-percent, .decrypt-size').forEach(e => e.remove());
                
                if (
                    trackOverallStatus === 'downloading' ||
                    trackOverallStatus === 'decrypting' ||
                    trackOverallStatus === 'completed' ||
                    trackOverallStatus === 'exists'
                ) {
                    let percentText = `${percent.toFixed(1)}%`;
                    const percentSpan = document.createElement('span');
                    percentSpan.className = 'decrypt-percent';
                    percentSpan.style.marginLeft = '6px';
                    percentSpan.style.fontWeight = 'bold';
                    if (trackOverallStatus === 'completed' || trackOverallStatus === 'exists') {
                        percentSpan.style.color = '#198754';
                    } else {
                        percentSpan.style.color = '#007bff';
                    }
                    percentSpan.textContent = percentText;
                    
                    const nodes = Array.from(statusElement2.childNodes);
                    let insertAfter = null;
                    for (let n of nodes) {
                        if (n.nodeType === 3 && n.textContent.includes('任务进度')) {
                            insertAfter = n;
                            break;
                        }
                    }
                    if (insertAfter) {
                        statusElement2.insertBefore(percentSpan, insertAfter.nextSibling);
                    } else {
                        statusElement2.appendChild(percentSpan);
                    }
                }
                
                if (downloadProgress && downloadProgress.current && downloadProgress.total) {
                    const current = (downloadProgress.current / (1024 * 1024)).toFixed(2);
                    const total = (downloadProgress.total / (1024 * 1024)).toFixed(2);
                    const sizeSpan = document.createElement('span');
                    sizeSpan.className = 'decrypt-size';
                    sizeSpan.style.fontWeight = 'bold';
                    if (trackOverallStatus === 'completed' || trackOverallStatus === 'exists') {
                        sizeSpan.style.color = '#198754';
                    } else {
                        sizeSpan.style.color = '#007bff';
                    }
                    sizeSpan.textContent = `${current}MB / ${total}MB`;
                    sizeSpan.style.marginLeft = 'auto';
                    statusElement2.appendChild(sizeSpan);
                } else {
                    const sizeSpan = document.createElement('span');
                    sizeSpan.className = 'decrypt-size';
                    sizeSpan.style.fontWeight = 'bold';
                    sizeSpan.style.color = '#6c757d';
                    sizeSpan.textContent = '等待中...';
                    sizeSpan.style.marginLeft = 'auto';
                    statusElement2.appendChild(sizeSpan);
                }
                
                if (!(trackOverallStatus === 'decrypting' || trackOverallStatus === 'completed' || trackOverallStatus === 'exists')) {
                    const decryptIcon = statusElement2.querySelector('.icon-decrypt');
                    if (decryptIcon) decryptIcon.textContent = '';
                }
            }

            // 右下角状态文本
            const decryptSizeSpan = trackEntryDiv.querySelector('.decrypt-size');
            if (decryptSizeSpan) {
                // 为所有状态设置统一的基础样式
                decryptSizeSpan.style.lineHeight = '1.2';
                decryptSizeSpan.style.height = 'auto';
                decryptSizeSpan.style.minHeight = '1.2em';
                decryptSizeSpan.style.display = 'block';
                
                if (trackOverallStatus === 'exists') {
                    decryptSizeSpan.textContent = '已存在';
                    decryptSizeSpan.style.color = '#1769aa';
                } else if (trackOverallStatus === 'failed') {
                    decryptSizeSpan.textContent = '失败';
                    decryptSizeSpan.style.color = '#721c24';
                } else if (track.check_success === true) {
                    decryptSizeSpan.innerHTML = '校验成功<i class="bi bi-shield-check ms-1" style="vertical-align: -0.1em; font-size: 0.9em; line-height: 1; display: inline-block;"></i>';
                    decryptSizeSpan.style.color = '#198754';
                }
            }
        });

        // 更新渲染计数
        const newRenderedCount = State.getRenderedTrackCount() + actualBatchSize;
        State.setRenderedTrackCount(newRenderedCount);
        
        // 如果是快速渲染且还有剩余音轨，安排后续渲染
        if (fastRender && State.getIsProgressiveRendering()) {
            console.log(`[FastRender] 已渲染 ${newRenderedCount}/${totalTracks} 个音轨，准备异步渲染剩余音轨`);
            
            // 显示加载提示
            if (newRenderedCount < totalTracks) {
                const loadingDiv = document.createElement('div');
                loadingDiv.id = 'track-loading-indicator';
                loadingDiv.className = 'text-center p-3 text-muted';
                loadingDiv.innerHTML = `<div class="spinner-border spinner-border-sm me-2" role="status"></div>正在加载剩余 ${totalTracks - newRenderedCount} 个音轨...`;
                tracksScrollableDiv.appendChild(loadingDiv);
            }
        }
        
    } else if (taskData.status === 'running' || taskData.status === 'ready') {
        if (startFrom === 0) {
            tracksScrollableDiv.innerHTML = '<p class="text-muted text-center p-3">正在加载音轨列表...</p>';
        }
    } else {
        if (startFrom === 0) {
            tracksScrollableDiv.innerHTML = '<p class="text-muted text-center p-3">未找到音轨信息。</p>';
        }
    }

    // 自动滚动
    if (shouldAutoScroll && tracksScrollableDiv) {
        tracksScrollableDiv.scrollTop = tracksScrollableDiv.scrollHeight;
    }

    // MV类型隐藏用户信息
    const linkInfo = taskData.link_info || {};
    const isMV = linkInfo.type === 'music-video';
    if (isMV) {
        const modalUserEls = document.querySelectorAll('#logModalUser');
        modalUserEls.forEach(el => { el.style.display = 'none'; });
    }
}

// 新增：继续渲染剩余音轨的函数
export function continueRenderingTracks(taskData) {
    if (!State.getIsProgressiveRendering() || !State.isLogModalActive) {
        return;
    }

    const renderedCount = State.getRenderedTrackCount();
    const totalCount = State.getTotalTrackCount();
    const remainingCount = totalCount - renderedCount;
    
    if (remainingCount <= 0) {
        State.setIsProgressiveRendering(false);
        const loadingIndicator = document.getElementById('track-loading-indicator');
        if (loadingIndicator) {
            loadingIndicator.remove();
        }
        return;
    }

    console.log(`[ContinueRender] 继续渲染剩余音轨，已渲染: ${renderedCount}/${totalCount}`);
    
    // 分批渲染剩余音轨，每批5个
    const batchSize = Math.min(5, remainingCount);
    renderModalContentFromTaskData(taskData, {
        fastRender: false,
        startFrom: renderedCount,
        batchSize: batchSize
    });
    
    // 更新加载提示
    const newRenderedCount = renderedCount + batchSize;
    const stillRemaining = totalCount - newRenderedCount;
    
    if (stillRemaining > 0) {
        const loadingIndicator = document.getElementById('track-loading-indicator');
        if (loadingIndicator) {
            loadingIndicator.innerHTML = `<div class="spinner-border spinner-border-sm me-2" role="status"></div>正在加载剩余 ${stillRemaining} 个音轨...`;
        }
        
        // 继续渲染下一批，间隔100ms
        setTimeout(() => {
            if (State.isLogModalActive && State.getIsProgressiveRendering()) {
                continueRenderingTracks(taskData);
            }
        }, 100);
    } else {
        // 全部渲染完成
        State.setIsProgressiveRendering(false);
        const loadingIndicator = document.getElementById('track-loading-indicator');
        if (loadingIndicator) {
            loadingIndicator.remove();
        }
        console.log(`[ContinueRender] 音轨渲染完成，总计 ${totalCount} 个`);
    }
}

// 新增：清理函数，用于释放内存资源
export function cleanupModalResources() {
    // 重置渲染状态
    State.resetRenderingState();
    
    // 清理加载指示器
    const loadingIndicator = document.getElementById('track-loading-indicator');
    if (loadingIndicator) {
        loadingIndicator.remove();
    }
    
    // 清理封面图片缓存
    const modalCoverEl = document.querySelector('#logModalCover');
    if (modalCoverEl) {
        modalCoverEl.src = '';
        modalCoverEl.alt = '';
    }

    // 清理日志输出区域
    const outputDiv = document.getElementById('formattedLogOutput');
    if (outputDiv) {
        // 移除所有子元素的事件监听器
        const removeEventListeners = (element) => {
            const clone = element.cloneNode(true);
            element.parentNode.replaceChild(clone, element);
        };

        // 递归清理所有子元素
        const cleanupElement = (element) => {
            if (element.children) {
                Array.from(element.children).forEach(child => {
                    cleanupElement(child);
                    removeEventListeners(child);
                });
            }
        };

        cleanupElement(outputDiv);
        outputDiv.innerHTML = '';
    }

    // 清理状态图标
    const statusElements = document.querySelectorAll('.track-status-icons');
    statusElements.forEach(el => {
        const spinner = el.querySelector('.spinner-border');
        if (spinner) {
            spinner.remove();
        }
    });

    // 清理进度条
    const progressBars = document.querySelectorAll('.progress-bar');
    progressBars.forEach(bar => {
        bar.style.width = '0%';
        bar.setAttribute('aria-valuenow', '0');
    });

    // 清理错误信息
    const errorElements = document.querySelectorAll('.track-error');
    errorElements.forEach(el => el.remove());

    // 清理Modal body
    const modalBody = document.querySelector('#logModal .modal-body');
    if (modalBody) {
        modalBody.innerHTML = ''; // 清空内容，不再显示加载图标
    }

    // 清理Modal header
    const modalHeader = document.querySelector('#logModal .modal-header');
    if (modalHeader) {
        const oldBar = modalHeader.querySelector('.overall-status-custom');
        if (oldBar) {
            oldBar.remove();
        }
    }

    // 强制垃圾回收
    if (window.gc) {
        try {
            window.gc();
        } catch (e) {
            console.debug('手动GC不可用');
        }
    }
}

// 动态插入自定义样式
(function addCustomStatusStyles() {
    // 移除可能存在的旧样式
    const oldStyle = document.getElementById('custom-status-style');
    if (oldStyle) {
        oldStyle.remove();
    }

    // 创建新样式元素
    const style = document.createElement('style');
    style.id = 'custom-status-style';
    style.type = 'text/css';
    
    // 添加样式内容
    const styleContent = `
        /* 只隐藏modal标题，不隐藏关闭按钮 */
        #logModal .modal-title { display: none !important; }
        
        /* 状态条样式 - 提高优先级 */
        #logModal .modal-header .overall-status-custom {
            display: flex !important;
            align-items: center !important;
            justify-content: center !important;
            margin: 2px auto 2px auto !important;
            border-radius: 18px !important;
            font-size: 1.1rem !important;
            font-weight: bold !important;
            width: fit-content !important;
            min-width: 260px !important;
            padding: 2px 32px !important;
            box-shadow: 0 2px 8px rgba(0,0,0,0.04) !important;
            border: 1px solid rgba(0,0,0,0.1) !important;
            transition: all 0.2s ease-in-out !important;
        }
        #logModal .modal-header .overall-status-custom.status-bg-success { 
            background: #d4edda !important; 
            color: #155724 !important; 
            border-color: #c3e6cb !important;
        }
        #logModal .modal-header .overall-status-custom.status-bg-error { 
            background: #f8d7da !important; 
            color: #721c24 !important; 
            border-color: #f5c6cb !important;
        }
        #logModal .modal-header .overall-status-custom.status-bg-blue { 
            background: #e3f0ff !important; 
            color: #1769aa !important; 
            border-color: #b8daff !important;
        }
        #logModal .modal-header .overall-status-custom.status-bg-gray { 
            background: #f1f1f1 !important; 
            color: #888 !important; 
            border-color: #ddd !important;
        }
        #logModal .modal-header .status-icon-text-align { 
            display: flex !important; 
            align-items: center !important; 
            gap: 6px !important; 
        }
        #logModal .modal-header .overall-status-custom i { 
            font-size: 1.3em !important; 
            margin-right: 8px !important; 
            vertical-align: middle !important; 
        }
    `;

    // 使用textContent而不是innerHTML来设置样式内容
    style.textContent = styleContent;

    // 确保样式被添加到head的最前面
    const head = document.head || document.getElementsByTagName('head')[0];
    if (head.firstChild) {
        head.insertBefore(style, head.firstChild);
    } else {
        head.appendChild(style);
    }

    // 验证样式是否成功添加
    console.debug('Custom status styles added:', document.getElementById('custom-status-style') !== null);
})();