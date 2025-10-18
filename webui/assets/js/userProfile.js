// 用户头像组件管理模块
// 文件：assets/js/userProfile.js

import { handleLogout } from './login.js';

let userProfileElements = null;
let isShowing = false; // 添加状态管理
let showTimeout = null; // 显示动画的定时器
let hideTimeout = null; // 隐藏动画的定时器
let dropdownTimeout = null; // 下拉菜单的定时器
let currentAvatarData = null; // 保存当前头像数据

// 初始化用户头像组件元素
export function initUserProfile() {
    userProfileElements = {
        profileContainer: document.getElementById('userProfile'),
        userName: document.getElementById('userName'),
        avatar: document.getElementById('userAvatar'),
        avatarText: document.getElementById('avatarText')
    };
    
    if (!userProfileElements.profileContainer) {
        console.warn('[UserProfile] 用户头像组件元素未找到');
        return false;
    }
    
    // 创建头像大图查看器
    createAvatarViewer();
    
    // 全局退出登录函数
    window.handleLogout = handleLogout;
    
    console.log('[UserProfile] 用户头像组件初始化完成');
    return true;
}

// 创建头像大图查看器
function createAvatarViewer() {
    const viewer = document.createElement('div');
    viewer.className = 'avatar-viewer-overlay';
    viewer.style.display = 'none';
    viewer.innerHTML = `
        <div class="avatar-viewer-content">
            <div class="avatar-viewer-header">
                <span class="avatar-viewer-username">用户</span>
                <button class="avatar-viewer-close" onclick="closeAvatarViewer()">
                    <i class="bi bi-x-lg"></i>
                </button>
            </div>
            <div class="avatar-viewer-body">
                <div class="avatar-viewer-image-container">
                    <!-- 头像将动态插入这里 -->
                </div>
            </div>
        </div>
    `;
    
    document.body.appendChild(viewer);
    userProfileElements.avatarViewer = viewer;
    
    // 全局关闭函数
    window.closeAvatarViewer = () => {
        hideAvatarViewer();
    };
    
    // 全局显示函数（供演示使用）
    window.showAvatarViewer = () => {
        showAvatarViewer();
    };
    
    // 点击背景关闭
    viewer.addEventListener('click', (e) => {
        if (e.target === viewer) {
            hideAvatarViewer();
        }
    });
    
    // ESC键关闭
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && viewer.style.display === 'flex') {
            hideAvatarViewer();
        }
    });
}

// 获取用户头像信息
export async function fetchUserAvatar(username) {
    if (!username) {
        console.warn('[UserProfile] 用户名为空，无法获取头像');
        return null;
    }
    
    try {
        console.log(`[UserProfile] 正在获取用户头像: ${username}`);
        
        const response = await fetch(`/api/user/avatar?username=${encodeURIComponent(username)}`, {
            method: 'GET',
            credentials: 'include',
            headers: {
                'Accept': 'application/json'
            }
        });
        
        if (!response.ok) {
            throw new Error(`获取头像失败: ${response.status}`);
        }
        
        const data = await response.json();
        
        if (data.status === 'success' && data.standard_username) {
            console.log(`[UserProfile] 头像获取成功:`, data);
            return {
                standardUsername: data.standard_username,
                avatarUrl: data.avatar_url
            };
        } else {
            throw new Error('头像数据格式错误');
        }
        
    } catch (error) {
        console.error('[UserProfile] 获取用户头像失败:', error);
        return null;
    }
}

