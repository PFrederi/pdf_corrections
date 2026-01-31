# Corrections PDF — Projets (Tkinter)

**Version : v0.7.7**

Fonctions :

-   Importation de PDF + ajout optionnel d'une marge **0 / 2,5 / 5 cm** (gauche / droite / 2 côtés)
-   Gestion de projet : créer / ouvrir / enregistrer (project.json)
-   Export "corrigé verrouillé" (PDF chiffré + restrictions)
-   Visualisation PDF : défilement multi-pages, zoom, scrollbar
-   **GuideCorrection** : overlays d’annotations réutilisables (enregistrer/charger, superposition, appliquer sur d’autres PDF)
-   **Notation** : barème hiérarchique (n, n.X, option n.X.Y), export/import JSON, totaux par niveau
-   **Correction V0** : sélection d'un item (feuille) + résultat → clic dans le PDF pour poser une pastille + libellé
-   **Points manuels** : outil "Points Ex" pour saisir des points par exercice principal (cercle rouge) → intégré au total et à la note finale
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
-   **Points Ex** : sélectionner l’outil, cliquer dans le PDF puis choisir l’**exercice principal** et le nombre de points.
    Les points apparaissent en **rouge dans un cercle rouge** et sont intégrés à la note.
    ⚠️ Si un point manuel existe pour un exercice, il **remplace** le total des pastilles de cet exercice.
    Astuce : dans **Marques du document**, un **double-clic** sur une ligne **MANUEL** permet de modifier les points.

Remarque : les annotations sont appliquées via la variante **corrected** (régénérée automatiquement à la fin du
glisser).

### GuideCorrection (overlay de correction)

Dans **Visualisation PDF**, l’onglet **GuideCorrection** (à côté de **Infos**) permet de créer un *overlay* de correction
réutilisable sur d’autres PDF.

**Principe**

-   Un overlay enregistre toutes les **annotations / marques / images** et leur **position**, **page par page**.
-   Il est sauvegardé dans le dossier du projet sous le nom : `GuideCorrection_XXXXX.json` (XXXXX saisi par l’utilisateur).
-   Plusieurs overlays peuvent coexister ; ils apparaissent dans la liste déroulante de l’onglet.

**Actions**

-   **Activer la superposition** : affiche l’overlay par-dessus le PDF en cours (prévisualisation).
-   **Aperçu : opacité 50%** : rend la superposition plus légère pour mieux distinguer overlay vs annotations “réelles”.
-   **Enregistrer depuis ce document** : crée un nouvel overlay à partir des annotations du document courant.
-   **Mettre à jour l’overlay** : **écrase** l’overlay sélectionné avec les annotations du document courant.
-   **Appliquer au document** : copie les annotations de l’overlay dans le document courant (nouveaux identifiants),
    puis régénère le PDF corrigé.

⚠️ Recommandation : l’overlay est idéal si les documents ont la **même mise en page** (taille de page, marges d’import identiques).

### Déplacer (annotations)

-   Sélectionner **Déplacer**, cliquer sur une annotation (pastille, texte, flèche, main levée) puis **glisser** et
    relâcher.
-   Alignement automatique dans la marge (distance réglable) pour les pastilles
