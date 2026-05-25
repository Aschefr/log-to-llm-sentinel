// ─── Rule Modal (shared) ────────────────────────────────────────────────────
// Extracted from rules.js so both Rules page and Monitor page can reuse
// the same rule creation/edit modal without code duplication.
//
// Usage:
//   setupRuleModal({ onSave: myReloadFn })
//   setupKeywordSuggestions()
// ─────────────────────────────────────────────────────────────────────────────

/* ── Callback set by host page ─────────────────────────────────────────────── */
let _ruleModalOnSave = null;

/**
 * crypto.randomUUID() is only available in secure contexts (HTTPS / localhost).
 * This fallback works on plain HTTP over LAN (e.g. http://192.168.x.x).
 */
function _generateUUID() {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
        return crypto.randomUUID();
    }
    // Polyfill: RFC4122 v4 UUID using Math.random()
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
        const r = Math.random() * 16 | 0;
        return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
    });
}

/* ── Resolution Pattern Tags ───────────────────────────────────────────────── */
let _resolutionPatterns = [];

function _resTagT(key, fallback) {
    return window.t ? window.t(key) : fallback;
}

function _escHtml(s) {
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}

/** Sync internal array → hidden input + UI */
function _resTagsSync() {
    const hidden = document.getElementById('rule-resolution-patterns');
    if (hidden) hidden.value = _resolutionPatterns.join(',');

    const countEl = document.getElementById('resolution-patterns-count');
    const clearBtn = document.getElementById('resolution-patterns-clear-all');
    const emptyEl = document.getElementById('resolution-patterns-empty');
    const n = _resolutionPatterns.length;

    if (countEl) {
        countEl.style.display = n > 0 ? 'inline' : 'none';
        countEl.textContent = _resTagT('rules.resolution_patterns_count', '{count} pattern(s)').replace('{count}', n);
    }
    if (clearBtn) clearBtn.style.display = n > 0 ? 'inline-flex' : 'none';
    if (emptyEl) emptyEl.style.display = n === 0 ? 'inline' : 'none';
}

/** Render all tags from _resolutionPatterns */
function _resTagsRender() {
    const box = document.getElementById('resolution-patterns-tags');
    if (!box) return;
    // Keep the empty placeholder
    const emptyEl = box.querySelector('#resolution-patterns-empty');
    box.innerHTML = '';
    if (emptyEl) box.appendChild(emptyEl);

    _resolutionPatterns.forEach((p, idx) => {
        const tag = document.createElement('span');
        tag.className = 'res-tag';
        tag.innerHTML = `${_escHtml(p)}<button type="button" class="res-tag-remove" data-idx="${idx}" title="Remove">✕</button>`;
        tag.querySelector('.res-tag-remove').addEventListener('click', (e) => {
            e.preventDefault();
            _resolutionPatterns.splice(idx, 1);
            _resTagsRender();
        });
        box.insertBefore(tag, emptyEl);
    });
    _resTagsSync();
}

/** Set patterns and render */
function resTagsSet(patterns) {
    _resolutionPatterns = (patterns || []).filter(p => p && p.trim()).map(p => p.trim());
    _resTagsRender();
}

/** Add one or more patterns (comma-separated string accepted) */
function resTagsAdd(input) {
    const parts = input.split(',').map(p => p.trim()).filter(p => p);
    let added = 0;
    parts.forEach(p => {
        if (!_resolutionPatterns.includes(p)) {
            _resolutionPatterns.push(p);
            added++;
        }
    });
    if (added) _resTagsRender();
}

/** Clear all patterns */
function resTagsClear() {
    _resolutionPatterns = [];
    _resTagsRender();
}

/* ── Keyword suggestions ───────────────────────────────────────────────────── */
const TYPICAL_KEYWORDS = [
    'error', 'exception', 'fatal', 'critical', 'warning', 'warn',
    'timeout', 'timed out', 'connection refused', 'refused',
    'unhandled', 'panic', 'traceback', 'stacktrace',
    'failed', 'failure', 'permission denied', 'unauthorized', 'forbidden',
    'rate limit', 'oom', 'out of memory', 'disk full', 'no space left',
    'segfault', '502', '503', '504',
];

