let activeSeverityFilter = null;

document.addEventListener('DOMContentLoaded', () => {
    loadStats();
    loadRulesStatus();
    loadRecentAnalyses();

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
                alert('Erreur lors de la suppression');
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
            container.innerHTML = '<div class="loading">Aucune analyse récente</div>';
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
                        <button class="btn-icon" onclick="copyAnalysisText(this)" title="Copier l'analyse">
                            <svg viewBox="0 0 24 24" width="14" height="14"><path fill="currentColor" d="M19,21H8V7H19M19,5H8A2,2 0 0,0 6,7V21A2,2 0 0,0 8,23H19A2,2 0 0,0 21,21V7A2,2 0 0,0 19,5M16,1H4A2,2 0 0,0 2,3V17H4V3H16V1Z" /></svg>
                        </button>
                        <button class="btn-icon delete-analysis-btn" onclick="deleteAnalysis(${a.id})" title="Supprimer cette analyse">
                            <svg viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M19,4H15.5L14.5,3H9.5L8.5,4H5V6H19V4M6,19A2,2 0 0,0 8,21H16A2,2 0 0,0 18,19V7H6V19Z" /></svg>
                        </button>
                    </div>
                </div>
                <div class="analysis-line">${escapeHtml(a.triggered_line)}</div>
                <div class="analysis-response markdown-body">${a.ollama_response ? marked.parse(a.ollama_response) : ''}</div>
                <div class="analysis-footer" style="margin-top: 1rem; padding-top: 0.75rem; border-top: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center;">
                    <button class="btn btn-secondary btn-sm" onclick="retryAnalysis(${a.id}, this)">🔄 Ré-essayer</button>
                    <button class="btn btn-primary btn-sm" onclick="openChat(${a.id})">💬 Approfondir avec l'IA</button>
                </div>
            </div>
        `).join('');
    } catch (error) {
        console.error('Erreur chargement analyses récentes:', error);
        document.getElementById('recent-analyses').innerHTML = '<div class="loading">Erreur de chargement</div>';
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
        alert('Erreur lors de la suppression');
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
    btn.innerHTML = '⏳...';
    try {
        const res = await apiFetch(`/api/monitor/retry/${analysisId}`, { method: 'POST' });
        if (res.status === 'ok') {
            loadRecentAnalyses();
            loadStats();
        }
    } catch (e) {
        alert('Erreur: ' + e.message);
    } finally {
        btn.disabled = false;
        btn.innerHTML = oldHtml;
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
        
        if (!container) return;
        if (rules.length === 0) {
            container.innerHTML = '<div class="loading">Aucune règle configurée</div>';
            return;
        }

        container.innerHTML = rules.map(rule => `
            <div class="rule-card">
                <div class="rule-info">
                    <h3 style="margin: 0; font-size: 1rem;">${escapeHtml(rule.name)}</h3>
                    <p style="margin-bottom: 0.5rem; font-size: 0.8rem; opacity: 0.8;">📁 ${escapeHtml(rule.log_file_path)}</p>
                    <div class="rule-last-line" style="margin-bottom: 0;">
                        <strong>Dernière détection :</strong>
                        ${rule.last_detection_id ? `<span class="detection-id-badge" style="font-size: 0.7rem; vertical-align: middle;">#${escapeHtml(rule.last_detection_id)}</span>` : '<span style="font-size: 0.75rem; opacity: 0.6;">Aucune</span>'}
                        <div class="last-line-content" style="font-size: 0.75rem; margin-top: 0.25rem;">${escapeHtml(rule.last_log_line || 'Aucune ligne trouvée ou fichier inaccessible')}</div>
                    </div>
                </div>
            </div>
        `).join('');
    } catch (error) {
        console.error('Erreur chargement état des logs:', error);
        const container = document.getElementById('rules-status-list');
        if (container) {
            container.innerHTML = `<div class="loading" style="color: var(--danger)">Erreur : ${escapeHtml(error.message)}</div>`;
        }
    }
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
