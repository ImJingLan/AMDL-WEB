import * as State from './state.js';
import * as ModalHandler from './modalHandler.js'; // 确认引入路径和名称
import * as UrlParams from './urlParams.js'; // 新增：引入URL参数处理模块
import { handleLogin } from './login.js';

// Textarea 自动调整大小函数
export function autoResizeTextarea() {
   if (!State.domElements.songLinksTextarea) return;
   const computedStyle = window.getComputedStyle(State.domElements.songLinksTextarea);
   const initialHeightCSSValue = '45px';
   const minHeight = parseInt(initialHeightCSSValue, 10) || 40;
   const maxHeight = parseInt(computedStyle.maxHeight, 10) || 300;
   let lineHeight = parseFloat(computedStyle.lineHeight);
   if (isNaN(lineHeight)) {
       const tempDiv = document.createElement('div');
       tempDiv.style.cssText = 'padding:0;border:0;visibility:hidden;position:absolute;font:' + computedStyle.font;
       tempDiv.textContent = 'M';
       document.body.appendChild(tempDiv);
       lineHeight = tempDiv.offsetHeight;
       document.body.removeChild(tempDiv);
   }
   lineHeight = lineHeight || 20;
   if (State.domElements.songLinksTextarea.value.trim() === '') {
       State.domElements.songLinksTextarea.style.height = initialHeightCSSValue;
   } else {
       State.domElements.songLinksTextarea.style.height = 'auto';
       let targetHeight = State.domElements.songLinksTextarea.scrollHeight + lineHeight;
       targetHeight = Math.max(minHeight, targetHeight);
       targetHeight = Math.min(maxHeight, targetHeight);
       State.domElements.songLinksTextarea.style.height = targetHeight + 'px';
   }
}

// 更新任务数 Badge
export function updatePendingTasksBadge(text, badgeClass = 'bg-secondary', enableMusicServerJump = false) {
    const badgeElement = State.domElements.pendingTasksBadge;
    if (badgeElement) {
        badgeElement.textContent = text;
        badgeElement.className = 'status-badge ' + badgeClass;
        
        // 清理之前的点击事件
        if (badgeElement._musicServerClickHandler) {
            badgeElement.removeEventListener('click', badgeElement._musicServerClickHandler);
            badgeElement._musicServerClickHandler = null;
        }
        
        // 移除所有可能的点击样式类
        badgeElement.classList.remove('logged-in-clickable');
        badgeElement.style.cursor = '';
        badgeElement.title = '';
        
        // 如果启用音乐服务器跳转功能
        if (enableMusicServerJump) {
            badgeElement.classList.add('logged-in-clickable');
            badgeElement.style.cursor = 'pointer';
            badgeElement.title = '点击跳转到音乐服务器';
            
            // 添加点击事件
            const handleMusicServerClick = () => {
                console.log('🎵 用户点击状态徽章，跳转到音乐服务器');
                
                // 添加点击反馈效果
                badgeElement.style.transform = 'scale(0.9)';
                setTimeout(() => {
                    badgeElement.style.transform = '';
                }, 150);
                
                // 在新标签页打开音乐服务器
                window.open('https://lyjw131.com:8096/music', '_blank', 'noopener,noreferrer');
            };
            
            badgeElement.addEventListener('click', handleMusicServerClick);
            badgeElement._musicServerClickHandler = handleMusicServerClick;
        }
    } else {
        console.error("无法找到 ID 为 'pendingTasks' 的元素。");
    }
}

