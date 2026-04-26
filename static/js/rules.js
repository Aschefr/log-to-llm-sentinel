document.addEventListener('DOMContentLoaded', () => {
    loadRules();
    setupModal();
    setupKeywordSuggestions();

    window.i18n?.onLanguageChange(() => {
        loadRules();
    });
});

const TYPICAL_KEYWORDS = [
    'error',
    'exception',
    'fatal',
    'critical',
    'warning',
    'warn',
    'timeout',
    'timed out',
    'connection refused',
    'refused',
    'unhandled',
    'panic',
    'traceback',
    'stacktrace',
    'failed',
    'failure',
    'permission denied',
    'unauthorized',
    'forbidden',
    'rate limit',
    'oom',
    'out of memory',
    'disk full',
    'no space left',
    'segfault',
    '502',
    '503',
    '504',
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

async function loadRules() {
    try {
        const rules = await apiFetch('/api/rules');
        const container = document.getElementById('rules-list');

        if (rules.length === 0) {
            container.innerHTML = `<div class="loading">${window.t ? window.t('rules.no_rules') : 'No rules configured'}</div>`;
            return;
        }

        container.innerHTML = rules.map(rule => `
            <div class="rule-card">
                <div class="rule-info">
                    <h3>${escapeHtml(rule.name)}</h3>
                    <p>📁 ${escapeHtml(rule.log_file_path)}</p>
                    <p>🔑 ${rule.keywords.join(', ')}</p>
                    ${rule.application_context ? `<p>🧩 ${escapeHtml(rule.application_context)}</p>` : ''}
                    <p>${rule.enabled ? `✅ ${window.t ? window.t('rules.enabled_status') : 'Enabled'}` : `❌ ${window.t ? window.t('rules.disabled_status') : 'Disabled'}`} | 🔔 ${rule.notify_on_match ? `${window.t ? window.t('rules.notification_threshold') : 'Threshold:'} ${rule.notify_severity_threshold || 'info'}` : (window.t ? window.t('rules.notifications_disabled') : 'Notifications disabled')}</p>
                </div>
                <div class="rule-actions">
                    <button id="test-btn-${rule.id}" class="btn btn-secondary btn-sm" onclick="testRule(${rule.id})">${window.t ? window.t('rules.test_rule') : '🧪 Test'}</button>
                    <button class="btn btn-primary btn-sm" onclick="editRule(${rule.id})">${window.t ? window.t('rules.edit') : '✏️ Edit'}</button>
                    <button class="btn btn-danger btn-sm" onclick="deleteRule(${rule.id}, this)">${window.t ? window.t('rules.delete') : '🗑️ Delete'}</button>
                </div>
                <div class="rule-toggles" style="display: flex; flex-direction: column; gap: 0.5rem; flex-basis: 100%;">
                    <div class="rule-last-line" style="display:flex; justify-content:space-between; align-items:center;">
                        <div>
                            <strong>${window.t ? window.t('rules.last_detected_line') : 'Last detected line:'}</strong>
                            <div class="last-line-content">${escapeHtml(rule.last_log_line || (window.t ? window.t('dashboard.no_line_found') : 'No line found or file inaccessible'))}</div>
                        </div>
                        <button class="btn btn-secondary btn-sm" onclick="window.location.href='/monitor?rule=${rule.id}&line=${encodeURIComponent(rule.last_log_line || '')}'" data-i18n="rules.view_in_monitor">
                            🔍 ${window.t ? window.t('rules.view_in_monitor') || 'Voir dans Monitor' : 'Voir dans Monitor'}
                        </button>
                    </div>
                </div>
            </div>
        `).join('');
    } catch (error) {
        console.error('Erreur chargement règles:', error);
    }
}


async function testRule(id) {
    const btn = document.getElementById(`test-btn-${id}`);
    const originalText = btn ? btn.innerHTML : (window.t ? window.t('rules.test_rule') : '🧪 Test');
    
    const abortController = new AbortController();
    let stopBtn = null;
    
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = window.t ? window.t('rules.test_in_progress') : 'Testing...';
        btn.classList.add('pulse-animation');
        
        stopBtn = document.createElement('button');
        stopBtn.className = 'btn btn-danger btn-sm';
        stopBtn.style.marginLeft = '0.5rem';
        stopBtn.innerHTML = window.t ? window.t('rules.stop_test') : '🛑 Stop';
        stopBtn.onclick = () => abortController.abort();
        btn.parentNode.insertBefore(stopBtn, btn.nextSibling);
    }

    try {
        await apiFetch(`/api/rules/${id}/test`, { 
            method: 'POST',
            signal: abortController.signal
        });
        
        // On pourrait notifier l'utilisateur de se rendre dans le Monitor pour voir le résultat.

        if (btn) {
            btn.innerHTML = window.t ? window.t('rules.test_done') : '✅ Done';
            btn.classList.remove('pulse-animation');
            btn.classList.add('btn-success-temporary');
            setTimeout(() => {
                if (document.getElementById(`test-btn-${id}`)) {
                    btn.innerHTML = originalText;
                    btn.disabled = false;
                    btn.classList.remove('btn-success-temporary');
                }
            }, 2500);
        }
    } catch (e) {
        if (btn) {
            btn.classList.remove('pulse-animation');
            btn.classList.add('btn-danger');
            if (e.name === 'AbortError') {
                btn.innerHTML = window.t ? window.t('rules.test_cancelled') : '❌ Cancelled';
            } else {
                console.error('Erreur test rule:', e);
                btn.innerHTML = window.t ? window.t('rules.test_error_btn') : '❌ Error';
            }
            setTimeout(() => {
                if (document.getElementById(`test-btn-${id}`)) {
                    btn.innerHTML = originalText;
                    btn.disabled = false;
                    btn.classList.remove('btn-danger');
                }
            }, 2500);
        }
    } finally {
        if (stopBtn && stopBtn.parentNode) {
            stopBtn.parentNode.removeChild(stopBtn);
        }
    }
}

