# PRD — Briefing Matinal Hermes (v2)

> **Versions** : v1 (init repo) → **v2 (révision post-analyse, 2026-04-19)**
> Changements majeurs v2 : cadence 2x/jour, budget "5 min = triage pur", politique unifiée, short-links avec tracking de clics, data model explicite, modes d'erreur documentés, prompts LLM spécifiés.

## Problème

Chris consomme son actualité de manière passive : scroll X soumis à l'algorithme, YouTube au hasard, Journal de Québec en diagonale. Résultat :

- **Biais algorithmique** — X sert ce qui engage, pas ce qui avance
- **Couverture inégale** — AI/Tech sur-alimenté, santé/politique/Tesla quasi absent
- **Pas de trace** — rien d'archivé, rien de structuré, impossible de revenir en arrière
- **Perte de temps** — 30-45 min de scroll dispersé au lieu de 5 min de lecture ciblée

## Solution

Un pipeline automatisé qui produit deux briefings HTML par jour (matin + soir), livrés sur Telegram, couvrant les sujets qui comptent pour Chris — pas ceux que l'algorithme veut lui montrer. Chaque briefing est conçu comme un **outil de triage ≤ 5 min**, pas comme une lecture complète. Les liens pertinents alimentent une queue de deep-dives pour le reste de la journée.

## Utilisateur

**Christian Boulet** — consultant TI (BST), Québec QC
- Sharp 9h-13h — le briefing matin doit être prêt avant
- Intérêts : AI/Tech, Tesla, SpaceX, santé (longévité, jeûne, ménopause), politique (QC+Canada+US+international), business
- Consomme sur téléphone (Telegram) — format mobile-first
- Veut des liens cliquables, pas un mur de texte
- **Parfaitement bilingue FR-EN** : structure du briefing en français québécois, citations/titres anglophones conservés tels quels (pas de traduction forcée)
- Apprécie les sources : Cleo Abram, Diary of a CEO, Tom Bilyeu, David Sinclair, Karpathy, Daniel Miessler, CernBasher

## Décisions V1 (log)

Décisions prises lors de la révision du PRD ; elles cadrent la V1 et ne sont pas re-négociables sans passer par v3.

| # | Décision | Valeur |
|---|---|---|
| D1 | Cadence | **2x/jour** — 6h45 + 17h30 America/Toronto |
| D2 | Cadence weekends | **Identique** (sam/dim = 2x/jour) |
| D3 | Budget lecture | **Triage pur ≤ 5 min** — deep-dives en queue |
| D4 | Sections politique | **1 section unifiée**, 3 items max |
| D5 | "À ne pas manquer" | **Dans les deux briefings** (1 item chacun) |
| D6 | Tracking | **Short-links + log de clics** (redirector dédié) |
| D7 | Langue | **FR-QC structurel**, bilingue toléré sans traduction |

## Spécifications fonctionnelles

### S1 — Sourcing multi-source

Le pipeline interroge 3 types de sources en parallèle pour chaque briefing :

| Source | Méthode | Données |
|---|---|---|
| Comptes X surveillés | xAI Responses API, tool `x_search` avec `allowed_x_handles` | Posts des comptes de la liste dans la fenêtre (2 appels car max 10 handles/call) |
| Recherche X thématique | xAI Responses API, tool `x_search` (query libre, sans handle restriction) | Requêtes thématiques (Tesla, SpaceX, santé, etc.) |
| Web | xAI Responses API, tool `web_search` | Journaux QC + recherches actualité (filtre `allowed_domains` côté prompt) |

**Fenêtres temporelles** (America/Toronto) :
- **Briefing matin (6h45)** : couvre `[hier 17h30 → aujourd'hui 6h30]` (~13h, majoritairement nocturne)
- **Briefing soir (17h30)** : couvre `[aujourd'hui 6h30 → aujourd'hui 17h15]` (~11h, journée complète)

La fenêtre est calculée dynamiquement au lancement ; un lag de 15 min avant l'heure cible sert de tampon de collecte.

