// ─── Configuration du diagnostic en lecture seule (proxy Docker, SSH limité) ───

const DIAG = { platform: { docker: 'compose', ssh: 'linux', web: 'compose' }, status: null, editing: { docker: null, ssh: null, web: null }, openRules: new Set() };

/** Saves which rules may use a connection, a moment after the last tick, then redraws with the list still open. */
function scheduleLinkRules(kind, li) {
    const id = Number(li.dataset.link);
    clearTimeout(scheduleLinkRules.timers?.[id]);
    scheduleLinkRules.timers = scheduleLinkRules.timers || {};
    const saved = li.querySelector('.diag-link-rules-saved');
    saved.textContent = '';
    scheduleLinkRules.timers[id] = setTimeout(async () => {
        const ruleIds = [...li.querySelectorAll('input[data-rule]:checked')].map(c => Number(c.dataset.rule));
        const focused = document.activeElement?.dataset?.rule;
        try {
            await apiFetch(`/api/diagnostics/links/${id}/rules`, { method: 'PUT', body: { rule_ids: ruleIds } });
            await refreshDiag();
            const row = document.querySelector(`#diag-${kind}-links .diag-link[data-link="${id}"]`);
            if (focused) row?.querySelector(`input[data-rule="${focused}"]`)?.focus();
            const note = row?.querySelector('.diag-link-rules-saved');
            if (note) { note.textContent = `✓ ${dT('meta.saved', 'Enregistré')}`; note.className = 'form-hint diag-link-rules-saved rf-ok'; }
        } catch (e) {
            saved.textContent = `✗ ${e.message}`;
            saved.className = 'form-hint diag-link-rules-saved rf-ko';
        }
    }, 500);
}

// Valeurs par défaut selon la plateforme : pré-remplies (et non en simple placeholder)
// pour qu'un clic sur « Tester et enregistrer » suffise.
const DIAG_DEFAULTS = {
    docker: { field: 'diag-docker-url', compose: 'http://docker-proxy:2375', unraid: 'http://dockersocket:2375' },
    ssh: { field: 'diag-ssh-user', linux: 'sentinel-diag', unraid: 'root' },
    web: { field: 'diag-web-url', compose: 'http://searxng:8080', unraid: '' },
};

function isDiagDefault(target, value) {
    const { field, ...values } = DIAG_DEFAULTS[target];
    return !value || Object.values(values).includes(value);
}

function selectPlatform(target, platform) {
    DIAG.platform[target] = platform;
    document.querySelectorAll(`.diag-platforms[data-target="${target}"] button`).forEach(b =>
        b.classList.toggle('active', b.dataset.platform === platform));
    const input = document.getElementById(DIAG_DEFAULTS[target].field);
    // Un deuxième proxy n'a pas la même adresse que le premier ; l'utilisateur SSH, lui, dépend seulement de la plateforme
    if ((target === 'ssh' || !diagLinks(target).length) && isDiagDefault(target, input.value.trim())) input.value = DIAG_DEFAULTS[target][platform];
    if (target === 'ssh') {
        document.getElementById('diag-account-row').classList.toggle('hidden', platform !== 'linux');
        renderSnippet();
    } else document.querySelectorAll(`[data-howto^="${target}-"]`).forEach(el =>
        el.classList.toggle('hidden', el.dataset.howto !== `${target}-${platform}`));
}

const DOCKER_COMPOSE_SNIPPET = `  docker-proxy:
    image: tecnativa/docker-socket-proxy:v0.5.0
    container_name: sentinel-docker-proxy
    restart: unless-stopped
    environment:
      CONTAINERS: 1   # liste, état, logs, consommation des conteneurs
      POST: 0         # aucune écriture : ni start, ni stop, ni restart, ni exec
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    # aucune section "ports:" : seul Sentinel le joint, par le réseau du compose`;

function dT(key, fallback) {
    const v = window.t ? window.t(key) : key;
    return (v && v !== key) ? v : fallback;
}

function diagResult(id, ok, text) {
    const el = document.getElementById(id);
    el.textContent = text;
    el.className = 'form-hint ' + (ok === null ? '' : ok ? 'rf-ok' : 'rf-ko');
}

function diagLinks(kind) {
    return (DIAG.status ? DIAG.status.links : []).filter(l => l.kind === kind);
}

