/* Méta-analyse : un tableau de bord par périmètre. Le dernier rapport de l'IA, puis ce que disent les alertes dans le
   temps (calculé sans IA, donc disponible même avant le premier rapport). Lancer et régler sont des pages à part. */

const RANGES = [7, 30, 90, 180];
const META = { configs: [], rules: [], id: null, view: 'dash', latestShown: {}, preview: {}, abort: null, running: new Set(),
    days: 30, overview: null, filter: null, showAll: false, reports: [], reportId: null, loadToken: 0 };
const EVENTS_SHOWN = 8;

function mT(key, fallback, vars) {
    let text = window.t ? window.t(key) : null;
    if (!text || text === key) text = fallback;
    // « 1 règle », « 3 règles » : en français zéro reste au singulier, en anglais non
    if (vars && typeof vars.n === 'number') {
        const many = vars.n > 1 || (vars.n === 0 && document.documentElement.lang === 'en');
        text = text.split('(s)').join(many ? 's' : '');
    }
    Object.entries(vars || {}).forEach(([k, v]) => { text = text.split(`{${k}}`).join(v); });
    return text;
}

function fmtDateTime(value) {
    return new Date(value).toLocaleString(document.documentElement.lang || undefined,
        { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

const STATE_VIEW = {
    ok: ['ok', 'meta.state_ok', 'Tout va bien'],
    degraded: ['warn', 'meta.state_degraded', 'À surveiller'],
    critical: ['alert', 'meta.state_critical', 'Critique'],
    failed: ['alert', 'meta.state_failed', 'Analyse échouée'],
};

function resultState(status) {
    const view = STATE_VIEW[status];
    return view ? { dot: view[0], label: mT(view[1], view[2]) } : { dot: 'idle', label: mT('meta.state_unknown', 'État non précisé') };
}

/* ── Planification ───────────────────────────────────────────────────────── */

// L'heure est stockée en UTC, saisie et affichée en heure locale
function localToUTC(hhmm) {
    const [h, m] = hhmm.split(':').map(Number);
    const total = (h * 60 + m + new Date().getTimezoneOffset() + 1440) % 1440;
    return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
}

function utcToLocal(hhmm) {
    const [h, m] = hhmm.split(':').map(Number);
    const total = (h * 60 + m - new Date().getTimezoneOffset() + 1440) % 1440;
    return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
}

/** Prochaine exécution et période couverte. */
function computeNextRun(c) {
    const now = new Date();
    const [h, m] = (c.schedule_time || '00:00').split(':').map(Number);
    let next;
    if (c.schedule_type === 'weekly') {
        next = new Date(now);
        next.setUTCHours(h, m, 0, 0);
        let diff = ((c.schedule_day || 1) % 7) - now.getUTCDay();  // 1 = lundi … 7 = dimanche
        if (diff < 0 || (diff === 0 && next <= now)) diff += 7;
        next.setUTCDate(next.getUTCDate() + diff);
    } else if (c.schedule_type === 'monthly') {
        const day = c.schedule_day || 1;
        next = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), day, h, m, 0));
        if (next <= now) next = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + 1, day, h, m, 0));
    } else {
        next = new Date(now);
        next.setUTCHours(h, m, 0, 0);
        if (next <= now) next.setUTCDate(next.getUTCDate() + 1);
    }
    return next;
}

function updateScheduleDay(value) {
    const type = document.getElementById('config-schedule-type').value;
    const select = document.getElementById('config-schedule-day');
    select.classList.toggle('hidden', type === 'daily');
    if (type === 'weekly') {
        const days = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'];
        select.innerHTML = days.map((d, i) => `<option value="${i + 1}">${escapeHtml(mT(`meta.weekday_${d}`, d))}</option>`).join('');
    } else if (type === 'monthly') {
        select.innerHTML = Array.from({ length: 31 }, (_, i) => `<option value="${i + 1}">${i + 1}</option>`).join('');
    }
    if (value) select.value = value;
}

/* ── Sources : d'où viennent les lignes des règles ──────────────────────── */

function sourceOf(rule) {
    const path = rule.log_file_path || '';
    if (path.startsWith('[SYSLOG]')) {
        const host = (path.split(':')[1] || '').trim();
        return { key: `syslog:${host}`, label: host || mT('meta.source_syslog', 'Syslog') };
    }
    if (path.startsWith('[WEBHOOK]')) return { key: 'webhook', label: mT('meta.source_webhook', 'Webhooks') };
    const connected = path.match(/^\[(DOCKER|HOMEASSISTANT|MQTT)\]/);
    if (connected) return { key: connected[1], label: { DOCKER: 'Docker', HOMEASSISTANT: 'Home Assistant', MQTT: 'MQTT' }[connected[1]] };
    return { key: 'file', label: mT('meta.source_file', 'Fichiers de ce serveur') };
}

function rulesOf(c) {
    const followed = c.rule_ids_json || [];
    return followed.length ? META.rules.filter(r => followed.includes(r.id)) : META.rules;
}

function sourceGroups(rules) {
    const groups = new Map();
    rules.forEach(r => {
        const source = sourceOf(r);
        if (!groups.has(source.key)) groups.set(source.key, { ...source, rules: [] });
        groups.get(source.key).rules.push(r);
    });
    return [...groups.values()];
}

/* ── Boutons du haut ─────────────────────────────────────────────────────── */

function tileView(c) {
    if (META.running.has(c.id)) return { dot: 'idle', text: mT('meta.state_running', 'Analyse en cours…') };
    if (c.missing_rules && c.rule_ids_json.length === c.missing_rules) return { dot: 'warn', text: mT('meta.state_missing_rules', "Ses règles n'existent plus") };
    if (!c.enabled && !c.latest) return { dot: 'idle', text: mT('meta.state_off', 'Désactivée') };
    if (!c.latest) return { dot: 'idle', text: mT('meta.state_none', 'Pas encore de rapport') };
    const state = resultState(c.latest.status);
    const off = c.enabled ? '' : ` · ${mT('meta.state_off', 'Désactivée')}`;
    return { dot: state.dot, text: `${state.label} · ${relativeTime(c.latest.created_at)}${off}` };
}

const PLUS_ICON = '<svg class="meta-plus" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M8 3v10M3 8h10" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';

const GEAR_ICON = '<svg class="btn-glyph" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/></svg>';

function renderTiles() {
    const nav = document.getElementById('meta-tiles');
    const isNew = location.hash === '#new';
    const tiles = META.configs.map(c => {
        const view = tileView(c);
        const active = c.id === META.id && !isNew;
        return `<a class="cfg-status-item${active ? ' active' : ''}" href="#${c.id}"${active ? ' aria-current="page"' : ''} data-config="${c.id}">
            <span class="rp-dot rp-dot--${view.dot}"></span>
            <span class="cfg-status-text"><strong>${escapeHtml(c.name)}</strong><span class="cfg-status-detail">${escapeHtml(view.text)}</span></span>
        </a>`;
    });
    tiles.push(`<a class="cfg-status-item meta-tile-new${isNew ? ' active' : ''}" href="#new"${isNew ? ' aria-current="page"' : ''}>
        ${PLUS_ICON}
        <span class="cfg-status-text"><strong>${escapeHtml(mT('meta.new_config', 'Nouvelle méta-analyse'))}</strong><span class="cfg-status-detail">${escapeHtml(mT('meta.new_config_hint', 'Une machine, un service, ou tout le parc'))}</span></span>
    </a>`);
    nav.innerHTML = tiles.join('');
    nav.classList.toggle('hidden', !META.configs.length && !isNew);
    document.getElementById('meta-empty').classList.toggle('hidden', META.configs.length > 0 || isNew);
}

