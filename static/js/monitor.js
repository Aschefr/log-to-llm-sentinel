// ─── Monitor Page ──────────────────────────────────────────────────────────
// Gestion des onglets, du live-tail avec colorisation, du buffer anti-spam
// et du panneau de détail au clic.

let monitorRules = [];
let monitorLogLines = 60;
let activeRuleId = null;
let tailIntervals = {};
let bufferIntervals = {};
let analysisIntervals = {};
let isFrozen = false;
let frozenContent = null;
let selectedLineText = null;
let activeKeywordFilter = null;
let autoOpenLine = false;
let monitorAnalysesOffset = 0;
let monitorAnalysesSeverity = null;

document.addEventListener('DOMContentLoaded', () => {
    // Setup shared rule modal (MON-09)
    setupRuleModal({ onSave: loadMonitorRules });

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
                `<div class="loading">${window.t ? window.t('monitor.no_active_rules') : 'No active rules.'} <a href="javascript:void(0)" onclick="openAddRuleModal()">${window.t ? window.t('monitor.create_rule') : 'Create a rule'}</a></div>`;
        }
    } catch (e) {
        document.getElementById('monitor-tabs').innerHTML =
            `<div class="loading" style="color:var(--danger)">${window.t ? window.t('common.error') : 'Erreur'} : ${escapeHtml(e.message)}</div>`;
    }
}

function renderTabs() {
    const tabs = document.getElementById('monitor-tabs');
    if (!tabs) return;
    tabs.innerHTML = monitorRules.map(r => {
        const stats = r.stats || { critical: 0, warning: 0, info: 0 };
        const unviewed = r.unviewed_count || 0;
        
        let badgesHtml = '';
        if (stats.critical > 0 || stats.warning > 0 || stats.info > 0) {
            badgesHtml = `
                <span class="tab-badges">
                    ${stats.critical > 0 ? `<span class="tab-badge critical" title="${window.t ? window.t('dashboard.critical') : 'Critical'}: ${stats.critical}">${stats.critical}</span>` : ''}
                    ${stats.warning > 0 ? `<span class="tab-badge warning" title="${window.t ? window.t('dashboard.warning') : 'Warning'}: ${stats.warning}">${stats.warning}</span>` : ''}
                    ${stats.info > 0 ? `<span class="tab-badge info" title="${window.t ? window.t('dashboard.info') : 'Info'}: ${stats.info}">${stats.info}</span>` : ''}
                </span>
            `;
        }

        const alertStatus = r.alert_status || 'normal';
        let statusTitle = window.t ? window.t('monitor.status_normal') : 'Normal';
        if (alertStatus === 'alert') {
            statusTitle = window.t ? window.t('monitor.status_alert') : 'In Alert';
        } else if (alertStatus === 'resolving') {
            statusTitle = window.t ? window.t('monitor.status_resolving') : 'Resolving';
        }

        const isActive = r.id === activeRuleId ? ' active' : '';
        return `
            <button class="monitor-tab${isActive}" id="tab-${r.id}" onclick="selectTab(${r.id})" title="${statusTitle}">
                <span class="rule-status-dot status-${alertStatus}"></span>
                <span class="tab-name">${escapeHtml(r.name)}</span>
                ${badgesHtml}
            </button>
        `;
    }).join('') + `
        <button class="monitor-tab monitor-tab-add" onclick="openAddRuleModal()" title="${window.t ? window.t('monitor.add_rule') : 'Add rule'}">
            +
        </button>
    `;
}


