// ─── Rules overview ─────────────────────────────────────────────────────────
// Every rule (disabled ones included) with its state, alerts over 7 days, last
// incident, MTTR and source liveness. A row opens the rule page.
// ─────────────────────────────────────────────────────────────────────────────

const RO = { data: null, filter: 'all', search: '' };

function roT(key, fallback, vars) {
    let s = window.t ? window.t(key) : key;
    if (!s || s === key) s = fallback;
    if (vars) Object.entries(vars).forEach(([k, v]) => { s = s.split(`{${k}}`).join(v); });
    return s;
}

function roMinutes(m) {
    if (m == null) return '—';
    if (m < 1) return roT('rule.less_than_minute', '< 1 min');
    if (m < 60) return `${Math.round(m)} min`;
    if (m < 1440) { const h = Math.floor(m / 60), mm = Math.round(m % 60); return mm ? `${h} h ${String(mm).padStart(2, '0')}` : `${h} h`; }
    return `${Math.floor(m / 1440)} ${roT('rule.day_short', 'j')}`;
}

function roWhen(iso) {
    if (!iso) return '—';
    const d = new Date(iso), now = new Date();
    const hm = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    const y = new Date(now); y.setDate(now.getDate() - 1);
    if (d.toDateString() === now.toDateString()) return hm;
    if (d.toDateString() === y.toDateString()) return `${roT('rule.yesterday', 'hier')} ${hm}`;
    return `${d.toLocaleDateString([], { day: '2-digit', month: '2-digit' })} ${hm}`;
}

document.addEventListener('DOMContentLoaded', () => {
    loadOverview();
    setInterval(loadOverview, 20000);
    document.getElementById('ro-search').addEventListener('input', e => { RO.search = e.target.value.trim().toLowerCase(); render(); });
    window.i18n?.onLanguageChange(render);
});

async function loadOverview() {
    try {
        RO.data = await apiFetch('/api/rules/overview');
        render();
    } catch (e) {
        document.getElementById('ro-table').innerHTML = `<div class="loading">${escapeHtml(roT('common.error', 'Erreur'))} : ${escapeHtml(e.message)}</div>`;
    }
    unskel('ro-kpis');
}

function ruleState(r) {
    if (!r.enabled) {
        return r.alert_status !== 'normal'
            ? { label: roT('rule.status_disabled', 'Désactivée'), sub: roT('rules.open_incident', 'incident non clos'), dot: 'rp-dot--warn', key: 'disabled' }
            : { label: roT('rule.status_disabled', 'Désactivée'), sub: roT('rules.monitoring_stopped', 'surveillance arrêtée'), dot: 'rp-dot--idle', key: 'disabled' };
    }
    if (r.alert_status !== 'normal') {
        const m = r.alert_started_at ? (Date.now() - new Date(r.alert_started_at).getTime()) / 60000 : null;
        return { label: roT('rule.status_alert', 'En alerte'), sub: m != null ? roT('rules.since', 'depuis {d}', { d: roMinutes(m) }) : '', dot: 'rp-dot--alert', key: 'alert' };
    }
    return { label: roT('rule.status_normal', 'Normal'), sub: r.muted ? roT('rules.source_muted', 'source muette') : roT('rules.no_incident', 'aucun incident'), dot: 'rp-dot--ok', key: 'normal' };
}

function sourceText(src) {
    if (src.type === 'webhook') return roT('rule.source_webhook', 'Webhook');
    if (src.type === 'syslog') return `${roT('rule.source_syslog', 'Syslog')} · ${src.label}`;
    const connected = { docker: 'Docker', homeassistant: 'Home Assistant', mqtt: 'MQTT' }[src.type];
    if (connected) return src.label ? `${connected} · ${src.label}` : connected;
    return `${roT('rule.source_file', 'Fichier')} · ${src.label}`;
}