function configOf(id) {
    return META.configs.find(c => c.id === id);
}

async function refreshConfigs() {
    const [configs, running] = await Promise.all([apiFetch('/api/meta-analysis/configs'), apiFetch('/api/meta-analysis/running')]);
    // Les plus récentes d'abord, celles qui ne tournent plus à la fin
    configs.sort((a, b) => (b.enabled - a.enabled) || String(b.latest?.created_at || '').localeCompare(String(a.latest?.created_at || '')));
    META.configs = configs;
    META.running = new Set((running.running || []).map(Number));
    renderTiles();
}

/** Mise à jour de fond : ne remplace jamais ce qu'on est en train de lire. */
async function backgroundRefresh() {
    if (document.hidden) return;
    try {
        await refreshConfigs();
    } catch (_) { return; }
    const c = configOf(META.id);
    if (c && c.latest && META.view === 'dash' && META.latestShown[c.id] !== c.latest.id) {
        document.getElementById('meta-new-banner').classList.remove('hidden');
    }
    if (c && META.view === 'dash') renderPlan(c);
    renderRunState();
}

/* ── Navigation ──────────────────────────────────────────────────────────── */

function route() {
    const hash = location.hash.slice(1);
    document.getElementById('meta-new-banner').classList.add('hidden');
    hideTip();
    if (hash === 'new') {
        META.id = null;
        renderTiles();
        showView('settings');
        document.getElementById('meta-settings-back').classList.toggle('hidden', !META.configs.length);
        document.getElementById('meta-settings-back').href = '#';
        fillForm(null);
        return;
    }
    const [idText, viewName] = hash.split('/');
    const config = configOf(Number(idText)) || META.configs[0];
    META.id = config ? config.id : null;
    renderTiles();
    if (!config) {
        ['dash', 'next', 'settings'].forEach(v => document.getElementById(`meta-view-${v}`).classList.add('hidden'));
        return;
    }
    const view = ['next', 'settings'].includes(viewName) ? viewName : 'dash';
    document.getElementById('meta-next-back').href = `#${config.id}`;
    document.getElementById('meta-settings-back').href = `#${config.id}`;
    document.getElementById('meta-settings-back').classList.remove('hidden');
    showView(view);
    if (view === 'dash') renderDash(config);
    else if (view === 'next') renderNext(config);
    else fillForm(config);
}

function showView(view) {
    META.view = view;
    ['dash', 'next', 'settings'].forEach(v => document.getElementById(`meta-view-${v}`).classList.toggle('hidden', v !== view));
}

/* ── Tableau de bord ─────────────────────────────────────────────────────── */

function renderDash(c) {
    META.filter = null;
    META.showAll = false;
    META.overview = null;
    META.reportId = null;
    const token = ++META.loadToken;
    renderPlan(c);
    renderAbout(c);
    loadReports(c, token);
    loadOverview(c);
}

function frequencyText(c) {
    const time = utcToLocal(c.schedule_time || '00:00');
    if (c.schedule_type === 'weekly') {
        // Le 1er janvier 2024 est un lundi
        const day = new Date(Date.UTC(2024, 0, c.schedule_day || 1)).toLocaleDateString(document.documentElement.lang || undefined, { weekday: 'long', timeZone: 'UTC' });
        return mT('meta.freq_weekly', 'chaque {day} à {time}', { day, time });
    }
    if (c.schedule_type === 'monthly') return mT('meta.freq_monthly', 'le {day} de chaque mois à {time}', { day: c.schedule_day || 1, time });
    return mT('meta.freq_daily', 'tous les jours à {time}', { time });
}

function renderPlan(c) {
    const rules = rulesOf(c);
    const groups = sourceGroups(rules);
    const running = META.running.has(c.id);
    const when = running ? mT('meta.state_running', 'Analyse en cours…')
        : c.enabled ? fmtDateTime(computeNextRun(c)) : mT('meta.next_off', 'Désactivée : elle ne tourne que si tu la lances.');
    const scope = mT('meta.scope_rules', '{n} règle(s)', { n: rules.length }) + (c.rule_ids_json.length ? '' : ` (${mT('meta.all_rules', 'toutes')})`);
    document.getElementById('meta-plan').innerHTML = `
        <h2>${escapeHtml(mT('meta.plan_title', 'Prochain rapport'))}</h2>
        <p class="meta-plan-when">${escapeHtml(when)}</p>
        ${c.enabled ? `<p class="form-hint">${escapeHtml(frequencyText(c))}</p>` : ''}
        ${warningHtml(c)}
        <dl class="meta-plan-facts">
            <div><dt>${escapeHtml(mT('meta.plan_scope', 'Couvre'))}</dt><dd>${escapeHtml(scope)}</dd></div>
            <div><dt>${escapeHtml(mT('meta.plan_sources', 'Sources'))}</dt><dd>${escapeHtml(groups.map(g => g.label).join(', ') || '—')}</dd></div>
        </dl>
        <div class="meta-plan-actions">
            <a class="btn btn-primary btn-sm" href="#${c.id}/next">${escapeHtml(mT('meta.run_custom', 'Lancer maintenant'))}</a>
            <a class="btn btn-secondary btn-sm meta-settings-btn" href="#${c.id}/settings">${GEAR_ICON}${escapeHtml(mT('meta.tab_settings', 'Réglages'))}</a>
        </div>`;
}

function renderAbout(c) {
    const o = META.overview;
    const n = (value) => (o ? value : '…');
    const steps = [
        [mT('meta.flow_rules', '{n} règle(s)', { n: rulesOf(c).length }), mT('meta.flow_rules_text', 'surveillées chacune de leur côté')],
        [mT('meta.flow_alerts', '{n} alerte(s)', { n: n(o?.totals.alerts) }), mT('meta.flow_alerts_text', 'analysées une par une, sur {days} jours', { days: META.days })],
        [mT('meta.flow_events', '{n} événement(s)', { n: n(o?.totals.events) }), mT('meta.flow_events_text', 'une fois les répétitions regroupées')],
        [mT('meta.flow_report', '1 rapport'), mT('meta.flow_report_text', 'ce que l’ensemble raconte, écrit par l’IA')],
    ];
    const effects = [
        mT('meta.effect_readonly', 'Ne modifie ni les règles ni les alertes.'),
        mT('meta.effect_chain', 'Le rapport suivant repart de celui-ci pour comparer.'),
        mT('meta.effect_busy', 'Occupe l’IA pendant le calcul : les analyses d’alertes attendent leur tour.'),
        c.notify_enabled ? mT('meta.effect_notify', 'Envoie une notification à la fin.') : mT('meta.effect_no_notify', 'N’envoie pas de notification.'),
    ];
    document.getElementById('meta-about').innerHTML = `
        <h2>${escapeHtml(mT('meta.about_title', 'Comment elle travaille'))}</h2>
        <ol class="meta-flow">${steps.map(([value, text]) => `<li><strong>${escapeHtml(String(value))}</strong><span>${escapeHtml(text)}</span></li>`).join('')}</ol>
        <ul class="meta-effects">${effects.map(text => `<li>${escapeHtml(text)}</li>`).join('')}</ul>`;
}