function diagStatusBadge(kind) {
    const el = document.getElementById({ docker: 'diag-docker-status', ssh: 'diag-host-status', web: 'diag-web-status' }[kind]);
    const n = diagLinks(kind).length;
    el.textContent = n ? dT(n > 1 ? 'diag.links_many' : 'diag.links_one', '{n} connexion(s)').replace('{n}', n) : dT('diag.status_off', 'non configuré');
    el.className = 'diag-status ' + (n ? 'diag-status--on' : '');
}

function diagLinkTarget(l) {
    if (l.kind === 'web') return l.web_url;
    return l.kind === 'docker' ? l.docker_url : `${l.ssh_user}@${l.ssh_host}${l.ssh_port !== 22 ? ':' + l.ssh_port : ''}`;
}

function diagLinkRules(l) {
    const total = (DIAG.status.rule_count || 0);
    if (!l.rules.length) return dT('diag.link_rules_none', 'Aucune règle');
    if (l.rules.length === total) return dT('diag.link_rules_all', 'Toutes les règles');
    return l.rules.join(', ');
}

function renderLinks(kind) {
    const list = document.getElementById(`diag-${kind}-links`);
    const links = diagLinks(kind);
    list.classList.toggle('hidden', !links.length);
    const rules = DIAG.status.all_rules || [];
    list.innerHTML = links.map(l => `<li class="diag-link" data-link="${l.id}">
        <div class="diag-link-main"><strong>${escapeHtml(l.name)}</strong> <code>${escapeHtml(diagLinkTarget(l))}</code>
            ${rules.length ? `<details class="diag-link-rules" ${DIAG.openRules.has(l.id) ? 'open' : ''}>
                <summary class="form-hint"><span class="diag-link-rules-text">${escapeHtml(diagLinkRules(l))}</span></summary>
                <div class="diag-link-rules-body">
                    <p class="form-hint">${escapeHtml(dT('diag.link_rules_hint', 'L’IA n’utilise cette connexion que pour les alertes des règles cochées.'))}</p>
                    <div class="diag-link-rules-list">${rules.map(r => `<label class="rf-check">
                        <input type="checkbox" data-rule="${r.id}" ${l.rule_ids.includes(r.id) ? 'checked' : ''}>
                        <span>${escapeHtml(r.name)}</span></label>`).join('')}</div>
                    <div class="diag-actions">
                        <button type="button" class="btn btn-secondary btn-sm" data-rules-set="all">${escapeHtml(dT('diag.link_rules_check_all', 'Tout cocher'))}</button>
                        <button type="button" class="btn btn-secondary btn-sm" data-rules-set="none">${escapeHtml(dT('diag.link_rules_check_none', 'Tout décocher'))}</button>
                        <span class="form-hint diag-link-rules-saved" role="status"></span>
                    </div>
                </div>
            </details>` : `<span class="form-hint">${escapeHtml(diagLinkRules(l))}</span>`}
            <span class="form-hint diag-link-result"></span></div>
        <div class="diag-actions">
            <button type="button" class="btn btn-secondary btn-sm" data-link-action="test">${escapeHtml(dT('diag.link_test', 'Tester'))}</button>
            <button type="button" class="btn btn-secondary btn-sm" data-link-action="edit">${escapeHtml(dT('common.edit', 'Modifier'))}</button>
            <button type="button" class="btn btn-secondary btn-sm" data-link-action="delete">${escapeHtml(dT('common.delete', 'Supprimer'))}</button>
        </div></li>`).join('');
    diagStatusBadge(kind);
}

async function refreshDiag() {
    DIAG.status = await apiFetch('/api/diagnostics');
    renderLinks('docker');
    renderLinks('ssh');
    renderLinks('web');
    renderCve();
    renderWebPause();
}

/** Says plainly why the AI is not searching at the moment. */
function renderWebPause() {
    const el = document.getElementById('diag-web-pause');
    const s = DIAG.status.web_search || {};
    let text = '';
    if (s.paused) {
        const engines = s.engines.length ? s.engines.join(', ') : dT('diag.web_no_engine', 'aucun');
        text = dT('diag.web_paused', 'Recherches suspendues encore {n} min : aucun moteur n’a répondu ({engines}). Insister prolongerait le blocage. Tester relance dès qu’un moteur répond.')
            .replace('{n}', s.minutes_left).replace('{engines}', engines);
    } else if (s.hour_full) {
        text = dT('diag.web_full', 'Limite atteinte : {n} recherches cette heure. L’IA reprendra quand l’heure sera écoulée.').replace('{n}', s.hour_limit);
    }
    el.textContent = text;
    el.classList.toggle('hidden', !text);
}

