/* Page Log chat : conversations, flux de la réponse, actions à confirmer et place dans le contexte. */

const CHAT = {
    convId: null,
    conversations: [],
    status: null,
    context: null,
    actions: {},
    hasAnalysis: false,
    pendingMessage: null,
    streamTools: [],
    follow: true,
};

let _activeTaskId = null;
let _streamPos = 0;
let _streamAbort = null;
let _streamFullText = '';

const SEVERITY_DOT = { critical: 'alert', warning: 'warn', info: 'idle' };
const SUGGESTIONS = ['chat.suggest_night', 'chat.suggest_open', 'chat.suggest_noisy', 'chat.suggest_correlated'];
const CHEVRON = '<svg class="chat-chevron" viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 3 5 5-5 5"/></svg>';
const ICON_STROKE = 'viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"';
const ICONS = {
    copy: `<svg ${ICON_STROKE}><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>`,
    retry: `<svg ${ICON_STROKE}><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-4.5"/></svg>`,
    edit: `<svg ${ICON_STROKE}><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>`,
    trash: `<svg ${ICON_STROKE}><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4h6v2"/></svg>`,
    stop: `<svg ${ICON_STROKE}><rect x="5" y="5" width="14" height="14" rx="2"/></svg>`,
};

function T(key, fallback) {
    const value = window.t ? window.t(key) : key;
    return value === key ? fallback : value;
}

function byId(id) { return document.getElementById(id); }

function numberText(n) { return Number(n).toLocaleString(document.documentElement.lang || 'fr'); }

function setError(message) { byId('chat-error').textContent = message || ''; }

function dbDate(iso) {
    if (!iso) return null;
    const date = new Date(/(Z|[+-]\d\d:\d\d)$/.test(iso) ? iso : iso + 'Z');
    return isNaN(date) ? null : date;
}

/** Aujourd'hui : 20:15. Hier : hier 20:15. Sinon : 02/05 20:15. */
function shortTime(iso) {
    const d = dbDate(iso);
    if (!d) return '';
    const time = d.toLocaleTimeString(document.documentElement.lang || 'fr', { hour: '2-digit', minute: '2-digit' });
    const now = new Date();
    const yesterday = new Date(now); yesterday.setDate(now.getDate() - 1);
    if (d.toDateString() === now.toDateString()) return time;
    if (d.toDateString() === yesterday.toDateString()) return `${T('chat.yesterday', 'hier')}\u00a0${time}`;
    const day = String(d.getDate()).padStart(2, '0');
    const month = String(d.getMonth() + 1).padStart(2, '0');
    return `${day}/${month}\u00a0${time}`;
}

function setTile(id, dot, detail, strong) {
    const tile = byId(id);
    tile.querySelector('.rp-dot').className = `rp-dot rp-dot--${dot}`;
    const detailEl = tile.querySelector('.cfg-status-detail');
    detailEl.removeAttribute('data-i18n');
    detailEl.textContent = detail;
    if (strong) tile.querySelector('strong').textContent = strong;
}

// ── État de l'IA ─────────────────────────────────────────────────────────────

async function refreshStatus() {
    try {
        CHAT.status = await apiFetch('/chat/api/status');
        renderStatus();
    } catch (e) {
        setTile('chat-st-model', 'idle', T('chat.st_unknown', 'État inconnu'), 'Ollama');
        setTile('chat-st-access', 'idle', T('chat.st_unknown', 'État inconnu'));
    }
}

function renderStatus() {
    const st = CHAT.status;
    if (!st) return;
    const { state, model, running, waiting } = st.ollama;
    const name = model || 'Ollama';
    if (state === 'unreachable') setTile('chat-st-model', 'alert', T('chat.st_unreachable', 'Ollama ne répond pas'), name);
    else if (state === 'model_missing') setTile('chat-st-model', 'alert', T('chat.st_model_missing', "Ce modèle n'est pas installé"), name);
    else if (state === 'not_configured') setTile('chat-st-model', 'warn', T('chat.st_not_configured', 'Ollama reste à configurer'), name);
    else if (waiting) setTile('chat-st-model', 'warn', T('chat.st_queue', 'Occupé, {n} en attente').replace('{n}', waiting), name);
    else if (running) setTile('chat-st-model', 'ok', T('chat.st_busy', 'Occupé'), name);
    else setTile('chat-st-model', 'ok', T('chat.st_ready', 'Prêt'), name);

    if (st.tools === null) setTile('chat-st-access', 'idle', T('chat.st_unknown', 'État inconnu'));
    else if (!st.tools) setTile('chat-st-access', 'warn', T('chat.st_no_tools', 'Ce modèle ne lit que cette conversation'));
    else {
        const parts = [T('chat.st_logs', 'Logs et alertes')];
        if (st.diagnostics.docker) parts.push('Docker');
        if (st.diagnostics.host) parts.push(T('chat.st_host', 'Hôte'));
        const web = st.diagnostics.web ? st.web_search : null;
        const webDown = web && (web.paused || web.hour_full);
        if (web && web.paused) parts.push(T('chat.st_web_paused', 'Web en pause ({n} min) : moteurs sans réponse').replace('{n}', web.minutes_left));
        else if (web && web.hour_full) parts.push(T('chat.st_web_full', 'Web : limite de l’heure atteinte'));
        else if (web) parts.push('Web');
        setTile('chat-st-access', webDown ? 'warn' : 'ok', parts.join(' · '));
    }

    const count = st.pending_actions.length;
    setTile('chat-st-actions', count ? 'warn' : 'idle', count
        ? (count > 1 ? T('chat.st_actions_many', '{n} actions').replace('{n}', count) : T('chat.st_actions_one', '1 action'))
        : T('chat.st_actions_none', 'Rien en attente'));
    byId('chat-st-actions').disabled = !count;
    renderContext();
}

/** Ouvre la première action en attente, même si elle est dans une autre conversation. */
async function showPendingAction() {
    const first = CHAT.status && CHAT.status.pending_actions[0];
    if (!first) return;
    if (first.conversation_id !== CHAT.convId) await selectConversation(first.conversation_id);
    const card = document.querySelector(`.action-card[data-action-id="${first.id}"]`);
    if (!card) return;
    card.scrollIntoView({ block: 'center' });
    card.querySelector('button:not([disabled])')?.focus({ preventScroll: true });
}