function setupKeywordSuggestions() {
    const datalist = document.getElementById('keywords-suggestions');
    const chips = document.getElementById('keyword-chips');
    if (!datalist || !chips) return;

    datalist.innerHTML = TYPICAL_KEYWORDS.map(k => `<option value="${escapeHtml(k)}"></option>`).join('');

    // Chips: show a short curated list (avoid overwhelming)
    const chipList = ['error', 'exception', 'timeout', 'permission denied', 'unauthorized', 'disk full', 'out of memory', 'panic', '502', '503'];
    chips.innerHTML = chipList.map(k => `<span class="chip" data-keyword="${escapeHtml(k)}">${escapeHtml(k)} +</span>`).join('');

    chips.addEventListener('click', (e) => {
        const el = e.target.closest('.chip');
        if (!el) return;
        const kw = el.getAttribute('data-keyword');
        if (!kw) return;
        addKeywordToInput(kw);
    });
}

function addKeywordToInput(keyword) {
    const input = document.getElementById('rule-keywords');
    if (!input) return;
    const current = input.value
        .split(',')
        .map(k => k.trim())
        .filter(Boolean);

    if (!current.includes(keyword)) {
        current.push(keyword);
        input.value = current.join(', ');
    }
    input.focus();
}

/* ── Modal setup ───────────────────────────────────────────────────────────── */

/**
 * Wire up the rule modal (close/cancel/submit + file browser + source tabs).
 * @param {Object} opts
 * @param {Function} opts.onSave - called after successful save (e.g. loadRules or loadMonitorRules)
 */
