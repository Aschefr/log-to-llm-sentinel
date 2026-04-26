document.addEventListener('DOMContentLoaded', () => {
    loadConfig();
    setupForm();
    setupTests();
    setupOllamaModelSelect();
    setupAppriseTags();
    setupModelPulling();
    setupNotificationMethodToggle();
});

function setupNotificationMethodToggle() {
    const select = document.getElementById('notification-method');
    if (!select) return;

    select.addEventListener('change', updateNotificationVisibility);
    updateNotificationVisibility(); // Appliquer au chargement
}

function updateNotificationVisibility() {
    const select = document.getElementById('notification-method');
    if (!select) return;
    const method = select.value;
    const smtpEl = document.getElementById('smtp-section');
    const appriseEl = document.getElementById('apprise-section');
    if (smtpEl)    smtpEl.style.display    = (method === 'smtp')    ? '' : 'none';
    if (appriseEl) appriseEl.style.display = (method === 'apprise') ? '' : 'none';
}


function setPullModel(name) {
    const input = document.getElementById('pull-model-name');
    if (input) input.value = name;
}

function applyProfile(type) {
    const temp = document.getElementById('ollama-temp');
    const ctx = document.getElementById('ollama-ctx');
    const threads = document.getElementById('ollama-threads');
    
    if (type === 'eco') {
        temp.value = 0.1;
        ctx.value = 2048;
        document.getElementById('ollama-think').checked = false;
    } else if (type === 'balanced') {
        temp.value = 0.4;
        ctx.value = 4096;
        document.getElementById('ollama-think').checked = true;
    } else if (type === 'gpu') {
        temp.value = 0.7;
        ctx.value = 8192;
        document.getElementById('ollama-think').checked = true;
    }
    
    // Déclencher l'auto-sauvegarde
    temp.dispatchEvent(new Event('input'));
    ctx.dispatchEvent(new Event('input'));
    threads.dispatchEvent(new Event('input'));
}

function setupModelPulling() {
    const btn = document.getElementById('pull-model-btn');
    const input = document.getElementById('pull-model-name');
    const progressContainer = document.getElementById('pull-progress-container');
    const progressBar = document.getElementById('pull-progress-bar');
    const statusText = document.getElementById('pull-status');

    if (!btn || !input) return;

    btn.addEventListener('click', async () => {
        const model = input.value.trim();
        if (!model) {
            alert('Veuillez saisir un nom de modèle');
            return;
        }

        btn.disabled = true;
        progressContainer.classList.remove('hidden');
        progressBar.style.width = '0%';
        statusText.textContent = `Démarrage du téléchargement de ${model}...`;

        try {
            const response = await fetch('/api/config/pull-model', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ model: model })
            });

            if (!response.ok) {
                const err = await response.json();
                throw new Error(err.detail || 'Erreur lors du pull');
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                const chunk = decoder.decode(value);
                const lines = chunk.split('\n\n');
                
                for (const line of lines) {
                    if (line.startsWith('data: ')) {
                        try {
                            const data = JSON.parse(line.substring(6));
                            if (data.error) throw new Error(data.error);
                            
                            if (data.status) {
                                statusText.textContent = data.status;
                                if (data.completed && data.total) {
                                    const pct = Math.round((data.completed / data.total) * 100);
                                    progressBar.style.width = `${pct}%`;
                                    statusText.textContent = `${data.status} (${pct}%)`;
                                }
                                if (data.status === 'success') {
                                    progressBar.style.width = '100%';
                                    statusText.textContent = '✅ Modèle téléchargé avec succès !';
                                    setTimeout(() => setupOllamaModelSelect(), 2000); // Rafraîchir la liste
                                }
                            }
                        } catch (e) {
                            console.error('Erreur parse chunk:', e);
                        }
                    }
                }
            }
        } catch (e) {
            statusText.textContent = `${window.t ? window.t('common.error') : 'Erreur'} : ${e.message}`;
            statusText.style.color = 'var(--danger)';
        } finally {
            btn.disabled = false;
        }
    });
}

