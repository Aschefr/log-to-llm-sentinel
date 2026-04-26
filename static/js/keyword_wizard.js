/**
 * keyword_wizard.js
 * Inline keyword auto-learning wizard for the rule creation/edit modal.
 * Attaches to #kw-wizard inside #rule-form.
 */

/* ── State ──────────────────────────────────────────────────────────────── */
let _wizardSessionId = null;
let _wizardPollTimer = null;
let _wizardFinalKeywords = [];

/* ── Granularity suggestion table ───────────────────────────────────────── */
const GRANULARITY_OPTIONS = [
    { value: 15 * 60,       label: '15 minutes' },
    { value: 30 * 60,       label: '30 minutes' },
    { value: 60 * 60,       label: '1 heure' },
    { value: 2 * 3600,      label: '2 heures' },
    { value: 6 * 3600,      label: '6 heures' },
    { value: 12 * 3600,     label: '12 heures' },
    { value: 24 * 3600,     label: '1 jour' },
    { value: 7 * 86400,     label: '1 semaine' },
    { value: 14 * 86400,    label: '2 semaines' },
];

function _suggestGranularity(durationS) {
    if (durationS <= 6 * 3600)   return 15 * 60;
    if (durationS <= 24 * 3600)  return 3600;
    if (durationS <= 7 * 86400)  return 6 * 3600;
    if (durationS <= 30 * 86400) return 86400;
    return 7 * 86400;
}

function _allowedGranularities(durationS) {
    if (durationS <= 6 * 3600)   return [15*60, 30*60, 3600];
    if (durationS <= 24 * 3600)  return [30*60, 3600, 2*3600, 6*3600];
    if (durationS <= 7 * 86400)  return [3600, 6*3600, 12*3600, 86400];
    if (durationS <= 30 * 86400) return [6*3600, 12*3600, 86400, 7*86400];
    return [86400, 7*86400, 14*86400];
}

/* ── Main entry-point called from rules.js ──────────────────────────────── */
function kwWizardOpen() {
    const wizard = document.getElementById('kw-wizard');
    if (!wizard) return;
    _wizardReset();
    wizard.classList.remove('kw-wizard--hidden');
    _renderConfigPhase();
}

function kwWizardClose() {
    const wizard = document.getElementById('kw-wizard');
    if (!wizard) return;
    _wizardStop();
    wizard.classList.add('kw-wizard--hidden');
    _wizardReset();
}

/* ── Internal helpers ───────────────────────────────────────────────────── */
function _wizardReset() {
    _wizardSessionId = null;
    _wizardFinalKeywords = [];
    _wizardStop();
}

function _wizardStop() {
    if (_wizardPollTimer) {
        clearInterval(_wizardPollTimer);
        _wizardPollTimer = null;
    }
}

function _wizardBody() {
    return document.getElementById('kw-wizard-body');
}

function _renderConfigPhase() {
    const logPath = (document.getElementById('rule-path') || {}).value || '';

    // Default period: last 24h in local time
    const now = new Date();
    const yesterday = new Date(now - 24 * 3600 * 1000);
    const toLocal = d => {
        const pad = n => String(n).padStart(2, '0');
        return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
    };

    _wizardBody().innerHTML = `
        <div class="kw-phase-steps">
            <span class="kw-step kw-step--active">1 Config</span>
            <span class="kw-step-sep">›</span>
            <span class="kw-step">2 Scan</span>
            <span class="kw-step-sep">›</span>
            <span class="kw-step">3 Raffinement</span>
            <span class="kw-step-sep">›</span>
            <span class="kw-step">4 Validé</span>
        </div>

        <div class="kw-config-grid">
            <label class="kw-label">Période d'analyse <span style="opacity:.5;font-size:.75em">(heure locale)</span></label>
            <div class="kw-period-row">
                <input type="datetime-local" id="kw-period-start" value="${toLocal(yesterday)}" class="kw-input" />
                <span style="opacity:.5">→</span>
                <input type="datetime-local" id="kw-period-end"   value="${toLocal(now)}"       class="kw-input" />
            </div>

            <label class="kw-label" style="margin-top:.75rem">
                Granularité (taille d'un paquet)
                <span id="kw-granularity-hint" class="kw-hint"></span>
            </label>
            <select id="kw-granularity" class="kw-input"></select>

            <div id="kw-packets-estimate" class="kw-hint" style="margin-top:.35rem"></div>

            <div id="kw-config-limit-hint" class="kw-hint" style="margin-top:.35rem;opacity:.55">
                Limite par paquet : récupération…
            </div>
        </div>

        <div class="kw-actions" style="margin-top:1rem">
            <button type="button" id="kw-start-btn" class="btn btn-primary btn-sm">▶ Lancer l'analyse</button>
            <button type="button" id="kw-cancel-btn" class="btn btn-secondary btn-sm" onclick="kwWizardClose()">✕ Annuler</button>
        </div>
    `;

    _setupConfigListeners();
    _refreshGranularitySelect();
    _fetchMaxChars();
}

