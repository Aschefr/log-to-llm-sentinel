document.addEventListener('DOMContentLoaded', () => {
    loadRules();
    setupModal();
});

async function loadRules() {
    try {
        const rules = await apiFetch('/api/rules');
        const container = document.getElementById('rules-list');

        if (rules.length === 0) {
            container.innerHTML = '<div class="loading">Aucune règle configurée</div>';
            return;
        }

        container.innerHTML = rules.map(rule => `
            <div class="rule-card">
                <div class="rule-info">
                    <h3>${escapeHtml(rule.name)}</h3>
                    <p>📁 ${escapeHtml(rule.log_file_path)}</p>
                    <p>🔑 ${rule.keywords.join(', ')}</p>
                    <p>${rule.enabled ? '✅ Activée' : '❌ Désactivée'} | 🔔 ${rule.notify_on_match ? 'Notifications activées' : 'Notifications désactivées'}</p>
                </div>
                <div class="rule-actions">
                    <button class="btn btn-primary btn-sm" onclick="editRule(${rule.id})">✏️ Éditer</button>
                    <button class="btn btn-danger btn-sm" onclick="deleteRule(${rule.id})">🗑️ Supprimer</button>
                </div>
            </div>
        `).join('');
    } catch (error) {
        console.error('Erreur chargement règles:', error);
    }
}

function setupModal() {
    const modal = document.getElementById('rule-modal');
    const addBtn = document.getElementById('add-rule-btn');
    const closeBtn = document.getElementById('close-modal');
    const cancelBtn = document.getElementById('cancel-rule');
    const form = document.getElementById('rule-form');

    addBtn.addEventListener('click', () => {
        resetForm();
        modal.classList.remove('hidden');
    });

    closeBtn.addEventListener('click', () => {
        modal.classList.add('hidden');
    });

    cancelBtn.addEventListener('click', () => {
        modal.classList.add('hidden');
    });

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        await saveRule();
    });
}

function resetForm() {
    document.getElementById('rule-id').value = '';
    document.getElementById('rule-name').value = '';
    document.getElementById('rule-path').value = '';
    document.getElementById('rule-keywords').value = '';
    document.getElementById('rule-context').value = '';
    document.getElementById('rule-enabled').checked = true;
    document.getElementById('rule-notify').checked = true;
    document.getElementById('modal-title').textContent = 'Nouvelle règle';
}

async function saveRule() {
    const id = document.getElementById('rule-id').value;
    const data = {
        name: document.getElementById('rule-name').value,
        log_file_path: document.getElementById('rule-path').value,
        keywords: document.getElementById('rule-keywords').value.split(',').map(k => k.trim()).filter(k => k),
        application_context: document.getElementById('rule-context').value,
        enabled: document.getElementById('rule-enabled').checked,
        notify_on_match: document.getElementById('rule-notify').checked,
    };

    try {
        if (id) {
            await apiFetch(`/api/rules/${id}`, {
                method: 'PUT',
                body: data,
            });
        } else {
            await apiFetch('/api/rules', {
                method: 'POST',
                body: data,
            });
        }

        document.getElementById('rule-modal').classList.add('hidden');
        loadRules();
    } catch (error) {
        console.error('Erreur sauvegarde règle:', error);
        alert('Erreur: ' + error.message);
    }
}

async function editRule(id) {
    try {
        const rule = await apiFetch(`/api/rules/${id}`);
        document.getElementById('rule-id').value = rule.id;
        document.getElementById('rule-name').value = rule.name;
        document.getElementById('rule-path').value = rule.log_file_path;
        document.getElementById('rule-keywords').value = rule.keywords.join(', ');
        document.getElementById('rule-context').value = rule.application_context || '';
        document.getElementById('rule-enabled').checked = rule.enabled;
        document.getElementById('rule-notify').checked = rule.notify_on_match;
        document.getElementById('modal-title').textContent = 'Éditer la règle';
        document.getElementById('rule-modal').classList.remove('hidden');
    } catch (error) {
        console.error('Erreur chargement règle:', error);
    }
}

async function deleteRule(id) {
    if (!confirm('Êtes-vous sûr de vouloir supprimer cette règle ?')) return;

    try {
        await apiFetch(`/api/rules/${id}`, {
            method: 'DELETE',
        });
        loadRules();
    } catch (error) {
        console.error('Erreur suppression règle:', error);
        alert('Erreur: ' + error.message);
    }
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
