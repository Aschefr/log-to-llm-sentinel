// ─── Dashboard ──────────────────────────────────────────────────────────────
// A watch post: a verdict first, then only what needs an action, then the trend,
// the autopilot and Ollama, then the history. Live updates never close or move
// what the user is reading.
// ─────────────────────────────────────────────────────────────────────────────

const DASH_PAGE_SIZE = 20;
const DASH_REFRESH_MS = 15000;
const DASH_CHART_HEIGHT = 112;
const DASH_TIMELINE_HOURS = [6, 12, 24, 48];
const DASH_TIMELINE_KEY = 'sentinel.dashboard.timelineHours';
const DASH_ICONS = {
    ok: 'M12 2C6.5 2 2 6.5 2 12S6.5 22 12 22 22 17.5 22 12 17.5 2 12 2M10 17L5 12L6.41 10.59L10 14.17L17.59 6.58L19 8L10 17Z',
    alert: 'M13 14H11V9H13M13 18H11V16H13M1 21H23L12 2L1 21Z',
    unknown: 'M11,18H13V16H11V18M12,2A10,10 0 0,0 2,12A10,10 0 0,0 12,22A10,10 0 0,0 22,12A10,10 0 0,0 12,2M12,20C7.59,20 4,16.41 4,12C4,7.59 7.59,4 12,4C16.41,4 20,7.59 20,12C20,16.41 16.41,20 12,20M12,6A4,4 0 0,0 8,10H10A2,2 0 0,1 12,8A2,2 0 0,1 14,10C14,12 11,11.75 11,15H13C13,12.75 16,12.5 16,10A4,4 0 0,0 12,6Z',
    off: 'M12,2A10,10 0 0,0 2,12A10,10 0 0,0 12,22A10,10 0 0,0 22,12A10,10 0 0,0 12,2M17,13H7V11H17V13Z',
};

const DASH = {
    watch: null, ollama: null, failed: false, updatedAt: null,
    filter: 'all', loaded: 0, hasMore: false, historyReady: false, historyFailed: false, historyRequest: 0,
    seenAttention: new Set(), attentionReady: false,
    q: '', rule: null, ruleName: '', historyCounts: null, historyRules: null,
    timeline: null, timelineHours: savedTimelineHours(), timelineRequest: 0, timelineIndex: null,
};

function dT(key, fallback, vars) {
    let s = window.t ? window.t(key) : key;
    if (!s || s === key) s = fallback;
    if (vars) Object.entries(vars).forEach(([k, v]) => { s = s.split(`{${k}}`).join(v); });
    return s;
}

function dCount(n, oneKey, oneText, manyKey, manyText) {
    return n === 1 ? dT(oneKey, oneText, { n }) : dT(manyKey, manyText, { n });
}

function dLocale() {
    return window.i18n?.getCurrentLang() || [];
}

function dWhen(iso) {
    if (!iso) return '—';
    const d = new Date(iso), now = new Date();
    const hm = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    const y = new Date(now); y.setDate(now.getDate() - 1);
    if (d.toDateString() === now.toDateString()) return hm;
    if (d.toDateString() === y.toDateString()) return `${dT('rule.yesterday', 'hier')} ${hm}`;
    return `${d.toLocaleDateString([], { day: '2-digit', month: '2-digit' })} ${hm}`;
}

function dSince(iso) {
    return formatMinutesShort(Math.max(0, (Date.now() - new Date(iso).getTime()) / 60000));
}

// Only touches the DOM when the content changed: a focused element, a hovered bar or an open table stay as they are.
function dSetHtml(el, html) {
    if (el._dashHtml === html) return;
    el._dashHtml = html;
    el.innerHTML = html;
}

document.addEventListener('DOMContentLoaded', () => {
    renderAll();
    refresh();
    setInterval(() => { if (!document.hidden) refresh(); }, DASH_REFRESH_MS);
    document.addEventListener('monitoring-changed', refresh);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
    document.addEventListener('analysis-viewed', refresh);
    document.addEventListener('events-changed', refresh);
    setupChartTip();
    setupTimeline();
    let shownLang = null;
    window.i18n?.onLanguageChange(lang => {
        const first = shownLang === null;
        shownLang = lang;
        renderAll();
        // Le premier rendu se fait avec les textes français de secours : seule une autre langue oblige à tout redessiner
        if (!first || lang !== 'fr') resetHistory();
        // Historique arrivé avant les traductions : les cartes sont redessinées sur place, sans vider la liste
        else if (DASH.historyReady) {
            document.querySelectorAll('#dash-history-list > .analysis-card').forEach(el => { el.dataset.signature = ''; });
            loadHistory();
        }
    });
});

function refresh() {
    loadTimeline();
    loadWatch();
    loadOllama();
    loadHistory();
}

async function loadWatch() {
    try {
        DASH.watch = await apiFetch('/api/dashboard/watch');
        DASH.failed = false;
        DASH.updatedAt = new Date();
    } catch (error) {
        console.error('Dashboard watch:', error);
        DASH.failed = true;
    }
    renderAll();
    unskel('dash-trend-body', 'dash-pilot');
}

async function loadOllama() {
    try {
        DASH.ollama = await apiFetch('/api/dashboard/ollama');
    } catch (error) {
        DASH.ollama = null;
    }
    renderAll();
    unskel('dash-ollama');
}

function renderAll() {
    renderVerdict();
    if (!DASH.watch) return;
    renderSituation();
    renderAttention();
    renderTrend();
    renderTimeline();
    renderPilot();
    renderOllama();
    renderFilters();
}

// ─── Verdict ────────────────────────────────────────────────────────────────

function ollamaProblem() {
    const o = DASH.ollama;
    if (!o || o.state === 'ok') return null;
    return {
        unreachable: dT('dashboard.ollama_verdict_down', 'Ollama injoignable'),
        model_missing: dT('dashboard.ollama_verdict_model', 'Modèle Ollama introuvable'),
        not_configured: dT('dashboard.ollama_verdict_none', 'Ollama non configuré'),
    }[o.state];
}

