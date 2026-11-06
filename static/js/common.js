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

/** "il y a 2 h", dans la langue de l'interface. */
function relativeTime(iso) {
    if (!iso) return '';
    const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
    const lang = document.documentElement.lang || 'fr';
    const format = new Intl.RelativeTimeFormat(lang, { numeric: 'auto', style: 'short' });
    if (seconds < 60) {
        const now = window.t ? window.t('common.ago_now') : null;
        return now && now !== 'common.ago_now' ? now : "à l'instant";
    }
    if (seconds < 3600) return format.format(-Math.round(seconds / 60), 'minute');
    if (seconds < 86400) return format.format(-Math.round(seconds / 3600), 'hour');
    return format.format(-Math.round(seconds / 86400), 'day');
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
    return date.toLocaleString(window.i18n?.getCurrentLang?.() === 'fr' ? 'fr-FR' : undefined, {
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

/** Ends the loading placeholder of these containers, once their first answer arrived (or failed). */
function unskel(...ids) {
    ids.forEach(id => document.getElementById(id)?.classList.remove('skel'));
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

/** opts.confirmLabel names the confirm button; opts.alternative = {label, onClick} offers a safer way, put first. */
function showInlineConfirm(btnElement, message, onConfirmCallback, opts = {}) {
    // 1. Fermer toute popup de confirmation existante
    document.querySelectorAll('.inline-confirm-popup').forEach(el => el.remove());

    // 2. Créer la popup
    const popup = document.createElement('div');
    popup.className = 'inline-confirm-popup';
    const alt = opts.alternative;

    popup.innerHTML = `
        <div class="inline-confirm-msg">${escapeHtml(message)}</div>
        <div class="inline-confirm-actions${alt ? ' inline-confirm-actions--three' : ''}">
            ${alt ? `<button class="btn btn-primary btn-sm alt-btn">${escapeHtml(alt.label)}</button>` : ''}
            <button class="btn ${alt ? 'btn-secondary' : 'btn-primary'} btn-sm confirm-btn">${escapeHtml(opts.confirmLabel || (window.t ? window.t('common.confirm') : 'OK'))}</button>
            <button class="btn btn-secondary btn-sm cancel-btn">${window.t ? window.t('common.cancel') : 'Annuler'}</button>
        </div>
    `;
    popup.querySelector('.alt-btn')?.addEventListener('click', e => {
        e.stopPropagation();
        popup.remove();
        alt.onClick();
    });
    
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

const _cardT = (k, fb) => (window.t && window.t(k) !== k) ? window.t(k) : fb;

const CEF_FACTS = [['UNIFIipsSignature', 'signature', 'Signature'], ['src', 'source', 'Source'], ['UNIFIsrcRegion', 'region', 'Pays'],
    ['dst', 'destination', 'Destination'], ['dpt', 'port', 'Port'], ['proto', 'protocol', 'Protocole'], ['act', 'action', 'Action'],
    ['UNIFIdirection', 'direction', 'Sens'], ['UNIFIrisk', 'risk', 'Risque']];

/** What a CEF line states, read without any AI: facts the analysis below may interpret, but cannot change. */
function cefFactsHtml(line) {
    if (!line || !line.includes('CEF:')) return '';
    const value = key => {
        const m = line.match(new RegExp(`(?:^|[\\s|])${key}=(.*?)(?=\\s+[A-Za-z_][\\w.-]*=|$)`, 'm'));
        return m ? m[1].trim() : '';
    };
    const items = CEF_FACTS.map(([key, id, fallback]) => [_cardT(`common.fact_${id}`, fallback), value(key)]).filter(([, v]) => v);
    if (!items.length) return '';
    return `<dl class="analysis-facts" aria-label="${escapeHtml(_cardT('common.facts_title', 'Ce que dit la ligne'))}">${items.map(([k, v]) =>
        `<div><dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd></div>`).join('')}</dl>`;
}

const CVE_PATTERN = /CVE-\d{4}-\d{4,7}/i;

function cveSlotHtml(a) {
    if (!a.triggered_line || !CVE_PATTERN.test(a.triggered_line)) return '';
    queueMicrotask(loadOpenCveSheets);
    return `<div class="analysis-cve" data-cve-analysis="${a.id}"></div>`;
}

function cveSheetHtml(s) {
    if (s.error) return `<p class="form-hint">${escapeHtml(s.id)} : ${escapeHtml(_cardT('common.cve_unavailable', 'base NVD injoignable.'))}</p>`;
    if (!s.found) return `<p class="form-hint">${escapeHtml(s.id)} : ${escapeHtml(_cardT('common.cve_missing', 'absent de la base NVD. Il peut être trop récent ou ne pas exister.'))}</p>`;
    const kev = s.kev_added
        ? `<span class="cve-badge cve-badge--kev">${escapeHtml(_cardT('common.cve_kev', 'Exploitée activement · KEV depuis {date}').replace('{date}', s.kev_added))}</span>`
        : `<span class="cve-badge">${escapeHtml(_cardT('common.cve_no_kev', 'Pas au catalogue KEV'))}</span>`;
    const score = s.score != null ? `<span class="cve-badge">CVSS ${escapeHtml(String(s.score))}</span>` : '';
    const products = (s.products || []).length
        ? `<p class="cve-products"><strong>${escapeHtml(_cardT('common.cve_affected', 'Versions touchées'))} :</strong> ${s.products.map(escapeHtml).join(' · ')}</p>` : '';
    return `<div class="cve-sheet">
        <div class="cve-head"><a href="${escapeHtml(s.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(s.id)}</a> ${kev} ${score}</div>
        ${products}
        <p class="form-hint">${escapeHtml(s.description || '')}</p>
    </div>`;
}

/** Sheets of the CVEs cited by open cards. Fetched once per card, only when it is open. */
async function loadOpenCveSheets() {
    const slots = document.querySelectorAll('[id^="analysis-card-"]:not(.collapsed) .analysis-cve[data-cve-analysis]:not([data-loaded])');
    for (const slot of slots) {
        slot.dataset.loaded = '1';
        try {
            const res = await apiFetch(`/api/monitor/analyses/${slot.dataset.cveAnalysis}/cve`);
            slot.innerHTML = res.enabled
                ? res.sheets.map(cveSheetHtml).join('')
                : `<p class="form-hint"><a href="/config#diagnostic">${escapeHtml(_cardT('common.cve_enable_hint', 'Afficher la fiche officielle de ce CVE'))}</a></p>`;
        } catch (_) {
            slot.innerHTML = '';
        }
    }
}

/** Closes the rule's open incident, as on its overview tab. */
function resolveIncidentFromCard(ruleId, btn) {
    showInlineConfirm(btn, _cardT('rule.resolve_confirm', 'Clore l’incident en cours ?'), async () => {
        try {
            await apiFetch(`/api/monitor/rules/${ruleId}/resolve`, { method: 'POST' });
            window.location.reload();
        } catch (e) {
            alert(`${_cardT('common.error', 'Erreur')}: ${e.message}`);
        }
    });
}

function renderAnalysisCard(a, opts = {}) {
    const collapsed = opts.collapsed !== false;
    const showDelete = opts.showDelete || false;
    const showCopy = opts.showCopy !== false;
    const showRuleName = opts.showRuleName !== false;
    const cardClass = opts.cardClass || 'analysis-card';
    // Traductions pas encore chargées : window.t rend la clé, le texte de secours vaut mieux
    const _t = (k, fb) => { const s = window.t ? window.t(k) : null; return s && s !== k ? s : fb; };

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

    // Quick actions, usable without opening the card
    const readBtn = isUnread ? `
        <button type="button" class="btn-icon card-read-btn" onclick="event.stopPropagation(); markCardRead(${a.id}, this)" title="${escapeHtml(_t('common.mark_read', 'Marquer comme lu'))}" aria-label="${escapeHtml(_t('common.mark_read', 'Marquer comme lu'))}">
            <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path fill="currentColor" d="${CARD_ICONS.read}" /></svg>
        </button>` : '';
    const flagBtn = a.rule_id ? irrelevantIconHtml(a.id, flagged) : '';
    const noteBtn = ev ? `
        <button type="button" class="btn-icon" onclick="event.stopPropagation(); openNoteFromHeader(this)" title="${escapeHtml(_t('common.note_add', 'Ajouter une note'))}" aria-label="${escapeHtml(_t('common.note_add', 'Ajouter une note'))}">
            <svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true"><path fill="currentColor" d="${CARD_ICONS.note}" /></svg>
        </button>` : '';

    const ruleName = showRuleName && a.rule_name
        ? `<strong>${_t('dashboard.rule_label', 'Règle:')} ${escapeHtml(a.rule_name)}</strong>`
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
    // Qui a écrit les lignes déclenchantes : un résumé qui parle d'autre chose se voit d'un coup d'œil
    const components = (a.components || []).slice(0, 2);
    const extra = (a.components || []).length - components.length;
    const sourceTag = components.length
        ? `<span class="analysis-src" title="${escapeHtml(_t('common.component_title', 'Composant qui a écrit les lignes de cette alerte'))}">${escapeHtml(components.join(' · '))}${extra > 0 ? ` +${extra}` : ''}</span>` : '';
    const summaryLine = a.summary && opts.showSummary !== false
        ? `<div class="analysis-summary-line">${sourceTag}${escapeHtml(a.summary)}</div>` : '';

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
                ${readBtn}
                ${flagBtn}
                ${noteBtn}
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
            ${cefFactsHtml(a.triggered_line)}
            ${cveSlotHtml(a)}
            ${a.reused_from ? `<p class="form-hint analysis-reused">${_t('common.analysis_reused', 'Même événement : analyse reprise, sans nouvel appel à l’IA.')}</p>` : ''}
            <div class="analysis-response markdown-body">${a.ollama_response ? renderMarkdown(a.ollama_response) : ''}</div>
            ${resolutionBlock}
            ${ev ? eventNotesHtml(ev, a.id) : ''}
            <div class="analysis-footer">
                <div style="display: flex; flex-wrap: wrap; gap: 0.5rem;">
                    <button class="btn btn-secondary btn-sm" onclick="event.stopPropagation(); retryAnalysis(${a.id}, this)">🔄 ${_t('common.retry', 'Retry')}</button>
                    <button class="btn btn-secondary btn-sm" onclick="event.stopPropagation(); notifyAnalysis(${a.id}, this)">🔔 ${_t('common.notify', 'Notify')}</button>
                    <button class="btn btn-secondary btn-sm" onclick="event.stopPropagation(); togglePromptView(${a.id}, this)" title="${_t('common.view_prompt_title', 'Show the exact messages sent to the LLM')}">📜 ${_t('common.view_prompt', 'Prompt')}</button>
                    ${ev ? `<button type="button" class="btn btn-secondary btn-sm" data-note-open onclick="event.stopPropagation(); openNoteForm(this)">📝 ${_t('common.note_add', 'Ajouter une note')}</button>` : ''}
                    ${a.rule_id ? `<button class="btn btn-secondary btn-sm${flagged ? ' is-flagged' : ''}" data-irrelevant onclick="event.stopPropagation(); toggleIrrelevant(${a.id}, this)" aria-pressed="${flagged}"
                        title="${_t('common.irrelevant_title', 'L’IA ajustera les mots-clés ou les exclusions de la règle pour ne plus déclencher sur ce genre de ligne')}">👎 ${flagged ? _t('common.irrelevant_done', 'Signalée pas pertinente') : _t('common.irrelevant', 'Pas pertinente')}</button>` : ''}
                    ${ev && ev.rule_in_alert && !a.resolution_status ? `<button type="button" class="btn btn-secondary btn-sm" onclick="event.stopPropagation(); resolveIncidentFromCard(${a.rule_id}, this)">${_t('rule.resolve', 'Marquer comme résolu')}</button>` : ''}
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
    return [a.id, ev.unread, ev.count, ev.last_at, ev.severity, a.resolution_status, a.resolved_at, a.summary, ev.flagged, ev.rule_in_alert, notes, (a.ollama_response || '').length].join('|');
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

const CARD_ICONS = {
    read: 'M0.41,13.41L6,19L7.41,17.58L1.83,12M22.24,5.58L11.66,16.17L7.5,12L6.07,13.41L11.66,19L23.66,7M18,7L16.59,5.58L10.24,11.93L11.66,13.34L18,7Z',
    irrelevant: 'M19,15V3H23V15H19M15,3A2,2 0 0,1 17,5V15C17,15.55 16.78,16.05 16.41,16.41L9.83,23L8.77,21.94C8.5,21.67 8.33,21.3 8.33,20.88L8.36,20.57L9.31,16H3C1.89,16 1,15.1 1,14V12C1,11.74 1.05,11.5 1.14,11.27L4.16,4.22C4.46,3.5 5.17,3 6,3H15M15,5H5.97L3,12V14H11.78L10.65,19.32L15,14.97V5Z',
    irrelevantOn: 'M19,15H23V3H19M15,3H6C5.17,3 4.46,3.5 4.16,4.22L1.14,11.27C1.05,11.5 1,11.74 1,12V14A2,2 0 0,0 3,16H9.31L8.36,20.57C8.34,20.67 8.33,20.77 8.33,20.88C8.33,21.3 8.5,21.67 8.77,21.94L9.83,23L16.41,16.41C16.78,16.05 17,15.55 17,15V5C17,3.89 16.1,3 15,3Z',
    note: 'M14.06,9L15,9.94L5.92,19H5V18.08L14.06,9M17.66,3C17.41,3 17.15,3.1 16.96,3.29L15.13,5.12L18.88,8.87L20.71,7.04C21.1,6.65 21.1,6 20.71,5.63L18.37,3.29C18.17,3.09 17.92,3 17.66,3M14.06,6.19L3,17.25V21H6.75L17.81,9.94L14.06,6.19Z',
};

function irrelevantIconInner(flagged) {
    return `<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true"><path fill="currentColor" d="${flagged ? CARD_ICONS.irrelevantOn : CARD_ICONS.irrelevant}" /></svg>`;
}

function irrelevantIconTitle(flagged) {
    const _t = (k, fb) => (window.t ? window.t(k) : null) || fb;
    return flagged ? _t('common.irrelevant_done', 'Signalée pas pertinente') : _t('common.irrelevant', 'Pas pertinente');
}

function irrelevantIconHtml(analysisId, flagged) {
    const title = escapeHtml(irrelevantIconTitle(flagged));
    return `<button type="button" class="btn-icon card-flag-btn${flagged ? ' is-flagged' : ''}" data-irrelevant aria-pressed="${flagged}" title="${title}" aria-label="${title}"
        onclick="event.stopPropagation(); toggleIrrelevant(${analysisId}, this)">${irrelevantIconInner(flagged)}</button>`;
}

/** Flags (or unflags) an alert as not relevant: the keyword arbiter reviews the rule right away.
 *  The card has the same button twice, in its header and its footer: both follow. */
async function toggleIrrelevant(analysisId, btn) {
    const _t = (k, fb) => (window.t ? window.t(k) : null) || fb;
    const flagged = btn.getAttribute('aria-pressed') === 'true';
    const buttons = [...(btn.closest('.analysis-card')?.querySelectorAll('[data-irrelevant]') || [btn])];
    buttons.forEach(b => { b.disabled = true; });
    try {
        await apiFetch(`/api/monitor/analyses/${analysisId}/feedback?value=${flagged ? 'none' : 'irrelevant'}`, { method: 'POST' });
        buttons.forEach(b => {
            b.setAttribute('aria-pressed', String(!flagged));
            b.classList.toggle('is-flagged', !flagged);
            if (b.classList.contains('btn-icon')) {
                b.innerHTML = irrelevantIconInner(!flagged);
                b.title = irrelevantIconTitle(!flagged);
                b.setAttribute('aria-label', b.title);
            } else {
                b.textContent = '👎 ' + irrelevantIconTitle(!flagged);
            }
        });
        if (!flagged && typeof showToast === 'function') {
            showToast(_t('common.irrelevant_toast', 'Merci : l’IA réexamine les mots-clés de la règle.'), 'success');
        }
    } catch (e) {
        alert(e.message);
    } finally {
        buttons.forEach(b => { b.disabled = false; });
    }
}

/** "Mark everything as read", after a warning: an important alert could go unseen.
 *  counts: the event counts ({unread, unread_critical}), or a function that fetches them. */
async function confirmMarkAllRead(btn, counts, markAll, ruleId = null) {
    const _t = (k, fb) => (window.t ? window.t(k) : null) || fb;
    try {
        if (typeof counts === 'function') counts = await counts();
    } catch (_) {
        counts = null;
    }
    const n = counts?.unread, critical = counts?.unread_critical || 0;
    if (counts && !n) {
        if (typeof showToast === 'function') showToast(_t('common.mark_all_nothing', 'Aucun événement non lu.'), 'success');
        return;
    }
    let message = n === 1 ? _t('common.mark_all_confirm_one', 'Marquer l’événement non lu comme lu ? Tu pourrais passer à côté d’une alerte importante.')
        : _t('common.mark_all_confirm_many', 'Marquer les {n} événements non lus comme lus ? Tu pourrais passer à côté d’une alerte importante.').replace('{n}', n ?? '');
    if (critical) message += ' ' + (critical === 1 ? _t('common.mark_all_confirm_critical_one', 'Dont 1 critique.')
        : _t('common.mark_all_confirm_critical_many', 'Dont {n} critiques.').replace('{n}', critical));
    showInlineConfirm(btn, message, async () => {
        try {
            await markAll();
            document.dispatchEvent(new CustomEvent('events-changed'));
            if (typeof showToast === 'function') showToast(_t('common.mark_all_done', 'Tout est marqué comme lu.'), 'success');
        } catch (e) {
            alert(`${_t('common.error', 'Erreur')} : ${e.message}`);
        }
    }, {
        confirmLabel: _t('monitor.mark_all_read', 'Tout marquer comme lu'),
        alternative: typeof openTriage === 'function' ? { label: _t('triage.title', 'Trier mes non lues'), onClick: () => openTriage(ruleId) } : null,
    });
}

/** "Mark as read" from a closed card: the card stays closed. */
async function markCardRead(analysisId, btn) {
    btn.disabled = true;
    await markAnalysisAsViewed(analysisId);
    if (document.getElementById(`analysis-card-${analysisId}`)?.classList.contains('unread')) {
        btn.disabled = false;  // the request failed: the card is still unread
        return;
    }
    const actions = btn.parentElement;
    btn.remove();
    actions.querySelector('button')?.focus();
}

/** "Add a note" from a closed card: opens the card on its note form. */
function openNoteFromHeader(btn) {
    const card = btn.closest('.analysis-card');
    expandAnalysisCard(card);
    const opener = card.querySelector('[data-note-open]');
    if (opener) openNoteForm(opener);
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
    setAnalysisCardOpen(card, card.classList.contains('collapsed'));
}

function expandAnalysisCard(card) {
    if (card.classList.contains('collapsed')) setAnalysisCardOpen(card, true);
}

function setAnalysisCardOpen(card, open) {
    const isCollapsed = !open;
    card.classList.toggle('collapsed', isCollapsed);
    const toggle = card.querySelector('.collapse-toggle');
    if (toggle) {
        toggle.textContent = isCollapsed ? '▶' : '▼';
        toggle.setAttribute('aria-expanded', String(!isCollapsed));
    }

    if (!isCollapsed) {
        loadOpenCveSheets();
        const idAttr = card.id;
        if (idAttr && idAttr.startsWith('analysis-card-')) {
            const analysisId = parseInt(idAttr.replace('analysis-card-', ''));
            if (!isNaN(analysisId)) {
                markAnalysisAsViewed(analysisId);
            }
        }
    }
}

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