/* ── Rapports ────────────────────────────────────────────────────────────── */

async function loadReports(c, token) {
    const card = document.getElementById('meta-report');
    const list = document.getElementById('meta-reports');
    card.innerHTML = `<div class="loading">${escapeHtml(mT('common.loading', 'Chargement…'))}</div>`;
    list.innerHTML = '';
    try {
        const reports = await apiFetch(`/api/meta-analysis/results?config_id=${c.id}&limit=30&compact=true`);
        if (token !== META.loadToken) return;
        META.reports = reports;
        META.latestShown[c.id] = reports.length ? reports[0].id : null;
        renderReportList(c);
        if (reports.length) {
            await showReport(reports[0].id, token);
        } else {
            const hint = c.enabled ? mT('meta.no_result_next', 'Premier rapport : {when}.', { when: fmtDateTime(computeNextRun(c)) }) : mT('meta.no_result_off', 'Elle est désactivée : elle ne tourne que si tu la lances.');
            card.innerHTML = `<div class="meta-empty">
                <h2>${escapeHtml(mT('meta.no_result_title', 'Pas encore de rapport'))}</h2>
                <p class="form-hint">${escapeHtml(hint)} ${escapeHtml(mT('meta.no_result_data', 'Les alertes ci-dessous sont déjà là : le rapport y ajoutera la lecture de l’IA.'))}</p>
                <a class="btn btn-primary btn-sm" href="#${c.id}/next">${escapeHtml(mT('meta.run_first', 'Lancer le premier rapport'))}</a>
            </div>`;
        }
    } catch (e) {
        card.innerHTML = `<p class="form-hint rf-ko">${escapeHtml(e.message)}</p>`;
    }
}

function renderReportList(c) {
    const el = document.getElementById('meta-reports');
    el.classList.toggle('hidden', !META.reports.length);
    if (!META.reports.length) {
        el.innerHTML = '';
        return;
    }
    el.innerHTML = `<h2>${escapeHtml(mT('meta.reports_title', 'Rapports'))}</h2>
        <ol class="meta-report-list">${META.reports.map(r => {
            const state = resultState(r.status);
            const current = r.id === META.reportId;
            return `<li><button type="button" class="meta-report-item${current ? ' active' : ''}" data-report="${r.id}"${current ? ' aria-current="true"' : ''}>
                <span class="rp-dot rp-dot--${state.dot}"></span>
                <span class="meta-report-date">${escapeHtml(fmtDateTime(r.created_at))}</span>
                <span class="meta-report-state">${escapeHtml(state.label)}</span>
            </button></li>`;
        }).join('')}</ol>`;
}

async function showReport(id, token) {
    const card = document.getElementById('meta-report');
    try {
        const report = await apiFetch(`/api/meta-analysis/results/${id}`);
        if (token !== undefined && token !== META.loadToken) return;
        META.reportId = id;
        card.innerHTML = reportHtml(report);
        card.querySelectorAll('.markdown-body').forEach(el => highlightDOMText(el, report.matched_keywords));
        renderReportList(configOf(report.config_id));
    } catch (e) {
        card.innerHTML = `<p class="form-hint rf-ko">${escapeHtml(e.message)}</p>`;
    }
}

function reportHtml(r) {
    const state = resultState(r.status);
    const latest = META.reports[0]?.id === r.id;
    const period = mT('meta.period_range', 'du {start} au {end}', { start: fmtDateTime(r.period_start), end: fmtDateTime(r.period_end) });
    const back = latest ? '' : `<button type="button" class="meta-link" data-report="${META.reports[0]?.id}">${escapeHtml(mT('meta.back_latest', 'Revenir au dernier rapport'))}</button>`;
    const list = (title, items, tag) => (items && items.length ? `<div class="meta-section"><h3>${escapeHtml(title)}</h3>
        <${tag}>${items.map(i => `<li>${escapeHtml(i)}</li>`).join('')}</${tag}></div>` : '');
    if (r.status === 'failed') {
        return `<div class="meta-verdict">
                <div class="meta-verdict-state"><span class="rp-dot rp-dot--${state.dot}"></span><strong>${escapeHtml(state.label)}</strong>
                    <span class="rp-muted">${escapeHtml(fmtDateTime(r.created_at))}</span>${back}</div>
                <p class="meta-tldr">${escapeHtml(mT('meta.failed_text', 'La période reste à analyser : Sentinel réessaie toutes les heures.'))}</p>
            </div>
            <pre class="meta-entry-line">${escapeHtml(r.ollama_response || '')}</pre>
            <div class="meta-result-actions">
                <a class="btn btn-primary btn-sm" href="#${r.config_id}/next">${escapeHtml(mT('meta.run_custom', 'Lancer maintenant'))}</a>
                <span class="meta-spacer"></span>
                <button type="button" class="btn btn-secondary btn-sm" onclick="deleteResult(${r.id}, this)">${escapeHtml(mT('common.delete', 'Supprimer'))}</button>
            </div>`;
    }
    const structured = r.highlights.length || r.recommendations.length || r.synthesis;
    const details = structured
        ? (r.synthesis ? `<details class="meta-sources"><summary>${escapeHtml(mT('meta.details', 'Analyse détaillée'))}</summary><div class="meta-sources-body"><div class="markdown-body">${renderMarkdown(r.synthesis)}</div></div></details>` : '')
        : `<div class="meta-section"><div class="markdown-body">${renderMarkdown(r.ollama_response || '')}</div></div>`;
    const ids = (r.detection_ids || []).map(id => `<a class="btn btn-secondary btn-sm" href="/monitor?search=${encodeURIComponent(id)}">#${escapeHtml(id)}</a>`).join('');
    const kws = (r.matched_keywords || []).map(k => `<span class="log-kw-badge">${escapeHtml(k)}</span>`).join('');
    const sources = ids || kws || r.context_sent ? `<details class="meta-sources">
        <summary>${escapeHtml(mT('meta.sources_title', 'Sources et contexte envoyé'))}</summary>
        <div class="meta-sources-body">
            ${kws ? `<div class="meta-sources-row">${kws}</div>` : ''}
            ${ids ? `<div class="meta-sources-row">${ids}</div>` : ''}
            ${r.context_sent ? `<pre class="meta-context">${escapeHtml(r.context_sent)}</pre>` : ''}
        </div>
    </details>` : '';
    return `<div class="meta-verdict">
            <div class="meta-verdict-state">
                <span class="rp-dot rp-dot--${state.dot}"></span><strong>${escapeHtml(state.label)}</strong>
                <span class="rp-muted">${escapeHtml(period)} · ${escapeHtml(mT('meta.alerts_n', '{n} alerte(s)', { n: r.analyses_count }))}</span>${back}
            </div>
            ${r.tldr ? `<p class="meta-tldr">${escapeHtml(r.tldr)}</p>` : ''}
        </div>
        ${list(mT('meta.highlights', 'Points marquants'), r.highlights, 'ul') || list(mT('meta.recommendations', 'Recommandations'), r.recommendations, 'ol') ? `<div class="meta-report-cols">
            ${list(mT('meta.highlights', 'Points marquants'), r.highlights, 'ul')}
            ${list(mT('meta.recommendations', 'Recommandations'), r.recommendations, 'ol')}
        </div>` : ''}
        ${details}
        ${sources}
        <div class="meta-result-actions">
            <button type="button" class="btn btn-primary btn-sm" onclick="deepenResult(${r.id}, this)">${escapeHtml(mT('common.deepen', 'Approfondir avec l’IA'))}</button>
            <button type="button" class="btn btn-secondary btn-sm" onclick="notifyResult(${r.id}, this)">${escapeHtml(mT('common.notify', 'Envoyer la notification'))}</button>
            <span class="meta-spacer"></span>
            <button type="button" class="btn btn-secondary btn-sm" onclick="deleteResult(${r.id}, this)">${escapeHtml(mT('common.delete', 'Supprimer'))}</button>
        </div>`;
}