function renderCve() {
    const on = !!DIAG.status.cve_lookup;
    document.getElementById('diag-cve-enabled').checked = on;
    const el = document.getElementById('diag-cve-status');
    el.textContent = on ? dT('diag.cve_on', 'activé') : dT('diag.status_disabled', 'désactivé');
    el.className = 'diag-status ' + (on ? 'diag-status--on' : '');
}

/** What the test of a SearXNG instance found: results, and which engines really answer from here. */
function webTestText(res) {
    if (!res.results) {
        const failed = res.failed.length ? res.failed.join(', ') : dT('diag.web_no_engine', 'aucun');
        return dT('diag.web_blocked', 'L’instance répond, mais aucun moteur ne renvoie de résultat pour l’instant (sans réponse : {failed}). Ils bloquent souvent pour un temps : réessaie plus tard.').replace('{failed}', failed);
    }
    const engines = res.engines.length ? res.engines.join(', ') : dT('diag.web_no_engine', 'aucun');
    let text = dT('diag.web_ok', '{n} résultats · moteurs qui répondent : {engines}').replace('{n}', res.results).replace('{engines}', engines);
    if (res.failed.length) text += ` · ${dT('diag.web_failed', 'sans réponse')} : ${res.failed.join(', ')}`;
    return text;
}

/** Empties the form of a kind, ready for a new connection. The first one starts with the platform's defaults. */
async function resetLinkForm(kind) {
    DIAG.editing[kind] = null;
    document.getElementById(`diag-${kind}-name`).value = '';
    document.getElementById(`diag-${kind}-cancel`).classList.add('hidden');
    document.getElementById(`diag-${kind}-form-title`).textContent = dT('diag.link_new', 'Nouvelle connexion');
    document.getElementById(`diag-${kind}-save`).classList.add('hidden');
    if (kind === 'web') {
        document.getElementById('diag-web-url').value = diagLinks('web').length ? '' : DIAG_DEFAULTS.web[DIAG.platform.web];
        return;
    }
    if (kind === 'docker') {
        document.getElementById('diag-docker-url').value = diagLinks('docker').length ? '' : DIAG_DEFAULTS.docker[DIAG.platform.docker];
        return;
    }
    document.getElementById('diag-ssh-host').value = '';
    document.getElementById('diag-ssh-port').value = 22;
    document.getElementById('diag-ssh-user').value = DIAG_DEFAULTS.ssh[DIAG.platform.ssh];
    document.getElementById('diag-ssh-output').classList.add('hidden');
    if (!diagLinks('ssh').length) {
        try {
            const cfg = await apiFetch('/api/config');
            if (cfg.server_ip && cfg.server_ip !== 'localhost') document.getElementById('diag-ssh-host').value = cfg.server_ip;
        } catch (_) { /* champ laissé vide */ }
    }
}

function editLink(kind, id) {
    const l = diagLinks(kind).find(x => x.id === id);
    if (!l) return;
    DIAG.editing[kind] = id;
    document.getElementById(`diag-${kind}-name`).value = l.name;
    document.getElementById(`diag-${kind}-cancel`).classList.remove('hidden');
    document.getElementById(`diag-${kind}-form-title`).textContent = dT('diag.link_edit', 'Modifier « {name} »').replace('{name}', l.name);
    if (kind === 'web') {
        document.getElementById('diag-web-url').value = l.web_url;
    } else if (kind === 'docker') {
        document.getElementById('diag-docker-url').value = l.docker_url;
    } else {
        document.getElementById('diag-ssh-host').value = l.ssh_host;
        document.getElementById('diag-ssh-port').value = l.ssh_port;
        document.getElementById('diag-ssh-user').value = l.ssh_user;
    }
    document.getElementById(`diag-${kind}-name`).focus();
}

