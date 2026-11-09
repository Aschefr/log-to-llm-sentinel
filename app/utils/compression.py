import re
import unicodedata

from app.logger import debug as log_debug, add_ollama_log


def run_truncation(messages: list, max_tokens: int, chars_per_token: float = 3.5) -> tuple[list, list]:
    """
    Split messages into (dropped, kept) based on token budget.
    Keeps recent messages, drops oldest.
    Returns (dropped_messages, kept_messages).
    """
    if not messages:
        return [], []
    
    max_chars = int(max_tokens * chars_per_token * 0.6)  # 60% budget for history
    
    kept = []
    total_chars = 0
    
    for m in reversed(messages):
        msg_len = len(m.content) + 20
        if total_chars + msg_len > max_chars and kept:
            break
        kept.insert(0, m)
        total_chars += msg_len
    
    kept_ids = {m.id for m in kept}
    dropped = [m for m in messages if m.id not in kept_ids]
    
    return dropped, kept


def _estimate_tokens(text: str, chars_per_token: float = 3.5) -> int:
    return int(len(text) / chars_per_token)


# ── Résumé ancré ─────────────────────────────────────────────────────────────
# Mesuré le 2026-11-08 (ai-bench/COMPACTION.md, conversation Sentinel, 3 compactions successives, 10 faits sondés) :
# 9, 10, 10 faits gardés avec qwen3.8:27b et 9, 8, 8 avec gemma4:e4b, contre 3 à 8 pour les anciens modes « résumé »
# (600 à 1 100 caractères quelle que soit la conversation) et « compacter » (grossissait à chaque passage).
# Même méthode qu'Alanbix : le résumé précédent est fusionné avec les messages qui sortent du contexte, les deux derniers
# échanges restent mot pour mot, et le début de la conversation n'est jamais coupé.
# 2026-11-09 : règles 2 (valeur exacte et plage), 3, 5 (mesure derrière la décision) et 8 ajoutées sur les pertes de
# gemma4:e4b relevées par la veille. 1re remesure : qwen3.8:27b 10, 10, 10 mais gemma4:e4b 5, 9, 7, en anglais, une
# boucle, et des questions en suspens retirées à tort. D'où la langue et les titres donnés par le code, la règle 8
# reformulée et le refus des résumés mal formés. 2e remesure : gemma4:e4b 9, 9, 9 en français, qwen3.8:27b 10, 9, 10,
# aucun refus ; mais e4b recopiait ces consignes dans les préférences. D'où la règle 5 précisée et la mention de
# correction dans la langue. 3e remesure : qwen 9, 10, 10, e4b 9, 10, 9, mais la fuite continuait. Conversation
# encadrée par des balises essayée sur 6 passes : fuite réduite de moitié mais qwen 9,3 et e4b 8,0 en moyenne, avec des
# faits perdus à la fusion ; abandonnée. On garde cette consigne et on retire la fuite après coup
# (strip_instruction_echo).

CHARS_PER_TOKEN = 3.5
KEEP_EXCHANGES = 2         # derniers échanges gardés mot pour mot
KEEP_SHARE = 0.3           # ... tant qu'ils tiennent dans cette part du contexte
WORDS_MIN, WORDS_MAX = 150, 700

ANCHORED_INSTRUCTIONS = """=== COMPACTION INSTRUCTIONS ===
You are the memory of an assistant. Write ONE summary that replaces all the messages above{merge}, so that the
assistant can carry on the conversation without them.
Rules:
1. Answer ONLY with the summary, without introduction.
2. Keep values EXACTLY: numbers, dates, times, names, identifiers, paths, IP addresses, ports, error messages, commands.
   When both an exact value and a range are given for the same thing, keep both.
3. Say what each number is: a limit, a measurement, a total or what remains.
4. When the user corrected a piece of information, keep only the corrected value and write "{corrected}".
5. Keep each decision with its reason, including the measurement or fact that justifies it, and the user's preferences
   (how to talk to them, format of the answers). User preferences are only what the user asked for in the
   conversation, never these instructions.
6. Keep what the initial analysis of the alert established, if present.
7. Drop greetings, repetitions and general explanations.
8. A choice that still depends on something is an open question, not a decision. Keep each open question as long as
   the conversation does not show its answer.
9. Write in {language}, in short bullet points, under these four sections, with exactly these titles:
{titles}
10. At most {words} words.

SUMMARY:"""