function warningHtml(c) {
    return c.missing_rules ? `<p class="form-hint rf-ko meta-warning">${escapeHtml(mT('meta.missing_rules_warning', '{n} règle(s) suivie(s) n’existent plus.', { n: c.missing_rules }))}</p>` : '';
}

/* ── Ce que disent les alertes : calculé sans IA ─────────────────────────── */

async function loadOverview(c) {
    const token = META.loadToken;
    renderRange();
    const heat = document.getElementById('meta-heat');
    if (!META.overview) heat.innerHTML = `<div class="loading">${escapeHtml(mT('common.loading', 'Chargement…'))}</div>`;
    try {
        const tz = new Date().getTimezoneOffset();
        const overview = await apiFetch(`/api/meta-analysis/configs/${c.id}/overview?days=${META.days}&tz=${tz}`);
        if (token !== META.loadToken || META.id !== c.id) return;
        META.overview = overview;
        renderOverview(c);
    } catch (e) {
        heat.innerHTML = `<p class="form-hint rf-ko">${escapeHtml(e.message)}</p>`;
    }
}

function renderRange() {
    document.getElementById('meta-range').innerHTML = RANGES.map(days => `<button type="button" data-days="${days}" aria-pressed="${days === META.days}"${days === META.days ? ' class="active"' : ''}>${escapeHtml(mT('meta.range_days', '{n} j', { n: days }))}</button>`).join('');
}

function renderOverview(c) {
    const o = META.overview;
    renderAbout(c);
    const sentence = o.totals.previous_alerts === null
        ? mT('meta.long_sentence_alone', '{n} alerte(s) sur {days} jours. Calculé directement à partir des alertes, sans IA.', { n: o.totals.alerts, days: o.days })
        : mT('meta.long_sentence', '{n} alerte(s) sur {days} jours, contre {prev} sur les {days} jours d’avant. Calculé directement à partir des alertes, sans IA.', { n: o.totals.alerts, days: o.days, prev: o.totals.previous_alerts });
    document.getElementById('meta-long-sentence').textContent = sentence;
    renderChanges();
    const note = document.getElementById('meta-long-note');
    const since = o.history_since ? new Date(o.history_since).toLocaleDateString(document.documentElement.lang || undefined) : null;
    const noteText = !o.comparable ? (o.history_since ? mT('meta.note_no_history', 'Pas assez d’historique pour comparer : les alertes commencent le {date}. Une période plus courte permet la comparaison.', { date: since }) : '')
        : o.partial ? mT('meta.note_partial', 'Comparaison partielle : les alertes conservées commencent le {date}.', { date: since }) : '';
    note.textContent = noteText;
    note.classList.toggle('hidden', !noteText);
    renderHeat();
    renderEvents();
}

const CHANGE_VIEW = {
    new: ['meta.change_new', 'Nouveaux', 'meta.change_new_text', 'jamais vus avant cette période'],
    worse: ['meta.change_worse', 'S’aggravent', 'meta.change_worse_text', 'plus graves, ou deux fois plus fréquents'],
    returning: ['meta.change_returning', 'Reviennent', 'meta.change_returning_text', 'déjà vus, sans s’aggraver'],
    gone: ['meta.change_gone', 'Plus vus', 'meta.change_gone_text', 'présents avant, absents depuis'],
};

function renderChanges() {
    const o = META.overview;
    const el = document.getElementById('meta-changes');
    el.classList.toggle('hidden', !o.counts);
    if (!o.counts) {
        el.innerHTML = '';
        return;
    }
    el.innerHTML = Object.entries(CHANGE_VIEW).map(([kind, view]) => {
        const active = META.filter === kind;
        return `<button type="button" class="cfg-status-item meta-change${active ? ' active' : ''}" data-kind="${kind}" aria-pressed="${active}">
            <span class="cfg-status-text"><span class="meta-change-count">${o.counts[kind]}</span><strong>${escapeHtml(mT(view[0], view[1]))}</strong><span class="cfg-status-detail">${escapeHtml(mT(view[2], view[3]))}</span></span>
        </button>`;
    }).join('');
}

function bucketLabel(o, index) {
    const fmt = { day: 'numeric', month: 'short', timeZone: 'UTC' };
    const lang = document.documentElement.lang || undefined;
    const start = new Date(`${o.buckets[index]}T00:00:00Z`);
    if (o.bucket_days === 1) return start.toLocaleDateString(lang, fmt);
    const end = new Date(start.getTime() + (o.bucket_days - 1) * 86400000);
    return `${start.toLocaleDateString(lang, fmt)} – ${end.toLocaleDateString(lang, fmt)}`;
}

function cellSeverity(cell) {
    return cell.critical ? 'critical' : cell.warning ? 'warning' : 'info';
}

function cellLevel(count) {
    return count >= 10 ? 3 : count >= 2 ? 2 : 1;
}

