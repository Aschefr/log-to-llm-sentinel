// ─── Configuration du diagnostic en lecture seule (proxy Docker, SSH limité) ───

const DIAG = { platform: { docker: 'compose', ssh: 'linux' }, status: null };

// Valeurs par défaut selon la plateforme : pré-remplies (et non en simple placeholder)
// pour qu'un clic sur « Tester et enregistrer » suffise.
const DIAG_DEFAULTS = {
    docker: { field: 'diag-docker-url', compose: 'http://docker-proxy:2375', unraid: 'http://sentinel-docker-proxy:2375' },
    ssh: { field: 'diag-ssh-user', linux: 'sentinel-diag', unraid: 'root' },
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
    if (isDiagDefault(target, input.value.trim())) input.value = DIAG_DEFAULTS[target][platform];
    if (target === 'ssh') renderSnippet();
    else document.querySelectorAll('[data-howto^="docker-"]').forEach(el =>
        el.classList.toggle('hidden', el.dataset.howto !== `docker-${platform}`));
}

const DOCKER_COMPOSE_SNIPPET = `  docker-proxy:
    image: tecnativa/docker-socket-proxy:v0.5.0
    container_name: sentinel-docker-proxy
    restart: unless-stopped
    environment:
      CONTAINERS: 1   # liste, état, logs, consommation des conteneurs
      INFO: 1         # informations générales du moteur Docker
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

function diagStatusBadge(id, on) {
    const el = document.getElementById(id);
    el.textContent = on ? dT('diag.status_on', 'configuré') : dT('diag.status_off', 'non configuré');
    el.className = 'diag-status ' + (on ? 'diag-status--on' : '');
}

async function loadDiagnostics() {
    try {
        const s = await apiFetch('/api/diagnostics');
        DIAG.status = s;
        // Plateforme déduite de la configuration enregistrée, puis valeurs par défaut si vide
        if (s.docker_proxy_url === DIAG_DEFAULTS.docker.unraid) DIAG.platform.docker = 'unraid';
        if (s.ssh_user === DIAG_DEFAULTS.ssh.unraid) DIAG.platform.ssh = 'unraid';
        document.getElementById('diag-docker-url').value = s.docker_proxy_url || DIAG_DEFAULTS.docker[DIAG.platform.docker];
        document.getElementById('diag-ssh-host').value = s.ssh_host || '';
        document.getElementById('diag-ssh-port').value = s.ssh_port || 22;
        document.getElementById('diag-ssh-user').value = s.ssh_user || DIAG_DEFAULTS.ssh[DIAG.platform.ssh];
        selectPlatform('docker', DIAG.platform.docker);
        document.querySelectorAll('.diag-platforms[data-target="ssh"] button').forEach(b =>
            b.classList.toggle('active', b.dataset.platform === DIAG.platform.ssh));
        if (!s.ssh_host) {
            try {
                const cfg = await apiFetch('/api/config');
                if (cfg.server_ip && cfg.server_ip !== 'localhost') document.getElementById('diag-ssh-host').value = cfg.server_ip;
            } catch (_) { /* champ laissé vide */ }
        }
        diagStatusBadge('diag-docker-status', s.available.docker);
        diagStatusBadge('diag-host-status', s.available.host);
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
    renderSnippet();
}

async function renderSnippet() {
    const pre = document.getElementById('diag-ssh-snippet');
    if (!DIAG.status || !document.getElementById('diag-pubkey').textContent) {
        pre.textContent = dT('diag.snippet_need_key', '← Génère d’abord la clé.');
        return;
    }
    try {
        const res = await apiFetch(`/api/diagnostics/ssh/install?platform=${DIAG.platform.ssh}`);
        pre.textContent = res.snippet;
    } catch (e) {
        pre.textContent = e.message;
    }
}

async function saveDiag(body, resultId) {
    try {
        await apiFetch('/api/diagnostics', { method: 'PUT', body });
        const s = await apiFetch('/api/diagnostics');
        DIAG.status = s;
        diagStatusBadge('diag-docker-status', s.available.docker);
        diagStatusBadge('diag-host-status', s.available.host);
        return true;
    } catch (e) {
        diagResult(resultId, false, `✗ ${e.message}`);
        return false;
    }
}

// Teste les valeurs saisies et ne les enregistre que si le test réussit
// (« Enregistrer quand même » est alors proposé en cas d'échec). Un champ vide désactive.
async function testAndSaveDocker() {
    const url = document.getElementById('diag-docker-url').value.trim();
    const anyway = document.getElementById('diag-docker-save');
    anyway.classList.add('hidden');
    if (!url) {
        if (await saveDiag({ docker_proxy_url: '' }, 'diag-docker-result'))
            diagResult('diag-docker-result', null, dT('diag.disabled', 'Champ vide enregistré : diagnostic désactivé.'));
        return;
    }
    diagResult('diag-docker-result', null, dT('diag.testing', 'Test en cours…'));
    const res = await apiFetch('/api/diagnostics/docker/test', { method: 'POST', body: { url } });
    if (!res.ok) {
        diagResult('diag-docker-result', false, `✗ ${res.error}`);
        anyway.classList.remove('hidden');
        return;
    }
    if (await saveDiag({ docker_proxy_url: url }, 'diag-docker-result'))
        diagResult('diag-docker-result', true,
            `✓ Docker ${res.server_version} · ${res.running}/${res.containers} ${dT('diag.containers_running', 'conteneurs actifs')} · ${dT('diag.saved_ok', 'enregistré')}`);
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
    if (!body.ssh_host && !body.ssh_user) {
        if (await saveDiag(body, 'diag-ssh-result'))
            diagResult('diag-ssh-result', null, dT('diag.disabled', 'Champ vide enregistré : diagnostic désactivé.'));
        return;
    }
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
    if (!await saveDiag(body, 'diag-ssh-result')) return;
    if (res.script_outdated) {
        diagResult('diag-ssh-result', false, dT('diag.script_outdated', '⚠ Connecté, mais le script installé sur le serveur est ancien : recolle le bloc de l’étape 2 pour profiter des derniers contrôles.'));
    } else {
        diagResult('diag-ssh-result', true, `✓ ${dT('diag.connected', 'Connecté')}${res.host_fingerprint ? ' · ' + res.host_fingerprint.split(' ')[1] : ''} · ${dT('diag.saved_ok', 'enregistré')}`);
    }
    out.textContent = res.output;
    out.classList.remove('hidden');
}

document.addEventListener('DOMContentLoaded', () => {
    if (!document.getElementById('diagnostics-section')) return;
    document.getElementById('diag-docker-compose-code').textContent = DOCKER_COMPOSE_SNIPPET;

    document.getElementById('diag-docker-test').addEventListener('click', testAndSaveDocker);
    document.getElementById('diag-docker-save').addEventListener('click', async (e) => {
        const btn = e.currentTarget;
        if (await saveDiag({ docker_proxy_url: document.getElementById('diag-docker-url').value.trim() }, 'diag-docker-result')) {
            btn.classList.add('hidden');
            diagResult('diag-docker-result', null, dT('diag.saved_untested', 'Enregistré sans test réussi.'));
        }
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

    document.getElementById('diag-ssh-test').addEventListener('click', testAndSaveSsh);
    document.getElementById('diag-ssh-save').addEventListener('click', async (e) => {
        const btn = e.currentTarget;
        if (await saveDiag(sshFields(), 'diag-ssh-result')) {
            btn.classList.add('hidden');
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
