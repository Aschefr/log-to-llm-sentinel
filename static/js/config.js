document.addEventListener('DOMContentLoaded', () => {
    loadConfig();
    setupForm();
    setupTests();
    setupOllamaModelSelect();
    setupRecommendedModels();
    const ctxInput = document.getElementById('ollama-ctx');
    ctxInput?.addEventListener('input', syncCtxChips);
    // Ce qui s'affiche est ce qui est enregistré : hors limites, le champ montre la valeur retenue
    ctxInput?.addEventListener('change', () => {
        if (ctxInput.value !== '') ctxInput.value = Math.min(131072, Math.max(8192, parseInt(ctxInput.value) || 32768));
        syncCtxChips();
    });
    setupAppriseTags();
    setupModelPulling();
    setupNotificationMethodToggle();
    setupMaintenance();
    setupBackupAndRestore();
    setupTabs();
    refreshStatus();
    setInterval(() => { if (!document.hidden) refreshStatus(); }, 30000);
    // The language file can arrive after the first render: everything written by a script is drawn again
    window.i18n?.onLanguageChange(() => {
        refreshStatus();
        renderRecommendedModels();
        renderModelStudy();
        loadSyslogHosts();
        if (typeof refreshDiag === 'function') refreshDiag().then(renderSnippet).catch(() => {});
    });
});

function cfgT(key, fallback, vars) {
    let text = window.t ? window.t(key) : null;
    if (!text || text === key) text = fallback;
    Object.entries(vars || {}).forEach(([k, v]) => { text = text.replace(`{${k}}`, v); });
    return text;
}

/* ── Tabs ─────────────────────────────────────────────────────────────── */

const CFG_TABS = { ollama: 'tab-ollama', notifications: 'tab-notifications', sources: 'tab-sources',
                   diagnostic: 'diagnostics-section', data: 'tab-data', advanced: 'tab-advanced' };

function selectConfigTab(tab) {
    const current = CFG_TABS[tab] ? tab : 'ollama';
    Object.entries(CFG_TABS).forEach(([name, id]) => document.getElementById(id).classList.toggle('hidden', name !== current));
    document.querySelectorAll('#cfg-tabs a').forEach(a => {
        const active = a.dataset.tab === current;
        a.classList.toggle('active', active);
        if (active) {
            a.setAttribute('aria-current', 'page');
        } else {
            a.removeAttribute('aria-current');
        }
    });
    if (current === 'sources') loadSyslogHosts();
    if (current === 'data') fetchMaintenanceStats();
}

function setupTabs() {
    const fromHash = () => selectConfigTab(location.hash.slice(1) === 'diagnostics-section' ? 'diagnostic' : location.hash.slice(1));
    window.addEventListener('hashchange', fromHash);
    fromHash();
    setupSectionBars();
}

/* Sections of each tab in a bar that stays under the header while scrolling (as in Alanbix's admin): the section in view
   is highlighted, a click scrolls to it. Buttons, not links: a #hash would switch the tab. */
function setupSectionBars() {
    const navbar = document.querySelector('.navbar');
    const stickyTop = () => (navbar && getComputedStyle(navbar).position === 'sticky' ? navbar.offsetHeight : 0);
    const setTop = () => document.documentElement.style.setProperty('--cfg-sticky-top', `${stickyTop()}px`);
    setTop();
    if (navbar && window.ResizeObserver) new ResizeObserver(setTop).observe(navbar);
    window.addEventListener('resize', setTop);

    const bars = [];
    Object.values(CFG_TABS).forEach(panelId => {
        const panel = document.getElementById(panelId);
        const sections = [...panel.querySelectorAll('.rp-card, .diag-card')].filter(s => s.querySelector('h2, h3'));
        if (sections.length < 2) return;
        const bar = document.createElement('nav');
        bar.className = 'cfg-jump';
        bar.setAttribute('aria-label', cfgT('config.section_jump', 'Aller à une section de l’onglet'));
        bar.setAttribute('data-i18n-aria-label', 'config.section_jump');
        const row = document.createElement('div');
        row.className = 'cfg-jump-row';
        sections.forEach((section, i) => {
            if (!section.id) section.id = `${panelId}-part${i + 1}`;
            section.classList.add('cfg-jump-target');
            const heading = section.querySelector('h2, h3');
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'cfg-jump-item';
            btn.dataset.target = section.id;
            btn.textContent = heading.textContent.trim();
            if (heading.dataset.i18n) btn.setAttribute('data-i18n', heading.dataset.i18n);
            btn.addEventListener('click', () => {
                const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
                section.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' });
                markActive(bar, section.id);
            });
            row.appendChild(btn);
        });
        bar.appendChild(row);
        panel.prepend(bar);
        bars.push({ panel, bar, sections });
    });

    function markActive(bar, id) {
        bar.querySelectorAll('.cfg-jump-item').forEach(btn => {
            const on = btn.dataset.target === id;
            btn.classList.toggle('active', on);
            if (on) {
                btn.setAttribute('aria-current', 'true');
                // Sur un écran étroit la barre défile : garder la section courante en vue
                const row = btn.parentElement;
                if (btn.offsetLeft < row.scrollLeft || btn.offsetLeft + btn.offsetWidth > row.scrollLeft + row.clientWidth) {
                    row.scrollLeft = btn.offsetLeft - 8;
                }
            } else {
                btn.removeAttribute('aria-current');
            }
        });
    }

    let pending = false;
    const spy = () => {
        pending = false;
        const current = bars.find(b => !b.panel.classList.contains('hidden'));
        if (!current) return;
        // Une section masquée par un réglage (SMTP, Apprise, Discord selon la méthode d'envoi) n'a pas de puce
        const shown = current.sections.filter(s => s.offsetParent !== null);
        current.bar.querySelectorAll('.cfg-jump-item').forEach(btn => {
            btn.hidden = !shown.some(s => s.id === btn.dataset.target);
        });
        if (!shown.length) return;
        const line = stickyTop() + current.bar.offsetHeight + 24;
        let active = shown[0];
        shown.forEach(s => { if (s.getBoundingClientRect().top <= line) active = s; });
        // Tout en bas de la page, la dernière section ne peut pas monter jusqu'à la barre : elle devient la courante
        if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 2) active = shown[shown.length - 1];
        markActive(current.bar, active.id);
        current.bar.classList.toggle('stuck', current.bar.getBoundingClientRect().top <= stickyTop() + 1 && window.scrollY > 0);
    };
    const onScroll = () => { if (!pending) { pending = true; requestAnimationFrame(spy); } };
    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('hashchange', () => requestAnimationFrame(spy));
    // Les réglages arrivent après l'ouverture de la page et peuvent masquer ou montrer des sections
    if (window.MutationObserver) {
        const watch = new MutationObserver(onScroll);
        bars.forEach(b => b.sections.forEach(s => watch.observe(s, { attributes: true, attributeFilter: ['class', 'style', 'hidden'] })));
    }
    document.addEventListener('change', onScroll);
    spy();
}