// 更新用户信息（支持头像URL）
export async function updateUserInfo(username, avatarData = null) {
    if (!userProfileElements) {
        console.warn('[UserProfile] 组件未初始化，无法更新用户信息');
        return;
    }
    
    if (!username || typeof username !== 'string') {
        console.warn('[UserProfile] 无效的用户名');
        return;
    }
    
    try {
        // 清除可能存在的隐藏定时器
        if (hideTimeout) {
            clearTimeout(hideTimeout);
            hideTimeout = null;
        }
        
        // 如果没有提供头像数据，尝试获取
        if (!avatarData) {
            avatarData = await fetchUserAvatar(username);
        }
        
        // 确定最终显示的用户名
        const displayUsername = avatarData?.standardUsername || username;
        
        // 保存当前头像数据供大图查看器使用
        currentAvatarData = {
            displayUsername: displayUsername,
            avatarUrl: avatarData?.avatarUrl,
            originalUsername: username
        };
        
        // 更新用户名显示
        if (userProfileElements.userName) {
            userProfileElements.userName.textContent = displayUsername;
        }
        
        // 更新头像显示
        updateAvatarDisplay(displayUsername, avatarData?.avatarUrl);
        
        // 显示用户头像组件
        if (userProfileElements.profileContainer) {
            isShowing = true; // 标记为正在显示
            
            const container = userProfileElements.profileContainer;
            container.style.display = 'flex';
            
            const isMobile = window.innerWidth < 768;

            if (isMobile) {
                container.style.opacity = '1'; // 移动端直接显示，无过渡
                container.style.transform = 'translateX(-50%) scale(1)';
                container.style.transition = 'none';
            } else {
                // 桌面端保留原有动画
                container.style.opacity = '0';
                container.style.transition = 'all 0.4s cubic-bezier(0.4, 0, 0.2, 1)';
                const initialTransform = 'translateX(20px)';
                const finalTransform = 'translateX(0)';
                container.style.transform = initialTransform;
                
                // 清除可能存在的显示定时器
                if (showTimeout) {
                    clearTimeout(showTimeout);
                }
                
                requestAnimationFrame(() => {
                    if (isShowing && userProfileElements.profileContainer) {
                        userProfileElements.profileContainer.style.opacity = '1';
                        userProfileElements.profileContainer.style.transform = finalTransform;
                    }
                });
            }
        }
        
        console.log(`[UserProfile] 用户信息已更新：${displayUsername}${avatarData?.avatarUrl ? ' (使用真实头像)' : ' (使用字母头像)'}`);
        
    } catch (error) {
        console.error('[UserProfile] 更新用户信息时出错:', error);
    }
}

// 更新头像显示（支持真实头像或字母头像）
function updateAvatarDisplay(displayUsername, avatarUrl) {
    if (!userProfileElements.avatar) return;
    
    // 清除现有内容
    userProfileElements.avatar.innerHTML = '';
    
    if (avatarUrl) {
        // 使用真实头像
        const avatarImg = document.createElement('img');
        avatarImg.src = avatarUrl;
        avatarImg.alt = displayUsername;
        avatarImg.style.cssText = `
            width: 100%;
            height: 100%;
            object-fit: cover;
            border-radius: 50%;
        `;
        
        // 图片加载失败时回退到字母头像
        avatarImg.onerror = () => {
            console.warn('[UserProfile] 头像图片加载失败，回退到字母头像');
            createLetterAvatar(displayUsername);
        };
        
        userProfileElements.avatar.appendChild(avatarImg);
        userProfileElements.avatar.style.background = 'transparent';
        
    } else {
        // 使用字母头像
        createLetterAvatar(displayUsername);
    }
}

// 创建字母头像
function createLetterAvatar(displayUsername) {
    if (!userProfileElements.avatar || !userProfileElements.avatarText) return;
    
    // 生成并更新头像首字母
    const avatarLetter = generateAvatarLetter(displayUsername);
    userProfileElements.avatarText.textContent = avatarLetter;
    
    // 更新头像背景色
    const avatarColor = generateAvatarColor(displayUsername);
    userProfileElements.avatar.style.background = avatarColor;
    
    // 确保文字显示
    userProfileElements.avatar.appendChild(userProfileElements.avatarText);
}

// 生成头像首字母
function generateAvatarLetter(userName) {
    if (!userName) return '?';
    
    // 处理中文姓名 - 取最后一个字符
    if (/[\u4e00-\u9fa5]/.test(userName)) {
        return userName.charAt(userName.length - 1).toUpperCase();
    }
    
    // 处理英文姓名 - 取第一个字符
    return userName.charAt(0).toUpperCase();
}

