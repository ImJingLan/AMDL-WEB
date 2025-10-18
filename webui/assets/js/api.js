import * as State from './state.js';
import * as UI from './ui.js';
import * as TaskQueue from './taskQueue.js';
import * as ModalHandler from './modalHandler.js'; // 确认 modalHandler.js 文件存在

// 辅助函数：将任务列表转换为 UUID -> Task 对象的 Map
function createMapFromTaskList(taskList) {
    const taskMap = new Map();
    if (Array.isArray(taskList)) {
        taskList.forEach(task => {
            if (task && task.uuid) {
                taskMap.set(task.uuid, task);
            }
        });
    }
    return taskMap;
}

// 直接启动轮询，不再需要获取用户名
export function startPolling() {
    console.log("启动任务状态轮询...");
    
    // 清除可能存在的旧定时器
    if (State.taskPollingIntervalId) {
        clearInterval(State.taskPollingIntervalId);
        clearTimeout(State.taskPollingIntervalId);
        State.setTaskPollingIntervalId(null);
    }
    
    // 立即执行首次轮询，后续由 adjustPollingInterval 控制间隔
    pollAndUpdateCovers(true);

    console.log("任务状态轮询已启动，使用动态间隔模式。");
}


// 核心：轮询并更新显示 (严格基于 focusedTaskUuid 刷新 Modal)
export async function pollAndUpdateCovers(isInitialCall = false) {
    // 检查是否暂停轮询
    if (State.getPollingPaused()) {
        return;
    }

    if (!State.domElements.taskQueueCoversContainer) return;
    // console.debug("Polling started...");

    let allTasks = [];
    let shouldUseLongPolling = false;

    try {
        // 首先进行一次普通轮询检查当前状态
        const quickResponse = await fetch('./api/task');
        if (!quickResponse.ok) {
            let errorMsg = `获取任务列表失败 (${quickResponse.status})`;
            try { const errorData = await quickResponse.json(); errorMsg = errorData.error || errorMsg; } catch(e) { /* ignore */ }
            throw new Error(errorMsg);
        }
        const quickTaskData = await quickResponse.json();
        if (!Array.isArray(quickTaskData)) { throw new Error("无效的任务列表响应格式 (非数组)"); }
        
        // 如果有任务，直接使用快速查询结果
        if (quickTaskData.length > 0) {
            allTasks = quickTaskData;
            shouldUseLongPolling = false;
        } else {
            // 如果没有任务，检查是否刚完成任务
            if (State.isRecentlyCompleted()) {
                // 任务刚完成，使用短轮询而不是长轮询
                allTasks = quickTaskData;
                shouldUseLongPolling = false;
            } else {
                // 任务完成超过5秒，启用长轮询等待新任务
                if (isInitialCall) {
                    allTasks = quickTaskData;
                    shouldUseLongPolling = false;
                } else {
                    shouldUseLongPolling = true;
                    
                    const longPollResponse = await fetch('./api/task?wait=true&timeout=30', {
                        signal: AbortSignal.timeout(60000) // 客户端超时，设置为60秒，大于服务端30秒超时
                    });
                    
                    if (!longPollResponse.ok) {
                        let errorMsg = `长轮询请求失败 (${longPollResponse.status})`;
                        try { const errorData = await longPollResponse.json(); errorMsg = errorData.error || errorMsg; } catch(e) { /* ignore */ }
                        throw new Error(errorMsg);
                    }
                    
                    const longPollTaskData = await longPollResponse.json();
                    if (!Array.isArray(longPollTaskData)) { throw new Error("无效的长轮询响应格式 (非数组)"); }
                    
                    allTasks = longPollTaskData;
                    
                    // console.debug(`长轮询检测到新任务: ${allTasks.length} 个`);
                }
            }
        }

        // 更新 Badge
        const activeTaskCount = allTasks.filter(t => t.status === 'running').length;
        // 判断是否所有任务都为 finish 或 error
        const allFinishedOrError = allTasks.length > 0 && allTasks.every(
            t => t.status === 'finish' || t.status === 'error'
        );
        
        // 检测任务完成状态变化
        const currentHasRunningTasks = activeTaskCount > 0;
        if (State.hadRunningTasks && !currentHasRunningTasks && allTasks.length > 0) {
            // 从有运行任务变为无运行任务，且仍有任务存在，说明任务刚完成
            State.setLastTaskCompletionTime(Date.now());
        }
        State.setHadRunningTasks(currentHasRunningTasks);
        
        if (allFinishedOrError) {
            UI.updatePendingTasksBadge('队列处理完毕', 'bg-info');
        } else if (activeTaskCount === 0 && allTasks.length === 0) {
            // 检查用户是否已登录，如果已登录则启用音乐服务器跳转
            const currentUser = State.getUserName();
            const isLoggedIn = currentUser && currentUser.trim() !== "";
            UI.updatePendingTasksBadge('准备就绪', 'bg-success', isLoggedIn);
        } else {
            UI.updatePendingTasksBadge(`活动任务: ${activeTaskCount}`, 'bg-info');
        }

        // 更新 State 中的最新任务数据 Map
        const taskMap = createMapFromTaskList(allTasks);
        State.setLatestTaskMap(taskMap);

        // 更新用户缓存 (使用 uuid)
        allTasks.forEach(task => {
            task.user = task.user || 'unknown';
            if (task.uuid && task.user !== 'unknown' && !State.linkUserCache.has(task.uuid)) {
                State.linkUserCache.set(task.uuid, task.user);
            }
             // 可选：保留 link -> user 映射，如果其他地方需要
            if (task.link && task.user !== 'unknown' && !State.linkUserCache.has(task.link)) {
                State.linkUserCache.set(task.link, task.user);
            }
        });

        // 新增：验证并清理URL参数
        const UrlParams = await import('./urlParams.js');
        UrlParams.checkAndCleanExpiredParams(taskMap);

        // 查找当前运行任务的 UUID (主要用于可能的特殊标记，不是用于 Modal 切换)
        const runningTask = allTasks.find(task => task.status === 'running');
        State.setCurrentRunningTaskUuid(runningTask ? runningTask.uuid : null);

        // Modal 刷新逻辑 (严格检查 focusedTaskUuid)
        if (State.isLogModalActive && State.focusedTaskUuid) {
             const currentFocusedUuid = State.focusedTaskUuid;
             const modalElement = State.domElements.logModalElement;

             if (modalElement) { // 确保元素存在
                 const latestTaskData = State.latestTaskMap.get(currentFocusedUuid);

                 if (latestTaskData) {
                    // 找到了关注任务的最新数据，异步更新 Modal
                    requestAnimationFrame(async () => {
                         // 双重确认：Modal 仍打开，并且关注的 UUID 未变
                         if (State.isLogModalActive && State.focusedTaskUuid === currentFocusedUuid) {
                            // console.debug(`Updating Modal content for focused UUID: ${currentFocusedUuid}`);
                             
                             // 始终更新基础信息（封面、用户等）
                             await ModalHandler.updateLogModalInfo(latestTaskData);
                             
                             // 检查是否正在进行分批渲染
                             if (State.getIsProgressiveRendering() && State.getCurrentRenderingTaskUuid() === currentFocusedUuid) {
                                 // 正在分批渲染中，不进行完整渲染，避免干扰
                                 console.debug(`跳过完整渲染，正在进行分批渲染 UUID: ${currentFocusedUuid}`);
                             } else {
                                 // 不在分批渲染中，进行正常的完整渲染
                                 ModalHandler.renderModalContentFromTaskData(latestTaskData);
                             }
                         } else {
                            // console.warn(`Modal refresh for ${currentFocusedUuid} aborted: Modal closed or focus changed.`);
                         }
                    });
                 } else {
                    // 关注的任务数据在最新轮询中消失了
                    console.warn(`Modal active and focused on ${currentFocusedUuid}, but task data not found. Modal content will NOT be cleared.`);
                    
                    // 新增：检查URL参数，如果存在则清除
                    const UrlParams = await import('./urlParams.js');
                    const currentUrlUuid = UrlParams.getMonitorUuid();
                    if (currentUrlUuid === currentFocusedUuid) {
                        console.log(`任务 ${currentFocusedUuid} 已不存在，尝试清除URL参数`);
                        
                        // 🛡️ 使用安全清除：检查页面状态
                        if (window.amdlPageState && (window.amdlPageState.isUnloading || window.amdlPageState.isHidden)) {
                            console.log('[API] 🛡️ 跳过URL参数清除，页面状态保护（任务不存在）');
                            
                            // 创建备份到sessionStorage
                            try {
                                sessionStorage.setItem('amdl_api_url_backup', JSON.stringify({
                                    monitor: UrlParams.getUrlParam('monitor'),
                                    uuid: currentUrlUuid,
                                    timestamp: Date.now(),
                                    source: 'api_task_missing'
                                }));
                                console.log('[API] 🛡️ 已备份URL参数（任务缺失）');
                            } catch (e) {
                                console.warn('[API] sessionStorage备份失败:', e);
                            }
                        } else {
                            // 安全清除URL参数
                            UrlParams.clearMonitorParams();
                        }
                    }
                    
                    // 保持 Modal 显示最后的状态，不主动清空
                 }
             } else {
                  console.error("Modal 元素丢失，无法刷新！");
             }
        }

        // 渲染任务队列封面 (包含所有状态)
        TaskQueue.renderTaskQueueCovers(allTasks);

        // 根据是否使用了长轮询调整下次轮询时间
        adjustPollingInterval(shouldUseLongPolling, allTasks.length);

    } catch (error) {
        console.error("轮询任务列表或处理时发生错误:", error); // 打印整个error对象
        let displayError = "发生错误"; // 默认错误信息
        if (error && error.message) {
            try {
                // 尝试将错误消息解析为JSON对象
                const errorObj = JSON.parse(error.message);
                if (errorObj && errorObj.error) {
                    displayError = errorObj.error;
                } else if (errorObj && errorObj.message) {
                    displayError = errorObj.message;
                } else {
                    // 如果解析出的JSON没有特定错误字段，但error.message本身是有效字符串
                    displayError = error.message;
                }
            } catch (e) {
                // 如果解析JSON失败，则直接显示原始error.message
                displayError = error.message;
            }
        } else if (typeof error === 'string') { // 如果error本身就是字符串
            displayError = error;
        }
        // 如果以上条件都不满足，且 error 对象存在，则尝试将其转换为字符串
        else if (error) {
            try {
                displayError = JSON.stringify(error);
            } catch (e) {
                 displayError = "发生未知错误"; // 最终回退
            }
        }

        const badgeElement = State.domElements.pendingTasksBadge;
        if (badgeElement && !badgeElement.classList.contains('bg-danger')) {
            UI.updatePendingTasksBadge(displayError, "bg-danger");
        }
        UI.updateScrollButtons(); // 即使出错也更新滚动按钮
        
        // 如果长轮询出错，快速重试
        adjustPollingInterval(false, 0, true);
    }
    // console.debug("Polling finished.");
}