function _setupConfigListeners() {
    const startInput = document.getElementById('kw-period-start');
    const endInput   = document.getElementById('kw-period-end');
    const granSel    = document.getElementById('kw-granularity');
    const startBtn   = document.getElementById('kw-start-btn');

    const refresh = () => { _refreshGranularitySelect(); _updateEstimate(); };
    startInput.addEventListener('change', refresh);
    endInput.addEventListener('change', refresh);
    granSel.addEventListener('change', _updateEstimate);
    startBtn.addEventListener('click', _launchSession);
}

function _durationS() {
    const s = document.getElementById('kw-period-start');
    const e = document.getElementById('kw-period-end');
    if (!s || !e || !s.value || !e.value) return 86400;
    const diff = (new Date(e.value) - new Date(s.value)) / 1000;
    return diff > 0 ? diff : 86400;
}

function _refreshGranularitySelect() {
    const sel = document.getElementById('kw-granularity');
    if (!sel) return;
    const dur = _durationS();
    const allowed = _allowedGranularities(dur);
    const suggested = _suggestGranularity(dur);
    const current = parseInt(sel.value) || 0;

    sel.innerHTML = GRANULARITY_OPTIONS
        .filter(o => allowed.includes(o.value))
        .map(o => `<option value="${o.value}" ${o.value === suggested ? 'data-suggested="1"' : ''}>${o.label}${o.value === suggested ? ' ★' : ''}</option>`)
        .join('');

    // Keep previous selection if still available
    if (current && [...sel.options].some(o => parseInt(o.value) === current)) {
        sel.value = current;
    } else {
        sel.value = suggested;
    }

    const hint = document.getElementById('kw-granularity-hint');
    if (hint) hint.textContent = `(suggestion : ${GRANULARITY_OPTIONS.find(o=>o.value===suggested)?.label || '?'})`;

    _updateEstimate();
}

function _updateEstimate() {
    const el = document.getElementById('kw-packets-estimate');
    if (!el) return;
    const dur = _durationS();
    const gran = parseInt((document.getElementById('kw-granularity') || {}).value) || 3600;
    const n = Math.max(1, Math.ceil(dur / gran));
    el.textContent = `≈ ${n} paquet${n > 1 ? 's' : ''} à traiter`;
}

async function _fetchMaxChars() {
    const el = document.getElementById('kw-config-limit-hint');
    if (!el) return;
    try {
        const cfg = await fetch('/api/config').then(r => r.json());
        const max = cfg.max_log_chars || 5000;
        el.textContent = `Limite par paquet : ${max.toLocaleString()} caractères (config globale)`;
    } catch {
        el.textContent = 'Limite par paquet : 5 000 caractères (défaut)';
    }
}