async function testLinkRow(kind, id, li) {
    const l = diagLinks(kind).find(x => x.id === id);
    const out = li.querySelector('.diag-link-result');
    out.textContent = dT('diag.testing', 'Test en cours…');
    out.className = 'form-hint diag-link-result';
    const res = kind === 'web'
        ? await apiFetch('/api/diagnostics/web/test', { method: 'POST', body: { url: l.web_url } })
        : kind === 'docker'
        ? await apiFetch('/api/diagnostics/docker/test', { method: 'POST', body: { url: l.docker_url } })
        : await apiFetch('/api/diagnostics/ssh/test', { method: 'POST', body: { ssh_host: l.ssh_host, ssh_port: l.ssh_port, ssh_user: l.ssh_user } });
    const empty = res.ok && kind === 'web' && !res.results;
    out.textContent = !res.ok ? `✗ ${res.error}` : empty ? `⚠ ${webTestText(res)}` : `✓ ${kind === 'web' ? webTestText(res) : dT('diag.connected', 'Connecté')}`;
    out.classList.add(res.ok && !empty ? 'rf-ok' : 'rf-ko');
    if (kind === 'web') { DIAG.status = await apiFetch('/api/diagnostics'); renderWebPause(); }
}

function diagLinkError(e) {
    const known = {
        link_name_taken: dT('diag.err_name_taken', 'Ce nom est déjà utilisé : l’IA désigne les connexions par leur nom.'),
        link_name_required: dT('diag.err_name_required', 'Donne un nom à cette connexion.'),
        link_url_invalid: dT('diag.err_url', 'L’adresse doit commencer par http:// ou https://.'),
        link_host_required: dT('diag.need_host', 'Renseigne l’adresse du serveur et l’utilisateur.'),
        link_port_invalid: dT('diag.err_port', 'Port invalide.'),
    };
    return known[e.message] || e.message;
}

/** Saves the form's connection, created or edited. Its name defaults to the machine's. */
async function saveLink(kind, values, resultId) {
    const id = DIAG.editing[kind];
    const typed = document.getElementById(`diag-${kind}-name`).value.trim();
    let name = typed || (kind === 'ssh' ? values.ssh_host : '');
    if (!name && kind === 'docker') {
        try { name = new URL(values.docker_url).hostname; } catch (_) { name = 'Docker'; }
    }
    if (!name && kind === 'web') name = 'SearXNG';
    try {
        await apiFetch(id ? `/api/diagnostics/links/${id}` : '/api/diagnostics/links', { method: id ? 'PUT' : 'POST', body: { name, kind, ...values } });
        await refreshDiag();
        return true;
    } catch (e) {
        diagResult(resultId, false, `✗ ${diagLinkError(e)}`);
        return false;
    }
}

async function loadDiagnostics() {
    try {
        await refreshDiag();
        const s = DIAG.status;
        // Plateforme déduite de la première connexion, puis valeurs par défaut
        if (diagLinks('docker').some(l => [DIAG_DEFAULTS.docker.unraid, 'http://sentinel-docker-proxy:2375'].includes(l.docker_url))) DIAG.platform.docker = 'unraid';
        if (diagLinks('ssh').some(l => l.ssh_user === DIAG_DEFAULTS.ssh.unraid)) DIAG.platform.ssh = 'unraid';
        const account = diagLinks('ssh').find(l => l.ssh_user && l.ssh_user !== 'root');
        if (account) setAccount(account.ssh_user);
        document.getElementById('diag-account-row').classList.toggle('hidden', DIAG.platform.ssh !== 'linux');
        await resetLinkForm('docker');
        await resetLinkForm('ssh');
        await resetLinkForm('web');
        selectPlatform('docker', DIAG.platform.docker);
        document.querySelectorAll('.diag-platforms[data-target="ssh"] button').forEach(b =>
            b.classList.toggle('active', b.dataset.platform === DIAG.platform.ssh));
        renderKey(s.public_key);
    } catch (e) {
        diagResult('diag-docker-result', false, e.message);
    }
}

function renderKey(pub) {
    const has = !!pub;
    document.getElementById('diag-pubkey').textContent = pub || '';
    document.getElementById('diag-pubkey').classList.toggle('hidden', !has);
    document.getElementById('diag-key-generate').classList.toggle('hidden', has);
    document.getElementById('diag-key-regenerate').classList.toggle('hidden', !has);
    document.getElementById('diag-ssh-remove').classList.toggle('hidden', !has);
    renderSnippet();
}