/* ── Real state, at the top of the page ───────────────────────────────── */

const cfgAgo = relativeTime;

function cfgMethodName(method) {
    return { smtp: 'Email', apprise: 'Apprise', discord: 'Discord' }[method] || method;
}

function ollamaStatusView(o) {
    if (o.state === 'ok') return ['ok', cfgT('config.status_ollama_ok', 'Connecté · {model}', { model: o.model })];
    if (o.state === 'model_missing') return ['warn', cfgT('config.status_ollama_missing', 'Modèle {model} absent', { model: o.model })];
    if (o.state === 'not_configured') return ['warn', cfgT('config.status_ollama_unset', 'À configurer')];
    return ['alert', cfgT('config.status_ollama_down', 'Injoignable')];
}

function notificationStatusView(n) {
    const name = cfgMethodName(n.method);
    if (n.state === 'incomplete') return ['warn', cfgT('config.status_notif_incomplete', '{method} · à compléter', { method: name })];
    if (n.state === 'failed') {
        const key = n.kind === 'send' ? 'config.status_notif_send_failed' : 'config.status_notif_test_failed';
        return ['alert', cfgT(key, '{method} · échec {ago}', { method: name, ago: cfgAgo(n.at) })];
    }
    if (n.state === 'ok') {
        const key = n.kind === 'send' ? 'config.status_notif_sent' : 'config.status_notif_tested';
        return ['ok', cfgT(key, '{method} · réussi {ago}', { method: name, ago: cfgAgo(n.at) })];
    }
    return ['idle', cfgT('config.status_notif_untested', '{method} · non testé', { method: name })];
}

function syslogStatusView(y) {
    if (y.state === 'off') return ['idle', cfgT('config.status_syslog_off', 'Désactivé')];
    if (y.state === 'not_listening') return ['alert', cfgT('config.status_syslog_down', "N'écoute pas")];
    if (y.state === 'waiting') return ['idle', cfgT('config.status_syslog_waiting', 'Actif · rien reçu en 7 jours')];
    return ['ok', cfgT('config.status_syslog_on', '{n} source(s) · {ago}', { n: y.hosts, ago: cfgAgo(y.last_seen) })];
}

function diagnosticsStatusView(d) {
    const parts = [];
    if (d.docker) parts.push(cfgT('config.status_diag_docker', 'Docker'));
    if (d.host) parts.push(cfgT('config.status_diag_host', 'Serveur'));
    if (d.web) parts.push('Web');
    if (d.cve) parts.push('CVE');
    if (parts.length) return ['ok', parts.join(' · ')];
    return ['idle', cfgT('config.status_diag_off', 'Non configuré')];
}

function cfgSize(bytes) {
    const megabytes = bytes / (1024 * 1024);
    const lang = document.documentElement.lang || 'fr';
    if (megabytes < 1) {
        const kilobytes = Math.max(bytes ? 1 : 0, Math.round(bytes / 1024));
        return `${kilobytes.toLocaleString(lang)} ${cfgT('config.unit_kb', 'Ko')}`;
    }
    const value = megabytes.toLocaleString(lang, { maximumFractionDigits: megabytes < 100 ? 1 : 0 });
    return `${value} ${cfgT('config.unit_mb', 'Mo')}`;
}

function cfgRetentionName(days) {
    const key = { 7: 'config.retention_1w', 30: 'config.retention_1m', 180: 'config.retention_6m', 365: 'config.retention_1y' }[days];
    return key ? cfgT(key, `${days} j`).toLowerCase() : `${days} ${cfgT('config.retention_days_lower', 'jours')}`;
}

function dataStatusView(d) {
    const size = cfgSize(d.size_bytes);
    if (!d.auto_delete) return ['idle', cfgT('config.status_data_keep', '{size} · tout conservé', { size })];
    return ['idle', cfgT('config.status_data_retention', '{size} · conservation {period}', { size, period: cfgRetentionName(d.retention_days) })];
}

function advancedStatusView(a) {
    if (a.debug) return ['warn', cfgT('config.status_debug_on', 'Mode debug actif')];
    return ['idle', cfgT('config.status_debug_off', 'Debug désactivé')];
}

function setStatusItem(name, tone, text) {
    const item = document.querySelector(`.cfg-status-item[data-status="${name}"]`);
    if (!item) return;
    item.querySelector('.rp-dot').className = `rp-dot rp-dot--${tone}`;
    const detail = item.querySelector('.cfg-status-detail');
    detail.removeAttribute('data-i18n');
    detail.textContent = text;
}

