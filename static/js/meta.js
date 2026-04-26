let allRules = [];

document.addEventListener('DOMContentLoaded', async () => {
    await loadRules();
    await loadConfigs();
    await loadResults();

    document.getElementById('config-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        await saveConfig();
    });
});

async function loadRules() {
    try {
        const rules = await apiFetch('/api/rules');
        allRules = rules;
        const select = document.getElementById('config-rules');
        select.innerHTML = rules.map(r => `<option value="${r.id}">${escapeHtml(r.name)}</option>`).join('');
    } catch (e) {
        console.error('Erreur chargement règles:', e);
    }
}

async function loadConfigs() {
    const container = document.getElementById('configs-container');
    const filterSelect = document.getElementById('filter-config');
    container.innerHTML = '<div class="loading">Chargement...</div>';

    try {
        const configs = await apiFetch('/api/meta-analysis/configs');
        
        // Update filter select
        const currentFilter = filterSelect.value;
        filterSelect.innerHTML = `<option value="">-- Toutes les configurations --</option>` + 
            configs.map(c => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join('');
        if (configs.find(c => c.id == currentFilter)) filterSelect.value = currentFilter;

        if (configs.length === 0) {
            container.innerHTML = `<p style="color:var(--text-secondary); text-align:center;">Aucune configuration.</p>`;
            return;
        }

        container.innerHTML = configs.map(c => {
            const rulesText = c.rule_ids_json.length > 0 
                ? `${c.rule_ids_json.length} règle(s) cible(s)` 
                : 'Toutes les règles';
            const status = c.enabled ? '<span style="color:var(--success);">Actif</span>' : '<span style="color:var(--danger);">Désactivé</span>';
            const lastRun = c.last_run_at ? new Date(c.last_run_at).toLocaleString() : 'Jamais';

            return `
            <div class="card" style="padding: 1rem; border-left: 4px solid var(${c.enabled ? '--accent' : '--border'});">
                <div style="display: flex; justify-content: space-between; margin-bottom: 0.5rem;">
                    <h3 style="margin: 0; font-size: 1.1rem;">${escapeHtml(c.name)}</h3>
                    <div>${status}</div>
                </div>
                <div style="font-size: 0.9rem; color: var(--text-secondary); margin-bottom: 1rem;">
                    <div>⏱ Tous les ${c.interval_hours}h | 📝 ${rulesText}</div>
                    <div>Dernier run: ${lastRun}</div>
                </div>
                <div style="display: flex; gap: 0.5rem; justify-content: flex-end;">
                    <button class="btn btn-secondary btn-sm" onclick='editConfig(${JSON.stringify(c).replace(/'/g, "&#39;")})'>Modifier</button>
                    <button class="btn btn-secondary btn-sm" onclick="triggerMeta(${c.id})">▶ Lancer</button>
                    <button class="btn btn-secondary btn-sm" style="color:var(--danger); border-color:var(--danger);" onclick="deleteConfig(${c.id})">Supprimer</button>
                </div>
            </div>
            `;
        }).join('');
    } catch (e) {
        container.innerHTML = `<div style="color:var(--danger)">Erreur: ${e.message}</div>`;
    }
}

async function loadResults() {
    const container = document.getElementById('results-container');
    const filterId = document.getElementById('filter-config').value;
    container.innerHTML = '<div class="loading">Chargement...</div>';

    try {
        let url = '/api/meta-analysis/results';
        if (filterId) url += `?config_id=${filterId}`;
        
        const results = await apiFetch(url);

        if (results.length === 0) {
            container.innerHTML = `<p style="color:var(--text-secondary); text-align:center;">Aucun résultat.</p>`;
            return;
        }

        container.innerHTML = results.map(r => {
            const start = new Date(r.period_start).toLocaleString();
            const end = new Date(r.period_end).toLocaleString();
            const created = new Date(r.created_at).toLocaleString();
            
            let idsHtml = '';
            if (r.detection_ids && r.detection_ids.length > 0) {
                idsHtml = `<div style="margin-top: 0.5rem; display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center;">
                    <strong style="font-size: 0.85rem; color: var(--text-secondary);">Analyses sources :</strong>
                    ${r.detection_ids.map(id => `<a href="/monitor?search=${encodeURIComponent(id)}" class="btn btn-secondary btn-sm" style="padding: 0.2rem 0.5rem; font-size: 0.75rem;">🔍 #${id}</a>`).join('')}
                </div>`;
            }

            let kwHtml = '';
            if (r.matched_keywords && r.matched_keywords.length > 0) {
                kwHtml = `<div style="margin-top: 0.5rem; display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center;">
                    <strong style="font-size: 0.85rem; color: var(--text-secondary);">Mots-clés déclencheurs :</strong>
                    ${r.matched_keywords.map(kw => `<span class="log-kw-badge">${escapeHtml(kw)}</span>`).join('')}
                </div>`;
            }

            return `
            <div class="card meta-result-card" style="padding: 1.5rem;">
                <div style="display: flex; justify-content: space-between; margin-bottom: 1rem; border-bottom: 1px solid var(--border); padding-bottom: 0.5rem;">
                    <h3 style="margin: 0;">${escapeHtml(r.config_name)}</h3>
                    <span style="color:var(--text-secondary); font-size: 0.9rem;">${created}</span>
                </div>
                <div style="margin-bottom: 1rem; font-size: 0.9rem; background: rgba(255,255,255,0.05); padding: 0.5rem; border-radius: 4px;">
                    <strong>Période :</strong> ${start} - ${end}<br>
                    <strong>Événements analysés :</strong> ${r.analyses_count}
                    ${kwHtml}
                    ${idsHtml}
                </div>
                <div class="markdown-body" style="font-size: 0.95rem;">
                    ${marked.parse(r.ollama_response || '')}
                </div>
            </div>
            `;
        }).join('');

        // Appliquer la surbrillance sur le texte rendu (TreeWalker pour ne pas casser le HTML)
        setTimeout(() => {
            document.querySelectorAll('.meta-result-card').forEach((card, idx) => {
                const r = results[idx];
                if (r.matched_keywords && r.matched_keywords.length > 0) {
                    const body = card.querySelector('.markdown-body');
                    highlightDOMText(body, r.matched_keywords);
                }
            });
        }, 10);
    } catch (e) {
        container.innerHTML = `<div style="color:var(--danger)">Erreur: ${e.message}</div>`;
    }
}

function highlightDOMText(element, keywords) {
    if (!keywords || keywords.length === 0) return;
    const escaped = keywords.filter(k => k && k.trim()).map(k => k.trim().replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
    if (escaped.length === 0) return;
    const pattern = new RegExp(`(${escaped.join('|')})`, 'gi');
    
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT, null, false);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    
    nodes.forEach(node => {
        if (node.parentNode.nodeName === 'MARK') return; // already highlighted
        if (node.parentNode.nodeName === 'CODE' || node.parentNode.nodeName === 'PRE') return; // éviter de casser le code
        
        const text = node.nodeValue;
        if (pattern.test(text)) {
            const span = document.createElement('span');
            span.innerHTML = text.replace(pattern, '<mark class="kw-highlight">$1</mark>');
            node.parentNode.replaceChild(span, node);
        }
    });
}

function openConfigModal() {
    document.getElementById('config-form').reset();
    document.getElementById('config-id').value = '';
    
    // Reset selection
    Array.from(document.getElementById('config-rules').options).forEach(opt => opt.selected = false);
    
    document.getElementById('modal-title').textContent = window.t ? window.t('meta.modal_title_new') || 'Nouvelle Configuration' : 'Nouvelle Configuration';
    document.getElementById('config-modal').style.display = 'block';
}

function closeConfigModal() {
    document.getElementById('config-modal').style.display = 'none';
}

function editConfig(config) {
    document.getElementById('config-id').value = config.id;
    document.getElementById('config-name').value = config.name;
    document.getElementById('config-interval').value = config.interval_hours;
    document.getElementById('config-context').value = config.context_size;
    document.getElementById('config-max').value = config.max_analyses;
    document.getElementById('config-prompt').value = config.system_prompt;
    document.getElementById('config-enabled').checked = config.enabled;
    document.getElementById('config-notify').checked = config.notify_enabled;

    // Set selection
    const ruleIds = config.rule_ids_json || [];
    Array.from(document.getElementById('config-rules').options).forEach(opt => {
        opt.selected = ruleIds.includes(parseInt(opt.value));
    });

    document.getElementById('modal-title').textContent = window.t ? window.t('meta.modal_title_edit') || 'Modifier Configuration' : 'Modifier Configuration';
    document.getElementById('config-modal').style.display = 'block';
}

async function saveConfig() {
    const id = document.getElementById('config-id').value;
    
    // Get selected rules
    const select = document.getElementById('config-rules');
    const ruleIds = Array.from(select.selectedOptions).map(opt => parseInt(opt.value));

    const payload = {
        name: document.getElementById('config-name').value,
        rule_ids: ruleIds,
        interval_hours: parseInt(document.getElementById('config-interval').value),
        context_size: parseInt(document.getElementById('config-context').value),
        max_analyses: parseInt(document.getElementById('config-max').value),
        system_prompt: document.getElementById('config-prompt').value,
        enabled: document.getElementById('config-enabled').checked,
        notify_enabled: document.getElementById('config-notify').checked
    };

    try {
        const url = id ? `/api/meta-analysis/configs/${id}` : '/api/meta-analysis/configs';
        const method = id ? 'PUT' : 'POST';
        
        await apiFetch(url, { method, body: payload });
        closeConfigModal();
        await loadConfigs();
    } catch (e) {
        alert('Erreur: ' + e.message);
    }
}

async function deleteConfig(id) {
    if (!confirm('Voulez-vous vraiment supprimer cette configuration ?')) return;
    try {
        await apiFetch(`/api/meta-analysis/configs/${id}`, { method: 'DELETE' });
        await loadConfigs();
    } catch (e) {
        alert('Erreur: ' + e.message);
    }
}

async function triggerMeta(id) {
    if (!confirm('Lancer la méta-analyse maintenant ? (S\'exécute en arrière-plan)')) return;
    try {
        await apiFetch(`/api/meta-analysis/trigger/${id}`, { method: 'POST' });
        alert('Méta-analyse lancée. Le résultat apparaîtra dans quelques minutes.');
        // Recharge les résultats d'ici 10s pour espérer l'avoir, sinon il faudra rafraichir.
        setTimeout(loadResults, 10000);
    } catch (e) {
        alert('Erreur: ' + e.message);
    }
}

// Fermer la modal en cliquant en dehors
window.onclick = function(event) {
    const modal = document.getElementById('config-modal');
    if (event.target == modal) {
        closeConfigModal();
    }
}
