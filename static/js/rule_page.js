// ─── Rule page ──────────────────────────────────────────────────────────────
// One page per rule, with tabs: overview, live log, analyses, resolution, settings.
// /rules/new shows the settings form only.
// ─────────────────────────────────────────────────────────────────────────────

const RP = {
    id: null,
    rule: null,
    tab: null,
    timers: [],
    headerTimer: null,
    an: { filter: 'all', period: '7', openOnly: false, offset: 0, hasMore: false, counts: null },
    focusAnalysisId: null,
    verdictLimit: 10,
};
const TABS = ['overview', 'log', 'analyses', 'resolution', 'keywords', 'settings'];

/* ── Helpers ───────────────────────────────────────────────────────────────── */
function T(key, fallback, vars) {
    let s = window.t ? window.t(key) : key;
    if (!s || s === key) s = fallback !== undefined ? fallback : key;
    if (vars) Object.entries(vars).forEach(([k, v]) => { s = s.split(`{${k}}`).join(v); });
    return s;
}

function esc(v) { return escapeHtml(v == null ? '' : String(v)); }

function fmtMinutes(m) {
    if (m == null) return '—';
    if (m < 1) return T('rule.less_than_minute', '< 1 min');
    if (m < 60) return `${Math.round(m)} min`;
    if (m < 1440) {
        const h = Math.floor(m / 60), mm = Math.round(m % 60);
        return mm ? `${h} h ${String(mm).padStart(2, '0')}` : `${h} h`;
    }
    const d = Math.floor(m / 1440), h = Math.round((m % 1440) / 60);
    return h ? `${d} ${T('rule.day_short', 'j')} ${h} h` : `${d} ${T('rule.day_short', 'j')}`;
}

function minutesSince(iso) { return iso ? (Date.now() - new Date(iso).getTime()) / 60000 : null; }

function fmtWhen(iso) {
    if (!iso) return '—';
    const d = new Date(iso), now = new Date();
    const hm = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    const yesterday = new Date(now); yesterday.setDate(now.getDate() - 1);
    if (d.toDateString() === now.toDateString()) return hm;
    if (d.toDateString() === yesterday.toDateString()) return `${T('rule.yesterday', 'hier')} ${hm}`;
    return `${d.toLocaleDateString([], { day: '2-digit', month: '2-digit' })} ${hm}`;
}

function sevBadge(sev) {
    const s = ['critical', 'warning', 'info'].includes(sev) ? sev : 'info';
    return `<span class="severity-badge ${s}">${s}</span>`;
}

function sourceLabel(path) {
    if (!path) return '';
    if (path.startsWith('[WEBHOOK]')) return T('rule.source_webhook', 'Webhook');
    if (path.startsWith('[SYSLOG]')) return `${T('rule.source_syslog', 'Syslog')} · ${path.split(':')[1] || '*'}`;
    return `${T('rule.source_file', 'Fichier')} · ${path}`;
}

function clearTabTimers() {
    RP.timers.forEach(clearInterval);
    RP.timers = [];
}

function flash(el, text, cls = 'rp-flash-ok') {
    if (!el) return;
    const n = document.createElement('div');
    n.className = `rp-flash ${cls}`;
    n.textContent = text;
    el.prepend(n);
    setTimeout(() => n.remove(), 3500);
}

/* ── Init ──────────────────────────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', async () => {
    RP.id = parseInt(document.getElementById('rule-page').dataset.ruleId) || null;

    setupRuleForm({
        onSave: (id, isNew) => {
            if (isNew) {
                // A new rule starts by learning from the log history (autopilot, or unless disabled)
                const learning = document.getElementById('rule-autopilot')?.checked
                    || (document.getElementById('rule-learn-history')?.checked
                        && document.querySelector('input[name="rule-keyword-tuning"]:checked')?.value !== 'off');
                window.location.href = `/rules/${id}#${learning ? 'keywords' : 'overview'}`;
                return;
            }
            loadHeader().then(rule => {
                // The autopilot may have just been switched on: refresh its state in the form
                if (rule) { _rfAutopilot = rule.autopilot || null; updateAutopilotUI(); }
            });
            flash(document.getElementById('tab-settings'), T('rule.saved', 'Réglages enregistrés.'));
        },
        onCancel: () => {
            if (!RP.id) { window.location.href = '/rules'; return; }
            editRule(RP.id);
            selectTab('overview');
        },
    });

    if (!RP.id) {
        document.getElementById('rp-name').textContent = T('rule.new_title', 'Nouvelle règle');
        resetForm();
        showPanel('settings');
        return;
    }

    document.getElementById('rp-header-actions').classList.remove('hidden');
    document.getElementById('rp-tabs').classList.remove('hidden');
    document.querySelectorAll('#rp-tabs a').forEach(a => a.addEventListener('click', e => {
        e.preventDefault();
        selectTab(a.dataset.tab);
    }));
    window.addEventListener('hashchange', () => {
        const tab = location.hash.slice(1);
        if (TABS.includes(tab) && tab !== RP.tab) selectTab(tab);
    });
    document.getElementById('rp-enabled').addEventListener('change', toggleEnabled);
    document.getElementById('rp-test-btn').addEventListener('click', testRule);
    document.getElementById('rp-download-btn').addEventListener('click', () => { window.location.href = `/api/rules/${RP.id}/download`; });
    document.getElementById('rp-delete-btn').addEventListener('click', e => deleteRule(e.currentTarget));
    setupAnalysesToolbar();

    const params = new URLSearchParams(location.search);
    RP.focusAnalysisId = parseInt(params.get('analysis')) || null;
    if (params.get('line')) {
        selectedLineText = params.get('line');
        activeKeywordFilter = '__all__';
        autoOpenLine = true;
    }
    if (params.has('analysis') || params.has('line')) history.replaceState({}, '', location.pathname + location.hash);

    const rule = await loadHeader();
    if (!rule) return;
    editRule(RP.id);
    RP.headerTimer = setInterval(loadHeader, 15000);

    let tab = location.hash.slice(1);
    if (RP.focusAnalysisId) tab = 'analyses';
    if (selectedLineText) tab = 'log';
    selectTab(TABS.includes(tab) ? tab : 'overview');

    window.i18n?.onLanguageChange(() => { loadHeader(); selectTab(RP.tab); });
});

function showPanel(tab) {
    TABS.forEach(t => document.getElementById(`tab-${t}`).classList.toggle('hidden', t !== tab));
    document.querySelectorAll('#rp-tabs a').forEach(a => {
        const on = a.dataset.tab === tab;
        a.classList.toggle('active', on);
        a.setAttribute('aria-selected', on ? 'true' : 'false');
    });
}

function selectTab(tab) {
    clearTabTimers();
    stopLogPolling();
    RP.tab = tab;
    showPanel(tab);
    if (location.hash.slice(1) !== tab) history.replaceState({}, '', `${location.pathname}${location.search}#${tab}`);
    if (tab === 'overview') { renderOverview(); RP.timers.push(setInterval(renderOverview, 15000)); }
    else if (tab === 'log') renderLogTab();
    else if (tab === 'analyses') { loadAnalyses(true); loadNotesPanel(); RP.timers.push(setInterval(pollAnalyses, 20000)); }
    else if (tab === 'resolution') { renderResolution(); RP.timers.push(setInterval(renderResolution, 20000)); }
    else if (tab === 'keywords') renderKeywords();
    // The AI arbiter may have changed the keywords since the form was filled
    else if (tab === 'settings' && RP.id) editRule(RP.id);
}

/* ── Autopilot setup journey (overview) ─────────────────────────────────────── */
/** Steps of the autopilot setup, ticked as the background work progresses. Shown until the rule is ready,
 *  then for 24 h so the result can be read. */