async function refreshStatus() {
    try {
        const s = await apiFetch('/api/config/status');
        const views = { ollama: ollamaStatusView(s.ollama), notifications: notificationStatusView(s.notifications),
                        syslog: syslogStatusView(s.syslog), diagnostics: diagnosticsStatusView(s.diagnostics),
                        data: dataStatusView(s.data), advanced: advancedStatusView(s.advanced) };
        Object.entries(views).forEach(([name, [tone, text]]) => setStatusItem(name, tone, text));
        const logSize = document.getElementById('cfg-log-size');
        if (logSize) logSize.textContent = cfgSize(s.advanced.log_bytes);
        document.getElementById('cfg-log-download')?.classList.toggle('hidden', !s.advanced.log_bytes);
    } catch (_) {
        ['ollama', 'notifications', 'syslog', 'diagnostics', 'data', 'advanced'].forEach(name => setStatusItem(name, 'idle', cfgT('config.status_unknown', 'État inconnu')));
    }
}

async function loadSyslogHosts() {
    const list = document.getElementById('cfg-hosts');
    if (!list) return;
    try {
        const { hosts } = await apiFetch('/api/monitor/syslog/hosts');
        if (!hosts.length) {
            list.innerHTML = `<li class="form-hint">${escapeHtml(cfgT('config.syslog_hosts_empty', "Rien reçu pour l'instant : indique l'adresse ci-dessus à la source qui envoie ses logs."))}</li>`;
            return;
        }
        list.innerHTML = hosts.map(h => `<li>
            <code>${escapeHtml(h.hostname)}</code>
            <span class="form-hint">${escapeHtml(cfgAgo(h.last_seen))}</span>
            ${h.rule ? `<a href="/rules/${h.rule.id}">${escapeHtml(h.rule.name)}</a>`
                     : `<a href="/rules">${escapeHtml(cfgT('config.syslog_host_no_rule', 'Aucune règle'))}</a>`}
        </li>`).join('');
    } catch (e) {
        list.innerHTML = `<li class="form-hint rf-ko">${escapeHtml(e.message)}</li>`;
    }
}

function cfgResult(id, ok, text) {
    const el = typeof id === 'string' ? document.getElementById(id) : id;
    if (!el) return;
    el.textContent = text;
    el.classList.remove('rf-ok', 'rf-ko');
    if (ok !== null) el.classList.add(ok ? 'rf-ok' : 'rf-ko');
}

function setupNotificationMethodToggle() {
    const select = document.getElementById('notification-method');
    if (!select) return;

    select.addEventListener('change', updateNotificationVisibility);
    updateNotificationVisibility(); // Appliquer au chargement
}

function updateNotificationVisibility() {
    const select = document.getElementById('notification-method');
    if (!select) return;
    const method = select.value;
    const smtpEl = document.getElementById('smtp-section');
    const appriseEl = document.getElementById('apprise-section');
    const discordEl = document.getElementById('discord-section');
    if (smtpEl)    smtpEl.style.display    = (method === 'smtp')    ? '' : 'none';
    if (appriseEl) appriseEl.style.display = (method === 'apprise') ? '' : 'none';
    if (discordEl) discordEl.style.display = (method === 'discord') ? '' : 'none';
}

function setPullModel(name) {
    const input = document.getElementById('pull-model-name');
    if (input) input.value = name;
}

/* Résultats de l'étude sur de vraies alertes, sur une RTX 3090 : voir docs/en/features/recommended-models.md
   (mesurés de nouveau le 2026-11-08 avec la question « à surveiller » : prompts actuels, 38 cas joués deux fois, contexte 32K,
   sans réflexion ; bonsai-2:27b mesuré avant, sur les prompts de la 1.9) */
const RECOMMENDED_MODELS = [
    { model: 'gemma4:e4b', tier: ['config.tier_minimum', 'Minimum'], memory: '3,2', seconds: 4 },
    { model: 'gemma4:12b', tier: ['config.tier_recommended', 'Conseillé'], memory: '7,6', seconds: 4 },
    { model: 'qwen3.8:27b', tier: ['config.tier_top', 'Top qualité'], memory: '16,3', seconds: 8 },
];

/* Même étude, détail par modèle, affiché dans « Comment ces modèles ont été choisis » */
const MODEL_STUDY = [
    { model: 'qwen3.8:27b', tier: ['config.tier_top', 'Top qualité'], correct: 74, falseAlarms: 0, missed: 2, steering: 30, seconds: 8 },
    { model: 'gemma4:12b', tier: ['config.tier_recommended', 'Conseillé'], correct: 74, falseAlarms: 2, missed: 0, steering: 32, seconds: 4 },
    { model: 'bonsai-2:27b', tier: ['config.study_not_kept', 'Non retenu'], correct: 72, falseAlarms: 2, missed: 2, steering: 31, seconds: 7.5 },
    { model: 'gemma4:e4b', tier: ['config.tier_minimum', 'Minimum'], correct: 62, falseAlarms: 14, missed: 0, steering: 31, seconds: 4 },
];

function renderModelStudy() {
    const table = document.getElementById('cfg-study-table');
    if (!table) return;
    const num = new Intl.NumberFormat(window.i18n?.getCurrentLang?.() === 'fr' ? 'fr-FR' : 'en-US');
    const head = (key, fallback, total) => `<th scope="col">${escapeHtml(cfgT(key, fallback))}${total ? `<span class="cfg-study-total">/${total}</span>` : ''}</th>`;
    table.innerHTML = `<thead><tr>
            ${head('config.study_model', 'Modèle')}
            ${head('config.study_correct', 'Bonne gravité', 76)}
            ${head('config.study_false', 'Fausses alertes', 30)}
            ${head('config.study_missed', 'Graves notées info', 22)}
            ${head('config.study_steering', 'Manipulations déjouées', 33)}
            ${head('config.study_time', 'Temps par analyse')}
        </tr></thead>
        <tbody>${MODEL_STUDY.map(m => `<tr>
            <th scope="row"><span class="mono">${escapeHtml(m.model)}</span><span class="cfg-study-tier">${escapeHtml(cfgT(m.tier[0], m.tier[1]))}</span></th>
            <td>${m.correct}</td>
            <td>${m.falseAlarms}</td>
            <td${m.missed ? ' class="cfg-study-bad"' : ''}>${m.missed}</td>
            <td>${m.steering}</td>
            <td>${num.format(m.seconds)} s</td>
        </tr>`).join('')}</tbody>`;
}