// ── Place dans le contexte ───────────────────────────────────────────────────

/** Estimation : environ 3,5 caractères par token. */
function estimateTokens(text) { return text ? Math.ceil(text.length / 3.5) : 0; }

function contextTokens(streamText = '') {
    const typed = byId('chat-input').value;
    return estimateTokens(`${CHAT.context.base_prompt}\nUtilisateur : ${typed}\nAssistant : ${streamText}`);
}

function renderContext(streamText = '') {
    const tile = byId('chat-st-context');
    const mobileBtn = byId('chat-ctx-btn');
    const data = CHAT.convId && CHAT.context;
    tile.disabled = !data;
    mobileBtn.classList.toggle('hidden', !data);
    if (!data) {
        setTile('chat-st-context', 'idle', T('chat.st_context_none', 'Aucune conversation ouverte'));
        byId('chat-full').classList.add('hidden');
        return;
    }
    const tokens = contextTokens(streamText);
    const max = data.ollama_ctx;
    const pct = Math.min(100, tokens / max * 100);
    const text = `≈ ${numberText(tokens)} / ${numberText(max)}`;
    setTile('chat-st-context', pct < 75 ? 'ok' : pct < 90 ? 'warn' : 'alert', `${text} tokens`);
    mobileBtn.textContent = `${Math.round(pct)} %`;
    mobileBtn.setAttribute('aria-label', `${T('chat.st_context', 'Contexte')} ${text} tokens`);
    const bar = byId('chat-context-bar');
    bar.style.width = `${pct}%`;
    bar.style.background = pct < 75 ? 'var(--success)' : pct < 90 ? 'var(--warning)' : 'var(--danger)';
    byId('chat-context-sentence').textContent = T('chat.context_sentence', "Environ {used} tokens sur {max} : c'est ce que le modèle peut lire d'un coup. Au-delà, il oublie le début.")
        .replace('{used}', numberText(tokens)).replace('{max}', numberText(max));
    byId('chat-context-prompt').textContent = data.base_prompt;
    const mode = CHAT.status ? CHAT.status.compression_mode : '';
    byId('chat-full').classList.toggle('hidden', pct < 95 || !!mode);
    byId('chat-context-auto').textContent = CHAT.pendingMessage
        ? T('chat.context_pending', "Ton message partira dès que l'espace sera libéré.")
        : mode ? T('chat.context_auto', 'Quand il sera plein : {mode}, comme réglé dans Configuration.').replace('{mode}', T(`chat.free_${mode}`, mode))
        : T('chat.context_ask', 'Quand il sera plein, ce choix te sera proposé. Un réglage automatique existe dans Configuration.');
}

function toggleContext(open) {
    const panel = byId('chat-context');
    const show = open === undefined ? panel.classList.contains('hidden') : open;
    panel.classList.toggle('hidden', !show);
    byId('chat-st-context').classList.toggle('active', show);
    byId('chat-st-context').setAttribute('aria-expanded', show);
    byId('chat-ctx-btn').setAttribute('aria-expanded', show);
}

async function loadContext(streamText = '') {
    try {
        CHAT.context = await apiFetch(`/chat/api/context/${CHAT.convId}`);
        renderContext(streamText);
    } catch (e) { /* le compteur reste à sa dernière valeur */ }
}

// ── Liste des conversations ──────────────────────────────────────────────────

async function loadConversations() {
    try {
        CHAT.conversations = await apiFetch('/chat/api/conversations');
        renderConversationList();
    } catch (e) {
        byId('conversation-list').innerHTML = `<li class="form-hint rf-ko">${escapeHtml(T('chat.error_network', 'Erreur réseau'))} : ${escapeHtml(e.message)}</li>`;
    }
}

function renderConversationList() {
    const list = byId('conversation-list');
    if (!CHAT.conversations.length) {
        list.innerHTML = `<li class="form-hint chat-items-empty">${escapeHtml(T('chat.no_conversation', 'Aucune conversation pour le moment.'))}</li>`;
        return;
    }
    list.innerHTML = CHAT.conversations.map(c => {
        const dot = c.running ? '<span class="rp-dot chat-dot-live"></span>'
            : c.severity ? `<span class="rp-dot rp-dot--${SEVERITY_DOT[c.severity] || 'idle'}"></span>` : '';
        const when = c.running ? T('chat.answering', 'Répond…') : shortTime(c.last_at);
        const meta = [c.rule_name, when].filter(Boolean).map(escapeHtml).join(' · ');
        const badge = c.pending_actions
            ? `<span class="chat-item-badge" title="${escapeAttr(T('chat.st_actions', 'À confirmer'))}">${c.pending_actions}</span>` : '';
        return `<li class="chat-item${c.id === CHAT.convId ? ' active' : ''}">
            <button type="button" class="chat-item-open" data-conv="${c.id}"${c.id === CHAT.convId ? ' aria-current="true"' : ''}>
                <span class="chat-item-title">${escapeHtml(c.title)}</span>
                <span class="chat-item-meta">${dot}<span>${meta}</span>${badge}</span>
            </button>
            <button type="button" class="chat-icon-btn chat-item-del" data-del="${c.id}" title="${escapeAttr(T('chat.delete_btn_title', 'Supprimer la conversation'))}" aria-label="${escapeAttr(T('chat.delete_btn_title', 'Supprimer la conversation'))}">${ICONS.trash}</button>
        </li>`;
    }).join('');
}

function deleteConversation(id, btn) {
    showInlineConfirm(btn, T('chat.delete_conversation', 'Supprimer cette conversation ?'), async () => {
        try {
            await apiFetch(`/chat/api/delete/${id}`, { method: 'DELETE' });
            if (CHAT.convId === id) showWelcome();
            await loadConversations();
            refreshStatus();
        } catch (e) { setError(e.message); }
    });
}

function setView(view) { byId('chat-page').dataset.view = view; }

function stopStream() {
    if (_streamAbort) _streamAbort.abort();
    _streamAbort = null;
    _activeTaskId = null;
    _streamFullText = '';
    _streamPos = 0;
    setSendBtn(false);
}

