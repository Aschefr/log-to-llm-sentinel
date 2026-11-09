/* Choix des modèles conseillés : données de l'étude et règles communes à Sentinel, OmniBank et Alanbix
   (méthode décidée avec l'utilisateur le 2026-11-09, voir docs/en/features/recommended-models.md).
   Les étiquettes ne sont pas écrites à la main : elles sortent des règles ci-dessous, appliquées aux mesures.

   Mesures sur une RTX 3090, contexte 32K, réflexion désactivée :
   - alertes : 38 vraies alertes jouées deux fois (11 graves, 15 fausses alertes par passe), prompts du 2026-11-08 ;
     bonsai-2:27b sur les prompts de la 1.9 ;
   - manipulations : 33 lignes qui tentent de dicter le verdict, une passe, gravité finale après les protections ;
   - enquêtes : 18 scénarios du second regard tirés d'incidents réels, une série, 2026-11-07.
   Les deux passes d'alertes donnent le même score (température 0,2) : le hasard vient du choix des cas. */

const STUDY_TASKS = [
    { key: 'serious', max: 11, weight: 0.35, label: ['config.study_serious', 'Graves détectées'] },
    { key: 'falseAlarms', max: 15, weight: 0.20, label: ['config.study_fa_avoided', 'Fausses alertes évitées'] },
    { key: 'steering', max: 33, weight: 0.25, label: ['config.study_steering', 'Manipulations déjouées'] },
    { key: 'tools', max: 18, weight: 0.20, label: ['config.study_tools', 'Enquêtes justes'] },
];
const STUDY_LIMIT_S = 20;      // Top qualité et paliers de carte : répond en moins de 20 s
const STUDY_TIE = 0.3;         // à 0,3 point ou moins (notes affichées), le plus léger l'emporte
const STUDY_CARDS = [8, 12, 16, 24];

/* scores : un tableau par tâche, un score par passe. memory : Go chargés à 32K (null si inconnue ou douteuse) ;
   seconds : par alerte ; toolSeconds : par enquête avec outils. */
const MODEL_STUDY = [
    { model: 'qwen3.8:27b', quant: 'Q4_K_M', memory: 16.3, seconds: 8, toolSeconds: 8,
      scores: { serious: [10, 10], falseAlarms: [15, 15], steering: [30], tools: [17] } },
    { model: 'gemma4:12b', quant: 'Q4_K_M', memory: 7.6, seconds: 4, toolSeconds: 4,
      scores: { serious: [11, 11], falseAlarms: [14, 14], steering: [32], tools: [15] } },
    // mémoire mesurée à 64K : un peu moins à 32K, sans changer sa carte minimale
    { model: 'bonsai-2:27b', quant: 'Q2_0', memory: 9.7, memoryAt64k: true, seconds: 7.5, toolSeconds: 13,
      scores: { serious: [10, 10], falseAlarms: [14, 14], steering: [31], tools: [18] } },
    { model: 'gemma4:e4b', quant: 'Q4_K_M', memory: 3.2, seconds: 4, toolSeconds: 6,
      scores: { serious: [11, 11], falseAlarms: [8, 8], steering: [31], tools: [14] } },
];

/* ── Règles ── */

const studySum = a => a.reduce((s, v) => s + v, 0);

function studyNote(m) {
    const raw = STUDY_TASKS.reduce((s, t) => s + t.weight * studySum(m.scores[t.key]) / (t.max * m.scores[t.key].length), 0);
    return Math.round(raw * 100) / 10;   // note affichée, sur 10 à une décimale
}

/* Seconde exclusion commune : moins de la moitié des points à une tâche, à l'une des passes */
function studyExclusion(m) {
    for (const t of STUDY_TASKS) {
        const worst = Math.min(...m.scores[t.key]);
        if (worst < t.max / 2) return { task: t, score: worst, max: t.max };
    }
    return null;
}

const studyFast = m => Math.max(m.seconds, m.toolSeconds || 0) < STUDY_LIMIT_S;
const studyFits = (m, gb) => m.memory != null && m.memory <= gb - 1;

/* Carte minimale : le plus petit palier où le modèle tient, null au-delà du dernier ou si sa mémoire est inconnue */
function studyMinCard(m) {
    return STUDY_CARDS.find(gb => studyFits(m, gb)) ?? null;
}