function installedModels() {
    const select = document.getElementById('ollama-model');
    return select ? [...select.options].map(o => o.value).filter(v => v && v !== '__custom__') : [];
}

function renderRecommendedModels() {
    const box = document.getElementById('cfg-models');
    if (!box) return;
    const select = document.getElementById('ollama-model');
    const installed = installedModels();
    box.innerHTML = RECOMMENDED_MODELS.map(m => {
        const isInstalled = installed.includes(m.model);
        const current = select && select.value === m.model;
        const state = !installed.length ? '' : isInstalled ? cfgT('config.model_installed', 'Installé') : cfgT('config.model_to_download', 'À télécharger');
        const detail = cfgT('config.model_detail', '{gb} Go · {s} s par analyse').replace('{gb}', window.i18n?.getCurrentLang?.() === 'fr' ? m.memory : m.memory.replace(',', '.')).replace('{s}', m.seconds);
        return `<button type="button" class="cfg-status-item${current ? ' active' : ''}" data-model="${m.model}" aria-pressed="${Boolean(current)}">
            <span class="cfg-status-text">
                <span class="cfg-status-detail">${escapeHtml(cfgT(m.tier[0], m.tier[1]))}</span>
                <strong class="mono">${escapeHtml(m.model)}</strong>
                <span class="cfg-status-detail">${escapeHtml(detail)}${state ? ` · ${escapeHtml(state)}` : ''}</span>
            </span>
        </button>`;
    }).join('');
}

function setupRecommendedModels() {
    const box = document.getElementById('cfg-models');
    const select = document.getElementById('ollama-model');
    if (!box || !select) return;
    box.addEventListener('click', e => {
        const item = e.target.closest('[data-model]');
        if (!item) return;
        if (installedModels().includes(item.dataset.model)) {
            select.value = item.dataset.model;
            select.dispatchEvent(new Event('change', { bubbles: true }));
        } else {
            setPullModel(item.dataset.model);
            document.getElementById('pull-model-btn').focus();
        }
        renderRecommendedModels();
    });
    select.addEventListener('change', renderRecommendedModels);
    // La liste arrive d'Ollama après l'ouverture de la page, et change après un téléchargement
    new MutationObserver(renderRecommendedModels).observe(select, { childList: true });
    renderRecommendedModels();
    renderModelStudy();
}

function syncCtxChips() {
    const value = Number(document.getElementById('ollama-ctx')?.value);
    document.querySelectorAll('#ctx-chips .chip').forEach(chip => {
        const active = Number(chip.dataset.ctx) === value;
        chip.classList.toggle('active', active);
        chip.setAttribute('aria-pressed', String(active));
    });
}

function setupModelPulling() {
    const btn = document.getElementById('pull-model-btn');
    const input = document.getElementById('pull-model-name');
    const progressContainer = document.getElementById('pull-progress-container');
    const progressBar = document.getElementById('pull-progress-bar');
    const statusText = document.getElementById('pull-status');

    if (!btn || !input) return;

    btn.addEventListener('click', async () => {
        const model = input.value.trim();
        if (!model) {
            alert(window.t ? window.t('config.enter_model_name') : 'Please enter a model name');
            return;
        }

        btn.disabled = true;
        progressContainer.classList.remove('hidden');
        progressBar.style.width = '0%';
        statusText.textContent = `${window.t ? window.t('config.model_download_start') : 'Starting download of'} ${model}...`;

        try {
            const response = await fetch('/api/config/pull-model', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ model: model })
            });

            if (!response.ok) {
                const err = await response.json();
                throw new Error(err.detail || 'Erreur lors du pull');
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                const chunk = decoder.decode(value);
                const lines = chunk.split('\n\n');
                
                for (const line of lines) {
                    if (line.startsWith('data: ')) {
                        try {
                            const data = JSON.parse(line.substring(6));
                            if (data.error) throw new Error(data.error);
                            
                            if (data.status) {
                                statusText.textContent = data.status;
                                if (data.completed && data.total) {
                                    const pct = Math.round((data.completed / data.total) * 100);
                                    progressBar.style.width = `${pct}%`;
                                    statusText.textContent = `${data.status} (${pct}%)`;
                                }
                                if (data.status === 'success') {
                                    progressBar.style.width = '100%';
                                    statusText.textContent = '✅ ' + (window.t ? window.t('config.model_download_success') : 'Model downloaded successfully!');
                                    setTimeout(() => setupOllamaModelSelect(), 2000);
                                }
                            }
                        } catch (e) {
                            console.error('Erreur parse chunk:', e);
                        }
                    }
                }
            }
        } catch (e) {
            statusText.textContent = `${window.t ? window.t('common.error') : 'Erreur'} : ${e.message}`;
            statusText.style.color = 'var(--danger)';
        } finally {
            btn.disabled = false;
        }
    });
}

