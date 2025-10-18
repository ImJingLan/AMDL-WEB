// 全局状态变量
export let user_name = "";
export let currentRunningTaskUuid = null; // 当前标记为运行中的任务UUID (轮询更新，可能非实时)
export let isLogModalActive = false; // Modal是否可见
export let focusedTaskUuid = null; // 用户当前点击封面意图关注的任务UUID
export let skipCheck = false; // 强制覆盖状态
export let isPollingPaused = false;
export const POLLING_INTERVAL = 1000; // 轮询间隔 (ms)
export let taskPollingIntervalId = null; // 轮询定时器 ID
export let lastTaskCompletionTime = null; // 最后一次任务完成的时间
export let hadRunningTasks = false; // 记录是否曾经有运行中的任务

// 渲染状态管理
export let currentRenderingTaskUuid = null; // 当前正在渲染的任务UUID
export let isProgressiveRendering = false; // 是否正在进行分批渲染
export let renderedTrackCount = 0; // 已渲染的音轨数量
export let totalTrackCount = 0; // 总音轨数量
export let lastRenderTaskData = null; // 上次渲染的任务数据，用于防止重复渲染

// 存储最新任务数据的 Map (key: uuid, value: task object)
export let latestTaskMap = new Map();

// 用户链接/UUID 缓存 (key: link or uuid, value: user name) - 可用于快速查找用户名
export const linkUserCache = new Map();

// 图片缓存 (key: uuid, value: Image对象)
export const imageCache = new Map();

// 图片搜索相关状态
export let recognitionResults = []; // 图片识别结果
export let searchState = null; // 搜索状态 {originalQuery, searchResults, currentOffset}

// Modal 实例
export let succeedModalInstance = null;
export let failedModalInstance = null;
export let logModalInstance = null;
export let imageSearchModalInstance = null;
export let recognitionResultModalInstance = null;

// DOM 元素引用
export let domElements = {};

// Setter 函数
export function setUserName(name) { user_name = name; }
export function setCurrentRunningTaskUuid(uuid) { currentRunningTaskUuid = uuid; } // 可选，如果仍需标记运行状态
export function setIsLogModalActive(isActive) { isLogModalActive = isActive; }
export function setFocusedTaskUuid(uuid) { focusedTaskUuid = uuid; }
export function setLatestTaskMap(taskMap) { latestTaskMap = taskMap; }
export function setTaskPollingIntervalId(intervalId) { taskPollingIntervalId = intervalId; }
export function setSkipCheck(value) { skipCheck = value; }
export function setPollingPaused(value) {
    isPollingPaused = value;
}
export function setLastTaskCompletionTime(time) { lastTaskCompletionTime = time; }
export function setHadRunningTasks(value) { hadRunningTasks = value; }

// 渲染状态管理函数
export function setCurrentRenderingTaskUuid(uuid) { currentRenderingTaskUuid = uuid; }
export function setIsProgressiveRendering(isRendering) { isProgressiveRendering = isRendering; }
export function setRenderedTrackCount(count) { renderedTrackCount = count; }
export function setTotalTrackCount(count) { totalTrackCount = count; }
export function setLastRenderTaskData(data) { lastRenderTaskData = data; }

export function setModalInstances(instances) {
    succeedModalInstance = instances.succeedModalInstance;
    failedModalInstance = instances.failedModalInstance;
    logModalInstance = instances.logModalInstance;
    imageSearchModalInstance = instances.imageSearchModalInstance;
    recognitionResultModalInstance = instances.recognitionResultModalInstance;
}

export function getModalInstances() {
    return {
        succeedModalInstance,
        failedModalInstance,
        logModalInstance,
        imageSearchModalInstance,
        recognitionResultModalInstance
    };
}

export function setDomElements(elements) {
    domElements = elements;
}

export function getDomElements() {
    return domElements;
}

// 获取强制覆盖状态
export function getSkipCheck() {
    return skipCheck;
}

// 获取轮询暂停状态
export function getPollingPaused() {
    return isPollingPaused;
}

// 获取是否刚完成任务（10秒内）
export function isRecentlyCompleted() {
    if (!lastTaskCompletionTime) return false;
    const now = Date.now();
    return (now - lastTaskCompletionTime) < 10000; // 10秒内，从5秒改为10秒
}

// 获取渲染状态
export function getCurrentRenderingTaskUuid() { return currentRenderingTaskUuid; }
export function getIsProgressiveRendering() { return isProgressiveRendering; }
export function getRenderedTrackCount() { return renderedTrackCount; }
export function getTotalTrackCount() { return totalTrackCount; }
export function getLastRenderTaskData() { return lastRenderTaskData; }

// 重置渲染状态
export function resetRenderingState() {
    currentRenderingTaskUuid = null;
    isProgressiveRendering = false;
    renderedTrackCount = 0;
    totalTrackCount = 0;
    lastRenderTaskData = null;
}

// Getter 函数
export function getUserName() { return user_name; }

// 图片搜索相关的getter和setter函数
export function setRecognitionResults(results) { recognitionResults = results; }
export function getRecognitionResults() { return recognitionResults; }

export function setSearchState(state) { searchState = state; }
export function getSearchState() { return searchState; }

// 获取用户UUID (从用户名生成或其他方式)
export function getUserUUID() {
    // 这里可以根据实际需求实现UUID获取逻辑
    // 暂时返回用户名作为标识
    return user_name || 'anonymous';
}