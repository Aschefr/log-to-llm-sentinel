// ─── Sources connectées : Docker, Home Assistant, MQTT, expéditeurs de logs ───
// Connection form shared by the rule form and the Configuration page, the source tabs of the rule form, and the
// status messages. A saved secret never comes back from the API: an empty field keeps it.

const SC = { connections: [], dockerLinks: [], loaded: false };

function scT(key, fallback, vars) {
    let s = window.t ? window.t(`sources.${key}`) : '';
    if (!s || s === `sources.${key}`) s = fallback;
    if (vars) Object.entries(vars).forEach(([k, v]) => { s = s.split(`{${k}}`).join(v); });
    return s;
}

const SC_ERRORS = {
    connection_missing: 'La connexion de cette règle n’existe plus. Choisis-en une autre dans les réglages de la règle.',
    container_missing: 'Ce conteneur n’existe pas ou plus.',
    container_stopped: 'Le conteneur est arrêté. La lecture reprendra à son redémarrage.',
    container_invalid: 'Ce nom de conteneur n’est pas valide.',
    docker_denied: 'Le proxy Docker refuse la lecture : vérifie CONTAINERS=1.',
    docker_unreachable: 'Le proxy Docker ne répond pas.',
    docker_error: 'Le proxy Docker a renvoyé une erreur.',
    hass_unreachable: 'Home Assistant ne répond pas à cette adresse.',
    hass_token_refused: 'Home Assistant refuse ce jeton.',
    hass_not_admin: 'Ce jeton n’appartient pas à un compte administrateur.',
    hass_protocol: 'Cette adresse ne répond pas comme Home Assistant.',
    hass_error: 'Home Assistant a renvoyé une erreur.',
    mqtt_unreachable: 'Le serveur MQTT ne répond pas à cette adresse.',
    mqtt_refused: 'Le serveur MQTT refuse ces identifiants.',
    mqtt_error: 'Le serveur MQTT a refusé la connexion.',
    mqtt_topic_invalid: 'Ce sujet MQTT n’est pas valide.',
    internal: 'Erreur interne de lecture de la source.',
};

function scErrorText(code, detail) {
    const text = scT(`err_${code}`, SC_ERRORS[code] || code);
    return detail && ['docker_error', 'hass_error', 'mqtt_error', 'internal'].includes(code) ? `${text} (${detail})` : text;
}

/** One sentence for a rule's source status, or '' when there is nothing to say. */
function scStatusText(status) {
    if (!status) return '';
    if (status.state === 'connected') return scT('state_connected', 'Connecté.');
    if (status.state === 'connecting') return scT('state_connecting', 'Connexion en cours…');
    return scErrorText(status.code, status.detail);
}

async function scLoad() {
    const [conns, diag] = await Promise.all([
        apiFetch('/api/sources/connections').catch(() => ({ connections: [] })),
        apiFetch('/api/diagnostics').catch(() => ({ links: [] })),
    ]);
    SC.connections = conns.connections || [];
    SC.dockerLinks = (diag.links || []).filter(l => l.kind === 'docker');
    SC.loaded = true;
}

// ── Formulaire de connexion ───────────────────