**Filtres de qualité appliqués à tous les posts X** :
- Exclure les replies (`in_reply_to` non nul)
- Exclure les retweets purs (préférer le post original si présent)
- Exclure les posts < 80 caractères (probablement du bruit/meme)
- Score d'engagement minimal : likes ≥ 50 OU reposts ≥ 10 (paramétrable)

**Dé-duplication** (appliquée avant rendu) :
- Clé primaire : URL canonique (domaine + path, query params supprimés sauf `v=` YouTube)
- Clé secondaire : SHA-1 des 200 premiers caractères du titre normalisé (lowercase, ponctuation supprimée)
- Si deux items partagent une clé, on garde celui avec le score le plus élevé ; les sources alternatives sont listées en "via ... + N autres"

### S1.bis — Prompts & critères de sélection LLM

Le LLM (xAI Grok) reçoit pour chaque appel un prompt structuré. Les templates de prompts sont versionnés dans `prompts/` :

- `prompts/x_search.txt` — recherche sur un compte X ou un thème
- `prompts/web_search.txt` — recherche web thématique
- `prompts/rank.txt` — sélection finale par section (si on excède `max_items`)
- `prompts/brief_60s.txt` — composition des 3 items "EN 60 SECONDES"
- `prompts/dont_miss.txt` — sélection du "À NE PAS MANQUER"

**Critères de pertinence** (inclus dans tous les prompts de sélection) :
1. **Signal > bruit** : préférer une annonce concrète (release, chiffre, décision) à une opinion
2. **Primauté** : une source primaire (compte officiel, communiqué) > une reprise
3. **Fraîcheur** : dans la fenêtre temporelle, plus récent > plus vieux, toutes choses égales
4. **Pertinence Chris** : match avec ses intérêts déclarés (cf. section Utilisateur)
5. **Évitement doublon cross-section** : si un item peut aller dans 2 sections, le placer dans la plus spécifique

**Sélection "EN 60 SECONDES"** : 3 items choisis *après* que toutes les sections sont remplies, parmi l'union des items sélectionnés. Critère : convergence inter-sources (plusieurs sources indépendantes mentionnent le même événement) OU impact mesurable (chiffre, décision réglementaire, launch).

**Sélection "À NE PAS MANQUER"** : 1 item par briefing, choisi dans l'union des items *non retenus* par les sections (pour éviter la redondance). Privilégier : vidéo longue (si matin = queue de la journée) ou thread analytique (si soir = lecture de soirée).

### S2 — Compilation HTML