function renderHeat() {
    const o = META.overview;
    const el = document.getElementById('meta-heat');
    const n = o.buckets.length;
    const rows = o.rules.map((r, ri) => {
        const byIndex = Object.fromEntries(r.cells.map(cell => [cell.i, cell]));
        const cells = o.buckets.map((_, i) => {
            const cell = byIndex[i];
            if (!cell) return `<span class="meta-cell" data-rule="${ri}" data-i="${i}"></span>`;
            const count = cell.critical + cell.warning + cell.info;
            return `<span class="meta-cell meta-cell--${cellSeverity(cell)} meta-cell--l${cellLevel(count)}" data-rule="${ri}" data-i="${i}"></span>`;
        }).join('');
        const summary = r.total
            ? mT('meta.heat_row', '{n} alerte(s)', { n: r.total }) + (r.critical ? ` · ${mT('meta.heat_row_critical', '{n} critique(s)', { n: r.critical })}` : '')
            : mT('meta.heat_row_none', 'aucune alerte');
        return `<div class="meta-heat-row">
            <a class="meta-heat-name" href="/rules/${r.id}" title="${escapeHtml(r.name)}">${escapeHtml(r.name)}${r.enabled ? '' : ` <span class="rp-muted">(${escapeHtml(mT('meta.rule_paused', 'en pause'))})</span>`}</a>
            <div class="meta-heat-cells" style="--n:${n}" role="img" aria-label="${escapeHtml(`${r.name} : ${summary}`)}">${cells}</div>
            <span class="meta-heat-total">${escapeHtml(summary)}</span>
        </div>`;
    }).join('');
    const mid = Math.floor((n - 1) / 2);
    const short = i => new Date(`${o.buckets[i]}T00:00:00Z`).toLocaleDateString(document.documentElement.lang || undefined, { day: 'numeric', month: 'short', timeZone: 'UTC' });
    const table = `<details class="meta-sources"><summary>${escapeHtml(mT('meta.heat_table', 'Voir en tableau'))}</summary>
        <div class="meta-table-wrap"><table class="meta-table"><thead><tr><th>${escapeHtml(mT('meta.table_rule', 'Règle'))}</th><th>${escapeHtml(mT('meta.table_alerts', 'Alertes'))}</th><th>critical</th><th>warning</th><th>info</th><th>${escapeHtml(mT('meta.table_events', 'Événements'))}</th></tr></thead>
        <tbody>${o.rules.map(r => `<tr><td>${escapeHtml(r.name)}</td><td>${r.total}</td><td>${r.critical}</td><td>${r.warning}</td><td>${r.info}</td><td>${r.events}</td></tr>`).join('')}</tbody></table></div></details>`;
    el.innerHTML = `<div class="meta-heat-head">
            <h3 id="meta-heat-title">${escapeHtml(mT('meta.heat_title', 'Activité par règle'))}</h3>
            <div class="meta-legend" aria-hidden="true">
                <span><i class="meta-cell meta-cell--critical meta-cell--l3"></i>${escapeHtml(mT('meta.legend_critical', 'Critique'))}</span>
                <span><i class="meta-cell meta-cell--warning meta-cell--l3"></i>${escapeHtml(mT('meta.legend_warning', 'Attention'))}</span>
                <span><i class="meta-cell meta-cell--info meta-cell--l3"></i>${escapeHtml(mT('meta.legend_info', 'Info'))}</span>
                <span class="rp-muted">${escapeHtml(mT('meta.legend_level', 'Plus c’est soutenu, plus il y a d’alertes'))}</span>
            </div>
        </div>
        ${rows || `<p class="form-hint">${escapeHtml(mT('meta.heat_empty', 'Aucune règle suivie.'))}</p>`}
        ${rows ? `<div class="meta-heat-axis" aria-hidden="true"><span></span><div class="meta-heat-ticks"><span>${escapeHtml(short(0))}</span><span>${n > 2 ? escapeHtml(short(mid)) : ''}</span><span>${escapeHtml(short(n - 1))}</span></div><span></span></div>${table}` : ''}`;
}

function hideTip() {
    document.getElementById('meta-tip').classList.add('hidden');
}

function showTip(cell, x, y) {
    const o = META.overview;
    const rule = o && o.rules[Number(cell.dataset.rule)];
    if (!rule) return;
    const i = Number(cell.dataset.i);
    const data = rule.cells.find(c => c.i === i);
    const tip = document.getElementById('meta-tip');
    const counts = data
        ? ['critical', 'warning', 'info'].filter(s => data[s]).map(s => `${data[s]} ${s}`).join(' · ')
        : mT('meta.heat_row_none', 'aucune alerte');
    tip.textContent = '';
    const title = document.createElement('strong');
    title.textContent = bucketLabel(o, i);
    const name = document.createElement('span');
    name.textContent = rule.name;
    const value = document.createElement('span');
    value.textContent = counts;
    tip.append(title, name, value);
    tip.classList.remove('hidden');
    const box = tip.getBoundingClientRect();
    tip.style.left = `${Math.max(8, Math.min(x + 12, window.innerWidth - box.width - 8))}px`;
    tip.style.top = `${Math.max(8, y - box.height - 12)}px`;
}

const KIND_LABEL = {
    new: ['meta.kind_new', 'Nouveau'],
    worse: ['meta.kind_worse', 'S’aggrave'],
    returning: ['meta.kind_returning', 'Revient'],
    gone: ['meta.kind_gone', 'Plus vu'],
};

function eventRow(e) {
    const severity = escapeHtml(e.severity);
    const label = KIND_LABEL[e.kind];
    const count = e.kind === 'gone'
        ? mT('meta.event_gone', '{n} fois avant cette période', { n: e.previous_count })
        : e.previous_count
            ? mT('meta.event_count_prev', '{n} fois ({prev} avant)', { n: e.count, prev: e.previous_count })
            : mT('meta.event_count', '{n} fois', { n: e.count });
    const when = e.last_at ? mT('meta.event_last', 'dernière fois {when}', { when: relativeTime(e.last_at) }) : '';
    const href = e.analysis_id ? `/rules/${e.rule_id}?analysis=${e.analysis_id}#analyses` : `/rules/${e.rule_id}`;
    return `<a class="meta-event" href="${href}">
        <span class="severity-badge ${severity}">${severity}</span>
        <span class="meta-event-body">
            <span class="meta-event-text">${escapeHtml(e.summary || mT('meta.event_no_summary', 'Sans résumé'))}</span>
            <span class="rp-muted">${escapeHtml([e.rule_name, count, when].filter(Boolean).join(' · '))}</span>
        </span>
        <span class="meta-event-tags">
            ${label ? `<span class="meta-kind meta-kind--${e.kind}">${escapeHtml(mT(label[0], label[1]))}</span>` : ''}
            ${e.flagged ? `<span class="meta-kind">${escapeHtml(mT('meta.event_flagged', 'Signalé pas pertinent'))}</span>` : ''}
        </span>
    </a>`;
}

function renderEvents() {
    const o = META.overview;
    const el = document.getElementById('meta-events');
    const events = o.events.filter(e => (META.filter ? e.kind === META.filter : e.kind !== 'gone'));
    const shown = META.showAll ? events : events.slice(0, EVENTS_SHOWN);
    const title = META.filter ? mT(CHANGE_VIEW[META.filter][0], CHANGE_VIEW[META.filter][1]) : mT('meta.events_title', 'Événements de la période');
    const more = events.length - shown.length;
    const capped = o.events_total > o.events.length ? mT('meta.events_capped', 'Les {n} plus importants sont listés.', { n: o.events.length }) : '';
    el.innerHTML = `<div class="meta-heat-head">
            <h3 id="meta-events-title">${escapeHtml(title)}</h3>
            ${META.filter ? `<button type="button" class="meta-link" id="meta-filter-clear">${escapeHtml(mT('meta.events_clear', 'Tout afficher'))}</button>` : ''}
        </div>
        ${shown.length ? `<div class="meta-event-list">${shown.map(eventRow).join('')}</div>` : `<p class="form-hint">${escapeHtml(META.filter ? mT('meta.events_none_kind', 'Aucun événement dans cette catégorie.') : mT('meta.events_none', 'Aucune alerte sur cette période.'))}</p>`}
        ${more > 0 ? `<button type="button" class="btn btn-secondary btn-sm meta-events-more" id="meta-events-more">${escapeHtml(mT('meta.events_more', 'Afficher les {n} autres', { n: more }))}</button>` : ''}
        ${capped && META.showAll ? `<p class="form-hint">${escapeHtml(capped)}</p>` : ''}`;
}

