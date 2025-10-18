import * as UI from './ui.js'; // 假设 ui.js 存在且 UI.showErrorMessage 已定义
import * as State from './state.js'; // 🎯 新增：导入State模块

// 全局变量来存储当前搜索状态
let currentSearchTerm = '';
let currentOffset = '';
let currentResultCount = 0;
let isSearchingMore = false;
let isScrolling = false; // 新增：滚动状态控制
let useCache = true; // 新增：缓存开关状态
let lastSearchTerm = ''; // 🎯 新增：记录最后搜索词

// 新增：防止重复显示提示的状态变量
let hasShownLimitMessage = false;
let hasShownEndMessage = false;

// 将搜索状态暴露到全局，供预加载函数使用
window.currentSearchTerm = currentSearchTerm;
window.currentOffset = currentOffset;
window.currentResultCount = currentResultCount;

// 新增：统一更新搜索状态的函数
function updateSearchState(searchTerm = null, offset = null, resultCount = null) {
    if (searchTerm !== null) {
        currentSearchTerm = searchTerm;
        window.currentSearchTerm = searchTerm;
    }
    if (offset !== null) {
        currentOffset = offset;
        window.currentOffset = offset;
    }
    if (resultCount !== null) {
        currentResultCount = resultCount;
        window.currentResultCount = resultCount;
    }
}

