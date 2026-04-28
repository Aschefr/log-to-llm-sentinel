// ─── Monitor Page ──────────────────────────────────────────────────────────
// Gestion des onglets, du live-tail avec colorisation, du buffer anti-spam
// et du panneau de détail au clic.

let monitorRules = [];
let monitorLogLines = 60;
let activeRuleId = null;
let tailIntervals = {};
let bufferIntervals = {};
let isFrozen = false;
let frozenContent = null;
let selectedLineText = null;
let activeKeywordFilter = null;
let autoOpenLine = false;

document.addEventListener('DOMContentLoaded', () => {
    loadMonitorRules();

    window.i18n?.onLanguageChange(() => {
        loadMonitorRules();
    });

    document.getElementById('monitor-search-btn').addEventListener('click', searchById);
    document.getElementById('monitor-search-id').addEventListener('keydown', e => {
        if (e.key === 'Enter') searchById();
    });

    // ─── Auto-recherche via query param ?search=<id> ou ?rule=<id> ──────────────────────
    const urlParams = new URLSearchParams(window.location.search);
    const searchParam = urlParams.get('search');
    const ruleParam = urlParams.get('rule');

    if (searchParam) {
        const searchInput = document.getElementById('monitor-search-id');
        if (searchInput) {
            searchInput.value = searchParam;
            setTimeout(() => searchById(), 300);
        }
    } else if (ruleParam) {
        // Le chargement sélectionnera cette règle si elle est valide
    }
});

// ─── Chargement des règles / onglets ───────────────────────────────────────

async function loadMonitorRules() {
    try {
        const res = await apiFetch('/api/monitor/rules');
        monitorRules = res.rules || [];
        monitorLogLines = res.monitor_log_lines || 60;
        
        renderTabs();
        renderTabs();
        if (monitorRules.length > 0) {
            const urlParams = new URLSearchParams(window.location.search);
            const ruleParam = urlParams.get('rule');
            const savedTab = sessionStorage.getItem('sentinel_monitor_tab');
            
            let ruleToSelect = monitorRules[0].id;
            if (ruleParam && monitorRules.find(r => r.id == ruleParam)) {
                ruleToSelect = parseInt(ruleParam);
            } else if (savedTab && monitorRules.find(r => r.id == savedTab)) {
                ruleToSelect = parseInt(savedTab);
            }
            
            selectTab(ruleToSelect);
        } else {
            document.getElementById('monitor-tab-content').innerHTML =
                `<div class="loading">${window.t ? window.t('monitor.no_active_rules') : 'No active rules.'} <a href="/rules">${window.t ? window.t('monitor.create_rule') : 'Create a rule'}</a></div>`;
        }
    } catch (e) {
        document.getElementById('monitor-tabs').innerHTML =
            `<div class="loading" style="color:var(--danger)">${window.t ? window.t('common.error') : 'Erreur'} : ${escapeHtml(e.message)}</div>`;
    }
}

function renderTabs() {
    const tabs = document.getElementById('monitor-tabs');
    tabs.innerHTML = monitorRules.map(r => `
        <button class="monitor-tab" id="tab-${r.id}" onclick="selectTab(${r.id})">
            ${escapeHtml(r.name)}
        </button>
    `).join('');
}

function selectTab(ruleId) {
    // Arrêter les anciens intervalles
    stopAllPolling();
    isFrozen = false;
    frozenContent = null;
    const urlParams = new URLSearchParams(window.location.search);
    const lineParam = urlParams.get('line');
    
    if (lineParam) {
        activeKeywordFilter = '__all__';
        selectedLineText = lineParam;
        autoOpenLine = true;
        
        // Nettoyer l'URL
        const newUrl = new URL(window.location.href);
        newUrl.searchParams.delete('line');
        window.history.replaceState({}, document.title, newUrl.toString());
    } else if (!activeKeywordFilter) {
        activeKeywordFilter = '__matches__'; // Par défaut
    }
    
    activeRuleId = ruleId;

    // Mettre à jour l'onglet actif
    document.querySelectorAll('.monitor-tab').forEach(t => t.classList.remove('active'));
    const activeTab = document.getElementById(`tab-${ruleId}`);
    if (activeTab) activeTab.classList.add('active');

    sessionStorage.setItem('sentinel_monitor_tab', ruleId);

    const rule = monitorRules.find(r => r.id === ruleId);
    if (!rule) return;

    renderTabContent(rule);
    startPolling(rule);
}

// ─── Rendu du contenu de l'onglet ─────────────────────────────────────────