/** Nouvelle conversation : rien n'est créé avant le premier message. */
function showWelcome() {
    stopStream();
    CHAT.convId = null;
    CHAT.context = null;
    CHAT.hasAnalysis = false;
    CHAT.pendingMessage = null;
    try { localStorage.removeItem('sentinel_last_conv_id'); } catch (e) { /* stockage indisponible */ }
    history.replaceState(null, '', '/chat/');
    toggleContext(false);
    setError('');
    byId('chat-welcome').classList.remove('hidden');
    byId('chat-messages').classList.add('hidden');
    byId('chat-messages').innerHTML = '';
    byId('current-chat-title').textContent = T('chat.new_title', 'Nouvelle conversation');
    byId('current-chat-meta').classList.add('hidden');
    renderSuggestions();
    updatePlaceholder();
    renderContext();
    renderConversationList();
    setView('conv');
}

function renderSuggestions() {
    const holder = byId('chat-suggestions');
    holder.innerHTML = SUGGESTIONS.map(key =>
        `<button type="button" class="chip" data-suggest="${key}">${escapeHtml(T(key, key))}</button>`).join('');
}

function updatePlaceholder() {
    const key = CHAT.hasAnalysis ? 'chat.input_placeholder_analysis' : 'chat.input_placeholder';
    byId('chat-input').placeholder = T(key, 'Pose ta question sur tes logs…');
    byId('chat-input').setAttribute('data-i18n-placeholder', key);
}

function showThread() {
    byId('chat-welcome').classList.add('hidden');
    byId('chat-messages').classList.remove('hidden');
    setView('conv');
}

async function selectConversation(id) {
    if (_streamAbort && CHAT.convId !== id) stopStream();
    CHAT.convId = id;
    CHAT.context = null;
    CHAT.pendingMessage = null;
    try { localStorage.setItem('sentinel_last_conv_id', id); } catch (e) { /* stockage indisponible */ }
    history.replaceState(null, '', `/chat/?id=${id}`);
    toggleContext(false);
    setError('');
    showThread();
    renderConversationList();
    await loadHistory(id);
}

// ── Historique ───────────────────────────────────────────────────────────────

function archiveHtml(conv, comp) {
    const modes = { summary: T('chat.archive_summary', "Début résumé par l'IA"), compact: T('chat.archive_compact', 'Début compacté') };
    const taId = `chat-archive-${comp.id || conv}`;
    return `<details class="rule-notes chat-note">
        <summary>${CHEVRON}<span>${escapeHtml(modes[comp.mode] || T('chat.compressed_context', 'Début compressé'))}</span><span class="rule-notes-count">${escapeHtml(shortTime(comp.compressed_at))}</span></summary>
        <div class="rule-notes-body">
            <p class="form-hint">${escapeHtml(T('chat.archive_hint', "L'IA lit ce texte à la place des messages qui précèdent. Tu peux le corriger."))}</p>
            <textarea id="${taId}" class="chat-archive-text mono" rows="8">${escapeHtml(comp.content || '')}</textarea>
            <div class="cfg-actions">
                <button type="button" class="btn btn-primary btn-sm" data-archive-save="${taId}">${escapeHtml(T('common.save', 'Enregistrer'))}</button>
                <button type="button" class="btn btn-secondary btn-sm" data-restore>${escapeHtml(T('chat.restore_history', "Restaurer l'historique"))}</button>
            </div>
        </div>
    </details>`;
}

function truncatedHtml(messages) {
    const rows = messages.map(m => {
        const who = m.role === 'user' ? T('chat.label_user', 'Toi') : T('chat.label_ollama', 'Ollama');
        const text = m.content.length > 200 ? `${m.content.substring(0, 200)}…` : m.content;
        return `<li><strong>${escapeHtml(who)}</strong> ${escapeHtml(text)}</li>`;
    }).join('');
    return `<details class="rule-notes chat-note">
        <summary>${CHEVRON}<span>${escapeHtml(T('chat.truncated_banner', "{n} messages anciens ne sont plus lus par l'IA").replace('{n}', messages.length))}</span></summary>
        <div class="rule-notes-body">
            <ul class="chat-excluded">${rows}</ul>
            <div class="cfg-actions"><button type="button" class="btn btn-secondary btn-sm" data-restore>${escapeHtml(T('chat.restore_history', "Restaurer l'historique"))}</button></div>
        </div>
    </details>`;
}

function attachedHtml(a) {
    const title = [T('chat.attached_title', "Alerte d'origine"), a.detection_id ? `#${a.detection_id}` : null].filter(Boolean).join(' ');
    return `<details class="rule-notes chat-note chat-attached">
        <summary>${CHEVRON}<span class="rp-dot rp-dot--${SEVERITY_DOT[a.severity] || 'idle'}"></span><span>${escapeHtml(title)}</span></summary>
        <div class="rule-notes-body">
            <pre class="mono chat-trigger">${escapeHtml(a.triggered_line || '')}</pre>
            <div class="markdown-body">${renderMarkdown(a.ollama_response || '')}</div>
            <div class="cfg-actions">
                <button type="button" class="btn btn-secondary btn-sm" data-investigate="${a.id}" title="${escapeAttr(T('common.investigate_title', ''))}">${escapeHtml(T('common.investigate', 'Investiguer'))}</button>
                <a class="btn btn-secondary btn-sm" href="/rules/${a.rule_id}?analysis=${a.id}#analyses">${escapeHtml(T('chat.view_in_rule', 'Voir dans la règle'))}</a>
            </div>
        </div>
    </details>`;
}