function autopilotJourneyHtml(j) {
    if (!j || !j.enabled) return '';
    const s = j.session || {};
    const sum = s.summary || {};
    const ready = j.status === 'active';
    if (ready && !(j.refreshed_at && minutesSince(j.refreshed_at) < 24 * 60)) return '';
    const observed = !!sum.observe_rounds;
    const observing = s.mode === 'live' && s.status === 'pending';
    const reading = !observing && ['pending', 'scanning'].includes(s.status);
    const refining = s.status === 'refining';
    const profiled = !!sum.profile || ready;
    const steps = [];
    const step = (key, state, title, detail, extra = '') => steps.push({ key, state, title, detail, extra });

    step('source', j.source.lines ? 'done' : 'active', T('rule.ap_step_source', 'Source connectée'),
        j.source.lines ? T('rule.ap_step_source_done', '{n} ligne(s) reçue(s) · dernière {when}', { n: j.source.lines.toLocaleString(), when: fmtWhen(j.source.last_at) })
            : T('rule.ap_step_source_wait', 'En attente de la première ligne de la source…'));

    if (observed) {
        const histLines = sum.history_lines ?? sum.lines ?? 0;
        step('history', 'warn', T('rule.ap_step_history', 'Lecture de l’historique'), histLines >= 50 && sum.history_span_h != null
            ? T('rule.ap_step_history_brief', '{n} ligne(s) sur {h} h seulement : trop peu de recul pour définir la règle.', { n: histLines.toLocaleString(), h: sum.history_span_h })
            : T('rule.ap_step_history_short', '{n} ligne(s) trouvée(s) : trop peu pour définir la règle.', { n: histLines.toLocaleString() }));
        let extra = '';
        if (observing) {
            const total = new Date(s.period_end) - new Date(s.period_start);
            const pct = total > 0 ? Math.min(100, Math.max(0, 100 * (Date.now() - new Date(s.period_start)) / total)) : 0;
            extra = `<div class="rp-progress ap-progress"><div style="width:${pct.toFixed(1)}%"></div></div>
                <span class="rp-muted">${esc(T('rule.ap_step_observe_alerts', 'En attendant, la règle alerte sur les lignes de niveau erreur explicite : {n} alerte(s) jusqu’ici.', { n: j.observation_alerts }))}</span>`;
        }
        step('observe', observing ? 'active' : 'done', T('rule.ap_step_observe', 'Observation de la source'),
            observing ? T('rule.ap_step_observe_active', 'Jusqu’à {when}, encore {left}.', { when: fmtWhen(s.period_end), left: fmtMinutes(-minutesSince(s.period_end)) })
                : T('rule.ap_step_observe_done', '{n} ligne(s) observée(s).', { n: (sum.lines || 0).toLocaleString() }), extra);
    } else {
        step('history', reading ? 'active' : 'done', T('rule.ap_step_history', 'Lecture de l’historique'),
            reading ? T('rule.ap_step_history_active', 'Lecture des 7 derniers jours du journal…') : T('rule.ap_step_history_done', '{n} ligne(s) lues, regroupées en {t} modèle(s).', { n: (sum.lines || 0).toLocaleString(), t: (sum.templates || 0).toLocaleString() }),
            reading ? `<div class="rp-progress ap-progress"><div style="width:${s.progress || 0}%"></div></div>` : '');
    }

    step('profile', profiled ? 'done' : refining ? 'active' : 'todo', T('rule.ap_step_profile', 'L’IA décrit le journal'),
        profiled ? T('rule.ap_step_profile_done', 'Règle nommée « {name} », contexte rédigé.', { name: j.rule.name })
            : T('rule.ap_step_profile_todo', 'Elle lit un extrait du journal pour nommer la règle et décrire ce qui y est normal.'));
    const noTrigger = ready && !j.rule.keywords;
    step('keywords', noTrigger ? 'warn' : ready ? 'done' : refining && profiled ? 'active' : 'todo', T('rule.ap_step_keywords', 'L’IA choisit les mots-clés'),
        noTrigger ? T('rule.ap_step_keywords_none', 'Rien à analyser pour l’instant : la règle ne surveille que le silence de la source. Nouvelle lecture du journal chaque semaine.')
        : ready ? T('rule.ap_step_keywords_done', '{k} mot(s)-clé(s), {x} exclusion(s).', { k: j.rule.keywords, x: j.rule.exclusions })
            : T('rule.ap_step_keywords_todo', 'Les lignes qui méritent une alerte, vérifiées sur les vraies lignes du journal.'));
    step('rhythm', ready ? 'done' : 'todo', T('rule.ap_step_rhythm', 'Rythme de la source'),
        ready ? T('rule.ap_step_rhythm_done', 'Regroupement des lignes sur {s} s · alerte si la source se tait plus de {h} h · notifications à partir de {sev}.', { s: j.rule.anti_spam_delay, h: j.rule.inactivity_period_hours, sev: j.rule.notify_severity_threshold })
            : T('rule.ap_step_rhythm_todo', 'Fenêtre de regroupement, délai de silence et seuil de notification, d’après le rythme réel des lignes et des alertes.'));
    step('ready', ready ? 'done' : 'todo', T('rule.ap_step_ready', 'Règle prête'),
        ready ? T('rule.ap_step_ready_done', 'Le journal sera relu chaque semaine (prochaine relecture {when}).', { when: fmtWhen(j.next_refresh_at) })
            : T('rule.ap_step_ready_todo', 'Une notification résumera ce qui a été défini.'));

    // Failure: shown on the first unfinished step
    if (j.status === 'error') {
        const cur = steps.find(x => !['done', 'warn'].includes(x.state));
        if (cur) { cur.state = 'error'; cur.extra = `<span class="rp-ko">${esc(autopilotStatusText(j))}</span>`; }
    } else {
        // Only one active step at a time (the first one)
        let seen = false;
        steps.forEach(x => { if (x.state === 'active') { if (seen) x.state = 'todo'; seen = true; } });
    }
    const doneCount = steps.filter(x => ['done', 'warn'].includes(x.state)).length;
    const icon = st => ({ done: '✓', warn: '!', error: '✕', active: '', todo: '' }[st]);
    return `
    <section class="rp-card ap-journey" aria-live="polite">
        <div class="rp-card-head"><h2>🧭 ${esc(T('rule.ap_session_title', 'Mise en place par le pilote automatique'))}</h2>
            <span class="rp-muted">${esc(ready ? T('rule.ap_journey_done', 'terminée') : T('rule.ap_journey_progress', 'étape {n} sur {t}', { n: Math.min(steps.length, doneCount + 1), t: steps.length }))}</span></div>
        <ol class="ap-steps">
            ${steps.map(x => `<li class="ap-step ap-step--${x.state}">
                <span class="ap-step-mark" aria-hidden="true">${icon(x.state)}</span>
                <div class="ap-step-body"><strong>${esc(x.title)}</strong><span class="rp-muted">${esc(x.detail)}</span>${x.extra}</div>
            </li>`).join('')}
        </ol>
        <div class="rp-actions"><a class="btn btn-secondary btn-sm" href="#keywords">${esc(T('rule.ap_journey_details', 'Détails dans l’onglet Mots-clés'))}</a></div>
    </section>`;
}

/* ── Header ────────────────────────────────────────────────────────────────── */
async function loadHeader() {
    try {
        const rule = await apiFetch(`/api/rules/${RP.id}`);
        RP.rule = rule;
        document.title = `${rule.name} — Log to LLM Sentinel`;
        document.getElementById('rp-name').textContent = rule.name;
        document.getElementById('rp-subtitle').textContent =
            `${sourceLabel(rule.log_file_path)} · ${T('rule.keywords', 'mots-clés')} : ${rule.keywords.join(', ') || '—'}`;
        document.getElementById('rp-enabled').checked = !!rule.enabled;

        const pill = document.getElementById('rp-status');
        pill.classList.remove('hidden', 'rp-pill--alert', 'rp-pill--ok', 'rp-pill--off', 'rp-pill--warn');
        if (!rule.enabled) {
            pill.classList.add(rule.alert_status !== 'normal' ? 'rp-pill--warn' : 'rp-pill--off');
            pill.textContent = rule.alert_status !== 'normal'
                ? T('rule.status_disabled_open', 'Désactivée · incident non clos') : T('rule.status_disabled', 'Désactivée');
        } else if (rule.alert_status === 'resolving') {
            pill.classList.add('rp-pill--warn');
            pill.textContent = T('rule.status_resolving', 'Vérification du retour à la normale…');
        } else if (rule.alert_status === 'alert') {
            pill.classList.add('rp-pill--alert');
            pill.textContent = `${T('rule.status_alert', 'En alerte')} · ${fmtMinutes(minutesSince(rule.alert_started_at))}`;
        } else {
            pill.classList.add('rp-pill--ok');
            pill.textContent = T('rule.status_normal', 'Normal');
        }

        setKwBadge(rule.keyword_pending || 0);
        setAutopilotPill(rule.autopilot);
        const counts = (await apiFetch(`/api/dashboard/events?limit=1&rule_id=${RP.id}`)).counts || {};
        setUnreadBadge(counts.unread);
        return rule;
    } catch (e) {
        document.getElementById('rp-name').textContent = T('rule.not_found', 'Règle introuvable');
        document.getElementById('rp-header-actions').classList.add('hidden');
        document.getElementById('rp-tabs').classList.add('hidden');
        return null;
    }
}

async function toggleEnabled(e) {
    const enabled = e.target.checked;
    try {
        await apiFetch(`/api/rules/${RP.id}`, { method: 'PUT', body: { enabled } });
        loadHeader();
    } catch (err) {
        e.target.checked = !enabled;
        alert(T('common.error', 'Erreur') + ': ' + err.message);
    }
}

function deleteRule(btn) {
    showInlineConfirm(btn, T('rules.delete_confirm', 'Supprimer cette règle ?'), async () => {
        try {
            await apiFetch(`/api/rules/${RP.id}`, { method: 'DELETE' });
            window.location.href = '/rules';
        } catch (e) {
            alert(T('common.error', 'Erreur') + ': ' + e.message);
        }
    });
}

async function testRule() {
    const btn = document.getElementById('rp-test-btn');
    const original = btn.innerHTML;
    btn.disabled = true;
    btn.textContent = T('rules.test_in_progress', 'Test en cours…');
    try {
        await apiFetch(`/api/rules/${RP.id}/test`, { method: 'POST' });
        btn.textContent = '✓ ' + T('common.done', 'Terminé');
        if (RP.tab === 'analyses') loadAnalyses(true);
    } catch (e) {
        btn.textContent = '✗ ' + T('rules.test_error_btn', 'Erreur');
    }
    setTimeout(() => { btn.innerHTML = original; btn.disabled = false; }, 2500);
}

async function resolveManually(btn) {
    showInlineConfirm(btn, T('rule.resolve_confirm', 'Clore l’incident en cours ?'), async () => {
        try {
            await apiFetch(`/api/monitor/rules/${RP.id}/resolve`, { method: 'POST' });
            loadHeader();
            selectTab(RP.tab);
        } catch (e) {
            alert(T('common.error', 'Erreur') + ': ' + e.message);
        }
    });
}