async function loadConfig() {
    try {
        const config = await apiFetch('/api/config');
        document.getElementById('smtp-host').value = config.smtp_host || '';
        document.getElementById('smtp-port').value = config.smtp_port || 587;
        document.getElementById('smtp-user').value = config.smtp_user || '';
        const passwordEl = document.getElementById('smtp-password');
        passwordEl.value = '';
        passwordEl.placeholder = config.smtp_password_set ? cfgT('config.smtp_password_saved', 'Enregistré : laisser vide pour le garder') : '';
        document.getElementById('smtp-recipient').value = config.smtp_recipient || '';
        // ssl_mode: priorité au nouveau champ, fallback legacy
        const sslMode = config.smtp_ssl_mode || (config.smtp_tls ? 'starttls' : 'none');
        document.getElementById('smtp-ssl-mode').value = sslMode;
        document.getElementById('ollama-url').value = config.ollama_url || 'http://host.docker.internal:11434';
        // Model select is populated async; keep desired value to apply later.
        window.__desiredOllamaModel = config.ollama_model || 'llama3';
        const methodEl = document.getElementById('notification-method');
        methodEl.value = config.notification_method || 'smtp';
        if (!methodEl.value) methodEl.value = 'smtp';  // valeur inconnue en base : ne jamais afficher un choix vide
        document.getElementById('apprise-url').value = config.apprise_url || '';
        document.getElementById('apprise-max-chars').value = config.apprise_max_chars || 1900;
        const discordMaxEl = document.getElementById('discord-max-chars');
        if (discordMaxEl) discordMaxEl.value = config.apprise_max_chars || 1900;
        const discordEl = document.getElementById('discord-webhook-url');
        if (discordEl) discordEl.value = config.discord_webhook_url || '';
        document.getElementById('monitor-log-lines').value = config.monitor_log_lines || 60;
        document.getElementById('instance-name').value = config.instance_name || '';
        document.getElementById('chat-system-prompt').value = config.chat_system_prompt || '';
        document.getElementById('chat-compression-mode').value = config.chat_compression_mode || '';
        document.getElementById('ollama-ctx').value = config.ollama_ctx || 32768;
        syncCtxChips();
        document.getElementById('ollama-think').checked = config.ollama_think === true;
        document.getElementById('ollama-think-tools').checked = config.ollama_think_tools !== false;
        document.getElementById('situation-ai').checked = config.situation_ai !== false;
        window.__desiredAppriseTags = config.apprise_tags || '';
        const debugEl = document.getElementById('debug-mode');
        if (debugEl) debugEl.checked = config.debug_mode === true;

        const autoDeleteEl = document.getElementById('auto-delete-analyses');
        if (autoDeleteEl) autoDeleteEl.checked = config.auto_delete_analyses === true;
        
        const syslogEnabledEl = document.getElementById('syslog-enabled');
        if (syslogEnabledEl) syslogEnabledEl.checked = config.syslog_enabled === true;
        const syslogForwardAddrEl = document.getElementById('syslog-forward-addr');
        if (syslogForwardAddrEl) syslogForwardAddrEl.value = config.syslog_forward_addr || '';
        
        const logRotationLimitMbEl = document.getElementById('log-rotation-limit-mb');
        if (logRotationLimitMbEl) logRotationLimitMbEl.value = config.log_rotation_limit_mb || 10;
        
        const syslogCurrentIpEl = document.getElementById('syslog-current-ip');
        if (syslogCurrentIpEl) {
            const displayIp = (config.server_ip && config.server_ip !== 'localhost') ? config.server_ip : (window.location.hostname || 'IP_HOTE');
            syslogCurrentIpEl.textContent = displayIp;
        }

        const retentionDays = config.auto_delete_retention_days || 30;
        const retentionSelect = document.getElementById('retention-period');
        const retentionCustom = document.getElementById('retention-days-custom');
        if (retentionSelect && retentionCustom) {
            const standardValues = ['7', '30', '180', '365'];
            if (standardValues.includes(retentionDays.toString())) {
                retentionSelect.value = retentionDays.toString();
                retentionCustom.classList.add('hidden');
            } else {
                retentionSelect.value = 'custom';
                retentionCustom.value = retentionDays;
                retentionCustom.classList.remove('hidden');
            }
        }

        updateNotificationVisibility();
        window.__configLoaded = true;
        
        fetchMaintenanceStats();
    } catch (error) {
        console.error('Erreur chargement config:', error);
    }
}

async function setupOllamaModelSelect() {
    const select = document.getElementById('ollama-model');
    const customGroup = document.getElementById('ollama-model-custom-group');
    const customInput = document.getElementById('ollama-model-custom');
    if (!select || !customGroup || !customInput) return;

    function setCustomVisible(on) {
        customGroup.classList.toggle('hidden', !on);
    }

    if (!select.dataset.listenerAdded) {
        select.addEventListener('change', () => {
            const v = select.value;
            if (v === '__custom__') {
                setCustomVisible(true);
                customInput.focus();
            } else {
                setCustomVisible(false);
            }
        });
        select.dataset.listenerAdded = 'true';
    }

    // Populate list
    const hint = document.getElementById('ollama-model-hint');
    try {
        const res = await apiFetch('/api/config/ollama/models');
        if (hint) {
            hint.textContent = cfgT('config.ollama_model_hint', 'La liste est récupérée depuis Ollama.');
            hint.classList.remove('rf-ko');
        }
        const models = (res && res.models) ? res.models : [];

        const desired = window.__desiredOllamaModel || 'llama3';
        const hasDesired = models.includes(desired);

        const options = [];
        if (models.length === 0) {
            options.push(`<option value="__custom__">${window.t ? window.t('config.autre_option') : 'Other\u2026'}</option>`);
        } else {
            options.push(...models.map(m => `<option value="${escapeHtml(m)}">${escapeHtml(m)}</option>`));
            options.push(`<option value="__custom__">${window.t ? window.t('config.autre_option') : 'Other\u2026'}</option>`);
        }
        select.innerHTML = options.join('');

        if (hasDesired) {
            select.value = desired;
            setCustomVisible(false);
        } else {
            select.value = '__custom__';
            customInput.value = desired;
            setCustomVisible(true);
        }
    } catch (e) {
        // La liste n'a pas pu être lue : l'explication va dans l'aide du champ, le modèle reste modifiable à la main
        select.innerHTML = `<option value="__custom__">${window.t ? window.t('config.autre_option') : 'Other\u2026'}</option>`;
        if (hint) {
            hint.removeAttribute('data-i18n');
            hint.textContent = cfgT('config.ollama_models_error', "Liste indisponible. Vérifie l'URL ci-dessus, ou saisis le modèle à la main.");
            hint.classList.add('rf-ko');
        }
        select.value = '__custom__';
        customInput.value = window.__desiredOllamaModel || 'llama3';
        setCustomVisible(true);
    }
}

