// ─── Monitor Page ──────────────────────────────────────────────────────────
// Gestion des onglets, du live-tail avec colorisation, du buffer anti-spam
// et du panneau de détail au clic.

let monitorRules = [];
let monitorLogLines = 60;
let activeRuleId = null;
let tailIntervals = {};
let bufferIntervals = {};
let isFrozen = false;
let frozenContent = null;
let selectedLineText = null;
let activeKeywordFilter = null;

document.addEventListener('DOMContentLoaded', () => {
    loadMonitorRules();

    document.getElementById('monitor-search-btn').addEventListener('click', searchById);
    document.getElementById('monitor-search-id').addEventListener('keydown', e => {
        if (e.key === 'Enter') searchById();
    });
});

// ─── Chargement des règles / onglets ───────────────────────────────────────

async function loadMonitorRules() {
    try {
        const res = await apiFetch('/api/monitor/rules');
        monitorRules = res.rules || [];
        monitorLogLines = res.monitor_log_lines || 60;
        
        renderTabs();
        if (monitorRules.length > 0) {
            selectTab(monitorRules[0].id);
        } else {
            document.getElementById('monitor-tab-content').innerHTML =
                '<div class="loading">Aucune règle active. <a href="/rules">Créer une règle</a></div>';
        }
    } catch (e) {
        document.getElementById('monitor-tabs').innerHTML =
            `<div class="loading" style="color:var(--danger)">Erreur : ${escapeHtml(e.message)}</div>`;
    }
}

function renderTabs() {
    const tabs = document.getElementById('monitor-tabs');
    tabs.innerHTML = monitorRules.map(r => `
        <button class="monitor-tab" id="tab-${r.id}" onclick="selectTab(${r.id})">
            ${escapeHtml(r.name)}
        </button>
    `).join('');
}

function selectTab(ruleId) {
    // Arrêter les anciens intervalles
    stopAllPolling();
    isFrozen = false;
    frozenContent = null;
    selectedLineText = null;
    activeKeywordFilter = null;
    activeRuleId = ruleId;

    // Mettre à jour l'onglet actif
    document.querySelectorAll('.monitor-tab').forEach(t => t.classList.remove('active'));
    const activeTab = document.getElementById(`tab-${ruleId}`);
    if (activeTab) activeTab.classList.add('active');

    const rule = monitorRules.find(r => r.id === ruleId);
    if (!rule) return;

    renderTabContent(rule);
    startPolling(rule);
}

// ─── Rendu du contenu de l'onglet ─────────────────────────────────────────

function renderTabContent(rule) {
    const kwList = rule.keywords.join(', ') || 'Aucun';
    document.getElementById('monitor-tab-content').innerHTML = `
        <!-- Paramètres de la règle -->
        <div class="monitor-rule-info">
            <div class="rule-info-grid">
                <div><span class="info-label">📁 Fichier</span><code>${escapeHtml(rule.log_file_path)}</code></div>
                <div>
                    <span class="info-label">🔑 Mots-clés (cliquer pour filtrer)</span>
                    <div class="kw-filter-badges" id="kw-filters-${rule.id}">
                        ${rule.keywords.map(kw =>
                            `<span class="log-kw-badge kw-filter-btn" data-kw="${encodeURIComponent(kw)}" onclick="toggleKeywordFilter(this, ${rule.id})">${escapeHtml(kw)}</span>`
                        ).join('')}
                    </div>
                </div>
                <div><span class="info-label">⏱ Anti-spam</span>${rule.anti_spam_delay}s</div>
                <div><span class="info-label">🔔 Seuil</span>${rule.notify_severity_threshold}</div>
            </div>
        </div>

        <!-- Buffer anti-spam -->
        <div class="monitor-buffer-status" id="buffer-status-${rule.id}">
            <span class="buffer-dot idle" id="buffer-dot-${rule.id}"></span>
            <span id="buffer-label-${rule.id}">Buffer inactif</span>
        </div>

        <!-- Visionneuse de logs -->
        <div class="monitor-viewer-header">
            <span class="viewer-title">📄 Log en direct <span class="viewer-linecount" id="linecount-${rule.id}"></span><span class="kw-filter-label hidden" id="kw-filter-label-${rule.id}"></span></span>
            <div class="viewer-actions">
                <button class="btn btn-secondary btn-sm" id="freeze-btn-${rule.id}" onclick="toggleFreeze(${rule.id})">❄️ Figer</button>
                <button class="btn btn-secondary btn-sm" onclick="copyViewerContent(${rule.id})">📋 Copier</button>
            </div>
        </div>
        <div class="monitor-log-viewer" id="log-viewer-${rule.id}">
            <div class="loading">Chargement des logs...</div>
        </div>

        <!-- Panneau de détail -->
        <div class="monitor-detail-panel hidden" id="detail-panel-${rule.id}">
            <div class="detail-panel-header">
                <strong>🔍 Détails de la ligne</strong>
                <button class="btn-icon" onclick="closeDetailPanel(${rule.id})">✕</button>
            </div>
            <div id="detail-panel-content-${rule.id}"></div>
        </div>

        <!-- Analyses récentes -->
        <div class="monitor-analyses-header">
            <strong>📊 Analyses récentes (LLM)</strong>
        </div>
        <div id="monitor-analyses-${rule.id}" class="monitor-analyses-list">
            <div class="loading">Chargement...</div>
        </div>
    `;

    // Charger les analyses immédiatement
    loadRuleAnalyses(rule.id);
}