function openAddRuleModal() {
    resetForm();
    document.getElementById('rule-modal').classList.remove('hidden');
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

async function deleteRule(ruleId, btnElement) {
    showInlineConfirm(btnElement, window.t ? window.t('monitor.delete_rule_confirm') : 'Are you sure you want to delete this rule?', async () => {
        try {
            await apiFetch(`/api/rules/${ruleId}`, { method: 'DELETE' });
            // After deletion, select the first available rule or none
            sessionStorage.removeItem('sentinel_monitor_tab');
            activeRuleId = null;
            loadMonitorRules();
        } catch (e) {
            alert((window.t ? window.t('common.error') : 'Error') + ': ' + e.message);
        }
    });
}

// Global deleteAnalysis for Monitor page (used by renderAnalysisCard)
async function deleteAnalysis(id, btnElement) {
    showInlineConfirm(btnElement, window.t ? window.t('common.confirm_delete_analysis') : 'Are you sure you want to delete this analysis?', async () => {
        try {
            await apiFetch(`/api/dashboard/analyses/${id}`, { method: 'DELETE' });
            const card = document.getElementById(`analysis-card-${id}`);
            if (card) card.remove();
        } catch (e) {
            alert((window.t ? window.t('common.error') : 'Error') + ': ' + e.message);
        }
    });
}

// ─── Rendu du contenu de l'onglet ─────────────────────────────────────────

function renderTabContent(rule) {
    monitorAnalysesOffset = 0;
    monitorAnalysesSeverity = null;
    const kwList = rule.keywords.join(', ') || window.t('common.na');
    document.getElementById('monitor-tab-content').innerHTML = `
        <!-- Paramètres de la règle -->
        <div class="monitor-rule-info">
            <div class="monitor-rule-info-header">
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
                        ${rule.excluded_patterns && rule.excluded_patterns.length > 0 ? 
                            rule.excluded_patterns.map(pat => `
                            <span class="log-kw-badge kw-filter-btn excl-filter-btn ${activeKeywordFilter === '__excluded_' + pat ? 'active' : ''}" data-kw="__excluded_${encodeURIComponent(pat)}" onclick="toggleKeywordFilter(this, ${rule.id})" title="${window.t ? window.t('monitor.excluded_filter_title') : 'Show only excluded (filtered) lines'}">🚫 ${escapeHtml(pat)}</span>
                            `).join('') : ''}
                    </div>
                </div>
                <div><span class="info-label">⏱ Anti-spam</span>${rule.anti_spam_delay}s</div>
                <div><span class="info-label">🔔 ${window.t('monitor.severity')}</span>${rule.notify_severity_threshold}</div>
                </div>
                <div style="display: flex; gap: 0.5rem; align-items: flex-start;">
                    <button class="btn btn-secondary btn-sm" onclick="editRule(${rule.id})" title="${window.t ? window.t('common.edit') : 'Edit'}">✏️ ${window.t ? window.t('monitor.edit_rule') : 'Edit rule'}</button>
                    <button class="btn btn-danger btn-sm" onclick="deleteRule(${rule.id}, this)" title="${window.t ? window.t('common.delete') : 'Delete'}">🗑️ ${window.t ? window.t('common.delete') : 'Delete'}</button>
                </div>
            </div>
        </div>

        ${rule.last_learning_session_id ? `
        <!-- MON-10: Auto-learning status panel -->
        <div class="monitor-autolearn-panel" id="monitor-autolearn-${rule.id}">
            <span class="info-label">🤖 ${window.t ? window.t('monitor.autolearn_title') : 'Auto-learning'}</span>
            <span class="kw-hint" style="opacity:.6">⏳ ${window.t ? window.t('common.loading') : 'Loading...'}</span>
        </div>
        ` : ''}

        <div style="display: flex; gap: 1rem; margin-bottom: 0.5rem; flex-wrap: wrap;">
            <!-- Buffer anti-spam -->
            <div class="monitor-buffer-status" id="buffer-status-${rule.id}" style="flex: 1; min-width: 250px; margin-bottom: 0;">
                <span class="buffer-dot idle" id="buffer-dot-${rule.id}"></span>
                <span id="buffer-label-${rule.id}">${window.t ? window.t('monitor.buffer_inactive') : 'Inactive buffer'}</span>
            </div>
            
            <div class="monitor-buffer-status" id="resolution-status-panel-${rule.id}" style="flex: 1; min-width: 250px; margin-bottom: 0;">
                <div style="display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; flex-wrap: wrap;">
                    <div style="display: flex; align-items: center; gap: 0.5rem;">
                        <span class="rule-status-dot status-${rule.alert_status || 'normal'}" id="resolution-dot-${rule.id}"></span>
                        <span>
                            <span style="opacity: 0.7;" data-i18n="monitor.panel_alert_status">État de l'alerte</span> : 
                            <strong id="resolution-text-${rule.id}" class="status-text-${rule.alert_status || 'normal'}">
                                ${rule.alert_status === 'alert' ? (window.t ? window.t('monitor.status_alert') : 'En Alerte') : rule.alert_status === 'resolving' ? (window.t ? window.t('monitor.status_resolving') : 'Vérification IA...') : (window.t ? window.t('monitor.status_normal') : 'Normal')}
                            </strong>
                            <span id="resolution-duration-${rule.id}" style="font-size: 0.8rem; opacity: 0.6; margin-left: 0.25rem;"></span>
                        </span>
                    </div>
                    <div style="display: flex; align-items: center; gap: 0.4rem; flex-wrap: wrap;">
                        <!-- Badges de résumé des verdicts (toujours visibles) -->
                        <span id="verdict-summary-${rule.id}" style="display: flex; gap: 0.3rem; align-items: center;"></span>
                        <!-- Bouton historique IA -->
                        <button class="btn btn-secondary btn-sm" style="padding: 0.15rem 0.5rem; font-size: 0.75rem;" onclick="toggleResolutionHistory(${rule.id})" id="btn-history-${rule.id}">
                            📋 ${window.t ? window.t('monitor.resolution_history_btn') : 'Voir l\'historique IA'}
                        </button>
                        <button class="btn btn-secondary btn-sm ${rule.alert_status !== 'normal' ? '' : 'hidden'}" id="btn-manual-resolve-${rule.id}" onclick="triggerManualResolve(${rule.id}, this)" data-i18n="monitor.btn_resolve_manually">✅ Marquer comme résolu</button>
                    </div>
                </div>
                <!-- Accordéon historique verdicts (replié par défaut) -->
                <div id="resolution-history-panel-${rule.id}" class="hidden" style="margin-top: 0.75rem; border-top: 1px solid var(--border); padding-top: 0.6rem;">
                    <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.4rem;">
                        <div style="font-size: 0.78rem; opacity: 0.6; font-weight: 500;">
                            🤖 ${window.t ? window.t('monitor.resolution_history_title') : 'Historique IA de résolution'}
                        </div>
                        <button class="btn btn-secondary btn-sm" style="padding: 0.1rem 0.45rem; font-size: 0.7rem;" onclick="auditPatternsWithAI(${rule.id}, this)" id="btn-audit-patterns-${rule.id}" title="${window.t ? window.t('monitor.audit_patterns_btn_title') : 'Demander a l\'IA d\'evaluer la pertinence des patterns'}">
                            🧹 ${window.t ? window.t('monitor.audit_patterns_btn') : 'Audit IA'}
                        </button>
                    </div>
                    <div id="audit-result-${rule.id}" class="hidden" style="margin-bottom: 0.5rem; font-size: 0.78rem; padding: 0.4rem 0.6rem; border-radius: 5px; background: rgba(99,102,241,0.06); border: 1px solid rgba(99,102,241,0.15);"></div>
                    <div id="resolution-history-list-${rule.id}" style="max-height: 280px; overflow-y: auto;">
                        <em style="opacity: 0.5; font-size: 0.8rem;">${window.t ? window.t('common.loading') : 'Chargement...'}</em>
                    </div>
                </div>
            </div>
        </div>

        <!-- MON-14 & MON-15: Timestamps & Inactivity -->
        <div class="monitor-buffer-status" style="margin-bottom: 0.5rem; display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; font-size: 0.85rem; gap: 0.5rem;">
            <span><span style="opacity: 0.7;">${window.t ? window.t('monitor.last_received') : 'Latest received line'}</span> <strong id="last-received-${rule.id}">${rule.last_line_received_at ? formatDate(rule.last_line_received_at) : '—'}</strong> <span id="last-received-ago-${rule.id}" style="opacity: 0.5; font-size: 0.8em;">${rule.last_line_received_at ? formatRelativeTime(rule.last_line_received_at) : ''}</span></span>
            
            <div id="inactivity-status-${rule.id}" style="display: ${rule.inactivity_warning_enabled ? 'flex' : 'none'}; align-items: center; gap: 0.5rem; justify-content: center; flex: 1; min-width: max-content;">
                <span class="buffer-dot idle" id="inactivity-dot-${rule.id}"></span>
                <span id="inactivity-label-${rule.id}">—</span>
            </div>

            <span><span style="opacity: 0.7;">${window.t ? window.t('monitor.last_analyzed') : 'Latest analysed line'}</span> <strong id="last-analyzed-${rule.id}">${rule.last_analysis_at ? formatDate(rule.last_analysis_at) : '—'}</strong></span>
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
                <button class="btn btn-secondary btn-sm" id="freeze-btn-${rule.id}" onclick="toggleFreeze(${rule.id})">${window.t ? '❄️ ' + window.t('monitor.freeze') : '❄️ Freeze'}</button>
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

        <!-- MON-11: Analyses with severity filter badges in header -->
        <div class="monitor-analyses-header" onclick="toggleAnalysesSection(${rule.id})">
            <span class="viewer-title">
                <span class="viewer-toggle-icon" id="analyses-toggle-icon-${rule.id}">▼</span>
                <strong>📊 ${window.t ? window.t('monitor.recent_analyses_llm') : 'Recent Analyses (LLM)'}</strong>
            </span>
            <div class="monitor-analyses-filters" onclick="event.stopPropagation()">
                ${rule.unviewed_count > 0 ? `
                <button class="btn btn-secondary btn-sm btn-mark-all-read" onclick="markAllAnalysesAsViewed(${rule.id}, this)" style="margin-right: 0.75rem; padding: 0.15rem 0.5rem; font-size: 0.75rem; line-height: 1;">
                    ✓ ${window.t ? window.t('monitor.mark_all_read') : 'Tout marquer comme lu'}
                </button>
                ` : ''}
                <span class="filter-badge ${!monitorAnalysesSeverity ? 'active' : ''}" onclick="filterMonitorAnalyses(${rule.id}, null)">Total: ${rule.stats?.total || 0}</span>
                <span class="filter-badge critical ${monitorAnalysesSeverity === 'critical' ? 'active' : ''}" onclick="filterMonitorAnalyses(${rule.id}, 'critical')">${window.t('dashboard.critical')}: ${rule.stats?.critical || 0}</span>
                <span class="filter-badge warning ${monitorAnalysesSeverity === 'warning' ? 'active' : ''}" onclick="filterMonitorAnalyses(${rule.id}, 'warning')">${window.t('dashboard.warning')}: ${rule.stats?.warning || 0}</span>
                <span class="filter-badge info ${monitorAnalysesSeverity === 'info' ? 'active' : ''}" onclick="filterMonitorAnalyses(${rule.id}, 'info')">${window.t('dashboard.info')}: ${rule.stats?.info || 0}</span>
            </div>
        </div>
        <div id="monitor-analyses-${rule.id}" class="monitor-analyses-list">
            <div class="loading">${window.t ? window.t('common.loading') : 'Loading...'}</div>
        </div>
    `;

    // MON-10: Load auto-learning status if session exists
    if (rule.last_learning_session_id) {
        _loadMonitorAutolearn(rule.id, rule.last_learning_session_id);
    }
    // MON-19: Charger les badges de resume des verdicts si la resolution est active
    if (rule.resolution_mode && rule.resolution_mode !== 'disabled') {
        loadAndRefreshVerdictSummary(rule.id);
    }
    // Charger les analyses immediatement
    loadMonitorAnalyses(rule.id);
    window.i18n?.applyTranslations();
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
    // MON-12: Auto-refresh analyses every 15s (polling new arrivals)
    analysisIntervals[rule.id] = setInterval(() => {
        if (!isFrozen) pollNewAnalyses(rule.id);
    }, 15000);
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
    Object.values(analysisIntervals).forEach(clearInterval);
    tailIntervals = {};
    bufferIntervals = {};
    analysisIntervals = {};
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
        } else if (rule.log_file_path && rule.log_file_path.startsWith('[SYSLOG]:')) {
            // Syslog rule → use in-memory syslog buffer tail
            const hostname = rule.log_file_path.split(':')[1];
            res = await apiFetch(
                `/api/monitor/syslog/tail/${encodeURIComponent(hostname)}?lines=${monitorLogLines}&keywords=${encodeURIComponent(kwParam)}`
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
        const savedScrollTop = viewer.scrollTop;

        const excludedPatterns = rule.excluded_patterns || [];

        viewer.innerHTML = res.lines.map((line, idx) => {
            const rawText = line.text || '';
            const text = escapeHtml(rawText);
            const isSelected = selectedLineText === rawText;
            const matchClass = line.matched ? 'matched' : '';
            const selectClass = isSelected ? 'selected' : '';

            // Client-side exclusion detection
            const matchedExclPattern = excludedPatterns.length > 0 
                ? excludedPatterns.find(pat => rawText.toLowerCase().includes(pat.toLowerCase())) 
                : null;
            const isExcluded = matchedExclPattern != null;
            const excludedClass = isExcluded ? 'log-line-excluded' : '';

            const kwBadges = line.matched_keywords && line.matched_keywords.length > 0
                ? `<span class="log-kw-badges">${line.matched_keywords.map(k => `<span class="log-kw-badge">${escapeHtml(k)}</span>`).join('')}</span>`
                : '';

            const exclBadge = isExcluded
                ? `<span class="log-kw-badges"><span class="log-excl-badge">🚫 ${escapeHtml(matchedExclPattern)}</span></span>`
                : '';

            return `<div class="log-line ${matchClass} ${selectClass} ${excludedClass}" data-rule="${rule.id}" data-idx="${idx}" data-excluded="${isExcluded}" data-excl-pat="${encodeURIComponent(matchedExclPattern || '')}" data-text="${encodeURIComponent(rawText)}" onclick="onLineClick(this, ${rule.id})">
                <span class="log-text">${text}</span>${kwBadges}${exclBadge}
            </div>`;
        }).join('');

        // Mettre à jour le compteur
        const matched = res.lines.filter(l => l.matched).length;
        const lc = document.getElementById(`linecount-${rule.id}`);
        if (lc) lc.textContent = `(${res.lines.length} ${window.t ? window.t('monitor.lines_label') : 'lines'}, ${matched} ${window.t ? window.t('monitor.matched_label') : 'matched'})`;

        // Réappliquer le filtre mot-clé actif d'abord pour calculer la hauteur finale du conteneur
        applyKeywordFilter(rule.id);
        updateFilterLabel(rule.id);
        
        // S'assurer que les badges reflètent bien l'état actif
        document.querySelectorAll('.kw-filter-btn').forEach(b => {
            const bKw = decodeURIComponent(b.dataset.kw || '');
            b.classList.toggle('active', bKw === activeKeywordFilter);
        });

        // Effectuer le défilement/restauration sur la hauteur finale filtrée
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
        } else {
            // User had scrolled up — restore their position
            viewer.scrollTop = savedScrollTop;
        }

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
        } else if (activeKeywordFilter.startsWith('__excluded_')) {
            const pat = activeKeywordFilter.substring('__excluded_'.length);
            show = line.dataset.excluded === 'true' && decodeURIComponent(line.dataset.exclPat || '') === pat;
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

        // Update recovery status panel (MON-18)
        const resDot = document.getElementById(`resolution-dot-${ruleId}`);
        const resText = document.getElementById(`resolution-text-${ruleId}`);
        const resDuration = document.getElementById(`resolution-duration-${ruleId}`);
        const resBtn = document.getElementById(`btn-manual-resolve-${ruleId}`);

        if (resDot && resText) {
            const status = buf.alert_status || 'normal';
            resDot.className = `rule-status-dot status-${status}`;
            
            let statusText = window.t ? window.t('monitor.status_normal') : 'Normal';
            if (status === 'alert') {
                statusText = window.t ? window.t('monitor.status_alert') : 'In Alert';
            } else if (status === 'resolving') {
                statusText = window.t ? window.t('monitor.status_resolving') : 'AI Checking...';
            }
            resText.textContent = statusText;
            resText.className = `status-text-${status}`;

            if (status !== 'normal' && buf.alert_started_at) {
                const elapsedMs = new Date() - new Date(buf.alert_started_at);
                const totalSec = Math.floor(elapsedMs / 1000);
                const hrs = Math.floor(totalSec / 3600);
                const mins = Math.floor((totalSec % 3600) / 60);
                const secs = totalSec % 60;
                
                const sinceText = window.t ? window.t('monitor.since') : 'since';
                let timeStr = '';
                if (hrs > 0) {
                    timeStr = `${hrs}h ${mins}m`;
                } else {
                    timeStr = `${mins}m ${secs}s`;
                }
                if (resDuration) resDuration.textContent = `(${sinceText} ${timeStr})`;
            } else {
                if (resDuration) resDuration.textContent = '';
            }

            if (resBtn) {
                resBtn.classList.toggle('hidden', status === 'normal');
            }
        }

        // Update timestamps (MON-14)
        const lastReceivedEl = document.getElementById(`last-received-${ruleId}`);
        if (lastReceivedEl) lastReceivedEl.textContent = buf.last_line_received_at ? formatDate(buf.last_line_received_at) : '—';
        const lastReceivedAgoEl = document.getElementById(`last-received-ago-${ruleId}`);
        if (lastReceivedAgoEl) lastReceivedAgoEl.textContent = buf.last_line_received_at ? formatRelativeTime(buf.last_line_received_at) : '';
        const lastAnalyzedEl = document.getElementById(`last-analyzed-${ruleId}`);
        if (lastAnalyzedEl) lastAnalyzedEl.textContent = buf.last_analysis_at ? formatDate(buf.last_analysis_at) : '—';

        // Update inactivity (MON-15)
        const inactStatusEl = document.getElementById(`inactivity-status-${ruleId}`);
        const inactDot = document.getElementById(`inactivity-dot-${ruleId}`);
        const inactLabel = document.getElementById(`inactivity-label-${ruleId}`);
        
        if (inactStatusEl && inactDot && inactLabel) {
            if (buf.inactivity_warning_enabled) {
                inactStatusEl.style.display = 'flex';
                let isInactive = false;
                if (buf.last_line_received_at) {
                    const receivedDate = new Date(buf.last_line_received_at);
                    const diffMs = new Date() - receivedDate;
                    if (diffMs > (buf.inactivity_period_hours * 3600000)) {
                        isInactive = true;
                    }
                }
                
                if (isInactive) {
                    inactDot.className = 'buffer-dot active'; 
                    inactDot.style.backgroundColor = 'var(--danger)';
                    inactDot.style.boxShadow = '0 0 8px var(--danger)';
                    inactLabel.innerHTML = `⚠️ <strong style="color:var(--danger)">${window.t ? window.t('monitor.inactivity_detected') : 'Inactivity detected'}</strong> (> ${buf.inactivity_period_hours}h)`;
                } else {
                    inactDot.className = 'buffer-dot idle';
                    inactDot.style.backgroundColor = '';
                    inactDot.style.boxShadow = '';
                    inactLabel.innerHTML = `✅ ${window.t ? window.t('monitor.activity_normal') : 'Activity normal'} (< ${buf.inactivity_period_hours}h)`;
                }
            } else {
                inactStatusEl.style.display = 'none';
            }
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
        ${relatedAnalysis.resolution_status === 'resolved' ? `
        <div class="detail-row">
            <span class="detail-label">${window.t ? window.t('monitor.resolution_box_title') : 'Retour à la normale'}</span>
            <div class="detail-value" style="background: rgba(16, 185, 129, 0.08); border: 1px solid rgba(16, 185, 129, 0.25); border-left: 3px solid var(--success); padding: 0.6rem 0.8rem; border-radius: 4px; font-size: 0.85rem; display: flex; flex-direction: column; gap: 0.35rem;">
                <div><span style="opacity: 0.7; font-weight: 500;">${window.t ? window.t('monitor.resolved_at_label') : 'Résolu le :'}</span> <span>${formatDate(relatedAnalysis.resolved_at)}</span></div>
                <div><span style="opacity: 0.7; font-weight: 500;">${window.t ? window.t('monitor.alert_duration_label') : 'Durée de l\'alerte :'}</span> <span>${(() => {
                    const start = new Date(relatedAnalysis.analyzed_at);
                    const end = new Date(relatedAnalysis.resolved_at);
                    const diffMs = end - start;
                    if (diffMs <= 0) return 'N/A';
                    const totalSec = Math.floor(diffMs / 1000);
                    const hrs = Math.floor(totalSec / 3600);
                    const mins = Math.floor((totalSec % 3600) / 60);
                    const secs = totalSec % 60;
                    if (hrs > 0) return `${hrs}h ${mins}m`;
                    if (mins > 0) return `${mins}m ${secs}s`;
                    return `${secs}s`;
                })()}</span></div>
                ${relatedAnalysis.resolution_line ? `
                <div style="margin-top: 0.25rem;"><span style="opacity: 0.7; font-weight: 500;">${window.t ? window.t('monitor.resolution_line_label') : 'Ligne de résolution :'}</span> <code style="word-break: break-all; font-size: 0.78rem; display: block; margin-top: 0.15rem; background: rgba(255,255,255,0.05); padding: 0.25rem 0.5rem; border-radius: 3px;">${escapeHtml(relatedAnalysis.resolution_line)}</code></div>
                ` : ''}
                ${relatedAnalysis.resolution_patterns && relatedAnalysis.resolution_patterns.length > 0 ? `
                <div><span style="opacity: 0.7; font-weight: 500;">${window.t ? window.t('monitor.resolution_patterns_label') : 'Motifs détectés :'}</span> <span>${relatedAnalysis.resolution_patterns.map(p => `<span class="log-kw-badge" style="background: rgba(16, 185, 129, 0.15); color: var(--success); border: 1px solid rgba(16, 185, 129, 0.3); font-size: 0.72rem; padding: 0.05rem 0.3rem; border-radius: 3px;">${escapeHtml(p)}</span>`).join(' ')}</span></div>
                ` : ''}
                ${relatedAnalysis.resolution_ai_explanation ? `
                <div style="margin-top: 0.4rem; border-top: 1px dashed rgba(16, 185, 129, 0.15); padding-top: 0.4rem;">
                    <span style="opacity: 0.7; font-weight: 500;">${window.t ? window.t('monitor.resolution_ai_conclusion') : 'Conclusion de l\'IA :'}</span>
                    ${relatedAnalysis.resolution_ai_confidence ? ` <span class="severity-badge info" style="background: rgba(59, 130, 246, 0.15); color: var(--info); border: 1px solid rgba(59, 130, 246, 0.35); font-size: 0.7rem; padding: 0.05rem 0.25rem; font-weight: normal; vertical-align: middle;">${relatedAnalysis.resolution_ai_confidence}%</span>` : ''}
                    <div style="margin-top: 0.15rem; font-style: italic; font-size: 0.8rem; opacity: 0.85;">${escapeHtml(relatedAnalysis.resolution_ai_explanation)}</div>
                </div>
                ` : ''}
            </div>
        </div>
        ` : ''}
        <div class="detail-actions" style="margin-top: 0.75rem; border-top: 1px solid var(--border); padding-top: 0.5rem; display: flex; justify-content: space-between; align-items: center;">
            <div style="display: flex; gap: 0.5rem;">
                <button class="btn btn-secondary btn-sm" onclick="retryAnalysis(${relatedAnalysis.id}, this)">🔄 ${window.t('common.retry')}</button>
                <button class="btn btn-secondary btn-sm" onclick="notifyAnalysis(${relatedAnalysis.id}, this)">🔔 ${window.t('common.notify')}</button>
            </div>
            <button class="btn btn-primary btn-sm" onclick="openChat(${relatedAnalysis.id})">💬 ${window.t('common.deepen')}</button>
        </div>
        ` : `
        <div class="detail-row"><em>${window.t ? window.t('monitor.no_auto_analysis') : 'No automatic analysis found for this line.'}</em></div>
        <div class="detail-actions" style="margin-top: 1rem; border-top: 1px solid var(--border); padding-top: 0.75rem;">
            <button class="btn btn-primary btn-sm" onclick="manualAnalyze(${ruleId}, this)">🤖 ${window.t('monitor.analyze_with_ollama')}</button>
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
        btn.textContent = isFrozen ? (window.t ? '▶️ ' + window.t('monitor.resume') : '▶️ Resume') : (window.t ? '❄️ ' + window.t('monitor.freeze') : '❄️ Freeze');
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

// ─── MON-11: severity filter dispatcher ────────────────────────────────────
function filterMonitorAnalyses(ruleId, severity) {
    monitorAnalysesSeverity = severity;
    monitorAnalysesOffset = 0;
    loadMonitorAnalyses(ruleId, severity, true);
}

async function pollNewAnalyses(ruleId) {
    const container = document.getElementById(`monitor-analyses-${ruleId}`);
    if (!container) return;

    const cards = container.querySelectorAll('.analysis-card');
    if (cards.length === 0) {
        loadMonitorAnalyses(ruleId, monitorAnalysesSeverity, true);
        return;
    }

    const currentIds = Array.from(cards).map(c => {
        const idAttr = c.id || '';
        return parseInt(idAttr.replace('analysis-card-', ''));
    }).filter(id => !isNaN(id));

    const maxId = currentIds.length > 0 ? Math.max(...currentIds) : 0;

    try {
        let url = `/api/dashboard/recent?limit=10&rule_id=${ruleId}&offset=0`;
        if (monitorAnalysesSeverity) {
            url += `&severity=${monitorAnalysesSeverity}`;
        }

        const res = await apiFetch(url);
        const analyses = res.analyses || res;

        // Update existing cards if their resolution state changed
        analyses.forEach(a => {
            const existingCard = document.getElementById(`analysis-card-${a.id}`);
            if (existingCard) {
                const hasResolvedBadge = existingCard.querySelector('.resolved-badge') !== null;
                const isResolvedInApi = a.resolution_status === 'resolved';

                if (isResolvedInApi !== hasResolvedBadge) {
                    const isCollapsed = existingCard.classList.contains('collapsed');
                    const newCardHtml = renderAnalysisCard(a, {
                        collapsed: isCollapsed,
                        showDelete: true,
                        showCopy: true,
                        showRuleName: false,
                    });

                    const tempDiv = document.createElement('div');
                    tempDiv.innerHTML = newCardHtml;
                    const newCardElement = tempDiv.firstElementChild;
                    existingCard.replaceWith(newCardElement);
                }
            }
        });

        // Find analyses that are newer than maxId
        const newAnalyses = analyses.filter(a => a.id > maxId);
        if (newAnalyses.length === 0) return;

        // Render cards for new analyses
        const newCardsHtml = newAnalyses.map(a => {
            return renderAnalysisCard(a, {
                collapsed: true,
                showDelete: true,
                showCopy: true,
                showRuleName: false,
                cardClass: 'analysis-card newly-added-highlight'
            });
        }).join('');

        // Prepend them to the container
        container.insertAdjacentHTML('afterbegin', newCardsHtml);

        // Adjust the offset so offset paging remains correct
        monitorAnalysesOffset += newAnalyses.length;

        // Auto-mark the first new one as viewed
        markAnalysisAsViewed(newAnalyses[0].id);

        // Force reload rule list stats to update badge counts in tabs
        loadMonitorRules();

    } catch (e) {
        console.error("Failed to poll new analyses:", e);
    }
}

async function loadMonitorAnalyses(ruleId, severityFilter = null, resetList = true) {
    const container = document.getElementById(`monitor-analyses-${ruleId}`);
    if (!container) return;

    // Use stored severity if called without explicit filter (e.g. from auto-refresh)
    if (severityFilter === undefined || severityFilter === null) {
        severityFilter = monitorAnalysesSeverity;
    }

    // Update active badge state
    const tabContent = document.getElementById('monitor-tab-content');
    const filterArea = tabContent ? tabContent.querySelector('.monitor-analyses-filters') : null;
    if (filterArea) {
        filterArea.querySelectorAll('.filter-badge').forEach(b => b.classList.remove('active'));
        const activeBadge = Array.from(filterArea.querySelectorAll('.filter-badge')).find(b => {
            const oc = b.getAttribute('onclick') || '';
            return severityFilter === null ? oc.includes(', null)') : oc.includes(`'${severityFilter}'`);
        });
        if (activeBadge) activeBadge.classList.add('active');
    }

    const offset = resetList ? 0 : monitorAnalysesOffset;
    if (resetList) monitorAnalysesOffset = 0;

    try {
        let url = `/api/dashboard/recent?limit=20&rule_id=${ruleId}&offset=${offset}`;
        if (severityFilter) {
            url += `&severity=${severityFilter}`;
        }
        
        const res = await apiFetch(url);
        const analyses = res.analyses || res;
        const hasMore = res.has_more || false;

        if (analyses.length === 0 && offset === 0) {
            container.innerHTML = `<div class="no-logs">${window.t ? window.t('monitor.no_recent_analyses') : 'No recent analyses'}${severityFilter ? ` (${severityFilter})` : ''}</div>`;
            return;
        }

        const cards = analyses.map((a, idx) => {
            const isFirst = (offset === 0 && idx === 0);
            return renderAnalysisCard(a, {
                collapsed: !isFirst,
                showDelete: true,
                showCopy: true,
                showRuleName: false,
            });
        }).join('');

        if (resetList) {
            container.innerHTML = cards;
            if (analyses.length > 0) {
                markAnalysisAsViewed(analyses[0].id);
            }
        } else {
            // Append (pagination)
            const showMoreBtn = container.querySelector('.monitor-show-more');
            if (showMoreBtn) showMoreBtn.remove();
            container.insertAdjacentHTML('beforeend', cards);
        }

        monitorAnalysesOffset = offset + analyses.length;

        // Show more button
        if (hasMore) {
            container.insertAdjacentHTML('beforeend', `
                <button class="btn btn-secondary monitor-show-more" onclick="loadMonitorAnalyses(${ruleId}, ${severityFilter ? `'${severityFilter}'` : 'null'}, false)">
                    ${window.t ? window.t('monitor.show_more') : 'Show more'}
                </button>
            `);
        }
    } catch (e) {
        container.innerHTML = `<div class="loading" style="color:var(--danger)">${window.t ? window.t('common.error') : 'Erreur'} : ${escapeHtml(e.message)}</div>`;
    }
}

function toggleAnalysisCard(analysisId) {
    const card = document.getElementById(`analysis-card-${analysisId}`);
    if (!card) return;
    const isCollapsed = card.classList.toggle('collapsed');
    const toggle = card.querySelector('.collapse-toggle');
    if (toggle) toggle.textContent = isCollapsed ? '▶' : '▼';
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
                ${renderAnalysisCard(a, {
                    collapsed: false,
                    showDelete: true,
                    showCopy: true,
                    showRuleName: true,
                })}
            </div>
        `;
        markAnalysisAsViewed(a.id);
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
                                    <button class="btn btn-primary btn-sm" onclick="openChat(${a.id})">💬 ${window.t('common.deepen')}</button>
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
        btn.innerHTML = '⏳ ' + window.t('common.sending');
        btn.disabled = true;

        const res = await apiFetch(`/api/monitor/notify/${analysisId}`, {
            method: 'POST'
        });

        if (res.status === 'ok') {
            btn.innerHTML = '✅ ' + window.t('common.sent');
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

// ─── MON-10: Auto-learning status panel in Monitor ─────────────────────────

let _monitorAutolearnTimers = {};

async function _loadMonitorAutolearn(ruleId, sessionId) {
    // Clear existing timer
    if (_monitorAutolearnTimers[ruleId]) {
        clearInterval(_monitorAutolearnTimers[ruleId]);
        delete _monitorAutolearnTimers[ruleId];
    }
    _renderAutolearnStatus(ruleId, sessionId);
    _monitorAutolearnTimers[ruleId] = setInterval(async () => {
        const done = await _renderAutolearnStatus(ruleId, sessionId);
        if (done) {
            clearInterval(_monitorAutolearnTimers[ruleId]);
            delete _monitorAutolearnTimers[ruleId];
        }
    }, 3000);
}

async function _renderAutolearnStatus(ruleId, sessionId) {
    try {
        const data = await fetch(`/api/keyword-learning/${sessionId}/status`).then(r => r.json());
        const panel = document.getElementById(`monitor-autolearn-${ruleId}`);
        if (!panel) return false;

        const pct = data.total_packets > 0
            ? Math.round((data.completed_packets / data.total_packets) * 100) : 0;

        const _t = (key, fb, vars) => {
            let s = window.t ? window.t(key) || fb : fb;
            if (vars) Object.entries(vars).forEach(([k, v]) => { s = s.replace(`{${k}}`, v); });
            return s;
        };

        const STATUS = {
            pending:   '⏳ ' + _t('kw.card_pending',   'Waiting to start…'),
            scanning:  '🔍 ' + _t('kw.card_scanning',  'Scan — {done}/{total} packets ({pct}%)', { done: data.completed_packets, total: data.total_packets, pct }),
            refining:  '🧠 ' + _t('kw.card_refining',  'AI refining…'),
            validated: '✅ ' + _t('kw.card_validated', 'Learning complete'),
            reverted:  '↩️ ' + _t('kw.card_reverted',  'Reverted'),
            error:     '⚠️ ' + _t('kw.card_error',     'Error: {msg}', { msg: data.error_message || 'Unknown' }),
        };

        const isActive = ['pending', 'scanning', 'refining'].includes(data.status);
        const isDone   = ['validated', 'reverted', 'error'].includes(data.status);

        panel.innerHTML = `
            <span class="info-label">🤖 ${window.t ? window.t('monitor.autolearn_title') : 'Auto-learning'}</span>
            <div class="kw-card-status">${STATUS[data.status] || data.status}</div>
            ${data.status === 'scanning' ? `
            <div style="margin:.3rem 0 .4rem">
                <div class="kw-progress-bar-track"><div class="kw-progress-bar" style="width:${pct}%"></div></div>
            </div>` : ''}
            ${data.status === 'scanning' && data.raw_keywords?.length ? `
            <div class="kw-tags-row" style="margin-top:.25rem;display:flex">
                ${data.raw_keywords.slice(0, 8).map(k => `<span class="kw-tag kw-tag--raw">${escapeHtml(k)}</span>`).join('')}
                ${data.raw_keywords.length > 8 ? `<span class="kw-hint" style="align-self:center">+${data.raw_keywords.length - 8}</span>` : ''}
            </div>` : ''}
            ${data.status === 'validated' && data.final_keywords?.length ? `
            <div class="kw-tags-row" style="margin-top:.25rem;display:flex">
                ${data.final_keywords.map(k => `<span class="kw-tag">${escapeHtml(k)}</span>`).join('')}
            </div>` : ''}
            <div class="kw-actions" style="margin-top:.4rem">
                ${isActive ? `<button type="button" class="btn btn-danger btn-sm" onclick="kwStopSession(${sessionId})">⏹ ${_t('kw.stop_btn','Stop')}</button>` : ''}
                ${data.status === 'validated' ? `<button type="button" class="btn btn-secondary btn-sm" onclick="kwRevertSession(${sessionId})">↩️ ${_t('kw.revert_btn','Revert')}</button>` : ''}
                ${isDone ? `<a href="/api/keyword-learning/${sessionId}/log" download class="btn btn-secondary btn-sm">📥 Log</a>` : ''}
            </div>
        `;

        return isDone;
    } catch { return false; }
}

async function markAllAnalysesAsViewed(ruleId, btn) {
    if (btn) btn.disabled = true;
    try {
        await apiFetch(`/api/monitor/rules/${ruleId}/view-all`, { method: 'POST' });
        const rule = monitorRules.find(r => r.id === ruleId);
        if (rule) {
            rule.unviewed_count = 0;
            if (rule.stats) {
                rule.stats.total = 0;
                rule.stats.critical = 0;
                rule.stats.warning = 0;
                rule.stats.info = 0;
            }
            renderTabs();
            renderTabContent(rule);
        }
    } catch (e) {
        console.error("Failed to mark all as read:", e);
        if (btn) btn.disabled = false;
    }
}

async function triggerManualResolve(ruleId, btnElement) {
    if (btnElement.classList.contains('disabled')) return;
    btnElement.classList.add('disabled');
    btnElement.textContent = window.t ? window.t('common.loading') : 'Loading...';
    try {
        await apiFetch(`/api/monitor/rules/${ruleId}/resolve`, { method: 'POST' });
        await loadMonitorRules();
        selectTab(ruleId);
    } catch (e) {
        alert((window.t ? window.t('common.error') : 'Error') + ': ' + e.message);
        btnElement.classList.remove('disabled');
        btnElement.innerHTML = window.t ? window.t('monitor.btn_resolve_manually') : '✅ Mark as resolved';
    }
}

// ─── Historique des verdicts de résolution (MON-19/MON-20) ────────────────

async function toggleResolutionHistory(ruleId) {
    const panel = document.getElementById(`resolution-history-panel-${ruleId}`);
    if (!panel) return;
    const isHidden = panel.classList.toggle('hidden');
    if (!isHidden) {
        await loadResolutionHistory(ruleId);
    }
}

async function loadResolutionHistory(ruleId, limit = 20) {
    const listEl = document.getElementById(`resolution-history-list-${ruleId}`);
    if (!listEl) return;
    listEl.innerHTML = `<em style="opacity:0.5;font-size:0.8rem;">${window.t ? window.t('common.loading') : 'Chargement...'}</em>`;
    try {
        const data = await apiFetch(`/api/monitor/rules/${ruleId}/resolution-history?limit=${limit}`);
        const verdicts = data.verdicts || [];

        // Mettre a jour les badges de resume
        updateVerdictSummaryBadges(ruleId, verdicts, data.total);

        if (verdicts.length === 0) {
            listEl.innerHTML = `<em style="opacity:0.45;font-size:0.8rem;">${window.t ? window.t('monitor.resolution_history_empty') : 'Aucun verdict enregistre.'}</em>`;
            return;
        }

        listEl.innerHTML = verdicts.map(v => renderVerdictRow(v, ruleId)).join('');
    } catch (e) {
        listEl.innerHTML = `<em style="color:var(--danger);font-size:0.8rem;">${escapeHtml(e.message)}</em>`;
    }
}

async function loadAndRefreshVerdictSummary(ruleId) {
    try {
        const data = await apiFetch(`/api/monitor/rules/${ruleId}/resolution-history?limit=50`);
        updateVerdictSummaryBadges(ruleId, data.verdicts || [], data.total || 0);
    } catch (e) {}
}

function updateVerdictSummaryBadges(ruleId, verdicts, total) {
    const summaryEl = document.getElementById(`verdict-summary-${ruleId}`);
    if (!summaryEl || total === 0) return;

    const accepted = verdicts.filter(v => ['accepted', 'accepted_no_ai', 'manual'].includes(v.outcome)).length;
    const rejected = verdicts.filter(v => ['rejected_ai', 'rejected_low_confidence'].includes(v.outcome)).length;
    const fp = verdicts.filter(v => v.outcome === 'false_positive_user').length;

    const badges = [];
    if (accepted > 0) badges.push(`<span title="${window.t ? window.t('monitor.resolution_summary_accepted') : 'acceptees'}" style="font-size:0.7rem;padding:0.05rem 0.3rem;border-radius:3px;background:rgba(16,185,129,0.15);color:var(--success);border:1px solid rgba(16,185,129,0.3);">✅ ${accepted}</span>`);
    if (rejected > 0) badges.push(`<span title="${window.t ? window.t('monitor.resolution_summary_rejected') : 'rejetees'}" style="font-size:0.7rem;padding:0.05rem 0.3rem;border-radius:3px;background:rgba(245,158,11,0.15);color:var(--warning);border:1px solid rgba(245,158,11,0.3);">⚠️ ${rejected}</span>`);
    if (fp > 0) badges.push(`<span title="${window.t ? window.t('monitor.resolution_summary_fp') : 'faux-positifs'}" style="font-size:0.7rem;padding:0.05rem 0.3rem;border-radius:3px;background:rgba(239,68,68,0.15);color:var(--danger);border:1px solid rgba(239,68,68,0.3);">🚫 ${fp}</span>`);
    summaryEl.innerHTML = badges.join('');
}

function getOutcomeLabel(outcome) {
    const map = {
        'accepted': window.t ? window.t('monitor.verdict_outcome_accepted') : 'Accepte',
        'accepted_no_ai': window.t ? window.t('monitor.verdict_outcome_accepted_no_ai') : 'Accepte (sans IA)',
        'rejected_ai': window.t ? window.t('monitor.verdict_outcome_rejected_ai') : 'Rejete par l\'IA',
        'rejected_low_confidence': window.t ? window.t('monitor.verdict_outcome_rejected_low_confidence') : 'Rejete (confiance basse)',
        'manual': window.t ? window.t('monitor.verdict_outcome_manual') : 'Manuel',
        'false_positive_user': window.t ? window.t('monitor.verdict_outcome_false_positive_user') : 'Faux-positif',
    };
    return map[outcome] || outcome;
}

function getOutcomeStyle(outcome) {
    if (['accepted', 'accepted_no_ai', 'manual'].includes(outcome)) {
        return 'background:rgba(16,185,129,0.12);color:var(--success);border:1px solid rgba(16,185,129,0.25);';
    } else if (['rejected_ai', 'rejected_low_confidence'].includes(outcome)) {
        return 'background:rgba(245,158,11,0.12);color:var(--warning);border:1px solid rgba(245,158,11,0.25);';
    } else if (outcome === 'false_positive_user') {
        return 'background:rgba(239,68,68,0.1);color:var(--danger);border:1px solid rgba(239,68,68,0.25);';
    }
    return 'background:rgba(255,255,255,0.05);border:1px solid var(--border);';
}

function renderVerdictRow(v, ruleId) {
    const isFp = v.outcome === 'false_positive_user';
    const canMarkFp = !isFp && ['accepted', 'accepted_no_ai', 'manual'].includes(v.outcome);
    const outcomeStyle = getOutcomeStyle(v.outcome);

    const severityBadge = v.max_severity
        ? `<span class="severity-badge ${escapeHtml(v.max_severity)}" style="font-size:0.65rem;padding:0.05rem 0.25rem;vertical-align:middle;">${escapeHtml(v.max_severity)}</span>`
        : '';

    const confidenceBadge = v.ai_confidence !== null && v.ai_confidence !== undefined
        ? `<span title="${window.t ? window.t('monitor.verdict_confidence_label') : 'Confiance IA'}" style="font-size:0.68rem;opacity:0.75;">${v.ai_confidence}%</span>`
        : '';

    const patternsHtml = v.resolution_patterns && v.resolution_patterns.length > 0
        ? v.resolution_patterns.map(p => `<span class="log-kw-badge" style="font-size:0.68rem;padding:0.05rem 0.25rem;background:rgba(99,102,241,0.1);color:var(--primary);border:1px solid rgba(99,102,241,0.2);">${escapeHtml(p)}</span>`).join(' ')
        : '';

    return `<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:0.4rem;padding:0.35rem 0.5rem;border-radius:5px;margin-bottom:0.3rem;font-size:0.79rem;${outcomeStyle}">
        <div style="flex:1;min-width:0;">
            <div style="display:flex;align-items:center;gap:0.3rem;flex-wrap:wrap;margin-bottom:0.15rem;">
                <span style="font-weight:600;">${escapeHtml(getOutcomeLabel(v.outcome))}</span>
                ${confidenceBadge}
                ${severityBadge}
                <span style="opacity:0.5;font-size:0.72rem;margin-left:auto;white-space:nowrap;">${v.created_at ? formatDate(v.created_at) : ''}</span>
            </div>
            ${patternsHtml ? `<div style="margin-bottom:0.1rem;">${patternsHtml}</div>` : ''}
            ${v.trigger ? `<div style="opacity:0.65;font-size:0.73rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${escapeHtml(v.trigger)}</div>` : ''}
        </div>
        <div style="display:flex;gap:0.25rem;align-items:center;flex-shrink:0;">
            <button class="btn btn-secondary btn-sm" style="padding:0.1rem 0.35rem;font-size:0.7rem;" onclick="openVerdictModal(${JSON.stringify(v).replace(/"/g, '&quot;')}, ${ruleId})" title="${window.t ? window.t('monitor.verdict_detail_btn') : 'Détail'}">🔍</button>
            ${canMarkFp ? `<button class="btn btn-secondary btn-sm" style="padding:0.1rem 0.35rem;font-size:0.7rem;color:var(--danger);" onclick="markVerdictFalsePositive(${v.id}, ${ruleId}, this)" title="${window.t ? window.t('monitor.verdict_mark_fp') : 'Marquer faux-positif'}">🚫</button>` : ''}
        </div>
    </div>`;
}

async function markVerdictFalsePositive(verdictId, ruleId, btnEl) {
    const confirmMsg = window.t ? window.t('monitor.verdict_mark_fp_confirm') : 'Marquer ce verdict comme faux-positif ?';
    showInlineConfirm(btnEl, confirmMsg, async () => {
        try {
            await apiFetch(`/api/monitor/verdicts/${verdictId}/mark-false-positive`, { method: 'POST' });
            await loadResolutionHistory(ruleId);
        } catch (e) {
            alert((window.t ? window.t('common.error') : 'Error') + ': ' + e.message);
        }
    });
}

function openVerdictModal(verdict, ruleId) {
    // Creer ou reutiliser le modal
    let modal = document.getElementById('verdict-detail-modal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'verdict-detail-modal';
        modal.style.cssText = 'position:fixed;inset:0;z-index:9999;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,0.6);backdrop-filter:blur(4px);';
        modal.innerHTML = '<div id="verdict-modal-inner" style="background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:1.5rem;max-width:700px;width:90%;max-height:80vh;overflow-y:auto;box-shadow:0 20px 60px rgba(0,0,0,0.5);"></div>';
        modal.addEventListener('click', e => { if (e.target === modal) modal.classList.add('hidden'); });
        document.body.appendChild(modal);
    }
    modal.classList.remove('hidden');

    const canMarkFp = !['false_positive_user'].includes(verdict.outcome) && ['accepted', 'accepted_no_ai', 'manual'].includes(verdict.outcome);
    const contextLines = verdict.context_lines || [];
    const patternsHtml = verdict.resolution_patterns && verdict.resolution_patterns.length > 0
        ? verdict.resolution_patterns.map(p => `<code style="background:rgba(99,102,241,0.1);color:var(--primary);border-radius:3px;padding:0.1rem 0.3rem;font-size:0.8rem;">${escapeHtml(p)}</code>`).join(' ')
        : '<em style="opacity:0.5;">—</em>';

    document.getElementById('verdict-modal-inner').innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem;">
            <strong style="font-size:1rem;">🤖 ${window.t ? window.t('monitor.resolution_history_title') : 'Verdict IA'}</strong>
            <button class="btn-icon" onclick="document.getElementById('verdict-detail-modal').classList.add('hidden')">✕</button>
        </div>
        <div style="display:grid;gap:0.5rem;font-size:0.85rem;">
            <div style="display:flex;gap:0.5rem;align-items:center;padding:0.4rem 0.6rem;border-radius:6px;${getOutcomeStyle(verdict.outcome)}">
                <strong>${escapeHtml(getOutcomeLabel(verdict.outcome))}</strong>
                ${verdict.ai_confidence !== null && verdict.ai_confidence !== undefined ? `<span style="opacity:0.7;">${verdict.ai_confidence}%</span>` : ''}
                ${verdict.max_severity ? `<span class="severity-badge ${escapeHtml(verdict.max_severity)}" style="font-size:0.7rem;">${escapeHtml(verdict.max_severity)}</span>` : ''}
                <span style="opacity:0.5;margin-left:auto;font-size:0.78rem;">${verdict.created_at ? formatDate(verdict.created_at) : ''}</span>
            </div>
            <div><span style="opacity:0.6;font-size:0.8rem;">${window.t ? window.t('monitor.verdict_trigger_label') : 'Déclencheur'}</span><div style="margin-top:0.15rem;">${escapeHtml(verdict.trigger || '—')}</div></div>
            <div><span style="opacity:0.6;font-size:0.8rem;">Patterns</span><div style="margin-top:0.15rem;">${patternsHtml}</div></div>
            ${verdict.resolution_line ? `<div><span style="opacity:0.6;font-size:0.8rem;">${window.t ? window.t('monitor.resolution_line_label') : 'Ligne de résolution'}</span><code style="display:block;margin-top:0.2rem;font-size:0.77rem;background:rgba(255,255,255,0.04);border:1px solid var(--border);border-radius:4px;padding:0.35rem 0.5rem;word-break:break-all;">${escapeHtml(verdict.resolution_line)}</code></div>` : ''}
            ${verdict.ai_explanation ? `<div><span style="opacity:0.6;font-size:0.8rem;">${window.t ? window.t('monitor.resolution_ai_conclusion') : 'Conclusion IA'}</span><div style="margin-top:0.2rem;font-style:italic;background:rgba(99,102,241,0.06);border:1px solid rgba(99,102,241,0.15);border-radius:4px;padding:0.4rem 0.6rem;font-size:0.82rem;">${escapeHtml(verdict.ai_explanation)}</div></div>` : ''}
            ${contextLines.length > 0 ? `
            <details style="margin-top:0.25rem;">
                <summary style="cursor:pointer;opacity:0.65;font-size:0.8rem;">${window.t ? window.t('monitor.verdict_context_label') : 'Logs contextuels'} (${contextLines.length} lignes)</summary>
                <pre style="margin-top:0.4rem;background:rgba(0,0,0,0.25);border:1px solid var(--border);border-radius:5px;padding:0.5rem 0.75rem;font-size:0.74rem;line-height:1.5;max-height:200px;overflow-y:auto;white-space:pre-wrap;word-break:break-all;">${contextLines.map(l => escapeHtml(l)).join('\n')}</pre>
            </details>` : ''}
        </div>
        <div style="margin-top:1rem;display:flex;justify-content:flex-end;gap:0.5rem;">
            ${canMarkFp ? `<button class="btn btn-secondary btn-sm" style="color:var(--danger);" onclick="markVerdictFalsePositive(${verdict.id}, ${ruleId}, this); document.getElementById('verdict-detail-modal').classList.add('hidden');">🚫 ${window.t ? window.t('monitor.verdict_mark_fp') : 'Marquer faux-positif'}</button>` : ''}
            <button class="btn btn-secondary btn-sm" onclick="document.getElementById('verdict-detail-modal').classList.add('hidden');">${window.t ? window.t('monitor.resolution_history_close') : 'Fermer'}</button>
        </div>
    `;
}

