document.addEventListener('DOMContentLoaded', () => {
    loadStats();
    loadRecentAnalyses();

    // Auto-refresh toutes les 30 secondes
    setInterval(() => {
        loadStats();
        loadRecentAnalyses();
    }, 30000);
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
                        <strong>Règle #${a.rule_id}</strong>
                        <span class="analysis-time">${formatDate(a.analyzed_at)}</span>
                    </div>
                    <span class="severity-badge ${escapeHtml(a.severity)}">${escapeHtml(a.severity)}</span>
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