// ─── Polling ───────────────────────────────────────────────────────────────

function startPolling(rule) {
    const fetchAndRender = async () => {
        await fetchLogs(rule);
    };
    const fetchBuffer = async () => {
        await fetchBufferStatus(rule.id);
    };

    fetchAndRender();
    fetchBuffer();
    tailIntervals[rule.id] = setInterval(fetchAndRender, 3000);
    bufferIntervals[rule.id] = setInterval(fetchBuffer, 2000);
}

function stopAllPolling() {
    Object.values(tailIntervals).forEach(clearInterval);
    Object.values(bufferIntervals).forEach(clearInterval);
    tailIntervals = {};
    bufferIntervals = {};
}

// ─── Fetch & Rendu des logs ────────────────────────────────────────────────

async function fetchLogs(rule) {
    if (isFrozen) return;

    const viewer = document.getElementById(`log-viewer-${rule.id}`);
    if (!viewer) return;

    try {
        const kwParam = rule.keywords.join(',');
        const res = await apiFetch(
            `/api/files/tail?path=${encodeURIComponent(rule.log_file_path)}&lines=${monitorLogLines}&keywords=${encodeURIComponent(kwParam)}`
        );

        if (!res.lines || res.lines.length === 0) {
            viewer.innerHTML = '<em class="no-logs">Fichier vide ou inaccessible.</em>';
            return;
        }

        const isAtBottom = Math.abs((viewer.scrollHeight - viewer.scrollTop) - viewer.clientHeight) < 20;

        viewer.innerHTML = res.lines.map((line, idx) => {
            const rawText = line.text || '';
            const text = escapeHtml(rawText);
            const isSelected = selectedLineText === rawText;
            const matchClass = line.matched ? 'matched' : '';
            const selectClass = isSelected ? 'selected' : '';
            
            const kwBadges = line.matched_keywords && line.matched_keywords.length > 0
                ? `<span class="log-kw-badges">${line.matched_keywords.map(k => `<span class="log-kw-badge">${escapeHtml(k)}</span>`).join('')}</span>`
                : '';
            return `<div class="log-line ${matchClass} ${selectClass}" data-rule="${rule.id}" data-idx="${idx}" data-text="${encodeURIComponent(rawText)}" onclick="onLineClick(this, ${rule.id})">
                <span class="log-text">${text}</span>${kwBadges}
            </div>`;
        }).join('');

        // Mettre à jour le compteur
        const matched = res.lines.filter(l => l.matched).length;
        const lc = document.getElementById(`linecount-${rule.id}`);
        if (lc) lc.textContent = `(${res.lines.length} lignes, ${matched} matchées)`;

        if (isAtBottom) viewer.scrollTop = viewer.scrollHeight;

        // Réappliquer le filtre mot-clé actif
        applyKeywordFilter(rule.id);
        updateFilterLabel(rule.id);

    } catch (e) {
        viewer.innerHTML = `<em class="no-logs" style="color:var(--danger)">Erreur : ${escapeHtml(e.message)}</em>`;
    }
}

