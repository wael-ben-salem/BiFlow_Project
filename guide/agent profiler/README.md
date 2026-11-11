# Agent profiler (`data_profiler`)

## 1. Mission

Le profiler est la première étape du pipeline BIFlow. Il lit le jeu de données brut, produit un
résumé statistique et détecte des problèmes connus. Il **ne corrige pas et ne supprime pas** les
lignes : ses défauts décrits dans `issues` permettent à l'équipe d'observer la qualité de l'entrée
et à l'ETL de connaître le contexte du run.

Il s'agit d'un agent Python déterministe sans appel LLM dans cette version du projet.

## 2. Fichiers à connaître

| Fichier | Lien avec l'agent |
|---|---|
| [data_profiler.py](../../src/biflow/agents/data_profiler.py) | Implémentation de `DataProfilerAgent`, modèles de sortie, conversion de types et détection des défauts. |
| [base.py](../../src/biflow/agents/base.py) | Contrat commun, validation, retries, trace et stockage de la sortie. |
| [data.py](../../src/biflow/common/data.py) | Lecture du CSV/Parquet ou de PostgreSQL, et chargement des métadonnées de provenance. |
| [schema.py](../../src/biflow/common/schema.py) | Colonnes brutes obligatoires/facultatives, types et valeurs officielles des boroughs. |
| [validation.py](../../src/biflow/orchestrator/validation.py) | Contrôles de colonnes obligatoires et de plage des valeurs. |
| [state.py](../../src/biflow/orchestrator/state.py) | `DatasetRef`, état du pipeline et ordre des étapes. |
| [graph.py](../../src/biflow/orchestrator/graph.py) | Insère le profiler en première étape et route vers `quality_etl` après succès. |
| [test_agents.py](../../tests/test_agents.py) | Vérifie que le profiler trouve les défauts artificiellement injectés dans le jeu de test. |

## 3. Entrée et sortie

### Entrée

`DataProfilerAgent.run(state)` lit `state.dataset`, un `DatasetRef` :

- si `file_path` est fourni, [load_raw_frame](../../src/biflow/common/data.py) lit le fichier
  Parquet ou CSV ;
- sinon, la fonction lit la table référencée (par défaut `raw.nyc311`) en PostgreSQL, avec les bornes
  de date éventuellement configurées.

Le schéma brut est défini dans [schema.py](../../src/biflow/common/schema.py). Les champs
`descriptor`, `incident_zip`, `resolution_description`, `latitude` et `longitude` sont facultatifs
pour un extrait. Le chargeur les ajoute vides et place leurs noms dans
`df.attrs["absent_columns"]`; les colonnes obligatoires manquantes provoquent un échec.

### Sortie

`ProfilerOutput`, enregistré sous `state.outputs["data_profiler"]`, contient :

| Champ | Signification |
|---|---|
| `row_count` | Nombre de lignes chargées. |
| `columns` | Colonnes effectivement présentes dans le DataFrame lu (inclut les colonnes facultatives ajoutées). |
| `null_rates` | Proportion de valeurs nulles par colonne brute, entre 0 et 1. |
| `date_min`, `date_max` | Première et dernière `created_date` interprétables, au format ISO ; `null` si aucune date valide. |
| `complaint_type_counts`, `borough_counts`, `status_counts` | Décomptes par valeur, y compris les valeurs manquantes. |
| `issues` | Défauts structurés par colonne, type, nombre, description et jusqu'à cinq `unique_key` d'exemple. |
| `anomalies` | Présentation textuelle des mêmes défauts pour les interfaces/rapports. |
| `absent_columns` | Colonnes facultatives absentes de l'extrait d'origine. |
| `source` | Provenance, si un fichier `.meta.json` existe à côté du fichier d'entrée. |
| `input_rows`, `output_rows` | Champs communs de suivi ; pour le profiler, ils valent tous deux `row_count`. |
| `notes` | Notes synthétiques, dont le nombre de types de défauts détectés. |

## 4. Déroulement du code

1. **Chargement** : `run()` appelle `load_raw_frame(state.dataset)`.
2. **Contrat de colonnes** : compare les colonnes aux `REQUIRED_COLUMNS`. Si une obligatoire manque,
   une `ValueError` liste les noms absents.
3. **Conversion en mémoire** : `coerce_types()` convertit les dates (`DATETIME_COLUMNS`) et
   coordonnées (`FLOAT_COLUMNS`). Les valeurs présentes mais non convertibles sont marquées dans
   des masques `bad_types`; elles deviennent `NaT` ou `NaN` pour le calcul. `unique_key` devient un
   entier nullable pandas.
4. **Détection** : `find_issues(raw)` applique les règles décrites ci-dessous sur une copie convertie.
5. **Résumé** : calcule les taux de valeurs nulles, les distributions de trois champs, la période des
   dates valides et récupère la provenance facultative.
6. **Validation** : `validate()` vérifie la présence des colonnes obligatoires, `row_count >= 1` et
   que les taux de valeurs nulles restent dans `[0, 1]`.