async function loadConfig() {
    try {
        const config = await apiFetch('/api/config');
        document.getElementById('smtp-host').value = config.smtp_host || '';
        document.getElementById('smtp-port').value = config.smtp_port || 587;
        document.getElementById('smtp-user').value = config.smtp_user || '';
        document.getElementById('smtp-password').value = '';
        document.getElementById('smtp-recipient').value = config.smtp_recipient || '';
        // ssl_mode: priorité au nouveau champ, fallback legacy
        const sslMode = config.smtp_ssl_mode || (config.smtp_tls ? 'starttls' : 'none');
        document.getElementById('smtp-ssl-mode').value = sslMode;
        document.getElementById('ollama-url').value = config.ollama_url || 'http://host.docker.internal:11434';
        // Model select is populated async; keep desired value to apply later.
        window.__desiredOllamaModel = config.ollama_model || 'llama3';
        document.getElementById('system-prompt').value = config.system_prompt || '';
        document.getElementById('notification-method').value = config.notification_method || 'smtp';
        document.getElementById('apprise-url').value = config.apprise_url || '';
        document.getElementById('apprise-max-chars').value = config.apprise_max_chars || 1900;
        document.getElementById('max-log-chars').value = config.max_log_chars || 5000;
        document.getElementById('monitor-log-lines').value = config.monitor_log_lines || 60;
        document.getElementById('ollama-temp').value = config.ollama_temp || 0.1;
        document.getElementById('ollama-ctx').value = config.ollama_ctx || 4096;
        const thinkEl = document.getElementById('ollama-think');
        if (thinkEl) thinkEl.checked = config.ollama_think !== false;
        window.__desiredAppriseTags = config.apprise_tags || '';
        const debugEl = document.getElementById('debug-mode');
        if (debugEl) {
            debugEl.checked = config.debug_mode === true;
            toggleLogsContainer(debugEl.checked);
        }
        updateNotificationVisibility();
        window.__configLoaded = true;
    } catch (error) {
        console.error('Erreur chargement config:', error);
    }
}

async function setupOllamaModelSelect() {
    const select = document.getElementById('ollama-model');
    const customGroup = document.getElementById('ollama-model-custom-group');
    const customInput = document.getElementById('ollama-model-custom');
    if (!select || !customGroup || !customInput) return;

    function setCustomVisible(on) {
        customGroup.classList.toggle('hidden', !on);
    }

    select.addEventListener('change', () => {
        const v = select.value;
        if (v === '__custom__') {
            setCustomVisible(true);
            customInput.focus();
        } else {
            setCustomVisible(false);
        }
    });

    // Populate list
    try {
        const res = await apiFetch('/api/config/ollama/models');
        const models = (res && res.models) ? res.models : [];

        const desired = window.__desiredOllamaModel || 'llama3';
        const hasDesired = models.includes(desired);

        const options = [];
        if (models.length === 0) {
            options.push(`<option value="__custom__">Autre…</option>`);
        } else {
            options.push(...models.map(m => `<option value="${escapeHtml(m)}">${escapeHtml(m)}</option>`));
            options.push(`<option value="__custom__">Autre…</option>`);
        }
        select.innerHTML = options.join('');

        if (hasDesired) {
            select.value = desired;
            setCustomVisible(false);
        } else {
            select.value = '__custom__';
            customInput.value = desired;
            setCustomVisible(true);
        }
    } catch (e) {
        // Fallback avec message d'erreur visible
        const errMsg = e && e.message ? e.message : 'Ollama injoignable';
        select.innerHTML = `<option value="__custom__">Autre… (${escapeHtml(errMsg)})</option>`;
        select.value = '__custom__';
        customInput.value = window.__desiredOllamaModel || 'llama3';
        setCustomVisible(true);
    }
}

