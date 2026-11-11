# Agent Qualité / ETL (`quality_etl`)

## 1. Mission

L'agent `quality_etl` est la deuxième étape du pipeline. Il relit les données brutes, applique des
règles de nettoyage explicites, minimise certaines données, ajoute des colonnes d'analyse et écrit
un instantané Parquet. Chaque ligne supprimée est conservée dans un fichier de quarantaine avec un
motif, afin qu'une suppression soit explicable.

Les règles sont du code déterministe, auditable dans Git, sans LLM dans cette version. Les KPI et
l'auditeur s'appuient ensuite sur le même instantané versionné.

## 2. Fichiers à connaître

| Fichier | Lien avec l'agent |
|---|---|
| [quality_etl.py](../../src/biflow/agents/quality_etl.py) | `QualityETLAgent`, `clean_frame`, règles, sortie et contrôles métier. |
| [data_profiler.py](../../src/biflow/agents/data_profiler.py) | Conversion de types et limites NYC réutilisées par le nettoyage. |
| [base.py](../../src/biflow/agents/base.py) | Exécution standard, retries, validation et traces. |
| [data.py](../../src/biflow/common/data.py) | Chargement des données brutes référencées par le run. |
| [reference.py](../../src/biflow/common/reference.py) | Valeurs de référence (notamment objectifs SLA) selon le mode fichier ou PostgreSQL. |
| [schema.py](../../src/biflow/common/schema.py) | Schéma brut partagé et liste des boroughs officiels. |
| [snapshot.py](../../src/biflow/common/snapshot.py) | Écriture/relecture de l'instantané et calcul de son empreinte de contenu. |
| [graph.py](../../src/biflow/orchestrator/graph.py) | Exécute ETL après le profiler, puis dirige vers `kpi_semantic`. |
| [test_agents.py](../../tests/test_agents.py) | Vérifie nombres de lignes, clés uniques, colonnes privées retirées et données normalisées. |
| [test_extensions.py](../../tests/test_extensions.py) | Vérifie la quarantaine, les motifs et l'absence du texte libre dans celle-ci. |
| [test_orchestrator.py](../../tests/test_orchestrator.py) | Vérifie retries et comportement de l'ETL dans le pipeline complet. |

## 3. Entrées, sorties et utilisation du profil

### Entrées

- `state.dataset` : référence vers un CSV/Parquet ou une table PostgreSQL ; le chargement est fait par
  `load_raw_frame()`.
- `state.outputs["data_profiler"]` : sortie précédente. L'ETL la lit pour comparer `row_count` au
  nombre de lignes qu'il vient lui-même de charger. Le détail des `issues` du profiler ne pilote pas
  directement les règles de nettoyage : les règles sont réappliquées sur les données brutes.
- Référentiel `sla_targets` : chargé par `load_reference(state.dataset)`. En mode fichier, le repli
  du projet est défini dans [reference.py](../../src/biflow/common/reference.py). Ses cibles SLA sont
  marquées comme provisoires (`is_placeholder=True`) et doivent être validées avant interprétation
  métier comme objectifs officiels. En mode PostgreSQL, les référentiels sont lus dans `ref.*`.

### Sortie

`QualityOutput` est enregistré dans `state.outputs["quality_etl"]`.

| Champ | Signification |
|---|---|
| `rows_in`, `rows_out`, `rows_dropped` | Volumes d'entrée, conservé et supprimé ; normalement `rows_in = rows_out + rows_dropped`. |
| `invalid_dates` | Nombre de lignes où `closed_date < created_date` et qui sont supprimées. Ce champ ne compte pas toutes les conversions de dates invalides. |
| `duplicates` | Nombre de lignes supprimées par la règle de doublons de `unique_key`. |
| `snapshot_path` | Chemin vers `nyc311_clean.parquet`. |
| `snapshot_hash` | Empreinte SHA-256 du contenu Parquet relu, calculée sur les colonnes et valeurs du DataFrame. |
| `quarantine_path`, `quarantine_rows` | Chemin et nombre de lignes écartées ; le chemin est `null` si aucune ligne n'est mise en quarantaine. |
| `columns` | Colonnes présentes dans l'instantané nettoyé. |
| `rules_applied` | Libellé de chaque règle / transformation et son nombre de lignes associé. |
| `rule_counts` | Même synthèse sous forme de dictionnaire règle -> nombre. |
| `output_table` | Nom logique `processed.nyc311_clean`. |
| `input_rows`, `output_rows`, `notes` | Champs de suivi hérités de `AgentOutput`. |

