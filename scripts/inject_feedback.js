/**
 * scripts/inject_feedback.js
 * ==========================
 * 前端按钮注入脚本。
 *
 * 使用 MutationObserver 动态在对话下方注入 [👍 认可] / [👎 不认可] 按钮。
 * 在 OpenClaw 的"自定义前端脚本"设置中粘贴此脚本即可启用。
 *
 * 配置项：
 *   - SMARTROUTER_BASE_URL: 反馈上报地址（默认同源 /v1/feedback）
 *   - 按钮选择器可按宿主 UI 调整
 */
(function () {
    'use strict';

    // ============ 配置 ============
    const CONFIG = {
        // 反馈上报地址，独立模式默认同源；插件模式可改为 /smart-router/api/feedback
        feedbackUrl: (window.SMARTROUTER_BASE_URL || '') + '/v1/feedback',
        // 对话消息容器选择器（按宿主 UI 调整）
        messageSelector: '.message-content, .markdown-body, [class*="message"], [class*="answer"], [class*="response"]',
        // 已注入标记
        injectedFlag: 'data-sr-feedback-injected',
        // 按钮样式
        btnClass: 'sr-feedback-btn',
    };

    // ============ 样式注入 ============
    const style = document.createElement('style');
    style.textContent = `
        .sr-feedback-container {
            display: flex;
            gap: 8px;
            margin-top: 8px;
            padding-top: 8px;
            border-top: 1px solid rgba(0,0,0,0.06);
        }
        .${CONFIG.btnClass} {
            display: inline-flex;
            align-items: center;
            gap: 4px;
            padding: 4px 10px;
            font-size: 12px;
            border: 1px solid #e5e7eb;
            border-radius: 16px;
            background: #fff;
            color: #6b7280;
            cursor: pointer;
            transition: all 0.15s;
            user-select: none;
        }
        .${CONFIG.btnClass}:hover {
            border-color: #3b82f6;
            color: #3b82f6;
        }
        .${CONFIG.btnClass}.active-positive {
            background: #ecfdf5;
            border-color: #10b981;
            color: #10b981;
        }
        .${CONFIG.btnClass}.active-negative {
            background: #fef2f2;
            border-color: #ef4444;
            color: #ef4444;
        }
        .${CONFIG.btnClass}:disabled {
            opacity: 0.5;
            cursor: not-allowed;
        }
    `;
    (document.head || document.documentElement).appendChild(style);

    // ============ 工具函数 ============
    function getRequestId(messageEl) {
        // 尝试从父容器或 data 属性获取 request_id
        let parent = messageEl.closest('[data-request-id], [data-id], [id]');
        if (parent) {
            return parent.getAttribute('data-request-id')
                || parent.getAttribute('data-id')
                || parent.id
                || '';
        }
        // 兜底：使用内容哈希
        const text = messageEl.textContent || '';
        let hash = 0;
        for (let i = 0; i < text.length; i++) {
            hash = ((hash << 5) - hash) + text.charCodeAt(i);
            hash |= 0;
        }
        return 'msg_' + Math.abs(hash).toString(36);
    }

    function getPromptSnapshot() {
        // 取最近一条用户消息作为上下文快照
        const userMsgs = document.querySelectorAll(
            '[class*="user"], [class*="question"], [data-role="user"]'
        );
        if (userMsgs.length === 0) return '';
        return userMsgs[userMsgs.length - 1].textContent?.slice(0, 500) || '';
    }

    async function submitFeedback(requestId, sentiment, contextSnapshot) {
        try {
            const resp = await fetch(CONFIG.feedbackUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    request_id: requestId,
                    sentiment: sentiment,
                    context_snapshot: contextSnapshot,
                    prompt: contextSnapshot,
                }),
            });
            return resp.ok;
        } catch (e) {
            console.warn('[SmartRouter] feedback submit failed:', e);
            return false;
        }
    }

    // ============ 按钮注入 ============
    function injectButtons(messageEl) {
        if (messageEl.hasAttribute(CONFIG.injectedFlag)) return;
        // 跳过用户消息（只对 AI 回复注入）
        const parent = messageEl.closest('[class*="user"], [data-role="user"]');
        if (parent) return;

        messageEl.setAttribute(CONFIG.injectedFlag, '1');

        const container = document.createElement('div');
        container.className = 'sr-feedback-container';

        const requestId = getRequestId(messageEl);

        const btnPositive = document.createElement('button');
        btnPositive.className = CONFIG.btnClass;
        btnPositive.innerHTML = '👍 认可';
        btnPositive.addEventListener('click', async function () {
            btnPositive.disabled = true;
            btnPositive.classList.add('active-positive');
            btnNegative.classList.remove('active-negative');
            await submitFeedback(requestId, 'positive', getPromptSnapshot());
            btnPositive.disabled = false;
        });

        const btnNegative = document.createElement('button');
        btnNegative.className = CONFIG.btnClass;
        btnNegative.innerHTML = '👎 不认可';
        btnNegative.addEventListener('click', async function () {
            btnNegative.disabled = true;
            btnNegative.classList.add('active-negative');
            btnPositive.classList.remove('active-positive');
            await submitFeedback(requestId, 'negative', getPromptSnapshot());
            btnNegative.disabled = false;
        });

        container.appendChild(btnPositive);
        container.appendChild(btnNegative);
        messageEl.appendChild(container);
    }

    // ============ MutationObserver ============
    function scanAndInject(root) {
        try {
            const targets = root.querySelectorAll
                ? root.querySelectorAll(CONFIG.messageSelector)
                : [];
            targets.forEach(injectButtons);
            // 如果 root 本身匹配
            if (root.matches && root.matches(CONFIG.messageSelector)) {
                injectButtons(root);
            }
        } catch (e) {
            // 静默失败
        }
    }

    const observer = new MutationObserver(function (mutations) {
        for (const mutation of mutations) {
            for (const node of mutation.addedNodes) {
                if (node.nodeType === Node.ELEMENT_NODE) {
                    scanAndInject(node);
                }
            }
        }
    });

    // ============ 启动 ============
    function start() {
        scanAndInject(document.body);
        observer.observe(document.body, {
            childList: true,
            subtree: true,
        });
        console.log('[SmartRouter] 反馈按钮注入脚本已启动');
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }

    // 暴露 API 供外部调用
    window.SmartRouterFeedback = {
        refresh: () => scanAndInject(document.body),
        config: CONFIG,
    };
})();
