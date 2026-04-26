/**
 * keyword_wizard.js
 * Keyword auto-learning wizard for the rule creation/edit modal.
 *
 * UX flow:
 * 1. User fills Name + Log path in rule form
 * 2. Clicks "🤖 Auto-apprendre" → wizard panel opens, "Enregistrer" is hidden
 * 3. User configures period + granularity (launch button grayed until name+path valid)
 * 4. "▶ Lancer l'analyse" → saves rule → starts session → closes modal
 * 5. Progress is shown in the rule card on the rules page
 */

/* ── State ─────────────────────────────────────────────────────────────── */
let _wizardSessionId    = null;
let _wizardPollTimer    = null;
let _wizardCardTimer    = null;
let _wizardFinalKeywords = [];

/* ── Granularity options ────────────────────────────────────────────────── */
const GRANULARITY_OPTIONS = [
    { value: 15 * 60,    label: '15 minutes' },
    { value: 30 * 60,    label: '30 minutes' },
    { value: 60 * 60,    label: '1 heure'    },
    { value: 2  * 3600,  label: '2 heures'   },
    { value: 6  * 3600,  label: '6 heures'   },
    { value: 12 * 3600,  label: '12 heures'  },
    { value: 24 * 3600,  label: '1 jour'     },
    { value: 7  * 86400, label: '1 semaine'  },
    { value: 14 * 86400, label: '2 semaines' },
];

function _suggestGranularity(durationS) {
    if (durationS <= 6  * 3600)  return 15 * 60;
    if (durationS <= 24 * 3600)  return 3600;
    if (durationS <= 7  * 86400) return 6 * 3600;
    if (durationS <= 30 * 86400) return 86400;
    return 7 * 86400;
}

function _allowedGranularities(durationS) {
    if (durationS <= 6  * 3600)  return [15*60, 30*60, 3600];
    if (durationS <= 24 * 3600)  return [30*60, 3600, 2*3600, 6*3600];
    if (durationS <= 7  * 86400) return [3600, 6*3600, 12*3600, 86400];
    if (durationS <= 30 * 86400) return [6*3600, 12*3600, 86400, 7*86400];
    return [86400, 7*86400, 14*86400];
}

/* ── Tab switch (replaces kwWizardOpen / kwWizardClose) ─────────────────── */
function kwTabSwitch(tab) {
    const manualPanel = document.getElementById('kw-panel-manual');
    const autoPanel   = document.getElementById('kw-panel-auto');
    const manualTab   = document.getElementById('kw-tab-manual');
    const autoTab     = document.getElementById('kw-tab-auto');
    const origSave    = document.querySelector('#rule-form .form-actions button[type="submit"]');
    const launchBtn   = document.getElementById('kw-launch-main-btn');

    if (tab === 'auto') {
        // Switch to auto-learning panel
        manualPanel.classList.add('kw-tab-panel--hidden');
        autoPanel.classList.remove('kw-tab-panel--hidden');
        manualTab.classList.remove('kw-tab--active');
        autoTab.classList.add('kw-tab--active');
        manualTab.setAttribute('aria-selected', 'false');
        autoTab.setAttribute('aria-selected', 'true');

        // Swap buttons
        if (origSave)  origSave.classList.add('hidden');
        if (launchBtn) launchBtn.classList.remove('hidden');

        // Render wizard if body is empty
        const body = document.getElementById('kw-wizard-body');
        if (body && !body.innerHTML.trim()) _renderConfigPhase();
        _watchFormValidation();
    } else {
        // Switch to manual panel
        autoPanel.classList.add('kw-tab-panel--hidden');
        manualPanel.classList.remove('kw-tab-panel--hidden');
        autoTab.classList.remove('kw-tab--active');
        manualTab.classList.add('kw-tab--active');
        autoTab.setAttribute('aria-selected', 'false');
        manualTab.setAttribute('aria-selected', 'true');

        // Restore buttons
        if (origSave)  origSave.classList.remove('hidden');
        if (launchBtn) launchBtn.classList.add('hidden');

        // Re-enable keyword input
        const kwInput = document.getElementById('rule-keywords');
        if (kwInput) kwInput.removeAttribute('readonly');
    }
}

