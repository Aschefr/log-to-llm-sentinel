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
let _wizardSessionId     = null;
let _wizardPollTimer     = null;
let _wizardCardTimer     = null;
let _wizardFinalKeywords = [];
let _wizardGranularity   = null; // null = not yet chosen by user
let _activeSession       = null; // session data when editing an active learning rule

/* ── Granularity options ────────────────────────────────────────────────── */
const GRANULARITY_OPTIONS = [
    { value: 1  * 60,    label: '1 minute'   },
    { value: 2  * 60,    label: '2 minutes'  },
    { value: 5  * 60,    label: '5 minutes'  },
    { value: 10 * 60,    label: '10 minutes' },
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
    if (durationS <= 10 * 60)   return 1  * 60;   // ≤10 min  → 1 min
    if (durationS <= 30 * 60)   return 5  * 60;   // ≤30 min  → 5 min
    if (durationS <= 6  * 3600) return 15 * 60;   // ≤6 h    → 15 min
    if (durationS <= 24 * 3600) return 3600;      // ≤24 h    → 1 h
    if (durationS <= 7  * 86400) return 6 * 3600; // ≤7 j    → 6 h
    if (durationS <= 30 * 86400) return 86400;    // ≤30 j    → 1 j
    return 7 * 86400;
}

function _allowedGranularities(durationS) {
    if (durationS <= 5  * 60)   return [1*60, 2*60];
    if (durationS <= 15 * 60)   return [1*60, 2*60, 5*60];
    if (durationS <= 30 * 60)   return [2*60, 5*60, 10*60, 15*60];
    if (durationS <= 6  * 3600) return [5*60, 10*60, 15*60, 30*60, 3600];
    if (durationS <= 24 * 3600) return [15*60, 30*60, 3600, 2*3600, 6*3600];
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

        // Swap buttons depending on session state
        if (_activeSession) {
            // Session running or finished: keep normal Save button, hide Launch
            if (origSave)  origSave.classList.remove('hidden');
            if (launchBtn) launchBtn.classList.add('hidden');
        } else {
            if (origSave)  origSave.classList.add('hidden');
            if (launchBtn) {
                launchBtn.classList.remove('hidden');
            }
        }

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

/**
 * Load an existing learning session into the wizard (called from editRule).
 * Pre-selects the auto tab and shows progress.
 */
async function kwWizardLoadSession(sessionId) {
    try {
        const data = await fetch(`/api/keyword-learning/${sessionId}/status`).then(r => r.json());
        _activeSession = data;
        _wizardSessionId = sessionId;

        // Clear body so _renderConfigPhase re-renders with session data
        const body = document.getElementById('kw-wizard-body');
        if (body) body.innerHTML = '';

        // Switch to auto tab (this triggers _renderConfigPhase)
        kwTabSwitch('auto');

        // Start polling for live updates if session is active
        if (['pending', 'scanning', 'refining'].includes(data.status)) {
            _startWizardSessionPoll(sessionId);
        }
    } catch (e) {
        console.error('kwWizardLoadSession error:', e);
        // Fallback: just show empty auto tab
        kwTabSwitch('auto');
    }
}

/** Poll session status to update the wizard steps in real time */
function _startWizardSessionPoll(sessionId) {
    if (_wizardPollTimer) clearInterval(_wizardPollTimer);
    _wizardPollTimer = setInterval(async () => {
        // Capture timer ref so we can detect if it was cancelled while fetch was in flight
        const thisTimer = _wizardPollTimer;
        try {
            const data = await fetch(`/api/keyword-learning/${sessionId}/status`).then(r => r.json());
            // If poll was stopped (reset) while we were fetching, discard the result
            if (_wizardPollTimer !== thisTimer) return;
            _activeSession = data;
            _updateWizardSteps(data);
            if (['validated', 'reverted', 'error'].includes(data.status)) {
                clearInterval(_wizardPollTimer);
                _wizardPollTimer = null;
            }
        } catch { /* ignore */ }
    }, 3000);
}

/** Update only the step indicators and progress text without rebuilding the form */
function _updateWizardSteps(data) {
    const stepsEl = document.getElementById('kw-wizard-steps');
    if (stepsEl) stepsEl.innerHTML = _buildStepsHtml(data.status);

    const progressEl = document.getElementById('kw-wizard-progress');
    if (progressEl) {
        if (data.status === 'scanning') {
            const pct = data.total_packets > 0 ? Math.round((data.completed_packets / data.total_packets) * 100) : 0;
            let label = window.t ? window.t('kw.progress_label') : 'Progression : {done}/{total} paquets ({pct}%)';
            label = label.replace('{done}', data.completed_packets).replace('{total}', data.total_packets).replace('{pct}', pct);
            let kwsHtml = '';
            if (data.raw_keywords && data.raw_keywords.length) {
                kwsHtml += `<div class="kw-tags-row" style="margin-top:.4rem">
                    ${data.raw_keywords.slice(0, 12).map(k => `<span class="kw-tag kw-tag--raw">${_esc(k)}</span>`).join('')}
                    ${data.raw_keywords.length > 12 ? `<span class="kw-hint">+${data.raw_keywords.length - 12}</span>` : ''}
                </div>`;
            }
            if (data.raw_exclusions && data.raw_exclusions.length) {
                kwsHtml += `<div class="kw-tags-row" style="margin-top:.2rem">
                    ${data.raw_exclusions.slice(0, 8).map(e => `<span class="kw-tag kw-tag--negative" style="text-decoration:line-through;opacity:0.7">${_esc(e)}</span>`).join('')}
                    ${data.raw_exclusions.length > 8 ? `<span class="kw-hint">+${data.raw_exclusions.length - 8}</span>` : ''}
                </div>`;
            }

            progressEl.innerHTML = `
                <div class="kw-hint" style="margin-bottom:.3rem">${label}</div>
                <div class="kw-progress-bar-track"><div class="kw-progress-bar" style="width:${pct}%"></div></div>
                ${kwsHtml}
            `;
            progressEl.style.display = 'block';
        } else if (data.status === 'refining') {
            progressEl.innerHTML = `<div class="kw-hint">${window.t ? window.t('kw.card_refining') : '🧠 Raffinement IA en cours…'}</div>`;
            progressEl.style.display = 'block';
        } else if (data.status === 'validated') {
            progressEl.innerHTML = `<div class="kw-hint">${window.t ? window.t('kw.card_validated') : '✅ Apprentissage terminé'}</div>`;
            progressEl.style.display = 'block';
        } else if (data.status === 'error') {
            let msg = window.t ? window.t('kw.card_error') : '⚠️ Erreur : {msg}';
            msg = msg.replace('{msg}', data.error_message || 'Inconnue');
            progressEl.innerHTML = `<div class="kw-hint" style="color:var(--danger)">${msg}</div>`;
            progressEl.style.display = 'block';
        } else {
            progressEl.style.display = 'none';
        }
    }
}


/* ── Internal helpers ───────────────────────────────────────────────────── */
function _wizardReset() {
    _wizardSessionId     = null;
    _wizardFinalKeywords = [];
    _wizardGranularity   = null;
    _activeSession       = null;
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
    // Re-check when source tabs are clicked (local ↔ webhook)
    document.querySelectorAll('.source-card').forEach(c => c.addEventListener('click', check));
    _updateLaunchBtn();
}

function _updateLaunchBtn() {
    const name = (document.getElementById('rule-name') || {}).value || '';
    const path = (document.getElementById('rule-path') || {}).value || '';
    const btn  = document.getElementById('kw-launch-main-btn');
    if (!btn) return;

    // Webhook mode: path is empty but token exists → valid
    const activeSource = document.querySelector('.source-card.kw-tab--active');
    const isWebhook = activeSource && activeSource.dataset.source === 'webhook';
    const hasSource = isWebhook ? !!window._currentWebhookToken : path.trim().length > 0;

    const valid = name.trim().length > 0 && hasSource;
    btn.disabled = !valid;
    btn.style.opacity = valid ? '1' : '0.45';
    btn.title = valid ? '' : (window.t ? window.t('kw.launch_no_path') : 'Renseignez le nom et le chemin du fichier log pour activer');

    // Button text: always "Launch" (button is hidden if active session)
    btn.innerHTML = '<span aria-hidden="true">🤖 </span>' + (window.t ? window.t('kw.launch_full') : 'Auto-apprentissage : Lancer l\'analyse');
}

/* ── Step indicator builder ─────────────────────────────────────────────── */
function _buildStepsHtml(sessionStatus) {
    const steps = [
        { key: 'kw.step_config',    fallback: '1 Config',       statuses: [null] },
        { key: 'kw.step_scan',      fallback: '2 Scan',         statuses: ['pending', 'scanning'] },
        { key: 'kw.step_refine',    fallback: '3 Raffinement',  statuses: ['refining'] },
        { key: 'kw.step_validated', fallback: '4 Validé',       statuses: ['validated'] },
    ];

    // Determine which step index is active
    let activeIdx = 0; // default: config
    if (sessionStatus) {
        if (['pending', 'scanning'].includes(sessionStatus)) activeIdx = 1;
        else if (sessionStatus === 'refining')               activeIdx = 2;
        else if (sessionStatus === 'validated')              activeIdx = 3;
        else if (['error', 'reverted'].includes(sessionStatus)) activeIdx = -1;
    }

    return steps.map((step, idx) => {
        let cls = 'kw-step';
        if (idx === activeIdx) cls += ' kw-step--active';
        else if (idx < activeIdx) cls += ' kw-step--done';
        const label = window.t ? window.t(step.key) || step.fallback : step.fallback;
        return `<span class="${cls}">${label}</span>` +
               (idx < steps.length - 1 ? '<span class="kw-step-sep">›</span>' : '');
    }).join('');
}

/* ── Phase 1: Config ────────────────────────────────────────────────────── */
function _renderConfigPhase() {
    const session = _activeSession;
    const toLocal  = d => {
        const p = n => String(n).padStart(2, '0');
        return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
    };

    let startVal, endVal, startDisabled = false;

    if (session && session.period_start) {
        // Editing an existing session: use its period values
        startVal = toLocal(new Date(session.period_start));
        endVal   = toLocal(new Date(session.period_end));
        // Lock start only while the session is running; allow editing once finished
        const _activeStatuses = ['pending', 'scanning', 'refining'];
        startDisabled = _activeStatuses.includes(session.status);
        if (session.granularity_s) _wizardGranularity = session.granularity_s;
    } else {
        const now      = new Date();
        const tomorrow = new Date(now.getTime() + 24 * 3600 * 1000);
        startVal = toLocal(now);
        endVal   = toLocal(tomorrow);
    }

    const statusToStep = { pending: 0, scanning: 1, refining: 2, validated: 3, error: -1, reverted: -1 };

    _wizardBody().innerHTML = `
        <div class="kw-phase-steps" id="kw-wizard-steps">
            ${_buildStepsHtml(session ? session.status : null)}
        </div>
        ${session && ['pending', 'scanning', 'refining'].includes(session.status) ? `
            <div id="kw-wizard-progress" style="margin-bottom:.5rem"></div>
            <div class="kw-hint" style="margin-bottom:.75rem;opacity:.65">
                ${window.t ? window.t('kw.resume_info') : 'L\'analyse est en cours. Vous pouvez modifier la date de fin ou la granularité.'}
            </div>
        ` : session && ['validated', 'error', 'reverted'].includes(session.status) ? `
            <div id="kw-wizard-progress" style="display:none"></div>
            <div style="margin-bottom:.75rem;display:flex;align-items:center;gap:.75rem;flex-wrap:wrap">
                <span class="kw-hint" style="opacity:.75">
                    ${session.status === 'validated' ? '✅ Session terminée.' : session.status === 'reverted' ? '↩️ Session annulée.' : '⚠️ Session en erreur.'}
                    Modifiez la période ci-dessous et relancez.
                </span>
                <button type="button" class="btn btn-secondary btn-sm"
                        onclick="_kwResetForNewSession()"
                        style="white-space:nowrap">🔄 Nouvelle session</button>
            </div>
        ` : '<div id="kw-wizard-progress" style="display:none"></div>'}
        <div class="kw-config-grid">
            <label class="kw-label">${window.t ? window.t('kw.period_label') : "Période d'analyse"} <span style="opacity:.5;font-size:.75em">${window.t ? window.t('kw.period_local_hint') : '(heure locale)'}</span></label>
            <div class="kw-period-row">
                <input type="datetime-local" id="kw-period-start" value="${startVal}"
                       class="kw-input${startDisabled ? ' kw-input--disabled' : ''}"
                       ${startDisabled ? 'disabled' : ''}
                       ${startDisabled ? `title="${window.t ? window.t('kw.start_locked') : 'Date de début verrouillée (session en cours)'}"` : ''}>
                <span style="opacity:.5">→</span>
                <input type="datetime-local" id="kw-period-end"   value="${endVal}" class="kw-input">
            </div>
            ${!startDisabled ? `
            <div style="margin-top:1rem; padding: 0.75rem; background: rgba(0,0,0,0.15); border: 1px solid var(--border); border-radius: 6px;">
                <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom: 0.6rem;">
                    <label style="display:flex; align-items:center; gap:0.6rem; cursor:pointer; margin:0; user-select:none; padding: 0.2rem; border-radius: 4px; transition: background 0.2s;" onmouseover="this.style.background='rgba(255,255,255,0.05)'" onmouseout="this.style.background='transparent'">
                        <div class="toggle-switch" style="pointer-events:none; margin:0">
                            <input type="checkbox" id="kw-profile-mode" checked>
                            <span class="toggle-slider"></span>
                        </div>
                        <span style="font-size: 0.8rem; font-weight: 600; color: var(--text-primary);">
                            <span id="kw-mode-live-label" style="display:inline-flex; align-items:center; gap:0.4rem">🔴 <span data-i18n="kw.mode_live">Capture en direct (Futur)</span></span>
                            <span id="kw-mode-hist-label" class="hidden" style="display:inline-flex; align-items:center; gap:0.4rem">🕰️ <span data-i18n="kw.mode_history">Analyse historique (Passé)</span></span>
                        </span>
                    </label>
                </div>
                <div class="kw-quick-profiles" style="display:flex;gap:0.4rem;flex-wrap:wrap">
                    <button type="button" class="btn btn-secondary btn-sm" onclick="_applyKwProfile(2)" style="font-size:0.75rem;padding:0.2rem 0.5rem">⏱️ <span data-i18n="kw.profile_quick">Rapide (2 min)</span></button>
                    <button type="button" class="btn btn-secondary btn-sm" onclick="_applyKwProfile(10)" style="font-size:0.75rem;padding:0.2rem 0.5rem">🚀 <span data-i18n="kw.profile_fast">Normal (10 min)</span></button>
                    <button type="button" class="btn btn-secondary btn-sm" onclick="_applyKwProfile(60)" style="font-size:0.75rem;padding:0.2rem 0.5rem">🔍 <span data-i18n="kw.profile_extended">Étendu (1h)</span></button>
                    <button type="button" class="btn btn-secondary btn-sm" onclick="_applyKwProfile(1440)" style="font-size:0.75rem;padding:0.2rem 0.5rem">📅 <span data-i18n="kw.profile_complete">Complet (1j)</span></button>
                </div>
            </div>
            ` : ''}
            <label class="kw-label" style="margin-top:.75rem">
                ${window.t ? window.t('kw.granularity_label') : 'Granularité (taille d\'un paquet)'}
                <span id="kw-granularity-hint" class="kw-hint"></span>
            </label>
            <select id="kw-granularity" class="kw-input"></select>
            <div id="kw-packets-estimate" class="kw-hint" style="margin-top:.35rem"></div>
            <div id="kw-config-limit-hint" class="kw-hint" style="margin-top:.35rem;opacity:.55">${window.t ? window.t('kw.limit_loading') : 'Limite par paquet : récupération…'}</div>
        </div>
    `;

    // Wire up listeners
    const startInput = document.getElementById('kw-period-start');
    const endInput   = document.getElementById('kw-period-end');
    const granSel    = document.getElementById('kw-granularity');
    const refresh    = () => { _refreshGranularitySelect(); _updateEstimate(); };
    startInput.addEventListener('change', refresh);
    endInput.addEventListener('change', refresh);
    granSel.addEventListener('change', () => {
        _wizardGranularity = parseInt(granSel.value) || null;
        _updateEstimate();
    });

    const modeSwitch = document.getElementById('kw-profile-mode');
    if (modeSwitch) {
        modeSwitch.addEventListener('change', (e) => {
            const labelLive = document.getElementById('kw-mode-live-label');
            const labelHist = document.getElementById('kw-mode-hist-label');
            if (e.target.checked) {
                if (labelLive) labelLive.classList.remove('hidden');
                if (labelHist) labelHist.classList.add('hidden');
            } else {
                if (labelLive) labelLive.classList.add('hidden');
                if (labelHist) labelHist.classList.remove('hidden');
            }
            
            // Auto-apply current duration to shift dates appropriately
            const startVal = document.getElementById('kw-period-start')?.value;
            const endVal = document.getElementById('kw-period-end')?.value;
            if (startVal && endVal && window._applyKwProfile) {
                const diffMs = new Date(endVal) - new Date(startVal);
                if (diffMs > 0) {
                    const diffMinutes = Math.round(diffMs / 60000);
                    window._applyKwProfile(diffMinutes);
                }
            }
        });
    }

    _refreshGranularitySelect();
    _fetchMaxChars();

    // If editing a session, show progress immediately
    if (session) _updateWizardSteps(session);
}

window._applyKwProfile = function(minutes) {
    const toLocal = d => {
        const p = n => String(n).padStart(2, '0');
        return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
    };
    const now = new Date();
    
    const modeSwitch = document.getElementById('kw-profile-mode');
    const isLive = modeSwitch ? modeSwitch.checked : true;
    
    let start, end;
    if (isLive) {
        start = now;
        end = new Date(now.getTime() + minutes * 60000);
    } else {
        start = new Date(now.getTime() - minutes * 60000);
        end = now;
    }
    
    const startInput = document.getElementById('kw-period-start');
    const endInput   = document.getElementById('kw-period-end');
    
    if (startInput && !startInput.disabled) {
        startInput.value = toLocal(start);
    }
    if (endInput) {
        endInput.value = toLocal(end);
    }
    
    _refreshGranularitySelect();
    _updateEstimate();
};

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

    // Preserve explicit user choice only if still in allowed list;
    // otherwise (first render or out-of-range) always use the suggestion.
    const keepCurrent = _wizardGranularity !== null &&
        [...sel.options].some(o => parseInt(o.value) === _wizardGranularity);
    sel.value = keepCurrent ? _wizardGranularity : suggested;

    const hint = document.getElementById('kw-granularity-hint');
    if (hint) {
        const label = GRANULARITY_OPTIONS.find(o => o.value === suggested)?.label || '?';
        const tpl   = window.t ? window.t('kw.granularity_suggestion') : '(suggestion : {label})';
        hint.textContent = tpl.replace('{label}', label);
    }

    _updateEstimate();
}

