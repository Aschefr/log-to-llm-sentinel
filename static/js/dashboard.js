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
        document.getElementById('total-an