function renderTabContent(rule) {
    const kwList = rule.keywords.join(', ') || window.t('common.na');
    document.getElementById('monitor-tab-content').innerHTML = `
        <!-- Paramètres de la règle -->
        <div class="monitor-rule-info">
            <div class="rule-info-grid">
                <div><span class="info-label">📁 ${window.t ? window.t('monitor.file_label') : 'File'}</span><code>${escapeHtml(rule.log_file_path)}</code></div>
                <div>
                    <span class="info-label">🔑 ${window.t('monitor.keywords')}</span>
                    <div class="kw-filter-badges" id="kw-filters-${rule.id}">
                        <span class="log-kw-badge kw-filter-btn ${activeKeywordFilter === '__matches__' ? 'active' : ''}" data-kw="__matches__" onclick="toggleKeywordFilter(this, ${rule.id})">Matches</span>
                        <span class="log-kw-badge kw-filter-btn ${activeKeywordFilter === '__all__' ? 'active' : ''}" data-kw="__all__" onclick="toggleKeywordFilter(this, ${rule.id})">${window.t ? window.t('monitor.all_filter') : 'All'}</span>
                        ${rule.keywords.map(kw =>
                            `<span class="log-kw-badge kw-filter-btn" data-kw="${encodeURIComponent(kw)}" onclick="toggleKeywordFilter(this, ${rule.id})">${escapeHtml(kw)}</span>`
                        ).join('')}
                        ${rule.excluded_patterns && rule.excluded_patterns.length > 0 ? `
                        <span class="log-kw-badge kw-filter-btn excl-filter-btn ${activeKeywordFilter === '__excluded__' ? 'active' : ''}" data-kw="__excluded__" onclick="toggleKeywordFilter(this, ${rule.id})" title="${window.t ? window.t('monitor.excluded_filter_title') : 'Show only excluded (filtered) lines'}">🚫 ${window.t ? window.t('monitor.excluded_filter') : 'Exclusions'}</span>` : ''}
                    </div>
                </div>
                <div><span class="info-label">⏱ Anti-spam</span>${rule.anti_spam_delay}s</div>
                <div><span class="info-label">🔔 ${window.t('monitor.severity')}</span>${rule.notify_severity_threshold}</div>
                <div>
                    <span class="info-label">📊 ${window.t ? window.t('monitor.statistics_filter') : 'Statistics (Filter)'}</span>
                    <div style="display:flex; gap:0.35rem; flex-wrap:wrap; margin-top:0.35rem;">
                        <span class="filter-badge" onclick="loadMonitorAnalyses(${rule.id}, null)" title="Total">Total: ${rule.stats?.total || 0}</span>
                        <span class="filter-badge critical" onclick="loadMonitorAnalyses(${rule.id}, 'critical')">${window.t('dashboard.critical')}: ${rule.stats?.critical || 0}</span>
                        <span class="filter-badge warning" onclick="loadMonitorAnalyses(${rule.id}, 'warning')">${window.t('dashboard.warning')}: ${rule.stats?.warning || 0}</span>
                        <span class="filter-badge info" onclick="loadMonitorAnalyses(${rule.id}, 'info')">${window.t('dashboard.info')}: ${rule.stats?.info || 0}</span>
                    </div>
                </div>
            </div>
        </div>

        <!-- Buffer anti-spam -->
        <div class="monitor-buffer-status" id="buffer-status-${rule.id}">
            <span class="buffer-dot idle" id="buffer-dot-${rule.id}"></span>
            <span id="buffer-label-${rule.id}">${window.t ? window.t('monitor.buffer_inactive') : 'Inactive buffer'}</span>
        </div>

        <!-- Visionneuse de logs -->
        <div class="monitor-viewer-header" onclick="toggleLogViewer(${rule.id})">
            <span class="viewer-title">
                <span class="viewer-toggle-icon">▼</span>
                📄 ${window.t ? window.t('monitor.live_log') : 'Live log'}
                <span class="viewer-linecount" id="linecount-${rule.id}"></span>
                <span class="kw-filter-label hidden" id="kw-filter-label-${rule.id}"></span>
            </span>
            <div class="viewer-actions" onclick="event.stopPropagation()">
                <button class="btn btn-secondary btn-sm" id="freeze-btn-${rule.id}" onclick="toggleFreeze(${rule.id})">${window.t ? window.t('monitor.freeze') : '❄️ Freeze'}</button>
                <button class="btn btn-secondary btn-sm" onclick="copyViewerContent(${rule.id})">${window.t ? window.t('common.copy') : 'Copy'}</button>
            </div>
        </div>
        <div class="monitor-log-viewer" id="log-viewer-${rule.id}">
            <div class="loading">${window.t ? window.t('common.loading') : 'Loading...'}</div>
        </div>

        <!-- Panneau de détail -->
        <div class="monitor-detail-panel hidden" id="detail-panel-${rule.id}">
            <div class="detail-panel-header">
                <strong>🔍 ${window.t ? window.t('monitor.detail_line_text') : 'Line Details'}</strong>
                <button class="btn-icon" onclick="closeDetailPanel(${rule.id})">✕</button>
            </div>
            <div id="detail-panel-content-${rule.id}"></div>
        </div>

        <!-- Analyses récentes -->
        <div class="monitor-analyses-header" onclick="toggleAnalysesSection(${rule.id})">
            <span class="viewer-title">
                <span class="viewer-toggle-icon" id="analyses-toggle-icon-${rule.id}">▼</span>
                <strong>📊 ${window.t ? window.t('monitor.recent_analyses_llm') : 'Recent Analyses (LLM)'}</strong>
            </span>
        </div>
        <div id="monitor-analyses-${rule.id}" class="monitor-analyses-list">
            <div class="loading">${window.t ? window.t('monitor.loading_analyses') : 'Loading...'}</div>
        </div>
    `;

    // Charger les analyses immédiatement
    loadMonitorAnalyses(rule.id);
}