// ─── Filtre par mot-clé ────────────────────────────────────────────────────

function toggleKeywordFilter(badgeEl, ruleId) {
    const kw = decodeURIComponent(badgeEl.dataset.kw || '');
    if (!kw) return;

    if (activeKeywordFilter === kw) {
        // Désactiver le filtre
        activeKeywordFilter = null;
    } else {
        // Activer ce filtre
        activeKeywordFilter = kw;
    }

    // Mettre à jour l'apparence de tous les badges de filtre
    document.querySelectorAll('.kw-filter-btn').forEach(b => {
        const bKw = decodeURIComponent(b.dataset.kw || '');
        b.classList.toggle('active', bKw === activeKeywordFilter);
    });

    applyKeywordFilter(ruleId);
    updateFilterLabel(ruleId);
}

function applyKeywordFilter(ruleId) {
    const viewer = document.getElementById(`log-viewer-${ruleId}`);
    if (!viewer) return;

    viewer.querySelectorAll('.log-line').forEach(line => {
        if (!activeKeywordFilter) {
            line.style.display = '';
        } else {
            // Vérifier si cette ligne a un badge correspondant au filtre actif
            const badges = Array.from(line.querySelectorAll('.log-kw-badge'))
                .map(b => b.textContent.trim().toLowerCase());
            line.style.display = badges.includes(activeKeywordFilter.toLowerCase()) ? '' : 'none';
        }
    });
}

function updateFilterLabel(ruleId) {
    const label = document.getElementById(`kw-filter-label-${ruleId}`);
    if (!label) return;
    if (activeKeywordFilter) {
        label.textContent = ` — filtre: "${activeKeywordFilter}"`;
        label.classList.remove('hidden');
    } else {
        label.textContent = '';
        label.classList.add('hidden');
    }
}


async function fetchBufferStatus(ruleId) {
    try {
        const buf = await apiFetch(`/api/monitor/buffer/${ruleId}`);
        const dot = document.getElementById(`buffer-dot-${ruleId}`);
        const label = document.getElementById(`buffer-label-${ruleId}`);
        if (!dot || !label) return;

        if (buf.active) {
            dot.className = 'buffer-dot active';
            const kwStr = buf.matched_keywords.length > 0
                ? ` — mots-clés: <strong>${buf.matched_keywords.map(k => escapeHtml(k)).join(', ')}</strong>`
                : '';
            label.innerHTML = `⏳ Buffer actif [<code>${escapeHtml(buf.detection_id || '...')}</code>] — ${buf.line_count} ligne(s) en attente${kwStr}`;
        } else {
            dot.className = 'buffer-dot idle';
            label.textContent = 'Buffer inactif';
        }
    } catch (e) {
        // Silencieux
    }
}

// ─── Clic sur une ligne ────────────────────────────────────────────────────