function setupForm() {
    const form = document.getElementById('config-form');
    const messageEl = document.getElementById('config-message');

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        await saveConfig(messageEl);
    });

    const debugEl = document.getElementById('debug-mode');
    if (debugEl) {
        debugEl.addEventListener('change', () => {
            toggleLogsContainer(debugEl.checked);
        });
    }

    const clearBtn = document.getElementById('clear-logs-btn');
    if (clearBtn) {
        clearBtn.addEventListener('click', async () => {
            try {
                await apiFetch('/api/config/logs', { method: 'DELETE' });
                document.getElementById('debug-logs').innerHTML = '';
            } catch (e) {
                console.error('Erreur effacement logs:', e);
            }
        });
    }

    const clearOllamaBtn = document.getElementById('clear-ollama-logs-btn');
    if (clearOllamaBtn) {
        clearOllamaBtn.addEventListener('click', async () => {
            try {
                await apiFetch('/api/config/ollama/logs', { method: 'DELETE' });
                document.getElementById('ollama-debug-logs').innerHTML = '';
            } catch (e) {
                console.error('Erreur effacement logs Ollama:', e);
            }
        });
    }

    const copyBtn = document.getElementById('copy-logs-btn');
    if (copyBtn) {
        copyBtn.addEventListener('click', () => {
            const logsEl = document.getElementById('debug-logs');
            const text = logsEl.innerText;
            
            copyToClipboard(text).then(() => {
                const oldText = copyBtn.innerHTML;
                copyBtn.innerHTML = '✅ Copié !';
                setTimeout(() => {
                    copyBtn.innerHTML = oldText;
                }, 2000);
            }).catch(err => {
                console.error('Erreur copie:', err);
                alert('Impossible de copier dans le presse-papier. Vérifiez les permissions de votre navigateur.');
            });
        });
    }

    const copyOllamaBtn = document.getElementById('copy-ollama-logs-btn');
    if (copyOllamaBtn) {
        copyOllamaBtn.addEventListener('click', () => {
            const logsEl = document.getElementById('ollama-debug-logs');
            const text = logsEl.innerText;
            copyToClipboard(text).then(() => {
                const oldText = copyOllamaBtn.innerHTML;
                copyOllamaBtn.innerHTML = '\u2705 Copié !';
                setTimeout(() => { copyOllamaBtn.innerHTML = oldText; }, 2000);
            }).catch(err => {
                console.error('Erreur copie Ollama:', err);
                alert('Impossible de copier dans le presse-papier. Vérifiez les permissions de votre navigateur.');
            });
        });
    }

    // Auto-save logic
    const inputs = form.querySelectorAll('input, select, textarea');
    let modifiedInputs = new Set();
    
    const debouncedSave = debounce(async () => {
        const inputsToGlow = Array.from(modifiedInputs);
        modifiedInputs.clear();
        
        const success = await saveConfig(messageEl, true);
        if (success) {
            inputsToGlow.forEach(input => {
                input.classList.add('save-success-glow');
                setTimeout(() => input.classList.remove('save-success-glow'), 1500);
            });
        }
    }, 1000);

    inputs.forEach(input => {
        const eventType = (input.type === 'checkbox' || input.tagName === 'SELECT') ? 'change' : 'input';
        input.addEventListener(eventType, () => {
            modifiedInputs.add(input);
            debouncedSave();
        });
    });
}

function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

function toggleLogsContainer(visible) {
    const container = document.getElementById('debug-logs-container');
    const ollamaContainer = document.getElementById('ollama-logs-container');
    if (container) container.classList.toggle('hidden', !visible);
    if (ollamaContainer) ollamaContainer.classList.toggle('hidden', !visible);

    if (visible) {
        startLogPolling();
    } else {
        stopLogPolling();
    }
}

let logInterval = null;

function startLogPolling() {
    if (logInterval) return;
    logInterval = setInterval(() => {
        fetchLogs();
        fetchOllamaLogs();
    }, 2000);
    fetchLogs();
    fetchOllamaLogs();
}

function stopLogPolling() {
    if (logInterval) {
        clearInterval(logInterval);
        logInterval = null;
    }
}

async function fetchLogs() {
    const container = document.getElementById('debug-logs');
    if (!container) return;

    try {
        const res = await apiFetch('/api/config/logs');
        if (res && res.logs) {
            renderLogs(res.logs);
        }
    } catch (e) {
        console.error('Erreur logs:', e);
    }
}

function renderLogs(logs) {
    const container = document.getElementById('debug-logs');
    if (!container) return;
    
    const wasAtBottom = container.scrollHeight - container.clientHeight <= container.scrollTop + 20;

    container.innerHTML = logs.map(l => `
        <div class="log-entry">
            <span class="log-time">${l.timestamp}</span>
            <span class="log-level ${l.level}">${l.level}</span>
            <span class="log-tag">[${l.tag}]</span>
            <span class="log-message">${escapeHtml(l.message)}</span>
        </div>
    `).join('');

    if (wasAtBottom) {
        container.scrollTop = container.scrollHeight;
    }
}