async function _launchSession() {
    const logPath = (document.getElementById('rule-path') || {}).value || '';
    if (!logPath) {
        alert('Veuillez d\'abord saisir le chemin du fichier log.');
        return;
    }

    const ruleIdEl = document.getElementById('rule-id');
    const ruleId = ruleIdEl && ruleIdEl.value ? parseInt(ruleIdEl.value) : null;

    const periodStart = document.getElementById('kw-period-start').value;
    const periodEnd   = document.getElementById('kw-period-end').value;
    const granularity = parseInt(document.getElementById('kw-granularity').value);

    if (!periodStart || !periodEnd || new Date(periodEnd) <= new Date(periodStart)) {
        alert('Période invalide — la date de fin doit être après la date de début.');
        return;
    }

    // Convert local datetime-local to ISO UTC
    const toUTC = localStr => new Date(localStr).toISOString();

    try {
        const res = await fetch('/api/keyword-learning/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                rule_id: ruleId,
                log_file_path: logPath,
                period_start: toUTC(periodStart),
                period_end:   toUTC(periodEnd),
                granularity_s: granularity,
            }),
        }).then(r => r.json());

        if (!res.session_id) throw new Error('Réponse inattendue du serveur');
        _wizardSessionId = res.session_id;
        _renderScanPhase();
        _startPolling();

        // Lock keyword input during learning
        const kwInput = document.getElementById('rule-keywords');
        if (kwInput) kwInput.setAttribute('readonly', 'readonly');
    } catch (e) {
        alert('Erreur lancement : ' + e.message);
    }
}

/* ── Phase 2 : Scan ─────────────────────────────────────────────────────── */
function _renderScanPhase() {
    _wizardBody().innerHTML = `
        <div class="kw-phase-steps">
            <span class="kw-step kw-step--done">1 Config</span>
            <span class="kw-step-sep">›</span>
            <span class="kw-step kw-step--active">2 Scan</span>
            <span class="kw-step-sep">›</span>
            <span class="kw-step">3 Raffinement</span>
            <span class="kw-step-sep">›</span>
            <span class="kw-step">4 Validé</span>
        </div>
        <div id="kw-progress-wrap" style="margin:.75rem 0">
            <div class="kw-progress-bar-track"><div class="kw-progress-bar" id="kw-pbar" style="width:0%"></div></div>
            <div id="kw-progress-label" class="kw-hint" style="margin-top:.3rem">Initialisation…</div>
        </div>
        <div id="kw-current-window" class="kw-hint" style="margin-bottom:.4rem"></div>
        <div class="kw-log-scroll" id="kw-log-scroll">
            <div id="kw-log-content" style="font-size:.7rem;opacity:.8"></div>
        </div>
        <div style="margin-top:.6rem">
            <strong class="kw-label">Candidats accumulés (<span id="kw-raw-count">0</span>)</strong>
            <div id="kw-raw-tags" class="kw-tags-row" style="margin-top:.3rem"></div>
        </div>
        <div class="kw-actions" style="margin-top:.75rem">
            <button type="button" class="btn btn-secondary btn-sm" onclick="_cancelWizardSession()">✕ Annuler</button>
        </div>
    `;
}

/* ── Phase 3 : Refinement ───────────────────────────────────────────────── */
function _renderRefinePhase(rawKeywords) {
    _wizardBody().innerHTML = `
        <div class="kw-phase-steps">
            <span class="kw-step kw-step--done">1 Config</span>
            <span class="kw-step-sep">›</span>
            <span class="kw-step kw-step--done">2 Scan</span>
            <span class="kw-step-sep">›</span>
            <span class="kw-step kw-step--active">3 Raffinement</span>
            <span class="kw-step-sep">›</span>
            <span class="kw-step">4 Validé</span>
        </div>
        <div class="kw-hint" style="margin:.6rem 0">
            🧠 L'IA épure ${rawKeywords.length} candidat(s)…
        </div>
        <div id="kw-rationale-list" class="kw-rationale-table"></div>
        <div class="kw-actions" style="margin-top:.75rem">
            <button type="button" class="btn btn-secondary btn-sm" onclick="_cancelWizardSession()">✕ Annuler</button>
        </div>
    `;
}

