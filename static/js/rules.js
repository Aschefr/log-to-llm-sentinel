// ─── Rules Page ─────────────────────────────────────────────────────────────
// Page-specific logic: rule list, cards, templates, test, delete.
// Modal logic (setup, save, edit, file browser) is in rule_modal.js (shared).
// ─────────────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    loadRules();

    // Setup shared modal with Rules-specific onSave callback
    setupRuleModal({ onSave: loadRules });

    // Wire up "Add Rule" button (page-specific)
    const addBtn = document.getElementById('add-rule-btn');
    if (addBtn) {
        addBtn.addEventListener('click', () => {
            resetForm();
            document.getElementById('rule-modal').classList.remove('hidden');
        });
    }

    window.i18n?.onLanguageChange(() => {
        loadRules();
    });
});

async function loadRules() {
    try {
        const rules = await apiFetch('/api/rules');
        const container = document.getElementById('rules-list');

        if (rules.length === 0) {
            container.innerHTML = `<div class="loading">${window.t ? window.t('rules.no_rules') : 'No rules configured'}</div>`;
            return;
        }

        container.innerHTML = rules.map(rule => `
            <div class="rule-card" id="rule-card-${rule.id}">
                <div class="rule-info">
                    <h3>${escapeHtml(rule.name)}</h3>
                    <p>${rule.log_file_path && rule.log_file_path.startsWith('[WEBHOOK]') 
                        ? `<span class="chip" style="background:var(--primary);color:white;border:none">🔗 Webhook</span> 
                           <button class="btn btn-secondary btn-sm" onclick="copyWebhookUrl('${rule.log_file_path.split(':')[1] || rule.id}', this)" title="Copier URL curl">📋 Copier URL</button>` 
                        : `📁 ${escapeHtml(rule.log_file_path)}`}</p>
                    <p>🔑 ${rule.keywords.join(', ') || '<em style="opacity:.5">Aucun mot-clé (apprentissage en cours…)</em>'}</p>
                    ${rule.application_context ? `<p>🧩 ${escapeHtml(rule.application_context)}</p>` : ''}
                    <p>${rule.enabled ? `✅ ${window.t ? window.t('rules.enabled_status') : 'Enabled'}` : `❌ ${window.t ? window.t('rules.disabled_status') : 'Disabled'}`} | 🔔 ${rule.notify_on_match ? `${window.t ? window.t('rules.notification_threshold') : 'Threshold:'} ${rule.notify_severity_threshold || 'info'}` : (window.t ? window.t('rules.notifications_disabled') : 'Notifications disabled')}</p>
                </div>
                <div class="rule-actions">
                    <button id="test-btn-${rule.id}" class="btn btn-secondary btn-sm" onclick="testRule(${rule.id})">🧪 ${window.t ? window.t('rules.test_rule') : 'Test'}</button>
                    <button class="btn btn-primary btn-sm" onclick="editRule(${rule.id})">✏️ ${window.t ? window.t('common.edit') : 'Edit'}</button>
                    <button class="btn btn-danger btn-sm" onclick="deleteRule(${rule.id}, this)">🗑️ ${window.t ? window.t('common.delete') : 'Delete'}</button>
                </div>
                <div class="rule-toggles" style="display:flex;flex-direction:column;gap:.5rem;flex-basis:100%">
                    <div class="rule-last-line" style="display:flex;justify-content:space-between;align-items:center">
                        <div>
                            <strong>${window.t ? window.t('rules.last_detected_line') : 'Last detected line:'}</strong>
                            <div class="last-line-content">${escapeHtml(rule.last_log_line || (window.t ? window.t('dashboard.no_line_found') : 'No line found or file inaccessible'))}</div>
                        </div>
                        <button class="btn btn-secondary btn-sm" onclick="window.location.href='/monitor?rule=${rule.id}&line=${encodeURIComponent(rule.last_log_line || '')}'">
                            🔍 ${window.t ? window.t('common.view_in_monitor') || 'Voir dans Monitor' : 'Voir dans Monitor'}
                        </button>
                    </div>
                    ${rule.last_learning_session_id ? `
                    <div class="kw-card-panel" id="rule-learning-${rule.id}">
                        <span class="kw-hint" style="opacity:.6">⏳ Chargement de la session d'apprentissage…</span>
                    </div>` : ''}
                </div>
            </div>
        `).join('');

        // Fetch and render any active learning sessions
        const activeSessions = rules.filter(r => r.last_learning_session_id);
        if (activeSessions.length) _pollAllLearningSessions(activeSessions);

    } catch (error) {
        console.error('Erreur chargement règles:', error);
    }
}

