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

/** Affiche / masque les messages exacts envoyés au LLM pour une analyse. */
async function togglePromptView(analysisId, btn) {
    const holder = btn.closest('.analysis-footer, .detail-actions') || btn.parentElement;
    const next = holder.nextElementSibling;
    if (next && next.classList.contains('prompt-view')) {
        next.remove();
        return;
    }
    const _t = (k, fb) => (window.t && window.t(k) !== k) ? window.t(k) : fb;
    try {
        const res = await apiFetch(`/api/monitor/analyses/${analysisId}/prompt`);
        const text = res.messages
            ? res.messages.map(m => `[${m.role.toUpperCase()}]\n${m.content}`).join('\n\n')
            : _t('common.prompt_unavailable', 'Prompt not stored for this analysis (created before v1.2.303).');
        holder.insertAdjacentHTML('afterend',
            `<pre class="prompt-view" onclick="event.stopPropagation()" style="font-family:monospace; font-size:0.75rem; white-space:pre-wrap; word-break:break-word; max-height:400px; overflow-y:auto; color:var(--text-secondary); background:var(--bg-secondary); border:1px solid var(--border); border-radius:4px; padding:0.75rem; margin-top:0.5rem;">${escapeHtml(text)}</pre>`);
    } catch (e) {
        alert(e.message);
    }
}