// 显示错误 Modal (可以进一步增强以显示 failed_tasks)
export function showErrorMessage(message, showLoginButton = false) {
    const modalFooter = document.querySelector("#failedModal .modal-footer");
    const loginButtonId = 'loginButton';
    const appleMusicButtonId = 'appleMusicButton';
    const clearInputButtonId = 'clearInputButton';
    
    if (modalFooter) {
        // 统一移除所有可能存在的动态按钮
        const existingLoginBtn = modalFooter.querySelector(`#${loginButtonId}`);
        if (existingLoginBtn) existingLoginBtn.remove();
        const existingAppleMusicBtn = modalFooter.querySelector(`#${appleMusicButtonId}`);
        if (existingAppleMusicBtn) existingAppleMusicBtn.remove();
        const existingClearInputBtn = modalFooter.querySelector(`#${clearInputButtonId}`);
        if (existingClearInputBtn) existingClearInputBtn.remove();

        if (showLoginButton) {
             const loginButton = document.createElement('button');
             loginButton.type = 'button';
             loginButton.id = loginButtonId;
             loginButton.className = 'btn btn-primary me-auto';
             loginButton.textContent = '登录';
             loginButton.onclick = () => { 
                 // 保存输入框内容到localStorage
                 if (State.domElements.songLinksTextarea && State.domElements.songLinksTextarea.value.trim()) {
                     localStorage.setItem('amdl_pending_input', State.domElements.songLinksTextarea.value);
                     console.log("已保存用户输入内容，登录后将自动恢复");
                 }
                 handleLogin();
             };
             modalFooter.prepend(loginButton);
        }
        // 只有不是登录提示时才加 Apple Music 按钮
        if (!showLoginButton) {
            const appleMusicButton = document.createElement('button');
            appleMusicButton.type = 'button';
            appleMusicButton.id = appleMusicButtonId;
            appleMusicButton.className = 'btn btn-primary me-auto';
            appleMusicButton.textContent = 'AppleMusic';
            appleMusicButton.onclick = () => { window.open('https://music.apple.com/cn', '_blank', 'noopener,noreferrer'); };
            modalFooter.prepend(appleMusicButton);
        }

        // 使用 innerHTML 或 textContent 显示消息。如果 message 包含 HTML 或需要换行，用 innerHTML。
        // 这里假设 message 是纯文本，但可能包含 \n
        State.domElements.errorMessageElement.innerHTML = message.replace(/\n/g, '<br>'); // 替换换行符为 <br>

        State.failedModalInstance.show();
    } else {
         console.error("错误 Modal、其消息元素或页脚元素未初始化!", message);
         let alertMsg = message; // 直接使用处理过的 message
         if (showLoginButton) alertMsg += "\n请尝试重新登录。";
         alert(alertMsg);
    }
}

// 滚动封面容器
export function scrollCovers(direction) {
    const container = State.domElements.coverScrollContainer;
    if (!container) return;
    
    // 获取所有占位符元素
    const placeholders = Array.from(container.querySelectorAll('.cover-placeholder'));
    if (!placeholders.length) return;
    
    // 在移动设备上使用精确的元素定位
    if (window.innerWidth < 768) {
        // 获取第一个占位符的完整宽度（包括margin）
        const firstPlaceholder = placeholders[0];
        const rect = firstPlaceholder.getBoundingClientRect();
        const style = window.getComputedStyle(firstPlaceholder);
        const marginLeft = parseFloat(style.marginLeft) || 0;
        const marginRight = parseFloat(style.marginRight) || 0;
        const totalWidth = rect.width + marginLeft + marginRight;
        
        // 计算当前滚动位置
        const currentScroll = container.scrollLeft;
        
        // 计算目标滚动位置
        let targetScroll;
        if (direction === 'left') {
            targetScroll = Math.max(0, currentScroll - totalWidth);
        } else {
            targetScroll = Math.min(
                container.scrollWidth - container.clientWidth,
                currentScroll + totalWidth
            );
        }
        
        // 平滑滚动到目标位置
        container.scrollTo({
            left: targetScroll,
            behavior: 'smooth'
        });
    } else {
        // 桌面端保持原有的多个元素滚动逻辑
        const placeholder = placeholders[0];
        const totalWidth = placeholder.offsetWidth;
        const visibleItems = Math.floor(container.clientWidth / totalWidth);
        const itemsToScroll = Math.max(1, Math.floor(visibleItems / 2));
        const scrollAmount = totalWidth * itemsToScroll;
        
        const currentScroll = container.scrollLeft;
        const maxScroll = container.scrollWidth - container.clientWidth;
        
        let targetScroll;
        if (direction === 'left') {
            targetScroll = Math.max(0, currentScroll - scrollAmount);
        } else {
            targetScroll = Math.min(maxScroll, currentScroll + scrollAmount);
        }
        
        container.scrollTo({
            left: targetScroll,
            behavior: 'smooth'
        });
    }
}

