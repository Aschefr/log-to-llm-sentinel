// ─── Rule form (Réglages tab of the rule page) ──────────────────────────────
// Form logic shared by the rule page: autopilot, source tabs, file browser,
// keywords (new manual rule only: afterwards they live in the Keywords tab),
// resolution settings, save.
//
// Usage:
//   setupRuleForm({ onSave(ruleId, isNew), onCancel() })
//   editRule(id)   → fills the form with the rule
//   resetForm()    → empty form for a new rule
// ─────────────────────────────────────────────────────────────────────────────

/* ── Callbacks set by host page ────────────────────────────────────────────── */
let _ruleFormOnSave = null;
let _ruleFormOnCancel = null;
let _rfAutopilot = null;  // autopilot state of the edited rule (null for a new rule)

/**
 * crypto.randomUUID() is only available in secure contexts (HTTPS / localhost).
 * This fallback works on plain HTTP over LAN (e.g. http://192.168.x.x).
 */
function _generateUUID() {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
        return crypto.randomUUID();
    }
    // Polyfill: RFC4122 v4 UUID using Math.random()
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
        const r = Math.random() * 16 | 0;
        return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
    });
}

function _rfT(key, fallback) {
    const v = window.t ? window.t(key) : key;
    return (v && v !== key) ? v : fallback;
}

/* ── Radio helpers ─────────────────────────────────────────────────────────── */
function _radioValue(name, fallback) {
    const el = document.querySelector(`input[name="${name}"]:checked`);
    return el ? el.value : fallback;
}

function _setRadio(name, value) {
    const el = document.querySelector(`input[name="${name}"][value="${value}"]`);
    if (el) el.checked = true;
}

/* ── Keyword suggestions ───────────────────────────────────────────────────── */
const TYPICAL_KEYWORDS = [
    'error', 'exception', 'fatal', 'critical', 'warning', 'warn',
    'timeout', 'timed out', 'connection refused', 'refused',
    'unhandled', 'panic', 'traceback', 'stacktrace',
    'failed', 'failure', 'permission denied', 'unauthorized', 'forbidden',
    'rate limit', 'oom', 'out of memory', 'disk full', 'no space left',
    'segfault', '502', '503', '504',
];

function setupKeywordSuggestions() {
    const datalist = document.getElementById('keywords-suggestions');
    const chips = document.getElementById('keyword-chips');
    if (!datalist || !chips) return;

    datalist.innerHTML = TYPICAL_KEYWORDS.map(k => `<option value="${escapeHtml(k)}"></option>`).join('');

    // Chips: show a short curated list (avoid overwhelming)
    const chipList = ['error', 'exception', 'timeout', 'permission denied', 'unauthorized', 'disk full', 'out of memory', 'panic', '502', '503'];
    chips.innerHTML = chipList.map(k => `<span class="chip" data-keyword="${escapeHtml(k)}">${escapeHtml(k)} +</span>`).join('');

    chips.addEventListener('click', (e) => {
        const el = e.target.closest('.chip');
        if (!el) return;
        const kw = el.getAttribute('data-keyword');
        if (!kw) return;
        addKeywordToInput(kw);
    });
}

function addKeywordToInput(keyword) {
    const input = document.getElementById('rule-keywords');
    if (!input) return;
    const current = input.value
        .split(',')
        .map(k => k.trim())
        .filter(Boolean);

    if (!current.includes(keyword)) {
        current.push(keyword);
        input.value = current.join(', ');
    }
    input.focus();
}

/* ── Form setup ────────────────────────────────────────────────────────────── */

/**
 * Wire up the rule form (submit + file browser + source tabs + resolution settings).
 * @param {Object} opts
 * @param {Function} opts.onSave   - called after a successful save with (ruleId, isNew)
 * @param {Function} opts.onCancel - called when the user cancels
 */