// 检查输入是否为链接
function isLink(text) {
    return text.match(/^https?:\/\//i) !== null;
}

// 处理提交
export async function handleSubmit() {
    const input = document.getElementById('song_links').value.trim();
    
    // 如果输入框为空，尝试打开图片搜索弹窗
    if (!input) {
        // 检查是否有用户登录以及图片搜索功能是否可用
        if (window.handleSubmitWithImageSearch && typeof window.handleSubmitWithImageSearch === 'function') {
            window.handleSubmitWithImageSearch();
            return;
        } else {
            showError('请输入内容');
            return;
        }
    }

    // 检查是否包含链接
    const hasLinks = input.split(/[,;\n]/).some(part => isLink(part.trim()));

    if (hasLinks) {
        // 如果包含链接，使用原有的发送逻辑
        // 确保 window.sendRequest 是一个已定义的函数
        if (window.sendRequest && typeof window.sendRequest === 'function') {
            window.sendRequest();
        } else {
            console.error('window.sendRequest 函数未定义');
            showError('处理链接的函数未准备好。');
        }
    } else {
        // 如果不包含链接，执行搜索
        updateSearchState(input, '', 0); // 使用统一函数更新状态
        // 重置提示状态变量
        hasShownLimitMessage = false;
        hasShownEndMessage = false;
        await performSearch(input, '', false);
    }
}

// 执行搜索
async function performSearch(searchTerm, offset = '', isLoadMore = false) {
    console.log('搜索API请求:', `/api/search?term=${searchTerm}&types=albums&limit=8${offset ? `&offset=${offset}` : ''}`);
    console.log('搜索参数:', {searchTerm, offset, isLoadMore, useCache: true});
    
    // 🎯 立即保存搜索词到状态中，确保预加载机制能正确获取
    if (!isLoadMore) {
        // 新搜索时初始化状态
        State.setSearchState({
            originalQuery: searchTerm,
            searchResults: [],
            currentOffset: 0,
            totalResults: 0,
            hasMore: false
        });
        console.log('[SearchState] 🎯 初始化搜索状态，搜索词:', searchTerm);
    }
    
    // 更新全局变量
    lastSearchTerm = searchTerm;
    updateSearchState(searchTerm, offset, null); // 也更新旧的状态变量保持兼容性
    
    // 检查是否可以使用预加载缓存（仅适用于桌面端的"搜索更多"）
    if (isLoadMore && window.innerWidth > 768) {
        const cachedData = checkPreloadCache(searchTerm, parseInt(offset) || 0);
        if (cachedData) {
            console.log('[PerformSearch] 🚀 使用预加载缓存，跳过网络请求');
            displaySearchResults(cachedData, true);
            return;
        }
    }
    
    let apiUrl = `/api/search?term=${encodeURIComponent(searchTerm)}&types=albums&limit=8`;
    if (offset) {
        apiUrl += `&offset=${offset}`;
    }
    
    try {
        const response = await fetch(apiUrl, {
            method: 'GET',
            headers: {
                'Content-Type': 'application/json',
            }
        });
        
        console.log('API响应状态:', response.status);
        
        if (!response.ok) {
            throw new Error(`API请求失败: ${response.status} ${response.statusText}`);
        }
        
        const data = await response.json();
        console.log('API返回数据:', data);
        
        displaySearchResults(data, isLoadMore);
        
    } catch (error) {
        console.error('搜索请求失败:', error);
        showError(`搜索失败: ${error.message}`);
    }
}

// 执行搜索并获取目标数量的结果（用于缓存开关切换）
async function performSearchWithTargetCount(searchTerm, targetCount) {
    const searchButton = document.getElementById('sendButton');
    const loadMoreButton = document.getElementById('loadMoreButton');
    
    // 使用搜索更多按钮显示状态
    let targetButton = loadMoreButton || searchButton;
    const strongElement = targetButton.querySelector('strong') || targetButton.querySelector('span');
    const originalText = strongElement ? strongElement.textContent : targetButton.textContent;

    try {
        // 更新按钮状态
        targetButton.disabled = true;
        if (strongElement) {
            strongElement.textContent = '刷新中...';
        } else {
            targetButton.textContent = '刷新中...';
        }

        // 构建API URL，请求目标数量的结果
        let apiUrl = `/api/search?term=${encodeURIComponent(searchTerm)}&types=albums&limit=${targetCount}`;

        console.log('刷新搜索API请求:', apiUrl);
        console.log('目标数量:', targetCount);

        // 构建请求头（强制不使用缓存）
        const headers = {
            'Content-Type': 'application/json',
            'X-Use-Cache': 'false'
        };

        // 调用搜索 API
        const response = await fetch(apiUrl, {
            method: 'GET',
            headers: headers
        });
        
        console.log('API响应状态:', response.status);
        
        if (!response.ok) {
            const errorText = await response.text();
            console.error('API错误响应:', errorText);
            throw new Error(`搜索失败: ${response.status} - ${errorText}`);
        }

        const data = await response.json();
        console.log('API返回数据:', data);
        
        // 显示搜索结果（清空现有结果）
        displaySearchResults(data, false);
        
        // 搜索完成后滚动到底部
        setTimeout(() => {
            const searchResultsContainer = document.getElementById('searchResults');
            if (searchResultsContainer && window.innerWidth >= 768) {
                const maxScrollTop = searchResultsContainer.scrollHeight - searchResultsContainer.clientHeight;
                searchResultsContainer.scrollTo({
                    top: maxScrollTop,
                    behavior: 'smooth'
                });
                console.log('缓存刷新完成，自动滚动到底部');
            }
        }, 200); // 等待DOM更新完成
        
    } catch (error) {
        console.error('刷新搜索失败:', error);
        showError(`刷新搜索失败: ${error.message}`);
    } finally {
        // 恢复按钮状态
        targetButton.disabled = false;
        if (strongElement) {
            strongElement.textContent = originalText;
        } else {
            targetButton.textContent = originalText;
        }
    }
}

// 显示搜索结果
function displaySearchResults(data, isLoadMore = false) {
    console.log('[Mobile] 重置提示状态变量');
    resetMobileMessages();
    
    console.log('处理搜索结果:', data);
    
    if (!data || !data.results || !data.results.albums || !data.results.albums.data) {
        showError('搜索结果格式不正确');
        return;
    }
    
    const results = data.results.albums.data;
    console.log(`找到 ${results.length} 个结果`);
    
    // 🎯 修复：获取当前搜索词
    const currentSearchState = State.getSearchState() || {};
    const currentSearchTerm = currentSearchState.originalQuery || lastSearchTerm || '';
    
    // 解析下一页offset
    let nextOffset = null;
    if (data.results.albums.next) {
        console.log('下一页offset原始值:', data.results.albums.next);
        
        try {
            const nextUrl = new URL(data.results.albums.next, window.location.origin);
            const offsetParam = nextUrl.searchParams.get('offset');
            if (offsetParam) {
                nextOffset = parseInt(offsetParam);
                console.log('解析后的offset值:', nextOffset);
                console.log('[OffsetDebug] ✅ 成功解析offset，表示有更多结果');
            } else {
                console.log('[OffsetDebug] ⚠️ URL中没有offset参数:', data.results.albums.next);
            }
        } catch (e) {
            console.error('解析下一页offset失败:', e);
            console.log('[OffsetDebug] ❌ URL解析失败，可能格式不正确');
        }
    } else {
        console.log('[OffsetDebug] ℹ️ 没有next字段，表示没有更多结果');
    }
    
    console.log('[OffsetDebug] 🔍 最终状态:', {
        hasNext: !!data.results.albums.next,
        nextOffset: nextOffset,
        hasMore: !!nextOffset
    });
    
    const searchResults = document.getElementById('searchResults');
    if (!searchResults) {
        console.error('未找到搜索结果容器');
        return;
    }
    
    // 获取或创建网格容器
    let gridContainer = searchResults.querySelector('.search-grid-container');
    if (!gridContainer || !isLoadMore) {
        // 如果不是加载更多，或者容器不存在，创建新容器
        if (!isLoadMore) {
            searchResults.innerHTML = ''; // 清空现有结果
        }
        gridContainer = document.createElement('div');
        gridContainer.className = 'search-grid-container grid grid-cols-2 md:grid-cols-4 gap-4';
        searchResults.appendChild(gridContainer);
    }
    
    // 记录当前偏移量（用于索引计算）
    const currentOffset = isLoadMore ? currentSearchState.currentOffset || 0 : 0;
    
    // 渲染搜索结果
    results.forEach((track, index) => {
        const trackElement = createTrackElement(track, currentOffset + index);
        gridContainer.appendChild(trackElement);
    });
    
    // 更新总结果计数
    const totalResults = (isLoadMore ? (currentSearchState.totalResults || 0) : 0) + results.length;
    console.log('总结果数量:', totalResults);
    
    // 🎯 修复：完整设置搜索状态，包括所有必要字段
    const allSearchResults = isLoadMore ? 
        [...(currentSearchState.searchResults || []), ...results] : 
        results;
    
    const newSearchState = {
        originalQuery: currentSearchTerm,
        searchResults: allSearchResults,
        currentOffset: nextOffset || totalResults,
        totalResults: totalResults,
        hasMore: !!nextOffset
    };
    
    console.log('[SearchState] 🎯 完整设置搜索状态:', newSearchState);
    State.setSearchState(newSearchState);
    
    // 添加"搜索更多"按钮或移动端滚动监听
    if (nextOffset) {
        if (window.innerWidth <= 768) {
            // 移动端：添加滚动监听
            addMobileScrollListener();
        } else {
            // 桌面端：添加"搜索更多"按钮
            addLoadMoreButton();
        }
    } else {
        // 没有更多结果
        if (window.innerWidth <= 768) {
            showMobileEndMessage();
        }
    }
    
    // 自动滚动到新内容（仅在加载更多时）
    if (isLoadMore && gridContainer.children.length > 0) {
        console.log('自动滚动到新内容');
        setTimeout(() => {
            // 找到新加载的第一个元素
            const existingItemsCount = gridContainer.children.length - results.length;
            const newItems = Array.from(gridContainer.children).slice(existingItemsCount);
            if (newItems.length > 0) {
                newItems[0].scrollIntoView({ 
                    behavior: 'smooth', 
                    block: 'start' 
                });
            }
        }, 100);
    }
    
    // 显示搜索Modal（如果尚未显示）
    showSearchModal();
    
    // 立即更新按钮状态
    setTimeout(() => {
        updateLoadMoreButtonState();
    }, 100);
    
    // 移动端内容加载完成后主动检查是否需要继续加载
    if (isLoadMore && window.innerWidth < 768) {
        setTimeout(() => {
            console.log('[Mobile] 内容加载完成，主动检查滚动状态');
            checkMobileScrollPosition();
        }, 150);
    }
}

// 显示搜索模态框
function showSearchModal() {
    const searchModalElement = document.getElementById('searchResultsModal');
    if (searchModalElement && typeof bootstrap !== 'undefined' && bootstrap.Modal) {
        // 添加搜索更多按钮到 modal-header
        addLoadMoreButton();
        
        // 获取现有的模态框实例
        const existingModal = bootstrap.Modal.getInstance(searchModalElement);
        
        if (existingModal) {
            // 如果模态框已经存在，直接更新内容而不重新创建
            existingModal._element.classList.add('show');
            document.body.classList.add('modal-open');
            const backdrop = document.querySelector('.modal-backdrop');
            if (backdrop) {
                backdrop.classList.add('show');
            }
        } else {
            // 如果是新的模态框，则创建并显示
            const searchModal = new bootstrap.Modal(searchModalElement, {
                keyboard: true
            });
            
            // 添加模态框关闭事件处理
            searchModalElement.addEventListener('hidden.bs.modal', function () {
                // 清理模态框状态
                document.body.classList.remove('modal-open');
                document.body.style.overflow = '';
                document.body.style.paddingRight = '';
                
                // 移除所有模态框背景
                const backdrops = document.querySelectorAll('.modal-backdrop');
                backdrops.forEach(backdrop => backdrop.remove());
                
                // 销毁模态框实例
                searchModal.dispose();
            }, { once: true });
            
            searchModal.show();
        }
        
        // 确保按钮状态在模态框显示后立即更新
        setTimeout(() => {
            updateLoadMoreButtonState();
        }, 50);
    } else {
        console.error('Bootstrap Modal 未定义或 modal 元素不存在。');
        showError('无法显示搜索结果弹窗。');
    }
}

// 添加搜索更多按钮
function addLoadMoreButton() {
    const modalHeader = document.querySelector('#searchResultsModal .modal-header');
    if (!modalHeader) return;
    
    // 检查是否已存在按钮
    let loadMoreButton = document.getElementById('loadMoreButton');
    let cacheToggleElement = document.getElementById('cacheToggleElement');
    
    if (!loadMoreButton) {
        // 确保modal-header使用flex布局
        modalHeader.style.display = 'flex';
        modalHeader.style.alignItems = 'center';
        modalHeader.style.justifyContent = 'space-between';
        
        // 隐藏原来的标题
        const title = modalHeader.querySelector('.modal-title');
        if (title) {
            title.style.display = 'none';
        }
        
        // 创建左侧缓存开关容器
        const leftContainer = document.createElement('div');
        leftContainer.className = 'd-flex align-items-center';
        leftContainer.style.flex = '0 0 auto';
        
        // 创建缓存开关元素
        cacheToggleElement = document.createElement('div');
        cacheToggleElement.id = 'cacheToggleElement';
        cacheToggleElement.className = 'cache-toggle-container';
        cacheToggleElement.innerHTML = `
            <input id="cacheToggle" type="checkbox" checked>
            <label class="cache-toggle-button" for="cacheToggle">
                <span class="cache-toggle-icon">
                    <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                        <path d="M4 7C4 5.89543 4.89543 5 6 5H18C19.1046 5 20 5.89543 20 7V17C20 18.1046 19.1046 19 18 19H6C4.89543 19 4 18.1046 4 17V7Z" stroke="currentColor" stroke-width="2"/>
                        <path d="M8 9H16M8 11H12M8 13H14" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
                        <circle cx="17" cy="8" r="2" fill="currentColor"/>
                    </svg>
                </span>
            </label>
        `;
        
        // 添加缓存开关事件
        const cacheToggle = cacheToggleElement.querySelector('#cacheToggle');
        cacheToggle.addEventListener('change', async (e) => {
            const wasUsingCache = useCache;
            useCache = e.target.checked;
            console.log('缓存开关状态:', useCache ? '开启' : '关闭');
            
            // 如果从使用缓存切换到不使用缓存，并且当前有搜索结果，则自动刷新
            if (wasUsingCache && !useCache && currentSearchTerm && currentResultCount > 0) {
                console.log('检测到缓存关闭，自动刷新搜索结果...');
                console.log('当前结果数量:', currentResultCount);
                
                // 保存当前的结果数量
                const targetResultCount = currentResultCount;
                
                // 重置搜索状态为初始状态
                updateSearchState(null, '', 0); // 使用统一函数重置状态
                
                // 重新执行搜索，获取目标数量的结果（不使用缓存）
                await performSearchWithTargetCount(currentSearchTerm, targetResultCount);
            }
        });
        
        leftContainer.appendChild(cacheToggleElement);
        
        // 创建中央容器 - 包含搜索更多按钮（桌面端显示）
        const centerContainer = document.createElement('div');
        centerContainer.className = 'd-flex justify-content-center align-items-center desktop-only';
        centerContainer.style.position = 'absolute';
        centerContainer.style.left = '50%';
        centerContainer.style.top = '50%';
        centerContainer.style.transform = 'translate(-50%, -50%)';
        centerContainer.style.zIndex = '10';
        centerContainer.style.pointerEvents = 'none';
        
        // 创建搜索更多按钮
        loadMoreButton = document.createElement('button');
        loadMoreButton.id = 'loadMoreButton';
        loadMoreButton.className = 'load-more-cta';
        loadMoreButton.style.pointerEvents = 'auto';
        loadMoreButton.innerHTML = `
            <span>搜索更多</span>
            <svg width="15px" height="10px" viewBox="0 0 13 10">
                <path d="M1,5 L11,5" />
                <polyline points="8 1 12 5 8 9" />
            </svg>
        `;
        
        // 添加搜索更多按钮点击事件
        loadMoreButton.addEventListener('click', async () => {
            await handleLoadMoreClick();
        });
        
        // 将按钮添加到中央容器
        centerContainer.appendChild(loadMoreButton);
        
        // 确保modal-header有相对定位
        modalHeader.style.position = 'relative';
        
        // 将左侧容器和中央容器添加到modal-header
        modalHeader.insertBefore(leftContainer, modalHeader.firstChild);
        modalHeader.appendChild(centerContainer);
        
        // 为关闭按钮设置flex属性
        const closeButton = modalHeader.querySelector('.close-button');
        if (closeButton) {
            closeButton.style.flex = '0 0 auto';
            closeButton.style.marginLeft = 'auto';
        }
        
        // 添加移动端滚动监听
        addMobileScrollListener();
    }
    
    // 更新按钮状态
    updateLoadMoreButtonState();
}

// 新增：处理加载更多点击的逻辑
async function handleLoadMoreClick() {
    // 如果正在滚动，忽略点击
    if (isScrolling) {
        console.log('正在滚动中，忽略点击');
        return;
    }
    
    // 🎯 修复：从State模块获取搜索状态
    const searchState = State.getSearchState();
    if (!searchState) {
        console.log('没有搜索状态，无法加载更多');
        return;
    }
    
    // 如果达到搜索数量限制，执行滚动操作
    if (searchState.totalResults >= 48) {
        const searchResultsContainer = document.getElementById('searchResults');
        if (searchResultsContainer && window.innerWidth >= 768) {
            // 设置滚动状态为true，禁用按钮
            isScrolling = true;
            const loadMoreButton = document.getElementById('loadMoreButton');
            if (loadMoreButton) {
                loadMoreButton.disabled = true;
                loadMoreButton.classList.add('disabled');
            }
            
            // 改进的两行高度计算方法
            const gridContainer = searchResultsContainer.querySelector('.search-grid-container');
            if (!gridContainer || gridContainer.children.length === 0) {
                isScrolling = false;
                updateLoadMoreButtonState();
                return;
            }
            
            // 方法：找到每行的第一个和第三行的第一个元素，计算两行的实际高度
            let twoRowsHeight = 400; // 默认高度作为回退
            
            if (gridContainer.children.length >= 8) {
                // 如果有至少8个元素（两行），直接计算前两行的高度
                const firstRowFirstItem = gridContainer.children[0];
                const thirdRowFirstItem = gridContainer.children[8]; // 第三行第一个元素
                
                if (firstRowFirstItem && thirdRowFirstItem) {
                    const firstRect = firstRowFirstItem.getBoundingClientRect();
                    const thirdRect = thirdRowFirstItem.getBoundingClientRect();
                    twoRowsHeight = thirdRect.top - firstRect.top;
                    console.log('使用8个元素计算两行高度:', twoRowsHeight);
                } else {
                    // 回退到单行计算
                    const firstItem = gridContainer.children[0];
                    const fifthItem = gridContainer.children[4]; // 第二行第一个元素
                    if (firstItem && fifthItem) {
                        const firstRect = firstItem.getBoundingClientRect();
                        const fifthRect = fifthItem.getBoundingClientRect();
                        twoRowsHeight = (fifthRect.top - firstRect.top) * 2;
                        console.log('使用4个元素推算两行高度:', twoRowsHeight);
                    }
                }
            } else if (gridContainer.children.length >= 4) {
                // 如果只有一行或更少，推算高度
                const firstItem = gridContainer.children[0];
                const fifthItem = gridContainer.children[4] || gridContainer.children[gridContainer.children.length - 1];
                
                if (firstItem && fifthItem && gridContainer.children.length > 4) {
                    const firstRect = firstItem.getBoundingClientRect();
                    const fifthRect = fifthItem.getBoundingClientRect();
                    const oneRowHeight = fifthRect.top - firstRect.top;
                    twoRowsHeight = oneRowHeight * 2;
                    console.log('推算两行高度（基于一行）:', twoRowsHeight);
                } else {
                    // 最后回退：使用单个元素高度估算
                    const firstItem = gridContainer.children[0];
                    const itemRect = firstItem.getBoundingClientRect();
                    twoRowsHeight = itemRect.height * 2 + 32; // 假设gap为16px * 2
                    console.log('最后回退：估算两行高度:', twoRowsHeight);
                }
            }
            
            const currentScrollTop = searchResultsContainer.scrollTop;
            const maxScrollTop = searchResultsContainer.scrollHeight - searchResultsContainer.clientHeight;
            
            // 检查是否已经在底部
            const isAtBottom = currentScrollTop >= maxScrollTop - 10;
            
            let targetScrollTop;
                    
            if (isAtBottom) {
                // 如果在底部，回到顶部
                targetScrollTop = 0;
                console.log('已在底部，回到顶部');
            } else {
                // 否则向下滚动两行的距离
                targetScrollTop = Math.min(currentScrollTop + twoRowsHeight, maxScrollTop);
                console.log(`向下滚动两行，当前位置: ${currentScrollTop}, 目标位置: ${targetScrollTop}, 两行高度: ${twoRowsHeight}`);
            }
            
            // 执行平滑滚动
            searchResultsContainer.scrollTo({
                top: targetScrollTop,
                behavior: 'smooth'
            });
                            
            // 监听滚动完成
            const checkScrollEnd = () => {
                const currentPos = searchResultsContainer.scrollTop;
                const targetReached = Math.abs(currentPos - targetScrollTop) <= 5;
                
                if (targetReached) {
                    isScrolling = false;
                    updateLoadMoreButtonState();
                    console.log('滚动完成，当前位置:', currentPos);
                } else {
                    setTimeout(checkScrollEnd, 50);
                }
            };
            
            setTimeout(checkScrollEnd, 100);
        }
        return;
    }
    
    // 原有的加载更多逻辑
    if (!searchState.hasMore || isSearchingMore) {
        console.log('没有更多结果或正在搜索中，忽略点击');
        return;
    }
    
    await performLoadMore();
}

// 新增：执行加载更多的核心逻辑
async function performLoadMore() {
    isSearchingMore = true;
    
    // 🎯 修复：从State模块获取搜索状态
    const searchState = State.getSearchState();
    if (!searchState || !searchState.hasMore) {
        console.log('没有搜索状态或没有更多结果，退出加载更多');
        isSearchingMore = false;
        return;
    }
    
    // 移动端显示加载提示
    showMobileLoadingIndicator();
    
    try {
        // 检查是否有预加载的缓存数据可以使用
        const preloadCache = checkPreloadCache(searchState.originalQuery, searchState.totalResults);
        if (preloadCache) {
            console.log('[SearchPreload] 使用预加载缓存数据，跳过API请求');
            displaySearchResults(preloadCache.data, true);
            
            // 立即触发新的预加载，为下一次点击准备
            setTimeout(() => {
                if (window.silentPreloadMoreResults) {
                    window.silentPreloadMoreResults();
                }
            }, 100);
            
            return;
        }
        
        // 发起搜索请求，使用State中的偏移量
        await performSearch(searchState.originalQuery, searchState.currentOffset.toString(), true);
    } finally {
        isSearchingMore = false;
        hideMobileLoadingIndicator();
        
        // 关键修复：在加载完成后立即重新检测滚动状态
        setTimeout(() => {
            checkMobileScrollPosition();
        }, 100);
    }
}

// 新增：检查移动端滚动位置并处理
function checkMobileScrollPosition() {
    // 只在移动端执行
    if (window.innerWidth >= 768) return;
    
    const searchResultsContainer = document.getElementById('searchResults');
    if (!searchResultsContainer) return;
    
    // 🎯 修复：从State模块获取搜索状态
    const searchState = State.getSearchState();
    if (!searchState) return;
    
    const scrollTop = searchResultsContainer.scrollTop;
    const scrollHeight = searchResultsContainer.scrollHeight;
    const clientHeight = searchResultsContainer.clientHeight;
    
    // 检查是否接近底部（留出30px的缓冲区，比原来更小）
    const isNearBottom = scrollTop + clientHeight >= scrollHeight - 30;
    
    console.log('[Mobile] 检查滚动位置:', {
        scrollTop,
        scrollHeight,
        clientHeight,
        isNearBottom,
        isSearchingMore,
        hasMore: searchState.hasMore,
        totalResults: searchState.totalResults,
        hasShownLimitMessage,
        hasShownEndMessage
    });
    
    if (isNearBottom) {
        if (!isSearchingMore && searchState.hasMore && searchState.totalResults < 48) {
            console.log('[Mobile] 检测到底部，自动触发加载更多');
            performLoadMore();
        } else if (searchState.totalResults >= 48 && !hasShownLimitMessage) {
            console.log('[Mobile] 已达到搜索限制，显示限制提示');
            hasShownLimitMessage = true; // 设置状态，防止重复显示
            showMobileLimitMessage();
        } else if (!searchState.hasMore && !hasShownEndMessage) {
            console.log('[Mobile] 没有更多内容，显示结束提示');
            hasShownEndMessage = true; // 设置状态，防止重复显示
            showMobileEndMessage();
        }
    }
}

// 新增：显示移动端限制提示
function showMobileLimitMessage() {
    // 只在移动端显示
    if (window.innerWidth >= 768) return;
    
    const searchResults = document.getElementById('searchResults');
    if (!searchResults) return;
    
    // 检查是否已存在提示
    let limitMessage = document.getElementById('mobile-limit-message');
    
    if (!limitMessage) {
        limitMessage = document.createElement('div');
        limitMessage.id = 'mobile-limit-message';
        limitMessage.className = 'mobile-limit-message';
        limitMessage.innerHTML = `
            <div class="limit-icon">🔒</div>
            <div class="limit-title">已达到搜索限制</div>
            <div class="limit-text">最多显示48个结果，已经为您展示了最相关的内容</div>
        `;
    }
    
    // 将提示添加到搜索结果容器底部
    searchResults.appendChild(limitMessage);
    limitMessage.style.display = 'block';
    
    // 3秒后自动隐藏
    setTimeout(() => {
        if (limitMessage && limitMessage.parentNode) {
            limitMessage.style.opacity = '0';
            setTimeout(() => {
                if (limitMessage.parentNode) {
                    limitMessage.parentNode.removeChild(limitMessage);
                }
            }, 300);
        }
    }, 3000);
    
    console.log('[Mobile] 显示搜索限制提示');
}

// 新增：显示移动端结束提示
function showMobileEndMessage() {
    // 只在移动端显示
    if (window.innerWidth >= 768) return;
    
    const searchResults = document.getElementById('searchResults');
    if (!searchResults) return;
    
    // 检查是否已存在提示
    let endMessage = document.getElementById('mobile-end-message');
    
    if (!endMessage) {
        endMessage = document.createElement('div');
        endMessage.id = 'mobile-end-message';
        endMessage.className = 'mobile-end-message';
        endMessage.innerHTML = `
            <div class="end-icon">✨</div>
            <div class="end-title">没有更多结果了</div>
            <div class="end-text">已经为您展示了所有相关内容</div>
        `;
    }
    
    // 将提示添加到搜索结果容器底部
    searchResults.appendChild(endMessage);
    endMessage.style.display = 'block';
    
    // 2秒后自动隐藏
    setTimeout(() => {
        if (endMessage && endMessage.parentNode) {
            endMessage.style.opacity = '0';
            setTimeout(() => {
                if (endMessage.parentNode) {
                    endMessage.parentNode.removeChild(endMessage);
                }
            }, 300);
        }
    }, 2000);
    
    console.log('[Mobile] 显示搜索结束提示');
}

// 新增：显示移动端加载指示器
function showMobileLoadingIndicator() {
    // 只在移动端显示
    if (window.innerWidth >= 768) return;
    
    const searchResults = document.getElementById('searchResults');
    if (!searchResults) return;
    
    // 检查是否已存在加载指示器
    let loadingIndicator = document.getElementById('mobile-loading-indicator');
    
    if (!loadingIndicator) {
        loadingIndicator = document.createElement('div');
        loadingIndicator.id = 'mobile-loading-indicator';
        loadingIndicator.className = 'mobile-loading-indicator';
        loadingIndicator.innerHTML = `
            <div class="loading-spinner"></div>
            <span class="loading-text">加载中...</span>
        `;
    }
    
    // 将加载指示器添加到搜索结果容器底部
    searchResults.appendChild(loadingIndicator);
    loadingIndicator.style.display = 'flex';
    
    console.log('[Mobile] 显示加载指示器');
}

// 新增：隐藏移动端加载指示器
function hideMobileLoadingIndicator() {
    const loadingIndicator = document.getElementById('mobile-loading-indicator');
    if (loadingIndicator) {
        loadingIndicator.style.display = 'none';
        // 延迟移除，避免闪烁
        setTimeout(() => {
            if (loadingIndicator.parentNode) {
                loadingIndicator.parentNode.removeChild(loadingIndicator);
            }
        }, 300);
        console.log('[Mobile] 隐藏加载指示器');
    }
}

// 更新搜索更多按钮状态
function updateLoadMoreButtonState() {
    const loadMoreButton = document.getElementById('loadMoreButton');
    if (!loadMoreButton) return;
    
    // 如果正在滚动，保持禁用状态
    if (isScrolling) {
        loadMoreButton.disabled = true;
        loadMoreButton.classList.add('disabled');
        loadMoreButton.querySelector('span').textContent = '滚动中...';
        return;
    }
    
    // 🎯 修复：从State模块获取正确的搜索状态
    const searchState = State.getSearchState();
    
    if (!searchState || !searchState.hasMore) {
        // 没有搜索状态或没有更多数据时禁用按钮
        loadMoreButton.disabled = true;
        loadMoreButton.classList.add('disabled');
        loadMoreButton.querySelector('span').textContent = '没有更多';
        console.log('[LoadMoreButton] 🔴 没有更多结果，禁用按钮');
    } else if (searchState.totalResults >= 48) {
        // 达到限制时保持可点击，改变文字提示
        loadMoreButton.disabled = false;
        loadMoreButton.classList.remove('disabled');
        loadMoreButton.querySelector('span').textContent = '滚动浏览';
        console.log('[LoadMoreButton] 🟡 达到48个结果限制，显示滚动提示');
    } else {
        // 正常状态：有更多结果可加载
        loadMoreButton.disabled = false;
        loadMoreButton.classList.remove('disabled');
        loadMoreButton.querySelector('span').textContent = '搜索更多';
        console.log('[LoadMoreButton] 🟢 正常状态，可以搜索更多');
    }
    
    // 🎯 新增：调试信息
    console.log('[LoadMoreButton] 当前搜索状态:', {
        hasMore: searchState ? searchState.hasMore : 'null',
        totalResults: searchState ? searchState.totalResults : 'null',
        currentOffset: searchState ? searchState.currentOffset : 'null',
        originalQuery: searchState ? searchState.originalQuery : 'null'
    });
}

// 新增：添加移动端滚动监听
function addMobileScrollListener() {
    const searchResultsContainer = document.getElementById('searchResults');
    if (!searchResultsContainer) return;
    
    let isThrottled = false;
    const throttleDelay = 100; // 减少防抖延迟，提高响应性
    
    const handleScroll = () => {
        if (isThrottled) return;
        
        // 只在移动端执行
        if (window.innerWidth >= 768) return;
        
        isThrottled = true;
        setTimeout(() => { isThrottled = false; }, throttleDelay);
        
        // 使用新的检查函数
        checkMobileScrollPosition();
    };
    
    // 移除可能存在的旧监听器
    searchResultsContainer.removeEventListener('scroll', handleScroll);
    
    // 添加新的滚动监听器
    searchResultsContainer.addEventListener('scroll', handleScroll, { passive: true });
    
    console.log('[Mobile] 移动端滚动监听器已添加');
}

// 处理选择项目 (已修复按钮高度和对齐问题)
async function handleSelectItem(button, link) {
    const originalText = button.textContent;
    const originalDisplay = button.style.display;
    
    // 获取当前跳过检查状态
    const skipCheck = State.getSkipCheck();
    console.log('[SearchItem] 当前跳过检查状态:', skipCheck);

    button.disabled = true;
    button.style.height = '2.5rem'; 
    button.style.width = '100%';   
    button.style.display = 'flex';
    button.style.justifyContent = 'center';
    button.style.alignItems = 'center';
    
    // --- 创建"发送中..."内容 ---
    const loadingContentWrapper = document.createElement('div');
    loadingContentWrapper.style.display = 'flex';
    loadingContentWrapper.style.alignItems = 'center';
    loadingContentWrapper.style.gap = '0.5rem';

    const spinner = document.createElement('div');
    spinner.className = 'spinner-border spinner-border-sm';
    spinner.setAttribute('role', 'status');
    spinner.setAttribute('aria-hidden', 'true');
    spinner.style.width = '1rem';
    spinner.style.height = '1rem';
    
    const loadingTextNode = document.createTextNode("发送中...");

    loadingContentWrapper.appendChild(spinner);
    loadingContentWrapper.appendChild(loadingTextNode);
    button.appendChild(loadingContentWrapper); // 将包装好的"发送中"内容添加到按钮
    // --- "发送中..."内容创建结束 ---

    // 更新按钮背景色以表示加载状态
    button.classList.remove('bg-blue-500', 'hover:bg-blue-600');
    button.classList.add('bg-green-500');

    try {
        // 发起API请求，添加 skip_check 参数
        const response = await fetch('/api/task', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify([{ 
                link: link,
                skip_check: skipCheck
            }])
        });

        button.innerHTML = ''; // 清空加载状态的图标和文本

        if (!response.ok) {
            throw new Error(`请求失败，状态码: ${response.status}`);
        }
        
        // 🎯 新增：设置任务完成时间以启用10秒短轮询模式
        const State = await import('./state.js');
        State.setLastTaskCompletionTime(Date.now());
        State.setHadRunningTasks(true); // 标记有任务运行，为后续检测完成做准备
        console.log('[SearchItem] 📥 设置任务完成时间，启用10秒短轮询模式');
        
        const sentTextSpan = document.createElement('span');
        sentTextSpan.textContent = '已发送';
        sentTextSpan.style.display = 'block'; 
        sentTextSpan.style.width = '100%';
        sentTextSpan.style.textAlign = 'center';
        button.appendChild(sentTextSpan);

        // 3秒后恢复按钮到初始状态
        setTimeout(() => {
            button.disabled = false;
            button.innerHTML = ''; 
            button.textContent = originalText; 
            button.classList.remove('bg-green-500');
            button.classList.add('bg-blue-500', 'hover:bg-blue-600'); 
            
            button.style.height = ''; 
            button.style.width = '';  
            button.style.display = originalDisplay; 
            button.style.justifyContent = ''; 
            button.style.alignItems = '';   
        }, 3000);

    } catch (error) {
        console.error('提交任务时出错:', error);
        button.innerHTML = ''; 

        // 显示错误状态
        const errorTextSpan = document.createElement('span');
        errorTextSpan.textContent = '发送失败';
        errorTextSpan.style.display = 'block'; 
        errorTextSpan.style.width = '100%';
        errorTextSpan.style.textAlign = 'center';
        button.appendChild(errorTextSpan);

        button.classList.remove('bg-green-500');
        button.classList.add('bg-red-500'); 

        // 3秒后恢复按钮到初始状态
        setTimeout(() => {
            button.disabled = false;
            button.innerHTML = ''; 
            button.textContent = originalText; 
            button.classList.remove('bg-red-500');
            button.classList.add('bg-blue-500', 'hover:bg-blue-600');

            button.style.height = ''; 
            button.style.width = '';  
            button.style.display = originalDisplay; 
            button.style.justifyContent = ''; 
            button.style.alignItems = '';   
        }, 3000);

        showError(`提交失败: ${error.message}`);
    }
}

