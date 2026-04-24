document.addEventListener('DOMContentLoaded', () => {
    loadConfig();
    setupForm();
    setupTests();
    setupOllamaModelSelect();
});

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
        document.getElementById('apprise-tags').value = config.apprise_tags || '';
        const debugEl = document.getElementById('debug-mode');
        if (debugEl) {
            debugEl.checked = config.debug_mode === true;
            toggleLogsContainer(debugEl.checked);
        }
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
        clearBtn.addEventListener('click', () => {
            document.getElementById('debug-logs').innerHTML = '';
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
    if (container) {
        container.classList.toggle('hidden', !visible);
        if (visible) {
            startLogPolling();
        } else {
            stopLogPolling();
        }
    }
}

let logInterval = null;

function startLogPolling() {
    if (logInterval) return;
    logInterval = setInterval(fetchLogs, 2000);
    fetchLogs();
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
    try {
        const res = await apiFetch(url, { method: 'POST' });
        const detail = (res && res.detail) ? res.detail : 'OK';
        showMessage(messageEl, detail, 'success');
    } catch (error) {
        console.error('Erreur test:', error);
        showMessage(messageEl, 'Erreur: ' + (error.message || 'inconnue'), 'error');
    } finally {
        buttonEl.disabled = false;
        buttonEl.textContent = oldText;
    }
}

async function saveConfig(messageEl, isAutoSave = false) {
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
        apprise_tags: document.getElementById('apprise-tags').value,
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