// ─── Polling ───────────────────────────────────────────────────────────────

function startPolling(rule) {
    const fetchAndRender = async () => {
        await fetchLogs(rule);
    };
    const fetchBuffer = async () => {
        await fetchBufferStatus(rule.id);
    };

    fetchAndRender();
    fetchBuffer();
    tailIntervals[rule.id] = setInterval(fetchAndRender, 3000);
    bufferIntervals[rule.id] = setInterval(fetchBuffer, 2000);
}

function toggleLogViewer(ruleId) {
    const viewer = document.getElementById(`log-viewer-${ruleId}`);
    if (!viewer) return;
    
    const isHidden = viewer.classList.toggle('hidden');
    const header = viewer.previousElementSibling;
    if (header) {
        header.classList.toggle('collapsed', isHidden);
    }
}

function toggleAnalysesSection(ruleId) {
    const section = document.getElementById(`monitor-analyses-${ruleId}`);
    if (!section) return;
    
    const isHidden = section.classList.toggle('hidden');
    const header = section.previousElementSibling;
    if (header) {
        header.classList.toggle('collapsed', isHidden);
    }
}

function stopAllPolling() {
    Object.values(tailIntervals).forEach(clearInterval);
    Object.values(bufferIntervals).forEach(clearInterval);
    tailIntervals = {};
    bufferIntervals = {};
}

// ─── Fetch & Rendu des logs ────────────────────────────────────────────────

async function fetchLogs(rule) {
    if (isFrozen) return;

    const viewer = document.getElementById(`log-viewer-${rule.id}`);
    if (!viewer) return;

    try {
        const kwParam = rule.keywords.join(',');
        let res;
        
        if (rule.log_file_path && rule.log_file_path.startsWith('[WEBHOOK]:')) {
            // Webhook rule → use in-memory ring buffer tail
            const token = rule.log_file_path.split(':')[1];
            res = await apiFetch(
                `/api/webhook/tail/${encodeURIComponent(token)}?lines=${monitorLogLines}&keywords=${encodeURIComponent(kwParam)}`
            );
        } else {
            // File-based rule → read from disk
            res = await apiFetch(
                `/api/files/tail?path=${encodeURIComponent(rule.log_file_path)}&lines=${monitorLogLines}&keywords=${encodeURIComponent(kwParam)}`
            );
        }

        if (!res.lines || res.lines.length === 0) {
            viewer.innerHTML = `<em class="no-logs">${window.t ? window.t('monitor.file_empty') : 'File empty or inaccessible.'}</em>`;
            return;
        }

        const isAtBottom = Math.abs((viewer.scrollHeight - viewer.scrollTop) - viewer.clientHeight) < 20;

        const excludedPatterns = rule.excluded_patterns || [];

        viewer.innerHTML = res.lines.map((line, idx) => {
            const rawText = line.text || '';
            const text = escapeHtml(rawText);
            const isSelected = selectedLineText === rawText;
            const matchClass = line.matched ? 'matched' : '';
            const selectClass = isSelected ? 'selected' : '';

            // Client-side exclusion detection
            const isExcluded = excludedPatterns.length > 0
                && excludedPatterns.some(pat => rawText.toLowerCase().includes(pat.toLowerCase()));
            const excludedClass = isExcluded ? 'log-line-excluded' : '';

            const kwBadges = line.matched_keywords && line.matched_keywords.length > 0
                ? `<span class="log-kw-badges">${line.matched_keywords.map(k => `<span class="log-kw-badge">${escapeHtml(k)}</span>`).join('')}</span>`
                : '';

            const exclBadge = isExcluded
                ? `<span class="log-kw-badges"><span class="log-excl-badge">🚫 ${excludedPatterns.find(p => rawText.toLowerCase().includes(p.toLowerCase())) || 'exclu'}</span></span>`
                : '';

            return `<div class="log-line ${matchClass} ${selectClass} ${excludedClass}" data-rule="${rule.id}" data-idx="${idx}" data-excluded="${isExcluded}" data-text="${encodeURIComponent(rawText)}" onclick="onLineClick(this, ${rule.id})">
                <span class="log-text">${text}</span>${kwBadges}${exclBadge}
            </div>`;
        }).join('');

        // Mettre à jour le compteur
        const matched = res.lines.filter(l => l.matched).length;
        const lc = document.getElementById(`linecount-${rule.id}`);
        if (lc) lc.textContent = `(${res.lines.length} ${window.t ? window.t('monitor.lines_label') : 'lines'}, ${matched} ${window.t ? window.t('monitor.matched_label') : 'matched'})`;

        if (autoOpenLine && selectedLineText) {
            const selectedEl = viewer.querySelector('.log-line.selected');
            if (selectedEl) {
                selectedEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
                // Ouvrir le panneau de détail si pas déjà ouvert
                const panel = document.getElementById(`detail-panel-${rule.id}`);
                if (panel && panel.classList.contains('hidden')) {
                    onLineClick(selectedEl, rule.id);
                }
                autoOpenLine = false; // Ne le faire qu'une fois
            }
        } else if (isAtBottom) {
            viewer.scrollTop = viewer.scrollHeight;
        }

        // Réappliquer le filtre mot-clé actif
        applyKeywordFilter(rule.id);
        updateFilterLabel(rule.id);
        
        // S'assurer que les badges reflètent bien l'état actif
        document.querySelectorAll('.kw-filter-btn').forEach(b => {
            const bKw = decodeURIComponent(b.dataset.kw || '');
            b.classList.toggle('active', bKw === activeKeywordFilter);
        });

    } catch (e) {
        viewer.innerHTML = `<em class="no-logs" style="color:var(--danger)">${window.t ? window.t('common.error') : 'Erreur'} : ${escapeHtml(e.message)}</em>`;
    }
}