function setupRuleForm(opts = {}) {
    _ruleFormOnSave = opts.onSave || null;
    _ruleFormOnCancel = opts.onCancel || null;

    const form = document.getElementById('rule-form');
    if (!form) return;

    setupFileBrowser();

    const syslogRuleCurrentIpEl = document.getElementById('syslog-rule-current-ip');
    if (syslogRuleCurrentIpEl) {
        apiFetch('/api/config')
            .then(config => {
                const displayIp = (config.server_ip && config.server_ip !== 'localhost') ? config.server_ip : (window.location.hostname || 'IP_HOTE');
                syslogRuleCurrentIpEl.textContent = displayIp;
            })
            .catch(() => {
                syslogRuleCurrentIpEl.textContent = window.location.hostname || 'IP_HOTE';
            });
    }

    const cancelBtn = document.getElementById('cancel-rule');
    if (cancelBtn) cancelBtn.addEventListener('click', () => { if (_ruleFormOnCancel) _ruleFormOnCancel(); });

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        if (!form.reportValidity()) return;
        await saveRule();
    });

    const pathInput = document.getElementById('rule-path');
    if (pathInput) {
        let previewTimeout;
        pathInput.addEventListener('input', () => {
            clearTimeout(previewTimeout);
            previewTimeout = setTimeout(() => {
                fetchFilePreview(pathInput.value);
            }, 500);
        });
    }

    const sourceCards = document.querySelectorAll('.source-card');
    const pathGroup = document.getElementById('path-group');
    const webhookGroup = document.getElementById('webhook-group');
    const syslogGroup = document.getElementById('syslog-group');
    const pathInputEl = document.getElementById('rule-path');
    const syslogHostnameEl = document.getElementById('syslog-hostname');

    sourceCards.forEach(card => {
        card.addEventListener('click', () => {
            sourceCards.forEach(c => { c.classList.remove('kw-tab--active'); c.setAttribute('aria-selected', 'false'); });
            card.classList.add('kw-tab--active');
            card.setAttribute('aria-selected', 'true');

            if (card.dataset.source === 'webhook') {
                pathGroup.classList.add('hidden');
                webhookGroup.classList.remove('hidden');
                if (syslogGroup) syslogGroup.classList.add('hidden');
                pathInputEl.removeAttribute('required');
                if (syslogHostnameEl) syslogHostnameEl.removeAttribute('required');
                pathInputEl.value = ''; // Clear value so it doesn't cause hidden validation issues

                // Générer un UUID si aucun token n'est encore défini (utile pour Nouvelle règle)
                if (!window._currentWebhookToken) {
                    window._currentWebhookToken = _generateUUID();
                }
                updateModalWebhookUrl();
            } else if (card.dataset.source === 'syslog') {
                pathGroup.classList.add('hidden');
                webhookGroup.classList.add('hidden');
                if (syslogGroup) syslogGroup.classList.remove('hidden');
                pathInputEl.removeAttribute('required');
                if (syslogHostnameEl) syslogHostnameEl.setAttribute('required', 'required');
                pathInputEl.value = '';
                loadSyslogHosts();
            } else {
                pathGroup.classList.remove('hidden');
                webhookGroup.classList.add('hidden');
                if (syslogGroup) syslogGroup.classList.add('hidden');
                pathInputEl.setAttribute('required', 'required');
                if (syslogHostnameEl) syslogHostnameEl.removeAttribute('required');
            }
        });
    });

    // ── Retour à la normale ──
    ['rule-resolution-enable', 'rule-probe-type'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('change', updateResolutionUI);
    });
    document.querySelectorAll('input[name="rule-resolution-calm-mode"], input[name="rule-resolution-ai-mode"]')
        .forEach(el => el.addEventListener('change', updateResolutionUI));
    const notifyEl = document.getElementById('rule-notify');
    if (notifyEl) notifyEl.addEventListener('change', updateResolutionUI);
    document.querySelectorAll('input[name="rule-keyword-tuning"]')
        .forEach(el => el.addEventListener('change', updateTuningUI));
    document.querySelectorAll('input[name="rule-mode"]').forEach(el => el.addEventListener('change', updateAutopilotUI));
    window.i18n?.onLanguageChange(() => { updateTuningUI(); updateAutopilotUI(); });

    const probeBtn = document.getElementById('rf-probe-test');
    if (probeBtn) probeBtn.addEventListener('click', testProbe);

    updateResolutionUI();
    setupKeywordSuggestions();
}

function _setAutopilot(on) {
    document.getElementById(on ? 'rule-autopilot' : 'rule-manual').checked = true;
    updateAutopilotUI();
}

/** Mode cards and what they show. Autopilot: only the source (the AI sets the rest), for a new rule as for an
 *  existing one. Manual: every setting; for a rule the autopilot was running, AI badges show what it set, and
 *  saving stops it. Switching back to the autopilot and saving starts it again. */