function _updateEstimate() {
    const el   = document.getElementById('kw-packets-estimate');
    if (!el) return;
    const dur  = _durationS();
    const gran = parseInt((document.getElementById('kw-granularity') || {}).value) || 3600;
    const n = Math.max(1, Math.ceil(dur / gran));
    const tpl = window.t ? window.t('kw.packets_estimate') : '\u2248 {n} paquet(s) \u00e0 traiter';
    el.textContent = tpl.replace('{n}', n);
}

async function _fetchMaxChars() {
    const el = document.getElementById('kw-config-limit-hint');
    if (!el) return;
    try {
        const cfg = await fetch('/api/config').then(r => r.json());
        const max = cfg.max_log_chars || 5000;
        const tpl = window.t ? window.t('kw.limit_hint') : 'Limite par paquet : {max} caractères (config globale)';
        el.textContent = tpl.replace('{max}', max.toLocaleString());
    } catch {
        el.textContent = window.t ? window.t('kw.limit_default') : 'Limite par paquet : 5 000 caractères (défaut)';
    }
}

/* ── Launch: save rule → start session → close modal ───────────────────── */
async function _launchSession() {
    const activeSource = document.querySelector('.source-card.kw-tab--active');
    const isWebhook = activeSource && activeSource.dataset.source === 'webhook';

    const logPath  = isWebhook
        ? '[WEBHOOK]:' + (window._currentWebhookToken || '')
        : ((document.getElementById('rule-path') || {}).value || '');
    const ruleName = (document.getElementById('rule-name') || {}).value || '';
    if (!logPath || !ruleName) return;

    // Safety guard: if session is already finished, don't start a new one
    // (use the "Nouvelle session" button instead)
    const _terminalStatuses = ['validated', 'error', 'reverted'];
    if (_activeSession && _terminalStatuses.includes(_activeSession.status)) {
        console.warn('_launchSession called with terminal session — aborting. Use _kwResetForNewSession() first.');
        return;
    }

    const periodStart = (document.getElementById('kw-period-start') || {}).value || '';
    const periodEnd   = (document.getElementById('kw-period-end')   || {}).value || '';
    const granularity = parseInt((document.getElementById('kw-granularity') || {}).value) || 3600;

    if (!periodStart || !periodEnd || new Date(periodEnd) <= new Date(periodStart)) {
        alert(window.t ? window.t('kw.error_invalid_period') : 'Période invalide — la date de fin doit être après la date de début.');
        return;
    }

    const launchBtn = document.getElementById('kw-launch-main-btn');
    if (launchBtn) { launchBtn.disabled = true; launchBtn.textContent = window.t ? window.t('kw.launch_saving') : 'Enregistrement…'; }

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
            excluded_patterns: ((document.getElementById('rule-excluded-patterns') || {}).value || '')
                .split(',').map(p => p.trim()).filter(Boolean),
        };
        const saved = await fetch(existingId ? `/api/rules/${existingId}` : '/api/rules', {
            method:  existingId ? 'PUT' : 'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify(ruleData),
        }).then(r => r.json());
        ruleId = saved.id || parseInt(existingId) || null;
    } catch (e) {
        alert((window.t ? window.t('kw.error_save') : 'Erreur lors de la sauvegarde de la règle : ') + e.message);
        if (launchBtn) { launchBtn.disabled = false; launchBtn.textContent = window.t ? window.t('kw.launch_btn') : '▶ Lancer l\'analyse'; }
        return;
    }

    if (launchBtn) launchBtn.textContent = window.t ? window.t('kw.launch_starting') : 'Démarrage…';

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
        alert((window.t ? window.t('kw.error_start') : 'Erreur lors du lancement de la session : ') + e.message);
        if (launchBtn) { launchBtn.disabled = false; launchBtn.textContent = window.t ? window.t('kw.launch_btn') : '▶ Lancer l\'analyse'; }
        return;
    }

    // Step 3: Close modal, reload rules list.
    // rules.js _pollAllLearningSessions() will pick up the new session automatically.
    document.getElementById('rule-modal').classList.add('hidden');
    kwWizardClose();
    
    // If we are on the monitor page, refresh the rules list to show the auto-learning panel
    if (window.loadMonitorRules) {
        window.loadMonitorRules();
    }

    // Remove this session from the completed-session cache in rules.js (if any)
    // so that re-launching the same rule's session is properly re-polled.
    if (typeof _completedSessions !== 'undefined') _completedSessions.delete(sessionId);

    if (typeof loadRules === 'function') await loadRules();
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
            ${data.raw_exclusions && data.raw_exclusions.length ? `
            <div class="kw-tags-row" style="margin-top:.2rem">
                ${data.raw_exclusions.slice(0, 8).map(e => `<span class="kw-tag kw-tag--negative" style="text-decoration:line-through;opacity:0.7">${_esc(e)}</span>`).join('')}
                ${data.raw_exclusions.length > 8 ? `<span class="kw-hint">+${data.raw_exclusions.length - 8}</span>` : ''}
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

/** Reset wizard to fresh state so user can define a new period and relaunch */
function _kwResetForNewSession() {
    _activeSession = null;
    _wizardSessionId = null;
    _wizardGranularity = null;
    _stopAllPolling();
    const body = _wizardBody();
    if (body) body.innerHTML = '';
    _renderConfigPhase();

    // Show launch button, hide save button (we're on auto tab)
    const origSave  = document.querySelector('#rule-form .form-actions button[type="submit"]');
    const launchBtn = document.getElementById('kw-launch-main-btn');
    if (origSave)  origSave.classList.add('hidden');
    if (launchBtn) launchBtn.classList.remove('hidden');

    _watchFormValidation();
}
