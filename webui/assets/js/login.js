// 登录处理模块
// 文件：assets/js/login.js

let loginIframe = null;
let loginModal = null;
let isLoginInProgress = false;
let loginHandled = false;

// 初始化登录模块
export function initLogin() {
    console.log('[Login] 初始化登录模块');
    
    // 创建登录iframe
    createLoginIframe();
    
    // 创建登录模态框
    createLoginModal();
    
    // 绑定全局登录处理函数
    window.handleLogin = handleLogin;
    
    return true;
}

// 创建登录iframe
function createLoginIframe() {
    loginIframe = document.createElement('iframe');
    loginIframe.setAttribute('scrolling', 'no');
    loginIframe.style.display = 'none';
    loginIframe.style.width = '100%';
    loginIframe.style.height = '100%';
    loginIframe.style.border = 'none';
    loginIframe.style.position = 'absolute';
    loginIframe.style.top = '0';
    loginIframe.style.left = '0';
    loginIframe.style.zIndex = '9999';
    loginIframe.style.overflow = 'hidden';
    loginIframe.style.background = 'transparent';
    // 隐藏iframe滚动条
    loginIframe.style.scrollbarWidth = 'none'; // Firefox
    loginIframe.style.msOverflowStyle = 'none'; // IE 10+
    // 监听iframe加载完成事件
    loginIframe.addEventListener('load', handleIframeLoad);
    document.body.appendChild(loginIframe);
}

// 创建登录模态框
function createLoginModal() {
    loginModal = document.createElement('div');
    loginModal.className = 'modal fade';
    loginModal.id = 'loginModal';
    loginModal.setAttribute('tabindex', '-1');
    loginModal.setAttribute('aria-labelledby', 'loginModalLabel');
    loginModal.setAttribute('aria-hidden', 'true');
    loginModal.innerHTML = `
        <style>
            @media (max-width: 600px) {
                #loginModal .modal-dialog {
                    width: 100% !important;
                    max-width: 98vw !important;
                    margin: 0 auto !important;
                }
                #loginModal .modal-content {
                    height: 95vh !important;
                }
                #loginModal .modal-body {
                    height: 75vh !important;
                }
            }
            #loginIframeContainer, #loginIframeContainer iframe {
                overflow: hidden !important;
            }
            #loginIframeContainer iframe {
                scrollbar-width: none !important; /* Firefox */
                -ms-overflow-style: none !important; /* IE 10+ */
            }
            #loginIframeContainer iframe::-webkit-scrollbar {
                display: none !important;
            }
        </style>
        <div class="modal-dialog modal-dialog-centered" style="max-width: 550px; width: 95vw; min-width: 0;">
            <div class="modal-content" style="height: 710px; padding: 0;">
                <div class="modal-header">
                    <h5 class="modal-title" id="loginModalLabel">登录</h5>
                    <button type="button" class="close-button" data-bs-dismiss="modal" aria-label="Close">
                        <svg class="svg-icon" viewBox="0 0 384 512">
                            <path d="M342.6 150.6c12.5-12.5 12.5-32.8 0-45.3s-32.8-12.5-45.3 0L192 210.7 86.6 105.4c-12.5-12.5-32.8-12.5-45.3 0s-12.5 32.8 0 45.3L146.7 256 41.4 361.4c-12.5 12.5-12.5 32.8 0 45.3s32.8 12.5 45.3 0L192 301.3 297.4 406.6c12.5 12.5 32.8 12.5 45.3 0s12.5-32.8 0-45.3L237.3 256 342.6 150.6z"/>
                        </svg>
                    </button>
                </div>
                <div class="modal-body" style="height: 600px; padding: 0; position: relative;">
                    <div id="loginIframeContainer" style="width: 100%; height: 100%; position: absolute; top: 0; left: 0; margin: 0; padding: 0;"></div>
                </div>
            </div>
        </div>
    `;
    document.body.appendChild(loginModal);

    // 初始化Bootstrap模态框
    const modalInstance = new bootstrap.Modal(loginModal);

    // 监听模态框关闭事件
    loginModal.addEventListener('hidden.bs.modal', () => {
        const container = document.getElementById('loginIframeContainer');
        if (container) container.innerHTML = '';
        loginIframe = null;
        isLoginInProgress = false;
    });
}

