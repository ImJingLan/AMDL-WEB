// 引入模块
import * as State from './state.js';
import * as UI from './ui.js';
import * as API from './api.js';
import * as ModalHandler from './modalHandler.js'; // 使用新的模块名
import * as TaskQueue from './taskQueue.js';
import * as UrlParams from './urlParams.js'; // 新增：引入URL参数处理模块
import * as CacheDebug from './cacheDebug.js'; // 新增：引入缓存调试工具
import * as UserProfile from './userProfile.js'; // 新增：引入用户头像模块
import { initLogin, handleLogin, handleLogout } from './login.js';
import { handleSubmit } from './search.js';

// 🛡️ 新增：页面刷新保护机制
let isPageUnloading = false;
let isPageHidden = false;

// 🛡️ 设置全局状态变量，供其他模块使用
window.amdlPageState = {
    isUnloading: false,
    isHidden: false
};

// 检测页面刷新/导航
window.addEventListener('beforeunload', () => {
    isPageUnloading = true;
    window.amdlPageState.isUnloading = true;
    console.log('[PageProtection] 检测到页面刷新/导航，保护URL参数不被清除');
    
    // 🛡️ 创建URL参数备份到sessionStorage
    const currentParams = {
        monitor: UrlParams.getUrlParam('monitor'),
        uuid: UrlParams.getUrlParam('uuid')
    };
    
    if (currentParams.monitor || currentParams.uuid) {
        try {
            sessionStorage.setItem('amdl_url_backup', JSON.stringify({
                ...currentParams,
                timestamp: Date.now(),
                source: 'beforeunload'
            }));
            console.log('[PageProtection] 🛡️ 已备份URL参数到sessionStorage');
        } catch (e) {
            console.warn('[PageProtection] sessionStorage备份失败:', e);
        }
    }
});

// 检测页面可见性变化
document.addEventListener('visibilitychange', () => {
    isPageHidden = document.hidden;
    window.amdlPageState.isHidden = document.hidden;
    console.log(`[PageProtection] 页面可见性变化: ${isPageHidden ? '隐藏' : '显示'}`);
    
    // 🛡️ 页面重新可见时，尝试恢复URL参数
    if (!isPageHidden) {
        setTimeout(() => {
            tryRestoreUrlParams();
        }, 100);
    }
});

// 🛡️ 页面完全加载后，尝试恢复URL参数
window.addEventListener('load', () => {
    setTimeout(() => {
        tryRestoreUrlParams();
    }, 500);
});

// 🛡️ 尝试从sessionStorage恢复URL参数
function tryRestoreUrlParams() {
    try {
        // 检查多个可能的备份来源
        const backupSources = [
            'amdl_url_backup',
            'amdl_pending_url_params', 
            'amdl_api_url_backup'
        ];
        
        let backup = null;
        let backupSource = null;
        
        for (const source of backupSources) {
            const data = sessionStorage.getItem(source);
            if (data) {
                backup = data;
                backupSource = source;
                break;
            }
        }
        
        if (backup) {
            const params = JSON.parse(backup);
            const currentParams = {
                monitor: UrlParams.getUrlParam('monitor'),
                uuid: UrlParams.getUrlParam('uuid')
            };
            
            // 检查当前是否缺少参数，但备份中有
            const needsRestore = (params.monitor && !currentParams.monitor) || 
                                (params.uuid && !currentParams.uuid);
            
            if (needsRestore) {
                console.log(`[PageProtection] 🔄 从 ${backupSource} 恢复URL参数:`, params);
                
                if (params.monitor) {
                    UrlParams.setUrlParam('monitor', params.monitor);
                }
                if (params.uuid) {
                    UrlParams.setUrlParam('uuid', params.uuid);
                }
                
                // 清除所有备份
                backupSources.forEach(source => {
                    sessionStorage.removeItem(source);
                });
                
                // 触发自动打开
                setTimeout(() => {
                    if (UrlParams.shouldAutoOpenLogModal()) {
                        console.log('[PageProtection] 🚀 恢复后自动打开监视窗口');
                        checkAndAutoOpenLogModal();
                    }
                }, 100);
            } else {
                // 参数完整，清除备份
                backupSources.forEach(source => {
                    sessionStorage.removeItem(source);
                });
            }
        }
    } catch (e) {
        console.warn('[PageProtection] URL参数恢复失败:', e);
    }
}

// 🛡️ 安全的URL参数清除函数
function safelyCleanUrlParams(reason = '用户操作') {
    // 如果页面正在卸载或隐藏，不清除URL参数
    if (isPageUnloading || isPageHidden) {
        console.log(`[PageProtection] 跳过URL参数清除，原因：页面状态异常 (${reason})`);
        return;
    }
    
    console.log(`[URLParams] 安全清除URL参数，原因：${reason}`);
    UrlParams.clearMonitorParams();
}