function scFormHtml(kind, conn) {
    const c = conn || {};
    const id = `sc-${kind}`;
    const secretHint = c.has_secret ? scT('secret_keep', 'Laisser vide pour garder celui enregistré') : '';
    const address = kind === 'homeassistant'
        ? `<div class="form-group"><label for="${id}-address">${scEsc(scT('hass_url', 'Adresse de Home Assistant'))}</label>
               <input type="text" id="${id}-address" class="mono" spellcheck="false" autocomplete="off" placeholder="http://homeassistant.local:8123" value="${scEsc(c.address || '')}"></div>
           <div class="form-group"><label for="${id}-secret">${scEsc(scT('hass_token', 'Jeton d’accès longue durée'))}</label>
               <input type="password" id="${id}-secret" class="mono" autocomplete="new-password" placeholder="${scEsc(secretHint)}"></div>`
        : `<div class="sc-row">
               <div class="form-group"><label for="${id}-address">${scEsc(scT('mqtt_host', 'Adresse du serveur'))}</label>
                   <input type="text" id="${id}-address" class="mono" spellcheck="false" autocomplete="off" placeholder="192.168.1.10" value="${scEsc(c.address || '')}"></div>
               <div class="form-group sc-port"><label for="${id}-port">${scEsc(scT('mqtt_port', 'Port'))}</label>
                   <input type="number" id="${id}-port" min="1" max="65535" value="${scEsc(String(c.port || 1883))}"></div>
           </div>
           <div class="sc-row">
               <div class="form-group"><label for="${id}-username">${scEsc(scT('mqtt_user', 'Utilisateur (si nécessaire)'))}</label>
                   <input type="text" id="${id}-username" autocomplete="off" value="${scEsc(c.username || '')}"></div>
               <div class="form-group"><label for="${id}-secret">${scEsc(scT('mqtt_password', 'Mot de passe'))}</label>
                   <input type="password" id="${id}-secret" autocomplete="new-password" placeholder="${scEsc(secretHint)}"></div>
           </div>
           <label class="sc-tls"><input type="checkbox" id="${id}-tls" ${c.use_tls ? 'checked' : ''}> ${scEsc(scT('mqtt_tls', 'Connexion chiffrée (TLS, souvent le port 8883)'))}</label>`;
    return `<div class="sc-form" data-conn-id="${c.id || ''}">
        <div class="form-group"><label for="${id}-name">${scEsc(scT('conn_name', 'Nom de la connexion'))}</label>
            <input type="text" id="${id}-name" autocomplete="off" placeholder="${scEsc(kind === 'mqtt' ? 'Mosquitto' : 'Maison')}" value="${scEsc(c.name || (kind === 'mqtt' ? 'Mosquitto' : 'Home Assistant'))}"></div>
        ${address}
        <div class="diag-actions">
            <button type="button" class="btn btn-secondary btn-sm" data-sc-test="${kind}">${scEsc(scT('test', 'Tester'))}</button>
            <button type="button" class="btn btn-primary btn-sm" data-sc-save="${kind}">${scEsc(scT('save_conn', 'Enregistrer la connexion'))}</button>
        </div>
        <p class="form-hint sc-result" id="${id}-result" role="status"></p>
        <div class="sc-topics" id="${id}-topics"></div>
    </div>`;
}

function scEsc(text) {
    return escapeHtml(text == null ? '' : String(text));
}

function scFormValues(kind, form) {
    const value = name => form.querySelector(`#sc-${kind}-${name}`)?.value?.trim() ?? '';
    return { id: parseInt(form.dataset.connId) || null, kind, name: value('name'), address: value('address'),
        port: kind === 'mqtt' ? (parseInt(value('port')) || 1883) : null, username: value('username'), secret: value('secret'),
        use_tls: !!form.querySelector(`#sc-${kind}-tls`)?.checked };
}

function scResult(kind, form, ok, text) {
    const el = form.querySelector(`#sc-${kind}-result`);
    el.textContent = text;
    el.className = `form-hint sc-result ${ok === null ? '' : ok ? 'rf-ok' : 'rf-ko'}`;
}

async function scTest(kind, form) {
    scResult(kind, form, null, scT('testing', 'Test en cours…'));
    const topics = form.querySelector(`#sc-${kind}-topics`);
    topics.innerHTML = '';
    let res;
    try {
        res = await apiFetch('/api/sources/connections/test', { method: 'POST', body: scFormValues(kind, form) });
    } catch (e) {
        scResult(kind, form, false, e.message);
        return;
    }
    if (!res.ok) {
        scResult(kind, form, false, scErrorText(res.code, res.detail));
        return;
    }
    if (kind === 'homeassistant') {
        scResult(kind, form, true, scT('hass_test_ok', 'Connecté à Home Assistant {version}. {n} entrée(s) dans son journal.', { version: res.version || '', n: res.entries }));
        return;
    }
    scResult(kind, form, true, res.topic_count
        ? scT('mqtt_test_topics', 'Connecté. {n} sujet(s) actif(s) vus en 3 secondes :', { n: res.topic_count })
        : scT('mqtt_test_ok', 'Connecté. Aucun message vu en 3 secondes.'));
    topics.innerHTML = (res.topics || []).map(t => `<button type="button" class="sc-topic mono" data-topic="${scEsc(t)}">${scEsc(t)}</button>`).join('');
}

