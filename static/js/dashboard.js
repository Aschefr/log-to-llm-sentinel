let activeSeverityFilter = null;

document.addEventListener('DOMContentLoaded', () => {
    loadStats();
    loadRulesStatus();
    loadRecentAnalyses();

    // Re-render dynamic content on language change
    window.i18n?.onLanguageChange(() => {
        loadRecentAnalyses();
        loadRulesStatus();
    });

    // Auto-refresh toutes les 30 secondes
    setInterval(() => {
        loadStats();
        loadRulesStatus();
        loadRecentAnalyses();
    }, 30000);

    // Filtres par sévérité
    document.querySelectorAll('.severity-card.clickable').forEach(card => {
        card.addEventListener('click', () => {
            const sev = card.getAttribute('data-severity');
            setSeverityFilter(sev);
        });
    });

    const clearAllBtn = document.getElementById('clear-all-analyses-btn');
    if (clearAllBtn) {
        clearAllBtn.addEventListener('click', async () => {
            if (!confirm('Voulez-vous vraiment effacer TOUTES les analyses du système ?')) return;
            try {
                await apiFetch('/api/dashboard/analyses/all/confirm', { method: 'DELETE' });
                loadStats();
                loadRecentAnalyses();
            } catch (error) {
                console.error('Erreur suppression:', error);
                alert(window.t ? window.t('common.error') : 'Erreur lors de la suppression');
            }
        });
    }
});

async function loadStats() {
    try {
        const stats = await apiFetch('/api/dashboard/stats');
        document.getElementById('total-rules').textContent = stats.total_rules;
        document.getElementById('active-rules').textContent = stats.active_rules;
        document.getElementById('total-analyses').textContent = stats.total_analyses;
        document.getElementById('today-analyses').textContent = stats.today_analyses;
        
        document.getElementById('critical-count').textContent = stats.critical_count;
        document.getElementById('warning-count').textContent = stats.warning_count;
        document.getElementById('info-count').textContent = stats.info_count;
    } catch (error) {
        console.error('Erreur chargement stats:', error);
    }
}

