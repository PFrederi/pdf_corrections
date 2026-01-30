# Corrections PDF — Projets (Tkinter)

**Version : v0.7.5**

Fonctions :

-   Importation de PDF + ajout optionnel d'une marge **0 / 2,5 / 5 cm** (gauche / droite / 2 côtés)
-   Gestion de projet : créer / ouvrir / enregistrer (project.json)
-   Export "corrigé verrouillé" (PDF chiffré + restrictions)
-   Visualisation PDF : défilement multi-pages, zoom, scrollbar
-   **Notation** : barème hiérarchique (n, n.X, option n.X.Y), export/import JSON, totaux par niveau
-   **Correction V0** : sélection d'un item (feuille) + résultat → clic dans le PDF pour poser une pastille + libellé
-   **Infos** : points attribués / points max par exercice + total général (document courant)

## Installation

```bash
pip install -r requirements.txt
```

## Lancer

Depuis le dossier `pdf_corrections_app` :

```bash
python -m app.main
```

## Nouveautés

-   Import / Projet : choix de la marge à l'import (**0 / 2,5 / 5 cm** + position gauche/droite/2 côtés)
-   Correction V0 : alignement dans la marge **configurable** (distance en cm)
-   Note finale : cadre robuste (compact/overlay), fond blanc semi-transparent et **marqueur** `NOTE_FINALE_BOX`
    pour une lecture fiable dans **Synthèse Note**
-   Récapitulatif des notes par lecture des PDF corrigés + export du tableau des notes au format **PRONOTE**

## Outils d'annotation (Visualisation PDF)

-   **Main levée** : sélectionner l'outil, choisir couleur + épaisseur, puis cliquer-glisser sur le PDF.
-   **Texte** : sélectionner l'outil, régler police (couleur/taille), optionnellement saisir le texte dans le champ
    (sinon une fenêtre demande le texte). Cliquer-glisser pour définir une zone.
-   **Flèche** : sélectionner l'outil, choisir couleur + épaisseur, puis cliquer-glisser pour définir la flèche.

Remarque : les annotations sont appliquées via la variante **corrected** (régénérée automatiquement à la fin du
glisser).

### Déplacer (annotations)

-   Sélectionner **Déplacer**, cliquer sur une annotation (pastille, texte, flèche, main levée) puis **glisser** et
    relâcher.
-   Alignement automatique dans la marge (distance réglable) pour les pastilles