/** Ouvre une conversation d'investigation guidée (l'IA consulte les logs via ses outils). */
async function investigateAnalysis(analysisId) {
    try {
        const res = await apiFetch('/chat/api/investigate', {
            method: 'POST',
            body: { analysis_id: analysisId }
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
            contentSpan.innerHTML = renderMarkdown(res.response);
        } else {
            contentSpan.innerHTML = `${window.t ? window.t('common.error') : 'Erreur'} : ${escapeHtml(res.detail)}`;
        }
    } catch (e) {
        if (e.name === 'AbortError') {
            const cancelledMsg = window.t ? window.t('chat.generation_cancelled') : 'Génération annulée.';
            contentSpan.innerHTML = `❌ ${cancelledMsg}`;
        } else {
            contentSpan.innerHTML = `${window.t ? window.t('common.error') : 'Erreur'} : ${escapeHtml(e.message)}`;
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
// Used by dashboard.js and rule_page.js (analyses lists)
// Options:
//   collapsed  : bool  — start collapsed (default: true)
//   showDelete : bool  — show delete button (default: false)
//   showCopy   : bool  — show copy button (default: false)
//   showRuleName: bool — show rule name in header (default: true)
//   cardClass  : string — extra CSS class on wrapper (default: '')
//   onToggle   : string — onclick function name for toggle (default: generic)

/** Durée lisible à partir d'un nombre de minutes (« 34 min », « 1 h 05 », « 2 j »). */
function formatMinutesShort(m) {
    if (m == null) return '';
    if (m < 1) return '< 1 min';
    if (m < 60) return `${Math.round(m)} min`;
    if (m < 1440) { const h = Math.floor(m / 60), mm = Math.round(m % 60); return mm ? `${h} h ${String(mm).padStart(2, '0')}` : `${h} h`; }
    return `${Math.floor(m / 1440)} ${(window.t && window.t('rule.day_short') !== 'rule.day_short') ? window.t('rule.day_short') : 'j'}`;
}

// AI answers quote log lines, and anyone on the network can send a syslog line: their HTML is not trusted.
// Images and styles are refused too: an external image would send data out just by opening the page.
const MARKDOWN_SANITIZE = {
    USE_PROFILES: { html: true },
    FORBID_TAGS: ['img', 'picture', 'source', 'video', 'audio', 'style', 'form', 'input', 'button', 'select', 'textarea'],
    FORBID_ATTR: ['style', 'id', 'name', 'srcset'],
};

/** Markdown to safe HTML; plain text when a library is not available, so a card always renders. */
function renderMarkdown(text) {
    if (window.marked && window.DOMPurify) return DOMPurify.sanitize(marked.parse(text || ''), MARKDOWN_SANITIZE);
    return `<p style="white-space: pre-wrap;">${escapeHtml(text || '')}</p>`;
}

function renderAnalysisCard(a, opts = {}) {
    const collapsed = opts.collapsed !== false;
    const showDelete = opts.showDelete || false;
    const showCopy = opts.showCopy !== false;
    const showRuleName = opts.showRuleName !== false;
    const cardClass = opts.cardClass || 'analysis-card';
    const _t = (k, fb) => window.t ? window.t(k) : fb;

    const collapsedCls = collapsed ? ' collapsed' : '';
    const toggleArrow = collapsed ? '▶' : '▼';

    const ev = opts.event;  // événement regroupant cette analyse, s'il y en a un
    const flagged = ev ? ev.flagged : a.feedback === 'irrelevant';
    const isUnread = ev ? ev.unread : a.viewed === false || a.viewed === 0;
    const unreadCls = isUnread ? ' unread' : '';
    const unreadIndicator = isUnread ? `<span class="unread-dot-indicator" title="${_t('monitor.unviewed_tooltip', 'Non consultée')}">●</span>` : '';

    // Keywords summary for collapsed header
    const kwSummary = a.matched_keywords?.length
        ? `<span class="analysis-kw-summary">${a.matched_keywords.slice(0,3).map(k => `<span class="log-kw-badge">${escapeHtml(k)}</span>`).join('')}${a.matched_keywords.length > 3 ? `<span class="log-kw-badge">+${a.matched_keywords.length - 3}</span>` : ''}</span>`
        : '';

    // Copy SVG icon
    const copyBtn = showCopy ? `
        <button class="btn-icon" onclick="event.stopPropagation(); copyAnalysisText(this)" title="${_t('common.copy_analysis', 'Copy')}">
            <svg viewBox="0 0 24 24" width="14" height="14"><path fill="currentColor" d="M19,21H8V7H19M19,5H8A2,2 0 0,0 6,7V21A2,2 0 0,0 8,23H19A2,2 0 0,0 21,21V7A2,2 0 0,0 19,5M16,1H4A2,2 0 0,0 2,3V17H4V3H16V1Z" /></svg>
        </button>` : '';

    const deleteBtn = showDelete ? `
        <button class="btn-icon delete-analysis-btn" onclick="event.stopPropagation(); ${ev ? `deleteEvent(${ev.rule_id}, '${ev.key}', ${ev.count}, this)` : `deleteAnalysis(${a.id}, this)`}" title="${_t('common.delete_analysis', 'Delete')}">
            <svg viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M19,4H15.5L14.5,3H9.5L8.5,4H5V6H19V4M6,19A2,2 0 0,0 8,21H16A2,2 0 0,0 18,19V7H6V19Z" /></svg>
        </button>` : '';

    const ruleName = showRuleName && a.rule_name
        ? `<strong>${_t('dashboard.rule_label', 'Rule:')} ${escapeHtml(a.rule_name)}</strong>`
        : '';

    const resolvedBadge = a.resolution_status === 'resolved'
        ? `<span class="resolved-badge">${a.resolution_minutes != null
            ? escapeHtml(_t('common.resolved_in', 'Résolu en {d}').replace('{d}', formatMinutesShort(a.resolution_minutes)))
            : _t('monitor.badge_resolved', 'Résolu')}</span>` : '';
    // Récurrence sur 7 jours (même événement à des valeurs variables près)
    let recurBadge = '';
    if (ev) {
        if (ev.count > 1) recurBadge = `<span class="recur-badge event-count" title="${escapeHtml(_t('common.event_count_title', '{n} occurrences de cet événement').replace('{n}', ev.count))}">× ${ev.count}</span>`;
    } else if (a.recurrence_7d != null) {
        recurBadge = a.recurrence_7d > 0
            ? `<span class="recur-badge" title="${escapeHtml(_t('common.recurrence_title', 'Déjà vu dans les 7 jours précédents'))}">${escapeHtml(_t('common.recurrence', '× {n} en 7 j').replace('{n}', a.recurrence_7d))}</span>`
            : `<span class="recur-badge recur-badge--new">${escapeHtml(_t('common.recurrence_new', 'nouveau'))}</span>`;
    }
    const summaryLine = a.summary && opts.showSummary !== false
        ? `<div class="analysis-summary-line">${escapeHtml(a.summary)}</div>` : '';

    let resolutionBlock = '';
    if (a.resolution_status === 'resolved' && a.resolved_at && a.analyzed_at) {
        const start = new Date(a.analyzed_at);
        const end = new Date(a.resolved_at);
        const diffMs = end - start;
        let durationStr = 'N/A';
        if (diffMs > 0) {
            const totalSec = Math.floor(diffMs / 1000);
            const hrs = Math.floor(totalSec / 3600);
            const mins = Math.floor((totalSec % 3600) / 60);
            const secs = totalSec % 60;
            if (hrs > 0) {
                durationStr = `${hrs}h ${mins}m`;
            } else if (mins > 0) {
                durationStr = `${mins}m ${secs}s`;
            } else {
                durationStr = `${secs}s`;
            }
        }
        
        let detailsHtml = `
            <div><span class="detail-label">${_t('monitor.resolved_at_label', 'Résolu le :')}</span> <span>${formatDate(a.resolved_at)}</span></div>
            <div><span class="detail-label">${_t('monitor.alert_duration_label', 'Durée de l\'alerte :')}</span> <span>${durationStr}</span></div>
        `;

        if (a.resolution_line) {
            detailsHtml += `
            <div style="flex-basis: 100%; margin-top: 0.25rem;"><span class="detail-label">${_t('monitor.resolution_line_label', 'Ligne de résolution :')}</span> <code class="resolution-line-code" style="word-break: break-all; font-size: 0.78rem; display: block; margin-top: 0.15rem; background: rgba(255,255,255,0.05); padding: 0.25rem 0.5rem; border-radius: 3px;">${escapeHtml(a.resolution_line)}</code></div>
            `;
        }

        if (a.resolution_patterns && a.resolution_patterns.length > 0) {
            detailsHtml += `
            <div style="flex-basis: 100%; margin-top: 0.25rem;"><span class="detail-label">${_t('monitor.resolution_patterns_label', 'Motifs détectés :')}</span> <span>${a.resolution_patterns.map(p => `<span class="log-kw-badge" style="background: rgba(16, 185, 129, 0.15); color: var(--success); border: 1px solid rgba(16, 185, 129, 0.3); font-size: 0.72rem; padding: 0.05rem 0.3rem; border-radius: 3px;">${escapeHtml(p)}</span>`).join(' ')}</span></div>
            `;
        }

        if (a.resolution_ai_explanation) {
            const confBadge = a.resolution_ai_confidence ? ` <span class="severity-badge info" style="background: rgba(59, 130, 246, 0.15); color: var(--info); border: 1px solid rgba(59, 130, 246, 0.35); font-size: 0.7rem; padding: 0.05rem 0.25rem; font-weight: normal; vertical-align: middle;">${a.resolution_ai_confidence}%</span>` : '';
            detailsHtml += `
            <div style="flex-basis: 100%; margin-top: 0.4rem; border-top: 1px dashed rgba(16, 185, 129, 0.15); padding-top: 0.4rem;"><span class="detail-label">${_t('monitor.resolution_ai_conclusion', 'Conclusion de l\'IA :')}</span>${confBadge}<div style="margin-top: 0.15rem; font-style: italic; font-size: 0.8rem; opacity: 0.85;">${escapeHtml(a.resolution_ai_explanation)}</div></div>
            `;
        }

        resolutionBlock = `
        <div class="analysis-resolution-box" style="margin-bottom: 0.8rem;">
            <div class="resolution-title">
                <span aria-hidden="true">🟢 </span><span data-i18n="monitor.resolution_box_title">${_t('monitor.resolution_box_title', 'Retour à la normale')}</span>
            </div>
            <div class="resolution-details" style="display: flex; flex-wrap: wrap; gap: 0.5rem 1.5rem; font-size: 0.82rem;">
                ${detailsHtml}
            </div>
        </div>`;
    }

    return `
    <div class="${cardClass}${collapsedCls}${unreadCls}" id="analysis-card-${a.id}"${ev ? ` data-event="${ev.rule_id}-${ev.key}"` : ''}>
        <div class="analysis-header" onclick="toggleAnalysisCardGeneric(event, this.parentElement)" style="cursor: pointer;">
            <div>
                <button type="button" class="collapse-toggle" aria-expanded="${!collapsed}" aria-label="${escapeHtml(_t('common.toggle_details', 'Afficher ou masquer le détail'))}">${toggleArrow}</button>
                ${ruleName}
                ${a.detection_id ? `<span class="detection-id-badge" style="margin-left:0.5rem">#${escapeHtml(a.detection_id)}</span>` : ''}
                <span class="analysis-time">${a.analyzed_at ? formatDate(a.analyzed_at) : ''}</span>
                <span class="severity-badge ${escapeHtml(ev ? ev.severity : a.severity)}">${escapeHtml(ev ? ev.severity : a.severity)}</span>
                ${resolvedBadge}
                ${recurBadge}
                ${ev ? `<span class="event-note-count${(ev.notes || []).length ? '' : ' hidden'}" title="${escapeHtml(_t('common.notes_title', 'Notes pour l’IA'))}"><span aria-hidden="true">📝 </span><span>${(ev.notes || []).length}</span></span>` : ''}
                ${unreadIndicator}
                ${summaryLine}
            </div>
            <div class="analysis-actions">
                ${kwSummary}
                ${copyBtn}
                ${deleteBtn}
            </div>
        </div>
        <div class="analysis-body">
            ${ev && ev.count > 1 ? eventOccurrencesHtml(ev, a.id) : ''}
            ${a.matched_keywords && a.matched_keywords.length > 0 ? `
            <div class="analysis-keywords">
                <span class="kw-label">${_t('monitor.keywords', 'Keywords')} :</span>
                ${a.matched_keywords.map(k => `<span class="log-kw-badge">${escapeHtml(k)}</span>`).join(' ')}
            </div>` : ''}
            <div class="analysis-line">${highlightKeywords(a.triggered_line || '', a.matched_keywords || [])}</div>
            <div class="analysis-response markdown-body">${a.ollama_response ? renderMarkdown(a.ollama_response) : ''}</div>
            ${resolutionBlock}
            ${ev ? eventNotesHtml(ev, a.id) : ''}
            <div class="analysis-footer">
                <div style="display: flex; flex-wrap: wrap; gap: 0.5rem;">
                    <button class="btn btn-secondary btn-sm" onclick="event.stopPropagation(); retryAnalysis(${a.id}, this)">🔄 ${_t('common.retry', 'Retry')}</button>
                    <button class="btn btn-secondary btn-sm" onclick="event.stopPropagation(); notifyAnalysis(${a.id}, this)">🔔 ${_t('common.notify', 'Notify')}</button>
                    <button class="btn btn-secondary btn-sm" onclick="event.stopPropagation(); togglePromptView(${a.id}, this)" title="${_t('common.view_prompt_title', 'Show the exact messages sent to the LLM')}">📜 ${_t('common.view_prompt', 'Prompt')}</button>
                    ${ev ? `<button type="button" class="btn btn-secondary btn-sm" onclick="event.stopPropagation(); openNoteForm(this)">📝 ${_t('common.note_add', 'Ajouter une note')}</button>` : ''}
                    ${a.rule_id ? `<button class="btn btn-secondary btn-sm${flagged ? ' is-flagged' : ''}" onclick="event.stopPropagation(); toggleIrrelevant(${a.id}, this)" aria-pressed="${flagged}"
                        title="${_t('common.irrelevant_title', 'L’IA ajustera les mots-clés ou les exclusions de la règle pour ne plus déclencher sur ce genre de ligne')}">👎 ${flagged ? _t('common.irrelevant_done', 'Signalée pas pertinente') : _t('common.irrelevant', 'Pas pertinente')}</button>` : ''}
                    ${a.rule_id && !opts.hideRuleLink ? `<a class="btn btn-secondary btn-sm" onclick="event.stopPropagation()" href="/rules/${a.rule_id}?analysis=${a.id}#analyses">🔍 ${_t('common.view_rule', 'Voir dans la règle')}</a>` : ''}
                </div>
                <button class="btn btn-primary btn-sm" onclick="event.stopPropagation(); openChat(${a.id})">💬 ${_t('common.deepen', 'Deepen')}</button>
                <button class="btn btn-secondary btn-sm" onclick="event.stopPropagation(); investigateAnalysis(${a.id})" title="${_t('common.investigate_title', 'Start an AI-guided investigation')}">🔎 ${_t('common.investigate', 'Investigate')}</button>
            </div>
        </div>
    </div>`;
}

/* ── Analyses grouped by event ───────────────────────────────────────────── */

function eventOccurrencesHtml(ev, shownId) {
    const _t = (k, fb) => (window.t ? window.t(k) : null) || fb;
    const severities = ['critical', 'warning', 'info'].filter(s => ev.severities[s]).map(s => `${ev.severities[s]} ${s}`).join(' · ');
    const range = _t('common.event_range', '{n} occurrences, du {first} au {last}').replace('{n}', ev.count)
        .replace('{first}', formatDate(ev.first_at)).replace('{last}', formatDate(ev.last_at));
    return `<div class="event-occurrences">
        <span>${escapeHtml(range)} · ${escapeHtml(severities)}</span>
        <button type="button" class="btn btn-secondary btn-sm" aria-expanded="false" data-rule="${ev.rule_id}" data-key="${ev.key}" data-shown="${shownId}" onclick="event.stopPropagation(); toggleOccurrences(this)">${escapeHtml(_t('common.event_more', 'Voir les autres occurrences'))}</button>
        <div class="event-occurrence-list hidden"></div>
    </div>`;
}

/** The other occurrences of an event, loaded when asked for. */
async function toggleOccurrences(btn) {
    const _t = (k, fb) => (window.t ? window.t(k) : null) || fb;
    const list = btn.nextElementSibling;
    const open = btn.getAttribute('aria-expanded') !== 'true';
    btn.setAttribute('aria-expanded', String(open));
    btn.textContent = open ? _t('common.event_hide', 'Masquer les occurrences') : _t('common.event_more', 'Voir les autres occurrences');
    list.classList.toggle('hidden', !open);
    if (open && !list.dataset.loaded) await loadOccurrences(btn, list, 0);
}

async function loadOccurrences(btn, list, offset) {
    const _t = (k, fb) => (window.t ? window.t(k) : null) || fb;
    list.querySelector('.event-occurrence-more')?.remove();
    try {
        const res = await apiFetch(`/api/dashboard/recent?rule_id=${btn.dataset.rule}&event_key=${btn.dataset.key}&limit=30&offset=${offset}`);
        list.dataset.loaded = '1';
        list.insertAdjacentHTML('beforeend', res.analyses.filter(a => String(a.id) !== btn.dataset.shown).map(a => `
            <a class="event-occurrence" href="/rules/${a.rule_id}?analysis=${a.id}#analyses">
                <span class="analysis-time">${a.analyzed_at ? formatDate(a.analyzed_at) : ''}</span>
                <span class="severity-badge ${escapeHtml(a.severity)}">${escapeHtml(a.severity)}</span>
                <span class="event-occurrence-text">${escapeHtml(a.summary || '')}</span>
            </a>`).join(''));
        if (res.has_more) {
            const more = document.createElement('button');
            more.type = 'button';
            more.className = 'btn btn-secondary btn-sm event-occurrence-more';
            more.textContent = _t('monitor.show_more', 'Afficher plus');
            more.addEventListener('click', e => { e.stopPropagation(); loadOccurrences(btn, list, offset + 30); });
            list.append(more);
        }
    } catch (e) {
        list.insertAdjacentHTML('beforeend', `<div class="rp-muted">${escapeHtml(_t('common.error', 'Erreur'))} : ${escapeHtml(e.message)}</div>`);
    }
}

function deleteEvent(ruleId, key, count, btn) {
    const _t = (k, fb) => (window.t ? window.t(k) : null) || fb;
    const message = count > 1 ? _t('common.confirm_delete_event', 'Supprimer cet événement et ses {n} occurrences ?').replace('{n}', count)
        : _t('common.confirm_delete_analysis', 'Supprimer cette analyse ?');
    showInlineConfirm(btn, message, async () => {
        try {
            await apiFetch(`/api/dashboard/events/${ruleId}/${key}`, { method: 'DELETE' });
            document.querySelector(`[data-event="${ruleId}-${key}"]`)?.remove();
            document.dispatchEvent(new CustomEvent('events-changed'));
        } catch (e) {
            alert(`${_t('common.error', 'Erreur')} : ${e.message}`);
        }
    });
}

function eventCardSignature(ev) {
    const a = ev.analysis;
    const notes = (ev.notes || []).map(n => `${n.id}:${n.updated_at}:${n.scope}`).join(',');
    return [a.id, ev.unread, ev.count, ev.last_at, ev.severity, a.resolution_status, a.resolved_at, a.summary, ev.flagged, notes, (a.ollama_response || '').length].join('|');
}

/** Card of one event, as a DOM element. */
function renderEventCard(ev, opts = {}) {
    const template = document.createElement('template');
    template.innerHTML = renderAnalysisCard(ev.analysis, { collapsed: true, showDelete: true, showCopy: true, ...opts, event: ev }).trim();
    const el = template.content.firstElementChild;
    el.dataset.signature = eventCardSignature(ev);
    return el;
}

/** Merges fresh events into a list. A card being read (open, or holding the focus) is never replaced or removed. */
function reconcileEvents(list, events, opts = {}) {
    const { freshClass, ...cardOpts } = opts;
    const isCard = el => el && el.classList.contains('analysis-card');
    const idOf = ev => `${ev.rule_id}-${ev.key}`;
    const incoming = new Set(events.map(idOf));
    const reading = el => !el.classList.contains('collapsed') || el.contains(document.activeElement) || el.querySelector('.event-note-form');
    [...list.children].forEach(el => { if (isCard(el) && !incoming.has(el.dataset.event) && !reading(el)) el.remove(); });
    const tail = [...list.children].find(el => !isCard(el)) || null;
    let cursor = isCard(list.firstElementChild) ? list.firstElementChild : null;
    const next = el => (isCard(el.nextElementSibling) ? el.nextElementSibling : null);
    events.forEach(ev => {
        while (cursor && !incoming.has(cursor.dataset.event)) cursor = next(cursor);
        let el = list.querySelector(`:scope > [data-event="${idOf(ev)}"]`);
        if (!el) {
            el = renderEventCard(ev, cardOpts);
            if (freshClass) el.classList.add(freshClass);
        } else if (el.dataset.signature !== eventCardSignature(ev)) {
            if (reading(el)) {
                const badge = el.querySelector('.event-count');
                if (badge) badge.textContent = `× ${ev.count}`;
            } else {
                const fresh = renderEventCard(ev, cardOpts);
                if (el === cursor) cursor = fresh;
                el.replaceWith(fresh);
                el = fresh;
            }
        }
        if (el === cursor) cursor = next(cursor);
        else list.insertBefore(el, cursor || tail);
    });
}

/** Flags (or unflags) an alert as not relevant: the keyword arbiter reviews the rule right away. */
async function toggleIrrelevant(analysisId, btn) {
    const _t = (k, fb) => (window.t ? window.t(k) : null) || fb;
    const flagged = btn.getAttribute('aria-pressed') === 'true';
    btn.disabled = true;
    try {
        await apiFetch(`/api/monitor/analyses/${analysisId}/feedback?value=${flagged ? 'none' : 'irrelevant'}`, { method: 'POST' });
        btn.setAttribute('aria-pressed', String(!flagged));
        btn.classList.toggle('is-flagged', !flagged);
        btn.textContent = '👎 ' + (flagged ? _t('common.irrelevant', 'Pas pertinente') : _t('common.irrelevant_done', 'Signalée pas pertinente'));
        if (!flagged && typeof showToast === 'function') {
            showToast(_t('common.irrelevant_toast', 'Merci : l’IA réexamine les mots-clés de la règle.'), 'success');
        }
    } catch (e) {
        alert(e.message);
    } finally {
        btn.disabled = false;
    }
}

// Cache of viewed analysis IDs to avoid duplicate network requests
const _viewedAnalysesCache = new Set();

async function markAnalysisAsViewed(analysisId) {
    if (!analysisId || _viewedAnalysesCache.has(analysisId)) return;
    _viewedAnalysesCache.add(analysisId);
    try {
        await apiFetch(`/api/monitor/analyses/${analysisId}/view`, { method: 'POST' });
        
        // Remove unread visual markers instantly
        const card = document.getElementById(`analysis-card-${analysisId}`);
        if (card) {
            card.classList.remove('unread');
            const dot = card.querySelector('.unread-dot-indicator');
            if (dot) dot.remove();
        }

        // Let the page update its unread counters (rule page tab badge and filters)
        document.dispatchEvent(new CustomEvent('analysis-viewed', { detail: { id: analysisId } }));
    } catch (e) {
        console.error("Failed to mark analysis as viewed:", e);
        _viewedAnalysesCache.delete(analysisId);
    }
}

function toggleAnalysisCardGeneric(event, card) {
    if (event.target.closest('button:not(.collapse-toggle), a, .btn')) return;
    const isCollapsed = card.classList.toggle('collapsed');
    const toggle = card.querySelector('.collapse-toggle');
    if (toggle) {
        toggle.textContent = isCollapsed ? '▶' : '▼';
        toggle.setAttribute('aria-expanded', String(!isCollapsed));
    }

    if (!isCollapsed) {
        const idAttr = card.id;
        if (idAttr && idAttr.startsWith('analysis-card-')) {
            const analysisId = parseInt(idAttr.replace('analysis-card-', ''));
            if (!isNaN(analysisId)) {
                markAnalysisAsViewed(analysisId);
            }
        }
    }
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

/* ── Theme Management ── */
function applyThemeIcon(theme) {
    const btn = document.getElementById('theme-toggle-btn');
    if (!btn) return;
    if (theme === 'light') {
        btn.innerHTML = '🌙';
        btn.setAttribute('data-i18n-title', 'header.theme_dark');
        if (window.t) {
            btn.setAttribute('title', window.t('header.theme_dark'));
        }
    } else {
        btn.innerHTML = '☀️';
        btn.setAttribute('data-i18n-title', 'header.theme_light');
        if (window.t) {
            btn.setAttribute('title', window.t('header.theme_light'));
        }
    }
}

function toggleTheme() {
    let currentTheme = document.documentElement.getAttribute('data-theme') || 'dark';
    let newTheme = currentTheme === 'light' ? 'dark' : 'light';
    
    document.documentElement.setAttribute('data-theme', newTheme);
    localStorage.setItem('theme', newTheme);
    applyThemeIcon(newTheme);
}

document.addEventListener('DOMContentLoaded', () => {
    let currentTheme = localStorage.getItem('theme') || 'dark';
    applyThemeIcon(currentTheme);
});

function translateBackendError(msg) {
    if (!msg) return msg;
    if (window.i18n?.getCurrentLang() !== 'en') return msg;

    if (msg.startsWith("Fichier introuvable sur le serveur :")) {
        return msg.replace("Fichier introuvable sur le serveur :", "File not found on the server:");
    }
    if (msg.startsWith("Fichier non lisible (permissions insuffisantes) :")) {
        return msg.replace("Fichier non lisible (permissions insuffisantes) :", "File not readable (insufficient permissions):");
    }
    if (msg.startsWith("Aucun candidat (mot-clé ou exclusion) trouvé sur l'ensemble de la période.")) {
        return "No candidate (keyword or exclusion) found over the entire period. Verify that the file contains logs during this period and that timestamps are readable.";
    }
    if (msg.startsWith("Ollama a rejeté la liste raffinée")) {
        return msg.replace("Ollama a rejeté la liste raffinée", "Ollama rejected the refined list")
                  .replace("fois de suite. La liste brute contenait :", "times in a row. The raw list contained:")
                  .replace(". Consultez le fichier log de session pour les détails.", ". See session log file for details.");
    }
    return msg;
}