// 根据用户名生成头像背景色
function generateAvatarColor(userName) {
    if (!userName) {
        return 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)';
    }
    
    // 颜色调色板
    const colorPalettes = [
        'linear-gradient(135deg, #667eea 0%, #764ba2 100%)', // 蓝紫色
        'linear-gradient(135deg, #f093fb 0%, #f5576c 100%)', // 粉红色
        'linear-gradient(135deg, #4facfe 0%, #00f2fe 100%)', // 蓝青色
        'linear-gradient(135deg, #43e97b 0%, #38f9d7 100%)', // 绿青色
        'linear-gradient(135deg, #fa709a 0%, #fee140 100%)', // 粉黄色
        'linear-gradient(135deg, #a8edea 0%, #fed6e3 100%)', // 青粉色
        'linear-gradient(135deg, #ff9a9e 0%, #fecfef 100%)', // 粉色调
        'linear-gradient(135deg, #ffecd2 0%, #fcb69f 100%)', // 橙色调
        'linear-gradient(135deg, #a18cd1 0%, #fbc2eb 100%)', // 紫粉色
        'linear-gradient(135deg, #fad0c4 0%, #ffd1ff 100%)', // 暖粉色
        'linear-gradient(135deg, #ffeeff 0%, #d299c2 100%)', // 淡紫色
        'linear-gradient(135deg, #89f7fe 0%, #66a6ff 100%)'  // 天蓝色
    ];
    
    // 基于用户名生成哈希值
    let hash = 0;
    for (let i = 0; i < userName.length; i++) {
        const char = userName.charCodeAt(i);
        hash = ((hash << 5) - hash) + char;
        hash = hash & hash; // 转换为32位整数
    }
    
    // 使用哈希值选择颜色
    const colorIndex = Math.abs(hash) % colorPalettes.length;
    return colorPalettes[colorIndex];
}

// 隐藏用户头像组件
export function hideUserProfile() {
    if (!userProfileElements || !userProfileElements.profileContainer) {
        return;
    }
    
    // 防御：确保全局模糊效果未激活
    document.body.classList.remove('modal-blur-active');

    // 标记为非显示状态
    isShowing = false;
    
    // 清除可能存在的显示定时器
    if (showTimeout) {
        clearTimeout(showTimeout);
        showTimeout = null;
    }
    
    // 清除可能存在的隐藏定时器
    if (hideTimeout) {
        clearTimeout(hideTimeout);
        hideTimeout = null;
    }
    
    // 立即开始隐藏动画
    const container = userProfileElements.profileContainer;
    const isMobile = window.innerWidth < 768;

    if (isMobile) {
        container.style.opacity = '0';
        container.style.transform = 'translateX(-50%) scale(1)'; // 与显示时保持一致，避免移动
        container.style.transition = 'opacity 0.3s ease'; // 只过渡透明度
        // 300ms后完全隐藏 (display: none)
        hideTimeout = setTimeout(() => {
            if (!isShowing && userProfileElements.profileContainer) {
                userProfileElements.profileContainer.style.display = 'none';
            }
            hideTimeout = null;
        }, 300);
    } else {
        // 桌面端保留原有动画
        container.style.transition = 'all 0.3s cubic-bezier(0.4, 0, 0.2, 1)';
        container.style.opacity = '0';
        const hiddenTransform = 'translateX(20px)';
        container.style.transform = hiddenTransform;
        
        // 300ms后完全隐藏
        hideTimeout = setTimeout(() => {
            if (!isShowing && userProfileElements.profileContainer) {
                userProfileElements.profileContainer.style.display = 'none';
            }
            hideTimeout = null;
        }, 300);
    }
    
    console.log('[UserProfile] 用户头像隐藏');
}

// 显示用户头像组件
export function showUserProfile() {
    if (!userProfileElements || !userProfileElements.profileContainer) {
        return;
    }
    
    // 防御：确保全局模糊效果未激活
    document.body.classList.remove('modal-blur-active');
    
    // 清除可能存在的隐藏定时器
    if (hideTimeout) {
        clearTimeout(hideTimeout);
        hideTimeout = null;
    }
    
    isShowing = true;
    const container = userProfileElements.profileContainer; // 使用变量简化
    container.style.display = 'flex';

    const isMobile = window.innerWidth < 768;

    if (isMobile) {
        container.style.opacity = '1';
        container.style.transform = 'translateX(-50%) scale(1)';
        container.style.transition = 'none';
    } else {
        // 桌面端保留原有动画
        container.style.opacity = '0';
        container.style.transition = 'all 0.4s cubic-bezier(0.4, 0, 0.2, 1)';
        const initialTransform = 'translateX(20px)';
        const finalTransform = 'translateX(0)';
        container.style.transform = initialTransform; 
        
        requestAnimationFrame(() => {
            if (isShowing && userProfileElements.profileContainer) {
                userProfileElements.profileContainer.style.opacity = '1';
                userProfileElements.profileContainer.style.transform = finalTransform;
            }
        });
    }
    
    console.log('[UserProfile] 用户头像显示');
}