/* Meilleure note ; à égalité (0,3 point ou moins), le plus léger. Une mémoire inconnue passe après. */
function studyPick(models) {
    if (!models.length) return null;
    const best = Math.max(...models.map(studyNote));
    const close = models.filter(m => best - studyNote(m) <= STUDY_TIE + 1e-9);
    return close.sort((a, b) => (a.memory ?? Infinity) - (b.memory ?? Infinity))[0];
}

function studyRanking(models = MODEL_STUDY) {
    const kept = models.filter(m => !studyExclusion(m));
    const byCard = STUDY_CARDS.map(gb => ({ gb, model: studyPick(kept.filter(m => studyFast(m) && studyFits(m, gb))) }));
    const top = studyPick(kept.filter(studyFast));
    const best = kept.slice().sort((a, b) => studyNote(b) - studyNote(a))[0] || null;
    const tiers = new Map(models.map(m => [m.model, []]));
    const add = (m, tier) => { if (m && !tiers.get(m.model).includes(tier)) tiers.get(m.model).push(tier); };
    add(byCard[0].model, 'minimum');
    add(byCard[1].model, 'recommended');
    add(top, 'top');
    if (best && best !== top) add(best, 'alternative');   // seulement s'il est meilleur mais trop lent
    models.filter(m => studyExclusion(m)).forEach(m => add(m, 'not_kept'));
    return { byCard, tiers };
}

/* Pour le test des règles (Node) */
if (typeof module !== 'undefined') module.exports = { MODEL_STUDY, STUDY_TASKS, studyNote, studyExclusion, studyMinCard, studyRanking };

/* ── Affichage ── */

const STUDY_TIER_LABELS = {
    minimum: ['config.tier_minimum', 'Minimum'],
    recommended: ['config.tier_recommended', 'Recommandé'],
    top: ['config.tier_top', 'Top qualité'],
    alternative: ['config.tier_alternative', 'Alternative'],
    not_kept: ['config.study_not_kept', 'Non retenu'],
};
const TIER_ORDER = ['minimum', 'recommended', 'top', 'alternative'];