async function fetchOllamaLogs() {
    const container = document.getElementById('ollama-debug-logs');
    if (!container) return;
    try {
        const res = await apiFetch('/api/config/ollama/logs');
        if (res && res.logs) {
            renderOllamaLogs(res.logs);
        }
    } catch (e) {}
}

function renderOllamaLogs(logs) {
    const container = document.getElementById('ollama-debug-logs');
    if (!container) return;

    // Ne pas écraser le DOM si l'utilisateur a une sélection active dans ce conteneur
    const sel = window.getSelection();
    if (sel && sel.rangeCount > 0 && sel.toString().length > 0) {
        const range = sel.getRangeAt(0);
        if (container.contains(range.commonAncestorContainer)) {
            return; // Protéger la sélection, on rafraîchira au prochain cycle
        }
    }

    const wasAtBottom = container.scrollHeight - container.clientHeight <= container.scrollTop + 20;

    container.innerHTML = logs.map(l => `
        <div class="log-entry ollama-log-entry">
            <div class="log-time">
                ${l.timestamp}
                ${l.detection_id ? `<span class="detection-id-badge" style="margin-left: 10px;">#${escapeHtml(l.detection_id)}</span>` : ''}
            </div>
            <div class="ollama-debug-block">
                <strong>PROMPT :</strong>
                <pre>${escapeHtml(l.prompt)}</pre>
            </div>
            <div class="ollama-debug-block">
                <strong>RÉPONSE :</strong>
                <pre>${escapeHtml(l.response)}</pre>
            </div>
        </div>
    `).reverse().join('');

    if (wasAtBottom) {
        container.scrollTop = container.scrollHeight;
    }
}

function setupTests() {
    const btnOllama = document.getElementById('test-ollama-btn');
    const btnSmtp = document.getElementById('test-smtp-btn');
    const btnApprise = document.getElementById('test-apprise-btn');

    const msgOllama = document.getElementById('ollama-test-message');
    const msgSmtp = document.getElementById('smtp-test-message');
    const msgApprise = document.getElementById('apprise-test-message');

    if (btnOllama && msgOllama) {
        btnOllama.addEventListener('click', async () => {
            await runTest('/api/config/test/ollama', msgOllama, btnOllama);
        });
    }
    if (btnSmtp && msgSmtp) {
        btnSmtp.addEventListener('click', async () => {
            await runTest('/api/config/test/smtp', msgSmtp, btnSmtp);
        });
    }
    if (btnApprise && msgApprise) {
        btnApprise.addEventListener('click', async () => {
            await runTest('/api/config/test/apprise', msgApprise, btnApprise);
        });
    }
}

async function runTest(url, messageEl, buttonEl) {
    const oldText = buttonEl.textContent;
    buttonEl.disabled = true;
    buttonEl.textContent = 'Test en cours...';
    
    const abortController = new AbortController();
    const stopBtn = document.createElement('button');
    stopBtn.className = 'btn btn-danger btn-sm';
    stopBtn.style.marginLeft = '0.5rem';
    stopBtn.innerHTML = '🛑 Arrêter';
    stopBtn.onclick = () => abortController.abort();
    
    buttonEl.parentNode.insertBefore(stopBtn, buttonEl.nextSibling);
    
    try {
        const res = await apiFetch(url, { 
            method: 'POST',
            signal: abortController.signal
        });
        const detail = (res && res.detail) ? res.detail : 'OK';
        showMessage(messageEl, detail, 'success');
    } catch (error) {
        if (error.name === 'AbortError') {
            showMessage(messageEl, 'Test annulé', 'error');
        } else {
            console.error('Erreur test:', error);
            showMessage(messageEl, 'Erreur: ' + (error.message || 'inconnue'), 'error');
        }
    } finally {
        buttonEl.disabled = false;
        buttonEl.textContent = oldText;
        if (stopBtn.parentNode) stopBtn.parentNode.removeChild(stopBtn);
    }
}

async function saveConfig(messageEl, isAutoSave = false) {
    if (!window.__configLoaded) {
        console.warn('Sauvegarde annulée: la config n\'a pas encore été chargée correctement.');
        return false;
    }
    if (isAutoSave) {
        messageEl.textContent = 'Auto-sauvegarde...';
        messageEl.className = 'message info';
        messageEl.classList.remove('hidden');
    }
    const modelSelect = document.getElementById('ollama-model');
    const modelCustom = document.getElementById('ollama-model-custom');
    const chosenModel = (modelSelect && modelSelect.value === '__custom__')
        ? (modelCustom ? modelCustom.value : '')
        : (modelSelect ? modelSelect.value : '');

    const data = {
        smtp_host: document.getElementById('smtp-host').value,
        smtp_port: parseInt(document.getElementById('smtp-port').value) || 587,
        smtp_user: document.getElementById('smtp-user').value,
        smtp_recipient: document.getElementById('smtp-recipient').value,
        smtp_ssl_mode: document.getElementById('smtp-ssl-mode').value,
        smtp_tls: document.getElementById('smtp-ssl-mode').value === 'starttls',
        ollama_url: document.getElementById('ollama-url').value,
        ollama_model: chosenModel,
        system_prompt: document.getElementById('system-prompt').value,
        notification_method: document.getElementById('notification-method').value,
        apprise_url: document.getElementById('apprise-url').value,
        apprise_tags: (document.getElementById('apprise-tags-select').value === '__custom__') 
            ? document.getElementById('apprise-tags').value 
            : document.getElementById('apprise-tags-select').value,
        apprise_max_chars: parseInt(document.getElementById('apprise-max-chars').value) || 1900,
        max_log_chars: parseInt(document.getElementById('max-log-chars').value) || 5000,
        monitor_log_lines: parseInt(document.getElementById('monitor-log-lines').value) || 60,
        ollama_temp: parseFloat(document.getElementById('ollama-temp').value) || 0.1,
        ollama_ctx: parseInt(document.getElementById('ollama-ctx').value) || 4096,
        ollama_think: document.getElementById('ollama-think').checked,
        debug_mode: document.getElementById('debug-mode') ? document.getElementById('debug-mode').checked : false,
    };

    const pwd = document.getElementById('smtp-password').value;
    if (pwd) data.smtp_password = pwd;


    try {
        await apiFetch('/api/config', {
            method: 'PUT',
            body: data,
        });
        const successMsg = isAutoSave ? 'Auto-sauvegardé' : 'Configuration sauvegardée avec succès';
        showMessage(messageEl, successMsg, 'success');
        return true;
    } catch (error) {
        console.error('Erreur sauvegarde config:', error);
        showMessage(messageEl, 'Erreur: ' + error.message, 'error');
        return false;
    }
}

async function setupAppriseTags() {
    const select = document.getElementById('apprise-tags-select');
    const customGroup = document.getElementById('apprise-tags-custom-group');
    const customInput = document.getElementById('apprise-tags');
    if (!select || !customGroup || !customInput) return;

    select.addEventListener('change', () => {
        customGroup.classList.toggle('hidden', select.value !== '__custom__');
    });

    try {
        const res = await apiFetch('/api/config/apprise/tags');
        const tags = (res && res.tags) ? res.tags : [];
        const desired = window.__desiredAppriseTags || '';

        const options = [];
        if (tags.length > 0) {
            options.push(...tags.map(t => `<option value="${escapeHtml(t)}">${escapeHtml(t)}</option>`));
        }
        options.push('<option value="__custom__">Autre (saisie manuelle)…</option>');
        select.innerHTML = options.join('');

        if (desired && tags.includes(desired)) {
            select.value = desired;
            customGroup.classList.add('hidden');
        } else {
            select.value = '__custom__';
            customInput.value = desired;
            customGroup.classList.remove('hidden');
        }
    } catch (e) {
        select.innerHTML = '<option value="__custom__">Autre (saisie manuelle)…</option>';
        select.value = '__custom__';
        customInput.value = window.__desiredAppriseTags || '';
        customGroup.classList.remove('hidden');
    }
}

window.setAppriseMax = (val) => {
    const input = document.getElementById('apprise-max-chars');
    if (input) {
        input.value = val;
        // Trigger save if auto-save is enabled
        input.dispatchEvent(new Event('input'));
    }
}
