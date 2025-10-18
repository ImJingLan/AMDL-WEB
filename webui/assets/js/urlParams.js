// urlParams.js - URL参数处理模块
// 处理日志监视窗口的URL参数控制

/**
 * 获取URL参数的值
 * @param {string} paramName - 参数名称
 * @returns {string|null} - 参数值，如果不存在则返回null
 */
export function getUrlParam(paramName) {
    const urlParams = new URLSearchParams(window.location.search);
    return urlParams.get(paramName);
}

/**
 * 设置URL参数（不刷新页面）
 * @param {string} paramName - 参数名称
 * @param {string} paramValue - 参数值
 */
export function setUrlParam(paramName, paramValue) {
    const url = new URL(window.location);
    url.searchParams.set(paramName, paramValue);
    window.history.replaceState({}, '', url);
}

/**
 * 移除URL参数（不刷新页面）
 * @param {string} paramName - 参数名称
 */
export function removeUrlParam(paramName) {
    const url = new URL(window.location);
    url.searchParams.delete(paramName);
    window.history.replaceState({}, '', url);
}

/**
 * 检查是否应该自动打开日志监视窗口
 * @returns {boolean} - 如果URL参数指示需要打开监视窗口则返回true
 */
export function shouldAutoOpenLogModal() {
    const monitor = getUrlParam('monitor');
    const uuid = getUrlParam('uuid');
    
    // 如果monitor参数为'true'或'1'，或者指定了uuid参数，则自动打开
    return monitor === 'true' || monitor === '1' || uuid !== null;
}

/**
 * 获取要监视的任务UUID
 * @returns {string|null} - 要监视的UUID，如果没有指定则返回null
 */
export function getMonitorUuid() {
    return getUrlParam('uuid');
}

/**
 * 设置监视状态到URL
 * @param {boolean} isOpen - 监视窗口是否打开
 * @param {string|null} uuid - 要监视的任务UUID，可选
 */
export function setMonitorStatus(isOpen, uuid = null) {
    if (isOpen) {
        setUrlParam('monitor', 'true');
        if (uuid) {
            setUrlParam('uuid', uuid);
        }
    } else {
        removeUrlParam('monitor');
        removeUrlParam('uuid');
    }
}

/**
 * 清除所有监视相关的URL参数
 */
export function clearMonitorParams() {
    removeUrlParam('monitor');
    removeUrlParam('uuid');
}

/**
 * 验证当前URL参数中的UUID是否仍然有效
 * @param {Map} taskMap - 当前的任务数据Map
 * @returns {boolean} - 如果URL参数有效则返回true，如果已清除无效参数则返回false
 */
export function validateAndCleanUrlParams(taskMap) {
    const currentUuid = getUrlParam('uuid');
    const currentMonitor = getUrlParam('monitor');
    
    // 如果没有监视相关的参数，直接返回true
    if (!currentMonitor && !currentUuid) {
        return true;
    }
    
    // 如果只有monitor参数没有uuid，认为是有效的（显示通用监视窗口）
    if (currentMonitor && !currentUuid) {
        return true;
    }
    
    // 如果有uuid参数，检查对应的任务是否存在
    if (currentUuid) {
        const taskExists = taskMap && taskMap.has(currentUuid);
        if (!taskExists) {
            console.log(`URL参数中的UUID ${currentUuid} 对应的任务不存在，尝试清除参数`);
            
            // 🛡️ 使用安全清除函数
            const wasCleared = safelyCleanUrlParams('任务UUID不存在');
            
            // 如果因为页面状态保护而未清除，创建sessionStorage备份
            if (!wasCleared) {
                try {
                    sessionStorage.setItem('amdl_pending_url_params', JSON.stringify({
                        monitor: currentMonitor,
                        uuid: currentUuid,
                        timestamp: Date.now()
                    }));
                    console.log('[URLParams] 🛡️ 已创建参数备份到sessionStorage');
                } catch (e) {
                    console.warn('[URLParams] 无法创建sessionStorage备份:', e);
                }
            }
            
            return !wasCleared; // 如果未清除，返回true（保持参数）
        }
    }
    
    return true;
}

/**
 * 检查并清理过期的URL参数（在轮询时调用）
 * @param {Map} taskMap - 当前的任务数据Map
 */
export function checkAndCleanExpiredParams(taskMap) {
    validateAndCleanUrlParams(taskMap);
}

// 🛡️ 新增：页面状态检查（与main.js同步）
function isPageUnloadingOrHidden() {
    // 检查全局变量（如果存在）
    if (typeof window !== 'undefined') {
        // 检查页面可见性
        if (document.hidden) {
            return true;
        }
        
        // 检查是否正在卸载（通过性能API）
        if (window.performance && window.performance.navigation) {
            const navType = window.performance.navigation.type;
            if (navType === 1) { // TYPE_RELOAD
                return true;
            }
        }
        
        // 检查全局状态变量（如果main.js已设置）
        if (window.amdlPageState && window.amdlPageState.isUnloading) {
            return true;
        }
    }
    
    return false;
}

// 🛡️ 安全的URL参数清除函数
function safelyCleanUrlParams(reason = 'URL验证') {
    if (isPageUnloadingOrHidden()) {
        console.log(`[URLParams] 🛡️ 跳过URL参数清除，原因：页面状态保护 (${reason})`);
        return false; // 返回false表示未清除
    }
    
    console.log(`[URLParams] 安全清除URL参数，原因：${reason}`);
    clearMonitorParams();
    return true; // 返回true表示已清除
} 