/* Keep legacy aliases so resetForm() in rules.js still works */
function kwWizardOpen()  { kwTabSwitch('auto');   }
function kwWizardClose() { kwTabSwitch('manual'); }

/* Called by rules.js resetForm() to fully reset the tab state */
function kwWizardReset() {
    _stopAllPolling();
    _wizardReset();
    // Reset tab to manual
    const body = document.getElementById('kw-wizard-body');
    if (body) body.innerHTML = '';
    kwTabSwitch('manual');
}


/* ── Internal helpers ───────────────────────────────────────────────────── */
function _wizardReset() {
    _wizardSessionId     = null;
    _wizardFinalKeywords = [];
    _stopAllPolling();
}

function _stopAllPolling() {
    if (_wizardPollTimer) { clearInterval(_wizardPollTimer); _wizardPollTimer = null; }
    if (_wizardCardTimer) { clearInterval(_wizardCardTimer); _wizardCardTimer = null; }
}

function _wizardBody() { return document.getElementById('kw-wizard-body'); }

/* ── Form validation ────────────────────────────────────────────────────── */
function _watchFormValidation() {
    const nameInput = document.getElementById('rule-name');
    const pathInput = document.getElementById('rule-path');
    const check = () => _updateLaunchBtn();
    if (nameInput) nameInput.addEventListener('input', check);
    if (pathInput) pathInput.addEventListener('input', check);
    // Also watch when the file browser selects a path
    if (pathInput) new MutationObserver(check).observe(pathInput, { attributes: true, attributeFilter: ['value'] });
    _updateLaunchBtn();
}

function _updateLaunchBtn() {
    const name = (document.getElementById('rule-name') || {}).value || '';
    const path = (document.getElementById('rule-path') || {}).value || '';
    const btn  = document.getElementById('kw-launch-main-btn');
    if (!btn) return;
    const valid = name.trim().length > 0 && path.trim().length > 0;
    btn.disabled = !valid;
    btn.style.opacity = valid ? '1' : '0.45';
    btn.title = valid ? '' : 'Renseignez le nom et le chemin du fichier log pour activer';
}