function setupRuleModal(opts = {}) {
    _ruleModalOnSave = opts.onSave || null;

    const modal = document.getElementById('rule-modal');
    const closeBtn = document.getElementById('close-modal');
    const cancelBtn = document.getElementById('cancel-rule');
    const form = document.getElementById('rule-form');

    if (!modal || !form) return;

    setupFileBrowser();

    if (closeBtn) closeBtn.addEventListener('click', () => { modal.classList.add('hidden'); if (_ruleModalOnSave) _ruleModalOnSave(); });
    if (cancelBtn) cancelBtn.addEventListener('click', () => { modal.classList.add('hidden'); if (_ruleModalOnSave) _ruleModalOnSave(); });

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        await saveRule();
    });

    const pathInput = document.getElementById('rule-path');
    if (pathInput) {
        let previewTimeout;
        pathInput.addEventListener('input', () => {
            clearTimeout(previewTimeout);
            previewTimeout = setTimeout(() => {
                fetchFilePreview(pathInput.value);
            }, 500);
        });
    }

    const sourceCards = document.querySelectorAll('.source-card');
    const pathGroup = document.getElementById('path-group');
    const webhookGroup = document.getElementById('webhook-group');
    const pathInputEl = document.getElementById('rule-path');

    sourceCards.forEach(card => {
        card.addEventListener('click', () => {
            sourceCards.forEach(c => c.classList.remove('kw-tab--active'));
            card.classList.add('kw-tab--active');

            if (card.dataset.source === 'webhook') {
                pathGroup.classList.add('hidden');
                webhookGroup.classList.remove('hidden');
                pathInputEl.removeAttribute('required');
                pathInputEl.value = ''; // Clear value so it doesn't cause hidden validation issues

                // Générer un UUID si aucun token n'est encore défini (utile pour Nouvelle règle)
                if (!window._currentWebhookToken) {
                    window._currentWebhookToken = _generateUUID();
                }
                updateModalWebhookUrl();
            } else {
                pathGroup.classList.remove('hidden');
                webhookGroup.classList.add('hidden');
                pathInputEl.setAttribute('required', 'required');
            }
        });
    });

    const inactCheck = document.getElementById('rule-inactivity-warning-enabled');
    const inactContainer = document.getElementById('inactivity-hours-container');
    if (inactCheck && inactContainer) {
        inactCheck.addEventListener('change', () => {
            inactContainer.style.display = inactCheck.checked ? 'flex' : 'none';
        });
    }

    const resEnable = document.getElementById('rule-resolution-enable');
    const resSettings = document.getElementById('resolution-settings-container');
    const resMode = document.getElementById('rule-resolution-mode');
    const resTimeoutCont = document.getElementById('resolution-timeout-container');
    const resPatternsCont = document.getElementById('resolution-patterns-container');
    const resAiEnable = document.getElementById('rule-resolution-ai-enabled');
    const resAiOptions = document.getElementById('resolution-ai-options');

    function updateResolutionUI() {
        if (!resEnable) return;
        resSettings.style.display = resEnable.checked ? 'flex' : 'none';
        
        if (resEnable.checked && resMode) {
            const mode = resMode.value;
            if (mode === 'timeout') {
                if (resTimeoutCont) resTimeoutCont.classList.remove('hidden');
                if (resPatternsCont) resPatternsCont.classList.add('hidden');
            } else if (mode === 'pattern') {
                if (resTimeoutCont) resTimeoutCont.classList.add('hidden');
                if (resPatternsCont) resPatternsCont.classList.remove('hidden');
            } else {
                if (resTimeoutCont) resTimeoutCont.classList.remove('hidden');
                if (resPatternsCont) resPatternsCont.classList.remove('hidden');
            }
        }
        
        if (resAiEnable && resAiOptions) {
            if (resAiEnable.checked) {
                resAiOptions.classList.remove('hidden');
            } else {
                resAiOptions.classList.add('hidden');
            }
        }
    }
    window.updateResolutionUI = updateResolutionUI;

    if (resEnable) resEnable.addEventListener('change', updateResolutionUI);
    if (resMode) resMode.addEventListener('change', updateResolutionUI);
    if (resAiEnable) resAiEnable.addEventListener('change', updateResolutionUI);
    
    // Initial display sync
    updateResolutionUI();

    // ── Resolution pattern tags wiring ──
    const addBtn = document.getElementById('resolution-pattern-add-btn');
    const addInput = document.getElementById('resolution-pattern-input');
    const clearAllBtn = document.getElementById('resolution-patterns-clear-all');

    if (addBtn && addInput) {
        addBtn.addEventListener('click', (e) => {
            e.preventDefault();
            if (addInput.value.trim()) {
                resTagsAdd(addInput.value);
                addInput.value = '';
                addInput.focus();
            }
        });
        addInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                addBtn.click();
            }
        });
    }
    if (clearAllBtn) {
        clearAllBtn.addEventListener('click', (e) => {
            e.preventDefault();
            if (typeof showInlineConfirm === 'function') {
                showInlineConfirm(
                    clearAllBtn,
                    _resTagT('rules.resolution_patterns_confirm_clear', 'Delete all resolution patterns?'),
                    () => { resTagsClear(); }
                );
            } else {
                resTagsClear();
            }
        });
    }
    // Initial render (empty for new rule)
    _resTagsRender();

    setupKeywordSuggestions();
}

/* ── Webhook helpers ───────────────────────────────────────────────────────── */
function fallbackCopyTextToClipboard(text) {
    const textArea = document.createElement("textarea");
    textArea.value = text;
    textArea.style.top = "0";
    textArea.style.left = "0";
    textArea.style.position = "fixed";
    document.body.appendChild(textArea);
    textArea.focus();
    textArea.select();
    try {
        document.execCommand('copy');
    } catch (err) {
        console.error('Fallback copy failed', err);
    }
    document.body.removeChild(textArea);
}

function updateModalWebhookUrl() {
    if (!window._currentWebhookToken) return;
    const url = window.location.origin + '/api/webhook/logs/' + window._currentWebhookToken;
    document.getElementById('webhook-curl-cmd').textContent = url;
}