function updateAutopilotUI() {
    const on = !!document.getElementById('rule-autopilot')?.checked;
    const isNew = !document.getElementById('rule-id').value;
    const wasOn = !!(_rfAutopilot && _rfAutopilot.enabled);
    document.getElementById('rule-form')?.classList.toggle('rf--minimal', on);
    const hint = document.getElementById('rf-ap-hint');
    if (hint) {
        let text;
        if (isNew) {
            text = on ? _rfT('rf.ap_hint_new_on', "Choisis seulement le journal à surveiller. L'IA lit son historique (ou l'observe 24 h s'il n'y en a pas) et définit le nom, le contexte, les mots-clés, la fenêtre de regroupement et le délai de silence. Tout reste modifiable ensuite dans les Réglages.")
                : _rfT('rf.ap_hint_new_off', 'Tu définis toi-même les mots-clés et les réglages de la règle.');
        } else if (on) {
            text = wasOn ? _rfT('rf.ap_hint_on', "L'IA tient la règle à jour : mots-clés, contexte relu chaque semaine, fenêtre de regroupement et délai de silence. Pour voir ou modifier ce qu'elle a défini, passe en Manuel.")
                : _rfT('rf.ap_hint_enable', "En enregistrant, le pilote relit l'historique du journal et reprend la fenêtre de regroupement, le délai de silence et le contexte s'il est vide. Le nom et ce que tu as écrit restent.");
        } else {
            text = wasOn ? _rfT('rf.ap_hint_disable', "Les réglages marqués IA ont été définis par le pilote. En enregistrant en Manuel, le pilote s'arrête : tout reste tel quel et tu le gères toi-même. Tu peux le réactiver quand tu veux.")
                : _rfT('rf.ap_hint_off', 'Tu gères toi-même les réglages de cette règle. Le pilote automatique peut les reprendre à tout moment.');
        }
        hint.textContent = text;
    }
    // Keywords: typed in the form only for a new manual rule
    document.querySelectorAll('.rf-kw-field').forEach(el => el.classList.toggle('hidden', !isNew || on));
    document.getElementById('rf-kw-link')?.classList.toggle('hidden', isNew);
    document.getElementById('rf-learn-history-row')?.classList.toggle('hidden', !isNew || on);
    document.getElementById('modal-quick-templates')?.classList.toggle('hidden', !isNew || on);
    const name = document.getElementById('rule-name');
    if (name) {
        name.required = !(isNew && on);
        name.placeholder = isNew && on ? _rfT('rf.ap_name_placeholder', "Laisse vide : l'IA le choisira") : _rfT('rules.placeholder_name', 'Nom de la règle');
    }
    const context = document.getElementById('rule-context');
    if (context) {
        context.placeholder = isNew && on ? _rfT('rf.ap_context_placeholder', "Laisse vide : l'IA le rédigera d'après le journal") : _rfT('rules.placeholder_context', 'Description du contexte...');
    }
    // AI badges (visible in Manual): what the running autopilot set and still manages
    const managed = wasOn ? (_rfAutopilot.managed || []) : [];
    document.querySelectorAll('.rf-ai-badge').forEach(b => b.classList.toggle('hidden', !managed.includes(b.dataset.ap)));
    const status = document.getElementById('rf-ap-status');
    if (status) {
        const text = wasOn && on ? autopilotStatusText(_rfAutopilot) : '';
        status.textContent = text;
        status.classList.toggle('hidden', !text);
    }
}

/** One-line state of the autopilot (rule page header and settings card). */
function autopilotStatusText(a) {
    if (!a || !a.enabled) return '';
    const when = a.observe_until ? new Date(a.observe_until + 'Z').toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' }) : '';
    if (a.status === 'learning') return _rfT('rf.ap_status_learning', "Lecture de l'historique du journal…");
    if (a.status === 'observing') return _rfT('rf.ap_status_observing', "Observation jusqu'à {when} : pas assez d'historique. En attendant, la règle alerte sur les lignes de niveau erreur.").replace('{when}', when);
    if (a.status === 'error') return a.error === 'cancelled'
        ? _rfT('rf.ap_status_cancelled', "Mise en place annulée. La règle alerte sur les lignes de niveau erreur ; lance une analyse de l'historique dans l'onglet Mots-clés pour reprendre.")
        : _rfT('rf.ap_status_error', "La mise en place a échoué ({error}). Nouvel essai automatique dans quelques heures ; en attendant, la règle alerte sur les lignes de niveau erreur.").replace('{error}', a.error || '?');
    return _rfT('rf.ap_status_active', 'Règle en place. Le journal est relu chaque semaine.');
}

/** Show/hide the dependent fields of the notification and resolution sections. */
function updateResolutionUI() {
    const resEnable = document.getElementById('rule-resolution-enable');
    const resSettings = document.getElementById('resolution-settings-container');
    if (resEnable && resSettings) resSettings.classList.toggle('hidden', !resEnable.checked);

    const calmLabel = document.getElementById('rf-calm-label');
    if (calmLabel) {
        calmLabel.textContent = _radioValue('rule-resolution-calm-mode', 'adaptive') === 'adaptive'
            ? _rfT('rf.calm_minimum', 'Minimum') : _rfT('rf.calm_duration', 'Durée');
    }

    const probeType = (document.getElementById('rule-probe-type') || {}).value || 'none';
    document.querySelectorAll('.rf-probe-field').forEach(el => el.classList.toggle('hidden', probeType === 'none'));
    document.querySelectorAll('.rf-probe-http').forEach(el => el.classList.toggle('hidden', probeType !== 'http'));
    const target = document.getElementById('rule-probe-target');
    if (target) target.placeholder = probeType === 'tcp' ? 'nextcloud.local:443' : 'https://nextcloud.local/status.php';

    const aiHint = document.getElementById('rf-ai-hint');
    if (aiHint) {
        const mode = _radioValue('rule-resolution-ai-mode', 'ambiguous');
        aiHint.textContent = {
            ambiguous: _rfT('rf.ai_ambiguous_hint', "L'IA ne vérifie que les lignes de succès. Un calme complet (avec sonde OK si configurée) clôt l'incident sans appel à l'IA."),
            always: _rfT('rf.ai_always_hint', "L'IA confirme chaque clôture, y compris après un calme complet. Plus lent, plus coûteux."),
            never: _rfT('rf.ai_never_hint', "Aucun appel à l'IA : la première ligne de succès ou le calme complet clôt l'incident."),
        }[mode];
    }

    const notify = document.getElementById('rule-notify');
    document.querySelectorAll('input[name="rule-severity-threshold"]').forEach(el => { el.disabled = notify && !notify.checked; });
}