async function onLineClick(el, ruleId) {
    const text = decodeURIComponent(el.dataset.text || '');
    const panel = document.getElementById(`detail-panel-${ruleId}`);
    const content = document.getElementById(`detail-panel-content-${ruleId}`);
    if (!panel || !content) return;

    panel.classList.remove('hidden');

    // Mise en évidence de la ligne
    document.querySelectorAll('.log-line.selected').forEach(l => l.classList.remove('selected'));
    el.classList.add('selected');
    selectedLineText = text;

    // Chercher une analyse correspondant à cette ligne
    let relatedAnalysis = null;
    try {
        const analyses = await apiFetch(`/api/monitor/analyses/${ruleId}`);
        relatedAnalysis = analyses.find(a =>
            a.triggered_line && a.triggered_line.includes(text.substring(0, 60))
        );
    } catch (e) {}

    const matchedKws = el.querySelectorAll('.log-kw-badge');
    const kwList = matchedKws.length > 0
        ? Array.from(matchedKws).map(b => b.textContent).join(', ')
        : 'Aucun (ligne non matchée)';

    content.innerHTML = `
        <div class="detail-row">
            <span class="detail-label">Texte de la ligne</span>
            <code class="detail-value">${escapeHtml(text)}</code>
        </div>
        <div class="detail-row">
            <span class="detail-label">Mots-clés détectés</span>
            <span class="detail-value">${matchedKws.length > 0 ? kwList : '<em>Aucun (ligne non filtrée)</em>'}</span>
        </div>
        ${relatedAnalysis ? `
        <div class="detail-row">
            <span class="detail-label">ID de détection</span>
            <code class="detail-value detection-id-badge">${escapeHtml(relatedAnalysis.detection_id || 'N/A')}</code>
        </div>
        <div class="detail-row">
            <span class="detail-label">Sévérité</span>
            <span class="severity-badge ${escapeHtml(relatedAnalysis.severity)}">${escapeHtml(relatedAnalysis.severity)}</span>
        </div>
        <div class="detail-row">
            <span class="detail-label">Analysé le</span>
            <span class="detail-value">${relatedAnalysis.analyzed_at ? formatDate(relatedAnalysis.analyzed_at) : '—'}</span>
        </div>
        <div class="detail-row">
            <span class="detail-label">Réponse LLM</span>
            <div class="detail-value analysis-response markdown-body">${relatedAnalysis.ollama_response ? marked.parse(relatedAnalysis.ollama_response) : '—'}</div>
        </div>
        <div class="detail-actions" style="margin-top: 1rem; border-top: 1px solid var(--border); padding-top: 0.75rem;">
            <button class="btn btn-secondary btn-sm" onclick="retryAnalysis(${relatedAnalysis.id}, this)">🔄 Ré-essayer l'analyse</button>
        </div>
        ` : `<div class="detail-row"><em>Aucune analyse LLM trouvée pour cette ligne.</em></div>`}
    `;
}

function closeDetailPanel(ruleId) {
    const panel = document.getElementById(`detail-panel-${ruleId}`);
    if (panel) panel.classList.add('hidden');
    document.querySelectorAll('.log-line.selected').forEach(l => l.classList.remove('selected'));
    selectedLineText = null;
}

// ─── Figer / copier ────────────────────────────────────────────────────────

function toggleFreeze(ruleId) {
    isFrozen = !isFrozen;
    const btn = document.getElementById(`freeze-btn-${ruleId}`);
    if (btn) {
        btn.textContent = isFrozen ? '▶️ Reprendre' : '❄️ Figer';
        btn.classList.toggle('active', isFrozen);
    }
}

function copyViewerContent(ruleId) {
    const viewer = document.getElementById(`log-viewer-${ruleId}`);
    if (!viewer) return;
    const text = Array.from(viewer.querySelectorAll('.log-line'))
        .map(el => el.querySelector('.log-text')?.textContent || '')
        .join('\n');
    copyToClipboard(text).then(() => alert('Logs copiés !')).catch(() => {});
}

// ─── Analyses récentes ─────────────────────────────────────────────────────

async function loadRuleAnalyses(ruleId) {
    const container = document.getElementById(`monitor-analyses-${ruleId}`);
    if (!container) return;
    try {
        const analyses = await apiFetch(`/api/monitor/analyses/${ruleId}`);
        if (!analyses || analyses.length === 0) {
            container.innerHTML = '<div class="loading">Aucune analyse pour cette règle.</div>';
            return;
        }
        container.innerHTML = analyses.map(a => `
            <div class="monitor-analysis-card">
                <div class="monitor-analysis-header">
                    <span class="detection-id-badge" title="ID de détection">${escapeHtml(a.detection_id || '—')}</span>
                    <span class="analysis-time">${a.analyzed_at ? formatDate(a.analyzed_at) : ''}</span>
                    <span class="severity-badge ${escapeHtml(a.severity)}">${escapeHtml(a.severity)}</span>
                </div>
                <div class="monitor-analysis-keywords">
                    Mots-clés: ${a.matched_keywords.length > 0
                        ? a.matched_keywords.map(k => `<span class="log-kw-badge">${escapeHtml(k)}</span>`).join(' ')
                        : '<em>N/A</em>'}
                </div>
                <div class="analysis-line">${escapeHtml(a.triggered_line || '')}</div>
                <div class="analysis-response markdown-body">${a.ollama_response ? marked.parse(a.ollama_response) : ''}</div>
            </div>
        `).join('');
    } catch (e) {
        container.innerHTML = `<div class="loading" style="color:var(--danger)">Erreur : ${escapeHtml(e.message)}</div>`;
    }
}