function copyModalWebhookUrl(btnElement) {
    const text = document.getElementById('webhook-curl-cmd').textContent;
    
    function showCopied() {
        if (!btnElement) return;
        const oldText = btnElement.innerHTML;
        btnElement.innerHTML = window.t ? window.t('rules.webhook_copied') || "✅ Copié !" : "✅ Copié !";
        setTimeout(() => { btnElement.innerHTML = oldText; }, 2000);
    }

    if (!navigator.clipboard) {
        fallbackCopyTextToClipboard(text);
        showCopied();
        return;
    }
    navigator.clipboard.writeText(text).then(() => {
        showCopied();
    }).catch(err => {
        console.error('Erreur de copie:', err);
        fallbackCopyTextToClipboard(text);
        showCopied();
    });
}

function copyWebhookUrl(ruleId, btnElement) {
    const url = window.location.origin + '/api/webhook/logs/' + ruleId;
    
    function showCopied() {
        if (!btnElement) return;
        const oldText = btnElement.innerHTML;
        btnElement.innerHTML = window.t ? window.t('rules.webhook_copied') || "✅ Copié !" : "✅ Copié !";
        setTimeout(() => { btnElement.innerHTML = oldText; }, 2000);
    }

    if (!navigator.clipboard) {
        fallbackCopyTextToClipboard(url);
        showCopied();
        return;
    }
    navigator.clipboard.writeText(url).then(() => {
        showCopied();
    }).catch(err => {
        console.error('Erreur de copie:', err);
        fallbackCopyTextToClipboard(url);
        showCopied();
    });
}

/* ── Reset form ────────────────────────────────────────────────────────────── */
function resetForm() {
    document.getElementById('rule-id').value = '';
    document.getElementById('rule-name').value = '';
    document.getElementById('rule-path').value = '';
    window._currentWebhookToken = null;

    const sourceCards = document.querySelectorAll('.source-card');
    sourceCards.forEach(c => c.classList.remove('kw-tab--active'));

    const localCard = document.querySelector('.source-card[data-source="local"]');
    if (localCard) {
        localCard.classList.add('kw-tab--active');
        document.getElementById('path-group').classList.remove('hidden');
        document.getElementById('webhook-group').classList.add('hidden');
        document.getElementById('rule-path').setAttribute('required', 'required');
    }
    document.getElementById('rule-keywords').value = '';
    document.getElementById('rule-context').value = '';
    document.getElementById('rule-enabled').checked = true;
    document.getElementById('rule-notify').checked = true;
    document.getElementById('rule-context-lines').value = '5';
    document.getElementById('rule-anti-spam').value = '60';
    document.getElementById('rule-severity-threshold').value = 'info';
    const inactCheck = document.getElementById('rule-inactivity-warning-enabled');
    if (inactCheck) {
        inactCheck.checked = true;
        document.getElementById('inactivity-hours-container').style.display = 'flex';
        document.getElementById('rule-inactivity-period-hours').value = '1';
        const inactNotify = document.getElementById('rule-inactivity-notify');
        if (inactNotify) inactNotify.checked = true;
    }
    const exclEl = document.getElementById('rule-excluded-patterns');
    if (exclEl) exclEl.value = '';

    // MON-18
    const resEnable = document.getElementById('rule-resolution-enable');
    if (resEnable) {
        resEnable.checked = true;
        document.getElementById('rule-resolution-mode').value = 'timeout';
        document.getElementById('rule-resolution-timeout').value = '30';
        resTagsClear();
        document.getElementById('rule-resolution-ai-enabled').checked = false;
        document.getElementById('rule-resolution-notify-search').checked = false;
        document.getElementById('rule-resolution-notify-resolved').checked = true;
        if (window.updateResolutionUI) window.updateResolutionUI();
    }

    document.getElementById('modal-title').textContent = window.t ? window.t('rules.modal_new_title') : 'New rule';
    closeFileBrowser();

    const previewContainer = document.getElementById('file-preview-container');
    if (previewContainer) {
        previewContainer.classList.add('hidden');
        document.getElementById('file-preview-content').innerHTML = '';
    }
    // Show quick templates for new rule
    const tpl = document.getElementById('modal-quick-templates');
    if (tpl) tpl.classList.remove('hidden');
    // Reset the keyword learning wizard and return to manual tab
    if (typeof kwWizardReset === 'function') kwWizardReset();
}

