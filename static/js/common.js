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
        const cpuAppEl = document.getElementById('stat-cpu-app');
        const cpuSysEl = document.getElementById('stat-cpu-sys');
        const ramEl = document.getElementById('stat-ram');
        const uptimeEl = document.getElementById('stat-uptime');
        
        if (cpuAppEl) cpuAppEl.textContent = `${Math.round(stats.app_cpu)}%`;
        if (cpuSysEl) cpuSysEl.textContent = `${Math.round(stats.sys_cpu)}%`;
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

async function openChat(analysisId, rawPrompt = null, rawResponse = null) {
    try {
        const res = await apiFetch('/chat/api/create', {
            method: 'POST',
            body: { 
                analysis_id: analysisId,
                raw_context_prompt: rawPrompt,
                raw_context_response: rawResponse
            }
        });
        if (res.id) {
            window.location.href = `/chat?id=${res.id}`;
        }
    } catch (e) {
        alert('Erreur lors de la création de la conversation: ' + e.message);
    }
}

async function askQuestion(analysisId, inputEl, contextPrompt = null, contextResponse = null) {
    const question = inputEl.value.trim();
    if (!question) return;

    const historyEl = document.getElementById(`chat-history-${analysisId}`);
    if (!historyEl) return;

    // Ajouter la question utilisateur
    historyEl.innerHTML += `<div class="chat-msg user"><strong>Vous :</strong> ${escapeHtml(question)}</div>`;
    inputEl.value = '';

    const abortController = new AbortController();

    const aiMsgEl = document.createElement('div');
    aiMsgEl.className = 'chat-msg ai';
    aiMsgEl.innerHTML = `<strong>Ollama :</strong> <span class="ai-content">⏳...</span> <button class="btn btn-danger btn-sm stop-chat-btn" style="float: right; padding: 2px 5px; font-size: 0.8rem;">🛑 Arrêter</button>`;
    historyEl.appendChild(aiMsgEl);
    historyEl.scrollTop = historyEl.scrollHeight;
    
    const stopBtn = aiMsgEl.querySelector('.stop-chat-btn');
    const contentSpan = aiMsgEl.querySelector('.ai-content');
    stopBtn.onclick = () => abortController.abort();

    try {
        const res = await apiFetch('/api/monitor/chat', {
            method: 'POST',
            body: {
                analysis_id: analysisId,
                question: question,
                context_prompt: contextPrompt,
                context_response: contextResponse
            },
            signal: abortController.signal
        });

        if (res.status === 'ok') {
            contentSpan.innerHTML = marked.parse(res.response);
        } else {
            contentSpan.innerHTML = `❌ Erreur : ${res.detail}`;
        }
    } catch (e) {
        if (e.name === 'AbortError') {
            contentSpan.innerHTML = `❌ Génération annulée.`;
        } else {
            contentSpan.innerHTML = `❌ Erreur : ${e.message}`;
        }
    } finally {
        if (stopBtn && stopBtn.parentNode) {
            stopBtn.parentNode.removeChild(stopBtn);
        }
    }
    historyEl.scrollTop = historyEl.scrollHeight;
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

/**
 * Surligne les occurrences de mots-clés dans un texte brut.
 * Retourne du HTML sécurisé avec les mots-clés entourés de <mark class="kw-highlight">.
 * @param {string} text - Texte brut à afficher (sera échappé)
 * @param {string[]} keywords - Liste de mots-clés à surligner
 * @returns {string} HTML avec surbrillance
 */
function highlightKeywords(text, keywords) {
    if (!text) return '';
    if (!keywords || keywords.length === 0) return escapeHtml(text);

    // Construire un regex combiné insensible à la casse
    const escaped = keywords
        .filter(k => k && k.trim())
        .map(k => k.trim().replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));

    if (escaped.length === 0) return escapeHtml(text);

    const pattern = new RegExp(`(${escaped.join('|')})`, 'gi');

    // Découper le texte en parties, échapper chacune, wrapper les matches
    return text.split(pattern).map((part, i) => {
        if (i % 2 === 1) {
            // C'est un match — on l'entoure d'un mark
            return `<mark class="kw-highlight">${escapeHtml(part)}</mark>`;
        }
        return escapeHtml(part);
    }).join('');
}