// Who keeps the rule's settings up to date: the autopilot, or the user.
function modeHtml(r) {
    const ap = r.autopilot;
    if (!ap?.enabled) return `<span class="ro-mode">${escapeHtml(roT('rules.mode_manual', 'Manuel'))}</span>`;
    const state = ap.status === 'error' ? roT('rules.mode_auto_error', 'en échec')
        : ['learning', 'observing'].includes(ap.status) ? roT('rules.mode_auto_setup', 'en préparation') : '';
    return `<span class="ro-mode-row"><span class="ro-mode ro-mode--auto ${ap.status === 'error' ? 'ro-mode--error' : ''}"><svg viewBox="0 0 16 16" width="11" height="11" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2.5 10.5 13.5 3l-3 11-2.5-4.5z"/></svg>${escapeHtml(roT('rules.mode_auto', 'Pilote automatique'))}</span>${state ? `<span class="rp-muted ro-mode-state">${escapeHtml(state)}</span>` : ''}</span>`;
}

function render() {
    if (!RO.data) return;
    const { rules, kpis } = RO.data;
    const states = rules.map(r => ({ r, s: ruleState(r) }));

    document.getElementById('ro-subtitle').textContent = roT('rules.subtitle', '{n} règles · {a} active(s)', {
        n: rules.length, a: rules.filter(r => r.enabled).length });
    document.getElementById('ro-kpis').innerHTML = `
        <div class="ro-kpi ${kpis.alert_count ? 'ro-kpi--alert' : ''}"><span class="rp-label">${escapeHtml(roT('rules.kpi_alert', 'En alerte'))}</span><strong>${kpis.alert_count}</strong></div>
        <div class="ro-kpi"><span class="rp-label">${escapeHtml(roT('rules.kpi_alerts7', 'Alertes · 7 jours'))}</span><strong>${kpis.total7}</strong></div>
        <div class="ro-kpi"><span class="rp-label">${escapeHtml(roT('rules.kpi_mttr', 'MTTR médian · 30 jours'))}</span><strong>${escapeHtml(roMinutes(kpis.mttr30_minutes))}</strong></div>
        <div class="ro-kpi ${kpis.muted_count ? 'ro-kpi--warn' : ''}"><span class="rp-label">${escapeHtml(roT('rules.kpi_muted', 'Sources muettes'))}</span><strong>${kpis.muted_count}</strong></div>`;

    const counts = { all: rules.length, alert: states.filter(x => x.s.key === 'alert').length,
        active: rules.filter(r => r.enabled).length, disabled: rules.filter(r => !r.enabled).length };
    document.getElementById('ro-filters').innerHTML = [['all', roT('rules.filter_all', 'Toutes')], ['alert', roT('rule.status_alert', 'En alerte')],
        ['active', roT('rules.filter_active', 'Actives')], ['disabled', roT('rules.filter_disabled', 'Désactivées')]]
        .map(([id, label]) => `<button type="button" class="${RO.filter === id ? 'active' : ''}" aria-pressed="${RO.filter === id}" onclick="RO.filter='${id}'; render()">${escapeHtml(label)} (${counts[id]})</button>`).join('');

    const visible = states.filter(({ r, s }) => {
        if (RO.filter === 'alert' && s.key !== 'alert') return false;
        if (RO.filter === 'active' && !r.enabled) return false;
        if (RO.filter === 'disabled' && r.enabled) return false;
        if (RO.search && !`${r.name} ${r.source.label} ${r.source.type}`.toLowerCase().includes(RO.search)) return false;
        return true;
    });

    const head = `<div class="ro-row ro-row--head" role="row">
        <span>${escapeHtml(roT('rules.col_state', 'État'))}</span><span>${escapeHtml(roT('rules.col_rule', 'Règle'))}</span>
        <span>${escapeHtml(roT('rules.col_alerts', 'Alertes 7 j'))}</span><span>${escapeHtml(roT('rules.col_incident', 'Dernier incident'))}</span>
        <span>${escapeHtml(roT('rules.col_mttr', 'MTTR'))}</span><span>${escapeHtml(roT('rules.col_source', 'Source'))}</span>
        <span>${escapeHtml(roT('rules.col_enabled', 'Activée'))}</span></div>`;
    if (!rules.length) {
        document.getElementById('ro-table').innerHTML = `<div class="ro-empty">${escapeHtml(roT('rules.no_rules', 'Aucune règle configurée.'))} <a href="/rules/new">${escapeHtml(roT('rules.create_first', 'Créer une règle'))}</a></div>`;
        return;
    }
    document.getElementById('ro-table').innerHTML = head + (visible.map(({ r, s }) => rowHtml(r, s)).join('')
        || `<div class="ro-empty">${escapeHtml(roT('rules.no_match', 'Aucune règle ne correspond.'))}</div>`);
}