let _ruleSessionTimers = {};
const _completedSessions = new Set(); // session IDs already finished — don't re-poll

function _pollAllLearningSessions(rules) {
    rules.forEach(rule => {
        const sid = rule.last_learning_session_id;
        // Skip sessions we already completed
        if (_completedSessions.has(sid)) return;

        // Clear existing timer for this rule
        if (_ruleSessionTimers[rule.id]) {
            clearInterval(_ruleSessionTimers[rule.id]);
            delete _ruleSessionTimers[rule.id];
        }
        // Fetch once immediately
        _fetchAndApplySession(rule.id, sid);
        // Then poll every 3s
        _ruleSessionTimers[rule.id] = setInterval(async () => {
            const done = await _fetchAndApplySession(rule.id, sid);
            if (done) {
                clearInterval(_ruleSessionTimers[rule.id]);
                delete _ruleSessionTimers[rule.id];
                _completedSessions.add(sid);
                // Update only the keywords line in this card, no full reload
                _refreshCardKeywords(rule.id);
            }
        }, 3000);
    });
}

/** Update only the keywords paragraph in a rule card without rebuilding the whole list */
async function _refreshCardKeywords(ruleId) {
    try {
        const rules = await apiFetch('/api/rules');
        const rule = rules.find(r => r.id === ruleId);
        if (!rule) return;
        const card = document.getElementById(`rule-card-${ruleId}`);
        if (!card) return;
        // Update keyword line (3rd <p> in .rule-info)
        const kwLine = card.querySelector('.rule-info p:nth-child(3)');
        if (kwLine) {
            kwLine.innerHTML = `🔑 ${
                rule.keywords.join(', ') ||
                `<em style="opacity:.5">${window.t ? window.t('rules.no_keywords') : 'Aucun mot-clé'}</em>`
            }`;
        }
    } catch(e) { /* silent */ }
}

async function _fetchAndApplySession(ruleId, sessionId) {
    try {
        const data = await fetch(`/api/keyword-learning/${sessionId}/status`).then(r => r.json());
        const card = document.getElementById(`rule-learning-${ruleId}`);
        // Card div not yet in DOM (loadRules still rendering) — don't stop polling
        if (!card) return false;

        const pct = data.total_packets > 0
            ? Math.round((data.completed_packets / data.total_packets) * 100) : 0;

        const _t = (key, fb, vars) => {
            let s = window.t ? window.t(key) || fb : fb;
            if (vars) Object.entries(vars).forEach(([k, v]) => { s = s.replace(`{${k}}`, v); });
            return s;
        };

        const STATUS = {
            pending:   '⏳ ' + _t('kw.card_pending',   'En attente de démarrage…'),
            scanning:  '🔍 ' + _t('kw.card_scanning',  'Scan — {done}/{total} paquets ({pct}%)', { done: data.completed_packets, total: data.total_packets, pct }),
            refining:  '🧠 ' + _t('kw.card_refining',  'Raffinement IA en cours…'),
            validated: '✅ ' + _t('kw.card_validated', 'Apprentissage terminé'),
            reverted:  '↩️ ' + _t('kw.card_reverted',  'Annulé — mots-clés restaurés'),
            error:     '⚠️ ' + _t('kw.card_error',     'Erreur : {msg}', { msg: data.error_message || 'Inconnue' }),
        };

        const isActive = ['pending', 'scanning', 'refining'].includes(data.status);
        const isDone   = ['validated', 'reverted', 'error'].includes(data.status);

        // ── Render exact same layout as Monitor ──────────────────────────────
        card.innerHTML = `
            <span class="info-label">🤖 ${window.t ? window.t('monitor.autolearn_title') : 'Auto-learning'}</span>
            <div class="kw-card-status">${STATUS[data.status] || data.status}</div>
            ${data.status === 'scanning' ? `
            <div style="margin:.3rem 0 .4rem">
                <div class="kw-progress-bar-track"><div class="kw-progress-bar" style="width:${pct}%"></div></div>
            </div>` : ''}
            ${data.status === 'scanning' && data.raw_keywords?.length ? `
            <div class="kw-tags-row" style="margin-top:.25rem;display:flex">
                ${data.raw_keywords.slice(0, 8).map(k => `<span class="kw-tag kw-tag--raw">${escapeHtml(k)}</span>`).join('')}
                ${data.raw_keywords.length > 8 ? `<span class="kw-hint" style="align-self:center">+${data.raw_keywords.length - 8}</span>` : ''}
            </div>` : ''}
            ${data.status === 'validated' && data.final_keywords?.length ? `
            <div class="kw-tags-row" style="margin-top:.25rem;display:flex">
                ${data.final_keywords.map(k => `<span class="kw-tag">${escapeHtml(k)}</span>`).join('')}
            </div>` : ''}
            <div class="kw-actions" style="margin-top:.4rem">
                ${isActive ? `<button type="button" class="btn btn-danger btn-sm" onclick="kwStopSession(${sessionId})">⏹ ${_t('kw.stop_btn','Stop')}</button>` : ''}
                ${data.status === 'validated' ? `<button type="button" class="btn btn-secondary btn-sm" onclick="kwRevertSession(${sessionId})">↩️ ${_t('kw.revert_btn','Revert')}</button>` : ''}
                ${isDone ? `<a href="/api/keyword-learning/${sessionId}/log" download class="btn btn-secondary btn-sm">📥 Log</a>` : ''}
            </div>
        `;

        return isDone;
    } catch { return false; }
}


