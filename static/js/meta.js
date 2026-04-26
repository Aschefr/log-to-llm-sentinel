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

    // Re-rendre le contenu dynamique lors d'un changement de langue
    window.i18n?.onLanguageChange(() => {
        loadConfigs();
    });
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

/**
 * Calcule la prochaine ex\u00e9cution planifi\u00e9e et la plage temporelle correspondante.
 * Retourne { nextRun: Date, periodStart: Date, periodEnd: Date }
 */
function _computeNextRun(c) {
    const now = new Date();
    const [h, m] = (c.schedule_time || '00:00').split(':').map(Number);

    let nextRun;

    if (c.schedule_type === 'daily') {
        nextRun = new Date(now);
        nextRun.setHours(h, m, 0, 0);
        if (nextRun <= now) nextRun.setDate(nextRun.getDate() + 1);

    } else if (c.schedule_type === 'weekly') {
        // schedule_day : 1=lundi \u2026 7=dimanche (ISO)
        const targetDow = ((c.schedule_day || 1) % 7); // JS: 0=dim, 1=lun...
        nextRun = new Date(now);
        nextRun.setHours(h, m, 0, 0);
        const curDow = now.getDay(); // 0=dim
        let diff = targetDow - curDow;
        if (diff < 0 || (diff === 0 && nextRun <= now)) diff += 7;
        nextRun.setDate(nextRun.getDate() + diff);

    } else { // monthly
        const targetDay = c.schedule_day || 1;
        nextRun = new Date(now.getFullYear(), now.getMonth(), targetDay, h, m, 0, 0);
        if (nextRun <= now) {
            nextRun = new Date(now.getFullYear(), now.getMonth() + 1, targetDay, h, m, 0, 0);
        }
    }

    // period_end = l'heure planifi\u00e9e de nextRun (au :00 pr\u00e8s)
    const periodEnd = new Date(nextRun);

    // period_start = last_run_at, ou nextRun - delta par d\u00e9faut
    let periodStart;
    if (c.last_run_at) {
        periodStart = new Date(c.last_run_at);
    } else {
        periodStart = new Date(periodEnd);
        if (c.schedule_type === 'weekly') periodStart.setDate(periodStart.getDate() - 7);
        else if (c.schedule_type === 'monthly') periodStart.setDate(periodStart.getDate() - 30);
        else periodStart.setDate(periodStart.getDate() - 1);
    }

    return { nextRun, periodStart, periodEnd };
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
            
            // Calcul prochaine exécution (heure locale du navigateur)
            const _nr = c.enabled ? _computeNextRun(c) : null;
            const _fmtDt = d => d.toLocaleString(undefined, {day:'2-digit', month:'2-digit', year:'numeric', hour:'2-digit', minute:'2-digit'});
            const nextRunHtml = _nr ? `
                <div style="margin-top:0.75rem; font-size:0.82rem; background:rgba(99,102,241,0.08); border:1px solid rgba(99,102,241,0.3); border-radius:6px; padding:0.5rem 0.75rem; display:flex; flex-wrap:wrap; gap:0.75rem; align-items:center;">
                    <span>⏰ <strong>${window.t('meta.next_run_label') || 'Prochaine analyse'} :</strong> ${_fmtDt(_nr.nextRun)}</span>
                    <span style="color:var(--text-secondary);">|</span>
                    <span>📅 <strong>${window.t('meta.next_period_label') || 'Période prévue'} :</strong> ${_fmtDt(_nr.periodStart)} → ${_fmtDt(_nr.periodEnd)}</span>
                </div>` : '';

            html += `
            <div class="card" style="border-left: 4px solid var(${c.enabled ? '--accent' : '--border'}); margin-bottom: 1rem;">
                <div style="padding: 1.5rem;">
                    <div style="display: flex; justify-content: space-between; margin-bottom: 0.5rem;">
                        <h3 style="margin: 0; font-size: 1.2rem;">${escapeHtml(c.name)}</h3>
                        <div>${status}</div>
                    </div>
                    <div style="font-size: 0.9rem; color: var(--text-secondary); margin-bottom: 0.5rem; display: flex; gap: 1rem; flex-wrap: wrap;">
                        <div>⏱ ${scheduleText}</div>
                        <div>📝 ${rulesText}</div>
                        <div>🕒 ${lastRunLabel} ${lastRun}</div>
                    </div>
                    ${nextRunHtml}
                    <div style="display: flex; gap: 0.5rem; margin-top:0.75rem;">
                        <button class="btn btn-secondary btn-sm" onclick='editConfig(${JSON.stringify(c).replace(/'/g, "&#39;")})'>${window.t('common.edit') || 'Modifier'}</button>
                        <button class="btn btn-secondary btn-sm" style="color:var(--danger); border-color:var(--danger);" onclick="deleteConfig(${c.id})">${window.t('common.delete')}</button>
                    </div>
                </div>
                
                <!-- Accordéons -->
                <div class="accordion-header" onclick="toggleAccordion('preview-${c.id}', this)">
                    <span>${window.t('meta.accordion_preview')}</span>
                    <span class="icon">▼</span>
                </div>
                <div id="preview-${c.id}" class="accordion-content">
                    <p style="font-size: 0.85rem; color: var(--text-secondary); margin-top:0;">${window.t('meta.preview_help')}</p>
                    <div id="preview-rules-${c.id}" style="display:flex; flex-direction:column; gap:0.75rem;">
                        <div class="loading" data-i18n="common.loading">Chargement...</div>
                    </div>
                    <div style="display:flex; align-items:center; gap:0.5rem; margin-top:1rem; flex-wrap:wrap;">
                        <button id="trigger-btn-${c.id}" class="btn btn-primary btn-sm" onclick="triggerCustomMeta(${c.id})">${window.t('meta.run_custom')}</button>
                        <button id="trigger-stop-${c.id}" class="btn btn-danger btn-sm" style="display:none;">🛑 ${window.t('common.stop') || 'Arrêter'}</button>
                        <span id="trigger-status-${c.id}" style="font-size:0.82rem; display:none;"></span>
                    </div>
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

        // Vérifier les analyses en cours (persistance cross-navigation)
        try {
            const runningRes = await apiFetch('/api/meta-analysis/running');
            if (runningRes.running && runningRes.running.length > 0) {
                runningRes.running.forEach(configId => {
                    _setTriggerStatus(configId, null, true, null, configId);
                });
            }
        } catch (_) { /* silencieux */ }
    } catch (e) {
        container.innerHTML = `<div style="color:var(--danger)">Erreur: ${e.message}</div>`;
    }
}

function toggleAccordion(id, headerEl) {
    const el = document.getElementById(id);
    const isOpen = el.classList.contains('open');
    
    if (isOpen) {
        el.classList.remove('open');
        headerEl.querySelector('.icon').textContent = '\u25bc';
    } else {
        el.classList.add('open');
        headerEl.querySelector('.icon').textContent = '\u25b2';
        
        if (id.startsWith('preview-')) {
            const configId = id.split('-')[1];
            // Si les données sont déjà en cache, on re-rend sans refetch (préserve les éditions)
            if (_previewData[configId]) {
                _renderPreview(configId);
            } else {
                loadPreview(configId);
            }
        } else if (id.startsWith('results-')) {
            loadResultsForConfig(id.split('-')[1]);
        }
    }
}

// Store per-config structured context data
const _previewData = {};

function _renderPreview(configId) {
    const container = document.getElementById(`preview-rules-${configId}`);
    if (!container) return;
    const data = _previewData[configId];
    
    // En-tête avec la période couverte
    let periodHeader = '';
    if (data && data.period_start && data.period_end) {
        const ps = new Date(data.period_start).toLocaleString();
        const pe = new Date(data.period_end).toLocaleString();
        periodHeader = `<div style="font-size:0.82rem; color:var(--text-secondary); background:rgba(255,255,255,0.04); border:1px solid var(--border); border-radius:4px; padding:0.4rem 0.75rem; margin-bottom:0.75rem;">
            📅 ${window.t('meta.preview_period')} : <strong>${ps}</strong> → <strong>${pe}</strong>
            &nbsp;&nbsp;•&nbsp;&nbsp; ${data.analyses_count || 0} ${window.t('meta.events_count')}
        </div>`;
    }
    
    if (!data || !data.rules_context || data.rules_context.length === 0) {
        container.innerHTML = periodHeader + `<p style="color:var(--text-secondary);">${window.t('meta.no_context_available')}</p>`;
        return;
    }

    container.innerHTML = periodHeader + data.rules_context.map((ruleCtx, ruleIdx) => {
        // Séparer les entrées actives et exclues
        const activeEntries = ruleCtx.entries.map((e, idx) => ({...e, _origIdx: idx})).filter(e => !e._excluded);
        const excludedCount = ruleCtx.entries.length - activeEntries.length;

        const entriesHtml = activeEntries.map((e) => {
            const entryIdx = e._origIdx;
            const sevColor = e.severity === 'CRITICAL' ? 'var(--danger)' : e.severity === 'WARNING' ? 'var(--warning)' : 'var(--info)';
            const kwBadges = e.keywords.map(k => `<span class="log-kw-badge">${escapeHtml(k)}</span>`).join('');
            return `
            <div id="entry-${configId}-${ruleIdx}-${entryIdx}" style="padding:0.6rem; border:1px solid rgba(255,255,255,0.06); border-radius:5px; background:rgba(255,255,255,0.02); display:flex; flex-direction:column; gap:0.35rem;">
                <div style="display:flex; justify-content:space-between; align-items:center; gap:0.5rem; flex-wrap:wrap;">
                    <span style="font-size:0.75rem; color:var(--text-secondary);">${e.date}</span>
                    <span style="font-size:0.75rem; font-weight:600; color:${sevColor};">${e.severity}</span>
                    <a href="/monitor?search=${encodeURIComponent(e.detection_id)}" class="btn btn-secondary btn-sm" style="padding:0.1rem 0.4rem; font-size:0.7rem;">🔍 ${e.detection_id}</a>
                    <button type="button" onclick="_deletePreviewEntry(${configId},${ruleIdx},${entryIdx})" title="${window.t('meta.exclude_btn')}" style="margin-left:auto; background:none; border:1px solid var(--danger); color:var(--danger); border-radius:3px; padding:0.1rem 0.4rem; font-size:0.75rem; cursor:pointer; line-height:1;">× ${window.t('meta.exclude_btn')}</button>
                </div>
                <div style="font-family:monospace; font-size:0.78rem; background:rgba(0,0,0,0.3); padding:0.3rem 0.5rem; border-radius:3px; white-space:pre-wrap; word-break:break-all;">${escapeHtml(e.triggered_line)}</div>
                <div style="font-size:0.78rem; color:var(--text-secondary); font-style:italic;">IA : ${escapeHtml(e.short_ia)}</div>
                ${kwBadges ? `<div style="display:flex; flex-wrap:wrap; gap:0.25rem;">${kwBadges}</div>` : ''}
                <textarea placeholder="${window.t('meta.annotation_placeholder')}" data-config="${configId}" data-rule="${ruleIdx}" data-entry="${entryIdx}" onchange="_updateAnnotation(this)" style="width:100%; font-size:0.78rem; background:rgba(255,255,255,0.04); border:1px solid var(--border); border-radius:3px; color:var(--text-primary); padding:0.3rem; resize:vertical; min-height:40px; font-family:inherit; margin-top:0.1rem;">${e.annotation || ''}</textarea>
            </div>`;
        }).join('');

        // Badges des entrées exclues avec bouton Restaurer
        const excludedEntries = ruleCtx.entries.map((e, idx) => ({...e, _origIdx: idx})).filter(e => e._excluded);
        const excludedRestoreHtml = excludedEntries.length > 0 ? `
            <div style="display:flex; flex-wrap:wrap; gap:0.35rem; margin-top:0.5rem; padding-top:0.5rem; border-top:1px dashed rgba(255,255,255,0.1);">
                <span style="font-size:0.75rem; color:var(--text-secondary); align-self:center;">${window.t('meta.restore_btn')} :</span>
                ${excludedEntries.map(e => `
                    <button type="button" onclick="_restorePreviewEntry(${configId},${ruleIdx},${e._origIdx})"
                        style="font-size:0.72rem; background:rgba(255,255,255,0.05); border:1px solid var(--danger); color:var(--danger); border-radius:3px; padding:0.1rem 0.5rem; cursor:pointer; line-height:1.4;">
                        ↺ ${e.detection_id}
                    </button>`).join('')}
            </div>` : '';

        const excludedBadge = excludedCount > 0
            ? (() => {
                const word = excludedCount > 1 ? window.t('meta.excluded_many') : window.t('meta.excluded_one');
                return `<span style="font-size:0.75rem; color:var(--danger); margin-left:0.5rem;">(${excludedCount} ${word})</span>`;
            })()
            : '';
        return `
        <div class="card" style="padding:1rem; border-left:3px solid var(--accent);">
            <div style="font-weight:600; margin-bottom:0.75rem; display:flex; justify-content:space-between; align-items:center;">
                <span>📌 ${escapeHtml(ruleCtx.rule_name)}${excludedBadge}</span>
                <span style="font-size:0.8rem; color:var(--text-secondary);">${activeEntries.length} ${window.t('meta.entries_count')}</span>
            </div>
            <div style="display:flex; flex-direction:column; gap:0.5rem;">${entriesHtml}</div>
            ${excludedRestoreHtml}
        </div>`;
    }).join('');

    if (data.matched_keywords && data.matched_keywords.length > 0) {
        setTimeout(() => highlightDOMText(container, data.matched_keywords), 10);
    }
}

function _deletePreviewEntry(configId, ruleIdx, entryIdx) {
    if (_previewData[configId]) {
        _previewData[configId].rules_context[ruleIdx].entries[entryIdx]._excluded = true;
    }
    _renderPreview(configId);
}

function _restorePreviewEntry(configId, ruleIdx, entryIdx) {
    if (_previewData[configId]) {
        _previewData[configId].rules_context[ruleIdx].entries[entryIdx]._excluded = false;
    }
    _renderPreview(configId);
}

function _updateAnnotation(el) {
    const { config, rule, entry } = el.dataset;
    if (_previewData[config]?.rules_context[rule]?.entries[entry]) {
        _previewData[config].rules_context[rule].entries[entry].annotation = el.value;
    }
}

async function loadPreview(configId) {
    const container = document.getElementById(`preview-rules-${configId}`);
    if (!container) return;
    container.innerHTML = `<div class="loading">${window.t('common.loading')}</div>`;
    try {
        const res = await apiFetch(`/api/meta-analysis/trigger/preview/${configId}`);
        _previewData[configId] = res;
        _renderPreview(configId);
    } catch (e) {
        container.innerHTML = `<div style="color:var(--danger)">Erreur: ${e.message}</div>`;
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

        container.innerHTML = results.map((r, idx) => {
            const start = new Date(r.period_start).toLocaleString();
            const end = new Date(r.period_end).toLocaleString();
            const created = new Date(r.created_at).toLocaleString();
            const resultCardId = `meta-ctx-${configId}-${idx}`;
            
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

            const ctxHtml = r.context_sent ? `
                <div style="margin-top:0.75rem;">
                    <div class="accordion-header" onclick="this.nextElementSibling.classList.toggle('open'); this.querySelector('.ctx-icon').textContent = this.nextElementSibling.classList.contains('open') ? '\u25b2' : '\u25bc';" style="border-radius:4px; border-top:none; background:rgba(255,255,255,0.03); border:1px solid var(--border);">
                        <span style="font-size:0.85rem;">📜 ${window.t('meta.context_sent_title') || 'Contexte envoyé au LLM'}</span>
                        <span class="ctx-icon">▼</span>
                    </div>
                    <div class="accordion-content" style="border:1px solid var(--border); border-top:none; border-radius:0 0 4px 4px; padding:0.75rem;">
                        <pre style="font-family:monospace; font-size:0.75rem; white-space:pre-wrap; word-break:break-all; color:var(--text-secondary); margin:0; max-height:400px; overflow-y:auto;">${escapeHtml(r.context_sent)}</pre>
                    </div>
                </div>` : '';

            return `
            <div class="card meta-result-card" id="${resultCardId}" style="padding: 1.25rem; margin-bottom:1rem; border: 1px solid var(--border);">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.75rem;">
                    <span style="font-size:0.85rem; color:var(--text-secondary);">${created}</span>
                    <div style="display:flex; gap:0.4rem; flex-wrap:wrap; justify-content:flex-end;">
                        <button class="btn btn-primary btn-sm" onclick="metaResultDeepen(${r.id}, ${r.config_id})">${window.t('monitor.deepen_with_ai')}</button>
                        <button class="btn btn-secondary btn-sm" onclick="metaResultNotify(${r.id})">${window.t('monitor.notify')}</button>
                        <button class="btn btn-secondary btn-sm" onclick="metaResultDelete(${r.id}, ${configId})" style="color:var(--danger); border-color:var(--danger);">🗑️ ${window.t('common.delete')}</button>
                    </div>
                </div>
                <div style="margin-bottom: 0.75rem; font-size: 0.9rem; background: rgba(255,255,255,0.05); padding: 0.5rem; border-radius: 4px;">
                    <strong>Période :</strong> ${start} → ${end}<br>
                    <strong>Événements analysés :</strong> ${r.analyses_count}
                    ${kwHtml}
                    ${idsHtml}
                </div>
                <div class="markdown-body" style="font-size: 0.95rem; background: rgba(0,0,0,0.3); padding: 1rem; border-radius: 4px;">
                    ${marked.parse(r.ollama_response || '')}
                </div>
                ${ctxHtml}
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

async function metaResultDeepen(resultId, configId) {
    // Ouvrir le chat avec le contexte de la méta-analyse
    // On récupère d'abord le texte du résultat depuis le DOM
    try {
        const results = await apiFetch(`/api/meta-analysis/results?config_id=${configId}&limit=50`);
        const r = results.find(x => x.id === resultId);
        if (!r) return alert('Résultat introuvable.');
        // Créer une conversation chat avec ce résultat comme contexte
        const conv = await apiFetch('/api/chat/conversations', {
            method: 'POST',
            body: { title: `Méta-Analyse #${resultId}`, analysis_context: r.ollama_response }
        });
        if (conv && conv.id) window.location.href = `/chat?conv=${conv.id}`;
    } catch(e) {
        alert('Erreur: ' + e.message);
    }
}

async function metaResultNotify(resultId) {
    try {
        const btn = event.target;
        btn.textContent = window.t('common.sending') || '⏳ Envoi...';
        await apiFetch(`/api/meta-analysis/results/${resultId}/notify`, { method: 'POST' });
        btn.textContent = window.t('common.sent') || '✅ Envoyé';
        setTimeout(() => btn.textContent = `🔔 ${window.t('monitor.notify')}`, 3000);
    } catch(e) {
        alert('Erreur: ' + e.message);
    }
}

async function metaResultDelete(resultId, configId) {
    if (!confirm(window.t('common.confirm_delete_analysis'))) return;
    try {
        await apiFetch(`/api/meta-analysis/results/${resultId}`, { method: 'DELETE' });
        // Recharger les résultats
        loadResultsForConfig(configId);
    } catch(e) {
        alert('Erreur: ' + e.message);
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
    
    // Mettre à jour les options de planification selon la langue
    const typeSelect = document.getElementById('config-schedule-type');
    typeSelect.options[0].text = window.t('meta.schedule_daily');
    typeSelect.options[1].text = window.t('meta.schedule_weekly');
    typeSelect.options[2].text = window.t('meta.schedule_monthly');

    document.getElementById('config-schedule-type').value = config.schedule_type || 'daily';
    updateScheduleUI();
    document.getElementById('config-schedule-day').value = config.schedule_day || 1;
    document.getElementById('config-schedule-time').value = config.schedule_time || '00:00';
    
    // Mettre à jour les options de contexte selon la langue
    const ctxSelect = document.getElementById('config-context');
    const ctxKeys = ['ctx_quick', 'ctx_standard', 'ctx_large', 'ctx_xlarge', 'ctx_massive'];
    ctxKeys.forEach((k, i) => { if (ctxSelect.options[i]) ctxSelect.options[i].text = window.t(`meta.${k}`); });
    
    document.getElementById('config-context').value = config.context_size;
    document.getElementById('config-max').value = config.max_analyses;
    document.getElementById('config-prompt').value = config.system_prompt;
    document.getElementById('config-enabled').checked = config.enabled;
    document.getElementById('config-notify').checked = config.notify_enabled;

    const ruleIds = config.rule_ids_json || [];
    document.querySelectorAll('.rule-chk').forEach(chk => {
        chk.checked = ruleIds.includes(parseInt(chk.value));
    });

    document.getElementById('modal-title').textContent = window.t('meta.modal_title_edit') || 'Modifier la configuration';
    document.getElementById('config-modal').classList.remove('hidden');
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
        if (id) delete _previewData[id];
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

// Map des AbortControllers actifs par configId
const _metaAbortControllers = {};

async function triggerCustomMeta(id) {
    // Charger les données si pas encore fait (apercu fermé)
    if (!_previewData[id]) {
        try {
            const res = await apiFetch(`/api/meta-analysis/trigger/preview/${id}`);
            _previewData[id] = res;
        } catch (e) {
            _setTriggerStatus(id, `❌ ${e.message}`, false);
            return;
        }
    }

    const data = _previewData[id];
    if (!data || !data.rules_context || data.rules_context.length === 0) {
        _setTriggerStatus(id, '❌ Aucun contexte disponible.', false);
        return;
    }

    // Reconstruire le texte (entrées actives + annotations)
    const lines = [];
    data.rules_context.forEach(ruleCtx => {
        ruleCtx.entries.forEach(e => {
            if (e._excluded) return;
            let block = `[${e.date}] [SEVERITY: ${e.severity}] [R\u00e8gle: ${ruleCtx.rule_name}] [ID: ${e.detection_id}]\nLigne: ${e.triggered_line}\nIA unitaire: ${e.short_ia}`;
            if (e.annotation && e.annotation.trim()) {
                block += `\n[ANNOTATION UTILISATEUR]: ${e.annotation.trim()}`;
            }
            lines.push(block);
        });
    });

    if (lines.length === 0) {
        _setTriggerStatus(id, '❌ Toutes les entrées ont été exclues.', false);
        return;
    }

    const abortController = new AbortController();
    _metaAbortControllers[id] = abortController;
    _setTriggerStatus(id, null, true, abortController, id);

    try {
        await apiFetch(`/api/meta-analysis/trigger/${id}`, {
            method: 'POST',
            body: {
                custom_context: lines.join('\n\n'),
                period_start: data.period_start,  // ISO+Z depuis le preview, évite la race condition avec le scheduleur
                period_end: data.period_end
            },
            signal: abortController.signal
        });
        _setTriggerStatus(id, `✅ ${lines.length} entrée(s) envoyée(s). Résultat bientôt dans les Historiques.`, false);
        setTimeout(() => _setTriggerStatus(id, null, false), 5000);
    } catch (e) {
        if (e.name === 'AbortError') {
            _setTriggerStatus(id, '⏹ Analyse annulée.', false);
        } else {
            _setTriggerStatus(id, `❌ Erreur : ${e.message}`, false);
        }
        setTimeout(() => _setTriggerStatus(id, null, false), 4000);
    } finally {
        delete _metaAbortControllers[id];
    }
}

function _setTriggerStatus(configId, message, loading, abortController, triggerId) {
    const btn = document.getElementById(`trigger-btn-${configId}`);
    const statusEl = document.getElementById(`trigger-status-${configId}`);
    const stopBtn = document.getElementById(`trigger-stop-${configId}`);
    if (!btn) return;

    if (loading) {
        btn.disabled = true;
        btn.innerHTML = `⏳ ${window.t('common.loading') || 'En cours...'}`;
        if (stopBtn) {
            stopBtn.style.display = 'inline-flex';
            stopBtn.onclick = () => {
                if (abortController) abortController.abort();
                apiFetch(`/api/meta-analysis/cancel/${triggerId}`, { method: 'POST' }).catch(() => {});
            };
        }
    } else {
        btn.disabled = false;
        btn.innerHTML = window.t('meta.run_custom') || '▶ Lancer l’analyse';
        if (stopBtn) stopBtn.style.display = 'none';
    }

    if (statusEl) {
        statusEl.textContent = message || '';
        statusEl.style.display = message ? 'block' : 'none';
    }
}

window.onclick = function(event) {
    const modal = document.getElementById('config-modal');
    if (event.target == modal) {
        closeConfigModal();
    }
}
