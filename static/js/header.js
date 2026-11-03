/* En-tête, présent sur toutes les pages : interrupteur de surveillance, version et nouveautés, statistiques, thème. */

const HEADER = { monitoring: undefined, update: null, changelog: null, serverSkew: null, serverOffset: 0 };
const SEEN_VERSION_KEY = 'sentinel_seen_version';

const HEADER_ICONS = {
    check: '<svg viewBox="0 0 16 16" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 8.5 6.5 12 13 4.5"/></svg>',
    sun: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
    moon: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20.5 14.5A8.5 8.5 0 0 1 9.5 3.5a8.5 8.5 0 1 0 11 11z"/></svg>',
};

function hT(key, fallback, vars) {
    let text = window.t ? window.t(key) : null;
    if (!text || text === key) text = fallback;
    Object.entries(vars || {}).forEach(([k, v]) => { text = text.split(`{${k}}`).join(v); });
    return text;
}

function headerLang() {
    return document.documentElement.lang || undefined;
}

/* ── Statistiques et heure du serveur ────────────────────────────────────── */

function formatUptime(seconds) {
    if (seconds < 60) return `${seconds}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    if (h >= 48) return `${Math.floor(h / 24)}j ${h % 24}h`;
    return `${h}h${String(m).padStart(2, '0')}`;
}

async function updateSystemStats() {
    try {
        const stats = await apiFetch('/api/dashboard/system-stats');
        const set = (id, text) => {
            const el = document.getElementById(id);
            if (el) el.textContent = text;
        };
        set('stat-cpu-app', `${Math.round(stats.app_cpu)}%`);
        set('stat-cpu-sys', `${Math.round(stats.sys_cpu)}%`);
        set('stat-ram', `${Math.round(stats.app_ram)} MB`);
        set('stat-uptime', formatUptime(stats.uptime));
        if (stats.server_time) {
            const match = stats.server_time.match(/([+-])(\d{2}):(\d{2})$/);
            HEADER.serverOffset = match ? (match[1] === '-' ? -1 : 1) * (Number(match[2]) * 60 + Number(match[3])) : 0;
            HEADER.serverSkew = Date.parse(stats.server_time) - Date.now();
            renderServerTime();
        }
        applyMonitoring(stats.monitoring || null);
    } catch (e) {
        // Sentinel ne répond pas : on ne prétend pas connaître l'état de la surveillance
        applyMonitoring(null);
    }
}

function renderServerTime() {
    const el = document.getElementById('server-time-display');
    if (!el || HEADER.serverSkew === null) return;
    // L'heure affichée est celle du serveur, celle des lignes de log, quel que soit le fuseau du navigateur
    const shifted = new Date(Date.now() + HEADER.serverSkew + HEADER.serverOffset * 60000);
    el.textContent = shifted.toLocaleTimeString(headerLang(), { hour: '2-digit', minute: '2-digit', second: '2-digit', timeZone: 'UTC' });
    const date = shifted.toLocaleDateString(headerLang(), { day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'UTC' });
    el.closest('.stat-item')?.setAttribute('title', `${hT('nav.tooltip_time', 'Heure du serveur')} · ${date}`);
}

/* ── Interrupteur de surveillance ────────────────────────────────────────── */

function applyMonitoring(state) {
    const previous = HEADER.monitoring;
    HEADER.monitoring = state;
    const btn = document.getElementById('monitoring-switch');
    if (!btn) return;
    const label = btn.querySelector('.nav-switch-label');
    btn.classList.remove('is-on', 'is-paused', 'is-unknown');
    btn.disabled = false;
    if (!state) {
        btn.classList.add('is-unknown');
        btn.setAttribute('aria-checked', 'mixed');
        btn.disabled = true;
        label.textContent = hT('header.monitoring_unknown', 'État inconnu');
        btn.title = hT('header.monitoring_unknown_title', 'Sentinel ne répond pas : état de la surveillance inconnu.');
    } else if (state.paused) {
        btn.classList.add('is-paused');
        btn.setAttribute('aria-checked', 'false');
        label.textContent = hT('header.monitoring_paused', 'En pause');
        const since = state.since ? new Date(state.since).toLocaleString(headerLang(), { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '';
        btn.title = hT('header.monitoring_paused_title', 'Surveillance en pause depuis le {since}. Clique pour la reprendre.', { since });
    } else {
        btn.classList.add('is-on');
        btn.setAttribute('aria-checked', 'true');
        label.textContent = hT('header.monitoring', 'Surveillance');
        btn.title = hT('header.monitoring_on_title', 'Surveillance active. Clique pour la mettre en pause.');
    }
    if (previous !== undefined && Boolean(previous?.paused) !== Boolean(state?.paused)) {
        document.dispatchEvent(new CustomEvent('monitoring-changed', { detail: state }));
    }
}

async function setMonitoring(paused) {
    const btn = document.getElementById('monitoring-switch');
    btn.disabled = true;
    try {
        applyMonitoring(await apiFetch('/api/dashboard/monitoring', { method: 'PUT', body: { paused } }));
    } catch (e) {
        btn.disabled = false;
        btn.title = `${hT('common.error', 'Erreur')} : ${e.message}`;
    }
}

function toggleMonitoring(btn) {
    if (!HEADER.monitoring) return;
    if (HEADER.monitoring.paused) {
        setMonitoring(false);
        return;
    }
    // Couper la surveillance se fait en un clic de trop : on confirme, et on dit ce que ça implique
    showInlineConfirm(btn, hT('header.monitoring_confirm', 'Mettre la surveillance en pause ? Plus aucune analyse ni notification automatique. Les lignes reçues pendant la pause ne seront pas analysées à la reprise.'), () => setMonitoring(true));
}

/* ── Version et mise à jour ──────────────────────────────────────────────── */

function renderVersionBadge() {
    const status = document.getElementById('version-status');
    if (!status) return;
    const update = HEADER.update;
    const btn = document.getElementById('version-btn');
    btn.classList.remove('has-update');
    if (update && update.is_available) {
        btn.classList.add('has-update');
        status.textContent = hT('header.update_to', '{v} disponible', { v: update.latest_version || '' }).trim();
    } else if (update && update.checked && !update.error) {
        status.innerHTML = `${HEADER_ICONS.check}<span>${escapeHtml(hT('header.up_to_date', 'À jour'))}</span>`;
    } else {
        status.textContent = '';
    }
    let seen = null;
    try {
        seen = localStorage.getItem(SEEN_VERSION_KEY);
        if (seen === null) localStorage.setItem(SEEN_VERSION_KEY, btn.dataset.version);  // Première visite : rien à signaler
    } catch (_) { /* stockage indisponible : pas de pastille */ }
    btn.classList.toggle('has-news', seen !== null && seen !== btn.dataset.version);
    btn.title = hT('header.version_title', 'Voir les nouveautés');
}

async function restoreUpdateStatus() {
    try {
        HEADER.update = await apiFetch('/api/system/update-status');
    } catch (_) {
        HEADER.update = null;
    }
    renderVersionBadge();
}

/* ── Nouveautés ──────────────────────────────────────────────────────────── */

function updateBoxHtml() {
    const u = HEADER.update;
    if (!u || !u.is_available) return '';
    const link = u.release_url ? `<a href="${escapeHtml(u.release_url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(hT('header.changelog_github', 'Voir sur GitHub'))}</a>` : '';
    return `<section class="cl-update">
        <h3>${escapeHtml(hT('header.changelog_update_title', 'Version {v} disponible', { v: u.latest_version || '' }))}</h3>
        <p class="form-hint">${escapeHtml(hT('header.update_instructions', 'Pour mettre à jour : git pull && docker compose up -d --build'))} ${link}</p>
        ${u.release_notes ? `<details class="cl-version"><summary>${escapeHtml(hT('header.changelog_update_notes', 'Ce qu’elle apporte'))}</summary><div class="markdown-body">${renderMarkdown(u.release_notes)}</div></details>` : ''}
    </section>`;
}

