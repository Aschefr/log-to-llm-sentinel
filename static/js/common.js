// Utilitaires communs
async function apiFetch(url, options = {}) {
    const defaults = {
        headers: {
            'Content-Type': 'application/json',
        },
    };

    const config = { ...defaults, ...options };
    if (config.body && typeof config.body === 'object') {
        config.body = JSON.stringify(config.body);
    }

    const response = await fetch(url, config);
    if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: 'error_unknown' }));
        let detail = error.detail || 'error_api';
        if (window.t) {
            const transApi = window.t('api_errors.' + detail);
            if (transApi !== 'api_errors.' + detail) detail = transApi;
            else {
                const transCommon = window.t('common.' + detail);
                if (transCommon !== 'common.' + detail) detail = transCommon;
            }
        }
        throw new Error(detail);
    }
    return response.json();
}

function showMessage(element, message, type = 'success') {
    element.textContent = message;
    element.className = `message ${type}`;
    element.classList.remove('hidden');
    setTimeout(() => element.classList.add('hidden'), 3000);
}

function formatDate(dateString) {
    if (!dateString) return '';
    // Si la chaîne ne finit pas par Z et n'a pas de fuseau horaire, 
    // on ajoute Z pour forcer l'interprétation UTC (format de la DB)
    let utcString = dateString;
    if (!dateString.endsWith('Z') && !dateString.includes('+')) {
        utcString += 'Z';
    }
    
    const date = new Date(utcString);
    return date.toLocaleString('fr-FR', {
        day: '2-digit',
        month: '2-digit',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
    });
}

/**
 * Returns a relative time string like "Il y a 02:34" (or "2:34 ago" in EN).
 * @param {string} dateString - ISO date string from DB
 * @returns {string} relative time HTML or empty string
 */
function formatRelativeTime(dateString) {
    if (!dateString) return '';
    let utcString = dateString;
    if (!dateString.endsWith('Z') && !dateString.includes('+')) {
        utcString += 'Z';
    }
    const date = new Date(utcString);
    const diffMs = Date.now() - date.getTime();
    if (diffMs < 0) return '';

    const totalSec = Math.floor(diffMs / 1000);
    const hours = Math.floor(totalSec / 3600);
    const mins = Math.floor((totalSec % 3600) / 60);
    const secs = totalSec % 60;

    const pad = n => String(n).padStart(2, '0');
    const timeStr = hours > 0
        ? `${pad(hours)}:${pad(mins)}:${pad(secs)}`
        : `${pad(mins)}:${pad(secs)}`;

    const prefix = window.t ? window.t('common.time_ago') : 'Il y a';
    return `${prefix} ${timeStr}`;
}

function escapeHtml(text) {
    if (!text) return '';
    const map = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;'
    };
    return text.toString().replace(/[&<>"']/g, function(m) { return map[m]; });
}

function copyToClipboard(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
        return navigator.clipboard.writeText(text);
    } else {
        // Fallback pour environnements non-sécurisés (HTTP via IP)
        return new Promise((resolve, reject) => {
            try {
                const textArea = document.createElement("textarea");
                textArea.value = text;
                textArea.style.position = "fixed";
                textArea.style.left = "-9999px";
                textArea.style.top = "0";
                document.body.appendChild(textArea);
                textArea.focus();
                textArea.select();
                const successful = document.execCommand('copy');
                document.body.removeChild(textArea);
                if (successful) resolve();
                else reject(new Error('ExecCommand copy failed'));
            } catch (err) {
                reject(err);
            }
        });
    }
}


document.addEventListener('DOMContentLoaded', () => {
    pollSystemStats();
    restoreUpdateBadge();
});

async function pollSystemStats() {
    updateSystemStatsUI();
    setInterval(updateSystemStatsUI, 10000);
}