async function renderSnippet() {
    const pre = document.getElementById('diag-ssh-snippet');
    if (!DIAG.status || !document.getElementById('diag-pubkey').textContent) {
        pre.textContent = dT('diag.snippet_need_key', '← Génère d’abord la clé.');
        return;
    }
    const query = `platform=${DIAG.platform.ssh}&user=${encodeURIComponent(DIAG_DEFAULTS.ssh.linux)}`;
    try {
        const res = await apiFetch(`/api/diagnostics/ssh/install?${query}`);
        pre.textContent = res.snippet;
        const un = await apiFetch(`/api/diagnostics/ssh/uninstall?${query}`);
        document.getElementById('diag-ssh-uninstall').textContent = un.snippet;
    } catch (e) {
        pre.textContent = e.message;
    }
}

/** The Linux account name: it becomes the platform default of the user field and goes into both blocks. */
function setAccount(name) {
    const user = document.getElementById('diag-ssh-user');
    if (DIAG.platform.ssh === 'linux' && !DIAG.editing.ssh && user.value.trim() === DIAG_DEFAULTS.ssh.linux) user.value = name;
    DIAG_DEFAULTS.ssh.linux = name;
    document.getElementById('diag-ssh-account').value = name;
}

// Teste les valeurs saisies et n'enregistre que si le test réussit (« Enregistrer quand même » est proposé sinon)
async function testAndSaveDocker() {
    const url = document.getElementById('diag-docker-url').value.trim();
    const anyway = document.getElementById('diag-docker-save');
    anyway.classList.add('hidden');
    if (!url) {
        diagResult('diag-docker-result', false, dT('diag.err_url', 'L’adresse doit commencer par http:// ou https://.'));
        return;
    }
    diagResult('diag-docker-result', null, dT('diag.testing', 'Test en cours…'));
    const res = await apiFetch('/api/diagnostics/docker/test', { method: 'POST', body: { url } });
    if (!res.ok) {
        diagResult('diag-docker-result', false, `✗ ${res.error}`);
        anyway.classList.remove('hidden');
        return;
    }
    if (await saveLink('docker', { docker_url: url }, 'diag-docker-result')) {
        await resetLinkForm('docker');
        diagResult('diag-docker-result', true,
            `✓ Docker ${res.server_version || ''} · ${res.running}/${res.containers} ${dT('diag.containers_running', 'conteneurs actifs')} · ${dT('diag.saved_ok', 'enregistré')}`);
    }
}

async function testAndSaveWeb() {
    const url = document.getElementById('diag-web-url').value.trim();
    const anyway = document.getElementById('diag-web-save');
    anyway.classList.add('hidden');
    if (!url) {
        diagResult('diag-web-result', false, dT('diag.err_url', 'L’adresse doit commencer par http:// ou https://.'));
        return;
    }
    diagResult('diag-web-result', null, dT('diag.testing', 'Test en cours…'));
    const res = await apiFetch('/api/diagnostics/web/test', { method: 'POST', body: { url } });
    if (!res.ok) {
        diagResult('diag-web-result', false, `✗ ${res.error}`);
        anyway.classList.remove('hidden');
        return;
    }
    if (await saveLink('web', { web_url: url }, 'diag-web-result')) {
        await resetLinkForm('web');
        diagResult('diag-web-result', res.results > 0, `${res.results ? '✓' : '⚠'} ${webTestText(res)}`);
    }
}

function sshFields() {
    return {
        ssh_host: document.getElementById('diag-ssh-host').value.trim(),
        ssh_port: parseInt(document.getElementById('diag-ssh-port').value) || 22,
        ssh_user: document.getElementById('diag-ssh-user').value.trim(),
    };
}