// --- DOMContentLoaded 事件监听 ---
document.addEventListener('DOMContentLoaded', (event) => {
    console.log("DOM fully loaded and parsed.");

    // 初始化登录模块
    initLogin();

    // 获取关键 DOM 元素并存储到 state.js
    const elements = {
        errorMessageElement: document.getElementById("errorMessage"),
        successMessageElement: document.getElementById("successMessage"),
        sendButton: document.getElementById("sendButton"),
        taskQueueCoversContainer: document.getElementById("taskQueueCovers"),
        coverScrollContainer: document.getElementById("coverScrollContainer"),
        scrollLeftBtn: document.getElementById("scrollLeftBtn"),
        scrollRightBtn: document.getElementById("scrollRightBtn"),
        songLinksTextarea: document.getElementById("song_links"),
        pendingTasksBadge: document.getElementById("pendingTasks"),
        logModalElement: document.getElementById('logModal'),
        succeedModalElement: document.getElementById('succeedModal'),
        failedModalElement: document.getElementById('failedModal'),
        searchResultsModalElement: document.getElementById('searchResultsModal'),
        confirmSuccessBtn: document.getElementById('confirmSuccessBtn'),
        viewLogsBtn: document.getElementById('viewLogsBtn'),
        customTooltip: document.getElementById('customTooltip'),
        // 用户头像区域的强制跳过开关
        userProfileSkipCheckbox: document.getElementById('userProfileSkipCheckbox'),
        userProfileSwitchContainer: document.getElementById('userProfileSwitchContainer'),
        // 图片搜索相关元素
        imageSearchModalElement: document.getElementById('imageSearchModal'),
        imageUploadArea: document.getElementById('imageUploadArea'),
        imageFileUpload: document.getElementById('imageFileUpload'),
        imagePreviewContainer: document.getElementById('imagePreviewContainer'),
        imagePreview: document.getElementById('imagePreview'),
        imageLoadingIndicator: document.getElementById('imageLoadingIndicator'),
        imageErrorMessage: document.getElementById('imageErrorMessage'),
        imageErrorText: document.getElementById('imageErrorText'),
        recognitionResultModalElement: document.getElementById('recognitionResultModal'),
        recognitionResults: document.getElementById('recognitionResults'),
        autoSearchResults: document.getElementById('autoSearchResults'),
        offsetSearchBtn: document.getElementById('offsetSearchBtn'),
        downloadSelectedBtn: document.getElementById('downloadSelectedBtn'),
        appleMusicsBtn: document.getElementById('appleMusicsBtn')
    };
    State.setDomElements(elements);

    // 检查核心元素是否存在
    const coreElements = [
        elements.songLinksTextarea, elements.sendButton, elements.pendingTasksBadge,
        elements.taskQueueCoversContainer, elements.coverScrollContainer,
        elements.scrollLeftBtn, elements.scrollRightBtn,
        elements.logModalElement, elements.succeedModalElement, elements.failedModalElement,
        elements.customTooltip, elements.userProfileSkipCheckbox, elements.userProfileSwitchContainer
    ];
    if (coreElements.some(el => !el)) {
        console.error("页面初始化失败：缺少必要的 DOM 元素。");
        UI.updatePendingTasksBadge("页面错误", "bg-danger");
        alert("页面加载不完整，部分功能可能无法使用。请尝试刷新页面。");
        return;
    }

    // 初始化 Modals 并存储到 state.js
    try {
        const instances = {
            succeedModalInstance: new bootstrap.Modal(elements.succeedModalElement),
            failedModalInstance: new bootstrap.Modal(elements.failedModalElement),
            logModalInstance: new bootstrap.Modal(elements.logModalElement),
            imageSearchModalInstance: new bootstrap.Modal(elements.imageSearchModalElement),
            recognitionResultModalInstance: new bootstrap.Modal(elements.recognitionResultModalElement)
        };
        State.setModalInstances(instances);
        console.log("Modal 实例初始化完成。");
        
    } catch (e) {
        console.error("初始化 Modal 实例时出错:", e);
        alert("页面初始化失败：无法创建 Modal。");
        return;
    }

    // 初始化用户头像组件
    UserProfile.initUserProfile();

    // --- 绑定 UI 事件监听器 ---

    // 滚动按钮
    if (elements.scrollLeftBtn && elements.scrollRightBtn && elements.coverScrollContainer) {
        elements.scrollLeftBtn.addEventListener('click', () => UI.scrollCovers('left'));
        elements.scrollRightBtn.addEventListener('click', () => UI.scrollCovers('right'));
        elements.coverScrollContainer.addEventListener('scroll', UI.updateScrollButtons);
        window.addEventListener('resize', UI.updateScrollButtons);
    } else {
        console.warn("滚动按钮或容器未完全找到。");
    }

    // 歌曲链接输入框自动调整大小
    if (elements.songLinksTextarea) {
        elements.songLinksTextarea.addEventListener('input', UI.autoResizeTextarea);
        UI.autoResizeTextarea(); // 初始调整
        
        // 检查并恢复暂存的输入内容（页面刷新或重新加载时）
        const pendingInput = localStorage.getItem('amdl_pending_input');
        if (pendingInput && pendingInput.trim()) {
            elements.songLinksTextarea.value = pendingInput;
            UI.autoResizeTextarea(); // 调整输入框大小
            console.log("检测到暂存的用户输入内容，已自动恢复");
            // 注意：这里不立即清除，因为用户可能还未登录
        }
    } else {
        console.warn("歌曲链接输入框未找到。");
    }

    // 注意：封面的点击事件现在由 taskQueue.js 在创建/更新元素时直接绑定

    // 任务详情 Modal 显示/隐藏事件
    if (elements.logModalElement && State.logModalInstance) {
        elements.logModalElement.addEventListener('show.bs.modal', () => {
            // Modal 开始显示时添加模糊效果
            document.body.classList.add('modal-blur-active');
            
            // 隐藏用户头像
            UserProfile.hideUserProfile();
        });

        elements.logModalElement.addEventListener('hide.bs.modal', () => {
            // Modal 开始隐藏时移除模糊效果
            document.body.classList.remove('modal-blur-active');
            // console.debug("Modal 关闭事件触发");
            State.setIsLogModalActive(false);
            State.setFocusedTaskUuid(null); // 清空关注的 UUID
            elements.logModalElement.dataset.showingUuid = ''; // 清空 dataset
            
            // 立即显示用户头像（开始关闭动画时）
            if (State.getUserName()) {
                UserProfile.showUserProfile();
            }
            
            // 新增：清除URL参数
            safelyCleanUrlParams();
        });

        elements.logModalElement.addEventListener('shown.bs.modal', async () => {
             // console.debug("Modal 打开事件触发");
             State.setIsLogModalActive(true); // 标记 Modal 可见
             
             // 移除预设高度的类
             elements.logModalElement.classList.remove('log-modal-prepare-height');
             
             // 检查是否是URL参数自动打开，避免重复处理
             if (elements.logModalElement.dataset.isAutoOpening === 'true') {
                 console.log('[Modal] URL参数自动打开，跳过shown.bs.modal处理');
                 return;
             }
             
             // 新增：更新URL参数，记录监视状态
             const taskUuidToShow = State.focusedTaskUuid;
             UrlParams.setMonitorStatus(true, taskUuidToShow);

             if (taskUuidToShow) {
                 // console.debug(`Modal shown, focused on UUID: ${taskUuidToShow}. Verifying content...`);
                 const taskData = State.latestTaskMap.get(taskUuidToShow);
                 
                 if (taskData) {
                    // 先更新基础信息（封面、用户等）
                    await ModalHandler.updateLogModalInfo(taskData);
                    
                    // 使用快速渲染模式：只渲染前3个音轨，立即显示窗口
                    ModalHandler.renderModalContentFromTaskData(taskData, { fastRender: true });
                    
                    // 如果有更多音轨需要渲染，延迟启动后续渲染
                    if (State.getIsProgressiveRendering()) {
                        setTimeout(() => {
                            if (State.isLogModalActive && State.focusedTaskUuid === taskUuidToShow) {
                                console.log(`[Modal] 开始渲染剩余音轨 for UUID: ${taskUuidToShow}`);
                                ModalHandler.continueRenderingTracks(taskData);
                            }
                        }, 200); // 200ms后开始渐进式渲染
                    }
                 } else {
                     console.warn(`Modal shown, but data for focused UUID ${taskUuidToShow} not found. Displaying 'not found'.`);
                      await ModalHandler.updateLogModalInfo(null);
                      const outputDiv = elements.logModalElement.querySelector('#formattedLogOutput');
                      if(outputDiv) outputDiv.innerHTML = '<p class="text-muted text-center p-5">任务数据无法加载或已不存在。</p>';
                 }
             } else {
                  console.warn("Modal shown, but no focusedTaskUuid set. Displaying 'no task'.");
                  await ModalHandler.updateLogModalInfo(null); // 显示无任务状态
             }
             
             // 🎯 新增：确保滚动位置始终位于顶部（包括URL参数自动打开和手动打开）
             setTimeout(() => {
                 const modalBody = elements.logModalElement.querySelector('.modal-body');
                 const scrollableArea = modalBody ? modalBody.querySelector('.log-tracks-scrollable') : null;
                 
                 // 重置Modal body滚动位置
                 if (modalBody) {
                     modalBody.scrollTop = 0;
                 }
                 
                 // 重置音轨列表滚动位置  
                 if (scrollableArea) {
                     scrollableArea.scrollTop = 0;
                 }
                 
                 console.log('[Modal] ✅ 滚动位置已重置到顶部');
             }, 150); // 等待内容渲染完成后重置滚动位置
        });
        
        // 添加Modal关闭事件，断开SSE连接
        elements.logModalElement.addEventListener('hidden.bs.modal', () => {
            const taskUuid = State.focusedTaskUuid;
            
            // 清理Modal资源
            ModalHandler.cleanupModalResources();
            
            // 清除Modal状态
            State.setIsLogModalActive(false);
            
            // 新增：确保URL参数已清除
            safelyCleanUrlParams();
        });
    } else {
        console.warn("任务详情 Modal 元素或实例未找到。");
    }

    // 成功 Modal 确认按钮事件
    if (elements.confirmSuccessBtn && elements.songLinksTextarea) {
        elements.confirmSuccessBtn.addEventListener('click', () => {
            UI.autoResizeTextarea();
        });
    } else {
        console.warn("成功确认按钮或输入框未找到。");
    }

    // "查看日志" 按钮事件
    if (elements.viewLogsBtn && State.succeedModalInstance && State.logModalInstance) {
        elements.viewLogsBtn.addEventListener('click', async () => {
            State.succeedModalInstance.hide();
            // 隐藏动画结束后执行
            elements.succeedModalElement.addEventListener('hidden.bs.modal', () => {
                 let taskToShow = null;
                 // 查找合适的任务来显示 (优先 running, 其次 error, 最后 finish)
                 const tasks = Array.from(State.latestTaskMap.values());
                 taskToShow = tasks.find(t => t.status === 'running') ||
                              tasks.filter(t => t.status === 'error').sort((a, b) => (b.added_timestamp || 0) - (a.added_timestamp || 0))[0] || // 按时间戳降序取第一个错误
                              tasks.filter(t => t.status === 'finish').sort((a, b) => (b.added_timestamp || 0) - (a.added_timestamp || 0))[0];  // 按时间戳降序取第一个完成

                 if (taskToShow && taskToShow.uuid) {
                      console.log(`"查看日志" 按钮：设置关注 UUID 为 ${taskToShow.uuid}`);
                      State.setFocusedTaskUuid(taskToShow.uuid); // 设置关注焦点
                      elements.logModalElement.dataset.showingUuid = taskToShow.uuid; // 设置 dataset
                      
                      // 新增：预先设置URL参数
                      UrlParams.setMonitorStatus(true, taskToShow.uuid);

                      // 添加预设高度的类
                      elements.logModalElement.classList.add('log-modal-prepare-height');
                      
                      State.logModalInstance.show(); // 显示 Modal
                 } else {
                      console.warn("点击 '查看日志' 时未找到合适的近期任务来显示。");
                      State.setFocusedTaskUuid(null);
                      elements.logModalElement.dataset.showingUuid = '';
                      
                      // 新增：设置URL参数（无特定UUID）
                      UrlParams.setMonitorStatus(true);

                      // 添加预设高度的类
                      elements.logModalElement.classList.add('log-modal-prepare-height');
                      
                      State.logModalInstance.show(); // 显示无任务状态的 Modal
                 }
            }, { once: true });
        });
    } else {
        console.warn("无法找到'查看日志'按钮或相关 Modal 实例。");
    }

    // 图片搜索 Modal 显示/隐藏事件
    if (elements.imageSearchModalElement) {
        elements.imageSearchModalElement.addEventListener('show.bs.modal', () => {
            // Modal 开始显示时添加模糊效果
            document.body.classList.add('modal-blur-active');
            
            // 隐藏用户头像
            UserProfile.hideUserProfile();
            
            // 重置图片搜索状态
            resetImageSearchModal();
        });

        elements.imageSearchModalElement.addEventListener('hide.bs.modal', () => {
            // Modal 开始隐藏时移除模糊效果
            document.body.classList.remove('modal-blur-active');
            
            // 立即显示用户头像（开始关闭动画时）
            if (State.getUserName()) {
                UserProfile.showUserProfile();
            }
            
            // 重置上传区域状态
            resetUploadArea();
        });
    } else {
        console.warn("图片搜索 Modal 元素未找到。");
    }

    // 搜索结果 Modal 显示/隐藏事件
    if (elements.searchResultsModalElement) {
        elements.searchResultsModalElement.addEventListener('show.bs.modal', () => {
            // Modal 开始显示时添加模糊效果
            document.body.classList.add('modal-blur-active');
            
            // 隐藏用户头像
            UserProfile.hideUserProfile();
        });

        elements.searchResultsModalElement.addEventListener('hide.bs.modal', () => {
            // Modal 开始隐藏时移除模糊效果
            document.body.classList.remove('modal-blur-active');
            
            // 立即显示用户头像（开始关闭动画时）
            if (State.getUserName()) {
                UserProfile.showUserProfile();
            }
        });

        elements.searchResultsModalElement.addEventListener('shown.bs.modal', async () => {
            // Modal 完全显示后，进行静默预加载搜索更多
            console.log('[SearchPreload] 搜索窗口已显示，开始预加载下一页内容');
            
            // 延迟500ms后进行预加载，避免影响窗口显示性能
            setTimeout(() => {
                silentPreloadMoreResults();
            }, 500);
        });
    } else {
        console.warn("搜索结果 Modal 元素未找到。");
    }

    // 状态显示更新
    if (elements.pendingTasksBadge) {
        // 移除点击事件监听器，改为只更新状态显示
        const updateStatusDisplay = (skipCheck, isInitialState = false) => {
            const badge = elements.pendingTasksBadge;
            // 移除所有背景类
            badge.classList.remove('bg-success', 'bg-info', 'bg-danger');
            
            // 根据状态设置文本和样式
            if (isInitialState && !skipCheck) {
                badge.textContent = '准备就绪';
                badge.classList.add('bg-success');
            } else {
                badge.textContent = skipCheck ? '强制覆盖' : '正常检查';
                badge.classList.add('bg-info'); // 正常检查和强制覆盖都使用蓝色
            }
        };
        
        // 初始化状态显示（使用准备就绪）
        updateStatusDisplay(State.getSkipCheck(), true);
    }

    // 切换跳过按钮点击事件
    if (elements.userProfileSkipCheckbox) {
        console.log('[SkipCheck] 初始化强制覆盖按钮事件监听器');
        
        // 移除可能存在的旧事件监听器
        const oldHandler = elements.userProfileSkipCheckbox._changeHandler;
        if (oldHandler) {
            elements.userProfileSkipCheckbox.removeEventListener('change', oldHandler);
        }
        
        // 创建新的事件处理函数
        const changeHandler = () => {
            console.log('[SkipCheck] 强制覆盖按钮状态改变');
            const newSkipCheck = elements.userProfileSkipCheckbox.checked;
            console.log('[SkipCheck] 新状态:', newSkipCheck);
            
            // 更新状态
            State.setSkipCheck(newSkipCheck);
            
            // 暂停轮询
            State.setPollingPaused(true);
            
            // 立即更新状态显示
            elements.pendingTasksBadge.textContent = newSkipCheck ? '强制覆盖' : '正常检查';
            elements.pendingTasksBadge.classList.remove('bg-success', 'bg-info', 'bg-danger');
            elements.pendingTasksBadge.classList.add('bg-info'); // 正常检查和强制覆盖都使用蓝色
            
            // 2秒后恢复轮询并更新状态显示
            setTimeout(() => {
                State.setPollingPaused(false);
                
                // 根据当前任务状态更新显示
                const taskMap = State.latestTaskMap;
                const allTasks = Array.from(taskMap.values());
                
                if (allTasks.length === 0) {
                    // 没有任务时，显示准备就绪并检查是否启用音乐服务器跳转
                    const currentUser = State.getUserName();
                    const isLoggedIn = currentUser && currentUser.trim() !== "";
                    UI.updatePendingTasksBadge('准备就绪', 'bg-success', isLoggedIn);
                } else {
                    // 有任务时，根据开关状态显示
                    const statusText = newSkipCheck ? '强制覆盖' : '正常检查';
                    elements.pendingTasksBadge.textContent = statusText;
                    elements.pendingTasksBadge.classList.remove('bg-success', 'bg-info', 'bg-danger');
                    elements.pendingTasksBadge.classList.add('bg-info');
                }
            }, 2000);
        };
        
        // 保存事件处理函数的引用
        elements.userProfileSkipCheckbox._changeHandler = changeHandler;
        
        // 添加新的事件监听器
        elements.userProfileSkipCheckbox.addEventListener('change', changeHandler);
        
        // 初始化复选框状态
        elements.userProfileSkipCheckbox.checked = State.getSkipCheck();
        console.log('[SkipCheck] 初始化状态:', State.getSkipCheck());
        
        // 设置初始禁用状态（登录检查前先禁用）
        if (elements.userProfileSwitchContainer) {
            elements.userProfileSkipCheckbox.disabled = true;
            elements.userProfileSwitchContainer.classList.add('disabled');
            elements.userProfileSwitchContainer.title = '登录后可用';
        }
    }

    // --- 开始应用逻辑 ---
    console.log("开始启动应用...");
    
    // 初始化图片搜索功能
    initImageSearchFeature();
    
    // 将图片搜索功能导出到全局
    window.handleSubmitWithImageSearch = handleSubmitWithImageSearch;
    
    checkUserLoginAndStartApp();

    console.log("所有初始化事件绑定完成。");
});