async function scSave(kind, form, onSaved) {
    const values = scFormValues(kind, form);
    try {
        const saved = await apiFetch(values.id ? `/api/sources/connections/${values.id}` : '/api/sources/connections',
            { method: values.id ? 'PUT' : 'POST', body: values });
        await scLoad();
        onSaved(saved);
    } catch (e) {
        scResult(kind, form, false, e.message);
    }
}

/** Fills a container with the form and wires its buttons. */
function scMountForm(container, kind, conn, onSaved, onTopic) {
    container.innerHTML = scFormHtml(kind, conn);
    const form = container.querySelector('.sc-form');
    form.querySelector('[data-sc-test]').addEventListener('click', () => scTest(kind, form));
    form.querySelector('[data-sc-save]').addEventListener('click', () => scSave(kind, form, onSaved));
    form.querySelector(`#sc-${kind}-topics`).addEventListener('click', e => {
        const topic = e.target.closest('[data-topic]');
        if (topic && onTopic) onTopic(topic.dataset.topic);
    });
}

// ── Onglets de source du formulaire de règle ──

function rsConnections(kind) {
    return SC.connections.filter(c => c.kind === kind);
}

function rsFillSelect(select, rows, selected) {
    select.innerHTML = rows.map(r => `<option value="${r.id}" ${r.id === selected ? 'selected' : ''}>${scEsc(r.name)}</option>`).join('');
}

function rsShowConnection(kind, selected) {
    const rows = rsConnections(kind);
    const select = document.getElementById(`rs-${kind}-conn`);
    rsFillSelect(select, rows, selected ?? rows[0]?.id);
    document.getElementById(`rs-${kind}-pick`).classList.toggle('hidden', !rows.length);
    const add = document.getElementById(`rs-${kind}-add`);
    add.open = !rows.length;
    scMountForm(document.getElementById(`rs-${kind}-form`), kind, null, saved => rsShowConnection(kind, saved.id), topic => {
        const input = document.getElementById('rs-mqtt-topic');
        if (input) input.value = topic;
    });
}

async function rsShowDocker(selected, container) {
    const rows = SC.dockerLinks;
    const select = document.getElementById('rs-docker-conn');
    rsFillSelect(select, rows, selected ?? rows[0]?.id);
    document.getElementById('rs-docker-none').classList.toggle('hidden', rows.length > 0);
    document.getElementById('rs-docker-pick').classList.toggle('hidden', !rows.length);
    if (container !== undefined) document.getElementById('rs-docker-container').value = container;
    document.getElementById('rs-docker-container').toggleAttribute('required', rows.length > 0);
    if (rows.length) await rsLoadContainers();
}

async function rsLoadContainers() {
    const id = document.getElementById('rs-docker-conn').value;
    const list = document.getElementById('rs-docker-list');
    const hint = document.getElementById('rs-docker-hint');
    list.innerHTML = '';
    hint.textContent = '';
    if (!id) return;
    try {
        const res = await apiFetch(`/api/sources/docker/containers?link_id=${id}`);
        if (!res.ok) {
            hint.textContent = res.detail || scT('err_docker_unreachable', SC_ERRORS.docker_unreachable);
            hint.className = 'form-hint rf-ko';
            return;
        }
        list.innerHTML = res.containers.map(c => `<option value="${scEsc(c.name)}">${scEsc(c.state || '')}</option>`).join('');
        hint.textContent = scT('docker_count', '{n} conteneur(s) sur ce proxy.', { n: res.containers.length });
        hint.className = 'form-hint';
    } catch (e) {
        hint.textContent = e.message;
        hint.className = 'form-hint rf-ko';
    }
}