async function testAndSaveSsh() {
    const body = sshFields();
    const out = document.getElementById('diag-ssh-output');
    const anyway = document.getElementById('diag-ssh-save');
    anyway.classList.add('hidden');
    out.classList.add('hidden');
    if (!body.ssh_host || !body.ssh_user) {
        diagResult('diag-ssh-result', false, dT('diag.need_host', 'Renseigne l’adresse du serveur et l’utilisateur.'));
        return;
    }
    diagResult('diag-ssh-result', null, dT('diag.testing', 'Test en cours…'));
    const res = await apiFetch('/api/diagnostics/ssh/test', { method: 'POST', body });
    if (!res.ok) {
        diagResult('diag-ssh-result', false, `✗ ${res.error}`);
        anyway.classList.remove('hidden');
        return;
    }
    if (!await saveLink('ssh', body, 'diag-ssh-result')) return;
    await resetLinkForm('ssh');
    if (res.script_outdated) {
        diagResult('diag-ssh-result', false, dT('diag.script_outdated', '⚠ Connecté, mais le script installé sur le serveur est ancien : recolle le bloc de l’étape 2.'));
    } else {
        diagResult('diag-ssh-result', true, `✓ ${dT('diag.connected', 'Connecté')}${(res.host_fingerprint || '').match(/SHA256:\S+/) ? ' · ' + res.host_fingerprint.match(/SHA256:\S+/)[0] : ''} · ${dT('diag.saved_ok', 'enregistré')}`);
    }
    out.textContent = res.output;
    out.classList.remove('hidden');
}