/* ── Save rule ─────────────────────────────────────────────────────────────── */
async function saveRule() {
    const id = document.getElementById('rule-id').value;
    const activeCard = document.querySelector('.source-card.kw-tab--active');
    const isWebhook = activeCard && activeCard.dataset.source === 'webhook';

    let logFilePath = document.getElementById('rule-path').value;
    if (isWebhook) {
        logFilePath = '[WEBHOOK]:' + (window._currentWebhookToken || _generateUUID());
    }

    const data = {
        name: document.getElementById('rule-name').value,
        log_file_path: logFilePath,
        keywords: document.getElementById('rule-keywords').value.split(',').map(k => k.trim()).filter(k => k),
        application_context: document.getElementById('rule-context').value,
        enabled: document.getElementById('rule-enabled').checked,
        notify_on_match: document.getElementById('rule-notify').checked,
        context_lines: parseInt(document.getElementById('rule-context-lines').value) || 5,
        anti_spam_delay: parseInt(document.getElementById('rule-anti-spam').value) || 60,
        notify_severity_threshold: document.getElementById('rule-severity-threshold').value,
        inactivity_warning_enabled: document.getElementById('rule-inactivity-warning-enabled').checked,
        inactivity_period_hours: parseInt(document.getElementById('rule-inactivity-period-hours').value) || 12,
        inactivity_notify: document.getElementById('rule-inactivity-notify') ? document.getElementById('rule-inactivity-notify').checked : true,
        excluded_patterns: ((document.getElementById('rule-excluded-patterns') || {}).value || '')
            .split(',').map(p => p.trim()).filter(p => p),
        // MON-18
        resolution_mode: document.getElementById('rule-resolution-enable').checked ? document.getElementById('rule-resolution-mode').value : 'disabled',
        resolution_timeout_minutes: parseInt(document.getElementById('rule-resolution-timeout').value) || 30,
        resolution_patterns: [..._resolutionPatterns],
        resolution_ai_enabled: document.getElementById('rule-resolution-ai-enabled').checked,
        resolution_notify_search: document.getElementById('rule-resolution-notify-search').checked,
        resolution_notify_resolved: document.getElementById('rule-resolution-notify-resolved').checked,
    };

    // If on manual tab, clear the learning session link so the auto tab
    // is not selected next time and the rule card shows manual state
    const autoTabActive = document.getElementById('kw-tab-auto')
        ?.classList.contains('kw-tab--active');
    if (!autoTabActive && id) {
        data.last_learning_session_id = -1; // sentinel: clear session link
    }

    try {
        if (id) {
            await apiFetch(`/api/rules/${id}`, {
                method: 'PUT',
                body: data,
            });
        } else {
            await apiFetch('/api/rules', {
                method: 'POST',
                body: data,
            });
        }

        // Clean up wizard state if we were on the manual tab
        if (!autoTabActive && typeof kwWizardReset === 'function') {
            kwWizardReset();
        }

        document.getElementById('rule-modal').classList.add('hidden');
        if (_ruleModalOnSave) _ruleModalOnSave();
    } catch (error) {
        console.error('Erreur sauvegarde règle:', error);
        alert((window.t ? window.t('common.error') : 'Erreur') + ': ' + error.message);
    }
}