async function loadHistory(id) {
    const container = byId('chat-messages');
    const scroller = byId('chat-scroll');
    container.innerHTML = `<p class="form-hint">${escapeHtml(T('chat.loading_history', 'Chargement…'))}</p>`;
    try {
        const data = await apiFetch(`/chat/api/history/${id}`);
        if (CHAT.convId !== id) return;
        byId('current-chat-title').removeAttribute('data-i18n');
        byId('current-chat-title').textContent = data.title;
        CHAT.actions = data.actions || {};
        CHAT.hasAnalysis = !!data.analysis;
        updatePlaceholder();
        const meta = byId('current-chat-meta');
        meta.classList.toggle('hidden', !data.analysis);
        meta.textContent = data.analysis ? [data.analysis.rule_name, shortTime(data.analysis.analyzed_at)].filter(Boolean).join(' · ') : '';

        const cutoff = data.compressed_at ? dbDate(data.compressed_at).getTime() : 0;
        const isTruncate = data.compression_mode === 'truncate';
        const excluded = m => isTruncate && cutoff && dbDate(m.created_at).getTime() <= cutoff;
        const archives = (data.compressions && data.compressions.length) ? data.compressions
            : (data.compressed_context && !isTruncate ? [{ id: null, mode: data.compression_mode, content: data.compressed_context, compressed_at: data.compressed_at }] : []);

        let html = '';
        if (data.analysis) html += attachedHtml(data.analysis);
        if (isTruncate && data.compressed_context) {
            const dropped = data.messages.filter(excluded);
            if (dropped.length) html += truncatedHtml(dropped);
        }
        let next = 0;
        data.messages.forEach(m => {
            while (!isTruncate && next < archives.length) {
                const at = dbDate(archives[next].compressed_at);
                if (!at || dbDate(m.created_at) <= at) break;
                html += archiveHtml(id, archives[next++]);
            }
            html += messageHtml(m.role, m.content, m.id, m.created_at, m.meta, excluded(m));
        });
        while (!isTruncate && next < archives.length) html += archiveHtml(id, archives[next++]);

        container.innerHTML = html || `<p class="form-hint">${escapeHtml(T('chat.empty_thread', 'Pose ta première question ci-dessous.'))}</p>`;
        container.querySelectorAll('.markdown-body').forEach(renderMath);
        CHAT.follow = true;
        scroller.scrollTop = scroller.scrollHeight;

        // Génération en cours (message envoyé, puis page quittée) : reprendre depuis le début du flux
        try {
            const pending = await apiFetch(`/chat/api/pending/${id}`);
            if (pending.task_id && CHAT.convId === id) {
                _activeTaskId = pending.task_id;
                _streamPos = 0;
                _streamFullText = '';
                if (_streamAbort) _streamAbort.abort();
                _streamAbort = new AbortController();
                setSendBtn(true);
                readStream(pending.task_id, 0, _streamAbort.signal);
            }
        } catch (e) { /* vérification facultative */ }

        loadContext();
    } catch (e) {
        container.innerHTML = `<p class="form-hint rf-ko">${escapeHtml(T('chat.error_network', 'Erreur réseau'))} : ${escapeHtml(e.message)}</p>`;
    }
}

// ── Messages ─────────────────────────────────────────────────────────────────

function renderMath(el) {
    if (!el || typeof renderMathInElement === 'undefined') return;
    try {
        renderMathInElement(el, {
            delimiters: [
                { left: '$$', right: '$$', display: true },
                { left: '$', right: '$', display: false },
                { left: '\\[', right: '\\]', display: true },
                { left: '\\(', right: '\\)', display: false },
            ],
            throwOnError: false,
            trust: false,
        });
    } catch (e) { /* KaTeX vient d'un CDN : absent hors ligne */ }
}

function escapeAttr(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/\r?\n/g, '&#10;');
}

function msgButton(action, icon, label, extra = '') {
    return `<button type="button" class="msg-action-btn" data-msg-action="${action}" ${extra}>${icon}<span>${escapeHtml(label)}</span></button>`;
}

function actionsHtml(role, text, msgId) {
    const copy = msgButton('copy', ICONS.copy, T('common.copy', 'Copier'), `data-text="${escapeAttr(text)}"`);
    const main = role === 'user'
        ? msgButton('edit', ICONS.edit, T('common.edit', 'Modifier'), `data-text="${escapeAttr(text)}" title="${escapeAttr(T('chat.edit_resend', 'Modifier et renvoyer'))}"`)
        : msgButton('retry', ICONS.retry, T('chat.regenerate', 'Régénérer'));
    const del = msgId ? msgButton('delete', ICONS.trash, T('common.delete', 'Supprimer'), `data-id="${msgId}"`) : '';
    return `<div class="message-actions">${copy}${main}${del}</div>`;
}

function messageHtml(role, content, msgId = null, timestamp = null, meta = null, excluded = false) {
    const isUser = role === 'user';
    const time = shortTime(timestamp);
    const name = isUser ? T('chat.label_user', 'Toi') : T('chat.label_ollama', 'Ollama');
    const label = `<div class="msg-label"><span>${escapeHtml(name)}</span>${time ? `<span class="msg-time">${escapeHtml(time)}</span>` : ''}</div>`;
    const dataId = msgId ? ` data-msg-id="${msgId}"` : '';
    const cls = `message-wrapper ${role}${excluded ? ' is-excluded' : ''}`;
    // Le plan d'une investigation est un long texte généré : il reste replié
    if (isUser && meta && meta.kind === 'investigation_plan') {
        return `<div class="${cls}"${dataId}><details class="rule-notes chat-note">
            <summary>${CHEVRON}<span>${escapeHtml(T('chat.investigation_plan', "Plan d'investigation donné à l'IA"))}</span></summary>
            <div class="rule-notes-body"><div class="markdown-body chat-user-text">${escapeHtml(content)}</div></div>
        </details></div>`;
    }
    const bubble = isUser
        ? `<div class="message-bubble user"><div class="markdown-body chat-user-text">${escapeHtml(content)}</div></div>`
        : `<div class="message-bubble assistant">${toolTraceHtml(meta && meta.tools)}<div class="markdown-body">${renderMarkdown(content)}</div></div>`;
    const cards = (!isUser && meta && meta.actions) ? meta.actions.map(id => actionCardHtml(CHAT.actions[id])).join('') : '';
    return `<div class="${cls}"${dataId}>${label}${bubble}${cards}${actionsHtml(role, content, msgId)}</div>`;
}