document.addEventListener('DOMContentLoaded', () => {
    if (!document.getElementById('diagnostics-section')) return;
    document.getElementById('diag-docker-compose-code').textContent = DOCKER_COMPOSE_SNIPPET;

    document.getElementById('diag-docker-test').addEventListener('click', testAndSaveDocker);
    document.getElementById('diag-docker-save').addEventListener('click', async () => {
        if (await saveLink('docker', { docker_url: document.getElementById('diag-docker-url').value.trim() }, 'diag-docker-result')) {
            await resetLinkForm('docker');
            diagResult('diag-docker-result', null, dT('diag.saved_untested', 'Enregistré sans test réussi.'));
        }
    });
    document.getElementById('diag-web-test').addEventListener('click', testAndSaveWeb);
    document.getElementById('diag-web-save').addEventListener('click', async () => {
        if (await saveLink('web', { web_url: document.getElementById('diag-web-url').value.trim() }, 'diag-web-result')) {
            await resetLinkForm('web');
            diagResult('diag-web-result', null, dT('diag.saved_untested', 'Enregistré sans test réussi.'));
        }
    });
    document.getElementById('diag-cve-enabled').addEventListener('change', async (e) => {
        const res = await apiFetch('/api/diagnostics/cve', { method: 'POST', body: { enabled: e.target.checked } });
        DIAG.status.cve_lookup = res.enabled;
        renderCve();
    });
    document.getElementById('diag-cve-test').addEventListener('click', async () => {
        diagResult('diag-cve-result', null, dT('diag.testing', 'Test en cours…'));
        const res = await apiFetch('/api/diagnostics/cve/test', { method: 'POST' });
        diagResult('diag-cve-result', res.ok, res.ok ? `✓ ${dT('diag.cve_ok', 'NVD répond')}` : `✗ ${res.error}`);
    });
    ['docker', 'ssh', 'web'].forEach(kind => {
        document.getElementById(`diag-${kind}-cancel`).addEventListener('click', () => {
            resetLinkForm(kind);
            diagResult(`diag-${kind}-result`, null, '');
        });
        const linkList = document.getElementById(`diag-${kind}-links`);
        linkList.addEventListener('toggle', (e) => {
            if (!e.target.matches('.diag-link-rules')) return;
            const id = Number(e.target.closest('.diag-link').dataset.link);
            if (e.target.open) DIAG.openRules.add(id); else DIAG.openRules.delete(id);
        }, true);
        linkList.addEventListener('change', (e) => {
            if (e.target.matches('input[data-rule]')) scheduleLinkRules(kind, e.target.closest('.diag-link'));
        });
        linkList.addEventListener('click', (e) => {
            const set = e.target.closest('button[data-rules-set]');
            if (!set) return;
            const li = set.closest('.diag-link');
            li.querySelectorAll('input[data-rule]').forEach(c => { c.checked = set.dataset.rulesSet === 'all'; });
            scheduleLinkRules(kind, li);
        });
        linkList.addEventListener('click', (e) => {
            const btn = e.target.closest('button[data-link-action]');
            if (!btn) return;
            const li = btn.closest('.diag-link');
            const id = Number(li.dataset.link);
            if (btn.dataset.linkAction === 'edit') editLink(kind, id);
            else if (btn.dataset.linkAction === 'test') testLinkRow(kind, id, li);
            else showInlineConfirm(btn, dT('diag.delete_confirm', 'Supprimer cette connexion ? L’IA n’y aura plus accès.'), async () => {
                await apiFetch(`/api/diagnostics/links/${id}`, { method: 'DELETE' });
                await refreshDiag();
                if (DIAG.editing[kind] === id || !diagLinks(kind).length) await resetLinkForm(kind);
            });
        });
    });

    const genKey = async (force) => {
        try {
            const res = await apiFetch(`/api/diagnostics/ssh/key${force ? '?force=true' : ''}`, { method: 'POST' });
            renderKey(res.public_key);
            loadDiagnostics();
        } catch (e) {
            diagResult('diag-ssh-result', false, e.message);
        }
    };
    document.getElementById('diag-key-generate').addEventListener('click', () => genKey(false));
    document.getElementById('diag-key-regenerate').addEventListener('click', (e) => {
        showInlineConfirm(e.currentTarget, dT('diag.regenerate_confirm', 'L’ancienne clé ne fonctionnera plus : il faudra recoller le bloc sur le serveur. Continuer ?'), () => genKey(true));
    });

    const importBox = document.getElementById('diag-key-import');
    const pasteBtn = document.getElementById('diag-key-paste');
    const showImport = (open) => {
        importBox.classList.toggle('hidden', !open);
        pasteBtn.setAttribute('aria-expanded', String(open));
        document.getElementById('diag-key-result').textContent = '';
        if (open) document.getElementById('diag-key-private').focus();
        else document.getElementById('diag-key-private').value = '';
    };
    pasteBtn.addEventListener('click', () => showImport(importBox.classList.contains('hidden')));
    document.getElementById('diag-key-paste-cancel').addEventListener('click', () => showImport(false));
    const importKey = async () => {
        const result = document.getElementById('diag-key-result');
        try {
            const res = await apiFetch('/api/diagnostics/ssh/key/import', { method: 'POST', body: { private_key: document.getElementById('diag-key-private').value } });
            showImport(false);
            renderKey(res.public_key);
            loadDiagnostics();
            diagResult('diag-ssh-result', true, `✓ ${dT('diag.key_imported', 'Clé enregistrée. Recolle le bloc sur le serveur si elle n’y est pas déjà autorisée.')}`);
        } catch (e) {
            result.textContent = `✗ ${e.message}`;
            result.className = 'form-hint rf-ko';
        }
    };
    document.getElementById('diag-key-use').addEventListener('click', (e) => {
        if (!document.getElementById('diag-pubkey').textContent) return importKey();
        showInlineConfirm(e.currentTarget, dT('diag.regenerate_confirm', 'L’ancienne clé ne fonctionnera plus : il faudra recoller le bloc sur le serveur. Continuer ?'), importKey);
    });
    let accountTimer = null;
    document.getElementById('diag-ssh-account').addEventListener('input', (e) => {
        clearTimeout(accountTimer);
        accountTimer = setTimeout(() => {
            const name = e.target.value.trim();
            if (!name) return;
            setAccount(name);
            renderSnippet();
        }, 300);
    });

    document.getElementById('diag-ssh-test').addEventListener('click', testAndSaveSsh);
    document.getElementById('diag-ssh-save').addEventListener('click', async () => {
        if (await saveLink('ssh', sshFields(), 'diag-ssh-result')) {
            await resetLinkForm('ssh');
            diagResult('diag-ssh-result', null, dT('diag.saved_untested', 'Enregistré sans test réussi.'));
        }
    });

    document.querySelectorAll('.diag-platforms').forEach(group => {
        group.addEventListener('click', (e) => {
            const btn = e.target.closest('button[data-platform]');
            if (!btn) return;
            selectPlatform(group.dataset.target, btn.dataset.platform);
        });
    });
    document.querySelectorAll('.diag-copy').forEach(btn => btn.addEventListener('click', () => {
        copyToClipboard(document.getElementById(btn.dataset.copy).textContent).then(() => {
            const old = btn.textContent;
            btn.textContent = '✓';
            setTimeout(() => { btn.textContent = old; }, 1500);
        });
    }));

    loadDiagnostics();
});