function computeVerdict() {
    const w = DASH.watch;
    if (DASH.failed) {
        return { state: 'unknown', title: dT('dashboard.verdict_unknown', 'État inconnu'), retry: true,
            sub: DASH.updatedAt ? dT('dashboard.verdict_unknown_since', 'Sentinel ne répond pas. Dernières données reçues à {t}.', { t: dWhen(DASH.updatedAt.toISOString()) })
                : dT('dashboard.verdict_unknown_sub', 'Sentinel ne répond pas.') };
    }
    if (!w) return { state: 'checking', title: dT('dashboard.verdict_checking', 'Vérification…'), sub: '' };
    if (w.monitoring && w.monitoring.paused) {
        return { state: 'off', title: dT('dashboard.verdict_paused', 'Surveillance en pause'),
            sub: dT('dashboard.verdict_paused_sub', 'Depuis {t}. Les lignes reçues ne sont ni analysées ni notifiées : ce qui suit peut être dépassé.', { t: dWhen(w.monitoring.since) }) };
    }
    if (!w.rules.total) {
        return { state: 'off', title: dT('dashboard.verdict_empty', 'Aucune source surveillée'), sub: dT('dashboard.verdict_empty_sub', 'Ajoute une source : le pilote automatique s’occupe du reste.'), create: true };
    }
    if (!w.rules.enabled) {
        return { state: 'off', title: dT('dashboard.verdict_off', 'Surveillance arrêtée'), sub: dCount(w.rules.total, 'dashboard.disabled_one', '{n} règle désactivée', 'dashboard.disabled_many', '{n} règles désactivées') };
    }
    const a = w.attention, parts = [];
    if (a.alerts.length) parts.push(dCount(a.alerts.length, 'dashboard.alerts_one', '{n} alerte ouverte', 'dashboard.alerts_many', '{n} alertes ouvertes'));
    if (a.muted.length) parts.push(dCount(a.muted.length, 'dashboard.muted_one', '{n} source muette', 'dashboard.muted_many', '{n} sources muettes'));
    if (a.pilot_errors.length) parts.push(dCount(a.pilot_errors.length, 'dashboard.pilot_errors_one', '{n} échec de l’autopilote', 'dashboard.pilot_errors_many', '{n} échecs de l’autopilote'));
    const problem = ollamaProblem();
    if (problem) parts.push(problem);

    const sub = [dCount(w.rules.enabled, 'dashboard.sources_one', '{n} source suivie', 'dashboard.sources_many', '{n} sources suivies')];
    if (w.rules.observing) sub.push(dT('dashboard.observing', '{n} en observation', { n: w.rules.observing }));
    sub.push(w.last_analysis_at ? dT('dashboard.last_analysis', 'dernière analyse {t}', { t: dWhen(w.last_analysis_at) }) : dT('dashboard.no_analysis', 'aucune analyse'));
    return {
        // Red is kept for what is serious: an alert the AI rated below critical stays amber.
        state: a.alerts.some(x => !x.analysis || x.analysis.severity === 'critical') ? 'alert' : parts.length ? 'warn' : 'ok',
        title: parts.length ? parts.join(' · ') : dT('dashboard.verdict_ok', 'Tout va bien'),
        sub: sub.join(' · '),
    };
}

function renderVerdict() {
    const v = computeVerdict();
    const icon = { ok: 'ok', alert: 'alert', warn: 'alert', unknown: 'unknown', checking: 'unknown', off: 'off' }[v.state];
    document.getElementById('dash-verdict').className = `dash-verdict dash-verdict--${v.state}`;
    dSetHtml(document.getElementById('dash-verdict-icon'), `<svg viewBox="0 0 24 24" width="34" height="34"><path fill="currentColor" d="${DASH_ICONS[icon]}"/></svg>`);
    const title = document.getElementById('dash-verdict-title');
    if (title.textContent !== v.title) title.textContent = v.title;
    let sub = escapeHtml(v.sub);
    if (v.retry) sub += ` <button type="button" class="btn btn-secondary btn-sm" onclick="refresh()">${escapeHtml(dT('dashboard.retry', 'Réessayer'))}</button>`;
    if (v.create) sub += ` <a class="btn btn-primary btn-sm" href="/rules/new">${escapeHtml(dT('rules.new_rule', '+ Nouvelle règle'))}</a>`;
    dSetHtml(document.getElementById('dash-verdict-sub'), sub);
    document.getElementById('dash-verdict-time').textContent = DASH.updatedAt && !DASH.failed
        ? dT('dashboard.updated_at', 'Mis à jour à {t}', { t: DASH.updatedAt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) }) : '';
    document.title = `${v.title} - Log to LLM Sentinel`;
}

// ─── Point de situation ─────────────────────────────────────────────────────
// Facts computed by the server, always current; above them, a paragraph the AI rewrites when the situation changes.