// 获取当前显示的用户名
export function getCurrentUserName() {
    if (userProfileElements && userProfileElements.userName) {
        return userProfileElements.userName.textContent;
    }
    return null;
}

// 获取当前显示状态
export function isUserProfileVisible() {
    return isShowing;
}

// 设置头像点击事件
export function setProfileClickHandler(customHandler = null) {
    if (!userProfileElements || !userProfileElements.profileContainer) {
        return;
    }
    
    // 移除旧的事件监听器
    removeProfileClickHandler();
    
    if (customHandler && typeof customHandler === 'function') {
        // 使用自定义处理器（如访客模式）
        userProfileElements.profileContainer.addEventListener('click', customHandler);
        userProfileElements.profileContainer._clickHandler = customHandler;
        userProfileElements.profileContainer.style.cursor = 'pointer';
    } else {
        // 使用默认的处理器（登录用户）- 只处理头像点击查看大图
        const avatarClickHandler = (event) => {
            event.stopPropagation();
            console.log('[UserProfile] 点击头像，显示大图');
            showAvatarViewer();
        };
        
        // 添加头像点击事件监听器
        if (userProfileElements.avatar) {
            userProfileElements.avatar.addEventListener('click', avatarClickHandler);
            userProfileElements.avatar._avatarClickHandler = avatarClickHandler;
            userProfileElements.avatar.style.cursor = 'pointer';
        }
    }
}

// 移除头像点击事件
export function removeProfileClickHandler() {
    if (userProfileElements && userProfileElements.profileContainer) {
        // 移除容器事件监听器
        if (userProfileElements.profileContainer._clickHandler) {
            userProfileElements.profileContainer.removeEventListener('click', userProfileElements.profileContainer._clickHandler);
            userProfileElements.profileContainer._clickHandler = null;
        }
        
        if (userProfileElements.profileContainer._mouseEnterHandler) {
            userProfileElements.profileContainer.removeEventListener('mouseenter', userProfileElements.profileContainer._mouseEnterHandler);
            userProfileElements.profileContainer._mouseEnterHandler = null;
        }
        
        if (userProfileElements.profileContainer._mouseLeaveHandler) {
            userProfileElements.profileContainer.removeEventListener('mouseleave', userProfileElements.profileContainer._mouseLeaveHandler);
            userProfileElements.profileContainer._mouseLeaveHandler = null;
        }
        
        userProfileElements.profileContainer.style.cursor = '';
        
        // 移除头像事件监听器
        if (userProfileElements.avatar && userProfileElements.avatar._avatarClickHandler) {
            userProfileElements.avatar.removeEventListener('click', userProfileElements.avatar._avatarClickHandler);
            userProfileElements.avatar._avatarClickHandler = null;
            userProfileElements.avatar.style.cursor = '';
        }
    }
}