async function updateSystemStatsUI() {
    try {
        const stats = await apiFetch('/api/dashboard/system-stats');
        const cpuAppEl = document.getElementById('stat-cpu-app');
        const cpuSysEl = document.getElementById('stat-cpu-sys');
        const ramEl = document.getElementById('stat-ram');
        const uptimeEl = document.getElementById('stat-uptime');
        
        if (cpuAppEl) cpuAppEl.textContent = `${Math.round(stats.app_cpu)}%`;
        if (cpuSysEl) cpuSysEl.textContent = `${Math.round(stats.sys_cpu)}%`;
        if (ramEl) ramEl.textContent = `${stats.app_ram} MB`;
        if (uptimeEl) uptimeEl.textContent = formatUptime(stats.uptime);
    } catch (e) {}
}

function formatUptime(seconds) {
    if (seconds < 60) return `${seconds}s`;
    if (seconds < 3600) return `${Math.floor(seconds/60)}m`;
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    return `${h}h${m}m`;
}

async function openChat(analysisId, rawPrompt = null, rawResponse = null) {
    try {
        const res = await apiFetch('/chat/api/create', {
            method: 'POST',
            body: { 
                analysis_id: analysisId,
                raw_context_prompt: rawPrompt,
                raw_context_response: rawResponse
            }
        });
        if (res.id) {
            window.location.href = `/chat?id=${res.id}`;
        }
    } catch (e) {
        const errorPrefix = window.t ? window.t('chat.error_create_conv') : 'Erreur lors de la création de la conversation :';
        alert(`${errorPrefix} ${e.message}`);
    }
}

async function askQuestion(analysisId, inputEl, contextPrompt = null, contextResponse = null) {
    const question = inputEl.value.trim();
    if (!question) return;

    const historyEl = document.getElementById(`chat-history-${analysisId}`);
    if (!historyEl) return;

    // Ajouter la question utilisateur
    const userLabel = window.t ? window.t('chat.label_user') : 'Vous';
    historyEl.innerHTML += `<div class="chat-msg user"><strong>${userLabel} :</strong> ${escapeHtml(question)}</div>`;
    inputEl.value = '';

    const abortController = new AbortController();

    const aiMsgEl = document.createElement('div');
    aiMsgEl.className = 'chat-msg ai';
    const ollamaLabel = window.t ? window.t('chat.label_ollama') : 'Ollama';
    const stopLabel = window.t ? window.t('common.stop') : 'Arrêter';
    aiMsgEl.innerHTML = `<strong>${ollamaLabel} :</strong> <span class="ai-content">⏳...</span> <button class="btn btn-danger btn-sm stop-chat-btn" style="float: right; padding: 2px 5px; font-size: 0.8rem;">🛑 ${stopLabel}</button>`;
    historyEl.appendChild(aiMsgEl);
    historyEl.scrollTop = historyEl.scrollHeight;
    
    const stopBtn = aiMsgEl.querySelector('.stop-chat-btn');
    const contentSpan = aiMsgEl.querySelector('.ai-content');
    stopBtn.onclick = () => abortController.abort();

    try {
        const res = await apiFetch('/api/monitor/chat', {
            method: 'POST',
            body: {
                analysis_id: analysisId,
                question: question,
                context_prompt: contextPrompt,
                context_response: contextResponse
            },
            signal: abortController.signal
        });

        if (res.status === 'ok') {
            contentSpan.innerHTML = marked.parse(res.response);
        } else {
            contentSpan.innerHTML = `${window.t ? window.t('common.error') : 'Erreur'} : ${res.detail}`;
        }
    } catch (e) {
        if (e.name === 'AbortError') {
            const cancelledMsg = window.t ? window.t('chat.generation_cancelled') : 'Génération annulée.';
            contentSpan.innerHTML = `❌ ${cancelledMsg}`;
        } else {
            contentSpan.innerHTML = `${window.t ? window.t('common.error') : 'Erreur'} : ${e.message}`;
        }
    } finally {
        if (stopBtn && stopBtn.parentNode) {
            stopBtn.parentNode.removeChild(stopBtn);
        }
    }
    historyEl.scrollTop = historyEl.scrollHeight;
}

function toggleSection(containerId, arrowId) {
    const container = document.getElementById(containerId);
    const arrow = document.getElementById(arrowId);
    if (!container) return;

    const isHidden = container.classList.contains('hidden');
    if (isHidden) {
        container.classList.remove('hidden');
        if (arrow) arrow.classList.add('expanded');
    } else {
        container.classList.add('hidden');
        if (arrow) arrow.classList.remove('expanded');
    }
}