## 4. Déroulement de `run()`

1. Vérifie que la sortie du profiler est présente dans le state.
2. Recharge les données brutes et les référentiels.
3. Appelle `clean_frame(raw, sla_targets, quarantine)` pour produire le DataFrame nettoyé et les
   comptes de règles.
4. Écrit `nyc311_clean.parquet` et calcule une empreinte sur sa relecture avec `write_snapshot()`.
5. Si nécessaire, écrit `quarantine.parquet` dans le dossier du run.
6. Si le jeu provient de PostgreSQL (pas de `file_path`), écrit également la table
   `processed.nyc311_clean`.
7. Construit `QualityOutput`, notamment le nombre supprimé, les colonnes et les règles appliquées.
8. `validate()` contrôle les invariants de sortie ; si l'étape réussit, le graphe passe à `kpi_semantic`.

## 5. Règles de transformation dans `clean_frame()`

### Conversion des types

`coerce_types()` est réutilisée du profiler : les colonnes date et coordonnées sont converties.
Les valeurs non convertibles deviennent des valeurs nulles pour les calculs ultérieurs. Le nombre de
valeurs converties en null est résumé par la règle `types convertis (valeurs invalides -> NULL)`.
Cette conversion seule n'entraîne pas systématiquement la suppression de la ligne.

### Suppressions et quarantaine

Les lignes concernées sont retirées du DataFrame propre et ajoutées à la quarantaine avec le motif
correspondant :

1. `unique_key` absente ;
2. `created_date` absente ou invalide ;
3. doublon de `unique_key` : **la dernière version est gardée** ;
4. `closed_date < created_date` ;
5. `complaint_type` absent.

Les opérations sont séquentielles ; une ligne déjà supprimée par une règle n'est pas recomptée par
les règles de suppression suivantes. La quarantaine exclut `resolution_description`, puis ajoute la
colonne `motif`. Le test d'extension confirme qu'il y a une ligne mise en quarantaine par ligne
supprimée et que le texte libre n'y figure pas.

### Réparations / normalisations

- `complaint_type` : conversion texte, espaces de début/fin retirés, séquences d'espaces ramenées à
  un seul espace.
- `borough` : espaces retirés et casse normalisée en majuscules ; toute valeur hors des cinq boroughs
  officiels devient `UNSPECIFIED` (y compris les valeurs nulles).
- `incident_zip` : espaces retirés ; un code non nul qui ne contient pas exactement cinq chiffres
  devient `NULL`.
- Coordonnées : une paire en dehors de l'emprise NYC est mise à `NULL`. Si une seule des deux
  coordonnées est présente mais que la paire n'est pas complète, le code met également les deux à
  `NULL`. Les coordonnées conservées sont arrondies à deux décimales (environ 1 km).
- `resolution_description` : retirée du DataFrame propre ; la constante `DROPPED_COLUMNS` indique le
  champ de texte libre à exclure.

### Enrichissements

| Colonne ajoutée | Calcul |
|---|---|
| `is_closed` | Vrai si `closed_date` est présente. |
| `resolution_hours` | Durée entre fermeture et création, exprimée en heures. |
| `created_day` | Jour de création normalisé à minuit, sous forme date. |
| `created_hour` | Heure de la journée (0 à 23). |
| `weekday` | Jour de la semaine (index pandas : lundi = 0). |
| `target_hours` | Objectif trouvé en faisant correspondre `complaint_type` au référentiel SLA. |
| `within_sla` | Résultat durée <= objectif ; nullable quand la demande n'est pas fermée ou sans objectif. |

Enfin les colonnes de texte prévues sont converties au type string, les lignes sont triées par date
et clé, puis `unique_key` est convertie en entier `int64`.

## 6. Validations et erreurs

Les contrôles de `QualityETLAgent.validate()` sont des contrôles bloquants par défaut (`severity="error"`)
sauf `rows_match_profile`, qui est un avertissement non bloquant.

