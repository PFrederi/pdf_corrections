# Corrections PDF — Projets (Tkinter)

Fonctions :

-   Importation de PDF + ajout d'une marge (5 cm par défaut) à gauche sur chaque page
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

-   Correction V0 : résumé des points attribués en haut du panneau
-   Correction V0 : mode déplacer une pastille (cliquer-glisser)
-   Correction V0 : ajout d'une fonction d'alignement dans la marge
-   Récapitulatif des notes par la lectures des PDF corrigés
-   export du tableau des notes au format PRONOTE pour pouvoir importer directement dans PRONOTE

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
-   Alignement automatique dans la marge (à 0,5cm du bord) pour les pastilles
