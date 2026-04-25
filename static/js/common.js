// Utilitaires communs
async function apiFetch(url, options = {}) {
    const defaults = {
        headers: {
            'Content-Type': 'application/json',
        },
    };

    const config = { ...defaults, ...options };
    if (config.body && typeof config.body === 'object') {
        config.body = JSON.stringify(config.body);
    }

    const response = await fetch(url, config);
    if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: 'Erreur inconnue' }));
        throw new Error(error.detail || 'Erreur API');
    }
    return response.json();
}

function showMessage(element, message, type = 'success') {
    element.textContent = message;
    element.className = `message ${type}`;
    element.classList.remove('hidden');
    setTimeout(() => element.classList.add('hidden'), 3000);
}

function formatDate(dateString) {
    if (!dateString) return '';
    // Si la chaîne ne finit pas par Z et n'a pas de fuseau horaire, 
    // on ajoute Z pour forcer l'interprétation UTC (format de la DB)
    let utcString = dateString;
    if (!dateString.endsWith('Z') && !dateString.includes('+')) {
        utcString += 'Z';
    }
    
    const date = new Date(utcString);
    return date.toLocaleString('fr-FR', {
        day: '2-digit',
        month: '2-digit',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
    });
}

function escapeHtml(text) {
    if (!text) return '';
    const map = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;'
    };
    return text.toString().replace(/[&<>"']/g, function(m) { return map[m]; });
}

function copyToClipboard(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
        return navigator.clipboard.writeText(text);
    } else {
        // Fallback pour environnements non-sécurisés (HTTP via IP)
        return new Promise((resolve, reject) => {
            try {
                const textArea = document.createElement("textarea");
                textArea.value = text;
                textArea.style.position = "fixed";
                textArea.style.left = "-9999px";
                textArea.style.top = "0";
                document.body.appendChild(textArea);
                textArea.focus();
                textArea.select();
                const successful = document.execCommand('copy');
                document.body.removeChild(textArea);
                if (successful) resolve();
                else reject(new Error('ExecCommand copy failed'));
            } catch (err) {
                reject(err);
            }
        });
    }
}


document.addEventListener('DOMContentLoaded', () => {
    pollSystemStats();
});

async function pollSystemStats() {
    updateSystemStatsUI();
    setInterval(updateSystemStatsUI, 10000);
}

async function updateSystemStatsUI() {
    try {
        const stats = await apiFetch('/api/dashboard/system-stats');
        const cpuEl = document.getElementById('stat-cpu');
        const ramEl = document.getElementById('stat-ram');
        const uptimeEl = document.getElementById('stat-uptime');
        
        if (cpuEl) cpuEl.textContent = `${Math.round(stats.app_cpu)}% / ${Math.round(stats.sys_cpu)}%`;
        if (ramEl) ramEl.textContent = `${stats.app_ram} MB`;
        if (uptimeEl) uptimeEl.textContent = formatUptime(stats.uptime);
    } catch (e) {}
}

function formatUptime(seconds) {
    if (seconds < 60) return `${seconds}s`;
    if (seconds < 3600) return `${Math.floor(seconds/60)}m`;
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    return `${h}h${m}m`;
}
