// ─── Configuration du diagnostic en lecture seule (proxy Docker, SSH limité, recherche web) ───
//
// Chaque carte : la liste des connexions, un bouton « Ajouter » qui ouvre le formulaire d'ajout, et sur chaque
// connexion ses actions (Tester, Modifier, Supprimer ; pour un serveur SSH aussi son bloc d'installation et son bloc
// de retrait). La clé de Sentinel est unique, commune à tous les serveurs.

const DIAG = {
    platform: { docker: 'compose', ssh: 'linux', web: 'compose' },
    status: null,
    adding: { docker: false, ssh: false, web: false },
    openRules: new Set(),
    // État d'affichage de chaque connexion, gardé quand la liste est redessinée : panneau ouvert, dernier résultat
    rows: {},
};

function rowState(id) {
    DIAG.rows[id] = DIAG.rows[id] || { panel: null, result: null, output: '', edit: null };
    return DIAG.rows[id];
}

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

// Champs d'une connexion, pour le formulaire « Modifier » de chaque ligne
const LINK_FIELDS = {
    docker: [{ key: 'docker_url', label: ['diag.docker_url', 'Adresse du proxy'] }],
    web: [{ key: 'web_url', label: ['diag.web_url', 'Adresse de l’instance'] }],
    ssh: [
        { key: 'ssh_host', label: ['diag.ssh_host', 'Adresse du serveur'] },
        { key: 'ssh_port', label: ['diag.ssh_port', 'Port'], type: 'number' },
        { key: 'ssh_user', label: ['diag.ssh_user', 'Utilisateur'] },
    ],
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

function hasKey() {
    return !!(DIAG.status && DIAG.status.public_key);
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

/** What the last test of a server reported: its script version and the address it sees Sentinel from. */
function sshMeta(l) {
    const parts = [];
    if (l.script_version) parts.push(l.script_version === '0'
        ? dT('diag.script_unnumbered', 'script sans numéro de version')
        : dT('diag.script_v', 'script v{v}').replace('{v}', l.script_version));
    if (l.ssh_client_ip) parts.push(dT('diag.client_seen', 'le serveur voit Sentinel depuis {ip}').replace('{ip}', l.ssh_client_ip));
    return parts.join(' · ');
}

function btn(action, label, extra = '', cls = '') {
    return `<button type="button" class="btn btn-secondary btn-sm ${cls}" data-link-action="${action}" ${extra}>${escapeHtml(label)}</button>`;
}

function linkRow(kind, l) {
    const rules = DIAG.status.all_rules || [];
    const st = rowState(l.id);
    // Les boutons qui ouvrent un panneau sous la connexion portent un chevron ; Tester et Supprimer agissent tout de suite
    const panelBtn = (action, label) => btn(action, label, `aria-expanded="${st.panel === action}"`, 'diag-panel-btn');
    const blockLabel = dT('diag.block_btn', 'Bloc d’installation');
    const meta = kind === 'ssh' ? sshMeta(l) : '';
    const warn = kind === 'ssh' && l.script_outdated
        ? `<p class="diag-link-warn" role="status"><span>${escapeHtml(dT('diag.link_outdated', 'Script ancien sur ce serveur : recolle son bloc d’installation pour avoir les derniers contrôles.'))}</span>${panelBtn('block', blockLabel)}</p>` : '';
    const result = st.result ? `<span class="form-hint diag-link-result ${st.result.ok === true ? 'rf-ok' : st.result.ok === false ? 'rf-ko' : ''}" role="status">${escapeHtml(st.result.text)}</span>`
        : '<span class="form-hint diag-link-result" role="status"></span>';
    return `<li class="diag-link" data-link="${l.id}">
        <div class="diag-link-main"><span class="diag-link-title"><strong>${escapeHtml(l.name)}</strong> <code>${escapeHtml(diagLinkTarget(l))}</code></span>
            ${meta ? `<span class="form-hint diag-link-meta">${escapeHtml(meta)}</span>` : ''}
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
            ${result}</div>
        <div class="diag-actions diag-link-actions">
            <span class="diag-btn-group">
                ${kind === 'ssh' && !l.script_outdated ? panelBtn('block', blockLabel) : ''}
                ${panelBtn('edit', dT('common.edit', 'Modifier'))}
                ${kind === 'ssh' ? panelBtn('remove', dT('diag.remove_title', 'Retirer l’accès')) : ''}
            </span>
            <span class="diag-btn-group diag-btn-group--direct">
                ${btn('test', dT('diag.link_test', 'Tester'))}
                ${btn('delete', dT('common.delete', 'Supprimer'))}
            </span>
        </div>
        ${warn}
        <div class="diag-link-panel hidden"></div></li>`;
}

function renderLinks(kind) {
    const list = document.getElementById(`diag-${kind}-links`);
    const links = diagLinks(kind);
    list.classList.toggle('hidden', !links.length);
    list.innerHTML = links.map(l => linkRow(kind, l)).join('');
    links.forEach(l => fillPanel(kind, l, list.querySelector(`.diag-link[data-link="${l.id}"]`)));
    diagStatusBadge(kind);
    renderAddArea(kind);
}

/** The add form is open when there is nothing yet, or after a click on « Ajouter ». A server needs Sentinel's key first. */
function renderAddArea(kind) {
    const has = diagLinks(kind).length > 0;
    const keyOk = kind !== 'ssh' || hasKey();
    const open = keyOk && (DIAG.adding[kind] || !has);
    document.getElementById(`diag-${kind}-add-form`).classList.toggle('hidden', !open);
    const add = document.getElementById(`diag-${kind}-add`);
    add.classList.toggle('hidden', open || !has || !keyOk);
    add.setAttribute('aria-expanded', String(open));
    document.getElementById(`diag-${kind}-cancel`).classList.toggle('hidden', !has);
    if (kind === 'ssh') {
        document.getElementById('diag-ssh-nokey').classList.toggle('hidden', keyOk || !has);
        document.getElementById('diag-ssh-servers-title').classList.toggle('hidden', !has);
    }
}

async function openAddForm(kind) {
    DIAG.adding[kind] = true;
    await resetLinkForm(kind);
    renderAddArea(kind);
    if (kind === 'ssh') renderSnippet();
    const form = document.getElementById(`diag-${kind}-add-form`);
    form.scrollIntoView({ block: 'nearest' });
    (kind === 'ssh' ? form.querySelector('.diag-platforms button.active') : document.getElementById(`diag-${kind}-name`))?.focus();
}

function closeAddForm(kind) {
    DIAG.adding[kind] = false;
    diagResult(`diag-${kind}-result`, null, '');
    renderAddArea(kind);
    document.getElementById(`diag-${kind}-add`).focus();
}

// ── Panneau de chaque connexion : bloc d'installation, bloc de retrait, modification, sortie du test ──

function fillPanel(kind, l, li) {
    const st = rowState(l.id);
    const panel = li.querySelector('.diag-link-panel');
    panel.classList.toggle('hidden', !st.panel);
    if (!st.panel) { panel.innerHTML = ''; return; }
    if (st.panel === 'output') {
        panel.innerHTML = `<pre class="diag-code">${escapeHtml(st.output)}</pre>`;
    } else if (st.panel === 'edit') {
        fillEditPanel(kind, l, panel, st);
    } else if (st.panel === 'block') {
        fillBlockPanel(l, panel);
    } else if (st.panel === 'remove') {
        fillRemovePanel(l, panel);
    }
}

function sshAccount(l) {
    return l.ssh_platform === 'linux' ? l.ssh_user : DIAG_DEFAULTS.ssh.linux;
}

function platformName(p) {
    return p === 'unraid' ? 'Unraid' : 'Linux';
}

function fillBlockPanel(l, panel) {
    const source = l.ssh_source || l.ssh_client_ip || defaultSource();
    const intro = (l.ssh_platform === 'linux'
        ? dT('diag.block_hint_linux', 'Bloc pour ce serveur : {platform}, compte {account}. Recolle-le après une mise à jour de Sentinel ou de sa clé : il remplace la clé et le script déjà installés, sans rien créer en double.')
        : dT('diag.block_hint_unraid', 'Bloc pour ce serveur : {platform}, utilisateur root. Recolle-le après une mise à jour de Sentinel ou de sa clé : il remplace la clé et le script déjà installés, sans rien créer en double.'))
        .replace('{platform}', platformName(l.ssh_platform)).replace('{account}', l.ssh_user);
    panel.innerHTML = `<p class="form-hint">${escapeHtml(intro)}</p>
        <label class="rf-stack diag-account"><span>${escapeHtml(dT('diag.source', 'Adresse de Sentinel vue par le serveur'))}</span>
            <input type="text" class="mono" data-block-source maxlength="200" spellcheck="false" autocomplete="off"></label>
        <p class="form-hint">${escapeHtml(dT('diag.source_hint', 'La clé ne sera acceptée que depuis cette adresse.'))}</p>
        <pre class="diag-code" data-block-code></pre>
        <div class="diag-actions"><button type="button" class="btn btn-secondary btn-sm" data-panel-copy="block">${escapeHtml(dT('common.copy', 'Copier'))}</button>
            <span class="form-hint" data-block-note role="status"></span></div>`;
    panel.querySelector('[data-block-source]').value = source;
    loadBlock(l, panel);
}

async function loadBlock(l, panel) {
    const pre = panel.querySelector('[data-block-code]');
    if (!hasKey()) { pre.textContent = dT('diag.snippet_need_key', '← Génère d’abord la clé.'); return; }
    const source = panel.querySelector('[data-block-source]').value.trim();
    try {
        const res = await apiFetch(`/api/diagnostics/ssh/install?platform=${l.ssh_platform}&user=${encodeURIComponent(sshAccount(l))}&source=${encodeURIComponent(source)}`);
        pre.textContent = res.snippet;
    } catch (e) {
        pre.textContent = e.message;
    }
}

async function fillRemovePanel(l, panel) {
    const hint = (l.ssh_platform === 'linux'
        ? dT('diag.remove_hint_linux', 'Colle ce bloc dans un terminal de ce serveur : il retire la clé de Sentinel, le script et le compte {account}. Supprime ensuite la connexion ici.')
        : dT('diag.remove_hint_unraid', 'Colle ce bloc dans le terminal d’Unraid : il retire la clé de Sentinel et le script. Supprime ensuite la connexion ici.'))
        .replace('{account}', l.ssh_user);
    panel.innerHTML = `<p class="form-hint">${escapeHtml(hint)}</p><pre class="diag-code" data-remove-code></pre>
        <div class="diag-actions"><button type="button" class="btn btn-secondary btn-sm" data-panel-copy="remove">${escapeHtml(dT('common.copy', 'Copier'))}</button></div>`;
    try {
        const res = await apiFetch(`/api/diagnostics/ssh/uninstall?platform=${l.ssh_platform}&user=${encodeURIComponent(sshAccount(l))}`);
        panel.querySelector('[data-remove-code]').textContent = res.snippet;
    } catch (e) {
        panel.querySelector('[data-remove-code]').textContent = e.message;
    }
}

function fillEditPanel(kind, l, panel, st) {
    const values = st.edit || { name: l.name, ...Object.fromEntries(LINK_FIELDS[kind].map(f => [f.key, l[f.key]])) };
    st.edit = values;
    const field = (key, label, type = 'text') => `<label class="rf-stack"><span>${escapeHtml(label)}</span>
        <input type="${type}" data-field="${key}" ${type === 'number' ? 'min="1" max="65535"' : 'spellcheck="false"'} ${key === 'name' ? 'maxlength="60"' : 'class="mono"'}></label>`;
    panel.innerHTML = `<div class="diag-edit-fields diag-edit-fields--${kind}">
            ${field('name', dT('diag.link_name', 'Nom'))}
            ${LINK_FIELDS[kind].map(f => field(f.key, dT(...f.label), f.type)).join('')}
        </div>
        <div class="diag-actions">
            <button type="button" class="btn btn-primary btn-sm" data-edit="save">${escapeHtml(dT('diag.test_save', 'Tester et enregistrer'))}</button>
            <button type="button" class="btn btn-secondary btn-sm hidden" data-edit="anyway">${escapeHtml(dT('diag.save_anyway', 'Enregistrer quand même'))}</button>
            <button type="button" class="btn btn-secondary btn-sm" data-edit="cancel">${escapeHtml(dT('common.cancel', 'Annuler'))}</button>
            <span class="form-hint" data-edit-result role="status"></span>
        </div>`;
    panel.querySelectorAll('input[data-field]').forEach(i => { i.value = values[i.dataset.field] ?? ''; });
    panel.querySelector('input[data-field="name"]').focus();
}

function editValues(kind, panel) {
    const v = Object.fromEntries([...panel.querySelectorAll('input[data-field]')].map(i => [i.dataset.field, i.value.trim()]));
    if (kind === 'ssh') v.ssh_port = parseInt(v.ssh_port) || 22;
    return v;
}

/** Tests the edited values, then saves them; « Enregistrer quand même » saves without a passing test. */
async function saveEdit(kind, l, li, tested) {
    const panel = li.querySelector('.diag-link-panel');
    const out = panel.querySelector('[data-edit-result]');
    const anyway = panel.querySelector('[data-edit="anyway"]');
    const { name, ...values } = editValues(kind, panel);
    rowState(l.id).edit = { name, ...values };
    anyway.classList.add('hidden');
    let res = null;
    if (tested) {
        out.textContent = dT('diag.testing', 'Test en cours…');
        out.className = 'form-hint';
        res = await runLinkTest(kind, values);
        if (!res.ok) {
            out.textContent = `✗ ${res.error}`;
            out.className = 'form-hint rf-ko';
            anyway.classList.remove('hidden');
            return;
        }
    }
    try {
        await saveLinkValues(kind, l.id, name, values, res);
    } catch (e) {
        out.textContent = `✗ ${diagLinkError(e)}`;
        out.className = 'form-hint rf-ko';
        return;
    }
    const st = rowState(l.id);
    st.edit = null;
    st.panel = null;
    showRowTest(kind, l.id, res, values, { saved: true });
    if (!res) st.result = { ok: null, text: dT('diag.saved_untested', 'Enregistré sans test réussi.') };
    await refreshDiag();
}

// ── Tests ──

async function runLinkTest(kind, values, linkId = null) {
    if (kind === 'web') return apiFetch('/api/diagnostics/web/test', { method: 'POST', body: { url: values.web_url } });
    if (kind === 'docker') return apiFetch('/api/diagnostics/docker/test', { method: 'POST', body: { url: values.docker_url } });
    return apiFetch('/api/diagnostics/ssh/test', { method: 'POST', body: { ssh_host: values.ssh_host, ssh_port: values.ssh_port, ssh_user: values.ssh_user, link_id: linkId } });
}

/** One line for a successful test. For a server: connected, fingerprint, and the address it sees Sentinel from,
 *  with a warning when the block limits the key to another address. */
function testSummary(kind, res, source, saved) {
    const tail = saved ? ` · ${dT('diag.saved_ok', 'enregistré')}` : '';
    if (kind === 'docker') return { ok: true, text: `✓ Docker ${res.server_version || ''} · ${res.running}/${res.containers} ${dT('diag.containers_running', 'conteneurs actifs')}${tail}` };
    if (kind === 'web') return { ok: res.results > 0, text: `${res.results ? '✓' : '⚠'} ${webTestText(res)}${tail}` };
    let seenNote = '';
    const typed = (source || '').trim();
    if (res.client_ip && typed && !typed.split(/[,\s]+/).includes(res.client_ip) && !typed.includes('/')) {
        seenNote = ' ' + dT('diag.client_mismatch', 'Le bloc limite la clé à {src}, mais le serveur voit Sentinel depuis {ip} : corrige l’adresse avant de recoller le bloc.')
            .replace('{src}', typed).replace('{ip}', res.client_ip);
    }
    if (res.script_outdated) return { ok: false, text: dT('diag.script_outdated', '⚠ Connecté, mais le script installé sur le serveur est ancien : recolle son bloc d’installation.') + tail + seenNote };
    const fp = (res.host_fingerprint || '').match(/SHA256:\S+/);
    return { ok: !seenNote, text: `✓ ${dT('diag.connected', 'Connecté')}${fp ? ' · ' + fp[0] : ''}${tail}${seenNote}` };
}

function showRowTest(kind, id, res, values, { saved = false, source = null } = {}) {
    const st = rowState(id);
    if (!res) return;
    const l = diagLinks(kind).find(x => x.id === id);
    st.result = testSummary(kind, res, source ?? l?.ssh_source ?? '', saved);
    if (kind === 'ssh' && res.output) { st.output = res.output; st.panel = 'output'; }
    else if (st.panel === 'edit') st.panel = null;
}

async function testLinkRow(kind, l, li) {
    const out = li.querySelector('.diag-link-result');
    out.textContent = dT('diag.testing', 'Test en cours…');
    out.className = 'form-hint diag-link-result';
    const res = await runLinkTest(kind, l, l.id);
    const st = rowState(l.id);
    if (!res.ok) {
        st.result = { ok: false, text: `✗ ${res.error}` };
        if (st.panel === 'output') st.panel = null;
    } else showRowTest(kind, l.id, res, l);
    // Le test d'un serveur retient sa version de script et l'adresse vue ; celui d'une instance web, sa pause
    if (kind !== 'docker') await refreshDiag(); else renderLinks(kind);
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

function defaultName(kind, values) {
    if (kind === 'ssh') return values.ssh_host;
    if (kind === 'docker') {
        try { return new URL(values.docker_url).hostname; } catch (_) { return 'Docker'; }
    }
    return 'SearXNG';
}

/** Creates (id null) or updates a connection; a server also keeps what its test reported. Its name defaults to the machine's. */
async function saveLinkValues(kind, id, name, values, res, extra = {}) {
    const body = { name: name || defaultName(kind, values), kind, ...values, ...extra };
    if (kind === 'ssh' && res) Object.assign(body, { ssh_script_version: res.script_version || '', ssh_client_ip: res.client_ip || '' });
    return apiFetch(id ? `/api/diagnostics/links/${id}` : '/api/diagnostics/links', { method: id ? 'PUT' : 'POST', body });
}

async function refreshDiag() {
    DIAG.status = await apiFetch('/api/diagnostics');
    renderLinks('docker');
    renderLinks('ssh');
    renderLinks('web');
    renderCve();
    renderSecondLook();
    renderWebPause();
}

/** Second look settings. It needs a Docker or SSH connection: without one, the switches say so instead of doing nothing. */
function renderSecondLook() {
    const s = DIAG.status.second_look || {};
    const usable = (DIAG.status.links || []).some(l => l.kind === 'docker' || l.kind === 'ssh');
    document.getElementById('diag-sl-enabled').checked = !!s.enabled;
    document.getElementById('diag-sl-info').checked = !!s.include_info;
    document.getElementById('diag-sl-lower').checked = !!s.may_lower;
    ['diag-sl-info', 'diag-sl-lower'].forEach(id => { document.getElementById(id).disabled = !s.enabled; });
    const el = document.getElementById('diag-sl-status');
    if (!s.enabled) {
        el.textContent = dT('diag.status_disabled', 'désactivé');
        el.className = 'diag-status';
    } else if (!usable) {
        el.textContent = dT('diag.sl_no_link', 'sans connexion Docker ni SSH');
        el.className = 'diag-status';
    } else {
        el.textContent = dT('diag.cve_on', 'activé');
        el.className = 'diag-status diag-status--on';
    }
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

/** Empties the add form of a kind. The first connection starts with the platform's defaults. */
async function resetLinkForm(kind) {
    document.getElementById(`diag-${kind}-name`).value = '';
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
    if (!diagLinks('ssh').length) {
        try {
            const cfg = await apiFetch('/api/config');
            if (cfg.server_ip && cfg.server_ip !== 'localhost') document.getElementById('diag-ssh-host').value = cfg.server_ip;
        } catch (_) { /* champ laissé vide */ }
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
        prefillSource();
        renderKey(s.public_key);
    } catch (e) {
        diagResult('diag-docker-result', false, e.message);
    }
}

function renderKey(pub) {
    const has = !!pub;
    if (DIAG.status) DIAG.status.public_key = pub || null;
    document.getElementById('diag-pubkey').textContent = pub || '';
    document.getElementById('diag-pubkey').classList.toggle('hidden', !has);
    document.getElementById('diag-key-none').classList.toggle('hidden', has);
    document.getElementById('diag-key-generate').classList.toggle('hidden', has);
    document.getElementById('diag-key-regenerate').classList.toggle('hidden', !has);
    renderAddArea('ssh');
    renderSnippet();
}

async function renderSnippet() {
    const pre = document.getElementById('diag-ssh-snippet');
    if (!hasKey()) {
        pre.textContent = dT('diag.snippet_need_key', '← Génère d’abord la clé.');
        return;
    }
    const source = document.getElementById('diag-ssh-source').value.trim();
    const query = `platform=${DIAG.platform.ssh}&user=${encodeURIComponent(DIAG_DEFAULTS.ssh.linux)}&source=${encodeURIComponent(source)}`;
    try {
        const res = await apiFetch(`/api/diagnostics/ssh/install?${query}`);
        pre.textContent = res.snippet;
    } catch (e) {
        pre.textContent = e.message;
    }
}

/** Address the server sees Sentinel from, for the key's from= option: the one a successful test reported (kept in this
 *  browser, or on a saved server), else the address used to open Sentinel when it is an IP. A host name is left out:
 *  it may not be what the server sees. */
const DIAG_SOURCE_KEY = 'sentinel_diag_source';
function defaultSource() {
    let seen = null;
    try { seen = localStorage.getItem(DIAG_SOURCE_KEY); } catch (e) { /* stockage indisponible */ }
    const fromLink = diagLinks('ssh').map(l => l.ssh_client_ip).find(Boolean);
    const host = location.hostname.replace(/^\[|\]$/g, '');
    const isIp = /^\d{1,3}(\.\d{1,3}){3}$/.test(host) || (host.includes(':') && /^[0-9a-f:]+$/i.test(host));
    return seen || fromLink || (isIp && !['127.0.0.1', '::1'].includes(host) ? host : '');
}

function prefillSource() {
    const input = document.getElementById('diag-ssh-source');
    if (!input.value.trim()) input.value = defaultSource();
}

/** The Linux account name: it becomes the platform default of the user field and goes into the block. */
function setAccount(name) {
    const user = document.getElementById('diag-ssh-user');
    if (DIAG.platform.ssh === 'linux' && user.value.trim() === DIAG_DEFAULTS.ssh.linux) user.value = name;
    DIAG_DEFAULTS.ssh.linux = name;
    document.getElementById('diag-ssh-account').value = name;
}

/** The add form: tests the typed values and saves only if the test passes (« Enregistrer quand même » otherwise).
 *  On success the form closes and the new connection shows the result. */
async function testAndAdd(kind) {
    const values = kind === 'ssh' ? sshFields()
        : { [kind === 'docker' ? 'docker_url' : 'web_url']: document.getElementById(`diag-${kind}-url`).value.trim() };
    const resultId = `diag-${kind}-result`;
    const anyway = document.getElementById(`diag-${kind}-save`);
    anyway.classList.add('hidden');
    if (kind === 'ssh' ? (!values.ssh_host || !values.ssh_user) : !Object.values(values)[0]) {
        diagResult(resultId, false, kind === 'ssh' ? dT('diag.need_host', 'Renseigne l’adresse du serveur et l’utilisateur.') : dT('diag.err_url', 'L’adresse doit commencer par http:// ou https://.'));
        return;
    }
    diagResult(resultId, null, dT('diag.testing', 'Test en cours…'));
    const res = await runLinkTest(kind, values);
    if (!res.ok) {
        diagResult(resultId, false, `✗ ${res.error}`);
        anyway.classList.remove('hidden');
        return;
    }
    await addLink(kind, values, res);
}

async function addLink(kind, values, res) {
    const resultId = `diag-${kind}-result`;
    const source = kind === 'ssh' ? document.getElementById('diag-ssh-source').value.trim() : null;
    let link;
    try {
        link = await saveLinkValues(kind, null, document.getElementById(`diag-${kind}-name`).value.trim(), values, res,
            kind === 'ssh' ? { ssh_source: source } : {});
    } catch (e) {
        diagResult(resultId, false, `✗ ${diagLinkError(e)}`);
        return;
    }
    if (kind === 'ssh' && res && res.client_ip) {
        try { localStorage.setItem(DIAG_SOURCE_KEY, res.client_ip); } catch (e) { /* stockage indisponible */ }
    }
    DIAG.status.links.push(link);
    if (res) showRowTest(kind, link.id, res, values, { saved: true, source });
    else rowState(link.id).result = { ok: null, text: dT('diag.saved_untested', 'Enregistré sans test réussi.') };
    DIAG.adding[kind] = false;
    diagResult(resultId, null, '');
    await resetLinkForm(kind);
    await refreshDiag();
    document.querySelector(`#diag-${kind}-links .diag-link[data-link="${link.id}"]`)?.scrollIntoView({ block: 'nearest' });
}

function sshFields() {
    return {
        ssh_host: document.getElementById('diag-ssh-host').value.trim(),
        ssh_port: parseInt(document.getElementById('diag-ssh-port').value) || 22,
        ssh_user: document.getElementById('diag-ssh-user').value.trim(),
    };
}

function flashCopied(btn) {
    const old = btn.textContent;
    btn.textContent = '✓';
    setTimeout(() => { btn.textContent = old; }, 1500);
}

/** Clicks inside a connection: its actions, its panel's buttons, and the rules list. */
async function onLinkClick(kind, e) {
    const li = e.target.closest('.diag-link');
    if (!li) return;
    const id = Number(li.dataset.link);
    const l = diagLinks(kind).find(x => x.id === id);
    const st = rowState(id);
    const set = e.target.closest('button[data-rules-set]');
    if (set) {
        li.querySelectorAll('input[data-rule]').forEach(c => { c.checked = set.dataset.rulesSet === 'all'; });
        scheduleLinkRules(kind, li);
        return;
    }
    const copy = e.target.closest('button[data-panel-copy]');
    if (copy) {
        const panel = li.querySelector('.diag-link-panel');
        const text = panel.querySelector(copy.dataset.panelCopy === 'block' ? '[data-block-code]' : '[data-remove-code]').textContent;
        await copyToClipboard(text);
        flashCopied(copy);
        if (copy.dataset.panelCopy === 'block') {
            // Ce bloc est désormais celui du serveur : son adresse sera reprise la prochaine fois
            const source = panel.querySelector('[data-block-source]').value.trim();
            try {
                const saved = await apiFetch(`/api/diagnostics/links/${id}/block`, { method: 'PUT', body: { ssh_source: source } });
                l.ssh_source = saved.ssh_source;
            } catch (err) {
                const note = panel.querySelector('[data-block-note]');
                note.textContent = `✗ ${err.message}`;
                note.className = 'form-hint rf-ko';
            }
        }
        return;
    }
    const edit = e.target.closest('button[data-edit]');
    if (edit) {
        if (edit.dataset.edit === 'cancel') {
            st.panel = null;
            st.edit = null;
            renderLinks(kind);
            document.querySelector(`#diag-${kind}-links .diag-link[data-link="${id}"] [data-link-action="edit"]`)?.focus();
        } else await saveEdit(kind, l, li, edit.dataset.edit === 'save');
        return;
    }
    const action = e.target.closest('button[data-link-action]')?.dataset.linkAction;
    if (!action) return;
    if (action === 'test') return testLinkRow(kind, l, li);
    if (action === 'delete') {
        const msg = kind === 'ssh'
            ? dT('diag.delete_confirm_ssh', 'Supprimer cette connexion ? L’IA n’y aura plus accès, mais la clé reste autorisée sur le serveur tant que tu n’y as pas collé le bloc « Retirer l’accès ».')
            : dT('diag.delete_confirm', 'Supprimer cette connexion ? L’IA n’y aura plus accès.');
        showInlineConfirm(e.target.closest('button'), msg, async () => {
            await apiFetch(`/api/diagnostics/links/${id}`, { method: 'DELETE' });
            delete DIAG.rows[id];
            await refreshDiag();
            if (!diagLinks(kind).length) await resetLinkForm(kind);
        });
        return;
    }
    // Bloc d'installation, retrait, modification : un panneau à la fois sous la connexion ; un second clic le referme
    st.panel = st.panel === action ? null : action;
    if (action !== 'edit') st.edit = null;
    renderLinks(kind);
    const row = document.querySelector(`#diag-${kind}-links .diag-link[data-link="${id}"]`);
    if (!st.panel) row?.querySelector(`[data-link-action="${action}"]`)?.focus();
    else row?.querySelector('.diag-link-panel')?.scrollIntoView({ block: 'nearest' });
}

document.addEventListener('DOMContentLoaded', () => {
    if (!document.getElementById('diagnostics-section')) return;
    document.getElementById('diag-docker-compose-code').textContent = DOCKER_COMPOSE_SNIPPET;

    ['docker', 'ssh', 'web'].forEach(kind => {
        document.getElementById(`diag-${kind}-test`).addEventListener('click', () => testAndAdd(kind));
        document.getElementById(`diag-${kind}-save`).addEventListener('click', () => addLink(kind,
            kind === 'ssh' ? sshFields() : { [kind === 'docker' ? 'docker_url' : 'web_url']: document.getElementById(`diag-${kind}-url`).value.trim() }, null));
        document.getElementById(`diag-${kind}-add`).addEventListener('click', () => openAddForm(kind));
        document.getElementById(`diag-${kind}-cancel`).addEventListener('click', () => closeAddForm(kind));
        const linkList = document.getElementById(`diag-${kind}-links`);
        linkList.addEventListener('toggle', (e) => {
            if (!e.target.matches('.diag-link-rules')) return;
            const id = Number(e.target.closest('.diag-link').dataset.link);
            if (e.target.open) DIAG.openRules.add(id); else DIAG.openRules.delete(id);
        }, true);
        linkList.addEventListener('change', (e) => {
            if (e.target.matches('input[data-rule]')) scheduleLinkRules(kind, e.target.closest('.diag-link'));
        });
        linkList.addEventListener('input', (e) => {
            const li = e.target.closest('.diag-link');
            if (!li) return;
            const id = Number(li.dataset.link);
            if (e.target.matches('input[data-field]')) {
                rowState(id).edit = { ...rowState(id).edit, [e.target.dataset.field]: e.target.value };
            } else if (e.target.matches('[data-block-source]')) {
                clearTimeout(linkList.blockTimer);
                linkList.blockTimer = setTimeout(() => {
                    const panel = linkList.querySelector(`.diag-link[data-link="${id}"] .diag-link-panel`);
                    if (panel?.querySelector('[data-block-source]')) loadBlock(diagLinks(kind).find(x => x.id === id), panel);
                }, 400);
            }
        });
        linkList.addEventListener('click', (e) => onLinkClick(kind, e));
    });

    document.getElementById('diag-cve-enabled').addEventListener('change', async (e) => {
        const res = await apiFetch('/api/diagnostics/cve', { method: 'POST', body: { enabled: e.target.checked } });
        DIAG.status.cve_lookup = res.enabled;
        renderCve();
    });
    [['diag-sl-enabled', 'enabled'], ['diag-sl-info', 'include_info'], ['diag-sl-lower', 'may_lower']].forEach(([id, key]) => {
        document.getElementById(id).addEventListener('change', async (e) => {
            try {
                DIAG.status.second_look = await apiFetch('/api/diagnostics/second-look', { method: 'POST', body: { [key]: e.target.checked } });
            } catch (_) {
                e.target.checked = !e.target.checked;
            }
            renderSecondLook();
        });
    });
    document.getElementById('diag-cve-test').addEventListener('click', async () => {
        diagResult('diag-cve-result', null, dT('diag.testing', 'Test en cours…'));
        const res = await apiFetch('/api/diagnostics/cve/test', { method: 'POST' });
        diagResult('diag-cve-result', res.ok, res.ok ? `✓ ${dT('diag.cve_ok', 'NVD répond')}` : `✗ ${res.error}`);
    });

    const genKey = async (force) => {
        try {
            const res = await apiFetch(`/api/diagnostics/ssh/key${force ? '?force=true' : ''}`, { method: 'POST' });
            renderKey(res.public_key);
            await loadDiagnostics();
            if (force) diagResult('diag-key-result', true, `✓ ${dT('diag.key_regenerated', 'Nouvelle clé : recolle le bloc d’installation sur chaque serveur.')}`);
        } catch (e) {
            diagResult('diag-key-result', false, e.message);
        }
    };
    document.getElementById('diag-key-generate').addEventListener('click', () => genKey(false));
    document.getElementById('diag-key-regenerate').addEventListener('click', (e) => {
        showInlineConfirm(e.currentTarget, dT('diag.regenerate_confirm', 'L’ancienne clé ne fonctionnera plus : il faudra recoller le bloc d’installation sur chaque serveur. Continuer ?'), () => genKey(true));
    });

    const importBox = document.getElementById('diag-key-import');
    const pasteBtn = document.getElementById('diag-key-paste');
    const showImport = (open) => {
        importBox.classList.toggle('hidden', !open);
        pasteBtn.setAttribute('aria-expanded', String(open));
        document.getElementById('diag-key-import-result').textContent = '';
        if (open) document.getElementById('diag-key-private').focus();
        else document.getElementById('diag-key-private').value = '';
    };
    pasteBtn.addEventListener('click', () => showImport(importBox.classList.contains('hidden')));
    document.getElementById('diag-key-paste-cancel').addEventListener('click', () => showImport(false));
    const importKey = async () => {
        const result = document.getElementById('diag-key-import-result');
        try {
            const res = await apiFetch('/api/diagnostics/ssh/key/import', { method: 'POST', body: { private_key: document.getElementById('diag-key-private').value } });
            showImport(false);
            renderKey(res.public_key);
            await loadDiagnostics();
            diagResult('diag-key-result', true, `✓ ${dT('diag.key_imported', 'Clé enregistrée. Recolle le bloc sur le serveur si elle n’y est pas déjà autorisée.')}`);
        } catch (e) {
            result.textContent = `✗ ${e.message}`;
            result.className = 'form-hint rf-ko';
        }
    };
    document.getElementById('diag-key-use').addEventListener('click', (e) => {
        if (!hasKey()) return importKey();
        showInlineConfirm(e.currentTarget, dT('diag.regenerate_confirm', 'L’ancienne clé ne fonctionnera plus : il faudra recoller le bloc d’installation sur chaque serveur. Continuer ?'), importKey);
    });
    let sourceTimer = null;
    document.getElementById('diag-ssh-source').addEventListener('input', () => {
        clearTimeout(sourceTimer);
        sourceTimer = setTimeout(renderSnippet, 400);
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

    document.querySelectorAll('.diag-platforms').forEach(group => {
        group.addEventListener('click', (e) => {
            const b = e.target.closest('button[data-platform]');
            if (!b) return;
            selectPlatform(group.dataset.target, b.dataset.platform);
        });
    });
    document.querySelectorAll('.diag-copy').forEach(b => b.addEventListener('click', () => {
        copyToClipboard(document.getElementById(b.dataset.copy).textContent).then(() => flashCopied(b));
    }));

    loadDiagnostics();
});
