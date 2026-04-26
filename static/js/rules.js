document.addEventListener('DOMContentLoaded', () => {
    loadRules();
    setupModal();
    setupKeywordSuggestions();
    loadAnalyses();

    window.i18n?.onLanguageChange(() => {
        loadRules();
        loadAnalyses();
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
            container.innerHTML = '<div class="loading">Aucune règle configurée</div>';
            return;
        }

        container.innerHTML = rules.map(rule => `
            <div class="rule-card">
                <div class="rule-info">
                    <h3>${escapeHtml(rule.name)}</h3>
                    <p>📁 ${escapeHtml(rule.log_file_path)}</p>
                    <p>🔑 ${rule.keywords.join(', ')}</p>
                    ${rule.application_context ? `<p>🧩 ${escapeHtml(rule.application_context)}</p>` : ''}
                    <p>${rule.enabled ? '✅ Activée' : '❌ Désactivée'} | 🔔 ${rule.notify_on_match ? `Seuil: ${rule.notify_severity_threshold || 'info'}` : 'Notifications désactivées'}</p>
                </div>
                <div class="rule-actions">
                    <button id="test-btn-${rule.id}" class="btn btn-secondary btn-sm" onclick="testRule(${rule.id})">🧪 Tester</button>
                    <button class="btn btn-primary btn-sm" onclick="editRule(${rule.id})">✏️ Éditer</button>
                    <button class="btn btn-danger btn-sm" onclick="deleteRule(${rule.id}, this)">🗑️ Supprimer</button>
                </div>
                <div class="rule-toggles" style="display: flex; flex-direction: column; gap: 0.5rem; flex-basis: 100%;">
                    <div class="rule-last-line">
                        <strong>Dernière ligne détectée :</strong>
                        <div class="last-line-content">${escapeHtml(rule.last_log_line || 'Aucune ligne trouvée ou fichier inaccessible')}</div>
                    </div>
                    <div>
                        <div class="rule-history-toggle" onclick="toggleLiveLogs(${rule.id}, this, '${escapeHtml(rule.log_file_path).replace(/'/g, "\\'")}')">
                            <span class="toggle-icon">▶</span> Log en temps réel
                        </div>
                        <div id="live-logs-${rule.id}" class="rule-history-inline hidden"></div>
                    </div>
                    <div>
                        <div class="rule-history-toggle" onclick="toggleRuleHistory(${rule.id}, this)">
                            <span class="toggle-icon">▶</span> Analyses LLM récentes
                        </div>
                        <div id="rule-history-${rule.id}" class="rule-history-inline hidden"></div>
                    </div>
                </div>
            </div>
        `).join('');
    } catch (error) {
        console.error('Erreur chargement règles:', error);
    }
}

const liveLogIntervals = {};

async function toggleLiveLogs(ruleId, toggleElement, path) {
    const container = document.getElementById(`live-logs-${ruleId}`);
    if (!container) return;

    let icon = null;
    if (toggleElement) {
        icon = toggleElement.querySelector('.toggle-icon');
    }

    if (container.classList.contains('hidden')) {
        container.classList.remove('hidden');
        if (icon) icon.textContent = '▼';
        container.innerHTML = '<div class="loading">Chargement des logs...</div>';
        
        const fetchLogs = async () => {
            if (container.classList.contains('hidden')) return; // Stop if closed
            try {
                const res = await apiFetch(`/api/files/tail?path=${encodeURIComponent(path)}&lines=15`);
                if (res.lines && res.lines.length > 0) {
                    const newContent = res.lines.map(l => escapeHtml(typeof l === 'string' ? l : l.text)).join('<br>');
                    let pre = document.getElementById(`live-log-pre-${ruleId}`);
                    
                    if (!pre) {
                        // Première fois : création du conteneur
                        container.innerHTML = `
                            <div class="live-logs-header-inline">
                                <button class="btn btn-secondary btn-sm" onclick="copyLiveLogs(${ruleId})">📋 Copier</button>
                                <button class="btn btn-secondary btn-sm" onclick="clearLiveLogs(${ruleId})">🗑️ Effacer</button>
                            </div>
                            <pre class="live-log-content" id="live-log-pre-${ruleId}">${newContent}</pre>
                        `;
                        pre = document.getElementById(`live-log-pre-${ruleId}`);
                        // Délai pour laisser le DOM se mettre à jour avant de scroller
                        setTimeout(() => pre.scrollTop = pre.scrollHeight, 10);
                    } else {
                        // Éviter de toucher au DOM si le texte est identique
                        if (pre.innerHTML !== newContent) {
                            // On vérifie si l'utilisateur est déjà tout en bas
                            const isAtBottom = Math.abs((pre.scrollHeight - pre.scrollTop) - pre.clientHeight) < 10;
                            
                            pre.innerHTML = newContent;
                            
                            // Si l'utilisateur lisait en bas, on auto-scroll
                            if (isAtBottom) {
                                setTimeout(() => pre.scrollTop = pre.scrollHeight, 10);
                            }
                        }
                    }
                } else {
                    container.innerHTML = '<em>Fichier vide ou illisible.</em>';
                }
            } catch (e) {
                container.innerHTML = `<em style="color: var(--danger)">${window.t ? window.t('common.error') : 'Erreur'} : ${escapeHtml(e.message)}</em>`;
            }
        };

        await fetchLogs();
        liveLogIntervals[ruleId] = setInterval(fetchLogs, 3000); // refresh every 3s
    } else {
        container.classList.add('hidden');
        if (icon) icon.textContent = '▶';
        if (liveLogIntervals[ruleId]) {
            clearInterval(liveLogIntervals[ruleId]);
            delete liveLogIntervals[ruleId];
        }
    }
}

async function toggleRuleHistory(ruleId, toggleElement) {
    const container = document.getElementById(`rule-history-${ruleId}`);
    if (!container) return;

    let icon = null;
    if (toggleElement) {
        icon = toggleElement.querySelector('.toggle-icon');
    } else {
        // Fallback for programmatic open
        const card = container.closest('.rule-card');
        if (card) {
            icon = card.querySelector('.toggle-icon');
        }
    }

    if (container.classList.contains('hidden')) {
        // Afficher l'historique
        container.classList.remove('hidden');
        if (icon) icon.textContent = '▼';
        
        // Toujours recharger quand on ouvre
        container.innerHTML = '<div class="loading">Chargement de l\'historique...</div>';
        try {
            const url = `/api/dashboard/recent?limit=10&rule_id=${ruleId}`;
            const analyses = await apiFetch(url);

            const header = `
                <div class="history-actions" style="margin-bottom: 0.5rem; display: flex; justify-content: flex-end;">
                    <button class="btn btn-secondary btn-sm" onclick="clearRuleHistory(${ruleId}, this)">🗑️ Effacer tout l'historique</button>
                </div>
            `;

            if (!analyses || analyses.length === 0) {
                container.innerHTML = header + '<div class="loading">Aucune analyse pour cette règle</div>';
                return;
            }

            container.innerHTML = header + analyses.map(a => `
                <div class="analysis-card inline-history-card">
                    <div class="analysis-header">
                        <div>
                            <span class="analysis-time">${a.analyzed_at ? formatDate(a.analyzed_at) : ''}</span>
                        </div>
                        <div class="analysis-actions">
                            <span class="severity-badge ${escapeHtml(a.severity)}">${escapeHtml(a.severity)}</span>
                            <button class="btn-icon" onclick="copyAnalysisText(this)" title="${window.t('common.copy_analysis')}">
                                <svg viewBox="0 0 24 24" width="14" height="14"><path fill="currentColor" d="M19,21H8V7H19M19,5H8A2,2 0 0,0 6,7V21A2,2 0 0,0 8,23H19A2,2 0 0,0 21,21V7A2,2 0 0,0 19,5M16,1H4A2,2 0 0,0 2,3V17H4V3H16V1Z" /></svg>
                            </button>
                            <button class="btn-icon delete-analysis-btn" onclick="deleteAnalysisInRules(${a.id}, ${ruleId}, this)" title="${window.t('common.delete_analysis')}">
                                <svg viewBox="0 0 24 24" width="14" height="14"><path fill="currentColor" d="M19,4H15.5L14.5,3H9.5L8.5,4H5V6H19V4M6,19A2,2 0 0,0 8,21H16A2,2 0 0,0 18,19V7H6V19Z" /></svg>
                            </button>
                        </div>
                    </div>
                    <div class="analysis-line">${escapeHtml(a.triggered_line || '')}</div>
                    <div class="analysis-response markdown-body">${a.ollama_response ? marked.parse(a.ollama_response) : ''}</div>
                </div>
            `).join('');
        } catch (e) {
            console.error('Erreur analyses:', e);
            container.innerHTML = `<div class="loading">${window.t ? window.t('common.error') : 'Erreur'}: ${escapeHtml(e.message || 'Impossible de charger l’historique')}</div>`;
        }
    } else {
        // Masquer l'historique
        container.classList.add('hidden');
        if (icon) icon.textContent = '▶';
    }
}

async function clearLiveLogs(ruleId) {
    const pre = document.getElementById(`live-log-pre-${ruleId}`);
    if (pre) pre.innerHTML = '';
}

async function copyLiveLogs(ruleId) {
    const pre = document.getElementById(`live-log-pre-${ruleId}`);
    if (!pre) return;
    try {
        await copyToClipboard(pre.innerText);
        alert('Logs copiés !');
    } catch (e) {
        console.error('Erreur copie logs:', e);
    }
}

function copyAnalysisText(btn) {
    const card = btn.closest('.analysis-card');
    if (!card) return;
    const line = card.querySelector('.analysis-line').innerText;
    const response = card.querySelector('.analysis-response').innerText;
    const text = `Ligne: ${line}\n\nAnalyse:\n${response}`;
    
    copyToClipboard(text).then(() => {
        const oldContent = btn.innerHTML;
        btn.innerHTML = '✅';
        setTimeout(() => { btn.innerHTML = oldContent; }, 2000);
    }).catch(err => {
        console.error('Erreur copie:', err);
    });
}

async function deleteAnalysisInRules(id, ruleId, btnElement) {
    showInlineConfirm(btnElement, 'Supprimer cette analyse ?', async () => {
        try {
            await apiFetch(`/api/dashboard/analyses/${id}`, { method: 'DELETE' });
            // Recharger l'historique (on referme/rouvre ou on appelle juste le contenu)
            const container = document.getElementById(`rule-history-${ruleId}`);
            container.classList.add('hidden'); // Hack simple pour forcer le re-toggle
            await toggleRuleHistory(ruleId);
        } catch (error) {
            console.error('Erreur suppression:', error);
            alert(window.t ? window.t('common.error') : 'Erreur lors de la suppression');
        }
    });
}

async function clearRuleHistory(ruleId, btnElement) {
    showInlineConfirm(btnElement, 'Effacer TOUT l\'historique d\'analyses pour cette règle ?', async () => {
        try {
            await apiFetch(`/api/dashboard/analyses/rule/${ruleId}`, { method: 'DELETE' });
            const container = document.getElementById(`rule-history-${ruleId}`);
            container.classList.add('hidden');
            await toggleRuleHistory(ruleId);
        } catch (error) {
            console.error('Erreur suppression:', error);
            alert(window.t ? window.t('common.error') : 'Erreur lors de la suppression');
        }
    });
}

async function testRule(id) {
    const btn = document.getElementById(`test-btn-${id}`);
    const originalText = btn ? btn.innerHTML : '🧪 Tester';
    
    const abortController = new AbortController();
    let stopBtn = null;
    
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '⏳ Test en cours...';
        btn.classList.add('pulse-animation');
        
        stopBtn = document.createElement('button');
        stopBtn.className = 'btn btn-danger btn-sm';
        stopBtn.style.marginLeft = '0.5rem';
        stopBtn.innerHTML = '🛑 Arrêter';
        stopBtn.onclick = () => abortController.abort();
        btn.parentNode.insertBefore(stopBtn, btn.nextSibling);
    }

    try {
        await apiFetch(`/api/rules/${id}/test`, { 
            method: 'POST',
            signal: abortController.signal
        });
        
        // S'assurer que le container est visible pour voir le résultat du test
        const container = document.getElementById(`rule-history-${id}`);
        if (container && container.classList.contains('hidden')) {
            await toggleRuleHistory(id);
        } else if (container) {
            // Si déjà ouvert, on referme et on rouvre pour recharger
            await toggleRuleHistory(id);
            await toggleRuleHistory(id);
        }

        if (btn) {
            btn.innerHTML = '✅ Terminé';
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
                btn.innerHTML = '❌ Annulé';
            } else {
                console.error('Erreur test règle:', e);
                btn.innerHTML = '❌ Erreur';
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
    document.getElementById('modal-title').textContent = 'Nouvelle règle';
    closeFileBrowser();
    
    const previewContainer = document.getElementById('file-preview-container');
    if (previewContainer) {
        previewContainer.classList.add('hidden');
        document.getElementById('file-preview-content').innerHTML = '';
    }
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

        list.innerHTML = rows.join('') || '<div class="loading">Dossier vide</div>';
    } catch (error) {
        console.error('Erreur browse:', error);
        list.innerHTML = `<div class="loading">${window.t ? window.t('common.error') : 'Erreur'}: ${escapeHtml(error.message || 'Impossible de lister ce dossier')}</div>`;
    }
}

function renderBrowserRow(entry, isParent) {
    const icon = entry.is_dir ? '📁' : '📄';
    const meta = entry.is_dir ? 'Dossier' : 'Fichier';
    const disabled = entry.readable === false ? 'opacity:0.6; pointer-events:none;' : '';
    const name = isParent ? entry.name : entry.name;
    return `
        <div class="file-browser-item" style="${disabled}" data-path="${escapeHtml(entry.path)}" data-is-dir="${entry.is_dir ? 'true' : 'false'}">
            <div class="left">
                <span>${icon}</span>
                <span class="name">${escapeHtml(name)}</span>
            </div>
            <div class="meta">${meta}${entry.readable === false ? ' • illisible' : ''}</div>
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
        document.getElementById('modal-title').textContent = 'Éditer la règle';
        document.getElementById('rule-modal').classList.remove('hidden');
        closeFileBrowser();
        fetchFilePreview(rule.log_file_path);
    } catch (error) {
        console.error('Erreur chargement règle:', error);
    }
}

async function deleteRule(id, btnElement) {
    showInlineConfirm(btnElement, 'Êtes-vous sûr de vouloir supprimer cette règle ?', async () => {
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
    content.innerHTML = '<em>Chargement de l\'aperçu...</em>';
    
    try {
        const res = await apiFetch(`/api/files/tail?path=${encodeURIComponent(path)}&lines=10`);
        if (res.lines && res.lines.length > 0) {
            content.innerHTML = res.lines.map(l => escapeHtml(typeof l === 'string' ? l : l.text)).join('<br>');
        } else {
            content.innerHTML = '<em>Fichier vide.</em>';
        }
        content.scrollTop = content.scrollHeight;
    } catch (e) {
        content.innerHTML = `<em style="color: var(--danger)">Aperçu non disponible : ${escapeHtml(e.message)}</em>`;
    }
}

function applyTemplate(type) {
    resetForm();
    const templates = {
        'auth': {
            name: 'Sécurité SSH / Connexions',
            path: '/system-logs/auth.log',
            keywords: 'failed, Accepted, invalid user, authentication failure, sudo',
            context: 'Surveillance des tentatives de connexion SSH et de l\'utilisation de sudo sur Ubuntu.'
        },
        'syslog': {
            name: 'Stabilité Système',
            path: '/system-logs/syslog',
            keywords: 'error, failed, fatal, critical, oom-killer, stopped',
            context: 'Journaux système généraux d\'Ubuntu. Surveille les plantages de services et les erreurs système.'
        },
        'journald': {
            name: 'Journald (Relais Docker)',
            path: '/logs/host_system_journal.log',
            keywords: 'error, fatal, panic, critical, failed',
            context: 'Relais des journaux binaires Systemd (journalctl) vers un fichier texte lisible.'
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
        document.getElementById('modal-title').textContent = 'Nouvelle règle (Modèle)';
        
        // Déclencher l'aperçu si possible
        fetchFilePreview(t.path);
    }
}

