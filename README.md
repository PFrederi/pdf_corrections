# Corrections PDF — Projets (Tkinter)

**Version : v0.7.5**

Fonctions :

<<<<<<< HEAD
-   Importation de PDF + ajout optionnel d'une marge **0 / 2,5 / 5 cm** (gauche / droite / 2 côtés)
=======
-   Importation de PDF + ajout d'une marge (5 cm par défaut) à gauche sur chaque page
>>>>>>> 4201597f12f2466f99b49d2bcf026dd86c87bc09
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

<<<<<<< HEAD
-   Import / Projet : choix de la marge à l'import (**0 / 2,5 / 5 cm** + position gauche/droite/2 côtés)
-   Correction V0 : alignement dans la marge **configurable** (distance en cm)
-   Note finale : cadre robuste (compact/overlay), fond blanc semi-transparent et **marqueur** `NOTE_FINALE_BOX`
    pour une lecture fiable dans **Synthèse Note**
-   Récapitulatif des notes par lecture des PDF corrigés + export du tableau des notes au format **PRONOTE**
=======
-   Correction V0 : résumé des points attribués en haut du panneau
-   Correction V0 : mode déplacer une pastille (cliquer-glisser)
-   Correction V0 : ajout d'une fonction d'alignement dans la marge
-   Récapitulatif des notes par la lectures des PDF corrigés
-   export du tableau des notes au format PRONOTE pour pouvoir importer directement dans PRONOTE
-   Ajout possible d'images dans le PDF au format PDF
-   Gestion complète des images par catégorie personnalisable
-   Possibilités d'exporter la librairie des images (totale ou par catégorie) pour réutilisation ultérieure
-   Modification des menus dans "Visualisation PDF" pour prendre plus d'ergonomie
-   Touche "F8" pour passer en mode plein écran de la copie
>>>>>>> 4201597f12f2466f99b49d2bcf026dd86c87bc09

## Outils d'annotation (Visualisation PDF)

-   **Main levée** : sélectionner l'outil, choisir couleur + épaisseur, puis cliquer-glisser sur le PDF.
-   **Texte** : sélectionner l'outil, régler police (couleur/taille), optionnellement saisir le texte dans le champ
    (sinon une fenêtre demande le texte). Cliquer-glisser pour définir une zone.
-   **Flèche** : sélectionner l'outil, choisir couleur + épaisseur, puis cliquer-glisser pour définir la flèche.
<<<<<<< HEAD
=======
-   **Image** : sélectionner l'outil, choisir l'image (PNG) à insérer.
>>>>>>> 4201597f12f2466f99b49d2bcf026dd86c87bc09

Remarque : les annotations sont appliquées via la variante **corrected** (régénérée automatiquement à la fin du
glisser).

### Déplacer (annotations)

-   Sélectionner **Déplacer**, cliquer sur une annotation (pastille, texte, flèche, main levée) puis **glisser** et
    relâcher.
<<<<<<< HEAD
-   Alignement automatique dans la marge (distance réglable) pour les pastilles
=======
-   Alignement automatique dans la marge (à 0,5cm du bord) pour les pastilles
>>>>>>> 4201597f12f2466f99b49d2bcf026dd86c87bc09