// 新增：根据情况调整轮询间隔
function adjustPollingInterval(usedLongPolling, taskCount, hasError = false) {
    if (State.taskPollingIntervalId) {
        clearInterval(State.taskPollingIntervalId);
        clearTimeout(State.taskPollingIntervalId);
    }
    
    let nextInterval;
    if (hasError) {
        // 出错时快速重试
        nextInterval = 2000;
    } else if (usedLongPolling) {
        // 使用了长轮询，下次轮询间隔较短
        nextInterval = taskCount > 0 ? 1000 : 3000;
    } else {
        // 正常轮询，根据任务数量调整间隔
        if (taskCount === 0) {
            // 检查是否刚完成任务
            if (State.isRecentlyCompleted()) {
                // 任务刚完成，保持短轮询10秒
                nextInterval = State.POLLING_INTERVAL;
            } else {
                // 无任务且不是刚完成，降低频率
                nextInterval = 5000;
            }
        } else {
            nextInterval = State.POLLING_INTERVAL; // 有任务时正常频率
        }
    }
    
    const newIntervalId = setTimeout(() => {
        // 改为一次性定时器，执行后重新设置
        pollAndUpdateCovers();
    }, nextInterval);
    
    State.setTaskPollingIntervalId(newIntervalId);
}

// 发送请求函数 (提交任务) - 移除用户名相关逻辑
export async function sendRequest() {
    // --- 登录状态检查 ---
    const currentUser = State.getUserName();
    if (!currentUser || currentUser.trim() === "") {
        UI.showErrorMessage("请先登录以使用AMDL服务。", true);
        return;
    }
    
    // --- 前置检查 ---
    // 检查所有需要的 DOM 元素和 Modal 实例是否存在
    if (!State.domElements.songLinksTextarea ||
        !State.succeedModalInstance ||
        !State.failedModalInstance ||
        !State.domElements.sendButton ||
        !State.domElements.successMessageElement ||
        !State.domElements.errorMessageElement) {
        UI.showErrorMessage("页面组件未完全加载，请刷新页面重试。");
        return;
    }
    // --- 前置检查结束 ---

    // --- 数据准备和验证 ---
    const rawLinksInput = State.domElements.songLinksTextarea.value;
    console.log("原始输入:\n", rawLinksInput); // <-- 日志 1

    if (!rawLinksInput || rawLinksInput.trim() === "") {
        UI.showErrorMessage("请输入有效的歌曲链接。");
        return;
    }

    // 使用更健壮的正则表达式拆分（处理多种空白符和分隔符）
    const allInputs = rawLinksInput.split(/[\r\n\s,;，；]+/) // 匹配回车,换行,空白符,中英文逗号/分号
                                 .map(link => link.trim())       // 去除首尾空格
                                 .filter(link => link && link.length > 0); // 过滤掉空字符串
    console.log("拆分后的所有输入:", allInputs); // <-- 日志 2

    // 筛选有效和无效链接
    const validLinks = allInputs.filter(link => link.startsWith("https://music.apple.com/"));
    console.log("有效的 Apple Music 链接:", validLinks); // <-- 日志 3

    const invalidLinks = allInputs.filter(link => !link.startsWith("https://music.apple.com/"));
    if (invalidLinks.length > 0) {
        console.warn(`忽略了 ${invalidLinks.length} 个无效输入:`, invalidLinks);
    }

    if (validLinks.length === 0) {
        if (invalidLinks.length > 0) {
            UI.showErrorMessage("输入内容均不是有效的 Apple Music 链接！");
        } else {
            UI.showErrorMessage("未检测到有效的 Apple Music 链接。");
        }
        return;
    }
    // --- 数据准备和验证结束 ---


    // --- 构建 Payload (移除用户名) ---
    const forceOverride = State.getSkipCheck();
    const tasksPayload = validLinks.map(link => ({
        link: link,            // link 是单个有效的链接字符串
        skip_check: forceOverride  // 使用新的 forceOverride 变量
    }));
    console.log("构建的任务 Payload (数组):", tasksPayload); // <-- 日志 4
    // **关键日志**：检查最终发送给后端的 JSON 字符串
    console.log("即将发送的 JSON:", JSON.stringify(tasksPayload)); // <-- 日志 5
    // --- Payload 构建结束 ---


    // --- UI 操作：禁用按钮，显示加载状态 ---
    State.domElements.sendButton.disabled = true;
    State.domElements.sendButton.classList.add('loading');
    // --- UI 操作结束 ---


    // --- 发送请求并处理响应 ---
    try {
        const response = await fetch('/api/task', { // 使用 POST /api/task 端点
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(tasksPayload) // 发送正确的 JSON 数组字符串
        });
        const responseData = await response.json();

        // 检查 HTTP 状态码
        if (!response.ok) {
            // 使用后端返回的 message 构造错误
            throw new Error(responseData.message || `请求失败 (${response.status})`);
        }

        // 处理业务状态和摘要信息
        const status = responseData.status;
        const message = responseData.message || "处理完成。";
        const acceptedCount = responseData.accepted_count || 0;
        const failedCount = responseData.failed_count || 0;
        const failureSummary = responseData.failure_summary || {};

        if (status === 'success' || status === 'partial_success') {
            // 格式化成功/部分成功的消息
            let successMsg = `后端消息: ${message}`;
            // 检查后端消息是否已包含任务计数信息
            if (!message.includes("接受") && !message.includes("失败")) {
                successMsg += `\n接受任务: ${acceptedCount} 个, 失败任务: ${failedCount} 个。`;
            }

            if (failedCount > 0 && Object.keys(failureSummary).length > 0) {
                successMsg += "\n失败原因统计:\n";
                for (const reason in failureSummary) {
                    successMsg += `- ${reason}: ${failureSummary[reason]} 次\n`;
                }
            } else if (failedCount > 0) {
                 successMsg += "(未能获取详细失败原因统计)\n";
            }

            if (invalidLinks.length > 0) {
                 successMsg += `\n(另有 ${invalidLinks.length} 个非 Apple Music 输入在前端被忽略)`;
            }

            // 🎯 新增：设置任务完成时间以启用10秒短轮询模式
            State.setLastTaskCompletionTime(Date.now());
            State.setHadRunningTasks(true); // 标记有任务运行，为后续检测完成做准备
            console.log('[MainPage] 📥 设置任务完成时间，启用10秒短轮询模式');

            // 更新成功 Modal 内容并显示
            State.domElements.successMessageElement.innerHTML = successMsg.trim().replace(/\n/g, '<br>');
            State.succeedModalInstance.show();
            State.domElements.songLinksTextarea.value = ''; // 清空输入
            UI.autoResizeTextarea(); // 调整大小
            
            // 清除可能存在的暂存输入内容
            localStorage.removeItem('amdl_pending_input');
            console.log("请求成功，已清除暂存的输入内容");

        } else { // status === 'failure' 或其他情况
             // 格式化失败消息
            let errorMsg = `后端消息: ${message}\n`;
            errorMsg += `接受任务: ${acceptedCount} 个, 失败任务: ${failedCount} 个。\n`;
             if (failedCount > 0 && Object.keys(failureSummary).length > 0) {
                 errorMsg += "\n失败原因统计:\n";
                 for (const reason in failureSummary) {
                     errorMsg += `- ${reason}: ${failureSummary[reason]} 次\n`;
                 }
             }
            throw new Error(errorMsg); // 抛出错误，由 catch 处理
        }

    } catch (error) {
        // 处理 fetch 错误或后端抛出的错误
        console.error("提交任务时出错:", error);
        let displayMessage = `链接提交遇到问题：\n${error.message}`;
        // 显示错误 Modal（移除登录按钮相关逻辑）
        UI.showErrorMessage(displayMessage.trim());

    } finally {
        // 恢复 UI 状态
        State.domElements.sendButton.disabled = false;
        State.domElements.sendButton.classList.remove('loading');
        // 触发一次轮询以更新界面
        setTimeout(pollAndUpdateCovers, 200);
    }
    // --- 请求处理结束 ---
}