// 更新滚动按钮状态
export function updateScrollButtons() {
    const container = State.domElements.coverScrollContainer;
    const leftBtn = State.domElements.scrollLeftBtn;
    const rightBtn = State.domElements.scrollRightBtn;
    if (!container || !leftBtn || !rightBtn) return;

    // 移动端直接隐藏并禁用按钮
    if (window.innerWidth < 768) {
        leftBtn.style.display = 'none';
        rightBtn.style.display = 'none';
        leftBtn.disabled = true;
        rightBtn.disabled = true;
        return; // 移动端不再执行后续逻辑
    }

    // 桌面端逻辑保持不变
    requestAnimationFrame(() => {
        if (!container || !leftBtn || !rightBtn) return; // 再次检查以防万一
        const { scrollLeft, scrollWidth, clientWidth } = container;
        const tolerance = 5;
        // 检查是否有子元素，如果没有子元素，则不可滚动
        const hasContent = container.firstElementChild !== null;
        const isScrollable = hasContent && scrollWidth > clientWidth + tolerance;

        leftBtn.style.display = isScrollable ? 'flex' : 'none';
        rightBtn.style.display = isScrollable ? 'flex' : 'none';

        if (isScrollable) {
            leftBtn.disabled = scrollLeft <= tolerance;
            rightBtn.disabled = scrollLeft >= (scrollWidth - clientWidth - tolerance);
        } else {
            leftBtn.disabled = true;
            rightBtn.disabled = true;
        }
    });
}

// 处理封面占位符的点击事件
export async function handleCoverClick(placeholderDiv) {
    if (!State.logModalInstance || !placeholderDiv) return;
    const uuid = placeholderDiv.dataset.uuid;
    if (!uuid) {
        console.warn("被点击的封面占位符缺少 data-uuid 属性。");
        return;
    }

    console.log(`用户点击封面占位符，意图关注 UUID: ${uuid}`);

    // 记录用户关注的 UUID
    State.setFocusedTaskUuid(uuid);

    const modalElement = State.domElements.logModalElement;
    if (modalElement) {
        // 设置 dataset 以便 'shown' 事件和其他逻辑知道当前显示的是哪个任务
        modalElement.dataset.showingUuid = uuid;
    } else {
         console.error("Log Modal 元素未找到！");
         return; // 无法继续
    }

    // 从 State 中获取最新的数据用于立即显示
    const taskData = State.latestTaskMap.get(uuid);

    // 显示 Modal 之前先尝试填充内容
    if (!taskData) {
        console.warn(`点击时无法在 latestTaskMap 中找到 UUID ${uuid}。Modal 将显示加载状态。`);
        await ModalHandler.updateLogModalInfo(null); // 清空或显示加载状态
        const outputDiv = modalElement?.querySelector('#formattedLogOutput');
        if(outputDiv) outputDiv.innerHTML = '<p class="text-muted text-center p-5">正在加载任务详情...</p>';
    } else {
        await ModalHandler.updateLogModalInfo(taskData);
        ModalHandler.renderModalContentFromTaskData(taskData);
    }

    // 新增：预先设置URL参数
    UrlParams.setMonitorStatus(true, uuid);

    // 显示 Modal
    State.logModalInstance.show();
}