function setupModal() {
    const modal = document.getElementById('rule-modal');
    const addBtn = document.getElementById('add-rule-btn');
    const closeBtn = document.getElementById('close-modal');
    const cancelBtn = document.getElementById('cancel-rule');
    const form = document.getElementById('rule-form');

    setupFileBrowser();

    addBtn.addEventListener('click', () => {
        resetForm();
        modal.classList.remove('hidden');
    });

    closeBtn.addEventListener('click', () => {
        modal.classList.add('hidden');
    });

    cancelBtn.addEventListener('click', () => {
        modal.classList.add('hidden');
    });

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        await saveRule();
    });

    const pathInput = document.getElementById('rule-path');
    let previewTimeout;
    pathInput.addEventListener('input', () => {
        clearTimeout(previewTimeout);
        previewTimeout = setTimeout(() => {
            fetchFilePreview(pathInput.value);
        }, 500);
    });
}

function resetForm() {
    document.getElementById('rule-id').value = '';
    document.getElementById('rule-name').value = '';
    document.getElementById('rule-path').value = '';
    document.getElementById('rule-keywords').value = '';
    document.getElementById('rule-context').value = '';
    document.getElementById('rule-enabled').checked = true;
    document.getElementById('rule-notify').checked = true;
    document.getElementById('rule-context-lines').value = '5';
    document.getElementById('rule-anti-spam').value = '60';
    document.getElementById('rule-severity-threshold').value = 'info';
    document.getElementById('modal-title').textContent = window.t ? window.t('rules.modal_new_title') : 'New rule';
    closeFileBrowser();
    
    const previewContainer = document.getElementById('file-preview-container');
    if (previewContainer) {
        previewContainer.classList.add('hidden');
        document.getElementById('file-preview-content').innerHTML = '';
    }
    // Reset the keyword learning wizard if open
    if (typeof kwWizardClose === 'function') kwWizardClose();
}

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