/* ── Phase 1: Config ────────────────────────────────────────────────────── */
function _renderConfigPhase() {
    const now      = new Date();
    const tomorrow = new Date(now.getTime() + 24 * 3600 * 1000);
    const toLocal  = d => {
        const p = n => String(n).padStart(2, '0');
        return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
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
                <input type="datetime-local" id="kw-period-start" value="${toLocal(now)}"      class="kw-input">
                <span style="opacity:.5">→</span>
                <input type="datetime-local" id="kw-period-end"   value="${toLocal(tomorrow)}" class="kw-input">
            </div>
            <label class="kw-label" style="margin-top:.75rem">
                Granularité (taille d'un paquet)
                <span id="kw-granularity-hint" class="kw-hint"></span>
            </label>
            <select id="kw-granularity" class="kw-input"></select>
            <div id="kw-packets-estimate" class="kw-hint" style="margin-top:.35rem"></div>
            <div id="kw-config-limit-hint" class="kw-hint" style="margin-top:.35rem;opacity:.55">Limite par paquet : récupération…</div>
        </div>
    `;

    // Wire up listeners
    const startInput = document.getElementById('kw-period-start');
    const endInput   = document.getElementById('kw-period-end');
    const granSel    = document.getElementById('kw-granularity');
    const refresh    = () => { _refreshGranularitySelect(); _updateEstimate(); };
    startInput.addEventListener('change', refresh);
    endInput.addEventListener('change', refresh);
    granSel.addEventListener('change', _updateEstimate);

    _refreshGranularitySelect();
    _fetchMaxChars();
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
    const dur       = _durationS();
    const allowed   = _allowedGranularities(dur);
    const suggested = _suggestGranularity(dur);
    const current   = parseInt(sel.value) || 0;

    sel.innerHTML = GRANULARITY_OPTIONS
        .filter(o => allowed.includes(o.value))
        .map(o => `<option value="${o.value}">${o.label}${o.value === suggested ? ' ★' : ''}</option>`)
        .join('');

    if (current && [...sel.options].some(o => parseInt(o.value) === current)) {
        sel.value = current;
    } else {
        sel.value = suggested;
    }

    const hint = document.getElementById('kw-granularity-hint');
    if (hint) hint.textContent = `(suggestion : ${GRANULARITY_OPTIONS.find(o => o.value === suggested)?.label || '?'})`;

    _updateEstimate();
}

function _updateEstimate() {
    const el   = document.getElementById('kw-packets-estimate');
    if (!el) return;
    const dur  = _durationS();
    const gran = parseInt((document.getElementById('kw-granularity') || {}).value) || 3600;
    const n    = Math.max(1, Math.ceil(dur / gran));
    el.textContent = `≈ ${n} paquet${n > 1 ? 's' : ''} à traiter`;
}

async function _fetchMaxChars() {
    const el = document.getElementById('kw-config-limit-hint');
    if (!el) return;
    try {
        const cfg = await fetch('/api/config').then(r => r.json());
        el.textContent = `Limite par paquet : ${(cfg.max_log_chars || 5000).toLocaleString()} caractères (config globale)`;
    } catch {
        el.textContent = 'Limite par paquet : 5 000 caractères (défaut)';
    }
}

/* ── Launch: save rule → start session → close modal ───────────────────── */
async function _launchSession() {
    const logPath  = (document.getElementById('rule-path') || {}).value || '';
    const ruleName = (document.getElementById('rule-name') || {}).value || '';
    if (!logPath || !ruleName) return;

    const periodStart = (document.getElementById('kw-period-start') || {}).value || '';
    const periodEnd   = (document.getElementById('kw-period-end')   || {}).value || '';
    const granularity = parseInt((document.getElementById('kw-granularity') || {}).value) || 3600;

    if (!periodStart || !periodEnd || new Date(periodEnd) <= new Date(periodStart)) {
        alert('Période invalide — la date de fin doit être après la date de début.');
        return;
    }

    const launchBtn = document.getElementById('kw-launch-main-btn');
    if (launchBtn) { launchBtn.disabled = true; launchBtn.textContent = 'Enregistrement…'; }

    // Step 1: Save the rule to get a real rule_id
    let ruleId = null;
    try {
        const existingId = (document.getElementById('rule-id') || {}).value || '';
        const kwRaw      = (document.getElementById('rule-keywords') || {}).value || '';
        const ruleData = {
            name:                      ruleName,
            log_file_path:             logPath,
            keywords:                  kwRaw.split(',').map(k => k.trim()).filter(Boolean),
            application_context:       (document.getElementById('rule-context') || {}).value || '',
            enabled:                   !!(document.getElementById('rule-enabled') || {checked: true}).checked,
            notify_on_match:           !!(document.getElementById('rule-notify')  || {checked: true}).checked,
            context_lines:             parseInt((document.getElementById('rule-context-lines') || {}).value) || 5,
            anti_spam_delay:           parseInt((document.getElementById('rule-anti-spam')     || {}).value) || 60,
            notify_severity_threshold: (document.getElementById('rule-severity-threshold') || {}).value || 'info',
        };
        const saved = await fetch(existingId ? `/api/rules/${existingId}` : '/api/rules', {
            method:  existingId ? 'PUT' : 'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify(ruleData),
        }).then(r => r.json());
        ruleId = saved.id || parseInt(existingId) || null;
    } catch (e) {
        alert('Erreur lors de la sauvegarde de la règle : ' + e.message);
        if (launchBtn) { launchBtn.disabled = false; launchBtn.textContent = '▶ Lancer l\'analyse'; }
        return;
    }

    if (launchBtn) launchBtn.textContent = 'Démarrage…';

    // Step 2: Start the learning session
    let sessionId = null;
    try {
        const toUTC = s => new Date(s).toISOString();
        const res = await fetch('/api/keyword-learning/start', {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({
                rule_id:       ruleId,
                log_file_path: logPath,
                period_start:  toUTC(periodStart),
                period_end:    toUTC(periodEnd),
                granularity_s: granularity,
            }),
        }).then(r => r.json());
        if (!res.session_id) throw new Error('Réponse inattendue du serveur');
        sessionId = res.session_id;
        _wizardSessionId = sessionId;
    } catch (e) {
        alert('Erreur lors du lancement de la session : ' + e.message);
        if (launchBtn) { launchBtn.disabled = false; launchBtn.textContent = '▶ Lancer l\'analyse'; }
        return;
    }

    // Step 3: Close modal, reload rules list, start card polling
    document.getElementById('rule-modal').classList.add('hidden');
    kwWizardClose();
    if (typeof loadRules === 'function') await loadRules();
    _startCardPolling(sessionId, ruleId);
}

/* ── Card polling (progress in rule card after modal closes) ────────────── */
function _startCardPolling(sessionId, ruleId) {
    if (_wizardCardTimer) clearInterval(_wizardCardTimer);
    _wizardCardTimer = setInterval(async () => {
        try {
            const data = await fetch(`/api/keyword-learning/${sessionId}/status`).then(r => r.json());
            _updateRuleCard(ruleId, data);
            if (['validated', 'reverted', 'error'].includes(data.status)) {
                clearInterval(_wizardCardTimer);
                _wizardCardTimer = null;
                // Final reload to show updated keywords
                if (typeof loadRules === 'function') loadRules();
            }
        } catch { /* ignore */ }
    }, 2000);
}

function _updateRuleCard(ruleId, data) {
    const card = document.getElementById(`rule-learning-${ruleId}`);
    if (!card) return;

    const pct = data.total_packets > 0
        ? Math.round((data.completed_packets / data.total_packets) * 100)
        : 0;

    const statusLabels = {
        scanning:  `🔍 Scan en cours — ${data.completed_packets}/${data.total_packets} paquets (${pct}%)`,
        refining:  '🧠 Raffinement par l\'IA…',
        validated: '✅ Mots-clés appliqués',
        error:     `⚠️ Erreur : ${data.error_message || 'Inconnue'}`,
    };

    card.innerHTML = `
        <div class="kw-card-status">${statusLabels[data.status] || data.status}</div>
        ${data.status === 'scanning' ? `
            <div class="kw-progress-bar-track" style="margin:.3rem 0">
                <div class="kw-progress-bar" style="width:${pct}%"></div>
            </div>
            ${data.raw_keywords && data.raw_keywords.length ? `
            <div class="kw-tags-row" style="margin-top:.3rem">
                ${data.raw_keywords.slice(0, 12).map(k => `<span class="kw-tag kw-tag--raw">${_esc(k)}</span>`).join('')}
                ${data.raw_keywords.length > 12 ? `<span class="kw-hint">+${data.raw_keywords.length - 12}</span>` : ''}
            </div>` : ''}
        ` : ''}
        ${data.status === 'scanning' || data.status === 'refining' ? `
            <button type="button" class="btn btn-danger btn-sm" style="margin-top:.4rem"
                onclick="kwStopSession(${_wizardSessionId})">⏹ Arrêter</button>
        ` : ''}
    `;
}

/* ── Global stop (called from rule card button) ─────────────────────────── */
async function kwStopSession(sessionId) {
    if (!sessionId) return;
    try {
        await fetch(`/api/keyword-learning/${sessionId}`, { method: 'DELETE' });
        if (_wizardCardTimer) { clearInterval(_wizardCardTimer); _wizardCardTimer = null; }
        if (typeof loadRules === 'function') loadRules();
    } catch (e) {
        console.error('Stop session error:', e);
    }
}

/* ── Revert (called from rule card) ─────────────────────────────────────── */
async function kwRevertSession(sessionId) {
    if (!sessionId) return;
    try {
        await fetch(`/api/keyword-learning/${sessionId}/revert`, { method: 'POST' });
        if (typeof loadRules === 'function') loadRules();
    } catch (e) {
        console.error('Revert session error:', e);
    }
}

/* ── Utility ─────────────────────────────────────────────────────────────── */
function _esc(text) {
    const d = document.createElement('div');
    d.textContent = String(text || '');
    return d.innerHTML;
}