// --- 新增：初始化封面滑动功能 ---
export function initializeSwipeScroll() {
    const container = State.domElements.coverScrollContainer;
    if (!container) return;

    // 只在移动设备上启用滑动
    if (window.matchMedia("(min-width: 768px)").matches) {
        return;
    }

    let isDragging = false;
    let startX;
    let scrollLeftStart;
    let lastTouchX;

    container.addEventListener('touchstart', (e) => {
        if (e.touches.length !== 1) return;
        isDragging = true;
        startX = e.touches[0].pageX;
        lastTouchX = startX;
        scrollLeftStart = container.scrollLeft;
    }, { passive: true });

    container.addEventListener('touchmove', (e) => {
        if (!isDragging || e.touches.length !== 1) return;
        e.preventDefault();

        const x = e.touches[0].pageX;
        const walk = (x - lastTouchX);
        lastTouchX = x;
        
        container.scrollLeft = Math.max(0, 
            Math.min(container.scrollLeft - walk, 
                container.scrollWidth - container.clientWidth));
    });

    const handleTouchEnd = () => {
        if (!isDragging) return;
        isDragging = false;

        const placeholder = container.querySelector('.cover-placeholder');
        if (!placeholder) return;
        
        // 计算完整的占位符宽度
        const placeholderStyle = window.getComputedStyle(placeholder);
        const marginLeft = parseFloat(placeholderStyle.marginLeft) || 0;
        const marginRight = parseFloat(placeholderStyle.marginRight) || 0;
        const totalWidth = placeholder.offsetWidth + marginLeft + marginRight;
        
        if (totalWidth <= 0) return;

        // 计算最接近的对齐位置
        const currentScroll = container.scrollLeft;
        const nearestMultiple = Math.round(currentScroll / totalWidth);
        const targetScroll = Math.max(0, 
            Math.min(nearestMultiple * totalWidth, 
                container.scrollWidth - container.clientWidth));

        // 平滑滚动到对齐位置
        container.scrollTo({
            left: targetScroll,
            behavior: 'smooth'
        });
    };

    container.addEventListener('touchend', handleTouchEnd);
    container.addEventListener('touchcancel', handleTouchEnd);
}

// --- 新增：初始化自定义 Tooltip 功能 ---
export function initializeCustomTooltips() {
    const tooltipElement = State.domElements.customTooltip;
    const container = State.domElements.taskQueueCoversContainer;

    if (!tooltipElement || !container) {
        console.warn("无法初始化自定义 Tooltip：缺少 Tooltip 元素或封面容器。");
        return;
    }

    // --- 新增：添加全局鼠标位置跟踪 ---
    document.addEventListener('mousemove', (e) => {
        window.mouseX = e.clientX;
        window.mouseY = e.clientY;
    }, { passive: true });
    
    let showTimeout, hideTimeout;

    const showTooltip = (placeholder) => {
        const tooltipText = placeholder.dataset.tooltipText;
        if (!tooltipText) return;

        // --- 新增：保存正在查看的 tooltip 对应的 uuid ---
        window.activeTooltipUuid = placeholder.dataset.uuid;

        // 更新 Tooltip 内容
        tooltipElement.innerHTML = tooltipText.replace(/\n/g, '<br>');
        tooltipElement.style.display = 'block'; // 立即显示以计算尺寸

        // 计算位置
        const placeholderRect = placeholder.getBoundingClientRect();
        const tooltipHeight = tooltipElement.offsetHeight;
        const tooltipWidth = tooltipElement.offsetWidth;
        const spaceBelow = window.innerHeight - placeholderRect.bottom;
        const spaceAbove = placeholderRect.top;
        const scrollY = window.scrollY;
        const scrollX = window.scrollX;
        const margin = 10; // Tooltip 与元素的间距

        // --- 修改定位逻辑：优先尝试下方 --- 
        let top;
        if (spaceBelow >= tooltipHeight + margin) {
            // 下方空间足够
            top = placeholderRect.bottom + scrollY + margin;
        } else if (spaceAbove >= tooltipHeight + margin) {
            // 下方不足，上方足够
            top = placeholderRect.top + scrollY - tooltipHeight - margin;
        } else {
            // 上下都不足，优先放下方（可能部分超出）
            top = placeholderRect.bottom + scrollY + margin;
        }

        // 计算左右位置（保持居中，并进行边界检查）
        let left = placeholderRect.left + scrollX + (placeholderRect.width / 2) - (tooltipWidth / 2);

        // 边界检查：左侧
        if (left < scrollX) {
            left = scrollX + 5;
        }
        // 边界检查：右侧
        else if (left + tooltipWidth > window.innerWidth + scrollX) {
            left = window.innerWidth + scrollX - tooltipWidth - 5;
        }

        tooltipElement.style.top = `${top}px`;
        tooltipElement.style.left = `${left}px`;

        // 使用 requestAnimationFrame 确保样式已应用，然后添加 show 类触发动画
        requestAnimationFrame(() => {
            tooltipElement.classList.add('show');
        });
    };

    const hideTooltip = () => {
        tooltipElement.classList.remove('show');
        // 在动画结束后再彻底隐藏
        // 检查元素是否还在 DOM 中，以防万一
        if (tooltipElement.parentNode) {
             tooltipElement.addEventListener('transitionend', () => {
                 // 再次检查是否应该隐藏（可能鼠标又移回来了）
                 if (!tooltipElement.classList.contains('show')) {
                     tooltipElement.style.display = 'none';
                     // --- 新增：清除活动 tooltip 信息 ---
                     window.activeTooltipUuid = null;
                 }
            }, { once: true });
        } else {
            // 如果元素已不在 DOM 中，直接隐藏
             tooltipElement.style.display = 'none';
             // --- 新增：清除活动 tooltip 信息 ---
             window.activeTooltipUuid = null;
        }
    };

    container.addEventListener('mouseover', (event) => {
        const placeholder = event.target.closest('.cover-placeholder');
        if (!placeholder) return;

        // --- 新增：只在非移动设备上显示 Tooltip ---
        if (window.innerWidth < 768) {
            return;
        }
        // --- 新增结束 ---

        // 清除可能存在的隐藏延时和显示延时
        clearTimeout(hideTimeout);
        clearTimeout(showTimeout);

        // --- 新增：延迟显示，减少闪烁 --- 
        showTimeout = setTimeout(() => {
            showTooltip(placeholder);
        }, 50); // 50ms 延迟
    });

    container.addEventListener('mouseout', (event) => {
        const placeholder = event.target.closest('.cover-placeholder');
        // 检查鼠标是否真的移出了占位符（而不是移到了其子元素或 Tooltip 上）
        if (placeholder && !placeholder.contains(event.relatedTarget)) {
            clearTimeout(showTimeout); // 清除待显示的 timeout
            
            // --- 新增：只在非移动设备上处理隐藏 Tooltip --- 
            if (window.innerWidth < 768) {
                return;
            }
            // --- 新增结束 ---
            
            // 延迟隐藏，给用户一点时间移回
            hideTimeout = setTimeout(hideTooltip, 100); // 100ms 延迟隐藏
        }
    });
}