// 新增：检查用户登录状态的函数
async function checkUserLoginAndStartApp() {
    console.log("检查用户登录状态...");
    
    try {
        // 添加超时控制，避免长时间等待
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 5000); // 5秒超时
        
        const response = await fetch('/oauth2/userinfo', {
            signal: controller.signal,
            credentials: 'include' // 确保包含cookies
        });
        
        clearTimeout(timeoutId);
        
        if (!response.ok) {
            // 区分不同的错误类型
            if (response.status === 401 || response.status === 403) {
                throw new Error(`需要登录认证: ${response.status}`);
            } else if (response.status >= 500) {
                throw new Error(`服务器错误: ${response.status}`);
            } else {
                throw new Error(`请求失败: ${response.status}`);
            }
        }
        
        const userInfo = await response.json();
        if (!userInfo || !userInfo.user) {
            throw new Error("无法从响应中获取有效的用户信息。");
        }
        
        // 保存用户名到状态
        State.setUserName(userInfo.user);
        State.setPollingPaused(false); // 登录成功恢复轮询
        // 登录成功后，自动关闭错误弹窗
        if (State.failedModalInstance) {
            State.failedModalInstance.hide();
        }
        console.log("用户登录验证成功，用户名:", userInfo.user);
        
        // 更新用户头像显示（会自动获取头像）
        await UserProfile.updateUserInfo(userInfo.user);
        
        // 设置头像交互事件（悬停显示菜单，点击退出登录）
        UserProfile.setProfileClickHandler();
        
        // 启动应用
        startApplication();
        
    } catch (error) {
        console.error('用户登录检查失败:', error);
        // 区分错误类型，只有认证错误才显示登录提示
        if (error.name === 'AbortError') {
            console.warn('登录检查超时，可能是网络问题，尝试继续启动应用');
            // 超时情况下不隐藏头像，直接提示登录
            showLoginPrompt('登录检查超时，请重新登录');
        } else if (error.message.includes('需要登录认证')) {
            State.setPollingPaused(true); // 未登录时暂停轮询
            // 只有明确的认证错误才显示登录提示
            showLoginPrompt(error.message);
        } else {
            console.warn('登录检查遇到问题，但不确定是否需要登录，尝试继续启动:', error.message);
            // 其他错误（如网络错误、服务器错误）直接提示登录
            showLoginPrompt('登录状态异常，请重新登录');
        }
    }
}

// 新增：显示登录提示的函数
function showLoginPrompt(errorMessage) {
    console.log("显示登录提示");
    
    // 隐藏用户头像
    UserProfile.hideUserProfile();
    
    // 保存输入框内容到localStorage
    const elements = State.domElements;
    if (elements.songLinksTextarea && elements.songLinksTextarea.value.trim()) {
        localStorage.setItem('amdl_pending_input', elements.songLinksTextarea.value);
        console.log("已保存用户输入内容，登录后将自动恢复");
    }
    
    // 禁用切换按钮并添加视觉效果
    if (elements.userProfileSwitchContainer && elements.userProfileSkipCheckbox) {
        elements.userProfileSkipCheckbox.disabled = true;
        elements.userProfileSwitchContainer.classList.add('disabled');
        elements.userProfileSwitchContainer.title = '登录后可用';
    }
    
    // 更新状态显示为点击登录（可点击）
    UI.updatePendingTasksBadge("点击登录", "bg-warning");
    
    // 让状态标签可点击
    if (elements.pendingTasksBadge) {
        const badge = elements.pendingTasksBadge;
        
        // 添加可点击样式
        badge.style.cursor = 'pointer';
        badge.style.transition = 'all 0.2s ease';
        badge.title = '点击登录'; // 添加提示文本
        
        // 添加悬停效果
        badge.addEventListener('mouseenter', () => {
            badge.style.filter = 'brightness(1.2)';
            badge.style.transform = 'scale(1.05)';
        });
        
        badge.addEventListener('mouseleave', () => {
            badge.style.filter = 'brightness(1)';
            badge.style.transform = 'scale(1)';
        });
        
        // 添加点击事件
        const handleLoginClick = () => {
            // 再次保存输入内容（防止用户在显示登录提示后继续输入）
            if (elements.songLinksTextarea && elements.songLinksTextarea.value.trim()) {
                localStorage.setItem('amdl_pending_input', elements.songLinksTextarea.value);
            }
            handleLogin();
        };
        
        badge.addEventListener('click', handleLoginClick);
        
        // 存储事件处理器，以便后续清理
        badge._loginClickHandler = handleLoginClick;
    }
    
    // 显示简洁的登录提示，不重复显示错误详情
    const loginMessage = "请先登录以使用AMDL服务。";
    UI.showErrorMessage(loginMessage, true); // 第二个参数为true表示显示登录按钮
}

// 新增：启动应用的函数
function startApplication() {
    console.log("启动应用...");
    
    // 🚀 新增：如果是URL参数打开，延迟启动背景渲染
    if (UrlParams.shouldAutoOpenLogModal()) {
        console.log('[StartApp] 🎯 检测到URL参数，优先处理监视窗口，延迟背景渲染');
        startApplicationDelayed();
        return;
    }
    
    // 正常启动流程
    startApplicationNormal();
}

// 新增：延迟启动应用（URL参数模式）
function startApplicationDelayed() {
    console.log('[StartApp] 🎯 URL参数模式：隐藏页面元素，专注监视窗口');
    
    // 🎯 隐藏所有页面元素，只保留背景和即将展开的监视窗口
    hidePageElementsForModalFocus();
    
    // 最小化初始启动：只启动必要组件
    const elements = State.domElements;
    
    // 启用切换按钮（UI基础功能）
    if (elements.userProfileSwitchContainer && elements.userProfileSkipCheckbox) {
        elements.userProfileSkipCheckbox.disabled = false;
        elements.userProfileSwitchContainer.classList.remove('disabled');
        elements.userProfileSwitchContainer.title = '';
    }
    
    // 恢复暂存的输入内容（但不渲染UI）
    if (elements.songLinksTextarea) {
        const pendingInput = localStorage.getItem('amdl_pending_input');
        if (pendingInput && pendingInput.trim()) {
            elements.songLinksTextarea.value = pendingInput;
            localStorage.removeItem('amdl_pending_input');
        }
    }
    
    // 清理登录状态
    cleanupLoginState();
    
    // 立即检查并打开监视窗口（不等待其他初始化）
    const targetUuid = UrlParams.getMonitorUuid();
    autoOpenLogModalWithRetry(targetUuid, 0);
    
    console.log('[StartApp] ✅ 监视窗口优先启动完成，页面元素已隐藏');
}

// 🎯 新增：隐藏页面元素，专注监视窗口
function hidePageElementsForModalFocus() {
    console.log('[StartApp] 🫥 隐藏页面元素，营造沉浸式体验');
    
    // 隐藏用户头像
    UserProfile.hideUserProfile();
    
    // 检查是否已经有预置的隐藏样式
    const presetStyle = document.getElementById('initial-hide-style');
    if (presetStyle) {
        console.log('[StartApp] ✅ 检测到预置隐藏样式，元素已隐藏');
        
        // 为预置隐藏的元素添加标记，方便后续恢复
        const elementsToHide = [
            'main.form-signin',           // 输入框区域
            '#coverPreviewSection',       // 封面预览区域
            '.gradient-title',            // 主标题
            '#pendingTasks',              // 状态标签
            '.d-inline-block'             // 标题右侧元素容器
        ];
        
        elementsToHide.forEach(selector => {
            const element = document.querySelector(selector);
            if (element) {
                element.dataset.hiddenForModal = 'true'; // 添加标记
            }
        });
        
        return; // 元素已经隐藏，直接返回
    }
    
    // 如果没有预置隐藏样式，手动隐藏元素
    const elementsToHide = [
        'main.form-signin',           // 输入框区域
        '#coverPreviewSection',       // 封面预览区域
        '.gradient-title',            // 主标题
        '#pendingTasks',              // 状态标签
        '.d-inline-block'             // 标题右侧元素容器
    ];
    
    elementsToHide.forEach(selector => {
        const element = document.querySelector(selector);
        if (element) {
            element.style.opacity = '0';
            element.style.pointerEvents = 'none';
            element.style.transition = 'opacity 0.3s ease';
            element.dataset.hiddenForModal = 'true'; // 标记为隐藏状态
        }
    });
    
    // 特殊处理：完全隐藏封面预览区域和输入区域
    const mainFormSignin = document.querySelector('main.form-signin');
    const coverPreviewSection = document.querySelector('#coverPreviewSection');
    
    if (mainFormSignin) {
        mainFormSignin.style.visibility = 'hidden';
    }
    
    if (coverPreviewSection) {
        coverPreviewSection.style.visibility = 'hidden';
    }
    
    console.log('[StartApp] ✅ 页面元素隐藏完成，背景和模糊效果保留');
}

// 🎯 新增：显示页面元素
function showPageElementsAfterModal() {
    console.log('[StartApp] 🎭 监视窗口展开完毕，恢复页面元素');
    
    // 🎯 移除预置的隐藏样式表
    const presetStyle = document.getElementById('initial-hide-style');
    if (presetStyle) {
        presetStyle.remove();
        console.log('[StartApp] 🗑️ 已移除预置隐藏样式表');
    }
    
    // 查找所有被隐藏的元素
    const hiddenElements = document.querySelectorAll('[data-hidden-for-modal="true"]');
    
    // 先恢复主要区域的可见性
    const mainFormSignin = document.querySelector('main.form-signin');
    const coverPreviewSection = document.querySelector('#coverPreviewSection');
    
    if (mainFormSignin) {
        mainFormSignin.style.visibility = '';
    }
    
    if (coverPreviewSection) {
        coverPreviewSection.style.visibility = '';
    }
    
    // 分批恢复元素，创造层次感
    hiddenElements.forEach((element, index) => {
        setTimeout(() => {
            // 先清除隐藏状态
            element.removeAttribute('data-hidden-for-modal');
            
            // 设置初始状态为隐藏
            element.style.opacity = '0';
            element.style.transform = 'translateY(10px)';
            element.style.transition = 'opacity 0.4s ease-out, transform 0.4s ease-out';
            element.style.pointerEvents = '';
            
            // 下一帧开始动画
            requestAnimationFrame(() => {
                element.style.opacity = '';
                element.style.transform = '';
                
                // 动画完成后清理样式
                setTimeout(() => {
                    element.style.transition = '';
                    element.style.opacity = '';
                    element.style.transform = '';
                }, 400);
            });
        }, index * 50); // 每个元素间隔50ms显示，创造层次感
    });
    
    console.log('[StartApp] ✅ 页面元素恢复动画已启动');
    
    // 恢复用户头像显示（如果用户已登录且不是URL参数模式）
    if (!UrlParams.shouldAutoOpenLogModal() && State.getUserName()) {
        UserProfile.showUserProfile();
    }
}

