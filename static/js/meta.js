let allRules = [];

document.addEventListener('DOMContentLoaded', async () => {
    await loadRules();
    await loadConfigs();

    document.getElementById('config-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        await saveConfig();
    });
    
    // Remplir les jours du mois (1-31)
    const daySelect = document.getElementById('config-schedule-day');
    for (let i = 1; i <= 31; i++) {
        const opt = document.createElement('option');
        opt.value = i; opt.textContent = i;
        daySelect.appendChild(opt);
    }
});

function updateScheduleUI() {
    const type = document.getElementById('config-schedule-type').value;
    const container = document.getElementById('schedule-day-container');
    const label = document.getElementById('schedule-day-label');
    const select = document.getElementById('config-schedule-day');
    
    if (type === 'daily') {
        container.style.display = 'none';
    } else if (type === 'weekly') {
        container.style.display = 'block';
        label.textContent = window.t('meta.schedule_weekday');
        select.innerHTML = `
            <option value="1">${window.t('meta.weekday_mon')}</option>
            <option value="2">${window.t('meta.weekday_tue')}</option>
            <option value="3">${window.t('meta.weekday_wed')}</option>
            <option value="4">${window.t('meta.weekday_thu')}</option>
            <option value="5">${window.t('meta.weekday_fri')}</option>
            <option value="6">${window.t('meta.weekday_sat')}</option>
            <option value="7">${window.t('meta.weekday_sun')}</option>
        `;
    } else if (type === 'monthly') {
        container.style.display = 'block';
        label.textContent = window.t('meta.schedule_monthday');
        select.innerHTML = '';
        for (let i = 1; i <= 31; i++) {
            select.innerHTML += `<option value="${i}">${i}</option>`;
        }
    }
}

async function loadRules() {
    try {
        const rules = await apiFetch('/api/rules');
        allRules = rules;
        const list = document.getElementById('config-rules-list');
        list.innerHTML = rules.map(r => `
            <div class="rule-checkbox-item">
                <input type="checkbox" id="chk-rule-${r.id}" value="${r.id}" class="rule-chk">
                <label for="chk-rule-${r.id}" style="margin:0; cursor:pointer;">${escapeHtml(r.name)}</label>
            </div>
        `).join('');
    } catch (e) {
        console.error('Erreur chargement règles:', e);
    }
}

async function loadConfigs() {
    const container = document.getElementById('configs-container');
    container.innerHTML = '<div class="loading">Chargement...</div>';

    try {
        const configs = await apiFetch('/api/meta-analysis/configs');
        
        if (configs.length === 0) {
            container.innerHTML = `<p style="color:var(--text-secondary); text-align:center;">${window.t('meta.no_configs')}</p>`;
            return;
        }

        let html = '';
        for (const c of configs) {
            const rulesText = c.rule_ids_json.length > 0 
                ? `${c.rule_ids_json.length} ${window.t('meta.rules').split('(')[0].trim().toLowerCase()}` 
                : window.t('meta.all_rules');
            const status = c.enabled 
                ? `<span style="color:var(--success);">${window.t('meta.status_active')}</span>` 
                : `<span style="color:var(--danger);">${window.t('meta.status_disabled')}</span>`;
            const lastRunLabel = window.t('meta.last_run');
            const neverLabel = window.t('meta.never');
            const lastRun = c.last_run_at ? new Date(c.last_run_at).toLocaleString() : neverLabel;
            
            let scheduleText = '';
            if (c.schedule_type === 'daily') scheduleText = `${window.t('meta.schedule_daily')} ${window.t('meta.schedule_hour').toLowerCase()} ${c.schedule_time}`;
            else if (c.schedule_type === 'weekly') scheduleText = `${window.t('meta.schedule_weekly')} (${window.t('meta.schedule_weekday')} ${c.schedule_day}) ${window.t('meta.schedule_hour').toLowerCase()} ${c.schedule_time}`;
            else if (c.schedule_type === 'monthly') scheduleText = `${window.t('meta.schedule_monthly')} ${window.t('meta.schedule_monthday').toLowerCase()} ${c.schedule_day} ${window.t('meta.schedule_hour').toLowerCase()} ${c.schedule_time}`;

            html += `
            <div class="card" style="border-left: 4px solid var(${c.enabled ? '--accent' : '--border'}); margin-bottom: 1rem;">
                <div style="padding: 1.5rem;">
                    <div style="display: flex; justify-content: space-between; margin-bottom: 0.5rem;">
                        <h3 style="margin: 0; font-size: 1.2rem;">${escapeHtml(c.name)}</h3>
                        <div>${status}</div>
                    </div>
                    <div style="font-size: 0.9rem; color: var(--text-secondary); margin-bottom: 1rem; display: flex; gap: 1rem; flex-wrap: wrap;">
                        <div>⏱ ${scheduleText}</div>
                        <div>📝 ${rulesText}</div>
                        <div>🕒 ${lastRunLabel} ${lastRun}</div>
                    </div>
                    <div style="display: flex; gap: 0.5rem;">
                        <button class="btn btn-secondary btn-sm" onclick='editConfig(${JSON.stringify(c).replace(/'/g, "&#39;")})'>Modifier</button>
                        <button class="btn btn-secondary btn-sm" style="color:var(--danger); border-color:var(--danger);" onclick="deleteConfig(${c.id})">Supprimer</button>
                    </div>
                </div>
                
                <!-- Accordéons -->
                <div class="accordion-header" onclick="toggleAccordion('preview-${c.id}', this)">
                    <span>${window.t('meta.accordion_preview')}</span>
                    <span class="icon">▼</span>
                </div>
                <div id="preview-${c.id}" class="accordion-content">
                    <p style="font-size: 0.85rem; color: var(--text-secondary); margin-top:0;">${window.t('meta.preview_help')}</p>
                    <div id="txt-preview-${c.id}" class="context-preview-box" contenteditable="true" spellcheck="false">Chargement...</div>
                    <button class="btn btn-primary btn-sm" style="margin-top:0.5rem;" onclick="triggerCustomMeta(${c.id})">${window.t('meta.run_custom')}</button>
                </div>

                <div class="accordion-header" onclick="toggleAccordion('results-${c.id}', this)">
                    <span>${window.t('meta.accordion_results')}</span>
                    <span class="icon">▼</span>
                </div>
                <div id="results-${c.id}" class="accordion-content" style="background: rgba(0,0,0,0.2);">
                    <div class="loading">Dépliez pour charger...</div>
                </div>
            </div>
            `;
        }
        container.innerHTML = html;
    } catch (e) {
        container.innerHTML = `<div style="color:var(--danger)">Erreur: ${e.message}</div>`;
    }
}