/**
 * Surligne les occurrences de mots-clés dans un texte brut.
 * Retourne du HTML sécurisé avec les mots-clés entourés de <mark class="kw-highlight">.
 * @param {string} text - Texte brut à afficher (sera échappé)
 * @param {string[]} keywords - Liste de mots-clés à surligner
 * @returns {string} HTML avec surbrillance
 */
function highlightKeywords(text, keywords) {
    if (!text) return '';
    if (!keywords || keywords.length === 0) return escapeHtml(text);

    // Construire un regex combiné insensible à la casse
    const escaped = keywords
        .filter(k => k && k.trim())
        .map(k => k.trim().replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));

    if (escaped.length === 0) return escapeHtml(text);

    const pattern = new RegExp(`(${escaped.join('|')})`, 'gi');

    // Découper le texte en parties, échapper chacune, wrapper les matches
    return text.split(pattern).map((part, i) => {
        if (i % 2 === 1) {
            // C'est un match — on l'entoure d'un mark
            return `<mark class="kw-highlight">${escapeHtml(part)}</mark>`;
        }
        return escapeHtml(part);
    }).join('');
}

function showInlineConfirm(btnElement, message, onConfirmCallback) {
    // 1. Fermer toute popup de confirmation existante
    document.querySelectorAll('.inline-confirm-popup').forEach(el => el.remove());
    
    // 2. Créer la popup
    const popup = document.createElement('div');
    popup.className = 'inline-confirm-popup';
    
    popup.innerHTML = `
        <div class="inline-confirm-msg">${escapeHtml(message)}</div>
        <div class="inline-confirm-actions">
            <button class="btn btn-primary btn-sm confirm-btn">${window.t ? window.t('common.confirm') : 'OK'}</button>
            <button class="btn btn-secondary btn-sm cancel-btn">${window.t ? window.t('common.cancel') : 'Annuler'}</button>
        </div>
    `;
    
    document.body.appendChild(popup);
    
    // 3. Positionnement par rapport au bouton
    const rect = btnElement.getBoundingClientRect();
    popup.style.top = `${rect.bottom + window.scrollY + 8}px`;
    
    let leftPos = rect.left + window.scrollX + (rect.width / 2) - (popup.offsetWidth / 2);
    if (leftPos < 10) leftPos = 10;
    if (leftPos + popup.offsetWidth > window.innerWidth - 10) {
        leftPos = window.innerWidth - popup.offsetWidth - 10;
    }
    popup.style.left = `${leftPos}px`;
    
    // 4. Événements
    const confirmBtn = popup.querySelector('.confirm-btn');
    const cancelBtn = popup.querySelector('.cancel-btn');
    
    const closePopup = () => popup.remove();
    
    confirmBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        closePopup();
        onConfirmCallback();
    });
    
    cancelBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        closePopup();
    });
    
    // Fermer si on clique ailleurs
    setTimeout(() => {
        const outsideClickListener = (e) => {
            if (!popup.contains(e.target) && e.target !== btnElement && !btnElement.contains(e.target)) {
                closePopup();
                document.removeEventListener('click', outsideClickListener);
            }
        };
        document.addEventListener('click', outsideClickListener);
    }, 10);
}

/**
 * Apply a check result object to the update badge UI.
 */