function changelogHtml() {
    const data = HEADER.changelog;
    if (!data || !data.sections.length) {
        return `<p class="form-hint">${escapeHtml(hT('header.changelog_empty', 'Journal des versions indisponible.'))}</p>`;
    }
    let opened = false;
    return data.sections.map(section => {
        const unreleased = section.version === 'Unreleased';
        const installed = section.version === data.current;
        const open = !opened && installed;
        if (installed) opened = true;
        const name = unreleased ? hT('header.changelog_next', 'Prochaine version') : `v${section.version}`;
        return `<details class="cl-version"${open || unreleased ? ' open' : ''}>
            <summary><strong>${escapeHtml(name)}</strong>
                ${section.date ? `<span class="rp-muted">${escapeHtml(section.date)}</span>` : ''}
                ${installed ? `<span class="meta-kind meta-kind--new">${escapeHtml(hT('header.changelog_installed', 'Installée'))}</span>` : ''}</summary>
            <div class="markdown-body">${renderMarkdown(section.body)}</div>
        </details>`;
    }).join('');
}

function renderChangelog() {
    document.getElementById('changelog-update').innerHTML = updateBoxHtml();
    document.getElementById('changelog-list').innerHTML = changelogHtml();
}

async function openChangelog() {
    const dialog = document.getElementById('changelog-dialog');
    const btn = document.getElementById('version-btn');
    try {
        localStorage.setItem(SEEN_VERSION_KEY, btn.dataset.version);
    } catch (_) { /* stockage indisponible */ }
    btn.classList.remove('has-news');
    dialog.showModal();
    document.getElementById('changelog-check-result').textContent = '';
    if (!HEADER.changelog) {
        document.getElementById('changelog-list').innerHTML = `<div class="loading">${escapeHtml(hT('common.loading', 'Chargement…'))}</div>`;
        try {
            HEADER.changelog = await apiFetch('/api/system/changelog');
        } catch (_) {
            HEADER.changelog = null;
        }
    }
    renderChangelog();
}