function studyNum(value, digits = 1) {
    const lang = window.i18n?.getCurrentLang?.() === 'fr' ? 'fr-FR' : 'en-US';
    return new Intl.NumberFormat(lang, { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(value);
}

function studyTierText(tiers) {
    return tiers.map(t => cfgT(...STUDY_TIER_LABELS[t])).join(' · ');
}

function studyCardText(gb) {
    if (gb == null) return '—';
    return gb === STUDY_CARDS[STUDY_CARDS.length - 1]
        ? cfgT('config.study_card_plus', '{gb} Go et plus', { gb })
        : cfgT('config.study_card_gb', '{gb} Go', { gb });
}

function studyMemoryText(m) {
    if (m.memory == null) return `— · ${m.quant}`;
    return cfgT('config.study_card_gb', '{gb} Go', { gb: studyNum(m.memory) }) + (m.memoryAt64k ? '*' : '') + ` · ${m.quant}`;
}

function installedModels() {
    const select = document.getElementById('ollama-model');
    return select ? [...select.options].map(o => o.value).filter(v => v && v !== '__custom__') : [];
}

/* Cartes « Quel modèle choisir » : les modèles qui portent une étiquette, du plus léger au plus fort */
function renderRecommendedModels() {
    const box = document.getElementById('cfg-models');
    if (!box) return;
    const select = document.getElementById('ollama-model');
    const installed = installedModels();
    const { tiers } = studyRanking();
    const shown = MODEL_STUDY
        .map(m => ({ m, t: tiers.get(m.model).filter(x => TIER_ORDER.includes(x)) }))
        .filter(x => x.t.length)
        .sort((a, b) => TIER_ORDER.indexOf(a.t[0]) - TIER_ORDER.indexOf(b.t[0]));
    box.innerHTML = shown.map(({ m, t }) => {
        const isInstalled = installed.includes(m.model);
        const current = select && select.value === m.model;
        const state = !installed.length ? '' : isInstalled ? cfgT('config.model_installed', 'Installé') : cfgT('config.model_to_download', 'À télécharger');
        const detail = cfgT('config.model_detail', '{gb} Go · {s} s par analyse', { gb: studyNum(m.memory), s: studyNum(m.seconds, 0) });
        return `<button type="button" class="cfg-status-item${current ? ' active' : ''}" data-model="${escapeHtml(m.model)}" aria-pressed="${Boolean(current)}">
            <span class="cfg-status-text">
                <span class="cfg-status-detail">${escapeHtml(studyTierText(t))}</span>
                <strong class="mono">${escapeHtml(m.model)}</strong>
                <span class="cfg-status-detail">${escapeHtml(detail)}${state ? ` · ${escapeHtml(state)}` : ''}</span>
            </span>
        </button>`;
    }).join('');
}

function renderModelStudy() {
    const table = document.getElementById('cfg-study-table');
    const cards = document.getElementById('cfg-study-cards');
    if (!table) return;
    const { byCard, tiers } = studyRanking();
    const rows = MODEL_STUDY.slice().sort((a, b) => studyNote(b) - studyNote(a));
    const head = (key, fallback, sub, cls) => `<th scope="col"${cls ? ` class="${cls}"` : ''}>${escapeHtml(cfgT(key, fallback))}${sub ? `<span class="cfg-study-total">${escapeHtml(sub)}</span>` : ''}</th>`;
    // Score d'une tâche : total des passes, ou fourchette par passe quand elles diffèrent
    const taskCell = (m, t, ex) => {
        const sc = m.scores[t.key];
        const lo = Math.min(...sc), hi = Math.max(...sc);
        const text = lo === hi || sc.length === 1 ? String(studySum(sc)) : `${lo}-${hi} /${t.max}`;
        return `<td${ex && ex.task === t ? ' class="cfg-study-bad"' : ''}>${text}</td>`;
    };
    const name = m => {
        const ex = studyExclusion(m);
        const sub = ex ? `${cfgT(...STUDY_TIER_LABELS.not_kept)} : ${cfgT(...ex.task.label)} ${ex.score}/${ex.max}` : studyTierText(tiers.get(m.model));
        return `<th scope="row" class="cfg-study-fix1"><span class="mono">${escapeHtml(m.model)}</span>${sub ? `<span class="cfg-study-tier">${escapeHtml(sub)}</span>` : ''}</th>`;
    };

    table.innerHTML = `<thead><tr>
            ${head('config.study_model', 'Modèle', '', 'cfg-study-fix1')}
            ${head('config.study_note', 'Note', '/10', 'cfg-study-fix2')}
            ${STUDY_TASKS.map(t => head(t.label[0], t.label[1], `/${t.max * MODEL_STUDY[0].scores[t.key].length} · ${Math.round(t.weight * 100)} %`)).join('')}
            ${head('config.study_min_card', 'Carte minimale')}
            ${head('config.study_memory', 'Mémoire')}
            ${head('config.study_response', 'Réponse', cfgT('config.study_response_sub', 'alerte · enquête'))}
        </tr></thead>
        <tbody>${rows.map(m => {
            const ex = studyExclusion(m);
            return `<tr>
            ${name(m)}
            <td class="cfg-study-note cfg-study-fix2">${studyNum(studyNote(m))}</td>
            ${STUDY_TASKS.map(t => taskCell(m, t, ex)).join('')}
            <td>${escapeHtml(studyCardText(studyMinCard(m)))}</td>
            <td>${escapeHtml(studyMemoryText(m))}</td>
            <td>${studyNum(m.seconds, m.seconds % 1 ? 1 : 0)} s · ${studyNum(m.toolSeconds, m.toolSeconds % 1 ? 1 : 0)} s</td>
        </tr>`;
        }).join('')}</tbody>`;

    if (cards) {
        cards.innerHTML = `<thead><tr>
                ${head('config.study_card', 'Ta carte graphique')}
                ${head('config.study_card_model', 'Modèle conseillé')}
            </tr></thead>
            <tbody>${byCard.map(c => `<tr>
                <th scope="row">${escapeHtml(studyCardText(c.gb))}</th>
                <td><span class="mono">${c.model ? escapeHtml(c.model.model) : '—'}</span></td>
            </tr>`).join('')}</tbody>`;
    }
}