function rowHtml(r, s) {
    const max = Math.max(1, ...r.days7.map(d => d.critical + d.warning + d.info));
    const bars = r.days7.map(d => {
        const total = d.critical + d.warning + d.info;
        const color = d.critical ? 'critical' : d.warning ? 'warning' : total ? 'info' : 'none';
        return `<span class="ro-bar ro-bar--${color}" style="height:${total ? Math.max(4, Math.round(total / max * 26)) : 2}px"></span>`;
    }).join('');
    const last = r.last;
    const lastWhen = last ? `${roWhen(last.analyzed_at)} · ${last.severity} · ${last.status === 'open'
        ? roT('rules.open', 'ouvert') : last.resolution_minutes == null ? roT('rules.resolved', 'résolu')
        : roT('rule.resolved_after', 'résolu en {d}', { d: roMinutes(last.resolution_minutes) })}` : '—';
    const ago = r.last_line_received_at
        ? roT('rules.last_line_ago', 'dernière ligne {t}', { t: roT('rule.ago', 'il y a {d}', { d: roMinutes((Date.now() - new Date(r.last_line_received_at).getTime()) / 60000) }) })
        : roT('rules.never_received', 'aucune ligne reçue');
    const live = !r.enabled ? ['—', roT('rules.not_monitored', 'non surveillée'), '']
        : r.source_status?.state === 'error' ? [roT('sources.state_error', 'Connexion en échec'), scErrorText(r.source_status.code, r.source_status.detail), 'rp-ko']
        : r.source_status?.state === 'waiting' ? [roT('sources.state_waiting', 'En attente'), scErrorText(r.source_status.code, r.source_status.detail), '']
        : r.muted ? [roT('rules.muted', 'Muette'), ago, 'rp-ko'] : [roT('rules.live', 'Active'), ago, 'rp-ok'];
    return `
    <div class="ro-row ${r.enabled ? '' : 'ro-row--off'}" role="row">
        <a class="ro-link" href="/rules/${r.id}" aria-label="${escapeHtml(r.name)}"></a>
        <span class="ro-state"><span class="ro-state-label"><span class="rp-dot ${s.dot}" aria-hidden="true"></span>${escapeHtml(s.label)}</span><span class="rp-muted">${escapeHtml(s.sub)}</span></span>
        <span class="ro-name"><strong>${escapeHtml(r.name)}</strong><span class="mono rp-muted">${escapeHtml(sourceText(r.source))}</span>${modeHtml(r)}</span>
        <span class="ro-alerts"><span class="ro-bars" aria-hidden="true">${bars}</span><strong>${r.total7}</strong></span>
        <span class="ro-incident"><span>${escapeHtml(last ? last.summary : roT('rules.no_alert', 'Aucune alerte.'))}</span><span class="rp-muted">${escapeHtml(lastWhen)}</span></span>
        <span>${escapeHtml(roMinutes(r.mttr30_minutes))}</span>
        <span class="ro-live"><span class="${live[2]}">${escapeHtml(live[0])}</span><span class="rp-muted">${escapeHtml(live[1])}</span></span>
        <span class="ro-toggle">
            <label class="toggle-switch" title="${escapeHtml(r.enabled ? roT('rules.disable', 'Désactiver') : roT('rules.enable', 'Réactiver'))}">
                <input type="checkbox" ${r.enabled ? 'checked' : ''} onchange="setEnabled(${r.id}, this)" aria-label="${escapeHtml((r.enabled ? roT('rules.disable', 'Désactiver') : roT('rules.enable', 'Réactiver')) + ' ' + r.name)}">
            </label>
        </span>
    </div>`;
}

async function setEnabled(id, input) {
    const enabled = input.checked;
    input.disabled = true;
    try {
        await apiFetch(`/api/rules/${id}`, { method: 'PUT', body: { enabled } });
        await loadOverview();
    } catch (e) {
        input.checked = !enabled;
        input.disabled = false;
        alert(roT('common.error', 'Erreur') + ': ' + e.message);
    }
}