| Contrôle / erreur | Signification et résultat |
|---|---|
| `rows_out_le_rows_in` | Le nombre de lignes conservées ne doit pas dépasser l'entrée. Sinon l'étape échoue à la validation. |
| `rows_balance` | Le nombre conservé + supprimé doit égaler l'entrée. Sinon l'étape échoue. |
| `counts_non_negative` | Les compteurs principaux doivent être positifs ou nuls. |
| `rows_out_not_empty` | Un résultat vide est refusé pour éviter de produire une couche propre inutilisable. |
| `no_free_text` | `resolution_description` ne doit pas figurer dans l'instantané. |
| `dropped_rows_quarantined` | `quarantine_rows` doit correspondre à `rows_dropped`. |
| `rows_match_profile` | Compare les lignes relues au compte du profiler ; échec en `warning`, donc avertissement tracé mais pas blocage. |
| `KeyError` du profil | Peut indiquer que le profiler n'a pas exécuté ou n'a pas produit `data_profiler`. L'ETL échoue, puis le mécanisme commun réessaie l'étape ; si la sortie reste absente, le run échoue. |
| Erreur lecture des données/référentiels | Fichier invalide, connexion ou requête de base en échec : exception propagée, retries puis `failed` si persistante. |
| Erreur d'écriture Parquet/quarantaine | Chemin inaccessible, dépendance/format ou disque en erreur : exception propagée, retries puis `failed` si persistante. |
| Erreur d'écriture PostgreSQL | En mode base, échec de connexion, droits ou insertion : l'étape échoue et le pipeline n'atteint pas les KPI. |
| Erreur de validation Pydantic ou `StepValidationError` | Sortie incompatible avec `QualityOutput` ou invariant métier échoué : tracée comme tentative échouée. |

La politique par défaut de [BaseAgent](../../src/biflow/agents/base.py) est un essai initial suivi
de deux nouvelles tentatives. Chaque tentative échouée est tracée avec type/message d'erreur ; après
l'échec final, l'orchestrateur fixe le run à `failed`, ne lance pas les étapes aval et ne publie pas
ce run.

## 7. Artefacts et provenance

Pour un run `data/processed/<run_id>/` :

- `nyc311_clean.parquet` : données transformées utilisées par les agents suivants ;
- `quarantine.parquet` : lignes supprimées et motifs si nécessaire ;
- `run.json` : état complet, sorties, validations, erreurs et trace métier.

Le hash du snapshot permet de relier les calculs au contenu réellement écrit et relu. Les étapes
suivantes peuvent ainsi citer le même snapshot et l'auditeur peut rejouer les requêtes. L'empreinte
n'est pas un hash du chemin du fichier : `frame_hash()` couvre les noms de colonnes et les valeurs.

En mode fichier, l'ETL écrit l'instantané et la quarantaine dans le dossier du run. En mode base,
il écrit aussi `processed.nyc311_clean` et lit les référentiels `ref.*`. La table est créée/remplacée
par le code avec `if_exists="replace"` ; il faut donc tenir compte de ce comportement lors d'une
exécution sur une base partagée.

## 8. Tests associés

- `test_quality_cleans_and_minimises` dans [test_agents.py](../../tests/test_agents.py) vérifie les
  volumes attendus sur données synthétiques, les doublons, les dates incohérentes, la suppression du
  texte libre, l'unicité des clés et les limites de normalisation.
- `test_dropped_rows_are_quarantined_with_reason` dans [test_extensions.py](../../tests/test_extensions.py)
  vérifie l'égalité entre suppressions et lignes quarantainées et l'absence du texte libre.
- `test_transient_failure_is_retried` et `test_persistent_failure_marks_run_failed_and_stops` dans
  [test_orchestrator.py](../../tests/test_orchestrator.py) couvrent les nouvelles tentatives et l'arrêt.

## 9. Réponses courtes pour une présentation

- **Pourquoi une quarantaine ?** Pour garder une preuve de chaque ligne supprimée et expliquer sa
  règle d'exclusion, sans conserver le champ de texte libre.
- **Pourquoi un snapshot ?** Pour que KPI, dashboard et audit se réfèrent au même contenu reproductible.
- **Le profiler demande-t-il directement à l'ETL de supprimer les lignes ?** Non : le profil sert de
  mesure/contexte ; le nettoyage suit les règles déterministes de `clean_frame()`.
- **Peut-on publier si une règle de validation échoue ?** Non pour un contrôle bloquant ; l'agent est
  retenté puis le run devient `failed` si l'anomalie persiste.
- **Les SLA sont-ils nécessairement des objectifs officiels ?** Non en mode fichier : les exemples
  intégrés sont explicitement provisoires et leur validation métier est requise.