function applyUpdateBadge(status) {
    const badge = document.getElementById('version-update-badge');
    if (!badge) return;
    badge.classList.remove('checking', 'update-available', 'system-up-to-date', 'check-failed');

    if (!status || !status.checked) {
        // Never checked yet — show neutral
        badge.innerHTML = `<span class="check-icon">—</span>`;
        return;
    }

    if (status.error) {
        badge.classList.add('check-failed');
        const errKey = 'header.check_failed';
        const errText = window.t ? window.t(errKey) : 'Check failed';
        badge.innerHTML = `<span class="stat-icon">⚠️</span> <span data-i18n="${errKey}">${errText}</span>`;
        badge.setAttribute('data-i18n-title', errKey);
        badge.setAttribute('title', errText);
    } else if (status.is_available) {
        badge.classList.add('update-available');
        const avKey = 'header.update_available';
        const avText = window.t ? window.t(avKey) : 'Update available';
        const instKey = 'header.update_instructions';
        const instText = window.t ? window.t(instKey) : 'git pull && docker compose up -d';
        badge.innerHTML = `<span class="pulse-dot"></span> <span data-i18n="${avKey}">${avText}</span>`;
        badge.setAttribute('data-i18n-title', instKey);
        badge.setAttribute('title', instText);
    } else {
        badge.classList.add('system-up-to-date');
        const upKey = 'header.up_to_date';
        const upText = window.t ? window.t(upKey) : 'Up to date';
        badge.innerHTML = `<span class="check-icon">✓</span> <span data-i18n="${upKey}">${upText}</span>`;
        badge.setAttribute('data-i18n-title', upKey);
        badge.setAttribute('title', upText);
    }
}

/**
 * On page load: restore badge state from server cache (persisted across navigations).
 */
async function restoreUpdateBadge() {
    try {
        const status = await apiFetch('/api/system/update-status');
        applyUpdateBadge(status);
    } catch (e) { /* silently ignore — badge stays neutral */ }
}

/**
 * Triggers a manual check for app updates from GitHub.
 */
async function checkAppUpdate() {
    const badge = document.getElementById('version-update-badge');
    if (!badge || badge.classList.contains('checking')) return;

    // State: Checking
    badge.classList.add('checking');
    const labelKey = 'header.checking';
    const labelText = window.t ? window.t(labelKey) : 'Checking...';
    
    badge.innerHTML = `<span class="pulse-dot" style="animation-duration: 0.8s;"></span> <span data-i18n="${labelKey}">${labelText}</span>`;
    badge.setAttribute('title', labelText);

    try {
        const status = await apiFetch('/api/system/update-check');
        applyUpdateBadge(status);
    } catch (e) {
        const badge = document.getElementById('version-update-badge');
        if (badge) {
            badge.classList.remove('checking');
            badge.classList.add('check-failed');
            const errKey = 'header.check_failed';
            const errText = window.t ? window.t(errKey) : 'Check failed';
            badge.innerHTML = `<span class="stat-icon">⚠️</span> <span data-i18n="${errKey}">${errText}</span>`;
            badge.setAttribute('title', errText);
        }
    }
}

// ─── Shared Analysis Card Template ──────────────────────────────────────────
// Used by dashboard.js, monitor.js (analyses list, search result, line detail)
// Options:
//   collapsed  : bool  — start collapsed (default: true)
//   showDelete : bool  — show delete button (default: false)
//   showCopy   : bool  — show copy button (default: false)
//   showRuleName: bool — show rule name in header (default: true)
//   cardClass  : string — extra CSS class on wrapper (default: '')
//   onToggle   : string — onclick function name for toggle (default: generic)