async function testRule(id) {
    const btn = document.getElementById(`test-btn-${id}`);
    const originalText = btn ? btn.innerHTML : (window.t ? window.t('rules.test_rule') : '🧪 Test');
    
    const abortController = new AbortController();
    let stopBtn = null;
    
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = window.t ? window.t('rules.test_in_progress') : 'Testing...';
        btn.classList.add('pulse-animation');
        
        stopBtn = document.createElement('button');
        stopBtn.className = 'btn btn-danger btn-sm';
        stopBtn.style.marginLeft = '0.5rem';
        stopBtn.innerHTML = window.t ? '🛑 ' + window.t('common.stop') : '🛑 Stop';
        stopBtn.onclick = () => abortController.abort();
        btn.parentNode.insertBefore(stopBtn, btn.nextSibling);
    }

    try {
        await apiFetch(`/api/rules/${id}/test`, { 
            method: 'POST',
            signal: abortController.signal
        });

        if (btn) {
            btn.innerHTML = window.t ? '✅ ' + window.t('common.done') : '✅ Done';
            btn.classList.remove('pulse-animation');
            btn.classList.add('btn-success-temporary');
            setTimeout(() => {
                if (document.getElementById(`test-btn-${id}`)) {
                    btn.innerHTML = originalText;
                    btn.disabled = false;
                    btn.classList.remove('btn-success-temporary');
                }
            }, 2500);
        }
    } catch (e) {
        if (btn) {
            btn.classList.remove('pulse-animation');
            btn.classList.add('btn-danger');
            if (e.name === 'AbortError') {
                btn.innerHTML = window.t ? '❌ ' + window.t('rules.test_cancelled') : '❌ Cancelled';
            } else {
                console.error('Erreur test rule:', e);
                btn.innerHTML = window.t ? '❌ ' + window.t('rules.test_error_btn') : '❌ Error';
            }
            setTimeout(() => {
                if (document.getElementById(`test-btn-${id}`)) {
                    btn.innerHTML = originalText;
                    btn.disabled = false;
                    btn.classList.remove('btn-danger');
                }
            }, 2500);
        }
    } finally {
        if (stopBtn && stopBtn.parentNode) {
            stopBtn.parentNode.removeChild(stopBtn);
        }
    }
}

async function deleteRule(id, btnElement) {
    showInlineConfirm(btnElement, window.t ? window.t('rules.delete_confirm') : 'Are you sure you want to delete this rule?', async () => {
        try {
            await apiFetch(`/api/rules/${id}`, {
                method: 'DELETE',
            });
            loadRules();
        } catch (error) {
            console.error('Erreur suppression règle:', error);
            alert((window.t ? window.t('common.error') : 'Erreur') + ': ' + error.message);
        }
    });
}