/* ── Overview tab ──────────────────────────────────────────────────────────── */
async function renderOverview() {
    const panel = document.getElementById('tab-overview');
    let s;
    try {
        s = await apiFetch(`/api/rules/${RP.id}/summary`);
    } catch (e) {
        panel.innerHTML = `<div class="loading">${esc(T('common.error', 'Erreur'))} : ${esc(e.message)}</div>`;
        return;
    }
    if (RP.tab !== 'overview') return;
    const inc = s.incident;
    const openAnalysis = s.latest.find(a => a.status === 'open');
    let incidentHtml;
    if (inc) {
        const probe = inc.probe;
        incidentHtml = `
        <section class="rp-card rp-incident" aria-label="${esc(T('rule.incident', 'Incident en cours'))}">
            <div class="rp-incident-head">
                <div class="rp-incident-text">
                    <span class="rp-label rp-label--alert">${esc(T('rule.incident_opened', 'Incident en cours · ouvert {when}', { when: fmtWhen(inc.started_at) }))}</span>
                    <p class="rp-incident-summary">${esc(inc.summary || T('rule.no_summary', 'Analyse en attente.'))}</p>
                </div>
                <div class="rp-actions">
                    ${openAnalysis ? `<button class="btn btn-primary btn-sm" onclick="investigateAnalysis(${openAnalysis.id})">${esc(T('rule.investigate', 'Investiguer avec l’IA'))}</button>
                    <button class="btn btn-secondary btn-sm" onclick="openChat(${openAnalysis.id})">${esc(T('rule.open_chat', 'Ouvrir dans le chat'))}</button>` : ''}
                    <button class="btn btn-secondary btn-sm" onclick="resolveManually(this)">${esc(T('rule.resolve', 'Marquer comme résolu'))}</button>
                </div>
            </div>
            <div class="rp-facts">
                <div class="rp-fact"><span class="rp-label">${esc(T('rule.last_error', 'Dernière erreur'))}</span>
                    <strong>${esc(T('rule.ago', 'il y a {d}', { d: fmtMinutes(minutesSince(inc.last_error_at)) }))}</strong>
                    <span class="rp-muted">${esc(T('rule.open_analyses', '{n} analyse(s) · sévérité max {sev}', { n: inc.open_count, sev: inc.max_severity }))}</span></div>
                <div class="rp-fact"><span class="rp-label">${esc(T('rule.next_check', 'Prochaine vérification'))}</span>
                    <strong>${RP.rule && !RP.rule.enabled ? esc(T('rule.check_paused', 'en pause (règle désactivée)'))
                        : inc.attempting ? esc(T('rule.checking_now', 'en cours…')) : inc.next_check_at ? esc(nextCheckText(inc.next_check_at)) : '—'}</strong>
                    <span class="rp-muted">${esc(T('rule.calm_required', 'calme requis : {d}', { d: fmtMinutes(inc.required_calm_minutes) }))}</span></div>
                <div class="rp-fact"><span class="rp-label">${esc(T('rule.liveness', 'Log vivant'))}</span>
                    <strong class="${inc.lines_since_error ? 'rp-ok' : ''}">${inc.lines_since_error == null ? '—' : esc(T('rule.normal_lines', '{n} lignes normales', { n: inc.lines_since_error }))}</strong>
                    <span class="rp-muted">${esc(T('rule.since_last_error', 'depuis la dernière erreur'))}</span></div>
                <div class="rp-fact"><span class="rp-label">${esc(T('rule.probe', 'Sonde de santé'))}</span>
                    ${probe ? `<strong class="${probe.ok ? 'rp-ok' : 'rp-ko'}">${esc(probe.detail)}</strong>
                    <span class="rp-muted">${esc(T('rule.ago', 'il y a {d}', { d: fmtMinutes(minutesSince(probe.at)) }))}</span>`
                    : `<strong>—</strong><span class="rp-muted"><a href="#settings" onclick="goToSettings('rf-resolution'); return false;">${esc(T('rule.probe_configure', 'configurer'))}</a></span>`}</div>
            </div>
        </section>`;
    } else {
        const last = s.latest[0];
        incidentHtml = `
        <section class="rp-card rp-calm">
            <span class="rp-dot rp-dot--ok" aria-hidden="true"></span>
            <div>
                <strong>${esc(RP.rule && !RP.rule.enabled ? T('rule.monitoring_off', 'Surveillance désactivée') : T('rule.no_incident', 'Aucun incident en cours'))}</strong>
                <span class="rp-muted">${last ? esc(T('rule.last_alert', 'Dernière alerte : {when}', { when: fmtWhen(last.analyzed_at) })) : esc(T('rule.no_alert_30d', 'Aucune alerte enregistrée.'))}</span>
            </div>
        </section>`;
    }

    const a = s.activity;
    const activityHtml = `
    <div class="rp-strip">
        <span class="rp-strip-item"><span class="rp-dot ${a.buffer_active ? 'rp-dot--warn' : 'rp-dot--idle'}" aria-hidden="true"></span>
            ${a.buffer_active ? esc(T('rule.buffer_active', 'Regroupement : {n} ligne(s) en attente d’analyse', { n: a.buffer_lines })) : esc(T('rule.buffer_idle', 'Aucune ligne en attente d’analyse'))}</span>
        <span class="rp-strip-item">${esc(T('rule.last_line', 'Dernière ligne reçue'))} : <strong>${a.last_line_received_at ? esc(formatDate(a.last_line_received_at)) : '—'}</strong>
            ${a.muted ? `<span class="rp-ko">· ${esc(T('rule.muted', 'source muette (> {h} h)', { h: a.inactivity_period_hours }))}</span>` : ''}</span>
        <span class="rp-strip-item">${esc(T('rule.last_analysis', 'Dernière analyse IA'))} : <strong>${a.last_analysis_at ? esc(formatDate(a.last_analysis_at)) : '—'}</strong></span>
    </div>`;

    const st = s.stats30;
    const max = Math.max(1, ...st.days.map(d => d.critical + d.warning + d.info));
    const bars = st.days.map(d => {
        const h = v => Math.round((v / max) * 110);
        const total = d.critical + d.warning + d.info;
        return `<span class="rp-bar" title="${esc(d.date)} : ${total}">
            <span style="height:${h(d.info)}px" class="rp-bar-info"></span>
            <span style="height:${h(d.warning)}px" class="rp-bar-warning"></span>
            <span style="height:${h(d.critical)}px" class="rp-bar-critical"></span></span>`;
    }).join('');
    const statsHtml = `
    <div class="rp-row">
        <section class="rp-card rp-chart-card">
            <div class="rp-card-head"><h2>${esc(T('rule.alerts_30d', 'Alertes · 30 jours'))}</h2>
                <span class="rp-legend"><span class="rp-sw rp-bar-critical"></span>critical <span class="rp-sw rp-bar-warning"></span>warning <span class="rp-sw rp-bar-info"></span>info</span></div>
            ${st.total ? `<div class="rp-chart" role="img" aria-label="${esc(T('rule.alerts_30d_aria', '{n} alertes sur 30 jours', { n: st.total }))}">${bars}</div>`
                : `<div class="rp-chart rp-chart--empty">${esc(T('rule.no_alert_period', 'Aucune alerte sur la période.'))}</div>`}
            <div class="rp-chart-axis"><span>${esc(fmtWhen(st.days[0].date + 'T12:00:00').split(' ')[0])}</span><span>${esc(T('rule.today', 'aujourd’hui'))}</span></div>
        </section>
        <section class="rp-kpis">
            <div class="rp-kpi"><span class="rp-label">${esc(T('rule.mttr', 'MTTR médian'))}</span><strong>${esc(fmtMinutes(st.mttr_minutes))}</strong><span class="rp-muted">${esc(T('rule.days_30', '30 jours'))}</span></div>
            <div class="rp-kpi"><span class="rp-label">${esc(T('rule.auto_resolution', 'Résolution auto'))}</span><strong>${st.resolved ? Math.round(100 * st.auto_resolved / st.resolved) + ' %' : '—'}</strong><span class="rp-muted">${esc(T('rule.auto_of', '{a} sur {b} analyses résolues', { a: st.auto_resolved, b: st.resolved }))}</span></div>
            <div class="rp-kpi"><span class="rp-label">${esc(T('rule.notifications', 'Notifications'))}</span><strong>${st.notified}</strong><span class="rp-muted">${esc(T('rule.threshold', 'seuil : {s}', { s: RP.rule ? (RP.rule.notify_on_match ? RP.rule.notify_severity_threshold : T('rule.off', 'désactivées')) : '—' }))}</span></div>
            <div class="rp-kpi"><span class="rp-label">${esc(T('rule.ai_calls', 'Appels IA'))}</span><strong>${st.ai_calls}</strong><span class="rp-muted">${esc(T('rule.ai_checks', 'dont {n} vérifications', { n: st.ai_checks }))}</span></div>
        </section>
    </div>`;

    const recHtml = s.recurring.length ? `
    <section class="rp-card rp-table-card">
        <div class="rp-card-head"><h2>${esc(T('rule.recurring', 'Événements récurrents · 7 jours'))}</h2><span class="rp-muted">${esc(T('rule.recurring_hint', 'Lignes identiques aux valeurs variables près'))}</span></div>
        <div class="rp-scroll"><table class="rp-table">
            <thead><tr><th>${esc(T('rule.event', 'Événement'))}</th><th>${esc(T('rule.count', 'Nombre'))}</th><th>${esc(T('rule.last', 'Dernier'))}</th><th>${esc(T('rule.hours', 'Heures'))}</th><th>${esc(T('rule.resolved_in', 'Résolu en'))}</th><th></th></tr></thead>
            <tbody>${s.recurring.map(r => `<tr>
                <td class="mono rp-sig" title="${esc(r.signature)}">${esc(r.signature.replace(/^<TS>\s*/, ''))}</td>
                <td><strong>× ${r.count}</strong></td>
                <td>${esc(fmtWhen(r.last_at))}</td>
                <td>${r.all_day ? esc(T('rule.all_day', 'toute la journée')) : esc(r.hours.map(h => String(h).padStart(2, '0') + ' h').join(', '))}</td>
                <td>${esc(fmtMinutes(r.mttr_minutes))}</td>
                <td><button class="btn btn-secondary btn-sm" data-sig="${esc(r.signature)}" onclick="proposeExclusion(this.dataset.sig)">${esc(T('rule.propose_exclusion', 'Ignorer ces lignes…'))}</button></td>
            </tr>`).join('')}</tbody>
        </table></div>
    </section>` : '';

    const latestHtml = `
    <section class="rp-card">
        <div class="rp-card-head"><h2>${esc(T('rule.latest', 'Dernières analyses'))}</h2><a href="#analyses" onclick="selectTab('analyses'); return false;">${esc(T('rule.all_analyses', 'Toutes les analyses →'))}</a></div>
        ${s.latest.length ? s.latest.map(l => `
        <a class="rp-latest" href="?analysis=${l.id}#analyses" onclick="RP.focusAnalysisId=${l.id}; selectTab('analyses'); return false;">
            <span class="rp-muted">${esc(fmtWhen(l.analyzed_at))}</span>${sevBadge(l.severity)}
            <span class="rp-latest-text">${esc(l.summary)}</span>
            <span class="${l.status === 'open' ? 'rp-ko' : 'rp-ok'}">${l.status === 'open' ? esc(T('rule.open', 'ouvert'))
                : l.resolution_minutes == null ? esc(T('rules.resolved', 'résolu')) : esc(T('rule.resolved_after', 'résolu en {d}', { d: fmtMinutes(l.resolution_minutes) }))}</span>
        </a>`).join('') : `<p class="rp-muted">${esc(T('rule.no_alert_30d', 'Aucune alerte enregistrée.'))}</p>`}
    </section>`;

    panel.innerHTML = autopilotJourneyHtml(s.autopilot) + incidentHtml + activityHtml + statsHtml + recHtml + latestHtml;
}