function situationText(ai, facts) {
    const rules = Object.fromEntries(facts.activity.top.map(t => [t.analysis_id, t.rule_id]));
    return escapeHtml(ai.text).replace(/\[#(\d+)\]/g, (m, id) => rules[id]
        ? `<a class="dash-situation-ref" href="/rules/${rules[id]}?analysis=${id}#analyses">#${id}</a>` : '');
}

function situationMeta(ai, facts) {
    if (facts.paused) return dT('dashboard.situation_paused', 'Surveillance en pause : ce point n’est plus mis à jour.');
    const when = ai.at ? dT('dashboard.situation_written', 'Rédigé par l’IA il y a {d}', { d: dSince(ai.at) }) : '';
    return ai.state === 'stale' ? `${when} · ${dT('dashboard.situation_stale', 'la situation a changé, réécriture à venir')}` : when;
}

function situationFacts(f) {
    const r = f.rules, a = f.activity, items = [];
    let watched = dCount(r.watched, 'dashboard.situation_rules_one', '{n} règle surveillée', 'dashboard.situation_rules_many', '{n} règles surveillées');
    if (r.in_alert.length) watched += ` · ${dT('dashboard.situation_in_alert', 'en alerte : {names}', { names: r.in_alert.map(x => x.rule_name).join(', ') })}`;
    if (r.silent.length) watched += ` · ${dT('dashboard.situation_silent', 'muette : {names}', { names: r.silent.map(x => x.rule_name).join(', ') })}`;
    items.push(watched);
    items.push(a.events
        ? dT('dashboard.situation_activity', '24 h : {events} · {new} nouveaux · {open} encore ouverts', {
            events: dCount(a.events, 'dashboard.situation_events_one', '{n} événement', 'dashboard.situation_events_many', '{n} événements'),
            new: a.new, open: a.open })
        : dT('dashboard.situation_no_activity', '24 h : aucune alerte.'));
    if (r.quietest && r.quietest.last_line_at && Date.now() - new Date(r.quietest.last_line_at).getTime() > 3600000) {
        items.push(dT('dashboard.situation_quietest', 'Silence le plus long : {name}, depuis {d}', { name: r.quietest.rule_name, d: dSince(r.quietest.last_line_at) })
            + (r.quietest.limit_hours ? ` (${dT('dashboard.silence_limit', 'limite : {h} h', { h: r.quietest.limit_hours })})` : ''));
    }
    return `<ul class="dash-situation-facts">${items.map(t => `<li>${escapeHtml(t)}</li>`).join('')}</ul>`;
}

function renderSituation() {
    const box = document.getElementById('dash-situation');
    const s = DASH.watch.situation;
    if (!box || !s) return;
    box.classList.remove('skel', 'skel--line');
    const ai = s.ai, parts = [];
    if (ai.enabled && ai.text) {
        parts.push(`<p class="dash-situation-text">${situationText(ai, s.facts)}</p>`);
        const meta = situationMeta(ai, s.facts);
        if (meta) parts.push(`<p class="dash-situation-meta">${escapeHtml(meta)}</p>`);
    } else if (ai.enabled && !s.facts.paused) {
        parts.push(`<p class="dash-situation-meta">${escapeHtml(dT('dashboard.situation_pending', 'L’IA rédigera ce point dès qu’elle sera disponible.'))}</p>`);
    }
    parts.push(situationFacts(s.facts));
    dSetHtml(box, parts.join(''));
}

// ─── À traiter ──────────────────────────────────────────────────────────────

function attentionRow(key, tag, name, text, when, href) {
    const fresh = DASH.attentionReady && !DASH.seenAttention.has(key);
    DASH.seenAttention.add(key);
    return `<a class="dash-row${fresh ? ' dash-new' : ''}" href="${href}">
        ${tag}
        <span class="dash-row-main"><strong>${escapeHtml(name)}</strong><span class="dash-row-text">${escapeHtml(text)}</span></span>
        <span class="dash-row-when">${escapeHtml(when)}</span>
    </a>`;
}

function renderAttention() {
    const a = DASH.watch.attention, rows = [];
    a.alerts.forEach(x => {
        const analysis = x.analysis;
        const severity = analysis ? analysis.severity : 'warning';
        const text = x.status === 'resolving' ? dT('dashboard.resolving', 'Retour à la normale en cours de vérification.')
            : analysis ? analysis.summary : dT('rule.status_alert', 'En alerte');
        const extra = x.open_count > 1 ? ` · ${dT('dashboard.open_count', '{n} analyses ouvertes', { n: x.open_count })}` : '';
        const when = (x.since ? dT('rules.since', 'depuis {d}', { d: dSince(x.since) }) : '') + extra;
        const href = analysis ? `/rules/${x.rule_id}?analysis=${analysis.id}#analyses` : `/rules/${x.rule_id}`;
        rows.push(attentionRow(`alert-${x.rule_id}-${analysis ? analysis.id : ''}`, `<span class="severity-badge ${escapeHtml(severity)}">${escapeHtml(severity)}</span>`, x.rule_name, text, when, href));
    });
    a.pilot_errors.forEach(x => rows.push(attentionRow(`pilot-${x.rule_id}`, `<span class="dash-tag">${escapeHtml(dT('dashboard.tag_pilot', 'Pilote'))}</span>`, x.rule_name,
        dT('dashboard.pilot_error_text', 'La mise en place a échoué. L’autopilote réessaiera automatiquement.'), '', `/rules/${x.rule_id}`)));
    const problem = DASH.ollama && DASH.ollama.state !== 'ok' ? DASH.ollama.state : null;
    if (problem) {
        const text = { unreachable: dT('dashboard.ollama_attention_down', 'Le serveur ne répond pas : les nouvelles alertes ne peuvent pas être analysées.'),
            model_missing: dT('dashboard.ollama_attention_model', 'Le modèle {m} n’est pas installé sur le serveur.', { m: DASH.ollama.model }),
            not_configured: dT('dashboard.ollama_attention_none', 'Aucun serveur n’est configuré.') }[problem];
        rows.push(attentionRow(`ollama-${problem}`, `<span class="dash-tag">Ollama</span>`, DASH.ollama.model || 'Ollama', text, '', '/config'));
    }
    (a.source_errors || []).forEach(x => rows.push(attentionRow(`source-${x.rule_id}`, `<span class="dash-tag">${escapeHtml(dT('dashboard.tag_source', 'Source'))}</span>`, x.rule_name,
        scErrorText(x.code, x.detail), '', `/rules/${x.rule_id}#settings`)));
    a.muted.forEach(x => rows.push(attentionRow(`muted-${x.rule_id}`, `<span class="dash-tag">${escapeHtml(dT('dashboard.tag_silence', 'Silence'))}</span>`, x.rule_name,
        x.last_line_at ? dT('dashboard.silence_text', 'Aucune ligne depuis {d}.', { d: dSince(x.last_line_at) }) : dT('dashboard.silence_never', 'Aucune ligne reçue.'),
        dT('dashboard.silence_limit', 'limite : {h} h', { h: x.limit_hours }), `/rules/${x.rule_id}`)));

    const unread = DASH.watch.counts.unread;
    const unreadRow = unread ? `<div class="dash-unread"><button type="button" class="dash-row dash-row--unread" onclick="showUnread()">
        <span class="dash-tag">${escapeHtml(dT('dashboard.tag_read', 'À lire'))}</span>
        <span class="dash-row-main"><strong>${escapeHtml(dCount(unread, 'dashboard.unread_one', '{n} événement non lu', 'dashboard.unread_many', '{n} événements non lus'))}</strong><span class="dash-row-text">${escapeHtml(dT('dashboard.unread_why', 'Critique, ou incident encore ouvert.'))}</span></span>
        <span class="dash-row-when">${escapeHtml(dT('dashboard.unread_action', 'Voir'))}</span>
    </button><button type="button" class="btn btn-secondary btn-sm dash-triage" onclick="openTriage()">${escapeHtml(dT('triage.open', 'Conseil de l’IA'))}</button></div>` : '';
    DASH.attentionReady = true;
    const empty = !rows.length && !unread;
    dSetHtml(document.getElementById('dash-attention'), empty
        ? `<p class="dash-calm">${escapeHtml(DASH.failed ? dT('dashboard.attention_unknown', 'Impossible de savoir ce qui est à traiter pour le moment.') : dT('dashboard.nothing_to_do', 'Rien à traiter.'))}</p>`
        : `<div class="dash-list">${rows.join('')}${unreadRow}</div>`);
}

function showUnread() {
    setHistoryFilter('unread');
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    document.getElementById('dash-history').scrollIntoView({ behavior: reduce ? 'auto' : 'smooth' });
}

// ─── Tendance ───────────────────────────────────────────────────────────────

function niceMax(v) {
    if (v <= 5) return Math.max(v, 1);
    const p = 10 ** Math.floor(Math.log10(v)), m = v / p;
    return ([1, 1.5, 2, 3, 5, 7.5, 10].find(step => m <= step)) * p;
}

function dayDate(day) {
    const [y, m, d] = day.date.split('-').map(Number);
    return new Date(y, m - 1, d);
}

function severityLabels() {
    return { critical: dT('dashboard.critical', 'Critiques'), warning: dT('dashboard.warning', 'Avertissements'), info: dT('dashboard.info', 'Infos') };
}

function trendDelta(t) {
    if (!t.previous_total) return t.total ? dT('dashboard.trend_new', 'Aucune alerte les 7 jours précédents') : '';
    const pct = Math.round((t.total - t.previous_total) / t.previous_total * 100);
    if (pct === 0) return dT('dashboard.trend_same', 'Autant que les 7 jours précédents');
    return pct > 0 ? dT('dashboard.trend_up', '+{p} % par rapport aux 7 jours précédents', { p: pct })
        : dT('dashboard.trend_down', '−{p} % par rapport aux 7 jours précédents', { p: -pct });
}

function renderTrend() {
    const t = DASH.watch.trend, labels = severityLabels();
    const body = document.getElementById('dash-trend-body');
    const totals = { critical: 0, warning: 0, info: 0 };
    t.days.forEach(d => Object.keys(totals).forEach(s => { totals[s] += d[s]; }));
    const top = niceMax(Math.max(...t.days.map(d => d.critical + d.warning + d.info), 1));
    const todayDate = new Date().toDateString();

    const days = t.days.map((d, i) => {
        const date = dayDate(d), total = d.critical + d.warning + d.info;
        const isToday = date.toDateString() === todayDate;
        const segs = ['critical', 'warning', 'info'].filter(s => d[s])
            .map(s => `<span class="dash-seg dash-seg--${s}" style="height:${Math.max(3, Math.round(d[s] / top * DASH_CHART_HEIGHT))}px"></span>`).join('');
        const full = date.toLocaleDateString(dLocale(), { weekday: 'long', day: 'numeric', month: 'long' });
        const aria = `${full} : ${total} · ${labels.critical} ${d.critical} · ${labels.warning} ${d.warning} · ${labels.info} ${d.info}`;
        return `<div class="dash-day${isToday ? ' dash-day--today' : ''}" tabindex="0" role="img" data-day="${i}" aria-label="${escapeHtml(aria)}">
            <span class="dash-stack">${segs}</span>
            <span class="dash-day-label">${escapeHtml(isToday ? dT('dashboard.today', 'auj.') : date.toLocaleDateString(dLocale(), { weekday: 'short' }))}</span>
        </div>`;
    }).join('');

    const legend = ['critical', 'warning', 'info'].map(s => `<li><span class="dash-swatch dash-swatch--${s}" aria-hidden="true"></span>${escapeHtml(labels[s])} <strong>${totals[s]}</strong></li>`).join('');
    const rows = t.days.map(d => `<tr><td>${escapeHtml(dayDate(d).toLocaleDateString(dLocale(), { weekday: 'short', day: 'numeric', month: 'short' }))}</td><td>${d.critical}</td><td>${d.warning}</td><td>${d.info}</td><td>${d.critical + d.warning + d.info}</td></tr>`).join('');
    const delta = trendDelta(t);
    const mttr = t.mttr30_minutes != null ? dT('dashboard.trend_mttr', 'Retour à la normale médian sur 30 jours : {d}', { d: formatMinutesShort(t.mttr30_minutes) }) : '';
    const wasOpen = body.querySelector('details')?.open;

    dSetHtml(body, `
        <p class="dash-trend-total"><strong>${escapeHtml(t.total ? dCount(t.total, 'dashboard.trend_total_one', '{n} alerte', 'dashboard.trend_total_many', '{n} alertes') : dT('dashboard.trend_none', 'Aucune alerte sur 7 jours.'))}</strong>${delta ? `<span>${escapeHtml(delta)}</span>` : ''}</p>
        <div class="dash-chart" style="--dash-chart-height:${DASH_CHART_HEIGHT}px">
            <span class="dash-tick dash-tick--top">${top}</span><span class="dash-tick dash-tick--zero">0</span>
            <div class="dash-days">${days}</div>
        </div>
        <ul class="dash-legend">${legend}</ul>
        ${mttr ? `<p class="dash-trend-mttr">${escapeHtml(mttr)}</p>` : ''}
        <details class="dash-table"><summary>${escapeHtml(dT('dashboard.trend_table', 'Voir le tableau'))}</summary>
            <table><thead><tr><th>${escapeHtml(dT('dashboard.col_day', 'Jour'))}</th><th>${escapeHtml(labels.critical)}</th><th>${escapeHtml(labels.warning)}</th><th>${escapeHtml(labels.info)}</th><th>${escapeHtml(dT('dashboard.col_total', 'Total'))}</th></tr></thead><tbody>${rows}</tbody></table>
        </details>`);
    if (wasOpen) body.querySelector('details').open = true;
}

function setupChartTip() {
    const panel = document.getElementById('dash-trend'), tip = document.getElementById('dash-tip');
    const show = day => {
        const d = DASH.watch?.trend.days[+day.dataset.day];
        if (!d) return;
        const labels = severityLabels();
        const rows = ['critical', 'warning', 'info'].map(s => `<li><span class="dash-swatch dash-swatch--${s}" aria-hidden="true"></span>${escapeHtml(labels[s])}<strong>${d[s]}</strong></li>`).join('');
        tip.innerHTML = `<div class="dash-tip-date">${escapeHtml(dayDate(d).toLocaleDateString(dLocale(), { weekday: 'long', day: 'numeric', month: 'long' }))}</div><ul>${rows}</ul>`;
        tip.classList.remove('hidden');
        const box = panel.getBoundingClientRect(), r = day.getBoundingClientRect();
        const left = r.left - box.left + r.width / 2 - tip.offsetWidth / 2;
        tip.style.left = `${Math.max(8, Math.min(left, box.width - tip.offsetWidth - 8))}px`;
        tip.style.top = `${Math.max(8, r.top - box.top - tip.offsetHeight + 24)}px`;
    };
    const hide = () => tip.classList.add('hidden');
    panel.addEventListener('mouseover', e => { const day = e.target.closest('.dash-day'); if (day) show(day); });
    panel.addEventListener('mouseout', e => { if (e.target.closest('.dash-day')) hide(); });
    panel.addEventListener('focusin', e => { const day = e.target.closest('.dash-day'); if (day) show(day); });
    panel.addEventListener('focusout', hide);
    panel.addEventListener('keydown', e => { if (e.key === 'Escape') hide(); });
}

// ─── Alertes des dernières heures ───────────────────────────────────────────
// Une courbe du nombre d'alertes, en 24 tranches quelle que soit la durée choisie. Les bandes colorées en donnent la
// gravité ; le trait suit le total. Le survol (ou les flèches au clavier) lit une tranche, le tableau les donne toutes.

function savedTimelineHours() {
    try {
        const h = Number(localStorage.getItem(DASH_TIMELINE_KEY));
        if (DASH_TIMELINE_HOURS.includes(h)) return h;
    } catch (e) { /* stockage indisponible : la valeur par défaut suffit */ }
    return 12;
}

function setTimelineHours(h) {
    if (h === DASH.timelineHours) return;
    DASH.timelineHours = h;
    try { localStorage.setItem(DASH_TIMELINE_KEY, String(h)); } catch (e) { /* idem */ }
    renderTimelineRange();
    // Le graphique garde sa forme, estompé, le temps que la nouvelle période arrive
    document.getElementById('dash-timeline-body').classList.add('is-loading');
    loadTimeline();
}

async function loadTimeline() {
    const request = ++DASH.timelineRequest, hours = DASH.timelineHours;
    try {
        const data = await apiFetch(`/api/dashboard/timeline?hours=${hours}`);
        if (request !== DASH.timelineRequest) return;
        DASH.timeline = data;
    } catch (error) {
        if (request !== DASH.timelineRequest) return;
        console.error('Dashboard timeline:', error);
    }
    document.getElementById('dash-timeline-body').classList.remove('is-loading');
    unskel('dash-timeline-body');
    renderTimeline();
}

function renderTimelineRange() {
    dSetHtml(document.getElementById('dash-range'), DASH_TIMELINE_HOURS.map(h =>
        `<button type="button" class="${DASH.timelineHours === h ? 'active' : ''}" aria-pressed="${DASH.timelineHours === h}" onclick="setTimelineHours(${h})">${escapeHtml(dT('dashboard.timeline_hours', '{h} h', { h }))}</button>`).join(''));
}

function bucketTotal(b) {
    return b.critical + b.warning + b.info;
}

function bucketTime(d) {
    return d.toLocaleTimeString(dLocale(), { hour: '2-digit', minute: '2-digit' });
}

// « hier 14:00 – 16:00 », « 14:30 – 14:45 (en cours) »
function bucketLabel(tl, i) {
    const start = new Date(tl.buckets[i].start), end = new Date(start.getTime() + tl.step_minutes * 60000);
    const now = new Date(), y = new Date(now); y.setDate(now.getDate() - 1);
    let day = '';
    if (start.toDateString() === y.toDateString()) day = `${dT('rule.yesterday', 'hier')} `;
    else if (start.toDateString() !== now.toDateString()) day = `${start.toLocaleDateString(dLocale(), { weekday: 'short', day: 'numeric' })} `;
    const current = i === tl.buckets.length - 1 ? ` (${dT('dashboard.timeline_current', 'en cours')})` : '';
    return `${day}${bucketTime(start)} – ${bucketTime(end)}${current}`;
}

// L'heure seule aujourd'hui, précédée du jour sinon
function dayTime(d) {
    return d.toDateString() === new Date().toDateString() ? bucketTime(d)
        : `${d.toLocaleDateString(dLocale(), { weekday: 'short' })} ${bucketTime(d)}`;
}

// Repères sur des heures rondes de l'heure locale : toutes les 2 h sur 6 h, 3 h sur 12 h, 6 h sur 24 h, 12 h sur 48 h
function axisTicks(tl) {
    const every = { 6: 120, 12: 180, 24: 360, 48: 720 }[tl.hours] || tl.step_minutes * 6;
    const slot = i => { const d = new Date(tl.buckets[i].start); return Math.floor((d.getHours() * 60 + d.getMinutes()) / every) + d.getDate() * 1000; };
    return tl.buckets.map((b, i) => i).filter(i => i > 0 && i < tl.buckets.length - 1 && slot(i) !== slot(i - 1));
}

function axisLabel(tl, i) {
    return i === tl.buckets.length - 1 ? dT('dashboard.timeline_now', 'maintenant') : dayTime(new Date(tl.buckets[i].start));
}

function renderTimeline() {
    renderTimelineRange();
    const tl = DASH.timeline, body = document.getElementById('dash-timeline-body');
    if (!tl) return;
    const labels = severityLabels(), n = tl.buckets.length, W = (n - 1) * 10, H = DASH_CHART_HEIGHT;
    const top = niceMax(Math.max(...tl.buckets.map(bucketTotal), 1));
    const x = i => i * 10, y = v => +(H - v / top * H).toFixed(2);
    // Bandes empilées : infos en bas, critiques en haut, comme les barres des 7 jours
    const levels = [b => b.info, b => b.info + b.warning, bucketTotal];
    const line = f => tl.buckets.map((b, i) => `${x(i)},${y(f(b))}`).join(' ');
    const bands = ['info', 'warning', 'critical'].map((s, k) => {
        const upper = line(levels[k]);
        const lower = k ? tl.buckets.map((b, i) => `${x(i)},${y(levels[k - 1](b))}`).reverse().join(' ') : `${W},${H} 0,${H}`;
        return `<polygon class="dash-band dash-band--${s}" points="${upper} ${lower}"/>`;
    }).join('');

    const totals = { critical: 0, warning: 0, info: 0 };
    tl.buckets.forEach(b => Object.keys(totals).forEach(s => { totals[s] += b[s]; }));
    const peak = tl.buckets.reduce((best, b, i) => bucketTotal(b) > bucketTotal(tl.buckets[best]) ? i : best, 0);
    const summary = tl.total
        ? `<strong>${escapeHtml(dCount(tl.total, 'dashboard.trend_total_one', '{n} alerte', 'dashboard.trend_total_many', '{n} alertes'))}</strong><span>${escapeHtml(dT('dashboard.timeline_peak', 'Pic : {n} vers {t}', { n: bucketTotal(tl.buckets[peak]), t: dayTime(new Date(tl.buckets[peak].start)) }))}</span>`
        : `<strong>${escapeHtml(dT('dashboard.timeline_none', 'Aucune alerte sur les {h} dernières heures.', { h: tl.hours }))}</strong>`;
    const ticks = [...axisTicks(tl), n - 1].map(i => {
        const edge = i === 0 ? ' dash-xlabel--first' : i === n - 1 ? ' dash-xlabel--last' : '';
        return `<span class="dash-xlabel${edge}" style="left:${(i / (n - 1) * 100).toFixed(3)}%">${escapeHtml(axisLabel(tl, i))}</span>`;
    }).join('');
    const legend = ['critical', 'warning', 'info'].map(s => `<li><span class="dash-swatch dash-swatch--${s}" aria-hidden="true"></span>${escapeHtml(labels[s])} <strong>${totals[s]}</strong></li>`).join('');
    const rows = tl.buckets.map((b, i) => `<tr><td>${escapeHtml(bucketLabel(tl, i))}</td><td>${b.critical}</td><td>${b.warning}</td><td>${b.info}</td><td>${bucketTotal(b)}</td></tr>`).join('');
    const aria = dT('dashboard.timeline_aria', 'Courbe des alertes sur {h} h, par tranches de {m} min. Flèches gauche et droite pour lire chaque tranche.', { h: tl.hours, m: tl.step_minutes });
    const wasOpen = body.querySelector('details')?.open;

    dSetHtml(body, `
        <p class="dash-trend-total">${summary}</p>
        <div class="dash-chart" style="--dash-chart-height:${H}px">
            <span class="dash-tick dash-tick--top">${top}</span><span class="dash-tick dash-tick--zero">0</span>
            <div class="dash-plot" tabindex="0" role="group" aria-label="${escapeHtml(aria)}">
                <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">
                    ${bands}
                    <polyline class="dash-line-total" points="${line(bucketTotal)}"/>
                </svg>
                <span class="dash-cross hidden" aria-hidden="true"><span class="dash-cross-dot"></span></span>
            </div>
            <div class="dash-xlabels" aria-hidden="true">${ticks}</div>
        </div>
        <ul class="dash-legend">${legend}</ul>
        <details class="dash-table"><summary>${escapeHtml(dT('dashboard.trend_table', 'Voir le tableau'))}</summary>
            <table><thead><tr><th>${escapeHtml(dT('dashboard.col_time', 'Heure'))}</th><th>${escapeHtml(labels.critical)}</th><th>${escapeHtml(labels.warning)}</th><th>${escapeHtml(labels.info)}</th><th>${escapeHtml(dT('dashboard.col_total', 'Total'))}</th></tr></thead><tbody>${rows}</tbody></table>
        </details>`);
    if (wasOpen) body.querySelector('details').open = true;
    fitAxisLabels();
    // Un rafraîchissement pendant le survol garde la tranche lue
    if (DASH.timelineIndex != null) showTimelinePoint(Math.min(DASH.timelineIndex, n - 1));
}

// Sur un écran étroit les heures se chevauchent : on retire celles qui touchent leur voisine, « maintenant » restant
function fitAxisLabels() {
    const labels = [...document.querySelectorAll('#dash-timeline .dash-xlabel')];
    if (!labels.length) return;
    labels.forEach(l => l.classList.remove('hidden'));
    const last = labels[labels.length - 1].getBoundingClientRect();
    let edge = -Infinity;
    labels.slice(0, -1).forEach(l => {
        const r = l.getBoundingClientRect();
        if (r.left < edge + 8 || r.right > last.left - 8) l.classList.add('hidden');
        else edge = r.right;
    });
}

function showTimelinePoint(i) {
    const tl = DASH.timeline, plot = document.querySelector('#dash-timeline .dash-plot');
    if (!tl || !plot) return;
    DASH.timelineIndex = i;
    const b = tl.buckets[i], n = tl.buckets.length, labels = severityLabels();
    const top = niceMax(Math.max(...tl.buckets.map(bucketTotal), 1));
    const cross = plot.querySelector('.dash-cross');
    cross.style.left = `${i / (n - 1) * 100}%`;
    cross.firstElementChild.style.top = `${(1 - bucketTotal(b) / top) * 100}%`;
    cross.classList.remove('hidden');

    const panel = document.getElementById('dash-timeline'), tip = document.getElementById('dash-timeline-tip');
    const rows = ['critical', 'warning', 'info'].map(s => `<li><span class="dash-swatch dash-swatch--${s}" aria-hidden="true"></span>${escapeHtml(labels[s])}<strong>${b[s]}</strong></li>`).join('');
    tip.innerHTML = `<div class="dash-tip-date">${escapeHtml(bucketLabel(tl, i))}</div><ul>${rows}</ul>`;
    tip.classList.remove('hidden');
    const box = panel.getBoundingClientRect(), r = plot.getBoundingClientRect();
    const px = r.left - box.left + r.width * i / (n - 1);
    // À droite du trait, ou à gauche quand la place manque
    const left = px + 14 + tip.offsetWidth <= box.width - 8 ? px + 14 : px - 14 - tip.offsetWidth;
    tip.style.left = `${Math.max(8, left)}px`;
    tip.style.top = `${r.top - box.top}px`;
}

function hideTimelinePoint() {
    DASH.timelineIndex = null;
    document.querySelector('#dash-timeline .dash-cross')?.classList.add('hidden');
    document.getElementById('dash-timeline-tip').classList.add('hidden');
}

function setupTimeline() {
    const body = document.getElementById('dash-timeline-body');
    let fitFrame = 0;
    window.addEventListener('resize', () => { cancelAnimationFrame(fitFrame); fitFrame = requestAnimationFrame(fitAxisLabels); });
    const pick = e => {
        const plot = e.target.closest?.('.dash-plot'), tl = DASH.timeline;
        if (!plot || !tl) return;
        const r = plot.getBoundingClientRect(), n = tl.buckets.length;
        showTimelinePoint(Math.max(0, Math.min(n - 1, Math.round((e.clientX - r.left) / r.width * (n - 1)))));
    };
    body.addEventListener('pointermove', pick);
    body.addEventListener('pointerdown', pick);
    body.addEventListener('pointerleave', hideTimelinePoint);
    body.addEventListener('focusin', e => { if (e.target.closest('.dash-plot') && DASH.timeline) showTimelinePoint(DASH.timeline.buckets.length - 1); });
    body.addEventListener('focusout', hideTimelinePoint);
    body.addEventListener('keydown', e => {
        if (!e.target.closest('.dash-plot') || !DASH.timeline) return;
        const n = DASH.timeline.buckets.length, i = DASH.timelineIndex ?? n - 1;
        const next = { ArrowLeft: i - 1, ArrowRight: i + 1, Home: 0, End: n - 1 }[e.key];
        if (e.key === 'Escape') hideTimelinePoint();
        if (next === undefined) return;
        e.preventDefault();
        showTimelinePoint(Math.max(0, Math.min(n - 1, next)));
    });
}

// ─── Pilote automatique et Ollama ───────────────────────────────────────────

// Text saved by the autopilot: `value` is shown as code, **bold** markers are dropped.
function pilotText(text) {
    return escapeHtml(text.replace(/\*\*/g, '')).replace(/`([^`]+)`/g, '<code>$1</code>');
}

function activityText(a) {
    if (a.type === 'event') return pilotText(a.text);
    const label = {
        'keyword:add': dT('dashboard.change_keyword_add', 'Mot-clé ajouté'), 'keyword:remove': dT('dashboard.change_keyword_remove', 'Mot-clé retiré'),
        'exclusion:add': dT('dashboard.change_exclusion_add', 'Exclusion ajoutée'), 'exclusion:remove': dT('dashboard.change_exclusion_remove', 'Exclusion retirée'),
    }[`${a.kind}:${a.action}`] || a.kind;
    const held = a.status === 'dismissed' ? ` ${dT('dashboard.change_dismissed', '(écarté par prudence)')}` : '';
    return `${escapeHtml(label)} <code>${escapeHtml(a.value)}</code>${escapeHtml(held)}`;
}

function pilotStatus(r) {
    const line = r.last_line_at ? dT('dashboard.pilot_last_line', 'dernière ligne il y a {d}', { d: dSince(r.last_line_at) }) : dT('dashboard.pilot_no_line', 'aucune ligne reçue');
    if (r.no_trigger && r.status === 'active') return { dot: 'rp-dot--warn', text: dT('dashboard.pilot_no_trigger', 'Ne surveille que le silence') };
    if (r.status === 'observing') return { dot: 'rp-dot--warn', text: `${dT('dashboard.pilot_observing', 'En observation jusqu’à {t}', { t: dWhen(r.observe_until) })} · ${line}` };
    if (r.status === 'learning') return { dot: 'rp-dot--warn', text: dT('dashboard.pilot_learning', 'Lecture de l’historique') };
    if (r.status === 'error') return { dot: 'rp-dot--alert', text: dT('dashboard.pilot_error', 'En échec') };
    return { dot: 'rp-dot--ok', text: dT('dashboard.pilot_active', 'Actif') };
}

function renderPilot() {
    const p = DASH.watch.pilot, el = document.getElementById('dash-pilot');
    if (!p.rules.length) {
        dSetHtml(el, `<p class="dash-panel-note">${escapeHtml(dT('dashboard.pilot_none', 'Aucune règle en pilote automatique.'))}</p>`);
        return;
    }
    const rules = p.rules.map(r => {
        const s = pilotStatus(r);
        return `<a class="dash-pilot-rule" href="/rules/${r.rule_id}"><span class="rp-dot ${s.dot}" aria-hidden="true"></span><span><strong>${escapeHtml(r.rule_name)}</strong><span class="dash-panel-note">${escapeHtml(s.text)}</span></span></a>`;
    }).join('');
    const activity = p.activity.map(a => `<li><time datetime="${escapeHtml(a.at || '')}">${escapeHtml(dWhen(a.at))}</time><span><strong>${escapeHtml(a.rule_name)}</strong> ${activityText(a)}</span></li>`).join('');
    dSetHtml(el, `<div class="dash-pilot-rules">${rules}</div>${activity ? `<h3 class="dash-panel-sub">${escapeHtml(dT('dashboard.pilot_activity', 'Dernières décisions'))}</h3><ul class="dash-activity">${activity}</ul>` : ''}`);
}

function renderOllama() {
    const o = DASH.ollama, el = document.getElementById('dash-ollama');
    if (!o) {
        dSetHtml(el, `<p class="dash-panel-note">${escapeHtml(dT('dashboard.ollama_unknown', 'État indisponible.'))}</p>`);
        return;
    }
    const load = [];
    if (o.running) load.push(dT('dashboard.ollama_running', 'analyse en cours'));
    if (o.waiting) load.push(dT('dashboard.ollama_waiting', '{n} en attente', { n: o.waiting }));
    const state = {
        ok: ['rp-dot--ok', dT('dashboard.ollama_ok', 'Joignable'), load.length ? load.join(' · ') : dT('dashboard.ollama_idle', 'au repos')],
        unreachable: ['rp-dot--alert', dT('dashboard.ollama_down', 'Injoignable'), ''],
        model_missing: ['rp-dot--warn', dT('dashboard.ollama_model', 'Modèle introuvable'), ''],
        not_configured: ['rp-dot--warn', dT('dashboard.ollama_none', 'Non configuré'), ''],
    }[o.state];
    const link = o.state === 'ok' ? '' : ` · <a href="/config">${escapeHtml(dT('dashboard.ollama_check', 'Vérifier la configuration'))}</a>`;
    dSetHtml(el, `<p class="dash-ollama"><span class="rp-dot ${state[0]}" aria-hidden="true"></span><span><strong>${escapeHtml(o.model || '—')}</strong><span class="dash-panel-note">${escapeHtml(state[1])}${state[2] ? ` · ${escapeHtml(state[2])}` : ''}${link}</span></span></p>`);
}

// ─── Historique ─────────────────────────────────────────────────────────────

function renderFilters() {
    // Les compteurs suivent la recherche et la règle choisie, dès que l'historique a répondu
    const c = DASH.historyCounts || DASH.watch?.counts, labels = severityLabels();
    if (!c) return;
    const filters = [['all', dT('dashboard.filter_all', 'Toutes'), c.total], ['critical', labels.critical, c.critical], ['warning', labels.warning, c.warning],
        ['info', labels.info, c.info], ['unread', dT('dashboard.filter_unread', 'Non lues'), c.unread]];
    dSetHtml(document.getElementById('dash-filters'), filters.map(([id, label, n]) =>
        `<button type="button" class="${DASH.filter === id ? 'active' : ''}" aria-pressed="${DASH.filter === id}" onclick="setHistoryFilter('${id}')">${escapeHtml(label)} (${n})</button>`).join(''));
    document.getElementById('dash-mark-all').classList.toggle('hidden', !DASH.watch?.counts.unread);
    renderRuleSelect();
}

// Filtre par règle : une liste, la plus fournie d'abord. Elle tient sur une ligne quel que soit le nombre de règles.
function renderRuleSelect() {
    let rules = DASH.historyRules || [];
    if (DASH.rule != null && !rules.some(r => r.rule_id === DASH.rule)) rules = [...rules, { rule_id: DASH.rule, rule_name: DASH.ruleName, count: 0 }];
    const el = document.getElementById('dash-rule');
    const total = rules.reduce((n, r) => n + r.count, 0);
    dSetHtml(el, `<option value="">${escapeHtml(dT('dashboard.rules_all', 'Toutes les règles'))} (${total})</option>`
        + rules.map(r => `<option value="${r.rule_id}"${DASH.rule === r.rule_id ? ' selected' : ''}>${escapeHtml(r.rule_name)} (${r.count})</option>`).join(''));
    el.value = DASH.rule == null ? '' : String(DASH.rule);
    el.classList.toggle('is-set', DASH.rule != null);
}

function setHistoryRule(value) {
    DASH.rule = value ? Number(value) : null;
    DASH.ruleName = (DASH.historyRules || []).find(r => r.rule_id === DASH.rule)?.rule_name || '';
    renderRuleSelect();
    resetHistory();
}

let _historySearchTimer = null;
function onHistorySearch(value) {
    clearTimeout(_historySearchTimer);
    _historySearchTimer = setTimeout(() => {
        if (DASH.q === value.trim()) return;
        DASH.q = value.trim();
        resetHistory();
    }, 300);
}

function setHistoryFilter(id) {
    if (DASH.filter === id) return;
    DASH.filter = id;
    if (DASH.watch) renderFilters();
    resetHistory();
}

function resetHistory() {
    document.getElementById('dash-history-list').innerHTML = '';
    DASH.loaded = 0;
    DASH.historyReady = false;
    loadHistory();
}

async function loadHistory(more = false) {
    const request = ++DASH.historyRequest;
    const limit = more ? DASH.loaded + DASH_PAGE_SIZE : Math.max(DASH.loaded, DASH_PAGE_SIZE);
    let url = `/api/dashboard/events?limit=${limit}&offset=0&rules=true`;
    if (DASH.rule != null) url += `&rule_id=${DASH.rule}`;
    if (DASH.q) url += `&q=${encodeURIComponent(DASH.q)}`;
    if (DASH.filter === 'unread') url += '&unread=true';
    else if (DASH.filter !== 'all') url += `&severity=${DASH.filter}`;
    try {
        const res = await apiFetch(url);
        if (request !== DASH.historyRequest) return;
        reconcileEvents(document.getElementById('dash-history-list'), res.events, { showRuleName: true, freshClass: DASH.historyReady ? 'dash-new' : null });
        DASH.loaded = res.events.length;
        DASH.hasMore = res.has_more;
        DASH.historyCounts = res.counts;
        DASH.historyRules = res.rules;
        renderFilters();
        DASH.historyFailed = false;
        DASH.historyReady = true;
    } catch (error) {
        if (request !== DASH.historyRequest) return;
        console.error('Dashboard history:', error);
        DASH.historyFailed = true;
    }
    renderHistoryState();
}

function renderHistoryState() {
    const el = document.getElementById('dash-history-state');
    const hasCards = document.getElementById('dash-history-list').children.length > 0;
    if (DASH.historyFailed && !hasCards) {
        dSetHtml(el, `<p class="dash-calm">${escapeHtml(dT('dashboard.loading_error', 'Erreur de chargement'))} <button type="button" class="btn btn-secondary btn-sm" onclick="loadHistory()">${escapeHtml(dT('dashboard.retry', 'Réessayer'))}</button></p>`);
    } else if (!hasCards && DASH.historyReady) {
        dSetHtml(el, `<p class="dash-calm">${escapeHtml(DASH.filter === 'all' && !DASH.q && DASH.rule == null ? dT('dashboard.history_none', 'Les analyses apparaîtront ici dès qu’une règle se déclenchera.') : dT('dashboard.history_none_filter', 'Aucune analyse pour ce filtre.'))}</p>`);
    } else if (!hasCards) {
        dSetHtml(el, '<div class="dash-skeleton" aria-hidden="true"><span></span><span></span><span></span></div>');
    } else if (DASH.hasMore) {
        dSetHtml(el, `<button type="button" class="btn btn-secondary dash-more" onclick="loadHistory(true)">${escapeHtml(dT('monitor.show_more', 'Afficher plus'))}</button>`);
    } else {
        dSetHtml(el, '');
    }
}

function copyAnalysisText(btn) {
    const card = btn.closest('.analysis-card');
    if (!card) return;
    const line = card.querySelector('.analysis-line').innerText;
    const response = card.querySelector('.analysis-response').innerText;
    const text = `Ligne: ${line}\n\nAnalyse:\n${response}`;

    copyToClipboard(text).then(() => {
        const oldContent = btn.innerHTML;
        btn.innerHTML = '✅';
        setTimeout(() => { btn.innerHTML = oldContent; }, 2000);
    }).catch(err => {
        console.error('Erreur copie:', err);
    });
}

async function retryAnalysis(analysisId, btn) {
    const oldHtml = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `🔄 ${window.t('common.analyzing')}`;
    try {
        const res = await apiFetch(`/api/monitor/retry/${analysisId}`, { method: 'POST' });
        if (!res.task_id) throw new Error(window.t ? window.t('monitor.unexpected_server_response') : 'Unexpected server response');

        pollTask(res.task_id, btn, oldHtml, refresh);
    } catch (e) {
        alert(window.t('common.error') + ': ' + e.message);
        btn.disabled = false;
        btn.innerHTML = oldHtml;
    }
}

/**
 * Polling d'une tâche d'analyse en arrière-plan.
 * Appelle GET /api/monitor/task/{taskId} toutes les 2s jusqu'à completion.
 */
function pollTask(taskId, btn, originalHtml, onDone) {
    const maxAttempts = 150;
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
                alert(window.t('common.error') + ': ' + (res.error || 'Erreur inconnue'));
            }
        } catch (_) {}
    }, 2000);
}

async function notifyAnalysis(analysisId, btn) {
    const oldHtml = btn.innerHTML;
    try {
        btn.innerHTML = '⏳ ' + window.t('common.sending');
        btn.disabled = true;

        const res = await apiFetch(`/api/monitor/notify/${analysisId}`, {
            method: 'POST'
        });

        if (res.status === 'ok') {
            btn.innerHTML = '✅ ' + window.t('common.sent');
            setTimeout(() => {
                btn.innerHTML = oldHtml;
                btn.disabled = false;
            }, 2000);
        } else {
            alert(window.t('common.error') + ": " + res.detail);
            btn.innerHTML = oldHtml;
            btn.disabled = false;
        }
    } catch (e) {
        alert(window.t('common.error') + ": " + e.message);
        btn.innerHTML = oldHtml;
        btn.disabled = false;
    }
}
