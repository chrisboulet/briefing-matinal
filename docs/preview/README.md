# Preview HTML

Snapshots de briefings rendus, **uniquement pour visualisation**. Pas de pipeline de prod ne lit ce dossier.

## Voir le rendu dans un navigateur

GitHub sert les `.html` en `text/plain` (sécurité). Pour voir le rendu effectif :

- **htmlpreview.github.io** :
  https://htmlpreview.github.io/?https://github.com/chrisboulet/briefing-matinal/blob/main/docs/preview/sample-matin.html

- **Localement** : cloner et `open docs/preview/sample-matin.html`

## Régénérer

```bash
python -m scripts.build_briefing --moment matin --fixture tests/fixtures/sample_matin.json
cp output/2026-04-19-matin.html docs/preview/sample-matin.html
```