/* ── Phase 4 : Validated ────────────────────────────────────────────────── */
function _renderValidatedPhase(finalKeywords, rationale) {
    _wizardFinalKeywords = [...finalKeywords];

    // Apply to keyword input immediately
    const kwInput = document.getElementById('rule-keywords');
    if (kwInput) {
        kwInput.removeAttribute('readonly');
        kwInput.value = finalKeywords.join(', ');
    }

    _wizardBody().innerHTML = `
        <div class="kw-phase-steps">
            <span class="kw-step kw-step--done">1 Config</span>
            <span class="kw-step-sep">›</span>
            <span class="kw-step kw-step--done">2 Scan</span>
            <span class="kw-step-sep">›</span>
            <span class="kw-step kw-step--done">3 Raffinement</span>
            <span class="kw-step-sep">›</span>
            <span class="kw-step kw-step--active">4 Validé</span>
        </div>
        <div class="kw-status-validated">
            ✅ Mots-clés appliqués à la règle
        </div>
        <div id="kw-final-tags" class="kw-tags-row" style="margin:.6rem 0"></div>
        <div class="kw-add-row" style="margin-bottom:.6rem">
            <input type="text" id="kw-add-input" placeholder="Ajouter un mot-clé…" class="kw-input" style="flex:1;max-width:220px" />
            <button type="button" class="btn btn-secondary btn-sm" onclick="_kwWizardAddTag()">+ Ajouter</button>
        </div>
        <div class="kw-hint" style="margin-bottom:.75rem;opacity:.6">
            ℹ️ Vous pouvez modifier la liste, supprimer des tags ou annuler le learning depuis la carte règle.
        </div>
        <div class="kw-actions">
            <button type="button" class="btn btn-secondary btn-sm" id="kw-revaluate-btn">↺ Ré-évaluer</button>
            <button type="button" class="btn btn-secondary btn-sm" onclick="_cancelWizardSession()">✕ Annuler le learning</button>
            <button type="button" class="btn btn-primary btn-sm" onclick="kwWizardClose()">✓ Fermer</button>
        </div>
    `;

    _renderFinalTags();
    document.getElementById('kw-revaluate-btn').addEventListener('click', _revaluate);
    document.getElementById('kw-add-input').addEventListener('keydown', e => {
        if (e.key === 'Enter') { e.preventDefault(); _kwWizardAddTag(); }
    });
}

function _renderFinalTags() {
    const container = document.getElementById('kw-final-tags');
    if (!container) return;
    container.innerHTML = _wizardFinalKeywords.map((kw, i) => `
        <span class="kw-tag">
            ${_esc(kw)}
            <span class="kw-tag-remove" onclick="_kwWizardRemoveTag(${i})">×</span>
        </span>
    `).join('');
}

function _kwWizardRemoveTag(idx) {
    _wizardFinalKeywords.splice(idx, 1);
    _renderFinalTags();
    const kwInput = document.getElementById('rule-keywords');
    if (kwInput) kwInput.value = _wizardFinalKeywords.join(', ');
}

function _kwWizardAddTag() {
    const input = document.getElementById('kw-add-input');
    if (!input) return;
    const val = input.value.trim();
    if (!val) return;
    val.split(',').map(s => s.trim()).filter(Boolean).forEach(k => {
        if (!_wizardFinalKeywords.includes(k)) _wizardFinalKeywords.push(k);
    });
    input.value = '';
    _renderFinalTags();
    const kwInput = document.getElementById('rule-keywords');
    if (kwInput) kwInput.value = _wizardFinalKeywords.join(', ');
}