// ─── Filtre par mot-clé ────────────────────────────────────────────────────

function toggleKeywordFilter(badgeEl, ruleId) {
    const kw = decodeURIComponent(badgeEl.dataset.kw || '');
    if (!kw) return;

    if (activeKeywordFilter === kw && kw !== '__matches__') {
        activeKeywordFilter = '__matches__';
    } else {
        activeKeywordFilter = kw;
    }

    document.querySelectorAll('.kw-filter-btn').forEach(b => {
        const bKw = decodeURIComponent(b.dataset.kw || '');
        b.classList.toggle('active', bKw === activeKeywordFilter);
    });

    applyKeywordFilter(ruleId);
    updateFilterLabel(ruleId);

    // Auto-scroll si "Tout voir"
    if (activeKeywordFilter === '__all__') {
        const viewer = document.getElementById(`log-viewer-${ruleId}`);
        if (viewer) viewer.scrollTop = viewer.scrollHeight;
    }
}

function applyKeywordFilter(ruleId) {
    const viewer = document.getElementById(`log-viewer-${ruleId}`);
    if (!viewer) return;

    let visibleCount = 0;
    viewer.querySelectorAll('.log-line').forEach(line => {
        let show = false;
        if (activeKeywordFilter === '__all__') {
            show = true;
        } else if (activeKeywordFilter === '__excluded__') {
            show = line.dataset.excluded === 'true';
        } else if (activeKeywordFilter === '__matches__') {
            show = line.querySelector('.log-kw-badge') !== null && line.dataset.excluded !== 'true';
        } else {
            const badges = Array.from(line.querySelectorAll('.log-kw-badge'))
                .map(b => b.textContent.trim().toLowerCase());
            show = badges.includes(activeKeywordFilter.toLowerCase());
        }
        line.style.display = show ? '' : 'none';
        if (show) visibleCount++;
    });

    // Encart vide si aucune ligne ne correspond au filtre actif
    const emptyId = `kw-empty-${ruleId}`;
    let emptyEl = viewer.querySelector(`#${emptyId}`);
    if (visibleCount === 0 && viewer.querySelectorAll('.log-line').length > 0) {
        if (!emptyEl) {
            emptyEl = document.createElement('em');
            emptyEl.id = emptyId;
            emptyEl.className = 'no-logs';
            viewer.appendChild(emptyEl);
        }
        const filterName = activeKeywordFilter === '__matches__' ? 'matches' :
                           activeKeywordFilter === '__all__'    ? 'tout' : `"${activeKeywordFilter}"`;
        emptyEl.textContent = window.t ? window.t('monitor.no_logs_filter') : `No lines to display for filter: ${filterName}`;
    } else if (emptyEl) {
        emptyEl.remove();
    }
}

function updateFilterLabel(ruleId) {
    const label = document.getElementById(`kw-filter-label-${ruleId}`);
    if (!label) return;
    
    if (activeKeywordFilter === '__matches__') {
        label.textContent = ` — ${window.t ? window.t('monitor.view_matches_only') : 'view: matches only'}`;
        label.classList.remove('hidden');
    } else if (activeKeywordFilter === '__all__') {
        label.textContent = ` — ${window.t ? window.t('monitor.view_full_log') : 'view: full log'}`;
        label.classList.remove('hidden');
    } else if (activeKeywordFilter) {
        label.textContent = ` — ${window.t ? window.t('monitor.filter_keyword') : 'filter:'} "${activeKeywordFilter}"`;
        label.classList.remove('hidden');
    } else {
        label.textContent = '';
        label.classList.add('hidden');
    }
}