async function checkAppUpdate() {
    const btn = document.getElementById('changelog-check');
    const result = document.getElementById('changelog-check-result');
    btn.disabled = true;
    result.className = 'form-hint cfg-result';
    result.textContent = hT('header.checking', 'Vérification…');
    try {
        HEADER.update = await apiFetch('/api/system/update-check');
    } catch (_) {
        HEADER.update = { error: true, checked: true };
    }
    btn.disabled = false;
    if (HEADER.update.error) {
        result.classList.add('rf-ko');
        result.textContent = hT('header.check_failed', 'Vérification impossible : GitHub est-il joignable ?');
    } else if (HEADER.update.is_available) {
        result.textContent = '';
    } else {
        result.classList.add('rf-ok');
        result.textContent = hT('header.check_latest', 'Tu as la dernière version.');
    }
    renderVersionBadge();
    renderChangelog();
}

/* ── Thème ───────────────────────────────────────────────────────────────── */

function applyThemeIcon(theme) {
    const btn = document.getElementById('theme-toggle-btn');
    if (!btn) return;
    const key = theme === 'light' ? 'header.theme_dark' : 'header.theme_light';
    btn.innerHTML = theme === 'light' ? HEADER_ICONS.moon : HEADER_ICONS.sun;
    btn.setAttribute('data-i18n-title', key);
    const label = hT(key, theme === 'light' ? 'Passer au thème sombre' : 'Passer au thème clair');
    btn.setAttribute('title', label);
    btn.setAttribute('aria-label', label);
}

function toggleTheme() {
    const current = document.documentElement.getAttribute('data-theme') || 'dark';
    const next = current === 'light' ? 'dark' : 'light';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('theme', next);
    applyThemeIcon(next);
}

/* ── Démarrage ───────────────────────────────────────────────────────────── */

document.addEventListener('DOMContentLoaded', () => {
    applyThemeIcon(localStorage.getItem('theme') || 'dark');
    updateSystemStats();
    setInterval(() => { if (!document.hidden) updateSystemStats(); }, 10000);
    setInterval(renderServerTime, 1000);
    restoreUpdateStatus();
    document.getElementById('monitoring-switch').addEventListener('click', e => toggleMonitoring(e.currentTarget));
    document.getElementById('version-btn').addEventListener('click', openChangelog);
    document.getElementById('changelog-check').addEventListener('click', checkAppUpdate);
    const dialog = document.getElementById('changelog-dialog');
    document.getElementById('changelog-close').addEventListener('click', () => dialog.close());
    // Un clic sur le fond referme : le contenu occupe toute la fenêtre, seul le fond a le dialogue pour cible
    dialog.addEventListener('click', e => { if (e.target === dialog) dialog.close(); });
    window.i18n?.onLanguageChange(() => {
        applyMonitoring(HEADER.monitoring === undefined ? null : HEADER.monitoring);
        renderVersionBadge();
        applyThemeIcon(document.documentElement.getAttribute('data-theme') || 'dark');
        if (dialog.open) renderChangelog();
    });
});