Le briefing est un fichier HTML standalone rendu par **Jinja2** (décision : pas de string formatting, Jinja2 imposé pour l'auto-escape).

**Structure du document** :

```
☀️ BRIEFING MATIN — [jour long FR, date]        (ou 🌙 BRIEFING SOIR)
─────────────────────
⚡ EN 60 SECONDES                  — 3 faits marquants
─────────────────────
🤖 AI / TECH                       — 3 items max
🚗 TESLA                           — 2 items max
🚀 SPACEX                          — 1-2 items max
🏥 SANTÉ                           — 2 items max
🏛️ POLITIQUE                       — 3 items max (QC+Canada+US+Inter unifié)
💼 BUSINESS                        — 2 items max
─────────────────────
📌 À NE PAS MANQUER                — 1 item (vidéo / thread / article long)
─────────────────────
⚙️ Méta : config hash, briefing_id, version
```

**Volume cible** : ~13-15 items par briefing = ~3-4 min de scan en triage pur. Le matin tend à être légèrement plus court (nuit plus calme), le soir plus dense — c'est naturel, pas besoin de forcer.

**Chaque item** :
- Titre (cliquable via short-link, cf. S6) — **langue d'origine préservée**
- 1-2 lignes de contexte en **français québécois**, style télégraphique
- Emoji de catégorie en préfixe
- Source indiquée en suffixe (ex : `via @cernbasher` ou `via JDQ`)
- Si multi-sources convergent : `via @X + 2 autres`

**Contraintes design** :
- **Mobile-first** — lisible sur Telegram iPhone/Android sans zoom
- **Auto dark/light** via `prefers-color-scheme` (palette unique neutre en fallback)
- **CSS inline** (balise `<style>` dans `<head>` OK, pas de fichier externe, pas de CDN)
- **Pas de JS**, pas de Google Fonts — polices système uniquement
- **Budget lisibilité ~50 KB** (pas une limite Telegram — limite self-imposée pour scan rapide ; Telegram document accepte 2 GB)
- **Accessibilité** : contraste AA minimum, taille de police ≥ 15px, liens clairement distincts

### S3 — Livraison Telegram

**Flux** :
1. Le cron Hermes déclenche le script à `HH-00:00:15` (6h44:45 / 17h29:45) — 15 s de marge réseau
2. Le script produit `output/YYYY-MM-DD-[matin|soir].html` + écrit sur stdout un JSON :
   ```json
   {"status": "ok", "path": "output/2026-04-19-matin.html", "briefing_id": "...", "items_count": 14}
   ```
3. Hermes lit le JSON, envoie le HTML comme **document Telegram** au chat de Chris
4. Hermes attend 200 OK, logge le `message_id` Telegram pour traçabilité

**Contrat script → Hermes** :
- Exit code `0` = succès, stdout JSON valide attendu
- Exit code `≠ 0` = échec, stderr contient le diagnostic
- Timeout soft : 150 s ; hard : 180 s (Hermes kill au-delà)

**Fallback en cas d'échec Telegram** :
1. Dépôt sur NAS : `/mnt/nas/commun/briefing-matinal/YYYY-MM-DD-[matin|soir].html`
2. Notification Hermes à Chris via canal secondaire (email ou SMS Hermes) : "Briefing du [moment] dispo sur NAS, Telegram a échoué : [raison]"

### S4 — Archivage

Chaque briefing est archivé en deux endroits :

- **Local repo** : `output/YYYY-MM-DD-[matin|soir].html` — gitignored
- **NAS** : `/mnt/nas/commun/briefing-matinal/YYYY-MM-DD-[matin|soir].html`

**Rétention** :
- Local : rotation 90 jours (cron de cleanup quotidien)
- NAS : illimité (c'est la trace long terme)

**Versionnage de la config** : chaque HTML archivé contient un commentaire HTML en pied de page avec le **hash SHA-256 de `comptes.json`** au moment du rendu, le **git commit hash** du repo, et la **version des prompts**. Permet de reproduire ou auditer un briefing passé.

### S5 — Paramètres configurables

`sources/comptes.json` est le fichier unique de configuration runtime. Il est validé au démarrage du script contre `sources/comptes.schema.json` (JSON Schema draft 2020-12) ; échec de validation = exit `2` avec diagnostic.

**Champs** :

```json
{
  "schema_version": 1,
  "comptes_x": ["@karpathy", "@DanielMiessler", ...],
  "recherches_thematiques": [
    {"theme": "Tesla", "query": "...", "section_id": "tesla"},
    {"theme": "Politique Canada", "query": "...", "section_id": "politique"},
    {"theme": "Politique US", "query": "...", "section_id": "politique"},
    {"theme": "Politique International", "query": "...", "section_id": "politique"}
  ],
  "sources_web": ["journaldequebec.com", "lesaffaires.com", "lapresse.ca"],
  "sections": [
    {"id": "ai-tech", "label": "AI / Tech", "emoji": "🤖", "max_items": 3},
    {"id": "tesla", "label": "Tesla", "emoji": "🚗", "max_items": 2},
    {"id": "spacex", "label": "SpaceX", "emoji": "🚀", "max_items": 2},
    {"id": "sante", "label": "Santé", "emoji": "🏥", "max_items": 2},
    {"id": "politique", "label": "Politique", "emoji": "🏛️", "max_items": 3},
    {"id": "business", "label": "Business", "emoji": "💼", "max_items": 2}
  ],
  "engagement_min": {"likes": 50, "reposts": 10}
}
```

**Règle de mapping N→1** : plusieurs `recherches_thematiques.section_id` peuvent pointer vers la même section (ex : les 3 recherches politiques pointent vers `politique`). Le script agrège puis sélectionne `max_items` finaux.

**Ajout d'une nouvelle section** : seulement éditer `sections` + (optionnellement) ajouter des `recherches_thematiques` pointant dessus. Aucun code à toucher si la section suit le pattern standard. Le template Jinja2 itère dynamiquement sur la config.

### S6 — Tracking clics & short-links (via Tailscale)

Tous les liens du briefing passent par un **redirector minimal** qui tourne sur la machine hermes-agent et est **exposé uniquement sur le tailnet Chris** (Tailscale). Aucun VPS, aucun port ouvert sur internet public, aucun tunnel Cloudflare nécessaire.

**Architecture** :
- Service FastAPI (ou équivalent Python léger) sur la machine hermes-agent, bindé sur l'IP Tailscale de la machine (`tailscale0`)
- Hostname via MagicDNS : `hermes.<tailnet-name>.ts.net` avec **HTTPS** (certs Tailscale auto-provisionnés — éviter http:// qui fait flagguer Telegram/iOS)
- Route : `GET /b/:short_id` → `302 Redirect` vers l'URL cible
- Storage : SQLite (`~/briefing_tracker.db`) — tables `short_links`, `clicks`
- Accessibilité : seuls les devices sur le tailnet de Chris (téléphone, laptop) résolvent le hostname et suivent les redirects

**Génération** :
- Au moment du rendu, chaque URL unique reçoit un `short_id` de 8 chars (base62, dérivé du SHA-256 de l'URL pour idempotence)
- Le script insère/UPSERT dans `short_links` avec : `short_id`, `target_url`, `briefing_id`, `section_id`, `item_title`, `created_at`
- Le HTML utilise `https://hermes.<tailnet>.ts.net/b/:short_id`

**Log des clics** :
- Chaque hit enregistre : `short_id`, `clicked_at`, `user_agent`
- Pas besoin de logger IP (tout vient du tailnet privé de Chris — un seul utilisateur de toute façon)
- Pas de cookies, pas de fingerprinting

**Edge case — Tailscale déconnecté** : si Chris clique depuis un device hors tailnet (avion, VPN corpo qui override), le hostname ne résout pas → lien mort. Impact : clic perdu + frustration. Atténuation V1 : accepter (cas rare). V1.5 éventuelle : encoder l'URL cible en base64 dans le path (`/b/:short_id/:b64_fallback`) pour qu'un bookmark local puisse recover même offline.

**Effet de bord désirable** : Telegram tente l'unfurl des URLs depuis **ses serveurs** (pas depuis ton téléphone). Les hostnames `*.ts.net` privés ne résolvent pas côté Telegram → **pas de preview générée**, briefing visuellement plus propre, zéro requête parasite dans les logs de tracking.

**Rétention clics** : 12 mois glissants, purge mensuelle.

**Exposition de la métrique** : endpoint `/stats?from=YYYY-MM-DD&to=YYYY-MM-DD` (toujours sur tailnet) qui retourne JSON pour alimenter un futur dashboard ou la PRD v3 ("apprentissage des clics").

### Data model

Un **Item** est l'unité de contenu manipulée dans tout le pipeline. Structure canonique :

```python
@dataclass
class Item:
    id: str                    # SHA-1(canonical_url)[:12]
    title: str                 # Langue d'origine préservée
    summary: str               # 1-2 lignes FR-QC générées par LLM
    canonical_url: str         # URL dédupliquée
    short_url: str             # URL via redirector (rempli au rendu)
    section_id: str            # Clé FK vers sections[].id
    source_type: str           # "x_account" | "x_search" | "web"
    source_handle: str         # "@cernbasher" | "Tesla (search)" | "journaldequebec.com"
    published_at: datetime     # UTC
    score: float               # 0.0-1.0, output du ranking LLM
    raw_excerpt: str           # Bout brut pour audit, non rendu
    alt_sources: list[str]     # Autres sources si dédupliqué (pour "via X + 2 autres")
```

Un **Briefing** est la collection finale :

```python
@dataclass
class Briefing:
    briefing_id: str           # "2026-04-19-matin"
    moment: Literal["matin", "soir"]
    generated_at: datetime     # UTC
    window_start: datetime
    window_end: datetime
    sixty_seconds: list[Item]  # 3 items
    sections: dict[str, list[Item]]  # section_id → items triés
    dont_miss: Item
    config_hash: str           # SHA-256 de comptes.json
    prompts_version: str       # ex: "prompts-v1.2"
    git_commit: str            # 7-char commit du repo
```

## Modes d'erreur & dégradation

Matrice de comportement face aux pannes externes. Aucune panne ne doit laisser Chris sans briefing ni sans message.

| Panne | Détection | Réaction |
|---|---|---|
| xAI Grok 5xx / timeout | HTTP ≥ 500 ou > 30s | 2 retries avec backoff (2s, 8s). Si échec persistant : briefing sans section X, note en entête "⚠️ X indisponible" |
| xAI quota dépassé | HTTP 429 | Log + backoff 60s + 1 retry. Si persiste : même traitement que 5xx |
| `web_search` 0 résultat | count == 0 | Pas d'erreur : on saute proprement, section vide skippée dans le rendu |
| `web_search` timeout | > 20s | 1 retry. Si échec : briefing sans items web, note discrète |
| Config invalide (schema) | Validation boot | Exit `2`, Hermes alerte Chris immédiatement, pas de briefing envoyé |
| Telegram 4xx/5xx | send_document ≠ 200 | 1 retry à 5s, puis fallback NAS + notif Hermes |
| NAS indisponible | mount/write fail | Log d'erreur, ne bloque pas Telegram. Si Telegram aussi down : exit `3`, Hermes envoie le HTML inline par email |
| Aucun item après filtrage | items_count == 0 global | Briefing minimal avec message "Journée calme — rien de marquant dans la fenêtre. Prochain briefing [HH:MM]." |
| Redirector down au rendu | HTTP fail sur healthcheck | Fallback : liens directs sans short-link (tracking perdu pour ce briefing, note en pied) |

**Observabilité** :
- Logs structurés JSON sur stdout → captés par systemd/journald côté Hermes
- Métriques simples : latence par étape (sourcing, ranking, rendu, envoi), tokens LLM consommés, nombre d'items par section
- Alerte active si : 2 échecs consécutifs du même briefing OU dépassement budget mensuel (> 15 $)

## Dev/prod & testing

**Séparation des environnements** via variable d'env `BRIEFING_ENV` :
- `production` — envoi au chat Telegram de Chris, écrit sur NAS, log redirector en SQLite prod
- `staging` — envoi à un chat Telegram de test (groupe dédié), écrit sur `output/staging/`, SQLite staging
- `dry-run` — ne poste rien, génère le HTML, print le JSON de sortie, quitte

**Flag CLI** :
```
python scripts/build-briefing.py --moment matin --dry-run
python scripts/build-briefing.py --moment soir --env staging
python scripts/build-briefing.py --moment matin --fixture tests/fixtures/2026-04-15-matin.json
```

**Fixtures** : `tests/fixtures/` contient des réponses xAI/web_search enregistrées (replay offline). Permet de tester rendu, dédup, sélection sans appel API réel. Utilisé aussi pour CI (si CI ajoutée plus tard).

**Tests** (à minima pour V1) :
- Validation schema de `comptes.json`
- Dédup : 2 items même URL → 1 item avec `alt_sources` peuplé
- Rendu Jinja2 : HTML valide, contraste, taille < 60 KB
- Sélection : si N > max_items, top N par score retenu
- Contrat stdout : JSON parseable avec tous les champs attendus

## Non-goals (V1)

- ❌ Posting automatique sur X
- ❌ Résumé de vidéos YouTube (trop coûteux/lent pour un quotidien)
- ❌ Analyse de sentiment ou opinion générée — juste les faits
- ❌ Interface web / dashboard (sauf endpoint `/stats` JSON brut pour S6)
- ❌ Multi-utilisateur — c'est personnel
- ❌ Scraping HTML direct (on passe exclusivement par `web_search`)
- ❌ Alertes breaking news hors cycle (v2+, cf. Évolution)
- ❌ Traduction automatique (bilingue assumé, titres EN gardés)
- ❌ Remplacer la veille pro BST (skill `bst-veille-hebdo` reste séparé)

## Contraintes techniques

- **Runtime** : Python 3.11+ (machine Hermes)
- **Template** : Jinja2 (décision figée)
- **API xAI** : **Responses API** (recommandée par xAI, la Chat Completions est legacy/deprecated) — `POST https://api.x.ai/v1/responses`, auth `Authorization: Bearer <clé>` (clé dans OpenClaw secrets). L'ancienne Live Search API (`search_parameters` sur `/v1/chat/completions`) est **dépréciée depuis 2026-01-12** (410 Gone). On utilise les tools server-side `x_search` et `web_search` passés dans le champ `tools`.
- **Doc canonique de référence** : https://docs.x.ai/overview — toute évolution d'endpoint/modèle vérifiée là en premier.
- **Modèle** : alias **`grok-4-1-fast-latest`** par défaut (optimisé agentic tool calling, pricing bas ~0.20 $/M input, 0.50 $/M output). Escalade vers **`grok-4.20-latest`** (flagship, meilleur taux d'hallucination, strict prompt adherence) uniquement si la qualité de triage n'est pas satisfaisante après 2 semaines d'utilisation. Usage d'alias `-latest` = upgrades automatiques.
- **Mode d'appel** : stateless (on ne persiste pas côté xAI — chaque briefing est indépendant, pas besoin du mode stateful de la Responses API).
- **Paramètres `x_search`** : `allowed_x_handles` (max 10/requête — les 15 comptes sont splittés sur 2 appels), `from_date`/`to_date` (ISO 8601, fenêtre glissante), mutuellement exclusif avec `excluded_x_handles`.
- **`web_search`** : outil server-side xAI (pas besoin d'un `web_search` externe, on reste dans un seul provider pour simplifier auth + observabilité + facturation).
- **Tracking** : service FastAPI + SQLite sur même hôte, exposé via nginx Hermes
- **Budget financier** : ~0.35-0.40 $/jour (breakdown : ~0.32 $ tool fees à 5 $/1000 appels × ~16 queries/jour × 3-5 tool calls internes + ~0.05 $ tokens). Alerte si > 15 $/mois.
- **Budget latence** : < 3 min par briefing (timeout hard 180s)
- **Budget tokens** : ~100 K tokens/jour max (garde-fou dans le script, abort si dépassé)

## Succès mesurable

| Métrique | Cible V1 | Mesure |
|---|---|---|
| Briefings livrés à l'heure | ≥ 95 % (2 retards max/mois × 2 briefings) | Log Hermes |
| Clics par briefing (moyenne) | ≥ 1 | SQLite redirector |
| Items cliqués / total items | ≥ 5 % | SQLite redirector |
| Chris arrête de scroll X matin | Self-report hebdo | Conversation |
| Liste d'ajustements après 2 sem | < 5 items | Fichier `FEEDBACK.md` |
| Coût mensuel | < 12 $ | Log xAI |

## Évolution future (v2+)

- **Scoring personnalisé** basé sur historique de clics (le tracking V1 alimente ça)
- **Alertes breaking news** hors cycle (seuil : > 3 comptes surveillés postent sur le même sujet en < 30 min)
- **Intégration veille pro BST** (sourcing partagé, pas de fusion)
- **Récap hebdo dimanche soir** compilé à partir des 14 briefings de la semaine
- **Mode audio** : TTS du briefing matin pour écoute en voiture (9h sharp)
- **Fine-tuning de la sélection** via RLHF léger sur les réactions Telegram (👍/👎 si ajouté)