async function fetchBufferStatus(ruleId) {
    try {
        const buf = await apiFetch(`/api/monitor/buffer/${ruleId}`);
        const dot = document.getElementById(`buffer-dot-${ruleId}`);
        const label = document.getElementById(`buffer-label-${ruleId}`);
        if (!dot || !label) return;

        if (buf.active) {
            dot.className = 'buffer-dot active';
            const kwStr = buf.matched_keywords.length > 0
                ? ` — ${window.t ? window.t('monitor.buffer_keywords') : 'keywords'}: <strong>${buf.matched_keywords.map(k => escapeHtml(k)).join(', ')}</strong>`
                : '';
            label.innerHTML = `⏳ ${window.t ? window.t('monitor.buffer_active') : 'Active buffer'} <span class="detection-id-badge" style="font-size: 0.7rem; vertical-align: middle;">#${escapeHtml(buf.detection_id || '...')}</span> — ${buf.line_count} ${window.t ? window.t('monitor.buffer_lines_pending') : 'line(s) pending'}${kwStr}`;
        } else {
            dot.className = 'buffer-dot idle';
            label.textContent = window.t ? window.t('monitor.buffer_inactive') : 'Inactive buffer';
        }
    } catch (e) {}
}

// ─── Clic sur une ligne ────────────────────────────────────────────────────

async function onLineClick(el, ruleId) {
    const text = decodeURIComponent(el.dataset.text || '');
    const panel = document.getElementById(`detail-panel-${ruleId}`);
    const content = document.getElementById(`detail-panel-content-${ruleId}`);
    const viewer = document.getElementById(`log-viewer-${ruleId}`);
    if (!panel || !content) return;

    panel.classList.remove('hidden');

    // Nettoyage sélections précédentes
    document.querySelectorAll('.log-line.selected, .log-line.bundle-selected').forEach(l => {
        l.classList.remove('selected');
        l.classList.remove('bundle-selected');
    });
    el.classList.add('selected');
    selectedLineText = text;

    // Chercher une analyse correspondant à cette ligne
    let relatedAnalysis = null;
    try {
        const analyses = await apiFetch(`/api/monitor/analyses/${ruleId}`);
        relatedAnalysis = analyses.find(a =>
            a.triggered_line && a.triggered_line.includes(text.substring(0, 80))
        );
    } catch (e) {}

    // Si analyse trouvée, mettre en évidence le bundle si nécessaire
    if (relatedAnalysis && viewer) {
        viewer.querySelectorAll('.log-line').forEach(line => {
            const lText = decodeURIComponent(line.dataset.text || '');
            if (lText && relatedAnalysis.triggered_line.includes(lText.substring(0, 60))) {
                line.classList.add('bundle-selected');
            }
        });
    }

    const matchedKws = el.querySelectorAll('.log-kw-badge');
    const kwList = matchedKws.length > 0
        ? Array.from(matchedKws).map(b => b.textContent).join(', ')
        : window.t ? window.t('monitor.detail_none_matched') : 'None (unmatched line)';

    content.innerHTML = `
        <div class="detail-row">
            <span class="detail-label">${window.t ? window.t('monitor.detail_line_text') : 'Line text'}</span>
            <code class="detail-value">${escapeHtml(text)}</code>
        </div>
        <div class="detail-row">
            <span class="detail-label">${window.t ? window.t('monitor.detail_detected_kw') : 'Detected keywords'}</span>
            <span class="detail-value">${matchedKws.length > 0 ? kwList : `<em>${window.t ? window.t('monitor.detail_none_filtered') : 'None (unfiltered line)'}</em>`}</span>
        </div>
        ${relatedAnalysis ? `
        <div class="detail-row">
            <span class="detail-label">${window.t ? window.t('monitor.detection_id') : 'Detection ID'}</span>
            <code class="detail-value detection-id-badge">#${escapeHtml(relatedAnalysis.detection_id || 'N/A')}</code>
        </div>
        <div class="detail-row">
            <span class="detail-label">${window.t ? window.t('monitor.severity') : 'Severity'}</span>
            <span class="severity-badge ${escapeHtml(relatedAnalysis.severity)}">${escapeHtml(relatedAnalysis.severity)}</span>
        </div>
        <div class="detail-row">
            <span class="detail-label">${window.t ? window.t('monitor.analyzed_at') : 'Analyzed at'}</span>
            <span class="detail-value">${relatedAnalysis.analyzed_at ? formatDate(relatedAnalysis.analyzed_at) : '—'}</span>
        </div>
        <div class="detail-row">
            <span class="detail-label">${window.t ? window.t('monitor.llm_response') : 'LLM Response'}</span>
            <div class="detail-value analysis-response markdown-body">${relatedAnalysis.ollama_response ? marked.parse(relatedAnalysis.ollama_response) : '—'}</div>
        </div>
        <div class="detail-actions" style="margin-top: 0.75rem; border-top: 1px solid var(--border); padding-top: 0.5rem; display: flex; justify-content: space-between; align-items: center;">
            <div style="display: flex; gap: 0.5rem;">
                <button class="btn btn-secondary btn-sm" onclick="retryAnalysis(${relatedAnalysis.id}, this)">${window.t('monitor.retry_analysis')}</button>
                <button class="btn btn-secondary btn-sm" onclick="notifyAnalysis(${relatedAnalysis.id}, this)">${window.t('monitor.notify')}</button>
            </div>
            <button class="btn btn-primary btn-sm" onclick="openChat(${relatedAnalysis.id})">${window.t('monitor.deepen_with_ai')}</button>
        </div>
        ` : `
        <div class="detail-row"><em>${window.t ? window.t('monitor.no_auto_analysis') : 'No automatic analysis found for this line.'}</em></div>
        <div class="detail-actions" style="margin-top: 1rem; border-top: 1px solid var(--border); padding-top: 0.75rem;">
            <button class="btn btn-primary btn-sm" onclick="manualAnalyze(${ruleId}, this)">${window.t('monitor.analyze_with_ollama')}</button>
        </div>
        `}
    `;
}

