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
        const analyses = await apiFetch('/api/dashboard/recent');
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
                        <span class="analysis-time">${formatDate(a.analyzed_at)}</span>
                    </div>
                    <div class="analysis-actions">
                        <span class="severity-badge ${escapeHtml(a.severity)}">${escapeHtml(a.severity)}</span>
                        <button class="btn-icon delete-analysis-btn" onclick="deleteAnalysis(${a.id})" title="Supprimer cette analyse">
                            <svg viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M19,4H15.5L14.5,3H9.5L8.5,4H5V6H19V4M6,19A2,2 0 0,0 8,21H16A2,2 0 0,0 18,19V7H6V19Z" /></svg>
                        </button>
                    </div>
                </div>
                <div class="analysis-line">${escapeHtml(a.triggered_line)}</div>
                <div class="analysis-response markdown-body">${a.ollama_response ? marked.parse(a.ollama_response) : ''}</div>
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

async function loadRulesStatus() {
    try {
        const rules = await apiFetch('/api/rules');
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
                        <strong>Dernière ligne détectée :</strong>
                        <div class="last-line-content" style="font-size: 0.75rem;">${escapeHtml(rule.last_log_line || 'Aucune ligne trouvée ou fichier inaccessible')}</div>
                    </div>
                </div>
            </div>
        `).join('');
    } catch (error) {
        console.error('Erreur chargement état des logs:', error);
    }
}