function toggleAccordion(id, headerEl) {
    const el = document.getElementById(id);
    const isOpen = el.classList.contains('open');
    
    if (isOpen) {
        el.classList.remove('open');
        headerEl.querySelector('.icon').textContent = '▼';
    } else {
        el.classList.add('open');
        headerEl.querySelector('.icon').textContent = '▲';
        
        if (id.startsWith('preview-')) {
            loadPreview(id.split('-')[1]);
        } else if (id.startsWith('results-')) {
            loadResultsForConfig(id.split('-')[1]);
        }
    }
}

async function loadPreview(configId) {
    const txt = document.getElementById(`txt-preview-${configId}`);
    txt.textContent = 'Chargement des événements...';
    try {
        const res = await apiFetch(`/api/meta-analysis/trigger/preview/${configId}`);
        txt.textContent = res.context;
        if (res.matched_keywords && res.matched_keywords.length > 0) {
            highlightDOMText(txt, res.matched_keywords);
        }
    } catch (e) {
        txt.textContent = 'Erreur: ' + e.message;
    }
}

async function loadResultsForConfig(configId) {
    const container = document.getElementById(`results-${configId}`);
    container.innerHTML = '<div class="loading">Chargement...</div>';
    try {
        const results = await apiFetch(`/api/meta-analysis/results?config_id=${configId}`);
        if (results.length === 0) {
            container.innerHTML = `<p style="color:var(--text-secondary);">${window.t('meta.no_results')}</p>`;
            return;
        }

        container.innerHTML = results.map(r => {
            const start = new Date(r.period_start).toLocaleString();
            const end = new Date(r.period_end).toLocaleString();
            
            let idsHtml = '';
            if (r.detection_ids && r.detection_ids.length > 0) {
                idsHtml = `<div style="margin-top: 0.5rem; display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center;">
                    <strong style="font-size: 0.85rem; color: var(--text-secondary);">${window.t('meta.sources')}</strong>
                    ${r.detection_ids.map(id => `<a href="/monitor?search=${encodeURIComponent(id)}" class="btn btn-secondary btn-sm" style="padding: 0.2rem 0.5rem; font-size: 0.75rem;">🔍 #${id}</a>`).join('')}
                </div>`;
            }

            let kwHtml = '';
            if (r.matched_keywords && r.matched_keywords.length > 0) {
                kwHtml = `<div style="margin-top: 0.5rem; display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center;">
                    <strong style="font-size: 0.85rem; color: var(--text-secondary);">${window.t('meta.keywords_label')}</strong>
                    ${r.matched_keywords.map(kw => `<span class="log-kw-badge">${escapeHtml(kw)}</span>`).join('')}
                </div>`;
            }

            return `
            <div class="card meta-result-card" style="padding: 1.5rem; margin-bottom:1rem; border: 1px solid var(--border);">
                <div style="margin-bottom: 1rem; font-size: 0.9rem; background: rgba(255,255,255,0.05); padding: 0.5rem; border-radius: 4px;">
                    <strong>Période :</strong> ${start} - ${end}<br>
                    <strong>Événements analysés :</strong> ${r.analyses_count}
                    ${kwHtml}
                    ${idsHtml}
                </div>
                <div class="markdown-body" style="font-size: 0.95rem; background: rgba(0,0,0,0.3); padding: 1rem; border-radius: 4px;">
                    ${marked.parse(r.ollama_response || '')}
                </div>
            </div>
            `;
        }).join('');

        setTimeout(() => {
            container.querySelectorAll('.meta-result-card').forEach((card, idx) => {
                const r = results[idx];
                if (r.matched_keywords && r.matched_keywords.length > 0) {
                    const body = card.querySelector('.markdown-body');
                    highlightDOMText(body, r.matched_keywords);
                }
            });
        }, 10);
    } catch(e) {
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
        if (node.parentNode.nodeName === 'MARK') return; 
        if (node.parentNode.nodeName === 'CODE' || node.parentNode.nodeName === 'PRE') return; 
        
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
    
    // Mettre le prompt par défaut traduit
    document.getElementById('config-prompt').value = window.t('meta.default_prompt') || 'Tu es un expert DevOps. Analyse ces événements et fais une synthèse globale de la situation du service.';
    
    // Mettre à jour les options de planification selon la langue
    const typeSelect = document.getElementById('config-schedule-type');
    typeSelect.options[0].text = window.t('meta.schedule_daily');
    typeSelect.options[1].text = window.t('meta.schedule_weekly');
    typeSelect.options[2].text = window.t('meta.schedule_monthly');
    
    // Mettre à jour les options de contexte selon la langue
    const ctxSelect = document.getElementById('config-context');
    const ctxKeys = ['ctx_quick', 'ctx_standard', 'ctx_large', 'ctx_xlarge', 'ctx_massive'];
    ctxKeys.forEach((k, i) => { if (ctxSelect.options[i]) ctxSelect.options[i].text = window.t(`meta.${k}`); });
    
    updateScheduleUI();
    
    document.getElementById('modal-title').textContent = window.t('meta.modal_title_new') || 'Nouvelle Configuration';
    document.getElementById('config-modal').classList.remove('hidden');
    document.getElementById('config-modal').style.display = 'flex';
}

function closeConfigModal() {
    document.getElementById('config-modal').classList.add('hidden');
    document.getElementById('config-modal').style.display = 'none';
}

function editConfig(config) {
    document.getElementById('config-id').value = config.id;
    document.getElementById('config-name').value = config.name;
    
    document.getElementById('config-schedule-type').value = config.schedule_type || 'daily';
    updateScheduleUI();
    document.getElementById('config-schedule-day').value = config.schedule_day || 1;
    document.getElementById('config-schedule-time').value = config.schedule_time || '00:00';
    
    document.getElementById('config-context').value = config.context_size;
    document.getElementById('config-max').value = config.max_analyses;
    document.getElementById('config-prompt').value = config.system_prompt;
    document.getElementById('config-enabled').checked = config.enabled;
    document.getElementById('config-notify').checked = config.notify_enabled;

    const ruleIds = config.rule_ids_json || [];
    document.querySelectorAll('.rule-chk').forEach(chk => {
        chk.checked = ruleIds.includes(parseInt(chk.value));
    });

    document.getElementById('modal-title').textContent = window.t ? window.t('meta.modal_title_edit') || 'Modifier Configuration' : 'Modifier Configuration';
    document.getElementById('config-modal').style.display = 'flex';
}

async function saveConfig() {
    const id = document.getElementById('config-id').value;
    const ruleIds = Array.from(document.querySelectorAll('.rule-chk:checked')).map(chk => parseInt(chk.value));

    const payload = {
        name: document.getElementById('config-name').value,
        rule_ids: ruleIds,
        schedule_type: document.getElementById('config-schedule-type').value,
        schedule_day: document.getElementById('config-schedule-day').value,
        schedule_time: document.getElementById('config-schedule-time').value,
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

async function triggerCustomMeta(id) {
    if (!confirm("Lancer la méta-analyse avec ce contexte ? (S'exécute en arrière-plan)")) return;
    const txt = document.getElementById(`txt-preview-${id}`).innerText;
    try {
        await apiFetch(`/api/meta-analysis/trigger/${id}`, { 
            method: 'POST',
            body: { custom_context: txt }
        });
        alert('Méta-analyse lancée. Le résultat apparaîtra dans les historiques bientôt (rechargez le résultat).');
    } catch (e) {
        alert('Erreur: ' + e.message);
    }
}

window.onclick = function(event) {
    const modal = document.getElementById('config-modal');
    if (event.target == modal) {
        closeConfigModal();
    }
}