function renderAnalysisCard(a, opts = {}) {
    const collapsed = opts.collapsed !== false;
    const showDelete = opts.showDelete || false;
    const showCopy = opts.showCopy !== false;
    const showRuleName = opts.showRuleName !== false;
    const cardClass = opts.cardClass || 'analysis-card';
    const _t = (k, fb) => window.t ? window.t(k) : fb;

    const collapsedCls = collapsed ? ' collapsed' : '';
    const toggleArrow = collapsed ? '▶' : '▼';

    // Keywords summary for collapsed header
    const kwSummary = collapsed && a.matched_keywords?.length
        ? `<span class="analysis-kw-summary">${a.matched_keywords.slice(0,3).map(k => `<span class="log-kw-badge">${escapeHtml(k)}</span>`).join('')}${a.matched_keywords.length > 3 ? `<span class="log-kw-badge">+${a.matched_keywords.length - 3}</span>` : ''}</span>`
        : '';

    // Copy SVG icon
    const copyBtn = showCopy ? `
        <button class="btn-icon" onclick="event.stopPropagation(); copyAnalysisText(this)" title="${_t('common.copy_analysis', 'Copy')}">
            <svg viewBox="0 0 24 24" width="14" height="14"><path fill="currentColor" d="M19,21H8V7H19M19,5H8A2,2 0 0,0 6,7V21A2,2 0 0,0 8,23H19A2,2 0 0,0 21,21V7A2,2 0 0,0 19,5M16,1H4A2,2 0 0,0 2,3V17H4V3H16V1Z" /></svg>
        </button>` : '';

    const deleteBtn = showDelete ? `
        <button class="btn-icon delete-analysis-btn" onclick="event.stopPropagation(); deleteAnalysis(${a.id}, this)" title="${_t('common.delete_analysis', 'Delete')}">
            <svg viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M19,4H15.5L14.5,3H9.5L8.5,4H5V6H19V4M6,19A2,2 0 0,0 8,21H16A2,2 0 0,0 18,19V7H6V19Z" /></svg>
        </button>` : '';

    const ruleName = showRuleName && a.rule_name
        ? `<strong>${_t('dashboard.rule_label', 'Rule:')} ${escapeHtml(a.rule_name)}</strong>`
        : '';

    return `
    <div class="${cardClass}${collapsedCls}" id="analysis-card-${a.id}">
        <div class="analysis-header" onclick="toggleAnalysisCardGeneric(event, this.parentElement)" style="cursor: pointer;">
            <div>
                <span class="collapse-toggle">${toggleArrow}</span>
                ${ruleName}
                ${a.detection_id ? `<span class="detection-id-badge" style="margin-left:0.5rem">#${escapeHtml(a.detection_id)}</span>` : ''}
                <span class="analysis-time">${a.analyzed_at ? formatDate(a.analyzed_at) : ''}</span>
                <span class="severity-badge ${escapeHtml(a.severity)}">${escapeHtml(a.severity)}</span>
            </div>
            <div class="analysis-actions">
                ${kwSummary}
                ${copyBtn}
                ${deleteBtn}
            </div>
        </div>
        <div class="analysis-body">
            ${a.matched_keywords && a.matched_keywords.length > 0 ? `
            <div class="analysis-keywords">
                <span class="kw-label">${_t('monitor.keywords', 'Keywords')} :</span>
                ${a.matched_keywords.map(k => `<span class="log-kw-badge">${escapeHtml(k)}</span>`).join(' ')}
            </div>` : ''}
            <div class="analysis-line">${highlightKeywords(a.triggered_line || '', a.matched_keywords || [])}</div>
            <div class="analysis-response markdown-body">${a.ollama_response ? marked.parse(a.ollama_response) : ''}</div>
            <div class="analysis-footer">
                <div style="display: flex; gap: 0.5rem;">
                    <button class="btn btn-secondary btn-sm" onclick="event.stopPropagation(); retryAnalysis(${a.id}, this)">🔄 ${_t('common.retry', 'Retry')}</button>
                    <button class="btn btn-secondary btn-sm" onclick="event.stopPropagation(); notifyAnalysis(${a.id}, this)">🔔 ${_t('common.notify', 'Notify')}</button>
                    ${a.detection_id ? `<button class="btn btn-secondary btn-sm" onclick="event.stopPropagation(); window.location.href='/monitor?search=${encodeURIComponent(a.detection_id)}'" title="${_t('common.view_in_monitor', 'View in Monitor')}">🔍 Monitor</button>` : ''}
                </div>
                <button class="btn btn-primary btn-sm" onclick="event.stopPropagation(); openChat(${a.id})">💬 ${_t('common.deepen', 'Deepen')}</button>
            </div>
        </div>
    </div>`;
}

function toggleAnalysisCardGeneric(event, card) {
    if (event.target.closest('button, a, .btn')) return;
    card.classList.toggle('collapsed');
    const toggle = card.querySelector('.collapse-toggle');
    if (toggle) toggle.textContent = card.classList.contains('collapsed') ? '▶' : '▼';
}
/* ── Server Time Display ── */
function updateServerTime() {
    const el = document.getElementById('server-time-display');
    if (!el) return;
    const now = new Date();
    el.textContent = now.toLocaleString(window._currentLang || 'fr-FR', {
        hour: '2-digit', minute: '2-digit', second: '2-digit',
        day: '2-digit', month: '2-digit', year: 'numeric'
    });
}
setInterval(updateServerTime, 1000);
updateServerTime();