function closeDetailPanel(ruleId) {
    const panel = document.getElementById(`detail-panel-${ruleId}`);
    if (panel) panel.classList.add('hidden');
    document.querySelectorAll('.log-line.selected, .log-line.bundle-selected').forEach(l => {
        l.classList.remove('selected');
        l.classList.remove('bundle-selected');
    });
    selectedLineText = null;
}

// ─── Figer / copier ────────────────────────────────────────────────────────

function toggleFreeze(ruleId) {
    isFrozen = !isFrozen;
    const btn = document.getElementById(`freeze-btn-${ruleId}`);
    if (btn) {
        btn.textContent = isFrozen ? (window.t ? window.t('monitor.resume') : '▶️ Resume') : (window.t ? window.t('monitor.freeze') : '❄️ Freeze');
        btn.classList.toggle('active', isFrozen);
    }
}

function copyViewerContent(ruleId) {
    const viewer = document.getElementById(`log-viewer-${ruleId}`);
    if (!viewer) return;
    const text = Array.from(viewer.querySelectorAll('.log-line'))
        .map(el => el.querySelector('.log-text')?.textContent || '')
        .join('\n');
    copyToClipboard(text).then(() => alert(window.t ? window.t('monitor.logs_copied') : 'Logs copied!')).catch(() => {});
}

// ─── Analyses récentes ─────────────────────────────────────────────────────

async function loadMonitorAnalyses(ruleId, severityFilter = null) {
    const container = document.getElementById(`monitor-analyses-${ruleId}`);
    if (!container) return;

    // Update active badge state
    const tabContent = document.getElementById('monitor-tab-content');
    const allBadges = tabContent ? tabContent.querySelectorAll('.filter-badge') : [];
    allBadges.forEach(b => b.classList.remove('active'));
    const activeBadge = Array.from(allBadges).find(b => {
        const oc = b.getAttribute('onclick') || '';
        return severityFilter === null ? oc.includes(', null)') : oc.includes(`'${severityFilter}'`);
    });
    if (activeBadge) activeBadge.classList.add('active');

    try {
        let url = `/api/dashboard/recent?limit=20&rule_id=${ruleId}`;
        if (severityFilter) {
            url += `&severity=${severityFilter}`;
        }
        
        const analyses = await apiFetch(url);
        if (analyses.length === 0) {
            container.innerHTML = `<div class="no-logs">${window.t ? window.t('monitor.no_recent_analyses') : 'No recent analyses'}${severityFilter ? ` (${severityFilter})` : ''}</div>`;
            return;
        }

        container.innerHTML = analyses.map(a => `
            <div class="monitor-analysis-card">
                <div class="monitor-analysis-header">
                    <span class="detection-id-badge" title="${window.t('monitor.detection_id_title')}">${escapeHtml(a.detection_id || '—')}</span>
                    <span class="analysis-time">${a.analyzed_at ? formatDate(a.analyzed_at) : ''}</span>
                    <span class="severity-badge ${escapeHtml(a.severity)}">${escapeHtml(a.severity)}</span>
                </div>
                <div class="monitor-analysis-keywords">
                    ${window.t ? window.t('monitor.kw_label') : 'Keywords:'} ${a.matched_keywords.length > 0
                        ? a.matched_keywords.map(k => `<span class="log-kw-badge">${escapeHtml(k)}</span>`).join(' ')
                        : '<em>N/A</em>'}
                </div>
                <div class="analysis-line">${highlightKeywords(a.triggered_line || '', a.matched_keywords || [])}</div>
                <div class="analysis-response markdown-body">${a.ollama_response ? marked.parse(a.ollama_response) : ''}</div>
                <div class="detail-actions" style="margin-top: 0.75rem; border-top: 1px solid var(--border); padding-top: 0.5rem; display: flex; justify-content: space-between; align-items: center;">
                    <div style="display: flex; gap: 0.5rem;">
                        <button class="btn btn-secondary btn-sm" onclick="retryAnalysis(${a.id}, this)">${window.t('monitor.retry')}</button>
                        <button class="btn btn-secondary btn-sm" onclick="notifyAnalysis(${a.id}, this)">${window.t('monitor.notify')}</button>
                    </div>
                    <button class="btn btn-primary btn-sm" onclick="openChat(${a.id})">${window.t('monitor.deepen_with_ai')}</button>
                </div>
            </div>
        `).join('');
    } catch (e) {
        container.innerHTML = `<div class="loading" style="color:var(--danger)">${window.t ? window.t('common.error') : 'Erreur'} : ${escapeHtml(e.message)}</div>`;
    }
}