// --- 新增：初始化 JS 控制的悬停缩放效果 ---
export function initializeHoverScaleEffect() {
    const container = State.domElements.taskQueueCoversContainer;
    if (!container) return;

    const animationOptions = {
        duration: 150, // 动画时长 (ms)
        easing: 'ease-in-out',
        fill: 'forwards' // 动画结束后保持状态
    };

    container.addEventListener('mouseenter', (event) => {
        const placeholder = event.target.closest('.cover-placeholder');
        if (!placeholder) return;
        const img = placeholder.querySelector('img');
        if (!img) return;

        // 取消任何正在进行的反向动画
        if (img._reverseAnimation) {
            img._reverseAnimation.cancel();
        }

        // 创建并播放缩小动画
        img._scaleAnimation = img.animate([
            { transform: 'scale(1)' },
            { transform: 'scale(0.95)' }
        ], animationOptions);

    }, true); // 使用捕获阶段，可能更早触发

    container.addEventListener('mouseleave', (event) => {
        const placeholder = event.target.closest('.cover-placeholder');
        if (!placeholder) return;
        const img = placeholder.querySelector('img');
        if (!img) return;

        // 取消任何正在进行的缩小动画
        if (img._scaleAnimation) {
            img._scaleAnimation.cancel();
        }

        // 创建并播放恢复动画
        img._reverseAnimation = img.animate([
            { transform: 'scale(0.95)' }, // 可能需要从当前计算值开始，但通常 scale(0.95) 即可
            { transform: 'scale(1)' }
        ], animationOptions);

    }, true); // 使用捕获阶段
}