function toolTraceHtml(tools, open = false) {
    if (!tools || !tools.length) return '';
    const chips = tools.map(tool => {
        const cls = tool.running ? ' running' : (tool.error ? ' error' : '');
        const suffix = tool.running ? ' …' : (tool.lines != null ? ` · ${tool.lines} ${tool.lines === 1 ? T('chat.tool_line', 'ligne') : T('chat.tool_lines', 'lignes')}` : '');
        const chip = `<span class="tool-chip${cls}" title="${escapeAttr(tool.label)}">${escapeHtml(tool.label)}${suffix}</span>`;
        if (tool.running || tool.output == null) return chip;
        // Sortie brute de l'outil : pour vérifier d'où vient la réponse
        const output = tool.output ? escapeHtml(tool.output) : escapeHtml(T('chat.tool_no_output', '(aucune sortie)'));
        return `<details class="tool-call"><summary>${CHEVRON}${chip}</summary><pre class="tool-output">${output}</pre></details>`;
    }).join('');
    return `<details class="tool-trace"${open ? ' open' : ''}><summary>${CHEVRON}<span>${escapeHtml(T('chat.tools_used', 'Ce que l\'IA a consulté'))} (${tools.length})</span></summary><div class="tool-chips">${chips}</div></details>`;
}

/** Action proposée : rien n'est appliqué sans clic sur « Appliquer ». `locked` : l'IA n'a pas fini sa réponse. */
function actionCardHtml(a, locked = false) {
    if (!a) return '';
    const labels = {
        pending: locked ? T('chat.action_wait_end', "L'IA termine sa réponse…") : T('chat.action_status_pending', 'En attente de ta confirmation'),
        applied: T('chat.action_status_applied', 'Appliquée'),
        rejected: T('chat.action_status_rejected', 'Refusée'),
        failed: T('chat.action_status_failed', 'Échec'),
    };
    let status = escapeHtml(labels[a.status] || a.status);
    if (a.status === 'failed' && a.result) status += ` : ${escapeHtml(a.result)}`;
    const effect = (a.status === 'applied' && a.result && a.result !== 'ok') ? `<div class="action-effect">${escapeHtml(a.result)}</div>` : '';
    const dis = locked ? ' disabled' : '';
    const buttons = a.status === 'pending'
        ? `<button type="button" class="btn btn-sm btn-secondary" data-decide="reject" data-id="${a.id}"${dis}>${escapeHtml(T('chat.action_reject', 'Refuser'))}</button>
           <button type="button" class="btn btn-sm btn-primary" data-decide="apply" data-id="${a.id}"${dis}>${escapeHtml(T('chat.action_apply', 'Appliquer'))}</button>`
        : '';
    return `<div class="action-card ${escapeAttr(a.status)}" data-action-id="${a.id}"${locked ? ' data-locked="1"' : ''}>
        <div class="action-title">${escapeHtml(T('chat.action_proposed', 'Action proposée'))} : ${escapeHtml(a.description)}</div>
        ${a.reason ? `<div class="action-reason">${escapeHtml(a.reason)}</div>` : ''}
        <div class="action-buttons"><span class="action-status">${status}</span>${buttons}</div>
        ${effect}
    </div>`;
}

function unlockActionCards() {
    document.querySelectorAll('.action-card[data-locked]').forEach(card => {
        const a = CHAT.actions[card.dataset.actionId];
        if (a) card.outerHTML = actionCardHtml(a);
    });
}

async function decideAction(id, decision, btn) {
    const card = btn.closest('.action-card');
    card.querySelectorAll('button').forEach(b => { b.disabled = true; });
    try {
        const a = await apiFetch(`/chat/api/actions/${id}`, { method: 'POST', body: { decision } });
        CHAT.actions[id] = a;
        card.outerHTML = actionCardHtml(a);
        refreshStatus();
        loadConversations();
    } catch (e) {
        card.querySelectorAll('button').forEach(b => { b.disabled = false; });
        setError(e.message);
    }
}

function handleStreamEvent(ev, aiBubble) {
    if (ev.type === 'tool_call' || ev.type === 'tool_result') {
        const known = CHAT.streamTools.find(x => x.id === ev.id);
        if (ev.type === 'tool_call' && !known) {
            CHAT.streamTools.push({ id: ev.id, label: ev.label, running: true });
        } else if (ev.type === 'tool_result' && known) {
            known.running = false;
            known.error = ev.error;
            known.lines = ev.lines;
            known.output = ev.output;
        }
        const html = toolTraceHtml(CHAT.streamTools, true);
        const old = aiBubble.querySelector('.tool-trace');
        if (old) old.outerHTML = html; else aiBubble.insertAdjacentHTML('afterbegin', html);
    } else if (ev.type === 'action') {
        if (document.querySelector(`.action-card[data-action-id="${ev.id}"]`)) return;
        apiFetch(`/chat/api/actions/${ev.id}`).then(a => {
            CHAT.actions[a.id] = a;
            if (document.querySelector(`.action-card[data-action-id="${a.id}"]`)) return;
            const cards = aiBubble.parentElement ? aiBubble.parentElement.querySelectorAll('.action-card') : [];
            const anchor = cards.length ? cards[cards.length - 1] : aiBubble;
            anchor.insertAdjacentHTML('afterend', actionCardHtml(a, !!_activeTaskId));
            refreshStatus();
        }).catch(() => {});
    }
}

// ── Actions sur un message ───────────────────────────────────────────────────

async function copyMsg(btn) {
    await copyToClipboard(btn.dataset.text);
    const label = btn.querySelector('span');
    const original = label.textContent;
    label.textContent = T('common.copied', 'Copié !');
    setTimeout(() => { label.textContent = original; }, 1500);
}

function deleteMessage(msgId, btn) {
    const wrapper = btn.closest('.message-wrapper');
    const nextWrapper = wrapper.classList.contains('user') ? wrapper.nextElementSibling : null;
    const nextId = nextWrapper && nextWrapper.classList.contains('assistant') ? parseInt(nextWrapper.dataset.msgId) : null;
    showInlineConfirm(btn, T('chat.delete_message_confirm', 'Supprimer ce message ?'), async () => {
        try {
            await apiFetch(`/chat/api/message/${msgId}`, { method: 'DELETE' });
            if (nextId) await apiFetch(`/chat/api/message/${nextId}`, { method: 'DELETE' });
            wrapper.remove();
            if (nextId) nextWrapper.remove();
            loadContext();
        } catch (e) { setError(e.message); }
    });
}