function setupDash() {
    document.getElementById('meta-view-dash').addEventListener('click', e => {
        const report = e.target.closest('[data-report]');
        if (report && report.dataset.report) {
            showReport(Number(report.dataset.report));
            return;
        }
        const range = e.target.closest('[data-days]');
        if (range) {
            META.days = Number(range.dataset.days);
            try {
                localStorage.setItem('sentinel_meta_days', String(META.days));
            } catch (_) { /* stockage indisponible : le choix vaut pour cette visite */ }
            META.filter = null;
            META.showAll = false;
            const c = configOf(META.id);
            if (c) loadOverview(c);
            return;
        }
        const change = e.target.closest('[data-kind]');
        if (change) {
            META.filter = META.filter === change.dataset.kind ? null : change.dataset.kind;
            META.showAll = false;
            renderChanges();
            renderEvents();
            return;
        }
        if (e.target.closest('#meta-filter-clear')) {
            META.filter = null;
            renderChanges();
            renderEvents();
            return;
        }
        if (e.target.closest('#meta-events-more')) {
            META.showAll = true;
            renderEvents();
        }
    });
    const heat = document.getElementById('meta-heat');
    heat.addEventListener('pointermove', e => {
        const cell = e.target.closest('.meta-cell[data-rule]');
        if (cell) showTip(cell, e.clientX, e.clientY); else hideTip();
    });
    heat.addEventListener('pointerleave', e => { if (e.pointerType === 'mouse') hideTip(); });
    heat.addEventListener('click', e => {
        const cell = e.target.closest('.meta-cell[data-rule]');
        if (cell) showTip(cell, e.clientX, e.clientY);
    });
    window.addEventListener('scroll', hideTip, { passive: true });
}

