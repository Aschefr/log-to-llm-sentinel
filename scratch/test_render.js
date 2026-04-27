const fs = require('fs');

const data = fs.readFileSync('scratch/rules_output.json', 'utf8');
const rules = JSON.parse(data);

function escapeHtml(text) {
    if (!text) return '';
    return text.toString().replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

const window = { t: (k) => k };

try {
    const html = rules.map(rule => `
            <div class="rule-card" id="rule-card-${rule.id}">
                <div class="rule-info">
                    <h3>${escapeHtml(rule.name)}</h3>
                    <p>${rule.log_file_path === '[WEBHOOK]' 
                        ? \`<span class="chip" style="background:var(--primary);color:white;border:none">🔗 Webhook</span> 
                           <button class="btn btn-secondary btn-sm" onclick="copyWebhookUrl(${rule.id})" title="Copier URL curl">📋 Copier URL</button>\` 
                        : \`📁 ${escapeHtml(rule.log_file_path)}\`}</p>
                    <p>🔑 ${rule.keywords.join(', ') || '<em style="opacity:.5">Aucun mot-clé (apprentissage en cours…)</em>'}</p>
                    ${rule.application_context ? \`<p>🧩 ${escapeHtml(rule.application_context)}</p>\` : ''}
                    <p>${rule.enabled ? \`✅ ${window.t ? window.t('rules.enabled_status') : 'Enabled'}\` : \`❌ ${window.t ? window.t('rules.disabled_status') : 'Disabled'}\`} | 🔔 ${rule.notify_on_match ? \`${window.t ? window.t('rules.notification_threshold') : 'Threshold:'} ${rule.notify_severity_threshold || 'info'}\` : (window.t ? window.t('rules.notifications_disabled') : 'Notifications disabled')}</p>
                </div>
                <div class="rule-actions">
                    <button id="test-btn-${rule.id}" class="btn btn-secondary btn-sm" onclick="testRule(${rule.id})">${window.t ? window.t('rules.test_rule') : '🧪 Test'}</button>
                    <button class="btn btn-primary btn-sm" onclick="editRule(${rule.id})">${window.t ? window.t('rules.edit') : '✏️ Edit'}</button>
                    <button class="btn btn-danger btn-sm" onclick="deleteRule(${rule.id}, this)">${window.t ? window.t('rules.delete') : '🗑️ Delete'}</button>
                </div>
                <div class="rule-toggles" style="display:flex;flex-direction:column;gap:.5rem;flex-basis:100%">
                    <div class="rule-last-line" style="display:flex;justify-content:space-between;align-items:center">
                        <div>
                            <strong>${window.t ? window.t('rules.last_detected_line') : 'Last detected line:'}</strong>
                            <div class="last-line-content">${escapeHtml(rule.last_log_line || (window.t ? window.t('dashboard.no_line_found') : 'No line found or file inaccessible'))}</div>
                        </div>
                        <button class="btn btn-secondary btn-sm" onclick="window.location.href='/monitor?rule=${rule.id}&line=${encodeURIComponent(rule.last_log_line || '')}'" data-i18n="rules.view_in_monitor">
                            🔍 ${window.t ? window.t('rules.view_in_monitor') || 'Voir dans Monitor' : 'Voir dans Monitor'}
                        </button>
                    </div>
                    ${rule.last_learning_session_id ? \`
                    <div class="kw-card-panel" id="rule-learning-${rule.id}">
                        <span class="kw-hint" style="opacity:.6">⏳ Chargement de la session d'apprentissage…</span>
                    </div>\` : ''}
                </div>
            </div>
        `).join('');
    console.log("SUCCESS!");
} catch (e) {
    console.error("ERROR:", e);
}
