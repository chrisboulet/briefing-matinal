# PRD — Briefing Matinal Hermes

## Problème

Chris consomme son actualité de manière passive : scroll X soumis à l'algorithme, YouTube au hasard, Journal de Québec en diagonale. Résultat :

- **Biais algorithmique** — X sert ce qui engage, pas ce qui avance
- **Couverture inégale** — AI/Tech sur-alimenté, santé/politique/Tesla quasi absent
- **Pas de trace** — rien d'archivé, rien de structuré, impossible de revenir en arrière
- **Perte de temps** — 30-45 min de scroll dispersé au lieu de 5 min de lecture ciblée

## Solution

Un pipeline automatisé qui produit un briefing HTML quotidien, livré sur Telegram à 6h45 AM, couvrant les sujets qui comptent pour Chris — pas ceux que l'algorithme veut lui montrer.

## Utilisateur

**Christian Boulet** — consultant TI (BST), Québec QC
- Sharp 9h-13h — le briefing doit être prêt avant
- Intérêts : AI/Tech, Tesla, SpaceX, santé (longévité, jeûne, ménopause), politique (QC+Canada+US+international), business
- Consomme sur téléphone (Telegram) — format mobile-first
- Veut des liens cliquables, pas un mur de texte
- Apprécie les sources : Cleo Abram, Diary of a CEO, Tom Bilyeu, David Sinclair, Karpathy, Daniel Miessler, CernBasher

## Spécifications fonctionnelles

### S1 — Sourcing multi-source

Le pipeline doit interroger au moins 3 types de sources en parallèle :

| Source | Méthode | Données |
|---|---|---|
| Comptes X surveillés | xAI Grok `x_search` | Derniers posts des comptes de la liste |
| Recherche X thématique | xAI Grok `x_search` | Tesla, SpaceX, santé, politique, business |
| Web | `web_search` | JDQ, Les Affaires, nouvelles QC/Canada/US, santé |

**Comptes X surveillés (15) :**
```
# AI / Tech
@karpathy, @DanielMiessler, @AnthropicAI, @OpenAI

# Tesla / SpaceX
@cernbasher, @WholeMarsBlog, @SpaceX

# Santé
@davidasinclair, @PeterAttiaMD, @hubermanlab

# Science / Découverte
@CleoAbram

# Business / Développement
@DiaryOfACEO, @TomBilyeu

# Politique / Canada-QC
@JDeQuebec, @lesaffaires
```

La liste est stockée dans `sources/comptes.json` — modifiable sans toucher au code.

### S2 — Compilation HTML

Le briefing est un fichier HTML standalone (pas de dépendances externes, inline CSS).

**Sections :**

```
☀️ BRIEFING — [date]
─────────────────────
⚡ EN 60 SECONDES (3 faits marquants du jour)
─────────────────────
🤖 AI / TECH          — 3-5 items max
🚗 TESLA              — 2-3 items max
🚀 SPACEEX            — 1-2 items max
🏥 SANTÉ              — 2-3 items max
🏛️ POLITIQUE          — 2-3 items max
💼 BUSINESS           — 2-3 items max
─────────────────────
📌 À NE PAS MANQUER    — 1 recommandation (vidéo, thread, article)
```

**Chaque item :**
- Titre cliquable (lien vers source)
- 1-2 lignes de contexte
- Emoji de catégorie
- Source indiquée (ex: "via @cernbasher" ou "via JDQ")

**Contraintes design :**
- Mobile-first (lisible sur Telegram iPhone/Android)
- Dark-friendly mais lisible en light aussi
- Inline CSS uniquement (pas de CDN, pas de JS)
- Taille max : 50KB (limite Telegram)
- Polices système (pas de Google Fonts)

### S3 — Livraison Telegram

Le briefing est livré comme document HTML sur Telegram à 6h45 AM (America/Toronto).

**Méthode :** Le cron Hermes exécute le script, le script génère le HTML, Hermes envoie le fichier via Telegram.

**Fallback :** Si Telegram échoue, déposer sur `/mnt/nas/commun/briefing-matinal/YYYY-MM-DD.html`.

### S4 — Archivage

Chaque briefing est archivé :
- Local : `output/YYYY-MM-DD.html`
- NAS : `/mnt/nas/commun/briefing-matinal/YYYY-MM-DD.html`

Le répertoire `output/` est gitignored.

### S5 — Paramètres configurables

`sources/comptes.json` contient :
```json
{
  "comptes_x": ["@karpathy", "@DanielMiessler", ...],
  "recherches_thematiques": [
    {"theme": "Tesla", "query": "Tesla FSD robotaxi Optimus Cybertruck"},
    {"theme": "SpaceX", "query": "SpaceX Starship Starlink launch"},
    {"theme": "Santé", "query": "longevity fasting menopause NMN aging"},
    {"theme": "Politique Canada", "query": "Canada Quebec politique"},
    {"theme": "Business", "query": "marchés bourse économie Québec Canada"}
  ],
  "sources_web": [
    "journaldequebec.com",
    "lesaffaires.com",
    "lapresse.ca"
  ],
  "sections": [
    {"id": "ai-tech", "label": "AI / Tech", "emoji": "🤖", "max_items": 5},
    {"id": "tesla", "label": "Tesla", "emoji": "🚗", "max_items": 3},
    {"id": "spacex", "label": "SpaceX", "emoji": "🚀", "max_items": 2},
    {"id": "sante", "label": "Santé", "emoji": "🏥", "max_items": 3},
    {"id": "politique", "label": "Politique", "emoji": "🏛️", "max_items": 3},
    {"id": "business", "label": "Business", "emoji": "💼", "max_items": 3}
  ]
}
```

## Non-goals (ne pas faire)

- ❌ Posting automatique sur X
- ❌ Résumé de vidéos YouTube (trop coûteux/lent pour un quotidien)
- ❌ Analyse de sentiment ou opinion générée — juste les faits
- ❌ Interface web / dashboard
- ❌ Multi-utilisateur — c'est personnel
- ❌ Remplacer la veille pro BST (skill bst-veille-hebdo reste séparé)

## Contraintes techniques

- **Runtime** : Python 3.11+ (sur machine Hermes)
- **API X** : xAI Grok `x_search` via `/v1/responses` (existant, clé dans OpenClaw secrets)
- **API Web** : `web_search` Hermes
- **Template** : Jinja2 ou string formatting Python
- **Coût** : ~$0.10-0.15/jour en appels xAI (3-5 appels Grok)
- **Latence** : < 3 minutes total pour générer le briefing

## Succès mesurable

- Chris lit le briefing 5 matins sur 7
- Chris arrête de scroll X le matin (remplacé par le briefing)
- Au moins 1 clic sur un lien par jour
- Après 2 semaines, Chris a une liste d'ajustements < 5 items

## Évolution future (v2+)

- Score de pertinence par item (apprentissage des clics)
- Intégration veille pro BST (sourcing partagé)
- Alertes instantanées sur sujets critiques (breaking news)
- Résumé hebdo compilé à partir des briefings quotidiens