/* ── Edit rule (populate form + open modal) ────────────────────────────────── */
async function editRule(id) {
    try {
        const rule = await apiFetch(`/api/rules/${id}`);
        document.getElementById('rule-id').value = rule.id;
        document.getElementById('rule-name').value = rule.name;

        const sourceCards = document.querySelectorAll('.source-card');
        sourceCards.forEach(c => c.classList.remove('kw-tab--active'));

        if (rule.log_file_path && rule.log_file_path.startsWith('[WEBHOOK]')) {
            const webhookCard = document.querySelector('.source-card[data-source="webhook"]');
            if (webhookCard) {
                window._currentWebhookToken = rule.log_file_path.split(':')[1] || rule.id;
                webhookCard.click();
            }
        } else {
            const localCard = document.querySelector('.source-card[data-source="local"]');
            if (localCard) {
                localCard.click();
            }
            document.getElementById('rule-path').value = rule.log_file_path;
        }

        document.getElementById('rule-keywords').value = rule.keywords.join(', ');
        document.getElementById('rule-context').value = rule.application_context || '';
        document.getElementById('rule-enabled').checked = rule.enabled;
        document.getElementById('rule-notify').checked = rule.notify_on_match;
        document.getElementById('rule-context-lines').value = rule.context_lines || 5;
        document.getElementById('rule-anti-spam').value = rule.anti_spam_delay || 60;
        document.getElementById('rule-severity-threshold').value = rule.notify_severity_threshold || 'info';
        
        const inactCheck = document.getElementById('rule-inactivity-warning-enabled');
        if (inactCheck) {
            inactCheck.checked = rule.inactivity_warning_enabled;
            document.getElementById('inactivity-hours-container').style.display = rule.inactivity_warning_enabled ? 'flex' : 'none';
            document.getElementById('rule-inactivity-period-hours').value = rule.inactivity_period_hours || 12;
            const inactNotify = document.getElementById('rule-inactivity-notify');
            if (inactNotify) inactNotify.checked = rule.inactivity_notify !== false;
        }

        const exclEl = document.getElementById('rule-excluded-patterns');
        if (exclEl) exclEl.value = (rule.excluded_patterns || []).join(', ');

        // MON-18
        const resEnable = document.getElementById('rule-resolution-enable');
        if (resEnable) {
            const hasRes = rule.resolution_mode !== 'disabled';
            resEnable.checked = hasRes;
            document.getElementById('rule-resolution-mode').value = hasRes ? rule.resolution_mode : 'timeout';
            document.getElementById('rule-resolution-timeout').value = rule.resolution_timeout_minutes || 30;
            resTagsSet(rule.resolution_patterns || []);
            document.getElementById('rule-resolution-ai-enabled').checked = rule.resolution_ai_enabled || false;
            document.getElementById('rule-resolution-notify-search').checked = rule.resolution_notify_search || false;
            document.getElementById('rule-resolution-notify-resolved').checked = rule.resolution_notify_resolved !== false;
            if (window.updateResolutionUI) window.updateResolutionUI();
        }

        document.getElementById('modal-title').textContent = window.t ? window.t('rules.modal_edit_title') : 'Edit rule';
        // Hide quick templates in edit mode
        const tpl = document.getElementById('modal-quick-templates');
        if (tpl) tpl.classList.add('hidden');
        document.getElementById('rule-modal').classList.remove('hidden');
        closeFileBrowser();
        if (rule.log_file_path !== '[WEBHOOK]') {
            fetchFilePreview(rule.log_file_path);
        } else {
            const container = document.getElementById('file-preview-container');
            if (container) container.classList.add('hidden');
        }

        // Only open auto-learning tab if the rule still has a linked session.
        // If last_learning_session_id is null, the user saved from the manual tab
        // → stay on manual tab (default).
        if (rule.last_learning_session_id && typeof kwWizardLoadSession === 'function') {
            await kwWizardLoadSession(rule.last_learning_session_id);
        } else if (typeof kwWizardReset === 'function') {
            // Ensure wizard state is clean when opening on manual tab
            kwWizardReset();
        }
    } catch (error) {
        console.error('Erreur chargement règle:', error);
    }
}

