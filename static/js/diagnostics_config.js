// ─── Configuration du diagnostic en lecture seule (proxy Docker, SSH limité) ───

const DIAG = { platform: { docker: 'compose', ssh: 'linux' }, status: null };

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
        document.getElementById('diag-docker-url').value = s.docker_proxy_url || '';
        document.getElementById('diag-ssh-host').value = s.ssh_host || '';
        document.getElementById('diag-ssh-port').value = s.ssh_port || 22;
        document.getElementById('diag-ssh-user').value = s.ssh_user || '';
        if (!s.ssh_host) {
            try {
                const cfg = await apiFetch('/api/config');
                if (cfg.server_ip && cfg.server_ip !== 'localhost') document.getElementById('diag-ssh-host').placeholder = cfg.server_ip;
            } catch (_) { /* placeholder par défaut */ }
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
        const user = document.getElementById('diag-ssh-user');
        if (!user.value) user.placeholder = DIAG.platform.ssh === 'unraid' ? 'root' : 'sentinel-diag';
    } catch (e) {
        pre.textContent = e.message;
    }
}

async function saveDiag(body, resultId) {
    try {
        await apiFetch('/api/diagnostics', { method: 'PUT', body });
        await loadDiagnostics();
        diagResult(resultId, true, dT('diag.saved', 'Enregistré.'));
    } catch (e) {
        diagResult(resultId, false, e.message);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    if (!document.getElementById('diagnostics-section')) return;
    document.getElementById('diag-docker-compose-code').textContent = DOCKER_COMPOSE_SNIPPET;

    document.getElementById('diag-docker-test').addEventListener('click', async () => {
        const url = document.getElementById('diag-docker-url').value.trim();
        if (!url) return;
        diagResult('diag-docker-result', null, dT('diag.testing', 'Test en cours…'));
        const res = await apiFetch('/api/diagnostics/docker/test', { method: 'POST', body: { url } });
        diagResult('diag-docker-result', res.ok, res.ok
            ? `✓ Docker ${res.server_version} · ${res.running}/${res.containers} ${dT('diag.containers_running', 'conteneurs actifs')}`
            : `✗ ${res.error}`);
    });
    document.getElementById('diag-docker-save').addEventListener('click', () =>
        saveDiag({ docker_proxy_url: document.getElementById('diag-docker-url').value }, 'diag-docker-result'));

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

    document.getElementById('diag-ssh-save').addEventListener('click', () => saveDiag({
        ssh_host: document.getElementById('diag-ssh-host').value,
        ssh_port: parseInt(document.getElementById('diag-ssh-port').value) || 22,
        ssh_user: document.getElementById('diag-ssh-user').value,
    }, 'diag-ssh-result'));
    document.getElementById('diag-ssh-test').addEventListener('click', async () => {
        const out = document.getElementById('diag-ssh-output');
        diagResult('diag-ssh-result', null, dT('diag.testing', 'Test en cours…'));
        const res = await apiFetch('/api/diagnostics/ssh/test', { method: 'POST' });
        diagResult('diag-ssh-result', res.ok, res.ok ? `✓ ${dT('diag.connected', 'Connecté')}${res.host_fingerprint ? ' · ' + res.host_fingerprint.split(' ')[1] : ''}` : `✗ ${res.error}`);
        if (res.ok && res.script_outdated) {
            diagResult('diag-ssh-result', false, dT('diag.script_outdated', '⚠ Connecté, mais le script installé sur le serveur est ancien : recolle le bloc de l’étape 2 pour profiter des derniers contrôles.'));
        }
        out.textContent = res.ok ? res.output : '';
        out.classList.toggle('hidden', !res.ok);
    });

    document.querySelectorAll('.diag-platforms').forEach(group => {
        group.addEventListener('click', (e) => {
            const btn = e.target.closest('button[data-platform]');
            if (!btn) return;
            group.querySelectorAll('button').forEach(b => b.classList.toggle('active', b === btn));
            DIAG.platform[group.dataset.target] = btn.dataset.platform;
            if (group.dataset.target === 'ssh') renderSnippet();
            else document.querySelectorAll('[data-howto^="docker-"]').forEach(el =>
                el.classList.toggle('hidden', el.dataset.howto !== `docker-${btn.dataset.platform}`));
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