// 处理登录请求
export async function handleLogin() {
    if (isLoginInProgress) {
        console.log('[Login] 登录正在进行中，忽略重复请求');
        return;
    }
    isLoginInProgress = true;

    // 登录时立即恢复轮询
    if (window.State && typeof window.State.setPollingPaused === 'function') {
        window.State.setPollingPaused(false);
    }

    // 先移除旧的iframe
    if (loginIframe && loginIframe.parentNode) loginIframe.parentNode.removeChild(loginIframe);

    // 创建隐藏iframe
    loginIframe = document.createElement('iframe');
    loginIframe.style.display = 'none';
    loginIframe.src = '/oauth2/sign_in';
    loginHandled = false;
    loginIframe.onload = async function() {
        if (loginHandled) return;
        let isCrossDomain = false;
        try {
            if (loginIframe.contentWindow.location.hostname === window.location.hostname) {
                // 静默登录，直接刷新状态
                loginHandled = true;
                if (window.checkUserLoginAndStartApp) {
                    window.checkUserLoginAndStartApp();
                }
                return;
            }
        } catch (e) {
            isCrossDomain = true;
        }
        if (isCrossDomain) {
            showLoginModalWithIframe();
        }
    };
    document.body.appendChild(loginIframe);
}

function showLoginModalWithIframe() {
    const modalInstance = bootstrap.Modal.getInstance(loginModal) || new bootstrap.Modal(loginModal);
    modalInstance.show();
    const container = document.getElementById('loginIframeContainer');
    if (container) {
        container.innerHTML = '';
        loginIframe.style.display = 'block';
        loginIframe.style.position = 'absolute';
        loginIframe.style.width = '100%';
        loginIframe.style.height = '100%';
        loginIframe.style.border = 'none';
        loginIframe.style.top = '0';
        loginIframe.style.left = '0';
        loginIframe.style.zIndex = '9999';
        loginIframe.style.overflow = 'hidden';
        // 隐藏iframe滚动条
        loginIframe.setAttribute('scrolling', 'no');
        loginIframe.style.scrollbarWidth = 'none';
        loginIframe.style.msOverflowStyle = 'none';
        container.appendChild(loginIframe);
    }
    loginHandled = false;
    loginIframe.onload = async function() {
        if (loginHandled) return;
        let isBackToSelf = false;
        try {
            if (loginIframe.contentWindow.location.hostname === window.location.hostname) {
                isBackToSelf = true;
            }
        } catch (e) {
            isBackToSelf = false;
        }
        if (isBackToSelf) {
            loginHandled = true;
            const modalInstance = bootstrap.Modal.getInstance(loginModal);
            if (modalInstance) modalInstance.hide();
            if (window.checkUserLoginAndStartApp) {
                window.checkUserLoginAndStartApp();
            }
            return;
        }
    };
}

// 处理iframe加载完成事件
async function handleIframeLoad() {
    try {
        // 尝试访问iframe的location，如果不报错说明已回到本域
        let isBackToSelf = false;
        try {
            const iframeLocation = loginIframe.contentWindow.location;
            if (iframeLocation.hostname === window.location.hostname) {
                isBackToSelf = true;
            }
        } catch (e) {
            // 跨域，说明还在第三方登录页
            isBackToSelf = false;
        }

        if (isBackToSelf) {
            // 登录流程已回到本域，关闭弹窗并刷新
            const modalInstance = bootstrap.Modal.getInstance(loginModal);
            if (modalInstance) modalInstance.hide();
            window.location.reload();
            return;
        }
        // 否则什么都不做，等待下一次load事件
    } catch (error) {
        console.error('[Login] iframe加载处理出错:', error);
    } finally {
        isLoginInProgress = false;
    }
}

// 处理登出
export function handleLogout() {
    // 先移除旧的iframe
    if (loginIframe && loginIframe.parentNode) loginIframe.parentNode.removeChild(loginIframe);

    // 创建隐藏iframe
    const logoutIframe = document.createElement('iframe');
    logoutIframe.style.display = 'none';
    logoutIframe.src = '/oauth2/sign_out';
    logoutIframe.onload = function() {
        // 登出完成后刷新前端登录状态
        if (window.checkUserLoginAndStartApp) {
            window.checkUserLoginAndStartApp();
        }
        isLoginInProgress = false;

        // 清除封面预览
        const covers = document.getElementById('taskQueueCovers');
        if (covers) covers.innerHTML = '';

        // 清理iframe
        setTimeout(() => {
            if (logoutIframe.parentNode) logoutIframe.parentNode.removeChild(logoutIframe);
        }, 100);

        // 清空输入框并恢复初始高度
        const textarea = document.getElementById('song_links');
        if (textarea) {
            textarea.value = '';
            if (window.UI && typeof window.UI.autoResizeTextarea === 'function') {
                window.UI.autoResizeTextarea();
            }
        }
    };
    document.body.appendChild(logoutIframe);
} 