async function retryAnalysis(analysisId, btn) {
    const oldHtml = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `🔄 ${window.t('common.analyzing')}`;

    try {
        const res = await apiFetch(`/api/monitor/retry/${analysisId}`, { method: 'POST' });
        if (!res.task_id) throw new Error(window.t ? window.t('monitor.unexpected_server_response') : 'Unexpected server response');

        pollTask(res.task_id, btn, oldHtml, () => {
            // Rafraîchir le panneau de détail si une ligne est sélectionnée
            const selected = document.querySelector('.log-line.selected');
            if (selected) onLineClick(selected, activeRuleId);
            if (activeRuleId) loadRuleAnalyses(activeRuleId);
            const searchInput = document.getElementById('monitor-search-id');
            if (searchInput && searchInput.value) searchById();
        });
    } catch (e) {
        alert(window.t('common.error') + ': ' + e.message);
        btn.disabled = false;
        btn.innerHTML = oldHtml;
    }
}

/**
 * Polling d'une tâche d'analyse en arrière-plan.
 * Appelle GET /api/monitor/task/{taskId} toutes les 2s jusqu'à completion.
 * @param {string} taskId
 * @param {HTMLElement} btn - bouton à restaurer après
 * @param {string} originalHtml - innerHTML original du bouton
 * @param {function} onDone - callback appelé quand status === 'done'
 */
function pollTask(taskId, btn, originalHtml, onDone) {
    const maxAttempts = 150; // 5 minutes max
    let attempts = 0;

    const interval = setInterval(async () => {
        attempts++;
        if (attempts > maxAttempts) {
            clearInterval(interval);
            btn.innerHTML = '⏰ Timeout';
            setTimeout(() => { btn.innerHTML = originalHtml; btn.disabled = false; }, 3000);
            return;
        }
        try {
            const res = await apiFetch(`/api/monitor/task/${taskId}`);
            if (res.status === 'done') {
                clearInterval(interval);
                btn.innerHTML = '✅';
                setTimeout(() => { btn.innerHTML = originalHtml; btn.disabled = false; }, 2000);
                if (onDone) onDone(res.analysis_id);
            } else if (res.status === 'error') {
                clearInterval(interval);
                btn.innerHTML = '❌';
                setTimeout(() => { btn.innerHTML = originalHtml; btn.disabled = false; }, 3000);
                alert(window.t('common.error') + ': ' + (res.error || (window.t ? window.t('monitor.unknown_error') : 'Unknown error')));
            }
            // status === 'running' → on continue le polling
        } catch (_) { /* erreur réseau transitoire — on réessaie */ }
    }, 2000);
}

// ─── Recherche par ID ──────────────────────────────────────────────────────