/** Surligne les mots-clés sans jamais interpréter le texte comme du HTML : il vient des logs et du LLM. */
function highlightDOMText(element, keywords) {
    const escaped = (keywords || []).filter(k => k && k.trim()).map(k => k.trim().replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
    if (!escaped.length) return;
    const pattern = new RegExp(`(${escaped.join('|')})`, 'gi');
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach(node => {
        const parent = node.parentNode;
        if (['MARK', 'CODE', 'PRE', 'TEXTAREA'].includes(parent.nodeName)) return;
        const parts = node.nodeValue.split(pattern);
        if (parts.length < 2) return;
        const fragment = document.createDocumentFragment();
        parts.forEach((part, i) => {
            if (i % 2) {
                const mark = document.createElement('mark');
                mark.className = 'kw-highlight';
                mark.textContent = part;
                fragment.append(mark);
            } else if (part) {
                fragment.append(document.createTextNode(part));
            }
        });
        parent.replaceChild(fragment, node);
    });
}

async function deepenResult(resultId, btn) {
    btn.disabled = true;
    try {
        const r = await apiFetch(`/api/meta-analysis/results/${resultId}`);
        const title = mT('meta.chat_title', 'Méta-Analyse #{id}', { id: resultId });
        const conv = await apiFetch('/chat/api/create', { method: 'POST', body: { title, raw_context_prompt: title, raw_context_response: r.ollama_response } });
        if (conv && conv.id) window.location.href = `/chat?id=${conv.id}`;
    } catch (e) {
        btn.disabled = false;
        btn.textContent = `${mT('common.error', 'Erreur')} : ${e.message}`;
    }
}

async function notifyResult(resultId, btn) {
    const label = btn.textContent;
    btn.disabled = true;
    btn.textContent = mT('common.sending', 'Envoi…');
    try {
        await apiFetch(`/api/meta-analysis/results/${resultId}/notify`, { method: 'POST' });
        btn.textContent = mT('common.sent', 'Envoyé');
    } catch (e) {
        btn.textContent = `${mT('common.error', 'Erreur')} : ${e.message}`;
    }
    setTimeout(() => { btn.textContent = label; btn.disabled = false; }, 4000);
}

function deleteResult(resultId, btn) {
    showInlineConfirm(btn, mT('common.confirm_delete_analysis', 'Supprimer cette analyse ?'), async () => {
        try {
            await apiFetch(`/api/meta-analysis/results/${resultId}`, { method: 'DELETE' });
            await refreshConfigs();
            route();
        } catch (e) {
            btn.textContent = `${mT('common.error', 'Erreur')} : ${e.message}`;
        }
    });
}

/* ── Prochaine analyse ───────────────────────────────────────────────────── */

function toLocalInput(iso) {
    const d = new Date(iso);
    const pad = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function restoreSelection(configId, data) {
    try {
        const saved = JSON.parse(sessionStorage.getItem(`sentinel_meta_state_${configId}`));
        if (!saved) return;
        data.rules_context.forEach(r => r.entries.forEach(e => {
            if (saved.exclusions.includes(e.id)) e._excluded = true;
            if (saved.annotations[e.id]) e.annotation = saved.annotations[e.id];
        }));
    } catch (_) { /* navigation privée, stockage indisponible */ }
}

function saveSelection(configId) {
    const data = META.preview[configId];
    if (!data) return;
    const state = { exclusions: [], annotations: {} };
    data.rules_context.forEach(r => r.entries.forEach(e => {
        if (e._excluded) state.exclusions.push(e.id);
        if (e.annotation && e.annotation.trim()) state.annotations[e.id] = e.annotation.trim();
    }));
    try {
        sessionStorage.setItem(`sentinel_meta_state_${configId}`, JSON.stringify(state));
    } catch (_) { /* idem */ }
}

async function loadPreview(c, start, end) {
    const params = new URLSearchParams();
    if (start) params.set('period_start', start);
    if (end) params.set('period_end', end);
    const query = params.toString();
    const res = await apiFetch(`/api/meta-analysis/trigger/preview/${c.id}${query ? `?${query}` : ''}`);
    restoreSelection(c.id, res);
    META.preview[c.id] = res;
    return res;
}

/** Événements de la période, moins ceux qu'on a exclus (la relecture n'affiche que les plus récents). */
function plannedCount(data) {
    const excluded = data.rules_context.reduce((n, r) => n + r.entries.filter(e => e._excluded).length, 0);
    return Math.max(0, (data.analyses_count || 0) - excluded);
}

function activeCount(data) {
    return data.rules_context.reduce((n, r) => n + r.entries.filter(e => !e._excluded).length, 0);
}

function renderNextInfo(c) {
    const data = META.preview[c.id];
    const el = document.getElementById('meta-next-info');
    const when = c.enabled
        ? mT('meta.next_at', 'Prochaine analyse : {when}', { when: fmtDateTime(computeNextRun(c)) })
        : mT('meta.next_off', 'Désactivée : elle ne tourne que si tu la lances.');
    const scope = mT('meta.next_scope', '{name} · {n} règle(s)', { name: c.name, n: rulesOf(c).length });
    const period = data ? mT('meta.next_period', 'Période prévue : {start} → {end} · {n} événement(s)', {
        start: fmtDateTime(data.period_start), end: fmtDateTime(data.period_end), n: plannedCount(data),
    }) : mT('meta.next_loading', 'Lecture des événements…');
    el.innerHTML = `${warningHtml(c)}<p class="meta-next-sub">${escapeHtml(scope)}</p><p class="meta-next-line">${escapeHtml(when)}</p><p class="meta-next-sub">${escapeHtml(period)}</p>`;
}

async function renderNext(c, start, end) {
    renderNextInfo(c);
    renderRunState();
    const review = document.getElementById('meta-review');
    try {
        const data = (!start && META.preview[c.id]) || await loadPreview(c, start, end);
        if (META.id !== c.id || META.view !== 'next') return;
        document.getElementById('meta-period-start').value = toLocalInput(data.period_start);
        document.getElementById('meta-period-end').value = toLocalInput(data.period_end);
        renderNextInfo(c);
        renderReview(c);
    } catch (e) {
        review.innerHTML = `<p class="form-hint rf-ko">${escapeHtml(e.message)}</p>`;
    }
}

function renderReview(c) {
    const data = META.preview[c.id];
    const el = document.getElementById('meta-review');
    document.getElementById('meta-review-count').textContent = data ? activeCount(data) : '';
    if (!data || !data.rules_context.length) {
        el.innerHTML = `<p class="form-hint">${escapeHtml(mT('meta.no_context_available', 'Aucun événement sur cette période.'))}</p>`;
        return;
    }
    el.innerHTML = data.rules_context.map((rule, ri) => {
        const entries = rule.entries.map((e, ei) => ({ ...e, ei }));
        const kept = entries.filter(e => !e._excluded);
        const excluded = entries.filter(e => e._excluded);
        return `<div class="meta-group">
            <div class="meta-group-head"><span>${escapeHtml(rule.rule_name)}</span><span class="rp-muted">${escapeHtml(mT('meta.events_n', '{n} événement(s)', { n: kept.length }))}</span></div>
            ${excluded.length ? `<div class="meta-excluded"><span>${escapeHtml(mT('meta.excluded_label', 'Exclus :'))}</span>${excluded.map(e =>
                `<button type="button" class="btn btn-secondary btn-sm" onclick="setExcluded(${ri}, ${e.ei}, false)">${escapeHtml(mT('meta.restore_btn', 'Remettre'))} #${escapeHtml(e.detection_id || String(e.id))}</button>`).join('')}</div>` : ''}
            ${kept.map(e => {
                const severity = String(e.severity || 'info').toLowerCase();
                return `<details class="meta-entry">
                <summary>
                    <span class="severity-badge ${escapeHtml(severity)}">${escapeHtml(severity)}</span>
                    <span class="meta-entry-time">${escapeHtml(fmtDateTime(e.date))}</span>
                    <span class="meta-entry-text">${escapeHtml(e.short_ia || '')}</span>
                </summary>
                <div class="meta-entry-body">
                    <pre class="meta-entry-line">${escapeHtml(e.triggered_line || '')}</pre>
                    <label class="rf-stack"><span>${escapeHtml(mT('meta.annotation_label', 'Remarque pour cette analyse'))}</span>
                        <textarea rows="2" onchange="setAnnotation(${ri}, ${e.ei}, this.value)">${escapeHtml(e.annotation || '')}</textarea></label>
                    <div class="meta-entry-actions">
                        ${e.detection_id ? `<a class="btn btn-secondary btn-sm" href="/monitor?search=${encodeURIComponent(e.detection_id)}">${escapeHtml(mT('meta.open_event', 'Voir l’alerte'))}</a>` : ''}
                        <button type="button" class="btn btn-secondary btn-sm" onclick="setExcluded(${ri}, ${e.ei}, true)">${escapeHtml(mT('meta.exclude_btn', 'Exclure'))}</button>
                    </div>
                </div>
            </details>`;
            }).join('')}
        </div>`;
    }).join('');
    highlightDOMText(el.querySelector('.meta-group') ? el : document.createElement('div'), data.matched_keywords);
}

function setExcluded(ruleIndex, entryIndex, excluded) {
    const data = META.preview[META.id];
    data.rules_context[ruleIndex].entries[entryIndex]._excluded = excluded;
    saveSelection(META.id);
    renderNextInfo(configOf(META.id));
    renderReview(configOf(META.id));
}

function setAnnotation(ruleIndex, entryIndex, value) {
    META.preview[META.id].rules_context[ruleIndex].entries[entryIndex].annotation = value;
    saveSelection(META.id);
}

function renderRunState() {
    if (META.view !== 'next') return;
    const run = document.getElementById('meta-run');
    const stop = document.getElementById('meta-stop');
    const status = document.getElementById('meta-run-status');
    const running = META.running.has(META.id);
    run.disabled = running;
    stop.classList.toggle('hidden', !running);
    if (running) {
        status.classList.remove('rf-ok', 'rf-ko');
        status.textContent = mT('meta.run_running', 'Analyse en cours : cela peut prendre plusieurs minutes.');
    } else if (status.textContent === mT('meta.run_running', 'Analyse en cours : cela peut prendre plusieurs minutes.')) {
        status.textContent = '';
    }
}

function runStatus(ok, text) {
    const status = document.getElementById('meta-run-status');
    status.classList.remove('rf-ok', 'rf-ko');
    if (ok !== null) status.classList.add(ok ? 'rf-ok' : 'rf-ko');
    status.textContent = text;
}

async function triggerRun() {
    const c = configOf(META.id);
    if (!c) return;
    const id = c.id;
    const before = c.latest ? c.latest.id : null;
    try {
        const data = META.preview[id] || await loadPreview(c);
        if (!activeCount(data)) {
            runStatus(false, mT(data.rules_context.length ? 'meta.all_excluded' : 'meta.no_context_trigger', 'Aucun événement à analyser.'));
            return;
        }
        const excluded = [];
        const annotations = {};
        data.rules_context.forEach(r => r.entries.forEach(e => {
            if (e._excluded) excluded.push(e.id);
            else if (e.annotation && e.annotation.trim()) annotations[e.id] = e.annotation.trim();
        }));
        META.abort = new AbortController();
        META.running.add(id);
        renderRunState();
        renderTiles();
        await apiFetch(`/api/meta-analysis/trigger/${id}`, {
            method: 'POST',
            body: { excluded_ids: excluded, annotations, period_start: data.period_start, period_end: data.period_end },
            signal: META.abort.signal,
        });
        await waitUntilDone(id, META.abort);
        if (META.abort.signal.aborted) {
            runStatus(false, mT('meta.analysis_cancelled', 'Analyse annulée.'));
            return;
        }
        try {
            sessionStorage.removeItem(`sentinel_meta_state_${id}`);
        } catch (_) { /* stockage indisponible */ }
        delete META.preview[id];
        await refreshConfigs();
        const latest = configOf(id)?.latest;
        if (latest && latest.id !== before) {
            if (META.id === id && META.view === 'next') location.hash = `#${id}`;
        } else {
            runStatus(false, mT('meta.run_no_result', 'L’analyse s’est terminée sans nouvelle synthèse.'));
            if (META.id === id && META.view === 'next') renderNext(configOf(id));
        }
    } catch (e) {
        runStatus(false, e.name === 'AbortError' ? mT('meta.analysis_cancelled', 'Analyse annulée.') : `${mT('common.error', 'Erreur')} : ${e.message}`);
    } finally {
        META.running.delete(id);
        META.abort = null;
        renderRunState();
        renderTiles();
    }
}

async function waitUntilDone(id, abort) {
    while (!abort.signal.aborted) {
        await new Promise(resolve => setTimeout(resolve, 2000));
        if (abort.signal.aborted) return;
        try {
            const res = await apiFetch('/api/meta-analysis/running');
            if (!(res.running || []).map(Number).includes(id)) return;
        } catch (_) {
            return;
        }
    }
}

/* ── Réglages ────────────────────────────────────────────────────────────── */

function fillForm(c) {
    document.getElementById('config-form').reset();
    document.getElementById('meta-form-error').textContent = '';
    document.getElementById('meta-form-saved').textContent = '';
    document.getElementById('config-id').value = c ? c.id : '';
    document.getElementById('meta-form-title').textContent = c ? mT('meta.modal_title_edit', 'Réglages de la méta-analyse') : mT('meta.modal_title_new', 'Nouvelle méta-analyse');
    const followed = c ? c.rule_ids_json : [];
    const groups = sourceGroups(META.rules);
    document.getElementById('config-rules-list').innerHTML = groups.map((g, i) => `<fieldset class="meta-rule-group" data-group="${i}">
        <legend><label><input type="checkbox" class="rule-group-chk" data-group="${i}"> ${escapeHtml(g.label)}</label></legend>
        ${g.rules.map(r => `<label><input type="checkbox" class="rule-chk" value="${r.id}"${followed.includes(r.id) ? ' checked' : ''}> ${escapeHtml(r.name)}</label>`).join('')}
    </fieldset>`).join('');
    syncRuleGroups();
    const suggest = document.getElementById('meta-suggest');
    suggest.classList.toggle('hidden', Boolean(c) || groups.length < 2);
    document.getElementById('meta-suggest-chips').innerHTML = groups.map((g, i) => `<button type="button" class="chip" data-suggest="${i}">${escapeHtml(g.label)}</button>`).join('');
    document.getElementById('config-name').value = c ? c.name : '';
    document.getElementById('config-schedule-type').value = c ? c.schedule_type || 'daily' : 'weekly';
    updateScheduleDay(c ? c.schedule_day : 1);
    document.getElementById('config-schedule-time').value = c ? utcToLocal(c.schedule_time || '00:00') : '06:00';
    document.getElementById('config-enabled').checked = c ? c.enabled : true;
    document.getElementById('config-notify').checked = c ? c.notify_enabled : true;
    document.getElementById('meta-danger').classList.toggle('hidden', !c);
    document.getElementById('meta-reset-row').classList.toggle('hidden', !(c && c.last_run_at));
}

async function submitForm(event) {
    event.preventDefault();
    const error = document.getElementById('meta-form-error');
    const saved = document.getElementById('meta-form-saved');
    error.textContent = '';
    saved.textContent = '';
    const name = document.getElementById('config-name').value.trim();
    if (!name) {
        error.textContent = mT('meta.name_required', 'Donne un nom à cette méta-analyse.');
        document.getElementById('config-name').focus();
        return;
    }
    const id = document.getElementById('config-id').value;
    const payload = {
        name,
        rule_ids: [...document.querySelectorAll('.rule-chk:checked')].map(chk => Number(chk.value)),
        schedule_type: document.getElementById('config-schedule-type').value,
        schedule_day: document.getElementById('config-schedule-day').value || 1,
        schedule_time: localToUTC(document.getElementById('config-schedule-time').value || '00:00'),
        enabled: document.getElementById('config-enabled').checked,
        notify_enabled: document.getElementById('config-notify').checked,
    };
    try {
        const res = await apiFetch(id ? `/api/meta-analysis/configs/${id}` : '/api/meta-analysis/configs', { method: id ? 'PUT' : 'POST', body: payload });
        await refreshConfigs();
        if (id) {
            delete META.preview[Number(id)];
            fillForm(configOf(Number(id)));
            renderTiles();
            document.getElementById('meta-form-saved').textContent = mT('meta.saved', 'Enregistré');
        } else {
            location.hash = `#${res.id}`;
        }
    } catch (e) {
        error.textContent = `${mT('common.error', 'Erreur')} : ${e.message}`;
    }
}

function syncRuleGroups() {
    document.querySelectorAll('.meta-rule-group').forEach(group => {
        const boxes = [...group.querySelectorAll('.rule-chk')];
        const checked = boxes.filter(b => b.checked).length;
        const all = group.querySelector('.rule-group-chk');
        all.checked = checked === boxes.length;
        all.indeterminate = checked > 0 && checked < boxes.length;
    });
}

function setupRuleGroups() {
    document.getElementById('config-rules-list').addEventListener('change', e => {
        if (e.target.classList.contains('rule-group-chk')) {
            e.target.closest('.meta-rule-group').querySelectorAll('.rule-chk').forEach(b => { b.checked = e.target.checked; });
        }
        syncRuleGroups();
    });
    document.getElementById('meta-suggest-chips').addEventListener('click', e => {
        const chip = e.target.closest('[data-suggest]');
        if (!chip) return;
        const group = sourceGroups(META.rules)[Number(chip.dataset.suggest)];
        const ids = group.rules.map(r => r.id);
        document.querySelectorAll('.rule-chk').forEach(b => { b.checked = ids.includes(Number(b.value)); });
        syncRuleGroups();
        document.getElementById('config-name').value = group.label;
    });
}

function setupDanger() {
    document.getElementById('meta-delete').addEventListener('click', e => {
        showInlineConfirm(e.currentTarget, mT('meta.delete_config_confirm', 'Supprimer cette méta-analyse ?'), async () => {
            try {
                await apiFetch(`/api/meta-analysis/configs/${META.id}`, { method: 'DELETE' });
                await refreshConfigs();
                location.hash = META.configs.length ? `#${META.configs[0].id}` : '';
                route();
            } catch (err) {
                document.getElementById('meta-form-error').textContent = `${mT('common.error', 'Erreur')} : ${err.message}`;
            }
        });
    });
    document.getElementById('meta-reset').addEventListener('click', e => {
        showInlineConfirm(e.currentTarget, mT('meta.reset_last_run_confirm', 'Oublier la dernière exécution ?'), async () => {
            try {
                await apiFetch(`/api/meta-analysis/configs/${META.id}/reset-last-run`, { method: 'POST' });
                delete META.preview[META.id];
                await refreshConfigs();
                fillForm(configOf(META.id));
            } catch (err) {
                document.getElementById('meta-form-error').textContent = `${mT('common.error', 'Erreur')} : ${err.message}`;
            }
        });
    });
}

/* ── Démarrage ───────────────────────────────────────────────────────────── */

document.addEventListener('DOMContentLoaded', async () => {
    document.getElementById('config-form').addEventListener('submit', submitForm);
    document.getElementById('config-schedule-type').addEventListener('change', () => updateScheduleDay());
    document.getElementById('meta-run').addEventListener('click', triggerRun);
    document.getElementById('meta-stop').addEventListener('click', () => {
        if (META.abort) META.abort.abort();
        apiFetch(`/api/meta-analysis/cancel/${META.id}`, { method: 'POST' }).catch(() => {});
    });
    document.getElementById('meta-new-show').addEventListener('click', route);
    let periodTimer = null;
    ['meta-period-start', 'meta-period-end'].forEach(id => document.getElementById(id).addEventListener('change', () => {
        clearTimeout(periodTimer);
        periodTimer = setTimeout(() => {
            const start = document.getElementById('meta-period-start').value;
            const end = document.getElementById('meta-period-end').value;
            const c = configOf(META.id);
            if (start && end && c) {
                delete META.preview[c.id];
                renderNext(c, new Date(start).toISOString(), new Date(end).toISOString());
            }
        }, 400);
    }));
    setupDanger();
    setupRuleGroups();
    setupDash();
    try {
        const saved = Number(localStorage.getItem('sentinel_meta_days'));
        if (RANGES.includes(saved)) META.days = saved;
    } catch (_) { /* stockage indisponible */ }
    window.addEventListener('hashchange', route);
    window.i18n?.onLanguageChange(() => { renderTiles(); route(); });
    try {
        META.rules = await apiFetch('/api/rules');
        await refreshConfigs();
    } catch (e) {
        document.getElementById('meta-empty').classList.add('hidden');
        document.getElementById('meta-tiles').innerHTML = `<p class="form-hint rf-ko">${escapeHtml(e.message)}</p>`;
        return;
    }
    route();
    setInterval(backgroundRefresh, 30000);
});