// 显示头像大图查看器
export function showAvatarViewer() {
    if (!userProfileElements.avatarViewer || !currentAvatarData) return;
    
    const viewer = userProfileElements.avatarViewer;
    const imageContainer = viewer.querySelector('.avatar-viewer-image-container');
    const usernameSpan = viewer.querySelector('.avatar-viewer-username');
    
    // 清空容器
    imageContainer.innerHTML = '';
    
    // 设置用户名（现在在左上角）
    usernameSpan.textContent = currentAvatarData.displayUsername || '用户';
    
    if (currentAvatarData.avatarUrl) {
        // 显示真实头像大图
        const imgWrapper = document.createElement('div');
        imgWrapper.className = 'avatar-viewer-image-wrapper';
        imgWrapper.style.cssText = `
            position: relative;
            width: 100%;
            height: 100%;
            display: flex;
            align-items: center;
            justify-content: center;
        `;
        
        const img = document.createElement('img');
        img.src = currentAvatarData.avatarUrl;
        img.alt = currentAvatarData.displayUsername || '用户头像';
        img.className = 'avatar-viewer-image';
        
        img.onerror = () => {
            // 图片加载失败，显示字母头像
            showLetterAvatarInViewer(imageContainer, currentAvatarData.displayUsername);
        };
        
        imgWrapper.appendChild(img);
        imageContainer.appendChild(imgWrapper);
    } else {
        // 显示字母头像大图
        showLetterAvatarInViewer(imageContainer, currentAvatarData.displayUsername);
    }
    
    // 显示查看器
    viewer.style.display = 'flex';
    viewer.style.opacity = '0';
    
    requestAnimationFrame(() => {
        viewer.style.opacity = '1';
    });
    
    console.log('[UserProfile] 头像大图查看器已显示');
}

// 在查看器中显示字母头像
function showLetterAvatarInViewer(container, displayUsername) {
    const letterAvatar = document.createElement('div');
    letterAvatar.className = 'avatar-viewer-letter';
    letterAvatar.style.position = 'relative'; // 确保下载提示能正确定位
    
    const avatarLetter = generateAvatarLetter(displayUsername);
    const avatarColor = generateAvatarColor(displayUsername);
    
    letterAvatar.textContent = avatarLetter;
    letterAvatar.style.background = avatarColor;
    
    container.appendChild(letterAvatar);
}

// 下载头像图片
function downloadAvatarImage(imageUrl, username) {
    try {
        const link = document.createElement('a');
        link.href = imageUrl;
        link.download = `${username || '用户'}_头像.jpg`;
        link.target = '_blank';
        
        // 对于跨域图片，需要使用fetch下载
        fetch(imageUrl)
            .then(response => response.blob())
            .then(blob => {
                const url = window.URL.createObjectURL(blob);
                link.href = url;
                document.body.appendChild(link);
                link.click();
                document.body.removeChild(link);
                window.URL.revokeObjectURL(url);
                console.log(`[UserProfile] 头像图片下载完成: ${username}`);
            })
            .catch(error => {
                console.warn('[UserProfile] 跨域下载失败，尝试直接下载:', error);
                // 如果fetch失败，直接尝试下载
                document.body.appendChild(link);
                link.click();
                document.body.removeChild(link);
            });
    } catch (error) {
        console.error('[UserProfile] 下载头像图片时出错:', error);
        alert('下载失败，请稍后重试');
    }
}

// 下载字母头像（转换为图片）
function downloadLetterAvatar(username, letter, backgroundColor) {
    try {
        // 创建canvas来生成字母头像图片
        const canvas = document.createElement('canvas');
        const ctx = canvas.getContext('2d');
        const size = 400; // 高分辨率
        
        canvas.width = size;
        canvas.height = size;
        
        // 创建圆形背景
        ctx.fillStyle = backgroundColor;
        ctx.beginPath();
        ctx.arc(size / 2, size / 2, size / 2, 0, 2 * Math.PI);
        ctx.fill();
        
        // 绘制字母
        ctx.fillStyle = 'white';
        ctx.font = `bold ${size * 0.4}px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(letter, size / 2, size / 2);
        
        // 转换为blob并下载
        canvas.toBlob(blob => {
            const url = window.URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = `${username || '用户'}_头像.png`;
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            window.URL.revokeObjectURL(url);
            console.log(`[UserProfile] 字母头像下载完成: ${username} (${letter})`);
        }, 'image/png');
        
    } catch (error) {
        console.error('[UserProfile] 生成字母头像时出错:', error);
        alert('下载失败，请稍后重试');
    }
}

// 隐藏头像大图查看器
function hideAvatarViewer() {
    if (!userProfileElements.avatarViewer) return;
    
    const viewer = userProfileElements.avatarViewer;
    viewer.style.opacity = '0';
    
    setTimeout(() => {
        viewer.style.display = 'none';
    }, 300);
    
    console.log('[UserProfile] 头像大图查看器已隐藏');
} 