7. **Transmission** : `BaseAgent.execute()` écrit la sortie JSON dans le state ; le graphe passe ensuite
   à `quality_etl`.

### Fonctions et constantes importantes

- `coerce_types(df)` : conversion de types et masques de valeurs non convertibles.
- `_keys(df, mask, n=5)` : collecte les clés d'exemple non nulles des lignes signalées.
- `find_issues(raw)` : règles de détection ; retourne une liste de `DataIssue`.
- `DataProfilerAgent.run(state)` : coordination du profil et fabrication de `ProfilerOutput`.
- `DataProfilerAgent.validate(output, state)` : validations structurelles de l'agent.
- `NYC_LAT`, `NYC_LON` : emprise géographique approximative utilisée par la règle de coordonnées.
- `REQUIRED_VALUES`, `ZIP_PATTERN` : champs métier requis au niveau des valeurs et format de code postal.

## 5. Défauts repérés

`issue_type` est limité aux valeurs suivantes : `duplicate`, `missing`, `invalid_type`,
`invalid_format`, `invalid_value`, `out_of_range`, `inconsistent`.

| Colonne | Type de défaut | Règle de détection |
|---|---|---|
| `unique_key` | `duplicate` | Clé déjà rencontrée, en conservant la première occurrence pour le comptage. |
| Dates et coordonnées | `invalid_type` | Valeur source présente mais impossible à convertir en date ou nombre. |
| `created_date`, `complaint_type`, `borough` | `missing` | Valeur vide/null après conversion, hors valeurs qui sont déjà comptées comme mauvais type. |
| `closed_date` | `inconsistent` | Date de fermeture antérieure à la date de création lorsque les deux sont présentes. |
| `closed_date` | `missing` | Statut `Closed` sans date de fermeture. |
| `incident_zip` | `invalid_format` | Valeur non vide qui ne correspond pas à cinq chiffres. |
| `borough` | `invalid_value` | Valeur non nulle hors de la liste des cinq boroughs officiels. |
| `latitude`, `longitude` | `out_of_range` | Coordonnée hors de l'emprise de NYC définie dans le module. |

Une règle n'ajoute pas d'`issue` quand son nombre de lignes est nul. `count` mesure les occurrences
selon la règle ; différentes règles peuvent donc décrire des défauts différents dans une même ligne.
Les `sample_keys` sont des exemples et non la liste complète des cas.

## 6. Erreurs et comportement attendu

| Situation | Où elle apparaît | Conséquence |
|---|---|---|
| Fichier illisible, CSV/Parquet invalide, table inaccessible ou erreur SQL | `load_raw_frame()` | L'exception remonte à `BaseAgent.execute()`, est enregistrée, puis l'étape est retentée. |
| Colonnes obligatoires absentes | `DataProfilerAgent.run()` (`ValueError`) | Chaque tentative échoue ; après épuisement des retries, le run est `failed` et l'ETL ne démarre pas. |
| Valeur présente mais date/nombre invalide | `coerce_types()` / `find_issues()` | En général, ce n'est pas une exception : la valeur est convertie en null et décrite dans `issues` comme `invalid_type`. |
| Aucun enregistrement | `validate()` (`row_count >= 1`) | Contrôle bloquant ; nouvelle tentative, puis `failed` si le résultat reste vide. |
| Taux nul hors `[0, 1]` | `validate()` | Contrôle bloquant, normalement impossible si les taux ont été calculés correctement. |
| Erreur inattendue de colonne / conversion | Exécution Python | Exception capturée et tracée par le mécanisme commun ; après retries, le pipeline s'arrête. |

Par défaut, deux nouvelles tentatives sont possibles après le premier échec. La trace contient le type
d'exception, un message et un traceback limité ; les erreurs sont également conservées dans
`state.errors`. Voir [base.py](../../src/biflow/agents/base.py) et
[orchestrator/state.py](../../src/biflow/orchestrator/state.py).

## 7. Ce que le profiler ne fait pas

- Il ne supprime ni ne répare les lignes.
- Il ne calcule pas les KPI finaux et n'écrit pas l'instantané nettoyé.
- Il ne déclare pas à lui seul un run publiable.
- Il ne transmet pas les valeurs détaillées de chaque `issue` à l'ETL comme instruction de nettoyage :
  l'ETL réapplique ses règles de façon déterministe à partir des données brutes. Il lit le résumé du
  profiler pour contrôler le nombre de lignes et produire une note si les nombres diffèrent.

## 8. Tests associés et vérification manuelle

Le test central est `test_profiler_finds_every_injected_defect` dans
[test_agents.py](../../tests/test_agents.py). Il compare le couple (colonne, type de défaut) et son
nombre avec les défauts injectés dans le jeu synthétique. Le test du pipeline complet est dans
[test_orchestrator.py](../../tests/test_orchestrator.py).

À expliquer pendant une validation : le profiler sert à **rendre la qualité visible et traçable**,
tandis que la décision de conserver, modifier ou supprimer les données appartient au maillon ETL.