async function testProbe() {
    const out = document.getElementById('rf-probe-result');
    const body = {
        probe_type: document.getElementById('rule-probe-type').value,
        probe_target: document.getElementById('rule-probe-target').value,
        probe_expect: document.getElementById('rule-probe-expect').value,
    };
    if (!out) return;
    out.textContent = _rfT('rf.probe_testing', 'Test en cours…');
    out.className = 'form-hint';
    try {
        const res = await apiFetch('/api/rules/probe-test', { method: 'POST', body });
        out.textContent = `${res.ok ? '✓' : '✗'} ${res.detail} · ${res.latency_ms} ms`;
        out.className = 'form-hint ' + (res.ok ? 'rf-ok' : 'rf-ko');
    } catch (e) {
        out.textContent = '✗ ' + e.message;
        out.className = 'form-hint rf-ko';
    }
}

/* ── Syslog: sources already sending lines ─────────────────────────────────── */
let _syslogHostsTimer = null;

/** List the hosts that sent lines; refreshed while the syslog source is shown (to catch the first message). */
async function loadSyslogHosts() {
    const list = document.getElementById('syslog-detected-list');
    const group = document.getElementById('syslog-group');
    if (!list || !group) return;
    clearTimeout(_syslogHostsTimer);
    if (group.classList.contains('hidden') || !group.offsetParent) return;
    let hosts = [];
    try { hosts = (await apiFetch('/api/monitor/syslog/hosts')).hosts || []; } catch (_) { /* liste facultative */ }
    const editedId = parseInt(document.getElementById('rule-id').value) || null;
    const current = (document.getElementById('syslog-hostname').value || '').trim();
    list.innerHTML = hosts.length ? hosts.map(h => {
        const taken = h.rule && h.rule.id !== editedId;
        const when = new Date(h.last_seen).toLocaleString([], { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
        const meta = taken ? _rfT('rf.syslog_taken', 'déjà suivie par « {name} »').replace('{name}', h.rule.name)
            : _rfT('rf.syslog_host_meta', '{n} ligne(s) · dernière {when}').replace('{n}', h.lines).replace('{when}', when);
        return `<button type="button" class="rf-detected-item${h.hostname === current ? ' active' : ''}" data-host="${escapeHtml(h.hostname)}" ${taken ? 'disabled' : ''} title="${escapeHtml(h.last_line)}">
            <strong>${escapeHtml(h.hostname)}</strong><span>${escapeHtml(meta)}</span></button>`;
    }).join('') : `<span class="form-hint">${escapeHtml(_rfT('rf.syslog_none', "Aucune ligne reçue pour l'instant. Configure l'envoi depuis ta source : elle apparaîtra ici dès son premier message."))}</span>`;
    list.querySelectorAll('.rf-detected-item:not([disabled])').forEach(b => b.addEventListener('click', () => {
        document.getElementById('syslog-hostname').value = b.dataset.host;
        list.querySelectorAll('.rf-detected-item').forEach(x => x.classList.toggle('active', x === b));
    }));
    _syslogHostsTimer = setTimeout(loadSyslogHosts, 5000);
}

/* ── Webhook helpers ───────────────────────────────────────────────────────── */
function fallbackCopyTextToClipboard(text) {
    const textArea = document.createElement("textarea");
    textArea.value = text;
    textArea.style.top = "0";
    textArea.style.left = "0";
    textArea.style.position = "fixed";
    document.body.appendChild(textArea);
    textArea.focus();
    textArea.select();
    try {
        document.execCommand('copy');
    } catch (err) {
        console.error('Fallback copy failed', err);
    }
    document.body.removeChild(textArea);
}

function updateModalWebhookUrl() {
    if (!window._currentWebhookToken) return;
    const url = window.location.origin + '/api/webhook/logs/' + window._currentWebhookToken;
    document.getElementById('webhook-curl-cmd').textContent = url;
}

function copyModalWebhookUrl(btnElement) {
    const text = document.getElementById('webhook-curl-cmd').textContent;
    
    function showCopied() {
        if (!btnElement) return;
        const oldText = btnElement.innerHTML;
        btnElement.innerHTML = window.t ? window.t('rules.webhook_copied') || "✅ Copié !" : "✅ Copié !";
        setTimeout(() => { btnElement.innerHTML = oldText; }, 2000);
    }

    if (!navigator.clipboard) {
        fallbackCopyTextToClipboard(text);
        showCopied();
        return;
    }
    navigator.clipboard.writeText(text).then(() => {
        showCopied();
    }).catch(err => {
        console.error('Erreur de copie:', err);
        fallbackCopyTextToClipboard(text);
        showCopied();
    });
}

function copyWebhookUrl(ruleId, btnElement) {
    const url = window.location.origin + '/api/webhook/logs/' + ruleId;
    
    function showCopied() {
        if (!btnElement) return;
        const oldText = btnElement.innerHTML;
        btnElement.innerHTML = window.t ? window.t('rules.webhook_copied') || "✅ Copié !" : "✅ Copié !";
        setTimeout(() => { btnElement.innerHTML = oldText; }, 2000);
    }

    if (!navigator.clipboard) {
        fallbackCopyTextToClipboard(url);
        showCopied();
        return;
    }
    navigator.clipboard.writeText(url).then(() => {
        showCopied();
    }).catch(err => {
        console.error('Erreur de copie:', err);
        fallbackCopyTextToClipboard(url);
        showCopied();
    });
}

/* ── Reset form ────────────────────────────────────────────────────────────── */
function resetForm() {
    document.getElementById('rule-id').value = '';
    document.getElementById('rule-name').value = '';
    document.getElementById('rule-path').value = '';
    window._currentWebhookToken = null;

    const localCard = document.querySelector('.source-card[data-source="local"]');
    if (localCard) localCard.click();
    const syslogHostnameEl = document.getElementById('syslog-hostname');
    if (syslogHostnameEl) syslogHostnameEl.value = '';

    document.getElementById('rule-keywords').value = '';
    document.getElementById('rule-context').value = '';
    document.getElementById('rule-notify').checked = true;
    document.getElementById('rule-context-lines').value = '5';
    document.getElementById('rule-anti-spam').value = '60';
    _setRadio('rule-severity-threshold', 'info');
    document.getElementById('rule-inactivity-period-hours').value = '1';
    document.getElementById('rule-inactivity-notify').checked = true;
    document.getElementById('rule-excluded-patterns').value = '';

    document.getElementById('rule-resolution-enable').checked = true;
    _setRadio('rule-resolution-calm-mode', 'adaptive');
    document.getElementById('rule-resolution-timeout').value = '30';
    document.getElementById('rule-resolution-success').checked = true;
    document.getElementById('rule-resolution-learn').checked = true;
    _setRadio('rule-resolution-ai-mode', 'ambiguous');
    document.getElementById('rule-probe-type').value = 'none';
    document.getElementById('rule-probe-target').value = '';
    document.getElementById('rule-probe-expect').value = '';
    document.getElementById('rule-resolution-notify-resolved').checked = true;
    updateResolutionUI();

    closeFileBrowser();
    const previewContainer = document.getElementById('file-preview-container');
    if (previewContainer) {
        previewContainer.classList.add('hidden');
        document.getElementById('file-preview-content').innerHTML = '';
    }
    // Quick templates are offered for a new rule only
    const tpl = document.getElementById('modal-quick-templates');
    if (tpl) tpl.classList.remove('hidden');
    _setRadio('rule-keyword-tuning', 'auto');
    document.getElementById('rule-learn-history').checked = true;
    document.getElementById('rf-learn-history-row').classList.remove('hidden');
    updateTuningUI();
    _rfAutopilot = null;
    _setAutopilot(true);
}

/* ── Save rule ─────────────────────────────────────────────────────────────── */
function _ruleFormData() {
    const activeCard = document.querySelector('.source-card.kw-tab--active');
    const isWebhook = activeCard && activeCard.dataset.source === 'webhook';
    const isSyslog = activeCard && activeCard.dataset.source === 'syslog';

    let logFilePath = document.getElementById('rule-path').value;
    if (isWebhook) {
        logFilePath = '[WEBHOOK]:' + (window._currentWebhookToken || _generateUUID());
    } else if (isSyslog) {
        logFilePath = '[SYSLOG]:' + (document.getElementById('syslog-hostname').value.trim() || '*');
    }

    const resolutionOn = document.getElementById('rule-resolution-enable').checked;
    const silentAlert = document.getElementById('rule-inactivity-notify').checked;
    const data = {
        name: document.getElementById('rule-name').value.trim(),
        log_file_path: logFilePath,
        keywords: document.getElementById('rule-keywords').value.split(',').map(k => k.trim()).filter(k => k),
        application_context: document.getElementById('rule-context').value,
        notify_on_match: document.getElementById('rule-notify').checked,
        context_lines: Math.max(0, parseInt(document.getElementById('rule-context-lines').value) || 0),
        anti_spam_delay: parseInt(document.getElementById('rule-anti-spam').value) || 60,
        notify_severity_threshold: _radioValue('rule-severity-threshold', 'info'),
        // La vitalité de la source est toujours affichée ; seule la notification est optionnelle
        inactivity_warning_enabled: true,
        inactivity_period_hours: parseInt(document.getElementById('rule-inactivity-period-hours').value) || 1,
        inactivity_notify: silentAlert,
        excluded_patterns: document.getElementById('rule-excluded-patterns').value
            .split(',').map(p => p.trim()).filter(p => p),
        resolution_mode: !resolutionOn ? 'disabled'
            : (document.getElementById('rule-resolution-success').checked ? 'both' : 'timeout'),
        resolution_timeout_minutes: parseInt(document.getElementById('rule-resolution-timeout').value) || 30,
        resolution_calm_mode: _radioValue('rule-resolution-calm-mode', 'adaptive'),
        resolution_ai_mode: _radioValue('rule-resolution-ai-mode', 'ambiguous'),
        resolution_learn: document.getElementById('rule-resolution-learn').checked,
        resolution_notify_resolved: document.getElementById('rule-resolution-notify-resolved').checked,
        probe_type: document.getElementById('rule-probe-type').value,
        probe_target: document.getElementById('rule-probe-target').value,
        probe_expect: document.getElementById('rule-probe-expect').value,
        keyword_tuning_mode: _radioValue('rule-keyword-tuning', 'auto'),
        autopilot: document.getElementById('rule-autopilot').checked,
    };
    const access = [...document.querySelectorAll('#rf-access-list input[type="checkbox"]')];
    if (access.length) {
        // Tout coché : les connexions ajoutées plus tard en profitent aussi
        data.diagnostic_link_ids = access.every(c => c.checked) ? null : access.filter(c => c.checked).map(c => Number(c.value));
    }
    // Existing rule: keywords and exclusions are edited in the Keywords tab, never overwritten from here
    if (document.getElementById('rule-id').value) {
        delete data.keywords;
        delete data.excluded_patterns;
    }
    return data;
}

/** Connections the AI may use for this rule (null: all of them). The section stays hidden without any connection. */
async function renderRuleAccess(selected) {
    const section = document.getElementById('rf-access');
    if (!section) return;
    const token = (renderRuleAccess.token = (renderRuleAccess.token || 0) + 1);
    let links = [];
    try {
        links = (await apiFetch('/api/diagnostics')).links;
    } catch (_) { /* sans la liste, le choix enregistré reste tel quel */ }
    if (token !== renderRuleAccess.token) return;
    section.classList.toggle('hidden', !links.length);
    document.getElementById('rf-access-list').innerHTML = links.map(l => `<label class="rf-check">
        <input type="checkbox" value="${l.id}" ${selected === null || selected.includes(l.id) ? 'checked' : ''}>
        <span><strong>${escapeHtml(l.name)}</strong> <span class="form-hint">${l.kind === 'docker' ? 'Docker' : 'SSH'}</span></span></label>`).join('');
}

/** Hint of the keyword tuning mode; the history option is offered for a new rule only. */
function updateTuningUI() {
    const hint = document.getElementById('rf-tuning-hint');
    if (!hint) return;
    const mode = _radioValue('rule-keyword-tuning', 'auto');
    const t = (k, fb) => { const v = window.t ? window.t(k) : null; return v && v !== k ? v : fb; };
    hint.textContent = {
        auto: t('rf.tuning_auto_hint', "L'IA ajoute les mots-clés qui manquent et écarte le bruit, après des vérifications sur tes données. Chaque changement est annulable dans l'onglet Mots-clés."),
        suggest: t('rf.tuning_suggest_hint', "L'IA propose ; tu valides chaque changement dans l'onglet Mots-clés."),
        off: t('rf.tuning_off_hint', 'Les mots-clés ne changent que lorsque tu les modifies.'),
    }[mode];
    document.getElementById('rf-learn-history-row')?.classList.toggle('dimmed', mode === 'off');
}

async function saveRule() {
    const id = document.getElementById('rule-id').value;
    const data = _ruleFormData();
    if (!id) data.learn_history = document.getElementById('rule-learn-history').checked;

    try {
        let savedId = id;
        if (id) {
            await apiFetch(`/api/rules/${id}`, { method: 'PUT', body: data });
        } else {
            const res = await apiFetch('/api/rules', { method: 'POST', body: { ...data, enabled: true } });
            savedId = res.id;
        }

        if (_ruleFormOnSave) _ruleFormOnSave(parseInt(savedId), !id);
    } catch (error) {
        console.error('Erreur sauvegarde règle:', error);
        alert((window.t ? window.t('common.error') : 'Erreur') + ': ' + error.message);
    }
}

/* ── Edit rule (fill the form) ─────────────────────────────────────────────── */
async function editRule(id) {
    try {
        const rule = await apiFetch(`/api/rules/${id}`);
        document.getElementById('rule-id').value = rule.id;
        document.getElementById('rule-name').value = rule.name;

        if (rule.log_file_path && rule.log_file_path.startsWith('[WEBHOOK]')) {
            window._currentWebhookToken = rule.log_file_path.split(':')[1] || rule.id;
            document.querySelector('.source-card[data-source="webhook"]')?.click();
        } else if (rule.log_file_path && rule.log_file_path.startsWith('[SYSLOG]')) {
            document.querySelector('.source-card[data-source="syslog"]')?.click();
            document.getElementById('syslog-hostname').value = rule.log_file_path.split(':')[1] || '*';
        } else {
            document.querySelector('.source-card[data-source="local"]')?.click();
            document.getElementById('rule-path').value = rule.log_file_path;
        }

        document.getElementById('rule-keywords').value = rule.keywords.join(', ');
        document.getElementById('rule-context').value = rule.application_context || '';
        document.getElementById('rule-notify').checked = rule.notify_on_match;
        document.getElementById('rule-context-lines').value = rule.context_lines ?? 5;
        document.getElementById('rule-anti-spam').value = rule.anti_spam_delay || 60;
        _setRadio('rule-severity-threshold', rule.notify_severity_threshold || 'info');
        document.getElementById('rule-inactivity-period-hours').value = rule.inactivity_period_hours || 1;
        document.getElementById('rule-inactivity-notify').checked = !!(rule.inactivity_warning_enabled && rule.inactivity_notify !== false);
        document.getElementById('rule-excluded-patterns').value = (rule.excluded_patterns || []).join(', ');

        const hasRes = rule.resolution_mode !== 'disabled';
        document.getElementById('rule-resolution-enable').checked = hasRes;
        _setRadio('rule-resolution-calm-mode', rule.resolution_calm_mode || 'adaptive');
        document.getElementById('rule-resolution-timeout').value = rule.resolution_timeout_minutes || 30;
        document.getElementById('rule-resolution-success').checked = ['pattern', 'both'].includes(rule.resolution_mode);
        document.getElementById('rule-resolution-learn').checked = rule.resolution_learn !== false;
        _setRadio('rule-resolution-ai-mode', rule.resolution_ai_mode || 'ambiguous');
        renderRuleAccess(rule.diagnostic_link_ids ?? null);
        document.getElementById('rule-probe-type').value = rule.probe_type || 'none';
        document.getElementById('rule-probe-target').value = rule.probe_target || '';
        document.getElementById('rule-probe-expect').value = rule.probe_expect || '';
        document.getElementById('rule-resolution-notify-resolved').checked = rule.resolution_notify_resolved !== false;
        updateResolutionUI();

        // Quick templates are for new rules only
        document.getElementById('modal-quick-templates')?.classList.add('hidden');
        closeFileBrowser();
        if (!rule.log_file_path.startsWith('[WEBHOOK]') && !rule.log_file_path.startsWith('[SYSLOG]')) {
            fetchFilePreview(rule.log_file_path);
        } else {
            document.getElementById('file-preview-container')?.classList.add('hidden');
        }

        _setRadio('rule-keyword-tuning', rule.keyword_tuning_mode || 'auto');
        document.getElementById('rf-learn-history-row').classList.add('hidden');
        updateTuningUI();
        _rfAutopilot = rule.autopilot || null;
        _setAutopilot(!!(_rfAutopilot && _rfAutopilot.enabled));
        return rule;
    } catch (error) {
        console.error('Erreur chargement règle:', error);
        return null;
    }
}

/* ── File browser ──────────────────────────────────────────────────────────── */
let _fileRootsCache = null;
let _fileBrowserCurrentPath = null;

function setupFileBrowser() {
    const browseBtn = document.getElementById('browse-logs-btn');
    const browser = document.getElementById('file-browser');
    const closeBtn = document.getElementById('file-browser-close');
    const list = document.getElementById('file-browser-list');
    const breadcrumb = document.getElementById('file-browser-breadcrumb');
    const showHidden = document.getElementById('file-browser-show-hidden');

    if (!browseBtn || !browser || !closeBtn || !list || !breadcrumb || !showHidden) return;

    browseBtn.addEventListener('click', async () => {
        browser.classList.toggle('hidden');
        if (browser.classList.contains('hidden')) return;

        await ensureFileRoots();
        const desired = getSuggestedBrowsePath();
        const startPath = desired || (_fileRootsCache && _fileRootsCache[0]) || '/logs';
        await browsePath(startPath);
    });

    closeBtn.addEventListener('click', () => closeFileBrowser());

    showHidden.addEventListener('change', async () => {
        if (!_fileBrowserCurrentPath) return;
        await browsePath(_fileBrowserCurrentPath);
    });

    list.addEventListener('click', async (e) => {
        const row = e.target.closest('[data-path]');
        if (!row) return;
        const path = row.getAttribute('data-path');
        const isDir = row.getAttribute('data-is-dir') === 'true';
        if (!path) return;

        if (isDir) {
            await browsePath(path);
        } else {
            document.getElementById('rule-path').value = path;
            closeFileBrowser();
            fetchFilePreview(path);
        }
    });

    breadcrumb.addEventListener('click', async (e) => {
        const el = e.target.closest('[data-root]');
        if (!el) return;
        const root = el.getAttribute('data-root');
        if (!root) return;
        await browsePath(root);
    });
}

function closeFileBrowser() {
    const browser = document.getElementById('file-browser');
    if (!browser) return;
    browser.classList.add('hidden');
}

async function ensureFileRoots() {
    if (_fileRootsCache) return _fileRootsCache;
    try {
        const res = await apiFetch('/api/files/roots');
        _fileRootsCache = (res && res.roots) || [];
    } catch (e) {
        _fileRootsCache = [];
    }
    return _fileRootsCache;
}

function getSuggestedBrowsePath() {
    const input = document.getElementById('rule-path');
    if (!input) return null;
    const v = (input.value || '').trim();
    if (!v) return null;
    // If user entered a file path, start from its parent folder.
    const slash = Math.max(v.lastIndexOf('/'), v.lastIndexOf('\\'));
    if (slash <= 0) return v;
    return v.slice(0, slash);
}

async function browsePath(path) {
    const list = document.getElementById('file-browser-list');
    const breadcrumb = document.getElementById('file-browser-breadcrumb');
    const showHidden = document.getElementById('file-browser-show-hidden');
    if (!list || !breadcrumb || !showHidden) return;

    list.innerHTML = `<div class="loading">${window.t ? window.t('common.loading') : 'Chargement...'}</div>`;

    try {
        const res = await apiFetch(`/api/files/browse?path=${encodeURIComponent(path)}&show_hidden=${showHidden.checked ? 'true' : 'false'}`);
        _fileBrowserCurrentPath = res.path;

        const roots = await ensureFileRoots();
        const rootsLinks = (roots || [])
            .map(r => `<span class="chip" data-root="${escapeHtml(r)}">📌 ${escapeHtml(r)}</span>`)
            .join(' ');

        breadcrumb.innerHTML = `
            <div><strong>${escapeHtml(res.path)}</strong></div>
            ${rootsLinks ? `<div class="form-hint">Racines: ${rootsLinks}</div>` : ''}
        `;

        const rows = [];
        if (res.parent) {
            rows.push(renderBrowserRow({ name: '.. (parent)', path: res.parent, is_dir: true, readable: true }, true));
        }

        for (const e of res.entries) {
            rows.push(renderBrowserRow(e, false));
        }

        list.innerHTML = rows.join('') || `<div class="loading">${window.t ? window.t('rules.folder_empty') : 'Empty folder'}</div>`;
    } catch (error) {
        console.error('Erreur browse:', error);
        list.innerHTML = `<div class="loading">${window.t ? window.t('common.error') : 'Erreur'}: ${escapeHtml(error.message || 'Impossible de lister ce dossier')}</div>`;
    }
}

function renderBrowserRow(entry, isParent) {
    const icon = entry.is_dir ? '📁' : '📄';
    const meta = entry.is_dir ? (window.t ? window.t('rules.folder') : 'Folder') : (window.t ? window.t('rules.file') : 'File');
    const disabled = entry.readable === false ? 'opacity:0.6; pointer-events:none;' : '';
    const name = isParent ? entry.name : entry.name;
    return `
        <div class="file-browser-item" style="${disabled}" data-path="${escapeHtml(entry.path)}" data-is-dir="${entry.is_dir ? 'true' : 'false'}">
            <div class="left">
                <span>${icon}</span>
                <span class="name">${escapeHtml(name)}</span>
            </div>
            <div class="meta">${meta}${entry.readable === false ? ` • ${window.t ? window.t('rules.unreadable') : 'unreadable'}` : ''}</div>
        </div>
    `;
}

async function fetchFilePreview(path) {
    const container = document.getElementById('file-preview-container');
    const content = document.getElementById('file-preview-content');
    if (!container || !content) return;

    if (!path || path.trim() === '') {
        container.classList.add('hidden');
        return;
    }

    container.classList.remove('hidden');
    content.innerHTML = `<em>${window.t ? window.t('rules.loading_preview') : 'Loading preview...'}</em>`;

    try {
        const res = await apiFetch(`/api/files/tail?path=${encodeURIComponent(path)}&lines=10`);
        if (res.lines && res.lines.length > 0) {
            content.innerHTML = res.lines.map(l => escapeHtml(typeof l === 'string' ? l : l.text)).join('<br>');
        } else {
            content.innerHTML = `<em>${window.t ? window.t('rules.file_empty_preview') : 'Empty file.'}</em>`;
        }
        content.scrollTop = content.scrollHeight;
    } catch (e) {
        content.innerHTML = `<em style="color: var(--danger)">${window.t ? window.t('rules.preview_unavailable') : 'Preview unavailable:'} ${escapeHtml(e.message)}</em>`;
    }
}

/* ── Quick templates ──────────────────────────────────────────────────────── */
function applyTemplate(type) {
    resetForm();
    // A template brings its own keywords: manual rule
    _setAutopilot(false);
    const templates = {
        'auth': {
            name: window.t ? window.t('rules.template_auth') : 'Security SSH / Connections',
            path: '/system-logs/auth.log',
            keywords: 'failed, Accepted, invalid user, authentication failure, sudo',
            context: 'SSH login and sudo usage monitoring on Ubuntu.'
        },
        'syslog': {
            name: window.t ? window.t('rules.template_syslog') : 'System Stability',
            path: '/system-logs/syslog',
            keywords: 'error, failed, fatal, critical, oom-killer, stopped',
            context: 'General Ubuntu system logs. Monitors service crashes and system errors.'
        },
        'journald': {
            name: window.t ? window.t('rules.template_journald') : 'Journald (Docker Relay)',
            path: '/logs/host_system_journal.log',
            keywords: 'error, fatal, panic, critical, failed',
            context: 'Relay of Systemd binary logs (journalctl) to a readable text file.'
        }
    };

    const t = templates[type];
    if (t) {
        document.getElementById('rule-name').value = t.name;
        document.getElementById('rule-path').value = t.path;
        document.getElementById('rule-keywords').value = t.keywords;
        document.getElementById('rule-context').value = t.context;

        fetchFilePreview(t.path);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    if (document.getElementById('rule-form') && !document.getElementById('rule-id').value) renderRuleAccess(null);
});