// ─── Audit IA des patterns (MON-21) ──────────────────────────────────────

async function auditPatternsWithAI(ruleId, btnEl) {
    if (btnEl.disabled) return;
    btnEl.disabled = true;
    const origHtml = btnEl.innerHTML;
    btnEl.innerHTML = `⏳ ${window.t ? window.t('common.loading') : 'Analyse...'}`;

    const resultEl = document.getElementById(`audit-result-${ruleId}`);

    try {
        const data = await apiFetch(`/api/monitor/rules/${ruleId}/audit-patterns`, { method: 'POST' });

        if (resultEl) {
            resultEl.classList.remove('hidden');

            const kept = data.kept || [];
            const removed = data.removed || [];
            const explanation = data.explanation || '';

            let html = `<div style="margin-bottom:0.3rem;font-weight:600;">🧹 ${window.t ? window.t('monitor.audit_result_title') : 'Resultat de l\'audit IA'}</div>`;

            if (removed.length > 0) {
                html += `<div style="margin-bottom:0.2rem;color:var(--danger);">❌ ${window.t ? window.t('monitor.audit_removed') : 'Supprimes'} (${removed.length}): ${removed.map(p => `<code style="background:rgba(239,68,68,0.1);padding:0.05rem 0.25rem;border-radius:3px;">${escapeHtml(p)}</code>`).join(' ')}</div>`;
            }

            if (kept.length > 0) {
                html += `<div style="margin-bottom:0.2rem;color:var(--success);">✅ ${window.t ? window.t('monitor.audit_kept') : 'Conserves'} (${kept.length}): ${kept.map(p => `<code style="background:rgba(16,185,129,0.1);padding:0.05rem 0.25rem;border-radius:3px;">${escapeHtml(p)}</code>`).join(' ')}</div>`;
            }

            if (explanation) {
                html += `<div style="margin-top:0.2rem;font-style:italic;opacity:0.75;">${escapeHtml(explanation)}</div>`;
            }

            if (removed.length === 0 && kept.length === 0) {
                html += `<div style="opacity:0.6;">${window.t ? window.t('monitor.audit_no_change') : 'Aucun changement suggere.'}</div>`;
            }

            resultEl.innerHTML = html;
        }

        // Rafraichir les badges et l'historique
        await loadAndRefreshVerdictSummary(ruleId);

    } catch (e) {
        if (resultEl) {
            resultEl.classList.remove('hidden');
            resultEl.innerHTML = `<span style="color:var(--danger);">❌ ${escapeHtml(e.message)}</span>`;
        }
    } finally {
        btnEl.disabled = false;
        btnEl.innerHTML = origHtml;
    }
}