/** Called when a connected source tab opens; `path` is the rule's source when editing it. */
async function rsOpen(source, path) {
    const required = { docker: 'rs-docker-container', mqtt: 'rs-mqtt-topic' };
    Object.values(required).forEach(id => document.getElementById(id)?.removeAttribute('required'));
    if (required[source]) document.getElementById(required[source])?.setAttribute('required', 'required');
    await scLoad();
    const [, id, ...rest] = (path || '').split(':');
    const connectionId = parseInt(id) || undefined;
    const value = rest.join(':');
    if (source === 'docker') await rsShowDocker(connectionId, path ? value : undefined);
    else rsShowConnection(source, connectionId);
    if (source === 'mqtt' && path) document.getElementById('rs-mqtt-topic').value = value;
    rsShowStatus();
}

function rsCloseAll() {
    ['rs-docker-container', 'rs-mqtt-topic'].forEach(id => document.getElementById(id)?.removeAttribute('required'));
}

/** The rule's source path, or null with a message on screen when something is missing. */
function rsPath(source) {
    const message = text => { const el = document.getElementById(`rs-${source}-error`); if (el) el.textContent = text; };
    const connection = document.getElementById(`rs-${source}-conn`)?.value;
    message('');
    if (!connection) {
        message(scT('need_connection', 'Enregistre d’abord une connexion.'));
        return null;
    }
    if (source === 'docker') return `[DOCKER]:${connection}:${document.getElementById('rs-docker-container').value.trim()}`;
    if (source === 'mqtt') return `[MQTT]:${connection}:${document.getElementById('rs-mqtt-topic').value.trim()}`;
    return `[HOMEASSISTANT]:${connection}`;
}

async function rsShowStatus() {
    const ruleId = document.getElementById('rule-id')?.value;
    document.querySelectorAll('.rs-status').forEach(el => { el.textContent = ''; });
    if (!ruleId) return;
    try {
        const { status } = await apiFetch(`/api/sources/status/${ruleId}`);
        const text = scStatusText(status);
        document.querySelectorAll('.rs-status').forEach(el => {
            el.textContent = text;
            el.className = `form-hint rs-status ${status?.state === 'connected' ? 'rf-ok' : status?.state === 'error' ? 'rf-ko' : ''}`;
        });
    } catch (e) { /* facultatif */ }
}

// ── Expéditeurs de logs, dans l'onglet Webhook ─

function rsShipperSnippets(url) {
    const parsed = new URL(url);
    const secure = parsed.protocol === 'https:';
    const port = parsed.port || (secure ? '443' : '80');
    const fluent = (input, job) => `${input}

[OUTPUT]
    Name             loki
    Match            *
    Host             ${parsed.hostname}
    Port             ${port}
    Uri              ${parsed.pathname}/loki/api/v1/push
    Labels           job=${job}
    Line_Format      key_value
    Drop_Single_Key  On${secure ? '\n    Tls              On' : ''}`;
    return {
        fluentbit: fluent('[INPUT]\n    Name  tail\n    Path  /var/log/monapp/*.log', 'monapp'),
        windows: fluent('[INPUT]\n    Name      winevtlog\n    Channels  System,Application', 'windows'),
        alloy: `local.file_match "app" {
  path_targets = [{ __path__ = "/var/log/monapp/*.log", job = "monapp" }]
}

loki.source.file "app" {
  targets    = local.file_match.app.targets
  forward_to = [loki.write.sentinel.receiver]
}

loki.write "sentinel" {
  endpoint {
    url = "${url}/loki/api/v1/push"
  }
}`,
        otel: `exporters:
  otlphttp/sentinel:
    logs_endpoint: ${url}/v1/logs

service:
  pipelines:
    logs:
      receivers: [otlp]
      exporters: [otlphttp/sentinel]`,
    };
}

