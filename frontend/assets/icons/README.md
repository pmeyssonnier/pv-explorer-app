# Set d'icônes pv-explorer

Identité visuelle de l'app, calée sur la palette existante (bordeaux `#6d2233`,
or `#c8952b`, papier `#f7f3ec`, encre `#2a221f`). Concept : **registre de
procès-verbal + loupe** (interroger les PV).

## Fichiers

| Fichier | Rôle |
|---|---|
| `favicon.svg` | Favicon onglet navigateur (emblème 32) — branché dans `index.html` |
| `apple-touch-icon.svg` | Icône écran d'accueil iOS/Android (full-bleed 180) |
| `logo-mark.svg` | Emblème seul (64) — en-tête, réseaux, base des favicons |
| `logo.svg` | Lockup horizontal : emblème + mot-symbole + sous-titre |
| `icons.svg` | Sprite d'icônes d'interface (`<symbol>`, grille 24, `currentColor`) |

## Utiliser les icônes UI

```html
<svg class="icon" aria-hidden="true"><use href="assets/icons/icons.svg#ico-search"/></svg>
```
```css
.icon { width: 1.25em; height: 1.25em; color: var(--bordeaux); vertical-align: -0.15em; }
```
La couleur s'hérite via `currentColor` → change `color` pour recolorer.

**Icônes disponibles :** `ico-search`, `ico-stats`, `ico-commune`, `ico-date`,
`ico-pv`, `ico-decision`, `ico-vote`, `ico-montant`, `ico-intervenant`,
`ico-thematique`, `ico-urgence`, `ico-source`.

## Générer les PNG (favicon.ico, apple-touch-icon.png)

Optionnel — les navigateurs modernes lisent le SVG. Pour les PNG (iOS ancien),
depuis une machine avec `rsvg-convert` (paquet `librsvg`) ou Inkscape :

```bash
rsvg-convert -w 180 -h 180 apple-touch-icon.svg -o apple-touch-icon.png
rsvg-convert -w 512 -h 512 apple-touch-icon.svg -o icon-512.png
rsvg-convert -w 32  -h 32  favicon.svg          -o favicon-32.png
```
Puis pointer `<link rel="apple-touch-icon" href="assets/icons/apple-touch-icon.png">`.