function nextCheckText(iso) {
    const m = -minutesSince(iso);
    return m <= 0 ? T('rule.check_imminent', 'imminente') : T('rule.in_minutes', 'dans {d}', { d: fmtMinutes(m) });
}

/** Opens the Keywords tab with the most specific literal part of a recurring event in the exclusion field. */
function proposeExclusion(signature) {
    const parts = signature.split(/<[A-Z]+>|\[×\d+\]/).map(p => p.trim()).filter(p => p.length > 3);
    RP.kwPrefill = parts.sort((a, b) => b.length - a.length)[0] || '';
    selectTab('keywords');
}

function goToSettings(sectionId) {
    selectTab('settings');
    const el = document.getElementById(sectionId);
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function setAutopilotPill(a) {
    const pill = document.getElementById('rp-autopilot');
    if (!pill) return;
    pill.classList.toggle('hidden', !(a && a.enabled));
    if (!a || !a.enabled) return;
    const label = {
        learning: T('rule.ap_pill_learning', 'Pilote auto · lecture de l’historique'),
        observing: T('rule.ap_pill_observing', 'Pilote auto · observation'),
        error: T('rule.ap_pill_error', 'Pilote auto · en attente'),
    }[a.status] || T('rule.ap_pill', 'Pilote auto');
    pill.textContent = label;
    pill.className = 'rp-pill rp-pill--ap' + (a.status === 'active' ? '' : ' rp-pill--ap-busy');
    pill.title = autopilotStatusText(a);
}

function setUnreadBadge(n) {
    const badge = document.getElementById('rp-unread-badge');
    badge.textContent = n || '';
    badge.classList.toggle('hidden', !n);
}

/* ── Analyses tab ──────────────────────────────────────────────────────────── */
// An alert opened on this page (common.js markAnalysisAsViewed) or an event deleted: update the counters without reloading
async function refreshAnalysisCounts() {
    try {
        setUnreadBadge((await apiFetch(`/api/dashboard/events?limit=1&rule_id=${RP.id}`)).counts?.unread);
        RP.an.counts = (await apiFetch(analysesUrl(0, 1))).counts;
        renderAnalysisFilters();
    } catch (_) { /* transient */ }
}
document.addEventListener('analysis-viewed', refreshAnalysisCounts);
document.addEventListener('events-changed', refreshAnalysisCounts);

function setupAnalysesToolbar() {
    document.getElementById('an-period').addEventListener('change', e => { RP.an.period = e.target.value; loadAnalyses(true); });
    document.getElementById('an-open-only').addEventListener('change', e => { RP.an.openOnly = e.target.checked; loadAnalyses(true); });
    document.getElementById('an-mark-read').addEventListener('click', async () => {
        await apiFetch(`/api/monitor/rules/${RP.id}/view-all`, { method: 'POST' });
        loadAnalyses(true);
        loadHeader();
    });
}

function analysesUrl(offset, limit) {
    let url = `/api/dashboard/events?rule_id=${RP.id}&limit=${limit}&offset=${offset}`;
    if (RP.focusAnalysisId) url += `&focus=${RP.focusAnalysisId}`;
    if (RP.an.period) url += `&days=${RP.an.period}`;
    if (RP.an.openOnly) url += '&open_only=true';
    if (RP.an.filter === 'unread') url += '&unread=true';
    else if (RP.an.filter !== 'all') url += `&severity=${RP.an.filter}`;
    return url;
}

function renderAnalysisFilters() {
    const c = RP.an.counts || {};
    const items = [['all', T('rule.filter_all', 'Toutes'), c.total], ['unread', T('rule.filter_unread', 'Non lues'), c.unread],
        ['critical', 'Critical', c.critical], ['warning', 'Warning', c.warning], ['info', 'Info', c.info]];
    document.getElementById('an-filters').innerHTML = items.map(([id, label, n]) =>
        `<button type="button" class="${RP.an.filter === id ? 'active' : ''}" aria-pressed="${RP.an.filter === id}" onclick="RP.an.filter='${id}'; loadAnalyses(true)">${esc(label)} (${n || 0})</button>`).join('');
}

const RULE_CARD_OPTS = { showRuleName: false, hideRuleLink: true };

function loadNotesPanel() {
    const panel = document.getElementById('an-notes');
    if (!panel || !RP.id) return;
    panel.dataset.rule = RP.id;
    loadRuleNotes(panel).catch(e => console.error('notes', e));
}
document.addEventListener('notes-changed', () => {
    if (RP.id && RP.tab === 'analyses') { loadNotesPanel(); pollAnalyses(); }
});

async function loadAnalyses(reset) {
    const list = document.getElementById('an-list');
    const offset = reset ? 0 : RP.an.offset;
    const focusId = reset ? RP.focusAnalysisId : null;
    const limit = focusId ? 60 : 20;
    try {
        const res = await apiFetch(analysesUrl(offset, limit));
        RP.an.counts = res.counts;
        RP.an.hasMore = res.has_more;
        renderAnalysisFilters();
        list.querySelector('.rp-more')?.remove();
        if (reset) list.innerHTML = '';
        list.append(...res.events.map(ev => renderEventCard(ev, { ...RULE_CARD_OPTS, collapsed: ev.analysis.id !== focusId })));
        if (reset && !res.events.length) list.innerHTML = `<div class="no-logs">${esc(T('monitor.no_recent_analyses', 'Aucune analyse récente'))}</div>`;
        RP.an.offset = offset + res.events.length;
        if (RP.an.hasMore) list.insertAdjacentHTML('beforeend', `<button class="btn btn-secondary rp-more" onclick="loadAnalyses(false)">${esc(T('monitor.show_more', 'Afficher plus'))}</button>`);
        if (focusId) {
            const card = document.getElementById(`analysis-card-${focusId}`);
            if (card) { card.scrollIntoView({ behavior: 'smooth', block: 'start' }); markAnalysisAsViewed(focusId); }
            RP.focusAnalysisId = null;
        }
    } catch (e) {
        list.innerHTML = `<div class="loading">${esc(T('common.error', 'Erreur'))} : ${esc(e.message)}</div>`;
    }
}

async function pollAnalyses() {
    const list = document.getElementById('an-list');
    if (!list.querySelector('.analysis-card')) return loadAnalyses(true);
    try {
        const res = await apiFetch(analysesUrl(0, Math.max(RP.an.offset, 10)));
        const newest = list.querySelector('.analysis-card')?.dataset.event;
        const changed = res.events[0] && `${res.events[0].rule_id}-${res.events[0].key}` !== newest;
        RP.an.counts = res.counts;
        renderAnalysisFilters();
        reconcileEvents(list, res.events, { ...RULE_CARD_OPTS, freshClass: 'newly-added-highlight' });
        RP.an.offset = Math.max(RP.an.offset, res.events.length);
        if (changed) loadHeader();
    } catch (_) { /* transient */ }
}

document.addEventListener('events-changed', () => { if (RP.id) { loadAnalyses(true); loadHeader(); } });

// Called by renderAnalysisCard buttons
async function deleteAnalysis(id, btnElement) {
    showInlineConfirm(btnElement, T('common.confirm_delete_analysis', 'Supprimer cette analyse ?'), async () => {
        try {
            await apiFetch(`/api/dashboard/analyses/${id}`, { method: 'DELETE' });
            document.getElementById(`analysis-card-${id}`)?.remove();
        } catch (e) {
            alert(T('common.error', 'Erreur') + ': ' + e.message);
        }
    });
}

async function retryAnalysis(analysisId, btn) {
    const oldHtml = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `🔄 ${T('common.analyzing', 'Analyse…')}`;
    try {
        const res = await apiFetch(`/api/monitor/retry/${analysisId}`, { method: 'POST' });
        if (!res.task_id) throw new Error(T('monitor.unexpected_server_response', 'Réponse inattendue du serveur'));
        pollTask(res.task_id, btn, oldHtml, () => {
            const selected = document.querySelector('.log-line.selected');
            if (selected) onLineClick(selected, RP.id);
            if (RP.tab === 'analyses') { RP.focusAnalysisId = analysisId; loadAnalyses(true); }
        });
    } catch (e) {
        alert(T('common.error', 'Erreur') + ': ' + e.message);
        btn.disabled = false;
        btn.innerHTML = oldHtml;
    }
}

/**
 * Polling d'une tâche d'analyse en arrière-plan (GET /api/monitor/task/{taskId} toutes les 2 s).
 */
function pollTask(taskId, btn, originalHtml, onDone) {
    const maxAttempts = 150; // 5 minutes max
    let attempts = 0;
    const interval = setInterval(async () => {
        attempts++;
        if (attempts > maxAttempts) {
            clearInterval(interval);
            btn.innerHTML = '⏰ Timeout';
            setTimeout(() => { btn.innerHTML = originalHtml; btn.disabled = false; }, 3000);
            return;
        }
        try {
            const res = await apiFetch(`/api/monitor/task/${taskId}`);
            if (res.status === 'done') {
                clearInterval(interval);
                btn.innerHTML = '✅';
                setTimeout(() => { btn.innerHTML = originalHtml; btn.disabled = false; }, 2000);
                if (onDone) onDone(res.analysis_id);
            } else if (res.status === 'error') {
                clearInterval(interval);
                btn.innerHTML = '❌';
                setTimeout(() => { btn.innerHTML = originalHtml; btn.disabled = false; }, 3000);
                alert(T('common.error', 'Erreur') + ': ' + (res.error || T('monitor.unknown_error', 'Erreur inconnue')));
            }
        } catch (_) { /* erreur réseau transitoire — on réessaie */ }
    }, 2000);
}

async function notifyAnalysis(analysisId, btn) {
    const oldHtml = btn.innerHTML;
    try {
        btn.innerHTML = '⏳ ' + T('common.sending', 'Envoi…');
        btn.disabled = true;
        const res = await apiFetch(`/api/monitor/notify/${analysisId}`, { method: 'POST' });
        if (res.status !== 'ok') throw new Error(res.detail);
        btn.innerHTML = '✅ ' + T('common.sent', 'Envoyée');
        setTimeout(() => { btn.innerHTML = oldHtml; btn.disabled = false; }, 2000);
    } catch (e) {
        alert(T('common.error', 'Erreur') + ': ' + e.message);
        btn.innerHTML = oldHtml;
        btn.disabled = false;
    }
}

/* ── Resolution tab ────────────────────────────────────────────────────────── */
const OUTCOMES = {
    accepted: ['rp-v-ok', 'rule.v_accepted', 'Accepté par l’IA'],
    accepted_no_ai: ['rp-v-info', 'rule.v_no_ai', 'Clos sans IA'],
    rejected_ai: ['rp-v-warn', 'rule.v_rejected', 'Refusé par l’IA'],
    rejected_low_confidence: ['rp-v-warn', 'rule.v_low_conf', 'Refusé (confiance faible)'],
    manual: ['rp-v-neutral', 'rule.v_manual', 'Manuel'],
    false_positive_user: ['rp-v-fp', 'rule.v_fp', 'Faux positif'],
};

function outcomeBadge(o) {
    const [cls, key, fb] = OUTCOMES[o] || ['rp-v-neutral', '', o];
    return `<span class="rp-v ${cls}">${esc(key ? T(key, fb) : fb)}</span>`;
}

function triggerText(trigger) {
    if (!trigger) return '—';
    let m = trigger.match(/^Timeout \((\d+) min\)( \+ probe OK)?/);
    if (m) return T('rule.trigger_calm', 'Calme {d}', { d: fmtMinutes(parseInt(m[1])) }) + (m[2] ? ' + ' + T('rule.trigger_probe', 'sonde OK') : '');
    m = trigger.match(/^Pattern match: '(.*)'$/);
    if (m) return T('rule.trigger_pattern', 'Ligne de succès « {p} »', { p: m[1] });
    if (trigger === 'Manual override') return T('rule.v_manual', 'Manuel');
    return trigger;
}

const AI_MODE_LABELS = { ambiguous: ['rf.ai_ambiguous', 'Si ambigu'], always: ['rf.ai_always', 'Toujours'], never: ['rf.ai_never', 'Jamais'] };

async function renderResolution() {
    const panel = document.getElementById('tab-resolution');
    let r;
    try {
        r = await apiFetch(`/api/rules/${RP.id}/resolution`);
    } catch (e) {
        panel.innerHTML = `<div class="loading">${esc(T('common.error', 'Erreur'))} : ${esc(e.message)}</div>`;
        return;
    }
    if (RP.tab !== 'resolution') return;
    const st = r.settings;
    const inc = r.incident;

    if (!r.enabled) {
        panel.innerHTML = `<section class="rp-card rp-calm"><div><strong>${esc(T('rule.res_disabled', 'Le retour à la normale automatique est désactivé pour cette règle.'))}</strong>
            <span class="rp-muted">${esc(T('rule.res_disabled_hint', 'Les incidents restent ouverts jusqu’à une clôture manuelle.'))}</span></div>
            <button class="btn btn-secondary btn-sm" onclick="goToSettings('rf-resolution')">${esc(T('rule.res_enable', 'Activer dans les réglages'))}</button></section>`;
        return;
    }

    const aiLabel = T(...AI_MODE_LABELS[st.ai_mode] || ['', st.ai_mode]);
    const calmText = (st.calm_mode === 'adaptive' ? T('rule.calm_adaptive_short', 'adaptatif, minimum {d}', { d: fmtMinutes(st.timeout_minutes) })
        : T('rule.calm_fixed_short', 'fixe, {d}', { d: fmtMinutes(st.timeout_minutes) }));
    let conditions;
    if (inc) {
        const pct = Math.min(100, Math.round(100 * (inc.calm_elapsed_minutes || 0) / Math.max(1, inc.required_calm_minutes)));
        const probe = inc.probe;
        conditions = `
        <section class="rp-card rp-conditions">
            <div class="rp-card-head"><h2>${esc(T('rule.closing', 'Clôture de l’incident'))}</h2></div>
            <span class="rp-muted">${esc(T('rule.closing_hint', 'L’incident sera clos quand toutes les conditions sont remplies.'))}</span>
            <div class="rp-cond">
                <div class="rp-cond-row"><span>${esc(T('rule.cond_calm', 'Calme : aucune erreur pendant {d}', { d: fmtMinutes(inc.required_calm_minutes) }))}</span>
                    <span class="rp-muted">${esc(fmtMinutes(inc.calm_elapsed_minutes))} / ${esc(fmtMinutes(inc.required_calm_minutes))}</span></div>
                <div class="rp-progress"><div style="width:${pct}%"></div></div>
            </div>
            ${st.probe_type !== 'none' && st.probe_target ? `<div class="rp-cond-row"><span>${esc(T('rule.cond_probe', 'Sonde de santé répond'))}</span>
                ${probe ? `<span class="rp-v ${probe.ok ? 'rp-v-ok' : 'rp-v-ko'}">${esc(probe.detail)}</span>` : `<span class="rp-muted">${esc(T('rule.probe_pending', 'en attente'))}</span>`}</div>` : ''}
            <div class="rp-cond-row"><span>${esc(T('rule.cond_liveness', 'Log vivant pendant le calme'))}</span>
                <span class="rp-v ${inc.lines_since_error ? 'rp-v-ok' : 'rp-v-neutral'}">${inc.lines_since_error == null ? '—' : esc(T('rule.lines_n', '{n} lignes', { n: inc.lines_since_error }))}</span></div>
            <div class="rp-cond-row"><span>${esc(T('rule.cond_ai', 'Validation IA'))}</span><span class="rp-muted">${esc(aiLabel)}</span></div>
            <div class="rp-actions rp-card-foot">
                <button class="btn btn-secondary btn-sm" onclick="resolveManually(this)">${esc(T('rule.resolve', 'Marquer comme résolu'))}</button>
                <button class="btn btn-secondary btn-sm" onclick="goToSettings('rf-resolution')">${esc(T('rule.edit_conditions', 'Modifier les conditions'))}</button>
            </div>
        </section>`;
    } else {
        conditions = `
        <section class="rp-card rp-conditions">
            <div class="rp-card-head"><h2>${esc(T('rule.no_incident', 'Aucun incident en cours'))}</h2></div>
            <div class="rp-cond-row"><span>${esc(T('rule.cond_calm_short', 'Calme requis'))}</span><span class="rp-muted">${esc(calmText)}</span></div>
            <div class="rp-cond-row"><span>${esc(T('rule.probe', 'Sonde de santé'))}</span><span class="rp-muted mono">${st.probe_type !== 'none' && st.probe_target ? esc(st.probe_target) : '—'}</span></div>
            <div class="rp-cond-row"><span>${esc(T('rf.success_lines', 'Lignes de succès'))}</span><span class="rp-muted">${esc(st.success_lines ? T('rule.on', 'activées') : T('rule.off', 'désactivées'))}</span></div>
            <div class="rp-cond-row"><span>${esc(T('rule.cond_ai', 'Validation IA'))}</span><span class="rp-muted">${esc(aiLabel)}</span></div>
            <div class="rp-actions rp-card-foot"><button class="btn btn-secondary btn-sm" onclick="goToSettings('rf-resolution')">${esc(T('rule.edit_conditions', 'Modifier les conditions'))}</button></div>
        </section>`;
    }

    const timeline = inc ? `
    <section class="rp-card rp-timeline-card">
        <div class="rp-card-head"><h2>${esc(T('rule.timeline', 'Chronologie de l’incident'))}</h2></div>
        <ol class="rp-timeline">${timelineItems(r.timeline)}</ol>
    </section>` : '';

    const suggestions = r.suggestions.length ? `
        <div class="rp-suggestions">
            <span class="rp-suggestions-title">${esc(T('rule.suggestions', 'Suggestions de l’IA à valider'))}</span>
            ${r.suggestions.map(p => `<div class="rp-suggestion">
                <span class="mono">${esc(p.pattern)}</span>
                <span class="rp-actions">
                    <button class="btn btn-primary btn-sm" data-p="${esc(p.pattern)}" onclick="patternAction('accept', this.dataset.p)">${esc(T('rule.accept', 'Accepter'))}</button>
                    <button class="btn btn-secondary btn-sm" data-p="${esc(p.pattern)}" onclick="patternAction('reject', this.dataset.p)">${esc(T('rule.reject', 'Refuser'))}</button>
                </span></div>`).join('')}
        </div>` : '';
    const maxWeight = Math.max(5, ...r.patterns.map(p => p.weight || 1));
    const patterns = `
    <section class="rp-card rp-table-card">
        <div class="rp-card-head">
            <div><h2>${esc(T('rf.success_lines', 'Lignes de succès'))}</h2>
                <span class="rp-muted">${esc(st.success_lines ? T('rule.patterns_hint', 'Une ligne contenant l’un de ces motifs déclenche une vérification. Les lignes d’erreur sont ignorées.')
                    : T('rule.patterns_off', 'Désactivées dans les réglages : ces motifs ne déclenchent rien pour le moment.'))}</span></div>
            <div class="rp-actions">
                <button class="btn btn-secondary btn-sm" onclick="auditPatterns(this)">${esc(T('rule.audit', 'Audit IA des motifs'))}</button>
            </div>
        </div>
        ${suggestions}
        <div id="rp-audit-result" class="rp-audit hidden"></div>
        ${r.patterns.length ? `<div class="rp-scroll"><table class="rp-table">
            <thead><tr><th>${esc(T('rule.pattern', 'Motif'))}</th><th>${esc(T('rule.origin', 'Origine'))}</th><th>${esc(T('rule.reliability', 'Fiabilité'))}</th><th>${esc(T('rule.triggers', 'Déclenchements'))}</th><th>${esc(T('rule.last_validation', 'Dernière validation'))}</th><th></th></tr></thead>
            <tbody>${r.patterns.map(p => `<tr>
                <td class="mono">${esc(p.pattern)}</td>
                <td>${esc(p.origin === 'manual' ? T('rule.origin_manual', 'Manuel') : p.origin === 'autopilot' ? T('rule.origin_autopilot', 'Pilote auto') : T('rule.origin_learned', 'Appris'))}</td>
                <td><span class="rp-weight"><span style="width:${Math.min(100, Math.round(100 * (p.weight || 1) / maxWeight))}%"></span></span> <span class="rp-muted">${esc(T('rule.weight', 'poids {n}', { n: p.weight || 1 }))}</span></td>
                <td>${esc(T('rule.hits', '{ok} acceptés · {ko} refusés', { ok: p.hits_ok, ko: p.hits_ko }))}</td>
                <td>${p.last_validated_at ? esc(fmtWhen(p.last_validated_at)) : '—'}</td>
                <td><button class="btn btn-secondary btn-sm" data-p="${esc(p.pattern)}" onclick="deletePattern(this, this.dataset.p)">${esc(T('common.delete', 'Supprimer'))}</button></td>
            </tr>`).join('')}</tbody>
        </table></div>` : `<p class="rp-muted">${esc(T('rule.no_patterns', 'Aucun motif.'))}</p>`}
        <form class="rp-add-pattern" onsubmit="addPattern(event)">
            <input type="text" id="rp-new-pattern" placeholder="${esc(T('rule.new_pattern', 'Nouveau motif, ex. connection restored'))}" aria-label="${esc(T('rule.new_pattern', 'Nouveau motif'))}">
            <button type="submit" class="btn btn-secondary btn-sm">${esc(T('rule.add_pattern', '+ Ajouter'))}</button>
        </form>
    </section>`;

    const c = r.verdict_counts;
    const total = Object.values(c).reduce((x, y) => x + y, 0);
    const verdicts = `
    <section class="rp-card rp-table-card">
        <div class="rp-card-head"><h2>${esc(T('rule.history', 'Historique des vérifications · 30 jours'))}</h2>
            <span class="rp-muted">${esc(T('rule.history_counts', '{n} vérifications · {a} acceptées · {r} refusées', {
                n: total, a: (c.accepted || 0) + (c.accepted_no_ai || 0), r: (c.rejected_ai || 0) + (c.rejected_low_confidence || 0) }))}</span></div>
        ${r.verdicts.length ? `<div class="rp-scroll"><table class="rp-table rp-verdicts">
            <thead><tr><th>${esc(T('rule.date', 'Date'))}</th><th>${esc(T('rule.trigger', 'Déclencheur'))}</th><th>${esc(T('rule.verdict', 'Verdict'))}</th><th>${esc(T('rule.ai', 'IA'))}</th><th>${esc(T('rule.explanation', 'Explication'))}</th><th></th></tr></thead>
            <tbody>${r.verdicts.slice(0, RP.verdictLimit).map(v => `<tr>
                <td class="rp-nowrap">${esc(fmtWhen(v.at))}</td>
                <td>${esc(triggerText(v.trigger))}</td>
                <td>${outcomeBadge(v.outcome)}</td>
                <td>${v.confidence != null ? v.confidence + ' %' : '—'}</td>
                <td class="rp-expl"><span class="rp-clamp" title="${esc(v.explanation || '')}" onclick="this.classList.toggle('open')">${esc(v.explanation || '')}</span></td>
                <td>${['accepted', 'accepted_no_ai', 'manual'].includes(v.outcome) ? `<button class="btn btn-secondary btn-sm" onclick="markFalsePositive(${v.id}, this)">${esc(T('rule.false_positive', 'Faux positif'))}</button>` : ''}</td>
            </tr>`).join('')}</tbody>
        </table></div>
        ${r.verdicts.length > RP.verdictLimit ? `<button class="btn btn-secondary btn-sm rp-more" onclick="RP.verdictLimit += 20; renderResolution()">${esc(T('monitor.show_more', 'Afficher plus'))}</button>` : ''}`
        : `<p class="rp-muted">${esc(T('rule.no_verdicts', 'Aucune vérification sur 30 jours.'))}</p>`}
    </section>`;

    panel.innerHTML = `<div class="rp-row">${conditions}${timeline}</div>${patterns}${verdicts}`;
}

// A long incident keeps its start and its latest steps in view, the rest stays folded.
const TIMELINE_EDGE = 5;
function timelineItems(items) {
    if (items.length <= TIMELINE_EDGE * 2 + 2) return items.map(timelineItem).join('');
    const hidden = items.slice(TIMELINE_EDGE, -TIMELINE_EDGE);
    return `${items.slice(0, TIMELINE_EDGE).map(timelineItem).join('')}
        <li class="rp-tl-more"><details><summary>${esc(T('rule.timeline_more', 'Voir les {n} étapes intermédiaires', { n: hidden.length }))}</summary>
            <ol class="rp-timeline">${hidden.map(timelineItem).join('')}</ol></details></li>
        ${items.slice(-TIMELINE_EDGE).map(timelineItem).join('')}`;
}

function timelineItem(e) {
    let dot = 'rp-dot--idle', title = '', detail = '';
    if (e.kind === 'opened') { dot = 'rp-dot--alert'; title = T('rule.tl_opened', 'Incident ouvert'); }
    else if (e.kind === 'analysis') { dot = e.severity === 'critical' ? 'rp-dot--alert' : 'rp-dot--warn'; title = `${T('rule.tl_analysis', 'Analyse')} ${e.severity}`; detail = e.summary; }
    else if (e.kind === 'verdict') {
        dot = ['accepted', 'accepted_no_ai'].includes(e.outcome) ? 'rp-dot--ok' : 'rp-dot--purple';
        title = `${triggerText(e.trigger)} — ${(OUTCOMES[e.outcome] ? T(OUTCOMES[e.outcome][1], OUTCOMES[e.outcome][2]) : e.outcome)}${e.confidence != null ? ` (${e.confidence} %)` : ''}`;
        detail = e.explanation || '';
    } else if (e.kind === 'probe') { dot = e.ok ? 'rp-dot--ok' : 'rp-dot--alert'; title = `${T('rule.probe', 'Sonde de santé')} : ${e.detail}`; }
    else if (e.kind === 'next') { title = T('rule.tl_next', 'Prochaine vérification prévue'); detail = T('rule.tl_next_hint', 'si aucune erreur d’ici là'); }
    return `<li><span class="rp-tl-time mono">${esc(fmtWhen(e.at))}</span><span class="rp-dot ${dot}" aria-hidden="true"></span>
        <span class="rp-tl-body"><strong>${esc(title)}</strong>${detail ? `<span class="rp-muted">${esc(detail)}</span>` : ''}</span></li>`;
}

async function patternAction(action, pattern) {
    try {
        await apiFetch(`/api/rules/${RP.id}/resolution-patterns/${action}`, { method: 'POST', body: { pattern } });
        renderResolution();
    } catch (e) { alert(T('common.error', 'Erreur') + ': ' + e.message); }
}

async function addPattern(event) {
    event.preventDefault();
    const input = document.getElementById('rp-new-pattern');
    const pattern = input.value.trim();
    if (!pattern) return;
    try {
        await apiFetch(`/api/rules/${RP.id}/resolution-patterns`, { method: 'POST', body: { pattern } });
        renderResolution();
    } catch (e) { alert(T('common.error', 'Erreur') + ': ' + e.message); }
}

function deletePattern(btn, pattern) {
    showInlineConfirm(btn, T('rule.delete_pattern_confirm', 'Supprimer ce motif ?'), async () => {
        try {
            await apiFetch(`/api/monitor/rules/${RP.id}/patterns/${encodeURIComponent(pattern)}`, { method: 'DELETE' });
            renderResolution();
        } catch (e) { alert(T('common.error', 'Erreur') + ': ' + e.message); }
    });
}

async function markFalsePositive(verdictId, btn) {
    btn.disabled = true;
    try {
        await apiFetch(`/api/monitor/verdicts/${verdictId}/mark-false-positive`, { method: 'POST' });
        loadHeader();
        renderResolution();
    } catch (e) {
        btn.disabled = false;
        alert(T('common.error', 'Erreur') + ': ' + e.message);
    }
}

async function auditPatterns(btn) {
    const box = document.getElementById('rp-audit-result');
    btn.disabled = true;
    const original = btn.textContent;
    btn.textContent = T('rule.audit_running', 'Audit en cours…');
    try {
        const res = await apiFetch(`/api/monitor/rules/${RP.id}/audit-patterns`, { method: 'POST' });
        await renderResolution();
        const fresh = document.getElementById('rp-audit-result');
        fresh.classList.remove('hidden');
        fresh.innerHTML = `<strong>${esc(T('rule.audit_done', '{n} motif(s) retiré(s)', { n: (res.removed || []).length }))}</strong>
            ${res.removed && res.removed.length ? `<span class="mono"> ${esc(res.removed.join(', '))}</span>` : ''}<p>${esc(res.explanation || '')}</p>`;
    } catch (e) {
        if (box) { box.classList.remove('hidden'); box.textContent = T('common.error', 'Erreur') + ': ' + e.message; }
        btn.disabled = false;
        btn.textContent = original;
    }
}

/* ── Live log tab (from the former Monitor page) ───────────────────────────── */
let monitorLogLines = 60;
let logIntervals = [];
let isFrozen = false;
let selectedLineText = null;
let activeKeywordFilter = null;
let autoOpenLine = false;

function stopLogPolling() {
    logIntervals.forEach(clearInterval);
    logIntervals = [];
}

async function renderLogTab() {
    const rule = RP.rule;
    const panel = document.getElementById('tab-log');
    if (!rule) return;
    if (!activeKeywordFilter) activeKeywordFilter = '__matches__';
    try { monitorLogLines = (await apiFetch('/api/config')).monitor_log_lines || 60; } catch (_) { /* default */ }
    const kwBadge = (kw, label, cls = '') =>
        `<button type="button" class="log-kw-badge kw-filter-btn ${cls} ${activeKeywordFilter === kw ? 'active' : ''}" data-kw="${encodeURIComponent(kw)}" onclick="toggleKeywordFilter(this, ${rule.id})">${esc(label)}</button>`;
    panel.innerHTML = `
        <div class="rp-strip">
            <span class="rp-strip-item"><span class="buffer-dot idle" id="buffer-dot-${rule.id}"></span> <span id="buffer-label-${rule.id}">${esc(T('monitor.buffer_inactive', 'Buffer inactif'))}</span></span>
        </div>
        <div class="kw-filter-badges rp-log-filters" id="kw-filters-${rule.id}">
            ${kwBadge('__matches__', T('rule.filter_matches', 'Correspondances'))}
            ${kwBadge('__all__', T('monitor.all_filter', 'Tout voir'))}
            ${rule.keywords.map(kw => kwBadge(kw, kw)).join('')}
            ${(rule.excluded_patterns || []).map(p => kwBadge('__excluded_' + p, '🚫 ' + p, 'excl-filter-btn')).join('')}
        </div>
        <div class="monitor-viewer-header">
            <span class="viewer-title">📄 ${esc(T('monitor.live_log', 'Journal en direct'))}
                <span class="viewer-linecount" id="linecount-${rule.id}"></span>
                <span class="kw-filter-label hidden" id="kw-filter-label-${rule.id}"></span></span>
            <div class="viewer-actions">
                <button class="btn btn-secondary btn-sm" id="freeze-btn-${rule.id}" onclick="toggleFreeze(${rule.id})">${isFrozen ? '▶️ ' + esc(T('monitor.resume', 'Reprendre')) : '❄️ ' + esc(T('monitor.freeze', 'Figer'))}</button>
                <button class="btn btn-secondary btn-sm" onclick="copyViewerContent(${rule.id})">${esc(T('common.copy', 'Copier'))}</button>
            </div>
        </div>
        <div class="monitor-log-viewer" id="log-viewer-${rule.id}"><div class="loading">${esc(T('common.loading', 'Chargement…'))}</div></div>
        <div class="monitor-detail-panel hidden" id="detail-panel-${rule.id}">
            <div class="detail-panel-header">
                <strong>🔍 ${esc(T('monitor.detail_line_text', 'Détail de la ligne'))}</strong>
                <button class="btn-icon" onclick="closeDetailPanel(${rule.id})" aria-label="${esc(T('common.close', 'Fermer'))}">✕</button>
            </div>
            <div id="detail-panel-content-${rule.id}"></div>
        </div>`;
    fetchLogs(rule);
    fetchBufferStatus(rule.id);
    logIntervals.push(setInterval(() => fetchLogs(RP.rule), 3000));
    logIntervals.push(setInterval(() => fetchBufferStatus(rule.id), 2000));
}

async function fetchLogs(rule) {
    if (isFrozen || !rule) return;
    const viewer = document.getElementById(`log-viewer-${rule.id}`);
    if (!viewer) return;
    try {
        const kwParam = rule.keywords.join(',');
        let res;
        if (rule.log_file_path && rule.log_file_path.startsWith('[WEBHOOK]:')) {
            const token = rule.log_file_path.split(':')[1];
            res = await apiFetch(`/api/webhook/tail/${encodeURIComponent(token)}?lines=${monitorLogLines}&keywords=${encodeURIComponent(kwParam)}`);
        } else if (rule.log_file_path && rule.log_file_path.startsWith('[SYSLOG]:')) {
            const hostname = rule.log_file_path.split(':')[1];
            res = await apiFetch(`/api/monitor/syslog/tail/${encodeURIComponent(hostname)}?lines=${monitorLogLines}&keywords=${encodeURIComponent(kwParam)}`);
        } else {
            res = await apiFetch(`/api/files/tail?path=${encodeURIComponent(rule.log_file_path)}&lines=${monitorLogLines}&keywords=${encodeURIComponent(kwParam)}`);
        }
        if (!res.lines || res.lines.length === 0) {
            viewer.innerHTML = `<em class="no-logs">${esc(T('monitor.file_empty', 'Fichier vide ou inaccessible.'))}</em>`;
            return;
        }
        const isAtBottom = Math.abs((viewer.scrollHeight - viewer.scrollTop) - viewer.clientHeight) < 20;
        const savedScrollTop = viewer.scrollTop;
        const excludedPatterns = rule.excluded_patterns || [];

        viewer.innerHTML = res.lines.map((line, idx) => {
            const rawText = line.text || '';
            const matchedExclPattern = excludedPatterns.find(pat => rawText.toLowerCase().includes(pat.toLowerCase())) || null;
            const isExcluded = matchedExclPattern != null;
            const kwBadges = line.matched_keywords && line.matched_keywords.length > 0
                ? `<span class="log-kw-badges">${line.matched_keywords.map(k => `<span class="log-kw-badge">${esc(k)}</span>`).join('')}</span>` : '';
            const exclBadge = isExcluded ? `<span class="log-kw-badges"><span class="log-excl-badge">🚫 ${esc(matchedExclPattern)}</span></span>` : '';
            return `<div class="log-line ${line.matched ? 'matched' : ''} ${selectedLineText === rawText ? 'selected' : ''} ${isExcluded ? 'log-line-excluded' : ''}" data-rule="${rule.id}" data-idx="${idx}" data-excluded="${isExcluded}" data-excl-pat="${encodeURIComponent(matchedExclPattern || '')}" data-text="${encodeURIComponent(rawText)}" onclick="onLineClick(this, ${rule.id})">
                <span class="log-text">${esc(rawText)}</span>${kwBadges}${exclBadge}
            </div>`;
        }).join('');

        const matched = res.lines.filter(l => l.matched).length;
        const lc = document.getElementById(`linecount-${rule.id}`);
        if (lc) lc.textContent = `(${res.lines.length} ${T('monitor.lines_label', 'lignes')}, ${matched} ${T('monitor.matched_label', 'correspondantes')})`;
        applyKeywordFilter(rule.id);
        updateFilterLabel(rule.id);

        if (autoOpenLine && selectedLineText) {
            const selectedEl = viewer.querySelector('.log-line.selected');
            if (selectedEl) {
                selectedEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
                const panel = document.getElementById(`detail-panel-${rule.id}`);
                if (panel && panel.classList.contains('hidden')) onLineClick(selectedEl, rule.id);
                autoOpenLine = false;
            }
        } else if (isAtBottom) {
            viewer.scrollTop = viewer.scrollHeight;
        } else {
            viewer.scrollTop = savedScrollTop;
        }
    } catch (e) {
        viewer.innerHTML = `<em class="no-logs rp-ko">${esc(T('common.error', 'Erreur'))} : ${esc(e.message)}</em>`;
    }
}

function toggleKeywordFilter(badgeEl, ruleId) {
    const kw = decodeURIComponent(badgeEl.dataset.kw || '');
    if (!kw) return;
    activeKeywordFilter = (activeKeywordFilter === kw && kw !== '__matches__') ? '__matches__' : kw;
    document.querySelectorAll('.kw-filter-btn').forEach(b => b.classList.toggle('active', decodeURIComponent(b.dataset.kw || '') === activeKeywordFilter));
    applyKeywordFilter(ruleId);
    updateFilterLabel(ruleId);
    if (activeKeywordFilter === '__all__') {
        const viewer = document.getElementById(`log-viewer-${ruleId}`);
        if (viewer) viewer.scrollTop = viewer.scrollHeight;
    }
}

function applyKeywordFilter(ruleId) {
    const viewer = document.getElementById(`log-viewer-${ruleId}`);
    if (!viewer) return;
    let visibleCount = 0;
    viewer.querySelectorAll('.log-line').forEach(line => {
        let show;
        if (activeKeywordFilter === '__all__') show = true;
        else if (activeKeywordFilter.startsWith('__excluded_')) {
            const pat = activeKeywordFilter.substring('__excluded_'.length);
            show = line.dataset.excluded === 'true' && decodeURIComponent(line.dataset.exclPat || '') === pat;
        } else if (activeKeywordFilter === '__matches__') {
            show = line.querySelector('.log-kw-badge') !== null && line.dataset.excluded !== 'true';
        } else {
            show = [...line.querySelectorAll('.log-kw-badge')].map(b => b.textContent.trim().toLowerCase()).includes(activeKeywordFilter.toLowerCase());
        }
        line.style.display = show ? '' : 'none';
        if (show) visibleCount++;
    });
    const emptyId = `kw-empty-${ruleId}`;
    let emptyEl = viewer.querySelector(`#${emptyId}`);
    if (visibleCount === 0 && viewer.querySelectorAll('.log-line').length > 0) {
        if (!emptyEl) {
            emptyEl = document.createElement('em');
            emptyEl.id = emptyId;
            emptyEl.className = 'no-logs';
            viewer.appendChild(emptyEl);
        }
        emptyEl.textContent = T('monitor.no_logs_filter', 'Aucune ligne à afficher pour ce filtre.');
    } else if (emptyEl) {
        emptyEl.remove();
    }
}

function updateFilterLabel(ruleId) {
    const label = document.getElementById(`kw-filter-label-${ruleId}`);
    if (!label) return;
    if (activeKeywordFilter === '__matches__') label.textContent = ` — ${T('monitor.view_matches_only', 'vue : correspondances uniquement')}`;
    else if (activeKeywordFilter === '__all__') label.textContent = ` — ${T('monitor.view_full_log', 'vue : log complet')}`;
    else label.textContent = ` — ${T('monitor.filter_keyword', 'filtre :')} "${activeKeywordFilter}"`;
    label.classList.remove('hidden');
}

async function fetchBufferStatus(ruleId) {
    try {
        const buf = await apiFetch(`/api/monitor/buffer/${ruleId}`);
        const dot = document.getElementById(`buffer-dot-${ruleId}`);
        const label = document.getElementById(`buffer-label-${ruleId}`);
        if (!dot || !label) return;
        if (buf.active) {
            dot.className = 'buffer-dot active';
            const kwStr = buf.matched_keywords.length > 0
                ? ` — ${T('monitor.buffer_keywords', 'mots-clés')} : <strong>${buf.matched_keywords.map(k => esc(k)).join(', ')}</strong>` : '';
            label.innerHTML = `⏳ ${esc(T('monitor.buffer_active', 'Regroupement en cours'))} <span class="detection-id-badge">#${esc(buf.detection_id || '...')}</span> — ${buf.line_count} ${esc(T('monitor.buffer_lines_pending', 'ligne(s) en attente'))}${kwStr}`;
        } else {
            dot.className = 'buffer-dot idle';
            label.textContent = T('monitor.buffer_inactive', 'Aucune ligne en attente d’analyse');
        }
    } catch (_) { /* transient */ }
}

async function onLineClick(el, ruleId) {
    const text = decodeURIComponent(el.dataset.text || '');
    const panel = document.getElementById(`detail-panel-${ruleId}`);
    const content = document.getElementById(`detail-panel-content-${ruleId}`);
    const viewer = document.getElementById(`log-viewer-${ruleId}`);
    if (!panel || !content) return;
    panel.classList.remove('hidden');
    document.querySelectorAll('.log-line.selected, .log-line.bundle-selected').forEach(l => l.classList.remove('selected', 'bundle-selected'));
    el.classList.add('selected');
    selectedLineText = text;

    let related = null;
    try {
        const analyses = await apiFetch(`/api/monitor/analyses/${ruleId}`);
        related = analyses.find(a => a.triggered_line && a.triggered_line.includes(text.substring(0, 80)));
    } catch (_) { /* none */ }
    if (related && viewer) {
        viewer.querySelectorAll('.log-line').forEach(line => {
            const lText = decodeURIComponent(line.dataset.text || '');
            if (lText && related.triggered_line.includes(lText.substring(0, 60))) line.classList.add('bundle-selected');
        });
    }
    const matchedKws = el.querySelectorAll('.log-kw-badge');
    const kwList = [...matchedKws].map(b => esc(b.textContent)).join(', ');

    content.innerHTML = `
        <div class="detail-row"><span class="detail-label">${esc(T('monitor.detail_line_text', 'Texte de la ligne'))}</span><code class="detail-value">${esc(text)}</code></div>
        <div class="detail-row"><span class="detail-label">${esc(T('monitor.detail_detected_kw', 'Mots-clés détectés'))}</span>
            <span class="detail-value">${matchedKws.length ? kwList : `<em>${esc(T('monitor.detail_none_filtered', 'Aucun'))}</em>`}</span></div>
        ${related ? `
        <div class="detail-row"><span class="detail-label">${esc(T('monitor.severity', 'Sévérité'))}</span>${sevBadge(related.severity)}</div>
        <div class="detail-row"><span class="detail-label">${esc(T('monitor.analyzed_at', 'Analysée le'))}</span><span class="detail-value">${related.analyzed_at ? esc(formatDate(related.analyzed_at)) : '—'}</span></div>
        <div class="detail-row"><span class="detail-label">${esc(T('monitor.llm_response', 'Réponse IA'))}</span>
            <div class="detail-value analysis-response markdown-body">${related.ollama_response ? renderMarkdown(related.ollama_response) : '—'}</div></div>
        <div class="detail-actions rp-actions">
            <button class="btn btn-secondary btn-sm" onclick="RP.focusAnalysisId=${related.id}; selectTab('analyses')">${esc(T('rule.open_analysis', 'Voir l’analyse'))}</button>
            <button class="btn btn-secondary btn-sm" onclick="togglePromptView(${related.id}, this)">📜 ${esc(T('common.view_prompt', 'Prompt'))}</button>
            <button class="btn btn-primary btn-sm" onclick="openChat(${related.id})">💬 ${esc(T('common.deepen', 'Approfondir'))}</button>
            <button class="btn btn-secondary btn-sm" onclick="investigateAnalysis(${related.id})">🔎 ${esc(T('common.investigate', 'Investiguer'))}</button>
        </div>` : `
        <div class="detail-row"><em>${esc(T('monitor.no_auto_analysis', 'Aucune analyse automatique pour cette ligne.'))}</em></div>
        <div class="detail-actions"><button class="btn btn-primary btn-sm" onclick="manualAnalyze(${ruleId}, this)">🤖 ${esc(T('monitor.analyze_with_ollama', 'Analyser avec l’IA'))}</button></div>`}`;
}

function closeDetailPanel(ruleId) {
    document.getElementById(`detail-panel-${ruleId}`)?.classList.add('hidden');
    document.querySelectorAll('.log-line.selected, .log-line.bundle-selected').forEach(l => l.classList.remove('selected', 'bundle-selected'));
    selectedLineText = null;
}

function toggleFreeze(ruleId) {
    isFrozen = !isFrozen;
    const btn = document.getElementById(`freeze-btn-${ruleId}`);
    if (btn) {
        btn.textContent = isFrozen ? '▶️ ' + T('monitor.resume', 'Reprendre') : '❄️ ' + T('monitor.freeze', 'Figer');
        btn.classList.toggle('active', isFrozen);
    }
}

function copyViewerContent(ruleId) {
    const viewer = document.getElementById(`log-viewer-${ruleId}`);
    if (!viewer) return;
    const text = [...viewer.querySelectorAll('.log-line')].map(el => el.querySelector('.log-text')?.textContent || '').join('\n');
    copyToClipboard(text).then(() => alert(T('monitor.logs_copied', 'Logs copiés !'))).catch(() => {});
}

async function manualAnalyze(ruleId, btn) {
    if (!selectedLineText) return;
    const oldHtml = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `⏳ ${T('common.analyzing', 'Analyse…')}`;
    try {
        const res = await apiFetch('/api/monitor/analyze-line', { method: 'POST', body: { line: selectedLineText, rule_id: ruleId } });
        if (!res.task_id) throw new Error(T('monitor.unexpected_server_response', 'Réponse inattendue du serveur'));
        pollTask(res.task_id, btn, oldHtml, (analysisId) => {
            if (analysisId) { RP.focusAnalysisId = analysisId; selectTab('analyses'); }
        });
    } catch (e) {
        alert(T('common.error', 'Erreur') + ': ' + e.message);
        btn.disabled = false;
        btn.innerHTML = oldHtml;
    }
}