function rsSelectShipper(id) {
    const url = document.getElementById('webhook-curl-cmd')?.textContent || '';
    if (!url) return;
    const snippets = rsShipperSnippets(url);
    document.querySelectorAll('#rs-shippers button').forEach(b => {
        b.classList.toggle('active', b.dataset.shipper === id);
        b.setAttribute('aria-pressed', b.dataset.shipper === id);
    });
    document.getElementById('rs-shipper-code').textContent = snippets[id];
    document.getElementById('rs-shipper-hint').textContent = scT(`shipper_${id}_hint`, '');
}

function rsCopyShipper(button) {
    const text = document.getElementById('rs-shipper-code').textContent;
    const done = () => { const label = button.textContent; button.textContent = scT('copied', 'Copié'); setTimeout(() => { button.textContent = label; }, 1500); };
    (navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject()).then(done, () => fallbackCopyTextToClipboard(text) || done());
}

document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('rs-docker-conn')?.addEventListener('change', rsLoadContainers);
    document.getElementById('rs-shippers')?.addEventListener('click', e => {
        const b = e.target.closest('[data-shipper]');
        if (b) rsSelectShipper(b.dataset.shipper);
    });
    document.getElementById('rs-shipper-details')?.addEventListener('toggle', () => rsSelectShipper(document.querySelector('#rs-shippers button.active')?.dataset.shipper || 'fluentbit'));
    scConfigInit();
});

// ── Page Configuration ────────────────────────

function scConfigInit() {
    if (!document.getElementById('cfg-source-conns')) return;
    scConfigRender();
    window.i18n?.onLanguageChange(scConfigRender);
}

async function scConfigRender() {
    await scLoad();
    const list = document.getElementById('cfg-source-conns');
    const kindName = { homeassistant: 'Home Assistant', mqtt: 'MQTT' };
    list.innerHTML = SC.connections.length ? SC.connections.map(c => `<li class="diag-link">
        <div class="diag-link-main"><strong>${scEsc(c.name)}</strong>
            <span class="form-hint">${scEsc(kindName[c.kind])} · <span class="mono">${scEsc(c.address)}${c.kind === 'mqtt' ? ':' + scEsc(c.port) : ''}</span></span>
            <span class="form-hint">${scEsc(c.rules.length ? scT('conn_rules', 'Utilisée par : {names}', { names: c.rules.join(', ') }) : scT('conn_unused', 'Aucune règle ne l’utilise.'))}</span>
        </div>
        <div class="diag-actions">
            <button type="button" class="btn btn-secondary btn-sm" onclick="scConfigEdit(${c.id})">${scEsc(scT('edit', 'Modifier'))}</button>
            <button type="button" class="btn btn-secondary btn-sm" onclick="scConfigDelete(${c.id}, this)" ${c.rules.length ? `disabled title="${scEsc(scT('conn_used_title', 'Utilisée par une règle'))}"` : ''}>${scEsc(scT('delete', 'Supprimer'))}</button>
        </div>
    </li>`).join('') : `<li class="form-hint">${scEsc(scT('conn_none', 'Aucune connexion. Tu peux aussi en créer une depuis l’onglet Source d’une règle.'))}</li>`;
    document.getElementById('cfg-source-form').innerHTML = '';
}

function scConfigAdd(kind) {
    const target = document.getElementById('cfg-source-form');
    scMountForm(target, kind, null, scConfigRender);
}

function scConfigEdit(id) {
    const conn = SC.connections.find(c => c.id === id);
    scMountForm(document.getElementById('cfg-source-form'), conn.kind, conn, scConfigRender);
}

function scConfigDelete(id, button) {
    const conn = SC.connections.find(c => c.id === id);
    showInlineConfirm(button, scT('delete_confirm', 'Supprimer la connexion « {name} » ?', { name: conn.name }), async () => {
        await apiFetch(`/api/sources/connections/${id}`, { method: 'DELETE' });
        scConfigRender();
    });
}