// 新增：正常启动应用（常规模式）
function startApplicationNormal() {
    console.log('[StartApp] 🎯 常规模式：完整启动所有服务');
    
    // 🎯 确保移除可能存在的预置隐藏样式（页面刷新后改变URL的情况）
    const presetStyle = document.getElementById('initial-hide-style');
    if (presetStyle) {
        presetStyle.remove();
        console.log('[StartApp] 🗑️ 常规模式下移除预置隐藏样式');
    }
    
    // 启用切换按钮并移除禁用样式
    const elements = State.domElements;
    if (elements.userProfileSwitchContainer && elements.userProfileSkipCheckbox) {
        elements.userProfileSkipCheckbox.disabled = false;
        elements.userProfileSwitchContainer.classList.remove('disabled');
        elements.userProfileSwitchContainer.title = '';
    }
    
    // 恢复暂存的输入内容
    if (elements.songLinksTextarea) {
        const pendingInput = localStorage.getItem('amdl_pending_input');
        if (pendingInput && pendingInput.trim()) {
            elements.songLinksTextarea.value = pendingInput;
            UI.autoResizeTextarea();
            localStorage.removeItem('amdl_pending_input');
        }
    }
    
    // 清理登录状态
    cleanupLoginState();
    
    // 启动所有服务
    startBackgroundServices();
    
    console.log('[StartApp] ✅ 常规启动完成');
}

// 新增：启动背景服务
function startBackgroundServices() {
    console.log('[StartApp] 🔄 启动背景服务...');
    
    API.startPolling(); // 启动轮询
    UI.updateScrollButtons(); // 初始化滚动按钮状态
    UI.initializeSwipeScroll(); // 初始化移动端滑动滚动
    UI.initializeCustomTooltips(); // 初始化自定义 Tooltip
    UI.initializeHoverScaleEffect(); // 初始化 JS 悬停缩放
    
    // 新增：启动通知系统
    initNotificationSystem();
    
    // 简化的缓存初始化日志
    console.log('[StartApp] 📦 图片缓存初始化完成');
    CacheDebug.logCacheStatus(); // 初始缓存状态
    
    console.log('[StartApp] ✅ 所有背景服务启动完成');
}

// 新增：清理登录状态的函数
function cleanupLoginState() {
    const elements = State.domElements;
    if (elements.pendingTasksBadge) {
        const badge = elements.pendingTasksBadge;
        
        // 移除登录点击事件监听器
        if (badge._loginClickHandler) {
            badge.removeEventListener('click', badge._loginClickHandler);
            badge._loginClickHandler = null;
        }
        
        // 恢复正常样式
        badge.style.cursor = '';
        badge.style.transition = '';
        badge.style.filter = '';
        badge.style.transform = '';
        badge.title = '';
        
        // 移除悬停事件监听器（通过克隆元素来清理所有事件监听器）
        const newBadge = badge.cloneNode(true);
        badge.parentNode.replaceChild(newBadge, badge);
        
        // 更新元素引用
        elements.pendingTasksBadge = newBadge;
        State.setDomElements(elements);
    }
}

// 将原有的 sendRequest 函数重命名为 handleDirectRequest
async function handleDirectRequest() {
    const elements = State.domElements;
    const input = elements.songLinksTextarea.value.trim();
    
    if (!input) {
        UI.showErrorMessage('请输入内容');
        return;
    }

    // 拆分多个链接，支持换行、逗号和分号分割
    const links = input.split(/[,;\n]/)
        .map(link => link.trim())
        .filter(link => link.length > 0);
    
    if (links.length === 0) {
        UI.showErrorMessage('请输入有效的链接');
        return;
    }

    console.log(`处理 ${links.length} 个链接:`, links);

    // 获取当前跳过检查状态
    const skipCheck = State.getSkipCheck();
    console.log('[DirectRequest] 当前跳过检查状态:', skipCheck);

    // 更新按钮状态
    elements.sendButton.disabled = true;
    elements.sendButton.querySelector('strong').textContent = '发送中...';

    try {
        // 将每个链接转换为任务格式，添加 skip_check 参数
        const tasks = links.map(link => ({ 
            link: link,
            skip_check: skipCheck
        }));
        
        const response = await fetch('/api/task', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(tasks)
        });

        if (!response.ok) {
            throw new Error(`请求失败，状态码: ${response.status}`);
        }

        // 解析响应数据
        const responseData = await response.json();
        console.log('服务器响应:', responseData);

        // 清空输入框
        elements.songLinksTextarea.value = '';
        UI.autoResizeTextarea();

        // 根据响应状态显示不同的消息
        const { status, message, accepted_count, failed_count, failure_summary } = responseData;
        
        // HTML转义函数，防止XSS攻击
        const escapeHtml = (text) => {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        };
        
        if (status === 'success') {
            // 全部成功
            const successMessage = accepted_count === 1 
                ? '你的歌曲链接已成功发送至AMDL，请稍等片刻等待处理。'
                : `你的 ${accepted_count} 个歌曲链接已成功发送至AMDL，请稍等片刻等待处理。`;
            
            // 设置成功标题
            const successModalLabel = elements.succeedModalElement.querySelector('#succeedModalLabel');
            if (successModalLabel) {
                successModalLabel.textContent = '发送成功';
            }
            
            elements.successMessageElement.innerHTML = escapeHtml(successMessage);
            State.succeedModalInstance.show();
            
        } else if (status === 'partial_success') {
            // 部分成功
            let partialMessage = `已成功发送 ${accepted_count} 个链接至AMDL，${failed_count} 个链接发送失败。<br><br>`;
            partialMessage += `服务器消息：${escapeHtml(message)}`;
            
            // 添加失败原因摘要
            if (failure_summary && Object.keys(failure_summary).length > 0) {
                partialMessage += '<br><br>失败原因统计：';
                for (const [reason, count] of Object.entries(failure_summary)) {
                    partialMessage += `<br>• ${escapeHtml(reason)}: ${count} 个`;
                }
            }
            
            // 设置部分成功标题
            const successModalLabel = elements.succeedModalElement.querySelector('#succeedModalLabel');
            if (successModalLabel) {
                successModalLabel.textContent = '部分成功';
            }
            
            elements.successMessageElement.innerHTML = partialMessage;
            State.succeedModalInstance.show();
            
        } else if (status === 'failure') {
            // 全部失败
            let failureMessage = `所有 ${failed_count} 个链接都发送失败。<br><br>`;
            failureMessage += `服务器消息：${escapeHtml(message)}`;
            
            // 添加失败原因摘要
            if (failure_summary && Object.keys(failure_summary).length > 0) {
                failureMessage += '<br><br>失败原因统计：';
                for (const [reason, count] of Object.entries(failure_summary)) {
                    failureMessage += `<br>• ${escapeHtml(reason)}: ${count} 个`;
                }
            }
            
            // 设置失败标题
            const failedModalLabel = elements.failedModalElement.querySelector('#failedModalLabel');
            if (failedModalLabel) {
                failedModalLabel.textContent = '发送失败';
            }
            
            elements.errorMessageElement.innerHTML = failureMessage;
            State.failedModalInstance.show();
            
        } else {
            // 未知状态，显示原始消息
            const unknownMessage = escapeHtml(message || `发送完成，接受 ${accepted_count || 0} 个，失败 ${failed_count || 0} 个。`);
            
            if ((accepted_count || 0) > 0) {
                // 设置未知成功状态标题
                const successModalLabel = elements.succeedModalElement.querySelector('#succeedModalLabel');
                if (successModalLabel) {
                    successModalLabel.textContent = '操作完成';
                }
                elements.successMessageElement.innerHTML = unknownMessage;
                State.succeedModalInstance.show();
            } else {
                // 设置未知失败状态标题
                const failedModalLabel = elements.failedModalElement.querySelector('#failedModalLabel');
                if (failedModalLabel) {
                    failedModalLabel.textContent = '操作失败';
                }
                elements.errorMessageElement.innerHTML = unknownMessage;
                State.failedModalInstance.show();
            }
        }

    } catch (error) {
        console.error('发送请求失败:', error);
        UI.showErrorMessage(`发送失败: ${error.message}`);
    } finally {
        // 恢复按钮状态
        elements.sendButton.disabled = false;
        elements.sendButton.querySelector('strong').textContent = '发送请求';
    }
}

// 导出函数供其他模块使用
window.sendRequest = handleDirectRequest;

// 切换跳过状态
export function toggleSkipState() {
    const currentState = State.getSkipState();
    const newState = !currentState;
    State.setSkipState(newState);
    
    // 更新状态显示
    const currentUser = State.getUserName();
    const isLoggedIn = currentUser && currentUser.trim() !== "";
    
    const statusText = newState ? "已跳过" : "准备就绪";
    const statusClass = newState ? "bg-info" : "bg-secondary";
    
    // 对于准备就绪状态，如果已登录则启用音乐服务器跳转
    const enableJump = !newState && isLoggedIn;
    UI.updatePendingTasksBadge(statusText, statusClass + " paused", enableJump);  // 添加 paused 类
    
    // 1秒后移除 paused 类
    setTimeout(() => {
        UI.updatePendingTasksBadge(statusText, statusClass, enableJump);
    }, 1000);
}

// 新增：检查URL参数并自动打开日志监视窗口
function checkAndAutoOpenLogModal() {
    if (UrlParams.shouldAutoOpenLogModal()) {
        console.log('🚀 检测到URL参数，立即打开日志监视窗口');
        
        const targetUuid = UrlParams.getMonitorUuid();
        
        // 🚀 优化：立即执行，无延迟！
        autoOpenLogModalWithRetry(targetUuid, 0);
    }
}