async function _revaluate() {
    if (!_wizardSessionId) return;
    _renderRefinePhase(_wizardFinalKeywords);
    try {
        await fetch(`/api/keyword-learning/${_wizardSessionId}/revaluate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ keywords: _wizardFinalKeywords }),
        });
        _startPolling();
    } catch (e) {
        console.error('Revaluate error:', e);
    }
}

/* ── Polling ────────────────────────────────────────────────────────────── */
function _startPolling() {
    _wizardStop();
    _wizardPollTimer = setInterval(_pollStatus, 2000);
}

async function _pollStatus() {
    if (!_wizardSessionId) return;
    try {
        const data = await fetch(`/api/keyword-learning/${_wizardSessionId}/status`).then(r => r.json());
        _applyStatus(data);
    } catch (e) {
        console.warn('Poll error:', e);
    }
}

function _applyStatus(data) {
    const status = data.status;

    if (status === 'scanning') {
        _updateScanUI(data);
    } else if (status === 'refining' || status === 'refining_done') {
        _updateRefineUI(data);
    } else if (status === 'validated') {
        _wizardStop();
        _renderValidatedPhase(data.final_keywords, data.refine_rationale);
    } else if (status === 'error') {
        _wizardStop();
        const body = _wizardBody();
        if (body) body.innerHTML = `<div style="color:var(--danger);padding:.75rem">
            ⚠️ Erreur : ${_esc(data.error_message || 'Inconnue')}
            <br><button class="btn btn-secondary btn-sm" style="margin-top:.5rem" onclick="kwWizardClose()">Fermer</button>
        </div>`;
        const kwInput = document.getElementById('rule-keywords');
        if (kwInput) kwInput.removeAttribute('readonly');
    } else if (status === 'reverted') {
        _wizardStop();
        kwWizardClose();
    }
}

function _updateScanUI(data) {
    const pbar = document.getElementById('kw-pbar');
    const label = document.getElementById('kw-progress-label');
    const win = document.getElementById('kw-current-window');
    const logContent = document.getElementById('kw-log-content');
    const rawCount = document.getElementById('kw-raw-count');
    const rawTags = document.getElementById('kw-raw-tags');

    if (!pbar) { _renderScanPhase(); return; }

    const pct = data.total_packets > 0
        ? Math.round((data.completed_packets / data.total_packets) * 100)
        : 0;

    pbar.style.width = pct + '%';
    if (label) label.textContent = `${data.completed_packets} / ${data.total_packets} paquets (${pct}%)`;
    if (win && data.current_window) win.textContent = '📦 ' + data.current_window;

    if (logContent && data.current_packet_keywords && data.current_packet_keywords.length) {
        const line = data.current_packet_keywords.join(', ');
        logContent.innerHTML += `<div>→ ${_esc(line)}</div>`;
        const scroll = document.getElementById('kw-log-scroll');
        if (scroll) scroll.scrollTop = scroll.scrollHeight;
    }

    if (rawCount) rawCount.textContent = data.raw_keywords.length;
    if (rawTags) {
        rawTags.innerHTML = data.raw_keywords.map(kw =>
            `<span class="kw-tag kw-tag--raw">${_esc(kw)}</span>`
        ).join('');
    }
}

function _updateRefineUI(data) {
    const list = document.getElementById('kw-rationale-list');
    if (!list) { _renderRefinePhase(data.raw_keywords); return; }
    const rationale = data.refine_rationale || {};
    const finals = data.final_keywords || [];
    // Show rationale rows
    if (Object.keys(rationale).length) {
        list.innerHTML = Object.entries(rationale).map(([kw, reason]) => {
            const kept = finals.includes(kw);
            return `<div class="kw-rationale-row">
                <span class="kw-rationale-icon">${kept ? '✓' : '✗'}</span>
                <span class="kw-rationale-kw ${kept ? 'kw-kept' : 'kw-removed'}">${_esc(kw)}</span>
                <span class="kw-rationale-reason">${_esc(reason)}</span>
            </div>`;
        }).join('');
    }
}

/* ── Cancel ─────────────────────────────────────────────────────────────── */
async function _cancelWizardSession() {
    if (_wizardSessionId) {
        try {
            await fetch(`/api/keyword-learning/${_wizardSessionId}`, { method: 'DELETE' });
        } catch {}
    }
    const kwInput = document.getElementById('rule-keywords');
    if (kwInput) kwInput.removeAttribute('readonly');
    kwWizardClose();
}

/* ── Utility ─────────────────────────────────────────────────────────────── */
function _esc(text) {
    const d = document.createElement('div');
    d.textContent = String(text || '');
    return d.innerHTML;
}
