import json
import re

# Update HTML
html_path = "templates/chat.html"
with open(html_path, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Add context bar HTML
bar_html = """
                <div class="context-bar-wrapper" style="font-size: 0.75rem; color: var(--text-secondary); margin-bottom: 0.5rem; display: none;" id="context-bar-container">
                    <div style="display: flex; justify-content: space-between; margin-bottom: 0.2rem;">
                        <span data-i18n="chat.context_usage">Utilisation du contexte</span>
                        <span id="context-usage-text">0 / 4096 tokens</span>
                    </div>
                    <div class="progress" style="height: 4px; background: rgba(255,255,255,0.1); border-radius: 2px; overflow: hidden;">
                        <div id="context-usage-bar" style="height: 100%; width: 0%; background: var(--success); transition: width 0.3s, background 0.3s;"></div>
                    </div>
                </div>
                <div class="chat-input-wrapper">
"""
content = content.replace('                <div class="chat-input-wrapper">', bar_html.strip(), 1)

# 2. Add JS logic
js_logic = """
// ── CHAT-07: Context management ──────────────────────────────────────────────
window._chatContextData = null;

function estimateTokens(text) {
    if (!text) return 0;
    // Heuristic: ~3.5 chars per token for Llama/Gemma models.
    return Math.ceil(text.length / 3.5);
}

function updateContextBar() {
    if (!window._chatContextData) return;
    const base = window._chatContextData.base_prompt;
    const max = window._chatContextData.ollama_ctx;
    const input = document.getElementById('chat-input').value;
    
    // Exact payload the backend will send
    const fullText = base + "\\nUtilisateur : " + input + "\\nAssistant : ";
    const tokens = estimateTokens(fullText);
    
    const fillBar = document.getElementById('context-usage-bar');
    const fillText = document.getElementById('context-usage-text');
    const container = document.getElementById('context-bar-container');
    
    if (fillBar && fillText && container) {
        container.style.display = 'block';
        const pct = Math.min(100, Math.max(0, (tokens / max) * 100));
        fillBar.style.width = pct + '%';
        fillText.textContent = `${tokens} / ${max} tokens`;
        
        if (pct < 75) fillBar.style.background = 'var(--success)';
        else if (pct < 90) fillBar.style.background = 'var(--warning)';
        else fillBar.style.background = 'var(--danger)';
    }
}
"""

# Insert JS before selectConversation
content = content.replace('async function loadConversations() {', js_logic + '\nasync function loadConversations() {', 1)

# 3. Add to loadHistory
load_history_end = """        } catch (_) { /* pending check optionnel, on ignore les erreurs */ }

        // Mettre à jour la barre de contexte
        try { window._chatContextData = await apiFetch('/chat/api/context/' + id); updateContextBar(); } catch(_){}

    } catch (e) {"""
content = content.replace('        } catch (_) { /* pending check optionnel, on ignore les erreurs */ }\n\n    } catch (e) {', load_history_end, 1)

# 4. Add to readStream done
read_stream_done = """                        // ── Auto-nommage au 1er échange complet ────────────────────────"""
read_stream_done_new = """                        // Mettre à jour le contexte après génération
                        try { window._chatContextData = await apiFetch('/chat/api/context/' + activeConvId); updateContextBar(); } catch(_){}
                        
                        // ── Auto-nommage au 1er échange complet ────────────────────────"""
content = content.replace(read_stream_done, read_stream_done_new, 1)

# 5. Add input listener
input_listener = """        input.addEventListener('input', function() {
            this.style.height = 'auto';
            this.style.height = (this.scrollHeight) + 'px';
            if (typeof updateContextBar === 'function') updateContextBar();
        });"""
content = content.replace("        input.addEventListener('input', function() {\n            this.style.height = 'auto';\n            this.style.height = (this.scrollHeight) + 'px';\n        });", input_listener, 1)

with open(html_path, "w", encoding="utf-8") as f:
    f.write(content)

# Update i18n JSONs
for lang, trans in [("fr", "Utilisation du contexte"), ("en", "Context Usage")]:
    p = f"static/i18n/{lang}.json"
    with open(p, "r", encoding="utf-8-sig") as f:
        d = json.load(f)
    if "chat" not in d: d["chat"] = {}
    d["chat"]["context_usage"] = trans
    with open(p, "w", encoding="utf-8-sig") as f:
        json.dump(d, f, ensure_ascii=False, indent=4)
        f.write("\n")

print("UI updated successfully.")