async function retryAnalysis(analysisId, btn) {
    const oldHtml = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '⏳...';
    try {
        const res = await apiFetch(`/api/monitor/retry/${analysisId}`, { method: 'POST' });
        if (res.status === 'ok') {
            // Re-cliquer sur la ligne sélectionnée pour rafraîchir le panneau
            const selected = document.querySelector('.log-line.selected');
            if (selected) {
                // On simule un clic sur la ligne pour forcer la mise à jour des données du panneau
                // sans avoir à réécrire toute la logique de onLineClick
                onLineClick(selected, activeRuleId);
            } else {
                // Si on est dans la recherche, on relance la recherche pour voir le résultat mis à jour
                const searchInput = document.getElementById('monitor-search-id');
                if (searchInput && searchInput.value) searchById();
            }
        }
    } catch (e) {
        alert('Erreur: ' + e.message);
    } finally {
        btn.disabled = false;
        btn.innerHTML = oldHtml;
    }
}

// ─── Recherche par ID ──────────────────────────────────────────────────────

async function searchById() {
    const input = document.getElementById('monitor-search-id');
    const resultPanel = document.getElementById('search-result');
    const id = input.value.trim();
    if (!id) return;

    resultPanel.classList.remove('hidden');
    resultPanel.innerHTML = '<div class="loading">Recherche en cours...</div>';

    try {
        const res = await apiFetch(`/api/monitor/search?id=${encodeURIComponent(id)}`);
        if (!res.found) {
            resultPanel.innerHTML = `<div class="search-result-empty">Aucune analyse trouvée pour l'ID <code>${escapeHtml(id)}</code>.</div>`;
            return;
        }
        const a = res.analysis;
        resultPanel.innerHTML = `
            <div class="search-result-card">
                <div class="search-result-close" onclick="document.getElementById('search-result').classList.add('hidden')">✕</div>
                <h3>Résultat pour <code class="detection-id-badge">${escapeHtml(a.detection_id)}</code></h3>
                <div class="detail-row"><span class="detail-label">Règle</span><span class="detail-value">${escapeHtml(a.rule_name)}</span></div>
                <div class="detail-row"><span class="detail-label">Analysé le</span><span class="detail-value">${a.analyzed_at ? formatDate(a.analyzed_at) : '—'}</span></div>
                <div class="detail-row"><span class="detail-label">Sévérité</span><span class="severity-badge ${escapeHtml(a.severity)}">${escapeHtml(a.severity)}</span></div>
                <div class="detail-row"><span class="detail-label">Mots-clés</span><span class="detail-value">${a.matched_keywords.map(k => `<span class="log-kw-badge">${escapeHtml(k)}</span>`).join(' ') || '—'}</span></div>
                <div class="detail-row"><span class="detail-label">Ligne</span><code class="detail-value">${escapeHtml(a.triggered_line || '')}</code></div>
                <div class="detail-row"><span class="detail-label">Analyse LLM</span>
                    <div class="detail-value analysis-response markdown-body">${a.ollama_response ? marked.parse(a.ollama_response) : '—'}</div>
                </div>
                <div class="detail-row"><span class="detail-label">Notifié</span><span class="detail-value">${a.notified ? '✅ Oui' : '❌ Non'}</span></div>
                <div class="detail-actions" style="margin-top: 1rem; border-top: 1px solid var(--border); padding-top: 0.75rem;">
                    <button class="btn btn-secondary btn-sm" onclick="retryAnalysis(${a.id}, this)">🔄 Ré-essayer l'analyse</button>
                </div>
            </div>
        `;
    } catch (e) {
        resultPanel.innerHTML = `<div class="loading" style="color:var(--danger)">Erreur : ${escapeHtml(e.message)}</div>`;
    }
}