// 显示错误消息
function showError(message) {
    if (window.UI && typeof window.UI.showErrorMessage === 'function') {
        window.UI.showErrorMessage(message);
    } else {
        console.warn('UI.showErrorMessage 未定义，使用 alert 作为回退。');
        alert(message); 
    }
}

// 检查预加载缓存
function checkPreloadCache(searchTerm, currentResultCount) {
    try {
        const cacheData = sessionStorage.getItem('amdl_search_preload_cache');
        if (!cacheData) {
            return null;
        }

        const cache = JSON.parse(cacheData);
        
        // 检查缓存是否匹配当前搜索条件
        if (cache.searchTerm !== searchTerm) {
            console.log('[SearchPreload] 缓存搜索词不匹配，清理缓存');
            sessionStorage.removeItem('amdl_search_preload_cache');
            return null;
        }

        // 检查缓存的offset是否匹配当前需要的offset
        const expectedOffset = currentResultCount;
        const cacheOffset = cache.offset - 8; // cache.offset是下次的offset，需要减去8得到当前缓存数据的offset
        
        if (cacheOffset !== expectedOffset) {
            console.log('[SearchPreload] 缓存offset不匹配，期望:', expectedOffset, '缓存:', cacheOffset);
            sessionStorage.removeItem('amdl_search_preload_cache');
            return null;
        }

        // 检查缓存是否过期（5分钟）
        const cacheAge = Date.now() - cache.timestamp;
        if (cacheAge > 5 * 60 * 1000) {
            console.log('[SearchPreload] 缓存已过期，清理缓存');
            sessionStorage.removeItem('amdl_search_preload_cache');
            return null;
        }

        // 缓存数据有效，清理缓存（防止重复使用）
        sessionStorage.removeItem('amdl_search_preload_cache');
        
        console.log('[SearchPreload] 找到有效缓存数据，使用预加载结果');
        return cache;

    } catch (error) {
        console.warn('[SearchPreload] 检查缓存时出错:', error.message);
        sessionStorage.removeItem('amdl_search_preload_cache');
        return null;
    }
}