async function searchById() {
    const input = document.getElementById('monitor-search-id');
    const resultPanel = document.getElementById('search-result');
    const id = input.value.trim();
    if (!id) return;

    resultPanel.classList.remove('hidden');
    resultPanel.innerHTML = `<div class="loading">${window.t ? window.t('monitor.search_in_progress') : 'Searching...'}</div>`;

    try {
        const res = await apiFetch(`/api/monitor/search?id=${encodeURIComponent(id)}`);
        if (!res.found) {
            resultPanel.innerHTML = `<div class="search-result-empty">${window.t ? window.t('monitor.no_analysis_found_id') : 'No analysis found for ID'} <code>${escapeHtml(id)}</code>.</div>`;
            return;
        }
        const a = res.analysis;
        resultPanel.innerHTML = `
            <div class="search-result-card">
                <div class="search-result-close" onclick="document.getElementById('search-result').classList.add('hidden')">✕</div>
                <h3>${window.t ? window.t('monitor.result_for') : 'Result for'} <code class="detection-id-badge">${escapeHtml(a.detection_id)}</code></h3>
                <div class="detail-row"><span class="detail-label">${window.t ? window.t('monitor.rule_label') : 'Rule'}</span><span class="detail-value">${escapeHtml(a.rule_name)}</span></div>
                <div class="detail-row"><span class="detail-label">${window.t ? window.t('monitor.analyzed_at') : 'Analyzed at'}</span><span class="detail-value">${a.analyzed_at ? formatDate(a.analyzed_at) : '—'}</span></div>
                <div class="detail-row"><span class="detail-label">${window.t ? window.t('monitor.severity') : 'Severity'}</span><span class="severity-badge ${escapeHtml(a.severity)}">${escapeHtml(a.severity)}</span></div>
                <div class="detail-row"><span class="detail-label">${window.t ? window.t('monitor.keywords') : 'Keywords'}</span><span class="detail-value">${a.matched_keywords.map(k => `<span class="log-kw-badge">${escapeHtml(k)}</span>`).join(' ') || '—'}</span></div>
                <div class="detail-row"><span class="detail-label">${window.t ? window.t('monitor.detail_line_label') : 'Line'}</span><code class="detail-value">${escapeHtml(a.triggered_line || '')}</code></div>
                <div class="detail-row"><span class="detail-label">${window.t ? window.t('monitor.detail_llm_analysis') : 'LLM Analysis'}</span>
                    <div class="detail-value analysis-response markdown-body">${a.ollama_response ? marked.parse(a.ollama_response) : '—'}</div>
                </div>

                <div class="detail-row"><span class="detail-label">${window.t ? window.t('monitor.notified') : 'Notified'}</span><span class="detail-value">${a.notified ? window.t('monitor.yes') : window.t('monitor.no')}</span></div>
                <div class="detail-actions" style="margin-top: 0.75rem; border-top: 1px solid var(--border); padding-top: 0.5rem; display: flex; justify-content: space-between; align-items: center;">
                    <div style="display: flex; gap: 0.5rem;">
                        <button class="btn btn-secondary btn-sm" onclick="retryAnalysis(${a.id}, this)">${window.t('monitor.retry_analysis')}</button>
                        <button class="btn btn-secondary btn-sm" onclick="notifyAnalysis(${a.id}, this)">${window.t('monitor.notify')}</button>
                    </div>
                    <button class="btn btn-primary btn-sm" onclick="openChat(${a.id})">${window.t('monitor.deepen_with_ai')}</button>
                </div>
            </div>
        `;
    } catch (e) {
        resultPanel.innerHTML = `<div class="loading" style="color:var(--danger)">${window.t ? window.t('common.error') : 'Erreur'} : ${escapeHtml(e.message)}</div>`;
    }
}

async function manualAnalyze(ruleId, btn) {
    if (!selectedLineText) return;
    const oldHtml = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `⏳ ${window.t('common.analyzing')}`;

    try {
        const res = await apiFetch('/api/monitor/analyze-line', {
            method: 'POST',
            body: { line: selectedLineText, rule_id: ruleId }
        });
        if (!res.task_id) throw new Error(window.t ? window.t('monitor.unexpected_server_response') : 'Unexpected server response');

        pollTask(res.task_id, btn, oldHtml, (analysisId) => {
            if (analysisId) {
                // Charger et afficher le résultat dans le panneau de détail
                apiFetch(`/api/monitor/analysis/${analysisId}`).then(data => {
                    const a = data.analysis;
                    if (!a) return;
                    const content = document.getElementById(`detail-panel-content-${ruleId}`);
                    if (content) {
                        content.innerHTML += `
                            <div class="manual-analysis-result" style="margin-top: 1.5rem; border-top: 2px dashed var(--accent); padding-top: 1rem;">
                                <div class="detail-row">
                                    <span class="detail-label">${window.t('monitor.manual_analysis')}</span>
                                    <span class="severity-badge ${a.severity}">${a.severity}</span>
                                </div>
                                <div class="analysis-response markdown-body">${marked.parse(a.ollama_response)}</div>
                                <div class="detail-actions" style="margin-top: 1rem; border-top: 1px solid var(--border); padding-top: 0.75rem; display: flex; justify-content: flex-end;">
                                    <button class="btn btn-primary btn-sm" onclick="openChat(${a.id})">${window.t('monitor.deepen_with_ai')}</button>
                                </div>
                            </div>
                        `;
                    }
                }).catch(() => {});
            }
        });
    } catch (e) {
        alert(window.t('common.error') + ': ' + e.message);
        btn.disabled = false;
        btn.innerHTML = oldHtml;
    }
}

async function notifyAnalysis(analysisId, btn) {
    const oldHtml = btn.innerHTML;
    try {
        btn.innerHTML = window.t('common.sending');
        btn.disabled = true;

        const res = await apiFetch(`/api/monitor/notify/${analysisId}`, {
            method: 'POST'
        });

        if (res.status === 'ok') {
            btn.innerHTML = window.t('common.sent');
            setTimeout(() => {
                btn.innerHTML = oldHtml;
                btn.disabled = false;
            }, 2000);
        } else {
            alert(window.t('common.error') + ": " + res.detail);
            btn.innerHTML = oldHtml;
            btn.disabled = false;
        }
    } catch (e) {
        alert(window.t('common.error') + ": " + e.message);
        btn.innerHTML = oldHtml;
        btn.disabled = false;
    }
}