function setupForm() {
    const form = document.getElementById('config-form');

    // Entrée dans un champ : le quitte et l'enregistre, sans recharger la page
    form.addEventListener('submit', (e) => {
        e.preventDefault();
        if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
    });

    // La section Diagnostic a son propre « Tester et enregistrer » (API dédiée) : hors enregistrement automatique.
    // Un champ est enregistré quand on le quitte, pour ne jamais appliquer une URL ou un mot de passe à moitié saisis.
    form.querySelectorAll('input, select, textarea').forEach(input => {
        if (input.closest('#diagnostics-section') || input.hasAttribute('data-no-save')) return;
        input.addEventListener('change', () => queueSave(input));
    });
    // Page fermée ou rechargée pendant la saisie : le champ en cours part quand même (requête keepalive)
    window.addEventListener('pagehide', () => {
        if (form.contains(document.activeElement)) document.activeElement.blur();
    });
}

const CFG_SAVE = { inFlight: null, dirty: false, inputs: new Set() };

function fieldSaveState(input, state, text) {
    const row = input.closest('.cfg-row');
    const holder = row && row.querySelector('.cfg-row-label');
    if (!holder) return;
    let el = holder.querySelector('.cfg-save-state');
    if (!el) {
        el = document.createElement('span');
        el.className = 'cfg-save-state';
        holder.querySelector('label, .cfg-label')?.after(el);
    }
    clearTimeout(el._timer);
    el.textContent = text;
    el.dataset.state = state;
    if (state === 'saved') el._timer = setTimeout(() => { el.textContent = ''; el.dataset.state = ''; }, 2500);
}

function queueSave(input) {
    CFG_SAVE.inputs.add(input);
    if (CFG_SAVE.inFlight) { CFG_SAVE.dirty = true; return; }
    CFG_SAVE.inFlight = runSaves().finally(() => { CFG_SAVE.inFlight = null; });
}

async function runSaves() {
    const live = document.getElementById('cfg-save-live');
    do {
        CFG_SAVE.dirty = false;
        const inputs = [...CFG_SAVE.inputs];
        CFG_SAVE.inputs.clear();
        inputs.forEach(input => fieldSaveState(input, 'saving', cfgT('config.field_saving', 'Enregistrement…')));
        const error = await saveConfig();
        if (error) {
            const text = cfgT('config.field_not_saved', 'Non enregistré : {error}', { error });
            inputs.forEach(input => fieldSaveState(input, 'error', text));
            if (live) live.textContent = text;
            continue;
        }
        const text = cfgT('config.field_saved', 'Enregistré');
        inputs.forEach(input => fieldSaveState(input, 'saved', text));
        if (live) live.textContent = text;
        inputs.forEach(input => {
            if (input.id === 'ollama-url') setupOllamaModelSelect();
            if (input.id === 'apprise-url') setupAppriseTags();
            if (input.id === 'smtp-password' && input.value) {
                input.value = '';
                input.placeholder = cfgT('config.smtp_password_saved', 'Enregistré : laisser vide pour le garder');
            }
        });
    } while (CFG_SAVE.dirty);
    refreshStatus();
}

function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

function setupTests() {
    const btnOllama = document.getElementById('test-ollama-btn');
    const btnSmtp = document.getElementById('test-smtp-btn');
    const btnApprise = document.getElementById('test-apprise-btn');
    const btnDiscord = document.getElementById('test-discord-btn');

    const msgOllama = document.getElementById('ollama-test-message');
    const msgSmtp = document.getElementById('smtp-test-message');
    const msgApprise = document.getElementById('apprise-test-message');
    const msgDiscord = document.getElementById('discord-test-message');

    if (btnOllama && msgOllama) {
        btnOllama.addEventListener('click', async () => {
            await runTest('/api/config/test/ollama', msgOllama, btnOllama);
        });
    }
    if (btnSmtp && msgSmtp) {
        btnSmtp.addEventListener('click', async () => {
            await runTest('/api/config/test/smtp', msgSmtp, btnSmtp);
        });
    }
    if (btnApprise && msgApprise) {
        btnApprise.addEventListener('click', async () => {
            await runTest('/api/config/test/apprise', msgApprise, btnApprise);
        });
    }
    if (btnDiscord && msgDiscord) {
        btnDiscord.addEventListener('click', async () => {
            await runTest('/api/config/test/discord', msgDiscord, btnDiscord);
        });
    }
}

async function runTest(url, messageEl, buttonEl) {
    const oldText = buttonEl.textContent;
    buttonEl.disabled = true;
    buttonEl.textContent = cfgT('config.test_in_progress', 'Testing...');
    cfgResult(messageEl, null, '');

    const abortController = new AbortController();
    const stopBtn = document.createElement('button');
    stopBtn.type = 'button';
    stopBtn.className = 'btn btn-secondary btn-sm';
    stopBtn.textContent = cfgT('common.stop', 'Stop');
    stopBtn.onclick = () => abortController.abort();
    buttonEl.after(stopBtn);

    try {
        // Le test doit porter sur ce qui vient d'être saisi : le champ quitté en cliquant ici est encore en cours d'envoi
        if (CFG_SAVE.inFlight) await CFG_SAVE.inFlight;
        const res = await apiFetch(url, { method: 'POST', signal: abortController.signal });
        cfgResult(messageEl, true, (res && res.detail) ? res.detail : 'OK');
    } catch (error) {
        if (error.name === 'AbortError') {
            cfgResult(messageEl, false, cfgT('config.test_cancelled', 'Test cancelled'));
        } else {
            console.error('Test error:', error);
            cfgResult(messageEl, false, `${cfgT('common.error', 'Error')} : ${error.message || 'unknown'}`);
        }
    } finally {
        buttonEl.disabled = false;
        buttonEl.textContent = oldText;
        stopBtn.remove();
        refreshStatus();
    }
}