/* ── File browser ──────────────────────────────────────────────────────────── */
let _fileRootsCache = null;
let _fileBrowserCurrentPath = null;

function setupFileBrowser() {
    const browseBtn = document.getElementById('browse-logs-btn');
    const browser = document.getElementById('file-browser');
    const closeBtn = document.getElementById('file-browser-close');
    const list = document.getElementById('file-browser-list');
    const breadcrumb = document.getElementById('file-browser-breadcrumb');
    const showHidden = document.getElementById('file-browser-show-hidden');

    if (!browseBtn || !browser || !closeBtn || !list || !breadcrumb || !showHidden) return;

    browseBtn.addEventListener('click', async () => {
        browser.classList.toggle('hidden');
        if (browser.classList.contains('hidden')) return;

        await ensureFileRoots();
        const desired = getSuggestedBrowsePath();
        const startPath = desired || (_fileRootsCache && _fileRootsCache[0]) || '/logs';
        await browsePath(startPath);
    });

    closeBtn.addEventListener('click', () => closeFileBrowser());

    showHidden.addEventListener('change', async () => {
        if (!_fileBrowserCurrentPath) return;
        await browsePath(_fileBrowserCurrentPath);
    });

    list.addEventListener('click', async (e) => {
        const row = e.target.closest('[data-path]');
        if (!row) return;
        const path = row.getAttribute('data-path');
        const isDir = row.getAttribute('data-is-dir') === 'true';
        if (!path) return;

        if (isDir) {
            await browsePath(path);
        } else {
            document.getElementById('rule-path').value = path;
            closeFileBrowser();
            fetchFilePreview(path);
        }
    });

    breadcrumb.addEventListener('click', async (e) => {
        const el = e.target.closest('[data-root]');
        if (!el) return;
        const root = el.getAttribute('data-root');
        if (!root) return;
        await browsePath(root);
    });
}

function closeFileBrowser() {
    const browser = document.getElementById('file-browser');
    if (!browser) return;
    browser.classList.add('hidden');
}

async function ensureFileRoots() {
    if (_fileRootsCache) return _fileRootsCache;
    try {
        const res = await apiFetch('/api/files/roots');
        _fileRootsCache = (res && res.roots) || [];
    } catch (e) {
        _fileRootsCache = [];
    }
    return _fileRootsCache;
}

function getSuggestedBrowsePath() {
    const input = document.getElementById('rule-path');
    if (!input) return null;
    const v = (input.value || '').trim();
    if (!v) return null;
    // If user entered a file path, start from its parent folder.
    const slash = Math.max(v.lastIndexOf('/'), v.lastIndexOf('\\'));
    if (slash <= 0) return v;
    return v.slice(0, slash);
}

async function browsePath(path) {
    const list = document.getElementById('file-browser-list');
    const breadcrumb = document.getElementById('file-browser-breadcrumb');
    const showHidden = document.getElementById('file-browser-show-hidden');
    if (!list || !breadcrumb || !showHidden) return;

    list.innerHTML = `<div class="loading">${window.t ? window.t('common.loading') : 'Chargement...'}</div>`;

    try {
        const res = await apiFetch(`/api/files/browse?path=${encodeURIComponent(path)}&show_hidden=${showHidden.checked ? 'true' : 'false'}`);
        _fileBrowserCurrentPath = res.path;

        const roots = await ensureFileRoots();
        const rootsLinks = (roots || [])
            .map(r => `<span class="chip" data-root="${escapeHtml(r)}">📌 ${escapeHtml(r)}</span>`)
            .join(' ');

        breadcrumb.innerHTML = `
            <div><strong>${escapeHtml(res.path)}</strong></div>
            ${rootsLinks ? `<div class="form-hint">Racines: ${rootsLinks}</div>` : ''}
        `;

        const rows = [];
        if (res.parent) {
            rows.push(renderBrowserRow({ name: '.. (parent)', path: res.parent, is_dir: true, readable: true }, true));
        }

        for (const e of res.entries) {
            rows.push(renderBrowserRow(e, false));
        }

        list.innerHTML = rows.join('') || `<div class="loading">${window.t ? window.t('rules.folder_empty') : 'Empty folder'}</div>`;
    } catch (error) {
        console.error('Erreur browse:', error);
        list.innerHTML = `<div class="loading">${window.t ? window.t('common.error') : 'Erreur'}: ${escapeHtml(error.message || 'Impossible de lister ce dossier')}</div>`;
    }
}