// 新增：自动打开日志监视窗口的重试逻辑
function autoOpenLogModalWithRetry(targetUuid, retryCount) {
    const maxRetries = 3; // 🚀 减少重试次数：5次→3次
    const retryInterval = 50; // 🚀 极致优化：100ms→50ms
    
    if (retryCount >= maxRetries) {
        console.warn('[AutoOpen] 自动打开日志监视窗口失败：超过最大重试次数');
        
        // 超过重试次数后，如果指定了UUID但找不到任务，清除URL参数
        if (targetUuid) {
            console.log('[AutoOpen] 指定的任务UUID不存在，清除URL参数');
            safelyCleanUrlParams('任务不存在');
        }
        
        return;
    }
    
    // 🚀 极致优化：最快速的依赖检查
    if (!State.logModalInstance || !State.domElements.logModalElement) {
        // 这种情况应该很少发生，因为我们已经在Modal创建后立即执行
        console.log(`[AutoOpen] 快速重试 ${retryCount + 1}/${maxRetries}：Modal实例准备中...`);
        setTimeout(() => autoOpenLogModalWithRetry(targetUuid, retryCount + 1), retryInterval);
        return;
    }
    
    // 🚀 终极优化：如果没有任务数据且是第一次重试，主动获取一次
    const allTasks = Array.from(State.latestTaskMap.values());
    if (allTasks.length === 0 && retryCount === 0) {
        console.log('[AutoOpen] ⚡ 主动获取任务数据，加速响应...');
        
        // 主动发起一次任务数据获取
        fetch('/api/task')
            .then(response => response.json())
            .then(data => {
                if (data && Array.isArray(data)) {
                    // 快速更新任务数据
                    const taskMap = new Map();
                    data.forEach(task => {
                        if (task && task.uuid) {
                            taskMap.set(task.uuid, task);
                        }
                    });
                    State.setLatestTaskMap(taskMap);
                    console.log(`[AutoOpen] ⚡ 快速获取到 ${data.length} 个任务，继续处理...`);
                    
                    // 立即重新尝试，无延迟
                    autoOpenLogModalWithRetry(targetUuid, retryCount + 1);
                } else {
                    // 获取失败，正常重试
                    setTimeout(() => autoOpenLogModalWithRetry(targetUuid, retryCount + 1), retryInterval);
                }
            })
            .catch(error => {
                console.log('[AutoOpen] 快速获取任务数据失败，继续重试:', error.message);
                setTimeout(() => autoOpenLogModalWithRetry(targetUuid, retryCount + 1), retryInterval);
            });
        return;
    }
    
    let taskToShow = null;
    
    if (targetUuid) {
        // 如果指定了UUID，尝试找到对应的任务
        taskToShow = State.latestTaskMap.get(targetUuid);
        
        if (!taskToShow) {
            // 🚀 极致优化：更快放弃策略
            if (allTasks.length === 0 && retryCount >= 1) { // 1次重试后就放弃
                console.log('[AutoOpen] 快速放弃：无任务数据，清除URL参数');
                safelyCleanUrlParams('无任务数据');
                return;
            }
            
            console.log(`[AutoOpen] 快速重试 ${retryCount + 1}/${maxRetries}：等待任务 ${targetUuid}...`);
            setTimeout(() => autoOpenLogModalWithRetry(targetUuid, retryCount + 1), retryInterval);
            return;
        }
    } else {
        // 没有指定UUID，查找合适的任务显示（优先运行中的任务）
        const tasks = Array.from(State.latestTaskMap.values());
        
        taskToShow = tasks.find(t => t.status === 'running') ||
                     tasks.filter(t => t.status === 'error').sort((a, b) => (b.added_timestamp || 0) - (a.added_timestamp || 0))[0] ||
                     tasks.filter(t => t.status === 'finish').sort((a, b) => (b.added_timestamp || 0) - (a.added_timestamp || 0))[0];
        
        if (!taskToShow && tasks.length === 0) {
            console.log(`[AutoOpen] 快速重试 ${retryCount + 1}/${maxRetries}：等待任务列表...`);
            setTimeout(() => autoOpenLogModalWithRetry(targetUuid, retryCount + 1), retryInterval);
            return;
        }
        
        // 如果没有合适的任务显示，清除URL参数
        if (!taskToShow) {
            console.log('[AutoOpen] 快速放弃：无合适任务，清除URL参数');
            safelyCleanUrlParams('无合适任务');
            return;
        }
    }
    
    // 🚀 找到任务，闪电打开Modal
    console.log(`[AutoOpen] ⚡ 闪电打开监视窗口！${taskToShow ? ` 任务: ${taskToShow.uuid}` : ''}`);
    
    if (taskToShow && taskToShow.uuid) {
        State.setFocusedTaskUuid(taskToShow.uuid);
        State.domElements.logModalElement.dataset.showingUuid = taskToShow.uuid;
        UrlParams.setMonitorStatus(true, taskToShow.uuid);
    } else {
        State.setFocusedTaskUuid(null);
        State.domElements.logModalElement.dataset.showingUuid = '';
        UrlParams.setMonitorStatus(true);
    }
    
    // 🚀 URL参数打开时使用中心放大动画
    showModalWithScaleAnimation();
}

// 新增：专用于URL参数自动打开Modal的函数（从中心放大动画）
function showModalWithScaleAnimation() {
    const modalElement = State.domElements.logModalElement;
    const modalDialog = modalElement.querySelector('.modal-dialog');
    
    console.log('[AutoOpen] ⚡ 使用高性能中心放大动画显示Modal');
    
    // 隐藏用户头像（URL参数自动打开时）
    UserProfile.hideUserProfile();
    
    // 设置标记，避免与shown.bs.modal事件冲突
    modalElement.dataset.isAutoOpening = 'true';
    
    // 添加动画性能优化类
    modalElement.classList.add('modal-scale-animation', 'modal-fixed-height');
    
    // 设置初始高度，防止图片加载后高度变化
    modalDialog.style.minHeight = '500px';
    modalDialog.style.height = 'auto';
    
    // 临时禁用Bootstrap的fade动画
    const hadFadeClass = modalElement.classList.contains('fade');
    if (hadFadeClass) {
        modalElement.classList.remove('fade');
    }
    
    // 设置初始状态（由CSS控制）
    modalDialog.style.transform = 'scale(0.5) translateZ(0)';
    modalDialog.style.opacity = '0';
    
    // 使用Bootstrap的show()方法，但动画已被禁用
    State.logModalInstance.show();
    
    // 手动处理Modal内容更新（因为跳过了shown.bs.modal）
    const taskUuidToShow = State.focusedTaskUuid;
    if (taskUuidToShow) {
        const taskData = State.latestTaskMap.get(taskUuidToShow);
        
        if (taskData) {
            // 先更新基础信息（封面、用户等）
            ModalHandler.updateLogModalInfo(taskData).then(() => {
                // 使用快速渲染模式：只渲染前3个音轨
                ModalHandler.renderModalContentFromTaskData(taskData, { fastRender: true });
                
                // 如果有更多音轨需要渲染，延迟启动后续渲染
                if (State.getIsProgressiveRendering()) {
                    setTimeout(() => {
                        if (State.isLogModalActive && State.focusedTaskUuid === taskUuidToShow) {
                            console.log(`[AutoOpen] 开始渲染剩余音轨 for UUID: ${taskUuidToShow}`);
                            ModalHandler.continueRenderingTracks(taskData);
                        }
                    }, 400); // 等待动画完成后再开始渲染
                }
            });
        } else {
            console.warn(`[AutoOpen] 任务数据不存在 UUID: ${taskUuidToShow}`);
            ModalHandler.updateLogModalInfo(null);
        }
    } else {
        console.warn("[AutoOpen] 无focusedTaskUuid，显示无任务状态");
        ModalHandler.updateLogModalInfo(null);
    }
    
    // 使用CSS动画执行放大效果（下一帧执行，确保DOM已更新）
    requestAnimationFrame(() => {
        modalElement.classList.add('animate-in');
    });
    
    // 动画完成后清理
    const cleanupAnimation = () => {
        // 移除动画类
        modalElement.classList.remove('modal-scale-animation', 'animate-in');
        
        // 清除内联样式
        modalDialog.style.transform = '';
        modalDialog.style.opacity = '';
        
        // 恢复Bootstrap的fade类
        if (hadFadeClass) {
            modalElement.classList.add('fade');
        }
        
        // 清除自动打开标记
        delete modalElement.dataset.isAutoOpening;
        
        console.log('[AutoOpen] ✅ 高性能中心放大动画完成');
        
        // 🎯 新增：恢复页面元素显示
        showPageElementsAfterModal();
        
        // 🚀 启动背景服务
        console.log('[AutoOpen] 🔄 启动背景服务...');
        startBackgroundServices();
    };
    
    // 监听动画结束事件
    const handleAnimationEnd = (event) => {
        if (event.target === modalDialog && event.animationName === 'modalScaleIn') {
            modalDialog.removeEventListener('animationend', handleAnimationEnd);
            cleanupAnimation();
        }
    };
    
    modalDialog.addEventListener('animationend', handleAnimationEnd);
    
    // 备用清理（防止动画事件未触发）
    setTimeout(() => {
        if (modalElement.classList.contains('animate-in')) {
            modalDialog.removeEventListener('animationend', handleAnimationEnd);
            cleanupAnimation();
        }
    }, 500);
    
    console.log('[AutoOpen] ✅ 高性能Modal动画已启动');
    
    // 确保滚动位置位于顶部
    setTimeout(() => {
        const modalBody = modalElement.querySelector('.modal-body');
        const scrollableArea = modalBody ? modalBody.querySelector('.log-tracks-scrollable') : null;
        
        // 重置Modal body滚动位置
        if (modalBody) {
            modalBody.scrollTop = 0;
        }
        
        // 重置音轨列表滚动位置
        if (scrollableArea) {
            scrollableArea.scrollTop = 0;
        }
        
        console.log('[AutoOpen] ✅ 滚动位置已重置到顶部');
    }, 100); // 等待DOM更新
}

window.checkUserLoginAndStartApp = checkUserLoginAndStartApp;

// 将 handleSubmit 函数暴露到全局
window.handleSubmit = handleSubmit;

// 将 UI 模块暴露到全局
window.UI = UI;

// 将静默预加载函数暴露到全局
window.silentPreloadMoreResults = silentPreloadMoreResults;

// 新增：静默预加载搜索更多内容
async function silentPreloadMoreResults() {
    try {
        // 检查当前搜索状态
        const searchState = State.getSearchState();
        if (!searchState || !searchState.searchResults || searchState.searchResults.length === 0) {
            console.log('[SearchPreload] 无当前搜索状态，跳过预加载');
            return;
        }

        // 获取当前搜索词和偏移量
        const currentSearchTerm = searchState.originalQuery || '';
        const currentResultCount = searchState.searchResults.length;
        
        if (!currentSearchTerm) {
            console.log('[SearchPreload] 无搜索词，跳过预加载');
            return;
        }

        // 检查是否已经有预加载缓存
        const existingCache = sessionStorage.getItem('amdl_search_preload_cache');
        if (existingCache) {
            try {
                const cacheData = JSON.parse(existingCache);
                // 如果是相同搜索词且偏移量匹配，不重复预加载
                if (cacheData.searchTerm === currentSearchTerm && 
                    (cacheData.offset - 8) === currentResultCount) {
                    console.log('[SearchPreload] 已存在有效缓存，跳过预加载');
                    return;
                }
            } catch (e) {
                console.warn('[SearchPreload] 解析现有缓存失败，清理缓存');
                sessionStorage.removeItem('amdl_search_preload_cache');
            }
        }

        console.log('[SearchPreload] 🚀 开始预加载下一页内容');
        console.log('[SearchPreload] 搜索词:', currentSearchTerm);
        console.log('[SearchPreload] 当前结果数:', currentResultCount);

        // 构造预加载请求
        const preloadOffset = currentResultCount; // 下一页的偏移量
        const preloadParams = new URLSearchParams({
            term: currentSearchTerm,
            limit: '8', // 每页8个结果
            offset: preloadOffset.toString(),
            types: 'albums',
        });

        // 发起预加载请求
        const response = await fetch(`/api/search?${preloadParams}`, {
            method: 'GET',
            headers: {
                'Content-Type': 'application/json'
            }
        });

        if (!response.ok) {
            throw new Error(`预加载请求失败: ${response.status}`);
        }

        const preloadData = await response.json();
        
        // 检查预加载结果
        if (!preloadData.results || !preloadData.results.albums || 
            !preloadData.results.albums.data || preloadData.results.albums.data.length === 0) {
            console.log('[SearchPreload] 预加载无更多结果，不缓存');
            return;
        }

        // 将预加载数据存储到缓存
        const cacheData = {
            searchTerm: currentSearchTerm,
            offset: preloadOffset + 8, // 下次搜索的偏移量
            data: preloadData,
            timestamp: Date.now(),
            resultCount: preloadData.results.albums.data.length
        };

        try {
            sessionStorage.setItem('amdl_search_preload_cache', JSON.stringify(cacheData));
            console.log('[SearchPreload] ✅ 预加载完成，已缓存', preloadData.results.albums.data.length, '个结果');
            console.log('[SearchPreload] 缓存偏移量:', preloadOffset, '-> 下次偏移量:', preloadOffset + 8);
        } catch (e) {
            console.warn('[SearchPreload] 缓存存储失败:', e.message);
        }

    } catch (error) {
        console.warn('[SearchPreload] 预加载失败:', error.message);
        // 预加载失败不影响正常功能，只是性能优化
    }
}