function editMsg(btn) {
    const wrapper = btn.closest('.message-wrapper');
    wrapper._editOriginal = wrapper.innerHTML;
    wrapper.innerHTML = `
        <div class="msg-label"><span>${escapeHtml(T('chat.label_user', 'Toi'))}</span></div>
        <div class="inline-edit-container">
            <textarea class="inline-edit-textarea" aria-label="${escapeAttr(T('chat.edit_resend', 'Modifier et renvoyer'))}"></textarea>
            <div class="cfg-actions">
                <button type="button" class="btn btn-secondary btn-sm" data-msg-action="edit-cancel">${escapeHtml(T('common.cancel', 'Annuler'))}</button>
                <button type="button" class="btn btn-primary btn-sm" data-msg-action="edit-send">${escapeHtml(T('chat.edit_send', 'Renvoyer'))}</button>
            </div>
        </div>`;
    const ta = wrapper.querySelector('textarea');
    const fit = () => { ta.style.height = 'auto'; ta.style.height = `${ta.scrollHeight}px`; };
    ta.value = btn.dataset.text;
    fit();
    ta.focus();
    ta.setSelectionRange(ta.value.length, ta.value.length);
    ta.addEventListener('input', fit);
    ta.addEventListener('keydown', e => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); confirmEdit(wrapper); }
        if (e.key === 'Escape') cancelEdit(wrapper);
    });
}

function cancelEdit(wrapper) {
    if (wrapper._editOriginal) wrapper.innerHTML = wrapper._editOriginal;
    delete wrapper._editOriginal;
}

/** Valider une modification : ce message et ceux qui le suivent disparaissent, puis la réponse repart. */
async function confirmEdit(wrapper) {
    const text = wrapper.querySelector('textarea').value.trim();
    if (!text || _activeTaskId) return;
    const container = byId('chat-messages');
    const all = [...container.querySelectorAll('.message-wrapper')];
    const idx = all.indexOf(wrapper);
    all.slice(idx).forEach(w => w.remove());
    try {
        await apiFetch(`/chat/api/messages/${CHAT.convId}?count=${all.length - idx}`, { method: 'DELETE' });
        await startTurn(text);
    } catch (e) { setError(e.message); }
}

async function retryLastAiMsg() {
    if (!CHAT.convId || _activeTaskId) return;
    setSendBtn(true);
    try {
        await apiFetch(`/chat/api/regenerate/${CHAT.convId}`, { method: 'POST' });
        await loadHistory(CHAT.convId);
        refreshStatus();
    } catch (e) { setError(e.message); }
    if (!_activeTaskId) setSendBtn(false);
}

// ── Envoi et flux de la réponse ──────────────────────────────────────────────

function setSendBtn(generating) {
    const btn = byId('send-btn');
    btn.classList.toggle('btn-primary', !generating);
    btn.classList.toggle('btn-secondary', generating);
    if (generating) {
        btn.removeAttribute('data-i18n');
        btn.innerHTML = `${ICONS.stop}<span>${escapeHtml(T('common.stop', 'Arrêter'))}</span>`;
    } else {
        btn.setAttribute('data-i18n', 'chat.send');
        btn.textContent = T('chat.send', 'Envoyer');
    }
}

function nearEnd() {
    const s = byId('chat-scroll');
    return s.scrollHeight - s.scrollTop - s.clientHeight < 80;
}

/** Suit la réponse seulement si le lecteur est déjà en bas : remonter lire n'est jamais interrompu. */
function followEnd(force = false) {
    const s = byId('chat-scroll');
    if (force) CHAT.follow = true;
    if (CHAT.follow) s.scrollTop = s.scrollHeight;
    byId('chat-jump').classList.toggle('hidden', CHAT.follow);
}

function streamingWrapper() {
    const wrapper = document.createElement('div');
    wrapper.className = 'message-wrapper assistant';
    wrapper.id = 'streaming-wrapper';
    wrapper.innerHTML = `
        <div class="msg-label"><span>${escapeHtml(T('chat.label_ollama', 'Ollama'))}</span><span class="msg-time">${escapeHtml(shortTime(new Date().toISOString()))}</span></div>
        <div class="message-bubble assistant" id="streaming-ai">
            <div class="thinking-state"><span class="rp-dot chat-dot-live"></span><span>${escapeHtml(T('chat.thinking', 'Ollama réfléchit…'))}</span></div>
            <div class="markdown-body"></div>
        </div>`;
    byId('chat-messages').appendChild(wrapper);
    return wrapper;
}

/** Enregistre le message et démarre la réponse. Le serveur continue même si la page se ferme. */
async function startTurn(content) {
    const container = byId('chat-messages');
    const empty = container.querySelector(':scope > p.form-hint');
    if (empty) empty.remove();
    const holder = document.createElement('div');
    holder.innerHTML = messageHtml('user', content, null, new Date().toISOString());
    container.appendChild(holder.firstElementChild);
    streamingWrapper();
    followEnd(true);
    setSendBtn(true);
    setError('');
    _streamFullText = '';
    CHAT.streamTools = [];
    try {
        const data = await apiFetch('/chat/api/send', { method: 'POST', body: { conversation_id: CHAT.convId, content } });
        _activeTaskId = data.task_id;
        _streamPos = 0;
        _streamAbort = new AbortController();
        readStream(data.task_id, 0, _streamAbort.signal);
        loadContext();
        refreshStatus();
        loadConversations();
    } catch (e) {
        const bubble = byId('streaming-ai');
        if (bubble) {
            bubble.querySelector('.thinking-state')?.remove();
            bubble.querySelector('.markdown-body').innerHTML = `<span class="rf-ko">${escapeHtml(e.message)}</span>`;
            bubble.removeAttribute('id');
        }
        byId('streaming-wrapper')?.removeAttribute('id');
        setSendBtn(false);
    }
}

async function sendMessage(force = false) {
    const input = byId('chat-input');
    const content = input.value.trim();
    if (!content || _activeTaskId) return;

    if (!CHAT.convId) {
        try {
            const res = await apiFetch('/chat/api/create', { method: 'POST', body: {} });
            CHAT.convId = res.id;
            try { localStorage.setItem('sentinel_last_conv_id', res.id); } catch (e) { /* stockage indisponible */ }
            history.replaceState(null, '', `/chat/?id=${res.id}`);
            byId('chat-messages').innerHTML = '';
            showThread();
            await loadConversations();
            await loadContext();
        } catch (e) { setError(e.message); return; }
    }

    if (CHAT.context && !force && contextTokens() >= CHAT.context.ollama_ctx) {
        const mode = CHAT.status ? CHAT.status.compression_mode : '';
        if (mode) {
            applyCompression(mode, content);
        } else {
            CHAT.pendingMessage = content;
            renderContext();
            toggleContext(true);
        }
        return;
    }

    input.value = '';
    input.style.height = 'auto';
    await startTurn(content);
}