async function loadRecentAnalyses() {
    try {
        let url = '/api/dashboard/recent?limit=20';
        if (activeSeverityFilter) {
            url += `&severity=${activeSeverityFilter}`;
        }
        const analyses = await apiFetch(url);
        const container = document.getElementById('recent-analyses');
        
        if (analyses.length === 0) {
            container.innerHTML = `<div class="loading">${window.t('dashboard.no_recent_analysis')}</div>`;
            return;
        }

        container.innerHTML = analyses.map(a => `
            <div class="analysis-card">
                <div class="analysis-header">
                    <div>
                        <strong>Règle: ${escapeHtml(a.rule_name || 'Règle #' + a.rule_id)}</strong>
                        ${a.detection_id ? `<span class="detection-id-badge" style="margin-left: 0.75rem;">#${escapeHtml(a.detection_id)}</span>` : ''}
                        <span class="analysis-time">${formatDate(a.analyzed_at)}</span>
                    </div>
                    <div class="analysis-actions">
                        <span class="severity-badge ${escapeHtml(a.severity)}">${escapeHtml(a.severity)}</span>
                        <button class="btn-icon" onclick="copyAnalysisText(this)" title="${window.t('common.copy_analysis')}">
                            <svg viewBox="0 0 24 24" width="14" height="14"><path fill="currentColor" d="M19,21H8V7H19M19,5H8A2,2 0 0,0 6,7V21A2,2 0 0,0 8,23H19A2,2 0 0,0 21,21V7A2,2 0 0,0 19,5M16,1H4A2,2 0 0,0 2,3V17H4V3H16V1Z" /></svg>
                        </button>
                        <button class="btn-icon delete-analysis-btn" onclick="deleteAnalysis(${a.id})" title="${window.t('common.delete_analysis')}">
                            <svg viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M19,4H15.5L14.5,3H9.5L8.5,4H5V6H19V4M6,19A2,2 0 0,0 8,21H16A2,2 0 0,0 18,19V7H6V19Z" /></svg>
                        </button>
                    </div>
                </div>
                ${a.matched_keywords && a.matched_keywords.length > 0 ? `
                <div class="analysis-keywords">
                    <span class="kw-label">${window.t('monitor.keywords') || 'Mots-clés'} :</span>
                    ${a.matched_keywords.map(k => `<span class="log-kw-badge">${escapeHtml(k)}</span>`).join(' ')}
                </div>` : ''}
                <div class="analysis-line">${highlightKeywords(a.triggered_line, a.matched_keywords || [])}</div>
                <div class="analysis-response markdown-body">${a.ollama_response ? marked.parse(a.ollama_response) : ''}</div>
                <div class="analysis-footer" style="margin-top: 1rem; padding-top: 0.75rem; border-top: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center;">
                    <div style="display: flex; gap: 0.5rem;">
                        <button class="btn btn-secondary btn-sm" onclick="retryAnalysis(${a.id}, this)">${window.t('common.retry')}</button>
                        <button class="btn btn-secondary btn-sm" onclick="notifyAnalysis(${a.id}, this)">${window.t('common.notify')}</button>
                        ${a.detection_id ? `<button class="btn btn-secondary btn-sm" onclick="window.location.href='/monitor?search=${encodeURIComponent(a.detection_id)}'" title="${window.t('common.view_in_monitor') || 'Voir dans Monitor'}">🔍 Monitor</button>` : ''}
                    </div>
                    <button class="btn btn-primary btn-sm" onclick="openChat(${a.id})">${window.t('common.deepen')}</button>
                </div>
            </div>
        `).join('');
    } catch (error) {
        console.error('Erreur chargement analyses récentes:', error);
        document.getElementById('recent-analyses').innerHTML = `<div class="loading">${window.t('dashboard.loading_error')}</div>`;
    }
}

async function deleteAnalysis(id) {
    if (!confirm('Supprimer cette analyse ?')) return;
    try {
        await apiFetch(`/api/dashboard/analyses/${id}`, { method: 'DELETE' });
        loadStats();
        loadRecentAnalyses();
    } catch (error) {
        console.error('Erreur suppression:', error);
        alert(window.t ? window.t('common.error') : 'Erreur lors de la suppression');
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

async function retryAnalysis(analysisId, btn) {
    const oldHtml = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `🔄 ${window.t('common.analyzing')}`;
    try {
        const res = await apiFetch(`/api/monitor/retry/${analysisId}`, { method: 'POST' });
        if (!res.task_id) throw new Error('Réponse inattendue du serveur');

        pollTask(res.task_id, btn, oldHtml, () => {
            loadRecentAnalyses();
            loadStats();
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
 */
function pollTask(taskId, btn, originalHtml, onDone) {
    const maxAttempts = 150;
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
                alert(window.t('common.error') + ': ' + (res.error || 'Erreur inconnue'));
            }
        } catch (_) {}
    }, 2000);
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

function setSeverityFilter(severity) {
    activeSeverityFilter = severity;
    
    // UI Update
    document.querySelectorAll('.severity-card').forEach(c => c.classList.remove('active'));
    const badge = document.getElementById('active-filter-label');
    
    if (severity) {
        document.querySelector(`.severity-card.${severity}`).classList.add('active');
        badge.classList.remove('hidden');
        badge.className = `filter-badge ${severity}`; // Apply color class
        document.getElementById('filter-name').textContent = severity.toUpperCase();
    } else {
        badge.classList.add('hidden');
    }
    
    loadRecentAnalyses();
}

async function loadRulesStatus() {
    console.log("Appel loadRulesStatus...");
    try {
        const rules = await apiFetch('/api/rules');
        console.log("Règles reçues:", rules);
        const container = document.getElementById('rules-status-list');
        
        if (rules.length === 0) {
            container.innerHTML = `<div class="loading">${window.t('dashboard.no_rule')}</div>`;
            return;
        }

        container.innerHTML = rules.map(rule => `
            <div class="rule-card">
                <div class="rule-info">
                    <h3 style="margin: 0; font-size: 1rem;">${escapeHtml(rule.name)}</h3>
                    <p style="margin-bottom: 0.5rem; font-size: 0.8rem; opacity: 0.8;">📁 ${escapeHtml(rule.log_file_path)}</p>
                    <div class="rule-last-line" style="margin-bottom: 0;">
                        <strong data-i18n="dashboard.last_detection">${window.t('dashboard.last_detection')}</strong>
                        ${rule.last_detection_id ? `<span class="detection-id-badge" style="font-size: 0.7rem; vertical-align: middle;">#${escapeHtml(rule.last_detection_id)}</span>` : `<span style="font-size: 0.75rem; opacity: 0.6;">${window.t('dashboard.no_detection')}</span>`}
                        <div class="last-line-content" style="font-size: 0.75rem; margin-top: 0.25rem;">${escapeHtml(rule.last_log_line || window.t('dashboard.no_line_found'))}</div>
                    </div>
                </div>
            </div>
        `).join('');
    } catch (error) {
        console.error('Erreur chargement état des logs:', error);
        const container = document.getElementById('rules-status-list');
        if (container) {
            container.innerHTML = `<div class="loading" style="color: var(--danger)">${window.t ? window.t('common.error') : 'Erreur'} : ${escapeHtml(error.message)}</div>`;
        }
    }
}