function renderBrowserRow(entry, isParent) {
    const icon = entry.is_dir ? '📁' : '📄';
    const meta = entry.is_dir ? (window.t ? window.t('rules.folder') : 'Folder') : (window.t ? window.t('rules.file') : 'File');
    const disabled = entry.readable === false ? 'opacity:0.6; pointer-events:none;' : '';
    const name = isParent ? entry.name : entry.name;
    return `
        <div class="file-browser-item" style="${disabled}" data-path="${escapeHtml(entry.path)}" data-is-dir="${entry.is_dir ? 'true' : 'false'}">
            <div class="left">
                <span>${icon}</span>
                <span class="name">${escapeHtml(name)}</span>
            </div>
            <div class="meta">${meta}${entry.readable === false ? ` • ${window.t ? window.t('rules.unreadable') : 'unreadable'}` : ''}</div>
        </div>
    `;
}

async function fetchFilePreview(path) {
    const container = document.getElementById('file-preview-container');
    const content = document.getElementById('file-preview-content');
    if (!container || !content) return;

    if (!path || path.trim() === '') {
        container.classList.add('hidden');
        return;
    }

    container.classList.remove('hidden');
    content.innerHTML = `<em>${window.t ? window.t('rules.loading_preview') : 'Loading preview...'}</em>`;

    try {
        const res = await apiFetch(`/api/files/tail?path=${encodeURIComponent(path)}&lines=10`);
        if (res.lines && res.lines.length > 0) {
            content.innerHTML = res.lines.map(l => escapeHtml(typeof l === 'string' ? l : l.text)).join('<br>');
        } else {
            content.innerHTML = `<em>${window.t ? window.t('rules.file_empty_preview') : 'Empty file.'}</em>`;
        }
        content.scrollTop = content.scrollHeight;
    } catch (e) {
        content.innerHTML = `<em style="color: var(--danger)">${window.t ? window.t('rules.preview_unavailable') : 'Preview unavailable:'} ${escapeHtml(e.message)}</em>`;
    }
}

/* ── Quick templates ──────────────────────────────────────────────────────── */
function applyTemplate(type) {
    resetForm();
    const templates = {
        'auth': {
            name: window.t ? window.t('rules.template_auth') : 'Security SSH / Connections',
            path: '/system-logs/auth.log',
            keywords: 'failed, Accepted, invalid user, authentication failure, sudo',
            context: 'SSH login and sudo usage monitoring on Ubuntu.'
        },
        'syslog': {
            name: window.t ? window.t('rules.template_syslog') : 'System Stability',
            path: '/system-logs/syslog',
            keywords: 'error, failed, fatal, critical, oom-killer, stopped',
            context: 'General Ubuntu system logs. Monitors service crashes and system errors.'
        },
        'journald': {
            name: window.t ? window.t('rules.template_journald') : 'Journald (Docker Relay)',
            path: '/logs/host_system_journal.log',
            keywords: 'error, fatal, panic, critical, failed',
            context: 'Relay of Systemd binary logs (journalctl) to a readable text file.'
        }
    };

    const t = templates[type];
    if (t) {
        document.getElementById('rule-name').value = t.name;
        document.getElementById('rule-path').value = t.path;
        document.getElementById('rule-keywords').value = t.keywords;
        document.getElementById('rule-context').value = t.context;

        document.getElementById('modal-title').textContent = window.t ? window.t('rules.new_rule_from_template') : 'New rule (Template)';

        fetchFilePreview(t.path);
    }
}