/** Arrête vraiment le modèle : ce qui a déjà été écrit reste dans la conversation. */
async function stopGeneration() {
    const taskId = _activeTaskId;
    if (_streamAbort) _streamAbort.abort();
    _streamAbort = null;
    _activeTaskId = null;
    _streamFullText = '';
    setSendBtn(false);
    try { await apiFetch(`/chat/api/stop/${taskId}`, { method: 'POST' }); } catch (e) { setError(e.message); }
    await loadHistory(CHAT.convId);
    loadConversations();
    refreshStatus();
}

async function readStream(taskId, fromPos, signal) {
    let bubble = byId('streaming-ai');
    if (!bubble) {
        streamingWrapper();
        bubble = byId('streaming-ai');
        followEnd();
    }
    const content = bubble.querySelector('.markdown-body');
    const thinking = bubble.querySelector('.thinking-state');
    if (fromPos === 0) CHAT.streamTools = [];

    try {
        const response = await fetch(`/chat/api/stream/${taskId}?from_pos=${fromPos}`, { signal });
        if (response.status === 404) {
            // Tâche inconnue (serveur redémarré, tâche nettoyée) : la réponse éventuelle est en base
            if (_activeTaskId === taskId) {
                stopStream();
                unlockActionCards();
                if (CHAT.convId) loadHistory(CHAT.convId);
            }
            return;
        }
        if (!response.ok) throw new Error('Stream unavailable');

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buf = '';
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buf += decoder.decode(value, { stream: true });
            const parts = buf.split('\n\n');
            buf = parts.pop();
            for (const part of parts) {
                if (!part.startsWith('data: ')) continue;
                let evt;
                try { evt = JSON.parse(part.substring(6)); } catch (e) { continue; }
                if (evt.error) {
                    thinking?.remove();
                    content.innerHTML = `<span class="rf-ko">${escapeHtml(evt.error)}</span>`;
                    bubble.removeAttribute('id');
                    byId('streaming-wrapper')?.removeAttribute('id');
                    _activeTaskId = null;
                    unlockActionCards();
                    setSendBtn(false);
                    refreshStatus();
                    return;
                }
                if (evt.event) {
                    _streamPos = evt.pos ?? (_streamPos + 1);
                    handleStreamEvent(evt.event, bubble);
                    followEnd();
                }
                if (evt.text) {
                    if (thinking) thinking.style.display = 'none';
                    _streamFullText += evt.text;
                    _streamPos = evt.pos ?? (_streamPos + 1);
                    content.innerHTML = renderMarkdown(_streamFullText);
                    renderMath(content);
                    followEnd();
                    renderContext(_streamFullText);
                }
                if (evt.done) {
                    await finishStream(bubble, taskId);
                    return;
                }
            }
        }
    } catch (e) {
        if (e.name === 'AbortError') return;
    }
    // Connexion coupée avant la fin (proxy, veille réseau) : reprendre où on en était
    setTimeout(() => {
        if (_activeTaskId === taskId && !signal.aborted) {
            _streamAbort = new AbortController();
            readStream(taskId, _streamPos, _streamAbort.signal);
        }
    }, 2000);
}

async function finishStream(bubble) {
    bubble.querySelector('.thinking-state')?.remove();
    bubble.removeAttribute('id');
    byId('streaming-wrapper')?.removeAttribute('id');
    _activeTaskId = null;
    _streamFullText = '';
    CHAT.streamTools = [];
    setSendBtn(false);
    const convId = CHAT.convId;
    // Après un arrêt, le titre attend la première réponse complète
    const needsTitle = byId('chat-messages').querySelectorAll('.message-wrapper').length <= 2
        || ['Nouvelle conversation', 'New conversation'].includes(byId('current-chat-title').textContent);
    // Recharger donne aux messages leur identifiant, donc leur bouton de suppression, sans bouger la lecture
    const scroller = byId('chat-scroll');
    const top = scroller.scrollTop;
    const following = CHAT.follow;
    await loadHistory(convId);
    if (!following) { CHAT.follow = false; scroller.scrollTop = top; followEnd(); }
    unlockActionCards();
    refreshStatus();
    if (needsTitle) await autoTitleConversation(convId);
    loadConversations();
}

async function autoTitleConversation(convId) {
    try {
        const res = await apiFetch(`/chat/api/auto-title/${convId}`, { method: 'POST', body: {} });
        if (res.title && CHAT.convId === convId) byId('current-chat-title').textContent = res.title;
    } catch (e) { /* le titre automatique est un plus, pas une garantie */ }
}

// ── Libérer de l'espace ──────────────────────────────────────────────────────

async function applyCompression(mode, pendingContent = null) {
    if (pendingContent !== null) CHAT.pendingMessage = pendingContent;
    toggleContext(false);
    const convId = CHAT.convId;
    const container = byId('chat-messages');
    container.insertAdjacentHTML('beforeend', `<div class="thinking-state" id="chat-compressing"><span class="rp-dot chat-dot-live"></span><span>${escapeHtml(T('chat.compression_loading', 'Compression en cours…'))}</span></div>`);
    followEnd(true);

    const after = async () => {
        await loadHistory(convId);
        if (CHAT.pendingMessage && CHAT.convId === convId) {
            byId('chat-input').value = CHAT.pendingMessage;
            CHAT.pendingMessage = null;
            await sendMessage(true);
        }
    };
    const failed = message => {
        byId('chat-compressing')?.remove();
        setError(message);
    };

    try {
        const res = await apiFetch(`/chat/api/compress/${convId}`, { method: 'POST', body: { mode } });
        if (res.status !== 'running') { await after(); return; }
        const timer = setInterval(async () => {
            try {
                const st = await apiFetch(`/chat/api/compress/status/${res.task_id}`);
                if (st.status === 'running') return;
                clearInterval(timer);
                if (st.status === 'error') failed(st.error === 'summary_rejected' ? T('chat.compression_rejected', st.error) : st.error);
                else await after();
            } catch (e) { clearInterval(timer); failed(e.message); }
        }, 2000);
    } catch (e) { failed(e.message); }
}