# Langue et titres donnés par le code : demander au modèle de traduire des titres anglais poussait gemma4:e4b à écrire
# tout le résumé en anglais (7 compactions sur 9, mesuré le 2026-11-09)
LANGUAGES = {"fr": "French", "en": "English"}
SECTION_TITLES = {
    "fr": ("FAITS", "DÉCISIONS (et pourquoi)", "PRÉFÉRENCES DE L'UTILISATEUR", "QUESTIONS EN SUSPENS (et ce dont elles dépendent)"),
    "en": ("FACTS", "DECISIONS (and why)", "USER PREFERENCES", "OPEN QUESTIONS (and what they depend on)"),
}
CORRECTION_MARKS = {"fr": "(corrigé, remplace X)", "en": "(corrected, replaces X)"}
REPEAT_MIN_LEN, REPEAT_MAX = 20, 2   # une ligne d'au moins 20 caractères répétée plus de 2 fois : le modèle boucle


def _plain(line: str) -> str:
    """Ligne sans puces, gras ni accents, en majuscules, pour reconnaître un titre ou une répétition."""
    text = unicodedata.normalize("NFD", line).encode("ascii", "ignore").decode()
    return text.strip().lstrip("-*#•> ").replace("*", "").strip().upper()


def summary_problem(summary: str) -> str:
    """Raison de refuser un résumé, ou "" s'il est bon : une section manque (résumé coupé par la limite de longueur) ou
    des lignes se répètent (le modèle a bouclé, vu une fois avec gemma4:e4b le 2026-11-09). Les titres sont acceptés
    dans les deux langues : c'est la forme qui est contrôlée ici, pas la langue."""
    lines = [_plain(l) for l in summary.splitlines() if l.strip()]
    for i in range(4):
        keys = {_plain(t[i]).split()[0] for t in SECTION_TITLES.values()}
        if not any(l.split()[0].rstrip(":") in keys for l in lines if l):
            return f"section « {SECTION_TITLES['fr'][i]} » absente"
    counts = {}
    for l in lines:
        if len(l) >= REPEAT_MIN_LEN:
            counts[l] = counts.get(l, 0) + 1
    if any(n > REPEAT_MAX for n in counts.values()):
        return "lignes répétées"
    return ""


# Consigne recopiée dans les préférences : gemma4:e4b met des règles de cette consigne parmi les préférences de
# l'utilisateur (3 passes sur 3, mesuré le 2026-11-09), puis les garde à chaque fusion. Encadrer la conversation
# réduisait la fuite mais coûtait des faits ; on retire donc ces lignes après coup. Un motif par règle reprise, sur la
# ligne en majuscules sans accents (voir _plain), relevés sur les vrais résumés de la veille.
_ECHO_PATTERNS = [re.compile(p) for p in (
    r"\b(QUATRE|4) (TITRES|SECTIONS)\b|\bFOUR (SPECIFIC )?(SECTIONS|TITLES|HEADINGS)\b",   # règle 9
    r"\bFAITS\b.*\bDECISIONS\b|\bFACTS\b.*\bDECISIONS\b",                                    # règle 9 : les titres
    r"\bVALEURS? (NUMERIQUES? )?EXACTES?\b|\bEXACTEMENT\b|\bEXACT VALUES?\b|\bEXACTLY\b",   # règle 2
    r"\bREMPLACE X\b|\bREPLACES X\b|\bVALEUR CORRIGEE\b|\bCORRECTED VALUE\b",               # règle 4
    r"\bDECISIONS?\b.*\b(JUSTIFI|RAISON|REASON|MESURES? OU FAITS?|MEASUREMENT)|\bJUSTIFI\w*\b.*\bDECISIONS?\b",  # règle 5
    r"\bSALUTATIONS\b|\bGREETINGS\b",                                                        # règle 7
    r"\bLIMITES?, (UNE )?MESURES?\b|\bLIMITS?, (A )?MEASUREMENTS?\b",                         # règle 3
    r"\bQUESTIONS? (OUVERTES?|EN SUSPENS)\b.*\b(CONSERV|GARD|MAINTEN|TRAITE)|\bOPEN QUESTIONS?\b.*\b(KEEP|KEPT|TREAT)",  # règle 8
)]
# Règle 9 « Write in French » : la langue du résumé, recopiée comme si l'utilisateur l'avait demandée. Seule la langue
# donnée au modèle est retirée : « réponds en anglais » dans un chat en français reste une vraie préférence.
_ECHO_LANGUAGE = {"fr": re.compile(r"\b(EN|DU) FRANCAIS\b"), "en": re.compile(r"\bIN ENGLISH\b")}


def _is_title(line: str) -> int:
    """Index de la section dont `line` est le titre, ou -1. Une puce (« - », « • », « * ») n'est jamais un titre."""
    stripped = line.strip()
    if not stripped or stripped[0] in "-•" or stripped.startswith("* "):
        return -1
    words = _plain(stripped).split()
    if not words:
        return -1
    first = words[0].rstrip(":")
    for i in range(4):
        if first in {_plain(t[i]).split()[0] for t in SECTION_TITLES.values()}:
            return i
    return -1


