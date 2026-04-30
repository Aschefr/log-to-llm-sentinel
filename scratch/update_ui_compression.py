import re

HTML_PATH = "templates/chat.html"
with open(HTML_PATH, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Inject context-warning inside context-bar-wrapper
context_warning = """
                    <div id="context-warning" class="hidden" style="background: rgba(239, 68, 68, 0.1); border: 1px solid var(--danger); padding: 0.5rem; border-radius: 0.4rem; margin-bottom: 0.5rem; display: flex; justify-content: space-between; align-items: center;">
                        <span style="font-size: 0.8rem; color: var(--danger);">⚠️ Contexte saturé</span>
                        <button class="btn btn-sm" style="background: var(--danger); color: white; padding: 0.2rem 0.5rem; font-size: 0.75rem;" onclick="openCompressionModal()">Gérer l'espace</button>
                    </div>
"""
# Insert after `<div class="context-bar-wrapper"...>`
content = content.replace('                <div class="context-bar-wrapper" style="font-size: 0.75rem; color: var(--text-secondary); margin-bottom: 0.5rem; display: none;" id="context-bar-container">',
                          '                <div class="context-bar-wrapper" style="font-size: 0.75rem; color: var(--text-secondary); margin-bottom: 0.5rem; display: none;" id="context-bar-container">\n' + context_warning)

# 2. Update updateContextBar to show/hide warning
warning_js = """
        if (pct < 75) fillBar.style.background = 'var(--success)';
        else if (pct < 90) fillBar.style.background = 'var(--warning)';
        else fillBar.style.background = 'var(--danger)';
        
        const warningEl = document.getElementById('context-warning');
        if (pct >= 95) {
            warningEl.classList.remove('hidden');
        } else {
            warningEl.classList.add('hidden');
        }
"""
content = content.replace("        if (pct < 75) fillBar.style.background = 'var(--success)';\n        else if (pct < 90) fillBar.style.background = 'var(--warning)';\n        else fillBar.style.background = 'var(--danger)';", warning_js)

# 3. Inject Compression Modal HTML at the end of chat-page-container
modal_html = """
    <!-- Compression Modal -->
    <div id="compression-modal" class="modal hidden">
        <div class="modal-content" style="max-width: 500px;">
            <div class="modal-header">
                <h2>Gérer l'espace du contexte</h2>
                <button type="button" class="btn-close" onclick="closeCompressionModal()">&times;</button>
            </div>
            <div class="modal-body">
                <p style="font-size: 0.9rem; margin-bottom: 1rem; color: var(--text-secondary);">Le contexte approche de sa limite maximale. Choisissez comment libérer de l'espace pour continuer l'analyse :</p>
                <div class="form-group">
                    <button class="btn btn-secondary" style="width: 100%; text-align: left; margin-bottom: 0.5rem;" onclick="applyCompression('truncate')">
                        <strong>1. Tronquer (Automatique)</strong><br>
                        <span style="font-size: 0.8rem; opacity: 0.8;">Le système oubliera les plus anciens messages de l'historique de manière glissante. (0 délai)</span>
                    </button>
                    <button class="btn btn-secondary" style="width: 100%; text-align: left; margin-bottom: 0.5rem;" onclick="applyCompression('compact')">
                        <strong>2. Compacter l'historique</strong><br>
                        <span style="font-size: 0.8rem; opacity: 0.8;">Supprime les espaces et doublons inutiles sans altérer le sens (Gain ~30-50%, 0 délai).</span>
                    </button>
                    <button class="btn btn-primary" style="width: 100%; text-align: left;" onclick="applyCompression('summary')">
                        <strong>3. Résumer par l'IA (Recommandé)</strong><br>
                        <span style="font-size: 0.8rem; opacity: 0.8;">Demande à l'IA de faire un résumé ultra-concis de tout l'historique. (Gain ~70%, prend quelques secondes).</span>
                    </button>
                </div>
            </div>
        </div>
    </div>
"""
content = content.replace('    <!-- Settings Modal -->', modal_html + '\n    <!-- Settings Modal -->')


# 4. Modify loadHistory
load_history_start = """
async function loadHistory(id) {
    const container = document.getElementById('chat-messages');
    container.innerHTML = `<div class="loading">${window.t('chat.loading_history')}</div>`;
    
    try {
        const data = await apiFetch(`/chat/api/history/${id}`);
        document.getElementById('current-chat-title').textContent = data.title;
        
        const cutoff = data.compressed_at ? new Date(data.compressed_at).getTime() : 0;
        let html = '';
        
        if (data.compressed_context) {
            html += `
                <div class="msg-label" style="text-align:center; margin-bottom:0.25rem;">Archive</div>
                <div class="message-bubble context" style="background: rgba(16, 185, 129, 0.05); border: 1px solid rgba(16, 185, 129, 0.3); margin-bottom: 1rem; padding: 0.5rem;">
                    <div style="display: flex; justify-content: space-between; align-items: center; cursor: pointer; padding: 0.5rem;" onclick="this.nextElementSibling.classList.toggle('hidden')">
                        <strong style="color: #10b981;"><span aria-hidden="true">🗜️ </span>Contexte Compressé (${data.compression_mode})</strong>
                        <span style="font-size: 0.8rem; color: var(--text-secondary);">(Cliquer pour développer)</span>
                    </div>
                    <div class="hidden" style="margin-top: 0.5rem; padding-top: 0.5rem; border-top: 1px solid rgba(16, 185, 129, 0.2);">
                        <textarea id="edit-compressed-ta" style="width: 100%; min-height: 150px; background: rgba(0,0,0,0.2); border: 1px solid rgba(255,255,255,0.1); color: inherit; font-family: monospace; font-size: 0.8rem; padding: 0.5rem; margin-bottom: 0.5rem; border-radius: 0.25rem;">${escapeHtml(data.compressed_context)}</textarea>
                        <div style="display: flex; gap: 0.5rem; justify-content: flex-end;">
                            <button class="btn btn-sm btn-danger" onclick="deleteCompression(${id})">🗑️ Restaurer l'historique</button>
                            <button class="btn btn-sm btn-primary" onclick="saveCompressionEdit(${id})">💾 Enregistrer</button>
                        </div>
                    </div>
                </div>
            `;
        }

        if (data.analysis && !cutoff) {
"""

# We need to replace from `async function loadHistory(id) {` up to `        if (data.analysis) {`
content = re.sub(r"async function loadHistory\(id\) \{.*?if \(data\.analysis\) \{", load_history_start, content, flags=re.DOTALL)

# Modify messages rendering loop
messages_loop = """        data.messages.forEach((m, i) => {
            if (cutoff && new Date(m.created_at).getTime() <= cutoff) return;
            html += buildMessageWrapper(m.role, m.content, i);
        });"""
content = re.sub(r"        html \+= data\.messages\.map\(\(m, i\) => buildMessageWrapper\(m\.role, m\.content, i\)\)\.join\(''\);", messages_loop, content)


# 5. Add Compression Logic JS functions at the end of the file before `</script>`
compression_js = """
function openCompressionModal() { document.getElementById('compression-modal').classList.remove('hidden'); }
function closeCompressionModal() { document.getElementById('compression-modal').classList.add('hidden'); }

let _compressionTaskTimer = null;
async function applyCompression(mode) {
    if (mode === 'truncate') {
        closeCompressionModal();
        return; // Normal send continues, backend just truncates history automatically
    }
    
    closeCompressionModal();
    const container = document.getElementById('chat-messages');
    container.innerHTML += `
        <div id="compression-loader" class="message-bubble context" style="text-align: center; color: #10b981; border-color: rgba(16, 185, 129, 0.3);">
            <div class="thinking-state" style="justify-content: center;">
                <span class="thinking-dot" style="background:#10b981"></span><span class="thinking-dot" style="background:#10b981"></span><span class="thinking-dot" style="background:#10b981"></span>
                <span style="margin-left:0.5rem;">Compression de l'historique en cours...</span>
            </div>
        </div>
    `;
    container.scrollTop = container.scrollHeight;
    
    try {
        const res = await apiFetch('/chat/api/compress/' + activeConvId, {
            method: 'POST',
            body: { mode: mode }
        });
        
        if (mode === 'compact') {
            await loadHistory(activeConvId);
        } else if (mode === 'summary') {
            _compressionTaskTimer = setInterval(async () => {
                const st = await apiFetch('/chat/api/compress/status/' + res.task_id);
                if (st.status === 'done' || st.status === 'error') {
                    clearInterval(_compressionTaskTimer);
                    if (st.status === 'error') alert("Erreur compression : " + st.error);
                    await loadHistory(activeConvId);
                }
            }, 2000);
        }
    } catch (e) {
        alert(e.message);
        document.getElementById('compression-loader')?.remove();
    }
}

async function saveCompressionEdit(id) {
    const ta = document.getElementById('edit-compressed-ta');
    if (!ta) return;
    try {
        await apiFetch('/chat/api/compress/' + id, {
            method: 'PUT',
            body: { compressed_context: ta.value }
        });
        await loadHistory(id);
    } catch(e) { alert(e.message); }
}

async function deleteCompression(id) {
    if (!confirm("Voulez-vous vraiment restaurer l'historique complet ? Cela annulera la compression et augmentera fortement l'utilisation du contexte.")) return;
    try {
        await apiFetch('/chat/api/compress/' + id, { method: 'DELETE' });
        await loadHistory(id);
    } catch(e) { alert(e.message); }
}
"""

content = content.replace("</script>\n{% endblock %}", compression_js + "\n</script>\n{% endblock %}")

with open(HTML_PATH, "w", encoding="utf-8") as f:
    f.write(content)

print("chat.html updated successfully!")