/** Sends every field. Returns an error message, or null when it is saved. */
async function saveConfig() {
    if (!window.__configLoaded) {
        return cfgT('config.not_loaded', "la configuration n'a pas pu être chargée");
    }
    const modelSelect = document.getElementById('ollama-model');
    const modelCustom = document.getElementById('ollama-model-custom');
    const chosenModel = (modelSelect && modelSelect.value === '__custom__')
        ? (modelCustom ? modelCustom.value : '')
        : (modelSelect ? modelSelect.value : '');

    const data = {
        smtp_host: document.getElementById('smtp-host').value,
        smtp_port: parseInt(document.getElementById('smtp-port').value) || 587,
        smtp_user: document.getElementById('smtp-user').value,
        smtp_recipient: document.getElementById('smtp-recipient').value,
        smtp_ssl_mode: document.getElementById('smtp-ssl-mode').value,
        smtp_tls: document.getElementById('smtp-ssl-mode').value === 'starttls',
        ollama_url: document.getElementById('ollama-url').value,
        ollama_model: chosenModel,
        notification_method: document.getElementById('notification-method').value,
        apprise_url: document.getElementById('apprise-url').value,
        apprise_tags: (document.getElementById('apprise-tags-select').value === '__custom__') 
            ? document.getElementById('apprise-tags').value 
            : document.getElementById('apprise-tags-select').value,
        apprise_max_chars: (document.getElementById('notification-method').value === 'discord')
            ? (parseInt(document.getElementById('discord-max-chars').value) || 1900)
            : (parseInt(document.getElementById('apprise-max-chars').value) || 1900),
        discord_webhook_url: document.getElementById('discord-webhook-url') ? document.getElementById('discord-webhook-url').value : '',
        chat_system_prompt: document.getElementById('chat-system-prompt').value,
        chat_compression_mode: document.getElementById('chat-compression-mode').value,
        monitor_log_lines: parseInt(document.getElementById('monitor-log-lines').value) || 60,
        ollama_ctx: Math.min(131072, Math.max(8192, parseInt(document.getElementById('ollama-ctx').value) || 32768)),
        ollama_think: document.getElementById('ollama-think').checked,
        ollama_think_tools: document.getElementById('ollama-think-tools').checked,
        situation_ai: document.getElementById('situation-ai').checked,
        debug_mode: document.getElementById('debug-mode') ? document.getElementById('debug-mode').checked : false,
        instance_name: document.getElementById('instance-name') ? document.getElementById('instance-name').value : '',
        auto_delete_analyses: document.getElementById('auto-delete-analyses') ? document.getElementById('auto-delete-analyses').checked : false,
        syslog_enabled: document.getElementById('syslog-enabled') ? document.getElementById('syslog-enabled').checked : false,
        syslog_forward_addr: document.getElementById('syslog-forward-addr') ? document.getElementById('syslog-forward-addr').value.trim() : '',
        log_rotation_limit_mb: document.getElementById('log-rotation-limit-mb') ? parseInt(document.getElementById('log-rotation-limit-mb').value) || 10 : 10,
    };

    const retSelect = document.getElementById('retention-period');
    const retCustom = document.getElementById('retention-days-custom');
    if (retSelect && retSelect.value === 'custom') {
        data.auto_delete_retention_days = parseInt(retCustom.value) || 30;
    } else if (retSelect) {
        data.auto_delete_retention_days = parseInt(retSelect.value) || 30;
    }

    const pwd = document.getElementById('smtp-password').value;
    if (pwd) data.smtp_password = pwd;

    try {
        await apiFetch('/api/config', { method: 'PUT', body: data, keepalive: true });
        return null;
    } catch (error) {
        console.error('Erreur sauvegarde config:', error);
        return error.message;
    }
}

async function setupAppriseTags() {
    const select = document.getElementById('apprise-tags-select');
    const customGroup = document.getElementById('apprise-tags-custom-group');
    const customInput = document.getElementById('apprise-tags');
    if (!select || !customGroup || !customInput) return;

    if (!select.dataset.listenerAdded) {
        select.addEventListener('change', () => {
            customGroup.classList.toggle('hidden', select.value !== '__custom__');
        });
        select.dataset.listenerAdded = 'true';
    }

    try {
        const res = await apiFetch('/api/config/apprise/tags');
        const tags = (res && res.tags) ? res.tags : [];
        const desired = window.__desiredAppriseTags || '';

        const options = [];
        if (tags.length > 0) {
            options.push(...tags.map(t => `<option value="${escapeHtml(t)}">${escapeHtml(t)}</option>`));
        }
        options.push(`<option value="__custom__">${window.t ? window.t('config.other_manual') : 'Other (manual entry)\u2026'}</option>`);
        select.innerHTML = options.join('');

        if (desired && tags.includes(desired)) {
            select.value = desired;
            customGroup.classList.add('hidden');
        } else {
            select.value = '__custom__';
            customInput.value = desired;
            customGroup.classList.remove('hidden');
        }
    } catch (e) {
        select.innerHTML = `<option value="__custom__">${window.t ? window.t('config.other_manual') : 'Other (manual entry)\u2026'}</option>`;
        select.value = '__custom__';
        customInput.value = window.__desiredAppriseTags || '';
        customGroup.classList.remove('hidden');
    }
}

