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
        clearAllBtn.addEventListener('click', async (e) => {
            showInlineConfirm(clearAllBtn, window.t ? window.t('dashboard.confirm_clear_all') : 'Are you sure you want to delete ALL analyses?', async () => {
                try {
                    await apiFetch('/api/dashboard/analyses/all/confirm', { method: 'DELETE' });
                    loadStats();
                    loadRecentAnalyses();
                } catch (error) {
                    console.error('Erreur suppression:', error);
                    alert(window.t ? window.t('common.error') : 'Erreur lors de la suppression');
                }
            });
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

let dashboardAnalysesOffset = 0;
const DASHBOARD_PAGE_SIZE = 20;

async function loadRecentAnalyses(append = false) {
    try {
        if (!append) dashboardAnalysesOffset = 0;

        let url = `/api/dashboard/recent?limit=${DASHBOARD_PAGE_SIZE}&offset=${dashboardAnalysesOffset}`;
        if (activeSeverityFilter) {
            url += `&severity=${activeSeverityFilter}`;
        }
        const res = await apiFetch(url);
        const analyses = res.analyses || res;
        const hasMore = res.has_more || false;
        const container = document.getElementById('recent-analyses');

        if (!append && analyses.length === 0) {
            container.innerHTML = `<div class="loading">${window.t('dashboard.no_recent_analysis')}</div>`;
            return;
        }

        const html = analyses.map((a, idx) => {
            const isFirst = !append && idx === 0;
            return renderAnalysisCard(a, {
                collapsed: !isFirst,
                showDelete: true,
                showCopy: true,
                showRuleName: true,
            });
        }).join('');

        if (append) {
            const oldBtn = container.querySelector('.dashboard-show-more');
            if (oldBtn) oldBtn.remove();
            container.insertAdjacentHTML('beforeend', html);
        } else {
            container.innerHTML = html;
        }

        if (hasMore) {
            const oldBtn = container.querySelector('.dashboard-show-more');
            if (oldBtn) oldBtn.remove();
            container.insertAdjacentHTML('beforeend', `
                <button class="btn btn-secondary dashboard-show-more monitor-show-more" onclick="loadMoreDashboardAnalyses()">
                    📜 ${window.t ? window.t('monitor.show_more') : 'Show more'}
                </button>
            `);
        }
    } catch (error) {
        console.error('Erreur chargement analyses récentes:', error);
        document.getElementById('recent-analyses').innerHTML = `<div class="loading">${window.t('dashboard.loading_error')}</div>`;
    }
}

function loadMoreDashboardAnalyses() {
    dashboardAnalysesOffset += DASHBOARD_PAGE_SIZE;
    loadRecentAnalyses(true);
}

async function deleteAnalysis(id, btnElement) {
    showInlineConfirm(btnElement, 'Supprimer cette analyse ?', async () => {
        try {
            await apiFetch(`/api/dashboard/analyses/${id}`, { method: 'DELETE' });
            loadStats();
            loadRecentAnalyses();
        } catch (error) {
            console.error('Erreur suppression:', error);
            alert(window.t ? window.t('common.error') : 'Erreur lors de la suppression');
        }
    });
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
        if (!res.task_id) throw new Error(window.t ? window.t('monitor.unexpected_server_response') : 'Unexpected server response');

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
                    <div class="rule-last-line" style="margin-bottom: 0.5rem;">
                        <strong style="font-size:0.75rem; opacity:0.7; text-transform:uppercase; letter-spacing:0.05em;">${window.t('dashboard.live_log_line')}</strong>
                        <div class="last-line-content" style="font-size:0.75rem; margin-top:0.2rem; opacity:0.85;">${escapeHtml(rule.last_log_line || window.t('dashboard.no_line_found'))}</div>
                    </div>
                    ${rule.last_detection_id ? `
                    <div style="margin-top:0.5rem; padding:0.5rem 0.6rem; background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.07); border-radius:6px;">
                        <div style="display:flex; align-items:center; gap:0.5rem; flex-wrap:wrap; margin-bottom:0.35rem;">
                            <strong style="font-size:0.75rem; opacity:0.7; text-transform:uppercase; letter-spacing:0.05em;">${window.t('dashboard.last_trigger')}</strong>
                            ${rule.last_analysis_severity ? `<span class="severity-badge ${rule.last_analysis_severity}" style="font-size:0.65rem; padding:0.1rem 0.4rem;">${rule.last_analysis_severity.toUpperCase()}</span>` : ''}
                            ${rule.last_analysis_at ? `<span style="font-size:0.7rem; opacity:0.55;">${new Date(rule.last_analysis_at).toLocaleString()}</span>` : ''}
                            <a href="/monitor?search=${encodeURIComponent(rule.last_detection_id)}" style="margin-left:auto; background:rgba(99,102,241,0.15); border:1px solid rgba(99,102,241,0.4); color:var(--accent); border-radius:4px; padding:0.1rem 0.5rem; font-size:0.7rem; text-decoration:none; font-family:monospace; transition:background 0.2s;" onmouseover="this.style.background='rgba(99,102,241,0.3)'" onmouseout="this.style.background='rgba(99,102,241,0.15)'">#${escapeHtml(rule.last_detection_id)} ↗</a>
                        </div>
                        <div class="last-line-content" style="font-size:0.72rem; opacity:0.75; word-break:break-all;">${escapeHtml(rule.last_triggered_line || '')}</div>
                    </div>` : `<div style="font-size:0.75rem; opacity:0.45; margin-top:0.3rem;">${window.t('dashboard.no_detection')}</div>`}
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