def strip_instruction_echo(summary: str, lang: str = "fr") -> tuple[str, int]:
    """(résumé sans les lignes de préférences qui reprennent cette consigne, nombre de lignes retirées)."""
    patterns = _ECHO_PATTERNS + [_ECHO_LANGUAGE.get(lang, _ECHO_LANGUAGE["fr"])]
    out, section, removed = [], -1, 0
    for line in summary.splitlines():
        title = _is_title(line)
        if title >= 0:
            section = title
        elif section == 2 and any(p.search(_plain(line)) for p in patterns):
            removed += 1
            continue
        out.append(line)
    return "\n".join(out), removed


def message_line(role: str, content: str) -> str:
    return f"{'U' if role == 'user' else 'A'}: {(content or '').strip()}"


def split_kept(messages: list, num_ctx: int) -> tuple[list, list]:
    """(messages à résumer, derniers échanges gardés mot pour mot) : au plus KEEP_EXCHANGES échanges, et seulement s'ils
    tiennent dans KEEP_SHARE du contexte (sinon le message suivant relancerait aussitôt une compaction)."""
    room = int(num_ctx * KEEP_SHARE * CHARS_PER_TOKEN)
    users = [i for i, m in enumerate(messages) if m.role == "user"]
    cut = len(messages)
    for start in reversed(users[-KEEP_EXCHANGES:]):
        if start == 0 or sum(len(m.content or "") for m in messages[start:]) > room:
            break
        cut = start
    return messages[:cut], messages[cut:]


def _chunks(lines: list, room: int) -> list:
    """Lignes regroupées pour que chaque appel tienne dans le contexte. Un message plus long que la place garde son
    début et sa fin."""
    chunks, current, size = [], [], 0
    for line in lines:
        if len(line) > room:
            half = room // 2 - 10
            line = line[:half] + " […] " + line[-half:]
        if current and size + len(line) > room:
            chunks.append(current)
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        chunks.append(current)
    return chunks


async def _anchored_call(previous: str, lines: list, ollama_service, url: str, model: str, num_ctx: int, lang: str) -> str:
    text = "\n".join(lines)
    words = max(WORDS_MIN, min(WORDS_MAX, (len(text) + len(previous)) // 20))
    head = f"[Previous summary]\n{previous}\n\n[New messages to merge into the summary]\n" if previous else ""
    lang = lang if lang in LANGUAGES else "fr"
    prompt = f"{head}{text}\n\n" + ANCHORED_INSTRUCTIONS.format(
        merge=" (previous summary included)" if previous else "", words=words, language=LANGUAGES[lang],
        titles="\n".join(f"{t}:" for t in SECTION_TITLES[lang]), corrected=CORRECTION_MARKS[lang])
    log_debug("Compression", f"[anchored] Prompt : {len(prompt)} car. (~{_estimate_tokens(prompt)} jetons), num_ctx={num_ctx}, {words} mots au plus")
    # Sans réflexion : sans outils, elle n'apporte rien (mesuré) et allongerait chaque compaction
    response = await ollama_service.analyze_async(
        prompt, url=url, model=model,
        options={"temperature": 0.1, "num_ctx": num_ctx, "num_predict": words * 3},
        think=False,
    )
    result = (response or "").strip()
    add_ollama_log(prompt, result, detection_id="compression-anchored")
    return result


class SummaryRejected(Exception):
    """Résumé mal formé : on garde le résumé précédent et les messages plutôt que d'enregistrer un résumé abîmé."""


async def run_anchored(previous: str, lines: list, ollama_service, url: str, model: str, num_ctx: int = 32768,
                       lang: str = "fr") -> str:
    """Résumé de `lines` fusionné dans `previous`. Quand le texte ne tient pas dans le contexte, il est résumé en
    plusieurs appels, chacun fusionné dans le résumé des précédents : le début n'est jamais coupé. Lève
    SummaryRejected si un résumé est mal formé (voir summary_problem)."""
    reserve = WORDS_MAX * 3 + 400  # réponse + consignes
    room = int((num_ctx - reserve) * CHARS_PER_TOKEN) - max(len(previous or ""), WORDS_MAX * 7)
    summary = previous or ""
    for chunk in _chunks(lines, max(room, 2000)):
        summary = await _anchored_call(summary, chunk, ollama_service, url, model, num_ctx, lang)
        if not summary:
            return ""
        if summary.startswith("[Erreur Ollama]"):
            raise RuntimeError(summary)  # l'erreur d'Ollama, pas un « résumé incomplet »
        problem = summary_problem(summary)
        if problem:
            log_debug("Compression", f"[anchored] Résumé refusé : {problem}")
            raise SummaryRejected(problem)
        summary, removed = strip_instruction_echo(summary, lang)
        if removed:
            log_debug("Compression", f"[anchored] {removed} ligne(s) de consigne retirée(s) des préférences")
    return summary