async function saveArchive(taId) {
    try {
        await apiFetch(`/chat/api/compress/${CHAT.convId}`, { method: 'PUT', body: { compressed_context: byId(taId).value } });
        await loadHistory(CHAT.convId);
    } catch (e) { setError(e.message); }
}

function restoreHistory(btn) {
    showInlineConfirm(btn, T('chat.restore_confirm', "Restaurer l'historique complet ?"), async () => {
        try {
            await apiFetch(`/chat/api/compress/${CHAT.convId}`, { method: 'DELETE' });
            await loadHistory(CHAT.convId);
        } catch (e) { setError(e.message); }
    });
}

// ── Événements ───────────────────────────────────────────────────────────────

function bindEvents() {
    byId('chat-new').addEventListener('click', showWelcome);
    byId('chat-back').addEventListener('click', () => setView('list'));
    byId('chat-st-actions').addEventListener('click', showPendingAction);
    byId('chat-st-context').addEventListener('click', () => toggleContext());
    byId('chat-ctx-btn').addEventListener('click', () => toggleContext());
    byId('chat-full-open').addEventListener('click', () => toggleContext(true));
    byId('chat-jump').addEventListener('click', () => followEnd(true));
    byId('send-btn').addEventListener('click', () => { if (_activeTaskId) stopGeneration(); else sendMessage(); });
    byId('chat-scroll').addEventListener('scroll', () => {
        CHAT.follow = nearEnd();
        byId('chat-jump').classList.toggle('hidden', CHAT.follow);
    }, { passive: true });

    const input = byId('chat-input');
    input.addEventListener('input', () => {
        input.style.height = 'auto';
        input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
        renderContext(_streamFullText);
    });
    input.addEventListener('keydown', e => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
    });

    byId('conversation-list').addEventListener('click', e => {
        const del = e.target.closest('[data-del]');
        if (del) { deleteConversation(parseInt(del.dataset.del), del); return; }
        const open = e.target.closest('[data-conv]');
        if (open) selectConversation(parseInt(open.dataset.conv));
    });
    byId('chat-suggestions').addEventListener('click', e => {
        const chip = e.target.closest('[data-suggest]');
        if (!chip) return;
        input.value = chip.textContent;
        sendMessage();
    });
    byId('chat-context').addEventListener('click', e => {
        const btn = e.target.closest('[data-compress]');
        if (btn) applyCompression(btn.dataset.compress);
    });
    byId('chat-messages').addEventListener('click', e => {
        const decide = e.target.closest('[data-decide]');
        if (decide) { decideAction(decide.dataset.id, decide.dataset.decide, decide); return; }
        const investigate = e.target.closest('[data-investigate]');
        if (investigate) { investigateAnalysis(parseInt(investigate.dataset.investigate)); return; }
        const save = e.target.closest('[data-archive-save]');
        if (save) { saveArchive(save.dataset.archiveSave); return; }
        const restore = e.target.closest('[data-restore]');
        if (restore) { restoreHistory(restore); return; }
        const btn = e.target.closest('[data-msg-action]');
        if (!btn) return;
        const action = btn.dataset.msgAction;
        if (action === 'copy') copyMsg(btn);
        else if (action === 'retry') retryLastAiMsg();
        else if (action === 'edit') editMsg(btn);
        else if (action === 'delete') deleteMessage(parseInt(btn.dataset.id), btn);
        else if (action === 'edit-cancel') cancelEdit(btn.closest('.message-wrapper'));
        else if (action === 'edit-send') confirmEdit(btn.closest('.message-wrapper'));
    });

    // Clavier mobile : garder la zone de saisie visible
    if (window.visualViewport) {
        let timer = null;
        window.visualViewport.addEventListener('resize', () => {
            clearTimeout(timer);
            timer = setTimeout(() => {
                if (document.activeElement === input) document.querySelector('.chat-footer').scrollIntoView({ block: 'end' });
            }, 80);
        });
    }

    // Onglet caché : le serveur continue de générer, on se reconnecte au retour
    document.addEventListener('visibilitychange', async () => {
        if (document.hidden) {
            if (_streamAbort) _streamAbort.abort();
            return;
        }
        refreshStatus();
        if (_activeTaskId) {
            _streamAbort = new AbortController();
            setSendBtn(true);
            readStream(_activeTaskId, _streamPos, _streamAbort.signal);
        } else if (CHAT.convId) {
            try {
                const pending = await apiFetch(`/chat/api/pending/${CHAT.convId}`);
                if (pending.task_id) {
                    _activeTaskId = pending.task_id;
                    _streamPos = 0;
                    _streamFullText = '';
                    _streamAbort = new AbortController();
                    setSendBtn(true);
                    readStream(pending.task_id, 0, _streamAbort.signal);
                }
            } catch (e) { /* vérification facultative */ }
        }
    });

    window.i18n?.onLanguageChange(() => {
        renderStatus();
        renderSuggestions();
        renderConversationList();
        updatePlaceholder();
        if (CHAT.convId && !_activeTaskId) loadHistory(CHAT.convId);
        else if (!CHAT.convId) byId('current-chat-title').textContent = T('chat.new_title', 'Nouvelle conversation');
        renderContext();
    });
}

document.addEventListener('DOMContentLoaded', async () => {
    bindEvents();
    renderSuggestions();
    refreshStatus();
    setInterval(() => { if (!document.hidden) refreshStatus(); }, 30000);
    await loadConversations();

    const fromUrl = parseInt(byId('chat-page').dataset.conversationId);
    let last = null;
    try { last = parseInt(localStorage.getItem('sentinel_last_conv_id')); } catch (e) { /* stockage indisponible */ }
    const known = id => CHAT.conversations.some(c => c.id === id);
    if (fromUrl) await selectConversation(fromUrl);
    else if (known(last)) await selectConversation(last);
    else if (CHAT.conversations.length) await selectConversation(CHAT.conversations[0].id);
    else { showWelcome(); setView('conv'); }
});