window.setOllamaCtx = function(val) {
    const el = document.getElementById('ollama-ctx');
    if (el) {
        el.value = val;
        syncCtxChips();
        // Trigger auto-save
        el.dispatchEvent(new Event('change'));
    }
}

window.setAppriseMax = (val) => {
    const input = document.getElementById('apprise-max-chars');
    if (input) {
        input.value = val;
        input.dispatchEvent(new Event('change'));
    }
    const inputD = document.getElementById('discord-max-chars');
    if (inputD) {
        inputD.value = val;
        inputD.dispatchEvent(new Event('change'));
    }
}

window.setDiscordMax = window.setAppriseMax;

function setupMaintenance() {
    const retentionSelect = document.getElementById('retention-period');
    const retentionCustom = document.getElementById('retention-days-custom');
    
    if (retentionSelect && retentionCustom) {
        retentionSelect.addEventListener('change', () => {
            if (retentionSelect.value === 'custom') {
                retentionCustom.classList.remove('hidden');
                retentionCustom.focus();
            } else {
                retentionCustom.classList.add('hidden');
            }
        });
    }

    const resetActions = [
        ['reset-mttr-btn', 'dashboard.confirm_reset_mttr', '/api/dashboard/reset-mttr', 'POST', 'config.reset_mttr_done'],
        ['clear-all-analyses-btn', 'dashboard.confirm_clear_all', '/api/dashboard/analyses/all/confirm', 'DELETE', 'config.clear_all_done'],
    ];
    resetActions.forEach(([id, confirmKey, url, method, doneKey]) => {
        const btn = document.getElementById(id);
        btn?.addEventListener('click', () => {
            showInlineConfirm(btn, window.t(confirmKey), async () => {
                try {
                    await apiFetch(url, { method });
                    cfgResult(`${id.replace('-btn', '')}-result`, true, window.t(doneKey));
                } catch (error) {
                    cfgResult(`${id.replace('-btn', '')}-result`, false, `${window.t('common.error')} : ${error.message}`);
                }
            });
        });
    });

    const cleanupBtn = document.getElementById('cleanup-btn');
    if (cleanupBtn) {
        cleanupBtn.addEventListener('click', () => {
            showInlineConfirm(cleanupBtn, cfgT('config.cleanup_confirm', 'Supprimer définitivement ces données ?'), async () => {
                cleanupBtn.disabled = true;
                try {
                    const res = await apiFetch('/api/config/maintenance/cleanup', { method: 'DELETE' });
                    cfgResult('cleanup-result', true, res.detail || cfgT('config.cleanup_done', 'Nettoyage terminé.'));
                    fetchMaintenanceStats();
                } catch (e) {
                    cfgResult('cleanup-result', false, `${cfgT('common.error', 'Erreur')} : ${e.message}`);
                    cleanupBtn.disabled = false;
                }
            });
        });
    }
}

async function fetchMaintenanceStats() {
    const usageVal = document.getElementById('disk-usage-val');
    const statsContainer = document.getElementById('cleanup-stats-container');
    const daysVal = document.getElementById('cleanup-days-val');
    const countVal = document.getElementById('cleanup-count-val');
    const cleanupBtn = document.getElementById('cleanup-btn');
    
    if (!usageVal) return;
    
    try {
        const res = await apiFetch('/api/config/maintenance/stats');
        
        usageVal.textContent = cfgSize(res.size_bytes);
        
        if (statsContainer && daysVal && countVal && cleanupBtn) {
            daysVal.textContent = res.retention_days;
            countVal.textContent = res.total_old_items;
            
            if (res.total_old_items > 0) {
                statsContainer.classList.remove('hidden');
                cleanupBtn.disabled = false;
            } else {
                statsContainer.classList.add('hidden');
                cleanupBtn.disabled = true;
            }
        }
    } catch (e) {
        usageVal.textContent = window.t ? window.t('config.disk_error') : 'Erreur';
    }
}

function setupBackupAndRestore() {
    const exportBtn = document.getElementById('export-btn');
    const exportHistory = document.getElementById('export-history');
    const importFile = document.getElementById('import-file');
    const importFileLabel = document.getElementById('import-file-label');
    const importBtn = document.getElementById('import-btn');

    if (!exportBtn || !importFile || !importBtn) return;

    exportBtn.addEventListener('click', () => {
        const withHistory = exportHistory ? exportHistory.checked : false;
        window.location.href = `/api/config/export?with_history=${withHistory}`;
    });

    importFile.addEventListener('change', () => {
        const file = importFile.files[0];
        if (file) {
            if (importFileLabel) importFileLabel.textContent = file.name;
            importBtn.disabled = false;
        } else {
            if (importFileLabel) importFileLabel.textContent = window.t ? window.t('config.select_backup_file') : 'Sélectionner le fichier ZIP de sauvegarde';
            importBtn.disabled = true;
        }
    });

    importBtn.addEventListener('click', async () => {
        const file = importFile.files[0];
        if (!file) return;

        const confirmMsg = window.t ? window.t('config.import_confirm_prompt') : 'Attention : L\'importation écrasera TOUTE la configuration actuelle. Continuer ?';
        showInlineConfirm(importBtn, confirmMsg, async () => {
            const formData = new FormData();
            formData.append('file', file);

            const oldText = importBtn.textContent;
            importBtn.disabled = true;
            importBtn.textContent = window.t ? window.t('config.importing') : 'Restauration en cours...';

            try {
                const response = await fetch('/api/config/import', {
                    method: 'POST',
                    body: formData
                });
                
                const result = await response.json();
                if (!response.ok) {
                    throw new Error(result.detail || (window.t ? window.t('config.import_error') : 'Erreur lors de l\'importation'));
                }
                
                window.location.reload();
            } catch (e) {
                cfgResult('import-result', false, `${cfgT('common.error', 'Erreur')} : ${e.message}`);
                importBtn.disabled = false;
                importBtn.textContent = oldText;
            }
        });
    });
}