// 图片搜索功能初始化
function initImageSearchFeature() {
    const elements = State.getDomElements();
    
    // 初始化图片上传区域拖拽功能
    initImageDragAndDrop();
    
    // 初始化输入框拖拽功能
    initTextareaDragAndDrop();
    
    // 文件选择事件
    if (elements.imageFileUpload) {
        elements.imageFileUpload.addEventListener('change', handleImageFileSelect);
    }
    
    // 上传区域点击事件
    if (elements.imageUploadArea) {
        elements.imageUploadArea.addEventListener('click', (e) => {
            // 防止重复触发 - 如果点击的是文件输入框本身，不再触发
            if (e.target === elements.imageFileUpload) {
                return;
            }
            // 如果正在处理文件，不响应点击
            if (elements.imageUploadArea.dataset.processing === 'true') {
                return;
            }
            
            // Safari兼容性：确保在用户交互上下文中触发文件选择
            if (elements.imageFileUpload) {
                try {
                    // 对于Safari，确保文件输入框是可见且可交互的
                    const fileInput = elements.imageFileUpload;
                    
                    // 临时使文件输入框可见，以确保Safari能正确处理点击
                    const originalStyle = {
                        position: fileInput.style.position,
                        left: fileInput.style.left,
                        opacity: fileInput.style.opacity,
                        visibility: fileInput.style.visibility,
                        pointerEvents: fileInput.style.pointerEvents
                    };
                    
                    // 设置为可见但透明
                    fileInput.style.position = 'absolute';
                    fileInput.style.left = '0';
                    fileInput.style.opacity = '0';
                    fileInput.style.visibility = 'visible';
                    fileInput.style.pointerEvents = 'auto';
                    
                    // 触发点击
                    fileInput.click();
                    
                    // 恢复原始样式
                    setTimeout(() => {
                        fileInput.style.position = originalStyle.position;
                        fileInput.style.left = originalStyle.left;
                        fileInput.style.opacity = originalStyle.opacity;
                        fileInput.style.visibility = originalStyle.visibility;
                        fileInput.style.pointerEvents = originalStyle.pointerEvents;
                    }, 100);
                    
                } catch (error) {
                    console.error('触发文件选择失败:', error);
                    showImageError('无法打开文件选择器，请直接点击"选择文件"文字或拖拽文件到此区域');
                }
            }
        });
        
        // 为Safari添加额外的label点击处理
        const uploadLabel = elements.imageUploadArea.querySelector('.upload-label');
        if (uploadLabel) {
            uploadLabel.addEventListener('click', (e) => {
                // 确保label的点击事件能够正确传递到文件输入框
                e.stopPropagation();
                console.log('Safari: 通过label触发文件选择');
            });
        }
    }
    
    // 偏移搜索按钮事件
    if (elements.offsetSearchBtn) {
        elements.offsetSearchBtn.addEventListener('click', handleOffsetSearch);
    }
    
    // 下载选中按钮事件
    if (elements.downloadSelectedBtn) {
        elements.downloadSelectedBtn.addEventListener('click', handleDownloadSelected);
    }
    
    // Apple Music按钮事件
    if (elements.appleMusicsBtn) {
        elements.appleMusicsBtn.addEventListener('click', handleAppleMusicOpen);
    }
    
    console.log('图片搜索功能初始化完成');

    // 1. 在initImageSearchFeature中，添加对onlyAlbumTitleCheckbox的监听
    const onlyAlbumTitleCheckbox = document.getElementById('onlyAlbumTitleCheckbox');
    if (onlyAlbumTitleCheckbox) {
        onlyAlbumTitleCheckbox.addEventListener('change', () => {
            // 重新搜索当前选中的识别结果
            const results = State.getRecognitionResults();
            const selected = document.querySelector('.result-item.selected');
            if (results && selected) {
                const idx = selected.dataset.index;
                autoSearchResult(results[idx]);
            }
        });
    }
}

// 初始化图片上传区域拖拽功能
function initImageDragAndDrop() {
    const elements = State.getDomElements();
    const uploadArea = elements.imageUploadArea;
    
    if (!uploadArea) return;
    
    // 防止默认拖拽行为
    ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
        uploadArea.addEventListener(eventName, preventDefaults, false);
        document.body.addEventListener(eventName, preventDefaults, false);
    });
    
    function preventDefaults(e) {
        e.preventDefault();
        e.stopPropagation();
    }
    
    // 拖拽进入和悬停效果
    ['dragenter', 'dragover'].forEach(eventName => {
        uploadArea.addEventListener(eventName, () => {
            uploadArea.classList.add('dragover');
        }, false);
    });
    
    // 拖拽离开效果
    ['dragleave', 'drop'].forEach(eventName => {
        uploadArea.addEventListener(eventName, () => {
            uploadArea.classList.remove('dragover');
        }, false);
    });
    
    // 处理文件拖放
    uploadArea.addEventListener('drop', handleImageDrop, false);
}

// 初始化输入框拖拽功能
function initTextareaDragAndDrop() {
    const elements = State.getDomElements();
    const textarea = elements.songLinksTextarea;
    
    if (!textarea) return;
    
    // 防止默认拖拽行为
    ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
        textarea.addEventListener(eventName, preventDefaults, false);
    });
    
    function preventDefaults(e) {
        e.preventDefault();
        e.stopPropagation();
    }
    
    // 拖拽进入和悬停效果
    ['dragenter', 'dragover'].forEach(eventName => {
        textarea.addEventListener(eventName, () => {
            textarea.classList.add('drag-over');
        }, false);
    });
    
    // 拖拽离开效果
    ['dragleave', 'drop'].forEach(eventName => {
        textarea.addEventListener(eventName, () => {
            textarea.classList.remove('drag-over');
        }, false);
    });
    
    // 处理文件拖放到输入框
    textarea.addEventListener('drop', handleTextareaDrop, false);
}

// 处理图片拖放
function handleImageDrop(e) {
    const dt = e.dataTransfer;
    const files = dt.files;
    
    if (files.length > 0) {
        const file = files[0];
        if (file.type.startsWith('image/')) {
            handleImageFile(file);
        } else {
            showImageError('请拖拽图片文件');
        }
    }
}

// 处理输入框拖放
function handleTextareaDrop(e) {
    const dt = e.dataTransfer;
    const files = dt.files;
    
    if (files.length > 0) {
        const file = files[0];
        if (file.type.startsWith('image/')) {
            // 如果拖拽的是图片，打开图片搜索弹窗
            const elements = State.getDomElements();
            const instances = State.getModalInstances();
            
            if (instances.imageSearchModalInstance) {
                instances.imageSearchModalInstance.show();
                // 延迟处理文件以确保弹窗完全打开
                setTimeout(() => {
                    handleImageFile(file);
                }, 300);
            }
        }
    }
}

// 处理文件选择
function handleImageFileSelect(event) {
    console.log('Safari: 文件选择事件触发', event);
    
    const files = event.target.files;
    console.log('Safari: 选中的文件数量:', files ? files.length : 0);
    
    if (files && files.length > 0) {
        console.log('Safari: 开始处理文件:', files[0].name);
        handleImageFile(files[0]);
    } else {
        console.warn('Safari: 未检测到文件或文件列表为空');
        // Safari有时会出现文件选择但无法读取的情况
        showImageError('文件选择失败，请重试或尝试拖拽文件到上传区域');
    }
    
    // 重置input值，允许重复选择同一个文件
    try {
        event.target.value = '';
    } catch (error) {
        console.warn('Safari: 重置文件输入框失败:', error);
    }
}

// 处理图片文件
function handleImageFile(file) {
    const elements = State.getDomElements();
    
    console.log('处理图片文件:', file.name, '类型:', file.type, '大小:', file.size);
    
    // 检查是否正在处理文件
    if (elements.imageUploadArea.dataset.processing === 'true') {
        console.log('正在处理其他文件，跳过本次上传');
        return;
    }
    
    // 标记为正在处理
    elements.imageUploadArea.dataset.processing = 'true';
    elements.imageUploadArea.style.pointerEvents = 'none';
    elements.imageUploadArea.style.opacity = '0.7';
    
    // 重置UI状态
    hideImageError();
    hideImageLoading();
    
    // 验证文件类型
    if (!file.type.startsWith('image/')) {
        showImageError('请选择图片文件 (例如 PNG, JPG)');
        resetUploadArea();
        return;
    }
    
    // 验证文件大小 (15MB)
    const maxFileSize = 15 * 1024 * 1024;
    if (file.size > maxFileSize) {
        const fileSizeMB = (file.size / (1024 * 1024)).toFixed(1);
        showImageError(`文件过大：${fileSizeMB}MB。最大允许文件大小为 15MB`);
        resetUploadArea();
        return;
    }
    
    // 显示图片预览 - 移到验证通过后，不再预先隐藏
    showImagePreview(file);
    
    // 上传并识别图片
    uploadAndRecognizeImage(file);
}

// 重置上传区域状态
function resetUploadArea() {
    const elements = State.getDomElements();
    elements.imageUploadArea.dataset.processing = 'false';
    elements.imageUploadArea.style.pointerEvents = '';
    elements.imageUploadArea.style.opacity = '';
}

// 重置图片搜索弹窗状态
function resetImageSearchModal() {
    console.log('重置图片搜索弹窗状态');
    
    // 重置上传区域
    resetUploadArea();
    
    // 隐藏所有UI元素
    hideImagePreview();
    hideImageLoading();
    hideImageError();
    
    // 重置文件输入框 - Safari兼容性改进
    const elements = State.getDomElements();
    if (elements.imageFileUpload) {
        try {
            // Safari可能需要特殊处理来重置文件输入框
            elements.imageFileUpload.value = '';
            
            // 为Safari添加额外的重置方法
            if (navigator.userAgent.toLowerCase().indexOf('safari') !== -1 && 
                navigator.userAgent.toLowerCase().indexOf('chrome') === -1) {
                // 仅在Safari浏览器中执行
                const form = elements.imageFileUpload.form || document.createElement('form');
                if (!elements.imageFileUpload.form) {
                    // 如果文件输入框不在form中，创建临时form进行重置
                    const tempForm = document.createElement('form');
                    const parent = elements.imageFileUpload.parentNode;
                    const nextSibling = elements.imageFileUpload.nextSibling;
                    
                    tempForm.appendChild(elements.imageFileUpload);
                    tempForm.reset();
                    
                    // 恢复文件输入框到原位置
                    if (nextSibling) {
                        parent.insertBefore(elements.imageFileUpload, nextSibling);
                    } else {
                        parent.appendChild(elements.imageFileUpload);
                    }
                } else {
                    // 如果在form中，直接重置
                    form.reset();
                }
            }
            
            console.log('Safari: 文件输入框重置完成');
        } catch (error) {
            console.warn('Safari: 文件输入框重置失败:', error);
        }
    }
    
    console.log('图片搜索弹窗状态重置完成');
}