async function saveRule() {
    const id = document.getElementById('rule-id').value;
    const data = {
        name: document.getElementById('rule-name').value,
        log_file_path: document.getElementById('rule-path').value,
        keywords: document.getElementById('rule-keywords').value.split(',').map(k => k.trim()).filter(k => k),
        application_context: document.getElementById('rule-context').value,
        enabled: document.getElementById('rule-enabled').checked,
        notify_on_match: document.getElementById('rule-notify').checked,
        context_lines: parseInt(document.getElementById('rule-context-lines').value) || 5,
        anti_spam_delay: parseInt(document.getElementById('rule-anti-spam').value) || 60,
        notify_severity_threshold: document.getElementById('rule-severity-threshold').value,
    };

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

        document.getElementById('rule-modal').classList.add('hidden');
        loadRules();
    } catch (error) {
        console.error('Erreur sauvegarde règle:', error);
        alert((window.t ? window.t('common.error') : 'Erreur') + ': ' + error.message);
    }
}

async function editRule(id) {
    try {
        const rule = await apiFetch(`/api/rules/${id}`);
        document.getElementById('rule-id').value = rule.id;
        document.getElementById('rule-name').value = rule.name;
        document.getElementById('rule-path').value = rule.log_file_path;
        document.getElementById('rule-keywords').value = rule.keywords.join(', ');
        document.getElementById('rule-context').value = rule.application_context || '';
        document.getElementById('rule-enabled').checked = rule.enabled;
        document.getElementById('rule-notify').checked = rule.notify_on_match;
        document.getElementById('rule-context-lines').value = rule.context_lines || 5;
        document.getElementById('rule-anti-spam').value = rule.anti_spam_delay || 60;
        document.getElementById('rule-severity-threshold').value = rule.notify_severity_threshold || 'info';
        document.getElementById('modal-title').textContent = window.t ? window.t('rules.modal_edit_title') : 'Edit rule';
        document.getElementById('rule-modal').classList.remove('hidden');
        closeFileBrowser();
        fetchFilePreview(rule.log_file_path);
    } catch (error) {
        console.error('Erreur chargement règle:', error);
    }
}

async function deleteRule(id, btnElement) {
    showInlineConfirm(btnElement, window.t ? window.t('rules.delete_confirm') : 'Are you sure you want to delete this rule?', async () => {
        try {
            await apiFetch(`/api/rules/${id}`, {
                method: 'DELETE',
            });
            loadRules();
        } catch (error) {
            console.error('Erreur suppression règle:', error);
            alert((window.t ? window.t('common.error') : 'Erreur') + ': ' + error.message);
        }
    });
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
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

function applyTemplate(type) {
    resetForm();
    const templates = {
        'auth': {
            name: window.t ? window.t('rules.template_auth').replace('🛡️ ', '') : 'Security SSH / Connections',
            path: '/system-logs/auth.log',
            keywords: 'failed, Accepted, invalid user, authentication failure, sudo',
            context: 'SSH login and sudo usage monitoring on Ubuntu.'
        },
        'syslog': {
            name: window.t ? window.t('rules.template_syslog').replace('⚙️ ', '') : 'System Stability',
            path: '/system-logs/syslog',
            keywords: 'error, failed, fatal, critical, oom-killer, stopped',
            context: 'General Ubuntu system logs. Monitors service crashes and system errors.'
        },
        'journald': {
            name: window.t ? window.t('rules.template_journald').replace('📜 ', '') : 'Journald (Docker Relay)',
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
        
        // Ouvrir la modal
        document.getElementById('rule-modal').classList.remove('hidden');
        document.getElementById('modal-title').textContent = window.t ? window.t('rules.new_rule_from_template') : 'New rule (Template)';
        
        // Déclencher l'aperçu si possible
        fetchFilePreview(t.path);
    }
}