// 重置移动端提示状态
function resetMobileMessages() {
    hasShownLimitMessage = false;
    hasShownEndMessage = false;
}

// 创建音轨元素
function createTrackElement(track, index) {
    const attributes = track.attributes || {};
    const artworkUrl = attributes.artwork?.url?.replace('{w}', '400').replace('{h}', '400') || 'https://placehold.co/400x400/e0e0e0/757575?text=封面';
    const name = attributes.name || '未知名称';
    const artistName = attributes.artistName || '未知艺术家';
    const releaseDate = attributes.releaseDate || '未知';
    const shareUrl = attributes.url || '#';

    const trackElement = document.createElement('div');
    trackElement.className = 'flex flex-col h-full items-center p-4 bg-slate-50 hover:bg-slate-100 rounded-lg transition group';
    trackElement.dataset.index = index;

    trackElement.innerHTML = `
        <style>
            .custom-tooltip {
                position: relative;
            }
            .custom-tooltip:hover::after {
                content: attr(data-tooltip);
                position: absolute;
                bottom: 100%;
                left: 50%;
                transform: translateX(-50%);
                padding: 5px 10px;
                background: rgba(0, 0, 0, 0.8);
                color: white;
                border-radius: 4px;
                font-size: 12px;
                white-space: nowrap;
                z-index: 1000;
                margin-bottom: 5px;
            }
        </style>
        <div class="cover-placeholder" style="width:100%;aspect-ratio:1/1;">
            <a href="${shareUrl}" target="_blank" class="block w-full h-full">
                <img src="${artworkUrl}" alt="${name}" class="w-full h-full object-cover rounded-md shadow-sm mb-2 hover:opacity-90 transition" loading="lazy">
            </a>
        </div>
        <div class="w-full text-center flex-1 flex flex-col">
            <div class="font-semibold text-base text-slate-800 mb-1 truncate custom-tooltip" data-tooltip="${name}">${name}</div>
            <div class="text-xs text-slate-600 mb-1">
                <a href="#" class="artist-link hover:text-blue-500 hover:underline truncate block custom-tooltip" data-tooltip="${artistName}" data-artist="${artistName}">${artistName}</a>
            </div>
            <div class="text-xs text-slate-500 mb-1">发行日期: ${releaseDate}</div>
            <div class="mt-auto">
                <button data-link="${shareUrl}" class="w-full select-item-button px-3 py-2 text-xs sm:text-sm bg-blue-500 hover:bg-blue-600 text-white font-medium rounded-md shadow-sm transition duration-150 ease-in-out">
                    下载
                </button>
            </div>
        </div>
    `;

    // 添加歌手名点击事件
    const artistLink = trackElement.querySelector('.artist-link');
    if (artistLink) {
        artistLink.addEventListener('click', async (e) => {
            e.preventDefault();
            const artistName = e.target.dataset.artist;
            if (artistName && artistName !== '未知艺术家') {
                // 更新搜索词并重新搜索
                const searchInput = document.getElementById('song_links');
                if (searchInput) {
                    searchInput.value = artistName;
                }
                
                await performSearch(artistName, '', false);
                
                // 搜索完成后触发预加载
                setTimeout(() => {
                    if (window.silentPreloadMoreResults) {
                        console.log('[ArtistSearch] 歌手搜索完成，开始预加载下一页');
                        window.silentPreloadMoreResults();
                    }
                }, 500);
            }
        });
    }

    // 添加下载按钮事件
    const selectButton = trackElement.querySelector('.select-item-button');
    if (shareUrl !== '#') {
        selectButton.addEventListener('click', async function() {
            await handleSelectItem(this, shareUrl);
        });
    } else {
        selectButton.disabled = true;
        selectButton.textContent = '无链接';
        selectButton.classList.remove('bg-blue-500', 'hover:bg-blue-600');
        selectButton.classList.add('bg-slate-400', 'cursor-not-allowed');
    }

    return trackElement;
}

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
    
    // 获取当前跳过检查状态
    const skipCheck = State.getSkipCheck();
    console.log('[DownloadSelected] 当前跳过检查状态:', skipCheck);
    
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
        // 将链接转换为任务格式，添加 skip_check 参数
        const tasks = downloadLinks.map(link => ({ 
            link: link,
            skip_check: skipCheck
        }));
        
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
        
        // 处理响应
        const result = await response.json();
        console.log('下载任务提交结果:', result);
        
        // 🎯 新增：设置任务完成时间以启用10秒短轮询模式
        State.setLastTaskCompletionTime(Date.now());
        State.setHadRunningTasks(true); // 标记有任务运行，为后续检测完成做准备
        State.setPollingPaused(false); // 确保轮询不被暂停
        console.log('[DownloadSelected] 📥 设置任务完成时间，启用10秒短轮询模式');
        
        // 显示下载结果
        const downloadResult = document.createElement('div');
        downloadResult.className = 'download-result';
        downloadResult.textContent = result.message || '下载任务提交成功';
        elements.downloadResult.appendChild(downloadResult);

        // 3秒后恢复按钮到初始状态
        setTimeout(() => {
            downloadBtn.disabled = false;
            downloadBtn.textContent = originalText;
        }, 3000);

    } catch (error) {
        console.error('下载任务提交失败:', error);
        const downloadResult = document.createElement('div');
        downloadResult.className = 'download-result error';
        downloadResult.textContent = `下载失败: ${error.message}`;
        elements.downloadResult.appendChild(downloadResult);

        // 3秒后恢复按钮到初始状态
        setTimeout(() => {
            downloadBtn.disabled = false;
            downloadBtn.textContent = originalText;
        }, 3000);
    }
}