// 显示图片预览
function showImagePreview(file) {
    const elements = State.getDomElements();
    
    // 先隐藏之前的预览（如果存在）
    hideImagePreview();
    
    const reader = new FileReader();
    
    reader.onload = (e) => {
        try {
            if (e.target && e.target.result) {
                elements.imagePreview.src = e.target.result;
                elements.imagePreviewContainer.classList.remove('hidden');
                console.log('图片预览显示成功');
            } else {
                console.error('FileReader 结果为空');
                showImageError('图片读取失败，请重试');
                resetUploadArea();
            }
        } catch (error) {
            console.error('显示图片预览时出错:', error);
            showImageError('图片预览显示失败');
            resetUploadArea();
        }
    };
    
    reader.onerror = (e) => {
        console.error('FileReader 读取文件时出错:', e);
        showImageError('文件读取失败，请重试');
        resetUploadArea();
    };
    
    reader.onabort = (e) => {
        console.log('FileReader 读取被中断:', e);
        showImageError('文件读取被中断');
        resetUploadArea();
    };
    
    // 开始读取文件
    try {
        reader.readAsDataURL(file);
    } catch (error) {
        console.error('启动文件读取时出错:', error);
        showImageError('无法读取选中的文件');
        resetUploadArea();
    }
}

// 隐藏图片预览
function hideImagePreview() {
    const elements = State.getDomElements();
    elements.imagePreviewContainer.classList.add('hidden');
}

// 显示加载状态
function showImageLoading() {
    const elements = State.getDomElements();
    elements.imageLoadingIndicator.classList.remove('hidden');
}

// 隐藏加载状态
function hideImageLoading() {
    const elements = State.getDomElements();
    elements.imageLoadingIndicator.classList.add('hidden');
}

// 显示错误信息
function showImageError(message) {
    const elements = State.getDomElements();
    elements.imageErrorText.textContent = message;
    elements.imageErrorMessage.classList.remove('hidden');
}

// 隐藏错误信息
function hideImageError() {
    const elements = State.getDomElements();
    elements.imageErrorMessage.classList.add('hidden');
}

// 上传并识别图片
async function uploadAndRecognizeImage(file) {
    const elements = State.getDomElements();
    const instances = State.getModalInstances();
    
    showImageLoading();
    hideImageError();
    
    const formData = new FormData();
    formData.append('file', file);
    
    try {
        console.log('发送图片识别请求...');
        const response = await fetch('/api/gemini', {
            method: 'POST',
            body: formData
        });
        
        if (!response.ok) {
            let errorMessage = `服务器错误: ${response.status} ${response.statusText}`;
            
            if (response.status === 413) {
                errorMessage = '文件过大，服务器无法处理。请选择较小的图片文件';
            } else {
                try {
                    const errorData = await response.text();
                    if (errorData) {
                        errorMessage += `. ${errorData}`;
                    }
                } catch (e) {
                    // 忽略解析错误
                }
            }
            
            throw new Error(errorMessage);
        }
        
        const results = await response.json();
        console.log('图片识别结果:', results);
        
        hideImageLoading();
        resetUploadArea(); // 识别成功后重置上传区域
        
        if (results && results.length > 0) {
            // 关闭图片搜索弹窗
            instances.imageSearchModalInstance.hide();
            
            // 显示识别结果弹窗
            showRecognitionResults(results);
        } else {
            showImageError('识别完成，但未找到相关专辑信息');
        }
        
    } catch (error) {
        console.error('图片识别失败:', error);
        hideImageLoading();
        resetUploadArea(); // 识别失败后也要重置上传区域
        showImageError(`识别失败: ${error.message}`);
    }
}

// 显示识别结果
function showRecognitionResults(results) {
    const elements = State.getDomElements();
    const instances = State.getModalInstances();
    
    // 保存识别结果到状态
    State.setRecognitionResults(results);
    
    // 渲染识别结果
    renderRecognitionResults(results);
    
    // 自动搜索第一个结果
    if (results.length > 0) {
        autoSearchFirstResult(results[0]);
    }
    
    // 显示结果弹窗
    instances.recognitionResultModalInstance.show();
}

// 渲染识别结果
function renderRecognitionResults(results) {
    const elements = State.getDomElements();
    const container = elements.recognitionResults;
    
    container.innerHTML = '';
    
    results.forEach((item, index) => {
        const resultDiv = document.createElement('div');
        resultDiv.className = `result-item ${index === 0 ? 'selected' : ''}`;
        resultDiv.dataset.index = index;
        
        resultDiv.innerHTML = `
            <h6 class="mb-2 text-primary">${item.album_title || '未知专辑'}</h6>
            <p class="mb-1 text-muted">${item.artist_name || '未知艺术家'}</p>
            <small class="text-secondary">点击选择此结果进行搜索</small>
        `;
        
        // 点击事件
        resultDiv.addEventListener('click', () => {
            // 移除其他选中状态
            container.querySelectorAll('.result-item').forEach(el => {
                el.classList.remove('selected');
            });
            
            // 选中当前项
            resultDiv.classList.add('selected');
            
            // 搜索选中项
            autoSearchResult(item);
        });
        
        container.appendChild(resultDiv);
    });
}

// 自动搜索第一个结果
function autoSearchFirstResult(result) {
    autoSearchResult(result);
}

// 自动搜索结果
async function autoSearchResult(result) {
    const elements = State.getDomElements();
    const searchContainer = elements.autoSearchResults;
    const onlyAlbumTitleCheckbox = document.getElementById('onlyAlbumTitleCheckbox');
    // 显示加载状态
    searchContainer.innerHTML = `
        <div class="d-flex justify-content-center">
            <div class="spinner-border spinner-border-sm" role="status">
                <span class="visually-hidden">Loading...</span>
            </div>
            <span class="ms-2">搜索中...</span>
        </div>
    `;
    try {
        // 构造搜索查询
        let query = '';
        if (onlyAlbumTitleCheckbox && onlyAlbumTitleCheckbox.checked) {
            query = result.album_title || '';
        } else {
            query = `${result.album_title || ''} ${result.artist_name || ''}`.trim();
        }
        if (!query) {
            throw new Error('无法构造搜索查询');
        }
        console.log('自动搜索查询:', query);
        // 使用GET方法，与现有搜索API保持一致
        const apiUrl = `/api/search?term=${encodeURIComponent(query)}&types=albums&limit=1`;
        console.log('自动搜索API URL:', apiUrl);
        const response = await fetch(apiUrl, {
            method: 'GET',
            headers: {
                'Content-Type': 'application/json',
            }
        });
        if (!response.ok) {
            throw new Error(`搜索失败: ${response.status}`);
        }
        const data = await response.json();
        console.log('自动搜索API返回数据:', data);
        // 提取搜索结果
        const searchResults = data?.results?.albums?.data || [];
        // 保存搜索查询到状态
        State.setSearchState({
            originalQuery: query,
            searchResults: searchResults,
            currentOffset: 0
        });
        // 渲染搜索结果
        renderAutoSearchResults(searchResults);
    } catch (error) {
        console.error('自动搜索失败:', error);
        searchContainer.innerHTML = `
            <div class="text-danger">
                <i class="bi bi-exclamation-triangle"></i>
                搜索失败: ${error.message}
            </div>
        `;
    }
}

// 渲染自动搜索结果
function renderAutoSearchResults(results) {
    const elements = State.getDomElements();
    const container = elements.autoSearchResults;
    
    if (!results || results.length === 0) {
        container.innerHTML = `
            <div class="text-muted text-center">
                <i class="bi bi-search"></i>
                <p class="mt-2">未找到相关结果</p>
            </div>
        `;
        return;
    }
    
    container.innerHTML = '';
    
    results.forEach((item, index) => {
        const resultDiv = document.createElement('div');
        resultDiv.className = `search-item ${index === 0 ? 'selected' : ''}`;
        resultDiv.dataset.index = index;
        
        // 适应Apple Music API数据结构
        const attributes = item.attributes || {};
        const artworkUrl = attributes.artwork?.url?.replace('{w}', '100').replace('{h}', '100') || '';
        const trackName = attributes.name || '未知标题';
        const artistName = attributes.artistName || '未知艺术家';
        const collectionName = attributes.name || '';
        const trackViewUrl = attributes.url || '';
        
        resultDiv.innerHTML = `
            <div class="d-flex align-items-center">
                <img src="${artworkUrl}" class="me-3" style="width: 60px; height: 60px; border-radius: 4px; object-fit: cover;" onerror="this.style.display='none'">
                <div class="flex-grow-1">
                    <h6 class="mb-1">${trackName}</h6>
                    <p class="mb-1 text-muted small">${artistName}</p>
                    <p class="mb-0 text-secondary small">${collectionName}</p>
                </div>
                <div class="text-end">
                    <input type="checkbox" class="form-check-input" ${index === 0 ? 'checked' : ''}>
                </div>
            </div>
        `;
        
        // 点击事件
        resultDiv.addEventListener('click', (e) => {
            if (e.target.type !== 'checkbox') {
                const checkbox = resultDiv.querySelector('input[type="checkbox"]');
                checkbox.checked = !checkbox.checked;
            }
            
            // 更新选中状态
            updateSearchItemSelection();
        });
        
        container.appendChild(resultDiv);
    });
}

// 更新搜索项选中状态
function updateSearchItemSelection() {
    const elements = State.getDomElements();
    const container = elements.autoSearchResults;
    
    container.querySelectorAll('.search-item').forEach(item => {
        const checkbox = item.querySelector('input[type="checkbox"]');
        if (checkbox.checked) {
            item.classList.add('selected');
        } else {
            item.classList.remove('selected');
        }
    });
}

// 处理偏移搜索
async function handleOffsetSearch() {
    const searchState = State.getSearchState();
    
    if (!searchState || !searchState.originalQuery) {
        console.error('没有可用的搜索查询');
        return;
    }
    
    const newOffset = searchState.currentOffset + 1;
    
    try {
        console.log('偏移搜索，新偏移量:', newOffset);
        
        // 使用GET方法，与现有搜索API保持一致
        const apiUrl = `/api/search?term=${encodeURIComponent(searchState.originalQuery)}&types=albums&limit=1&offset=${newOffset}`;
        console.log('偏移搜索API URL:', apiUrl);
        
        const response = await fetch(apiUrl, {
            method: 'GET',
            headers: {
                'Content-Type': 'application/json',
            }
        });
        
        if (!response.ok) {
            throw new Error(`偏移搜索失败: ${response.status}`);
        }
        
        const data = await response.json();
        console.log('偏移搜索API返回数据:', data);
        
        // 提取搜索结果
        const searchResults = data?.results?.albums?.data || [];
        
        // 更新搜索状态
        State.setSearchState({
            ...searchState,
            searchResults: searchResults,
            currentOffset: newOffset
        });
        
        // 重新渲染结果
        renderAutoSearchResults(searchResults);
        
    } catch (error) {
        console.error('偏移搜索失败:', error);
        const elements = State.getDomElements();
        elements.autoSearchResults.innerHTML = `
            <div class="text-danger">
                <i class="bi bi-exclamation-triangle"></i>
                偏移搜索失败: ${error.message}
            </div>
        `;
    }
}

