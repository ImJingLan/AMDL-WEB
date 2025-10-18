// cacheDebug.js - 图片缓存调试工具
import * as State from './state.js';

// 创建缓存状态监控函数
export function logCacheStatus() {
    console.group('[CacheDebug] 缓存状态报告');
    console.log(`缓存大小: ${State.imageCache.size}`);
    console.log(`缓存键列表:`, Array.from(State.imageCache.keys()));
    
    // 详细显示每个缓存项
    State.imageCache.forEach((image, uuid) => {
        console.log(`UUID: ${uuid}`);
        console.log(`  - URL: ${image.src}`);
        console.log(`  - 尺寸: ${image.naturalWidth}x${image.naturalHeight}`);
        console.log(`  - 加载状态: ${image.complete ? '已完成' : '加载中'}`);
    });
    console.groupEnd();
}

// 检查特定UUID的缓存状态
export function checkCacheForUuid(uuid) {
    console.group(`[CacheDebug] 检查UUID: ${uuid}`);
    
    const hasCached = State.imageCache.has(uuid);
    console.log(`缓存中存在: ${hasCached}`);
    
    if (hasCached) {
        const cachedImage = State.imageCache.get(uuid);
        console.log(`URL: ${cachedImage.src}`);
        console.log(`尺寸: ${cachedImage.naturalWidth}x${cachedImage.naturalHeight}`);
        console.log(`完成状态: ${cachedImage.complete}`);
    }
    
    console.groupEnd();
    return hasCached;
}

// 清除缓存
export function clearImageCache() {
    const oldSize = State.imageCache.size;
    State.imageCache.clear();
    console.log(`[CacheDebug] 缓存已清除，清除前大小: ${oldSize}, 清除后大小: ${State.imageCache.size}`);
}

// 统计缓存中的图片类型
export function analyzeCacheTypes() {
    console.group('[CacheDebug] 缓存类型分析');
    
    let mvCount = 0;
    let musicCount = 0;
    let unknownCount = 0;
    
    State.imageCache.forEach((image, uuid) => {
        if (image.src.includes('1920x1080')) {
            mvCount++;
        } else if (image.src.includes('600x600') || image.src.includes('1200x1200')) {
            musicCount++;
        } else {
            unknownCount++;
        }
    });
    
    console.log(`MV类型图片 (1920x1080): ${mvCount}`);
    console.log(`音乐类型图片 (600x600/1200x1200): ${musicCount}`);
    console.log(`未知类型图片: ${unknownCount}`);
    console.log(`总计: ${State.imageCache.size}`);
    
    console.groupEnd();
}

// 测试图片加载
export function testImageLoad(url) {
    console.log(`[CacheDebug] 测试图片加载: ${url}`);
    
    const testImage = new Image();
    
    testImage.onload = () => {
        console.log(`[CacheDebug] 图片加载成功`);
        console.log(`  - 尺寸: ${testImage.naturalWidth}x${testImage.naturalHeight}`);
        console.log(`  - URL: ${testImage.src}`);
    };
    
    testImage.onerror = (e) => {
        console.error(`[CacheDebug] 图片加载失败`, e);
    };
    
    testImage.src = url;
}

// 自动监控模式
let monitorInterval = null;

export function startMonitoring(interval = 5000) {
    if (monitorInterval) {
        console.log('[CacheDebug] 监控已在运行中');
        return;
    }
    
    console.log(`[CacheDebug] 开始监控缓存状态，间隔: ${interval}ms`);
    
    monitorInterval = setInterval(() => {
        console.log(`[CacheDebug] 自动监控 - 缓存大小: ${State.imageCache.size}`);
        
        if (State.imageCache.size > 0) {
            analyzeCacheTypes();
        }
    }, interval);
}

export function stopMonitoring() {
    if (monitorInterval) {
        clearInterval(monitorInterval);
        monitorInterval = null;
        console.log('[CacheDebug] 监控已停止');
    } else {
        console.log('[CacheDebug] 监控未在运行');
    }
}

// 导出到全局，方便在控制台使用
if (typeof window !== 'undefined') {
    window.cacheDebug = {
        logStatus: logCacheStatus,
        checkUuid: checkCacheForUuid,
        clear: clearImageCache,
        analyze: analyzeCacheTypes,
        testLoad: testImageLoad,
        startMonitor: startMonitoring,
        stopMonitor: stopMonitoring
    };
    
    console.log('[CacheDebug] 调试工具已加载，可在控制台使用 window.cacheDebug');
} 