// 处理下载选中
async function handleDownloadSelected() {
    const elements = State.getDomElements();
    const container = elements.autoSearchResults;
    
    // 获取选中的项目
    const selectedItems = [];
    container.querySelectorAll('.search-item').forEach(item => {
        const checkbox = item.querySelector('input[type="checkbox"]');
        if (checkbox.checked) {
            const index = parseInt(item.dataset.index);
            const searchState = State.getSearchState();
            if (searchState && searchState.searchResults[index]) {
                selectedItems.push(searchState.searchResults[index]);
            }
        }
    });
    
    if (selectedItems.length === 0) {
        alert('请至少选择一个项目进行下载');
        return;
    }
    
    console.log('选中的下载项目:', selectedItems);
    
    // 构造下载链接 - 适应Apple Music API数据结构
    const downloadLinks = selectedItems.map(item => {
        const attributes = item.attributes || {};
        return attributes.url || '';
    }).filter(link => link);
    
    if (downloadLinks.length === 0) {
        alert('选中的项目没有可用的下载链接');
        return;
    }
    
    // 禁用按钮并显示加载状态
    const downloadBtn = elements.downloadSelectedBtn;
    const originalText = downloadBtn.textContent;
    downloadBtn.disabled = true;
    downloadBtn.textContent = '发送中...';
    
    try {
        // 将链接转换为任务格式
        const tasks = downloadLinks.map(link => ({ link: link }));
        
        // 直接POST到API端点
        const response = await fetch('/api/task', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(tasks)
        });

        if (!response.ok) {
            throw new Error(`请求失败，状态码: ${response.status}`);
        }

        // 解析响应数据
        const responseData = await response.json();
        console.log('下载任务发送成功:', responseData);

        // 🎯 新增：设置任务完成时间以启用10秒短轮询模式
        State.setLastTaskCompletionTime(Date.now());
        State.setHadRunningTasks(true); // 标记有任务运行，为后续检测完成做准备
        console.log('[DownloadTask] 📥 设置任务完成时间，启用10秒短轮询模式');

        // 显示成功提示
        downloadBtn.textContent = '发送成功';
        downloadBtn.classList.remove('btn-primary');
        downloadBtn.classList.add('btn-success');
        
        // 2秒后恢复按钮状态
        setTimeout(() => {
            downloadBtn.disabled = false;
            downloadBtn.textContent = originalText;
            downloadBtn.classList.remove('btn-success');
            downloadBtn.classList.add('btn-primary');
        }, 2000);
        
    } catch (error) {
        console.error('发送下载任务失败:', error);
        
        // 显示错误提示
        downloadBtn.textContent = '发送失败';
        downloadBtn.classList.remove('btn-primary');
        downloadBtn.classList.add('btn-danger');
        
        // 2秒后恢复按钮状态
        setTimeout(() => {
            downloadBtn.disabled = false;
            downloadBtn.textContent = originalText;
            downloadBtn.classList.remove('btn-danger');
            downloadBtn.classList.add('btn-primary');
        }, 2000);
        
        alert(`发送失败: ${error.message}`);
    }
}

// 修改handleSubmit函数以支持图片搜索
export function handleSubmitWithImageSearch() {
    const elements = State.getDomElements();
    const instances = State.getModalInstances();
    const textarea = elements.songLinksTextarea;
    
    // 如果输入框为空且用户已登录，显示图片搜索弹窗
    if ((!textarea.value || textarea.value.trim() === '') && State.getUserName()) {
        console.log('输入框为空，打开图片搜索弹窗');
        instances.imageSearchModalInstance.show();
        return;
    }
    
    // 否则执行正常的提交流程
    handleSubmit();
}

// 处理Apple Music按钮点击
function handleAppleMusicOpen() {
    const elements = State.getDomElements();
    const container = elements.autoSearchResults;
    
    // 获取当前选中的项目
    const selectedItems = [];
    container.querySelectorAll('.search-item').forEach(item => {
        const checkbox = item.querySelector('input[type="checkbox"]');
        if (checkbox.checked) {
            const index = parseInt(item.dataset.index);
            const searchState = State.getSearchState();
            if (searchState && searchState.searchResults[index]) {
                selectedItems.push(searchState.searchResults[index]);
            }
        }
    });
    
    if (selectedItems.length === 0) {
        alert('请先选择要打开的项目');
        return;
    }
    
    // 获取第一个选中项目的Apple Music链接
    const firstSelected = selectedItems[0];
    const attributes = firstSelected.attributes || {};
    const appleMusicUrl = attributes.url || '';
    
    if (!appleMusicUrl) {
        alert('选中的项目没有可用的Apple Music链接');
        return;
    }
    
    console.log('打开Apple Music链接:', appleMusicUrl);
    
    // 在新标签页中打开Apple Music链接
    window.open(appleMusicUrl, '_blank');
}

// ======================== 新增：通知系统 ========================

// 通知系统变量
let notificationEventSource = null;
let notificationContainer = null;
let notificationId = 0;

// 初始化通知系统
function initNotificationSystem() {
    console.log('[Notification] 🔔 初始化通知系统...');
    
    // 获取通知容器
    notificationContainer = document.getElementById('notificationContainer');
    if (!notificationContainer) {
        console.error('[Notification] 通知容器未找到，无法初始化通知系统');
        return;
    }
    
    // 启动通知SSE连接
    startNotificationSSE();
}

// 启动通知SSE连接
function startNotificationSSE() {
    // 如果已有连接，先关闭
    if (notificationEventSource) {
        notificationEventSource.close();
        notificationEventSource = null;
    }
    
    try {
        console.log('[Notification] 🔗 连接通知SSE...');
        notificationEventSource = new EventSource('/api/progress/notice');
        
        notificationEventSource.onopen = function(event) {
            console.log('[Notification] ✅ 通知SSE连接已建立');
        };
        
        notificationEventSource.onmessage = function(event) {
            try {
                const data = JSON.parse(event.data);
                console.log('[Notification] 📨 收到通知数据:', data);
                
                if (data.event === 'connected') {
                    console.log('[Notification] 🤝 通知SSE握手成功');
                } else if (data.event === 'task_completed') {
                    showTaskCompletionNotification(data);
                }
            } catch (e) {
                console.error('[Notification] 解析通知数据失败:', e, event.data);
            }
        };
        
        notificationEventSource.onerror = function(event) {
            console.error('[Notification] ❌ 通知SSE连接出错:', event);
            
            // 如果连接失败，3秒后重试
            setTimeout(() => {
                if (!isPageUnloading && !isPageHidden) {
                    console.log('[Notification] 🔄 重试通知SSE连接...');
                    startNotificationSSE();
                }
            }, 3000);
        };
        
        // 页面卸载时关闭连接
        window.addEventListener('beforeunload', () => {
            if (notificationEventSource) {
                notificationEventSource.close();
                notificationEventSource = null;
            }
        });
        
    } catch (error) {
        console.error('[Notification] 创建通知SSE连接失败:', error);
    }
}

// 显示任务完成通知
function showTaskCompletionNotification(data) {
    const {
        type,        // 'success' 或 'error'
        uuid,
        user,
        task_name,
        task_type,
        message,
        timestamp
    } = data;
    
    // 创建通知配置
    const config = {
        type: type,
        title: type === 'success' ? '任务完成' : '任务失败',
        message: message,
        taskType: task_type,
        timestamp: timestamp,
        duration: type === 'success' ? 5000 : 8000 // 成功5秒，失败8秒
    };
    
    // 显示通知
    showNotification(config);
}

// 显示通知的核心函数
function showNotification(config) {
    if (!notificationContainer) {
        console.error('[Notification] 通知容器不存在');
        return;
    }
    
    const {
        type = 'success',
        title = '通知',
        message = '',
        taskType = '',
        timestamp = new Date().toISOString(),
        duration = 5000
    } = config;
    
    // 生成唯一ID
    const currentId = ++notificationId;
    
    // 创建通知元素
    const notification = document.createElement('div');
    notification.className = `notification ${type}`;
    notification.dataset.id = currentId;
    
    // 格式化时间
    const timeStr = formatNotificationTime(timestamp);
    
    // 获取图标
    const icon = getNotificationIcon(type);
    
    // 构建HTML
    notification.innerHTML = `
        <div class="notification-header">
            <div class="notification-title">
                <span class="notification-icon ${type}">${icon}</span>
                <span>${escapeHtmlForNotification(title)}</span>
            </div>
            <button class="notification-close" onclick="closeNotification(${currentId})" aria-label="关闭通知">
                ×
            </button>
        </div>
        <div class="notification-message">
            ${escapeHtmlForNotification(message)}
        </div>
        <div class="notification-meta">
            <span class="notification-time">${timeStr}</span>
            ${taskType ? `<span class="notification-type">${escapeHtmlForNotification(taskType)}</span>` : ''}
        </div>
    `;
    
    // 添加到容器顶部
    notificationContainer.insertBefore(notification, notificationContainer.firstChild);
    
    // 触发显示动画
    setTimeout(() => {
        notification.classList.add('show');
    }, 10);
    
    // 自动关闭
    if (duration > 0) {
        setTimeout(() => {
            closeNotification(currentId);
        }, duration);
    }
    
    console.log(`[Notification] 显示${type}通知: ${title} - ${message}`);
}

// 关闭通知
function closeNotification(id) {
    const notification = notificationContainer.querySelector(`[data-id="${id}"]`);
    if (!notification) return;
    
    // 移除显示类，触发关闭动画
    notification.classList.remove('show');
    
    // 动画完成后移除元素
    setTimeout(() => {
        if (notification.parentNode) {
            notification.parentNode.removeChild(notification);
        }
    }, 300);
}

// 获取通知图标
function getNotificationIcon(type) {
    switch (type) {
        case 'success':
            return '✅';
        case 'error':
            return '❌';
        case 'warning':
            return '⚠️';
        case 'info':
            return 'ℹ️';
        default:
            return '📢';
    }
}

// 格式化通知时间
function formatNotificationTime(timestamp) {
    try {
        const date = new Date(timestamp);
        const now = new Date();
        const diff = now - date;
        
        if (diff < 60000) { // 1分钟内
            return '刚刚';
        } else if (diff < 3600000) { // 1小时内
            const minutes = Math.floor(diff / 60000);
            return `${minutes}分钟前`;
        } else if (diff < 86400000) { // 24小时内
            const hours = Math.floor(diff / 3600000);
            return `${hours}小时前`;
        } else {
            return date.toLocaleDateString('zh-CN', {
                month: 'short',
                day: 'numeric',
                hour: '2-digit',
                minute: '2-digit'
            });
        }
    } catch (e) {
        return '刚刚';
    }
}

// HTML转义函数（专用于通知系统）
function escapeHtmlForNotification(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// 导出关闭通知函数到全局作用域，供HTML onclick使用
window.closeNotification = closeNotification;

// 导出测试函数（开发调试用）
window.testNotification = function(type = 'success') {
    const testConfigs = {
        success: {
            type: 'success',
            title: '下载完成',
            message: '专辑《测试专辑》下载完成',
            taskType: '专辑',
            duration: 5000
        },
        error: {
            type: 'error',
            title: '下载失败',
            message: '专辑《测试专辑》下载失败：网络连接超时',
            taskType: '专辑',
            duration: 8000
        }
    };
    
    showNotification(testConfigs[type] || testConfigs.success);
};