# Agent KPI et couche sémantique (`kpi_semantic`)

## Mission

Cet agent calcule les indicateurs métier et les ventilations à partir du snapshot ETL. Il traduit
aussi les questions métier en SQL et exécute ces requêtes de façon contrôlée. Les KPI du catalogue
sont définis dans Git et calculés par SQL DuckDB ; le LLM n'est pas utilisé pour calculer leurs
valeurs.

La sortie conserve les SQL, résultats, identifiants de requête et hash du snapshot. L'auditeur peut
ainsi comparer un KPI à sa requête d'origine et rejouer cette requête.

## Fichiers et dépendances

| Fichier | Responsabilité |
|---|---|
| [kpi_semantic.py](../../src/biflow/agents/kpi_semantic.py) | `KPISemanticAgent`, modèles KPI/réponses, exécution et journalisation des requêtes, masquage, traitement des questions et fonction `ask()`. |
| [kpi_catalog.yaml](../../src/biflow/semantic/kpi_catalog.yaml) | Définitions et SQL des KPI et ventilations ; source de référence revue dans Git. |
| [catalog.py](../../src/biflow/semantic/catalog.py) | Lecture et validation Pydantic du catalogue, accès aux définitions. |
| [text_to_sql.py](../../src/biflow/semantic/text_to_sql.py) | Prompt, extraction du SQL, traduction LLM ou baseline et modalités autorisées. |
| [scope.py](../../src/biflow/semantic/scope.py) | Détection des questions hors périmètre. |
| [sandbox.py](../../src/biflow/semantic/sandbox.py) | Bac à sable DuckDB en mémoire : SELECT seulement, tables autorisées, durée et lignes limitées. |
| [privacy.py](../../src/biflow/llm/privacy.py) | Détection/masquage des données personnelles avant transmission au LLM. |
| [reference.py](../../src/biflow/common/reference.py) | Référentiels populations et objectifs SLA, pour les requêtes utilisant ces dimensions. |
| [config.py](../../src/biflow/common/config.py) | Réglages, notamment taille minimale de groupe et limites SQL. |
| [test_agents.py](../../tests/test_agents.py) et [test_extensions.py](../../tests/test_extensions.py) | Calculs attendus, questions, confidentialité, SQL non sûr, cross-check et snapshot altéré. |

## Entrées et sorties

Entrées : `state.outputs["quality_etl"]` (chemin et hash du snapshot), `state.questions`,
`state.dataset` pour les référentiels, et la configuration active du LLM.

`KPIOutput` est enregistré sous `state.outputs["kpi_semantic"]` :

- `kpis` : noms, valeurs numériques, unités, définitions et `query_id` ;
- `breakdowns` : ventilation par modalité, nombre d'éléments `n` et booléen `masked` ;
- `answers` : question, mode (`llm`, `baseline`, `refused`, `none`), SQL, lignes, erreur/refus,
  détection de données personnelles et résultat du cross-check ;
- `queries` : SQL, objectif, résultat, heure d'exécution et `snapshot_hash` ;
- `semantic_model` : tables, mesures et dimensions utilisées ;
- `min_group_size`, `llm_model`, `snapshot_hash` : contexte et provenance.

Les unités sont contraintes (`count`, `hours`, `percent`, `ratio`, `per_10k`). Les contrôles de plage
sont définis dans `KPI_RANGES`.

## Déroulement et fonctions clés

1. `run()` lit la sortie ETL et charge le catalogue YAML.
2. Monte un `SQLSandbox` contenant le snapshot `requests` et les tables de référence.
3. La fonction locale `run_query()` exécute chaque requête, refuse une requête de question qui ne
   retourne pas `n` quand ce champ est nécessaire au masquage, puis conserve un `QueryRecord`.
4. Chaque définition du catalogue est exécutée. Les valeurs globales constituent les KPI ; les
   requêtes de ventilation incluent la dimension, `value` et `n`.
5. `mask_rows()` remplace la valeur et le décompte par `null` si `n < min_group_size`.
6. Chaque question passe par `answer()`, puis le sandbox est fermé dans un `finally`.
7. `validate()` vérifie que des KPI existent, que leurs valeurs respectent leur unité, que les petits
   groupes sont masqués. Les questions sans réponse ne sont qu'un avertissement.

Fonctions importantes :

- `mask_rows(rows, min_n)` : protection des agrégats trop petits.
- `same_answer(a, b)` : compare deux résultats en tolérant des SQL/colonnes différents et une faible
  différence numérique.
- `answer(...)` : contrôle de confidentialité et de périmètre, génération SQL, exécution et repli.
- `cross_check(...)` : compare le résultat du SQL LLM avec celui de la baseline si celle-ci comprend
  la question.
- `ask(...)` : même parcours pour la page de questions après le run ; vérifie d'abord que le snapshot
  correspond au hash audité.
- `baseline_to_sql()` : traduction déterministe par mots-clés quand le LLM est absent ou échoue.

## Parcours d'une question

1. `redact(question)` détecte les données personnelles et produit une question expurgée.
2. `out_of_scope(question)` refuse les demandes portant sur une cause, des effectifs ou une prévision,
   plutôt que de demander au LLM d'y répondre.
3. Si un LLM est configuré, la question et le schéma (pas les lignes individuelles) lui sont transmis.
   Des modalités fréquentes au-dessus du seuil peuvent être incluses.
4. La requête générée doit passer le sandbox. Une requête doit aussi retourner `n` pour appliquer le
   seuil de confidentialité.
5. En cas de succès, la baseline vérifie le résultat lorsqu'elle sait traduire la question.
6. Après deux tentatives SQL LLM infructueuses, la baseline est tentée. Sans LLM, seule la baseline
   est utilisée. Une question inconnue par la baseline reçoit `mode="none"`.

Le sandbox n'accepte qu'une instruction SELECT sur les tables `requests`, `boroughs` et `sla_targets`.
Il interdit l'accès externe aux fichiers, limite le temps et le nombre de lignes. La limite étendue
`CATALOG_MAX_ROWS` sert aux requêtes rédigées dans le catalogue.

## Erreurs et résultats

| Situation | Comportement |
|---|---|
| Catalogue YAML illisible ou invalide, snapshot/référentiel impossible à charger | Exception de l'étape ; `BaseAgent` trace et retente, puis le run échoue si persistant. |
| Une requête cataloguée ne s'exécute pas ou est invalide | Exception dans `run()` ; étape retentée puis `failed` si elle persiste. |
| Aucun KPI calculable / tous les KPI absents | `kpis_not_empty` échoue lors de validation ; retries puis échec du run. |
| KPI hors de la plage de son unité | Contrôle bloquant ; retries puis échec si persistant. |
| SQL généré non-SELECT, table interdite, accès fichier, trop de lignes, timeout | Refus `UnsafeQueryError` / timeout. Pour une question, le LLM reçoit l'erreur pour une correction, puis la baseline est essayée. Pour un SQL du catalogue, l'étape échoue. |
| Question hors périmètre | `mode="refused"` avec explication ; pas de SQL et pas d'appel LLM. Ce n'est pas une panne de l'étape. |
| Question non comprise par le LLM et la baseline | `mode="none"` avec erreur descriptive ; contrôle `questions_answered` en warning, donc n'empêche pas à lui seul l'étape de passer. |
| SQL baseline en erreur | `Answer.error` est renseigné ; le warning est tracé et l'étape peut continuer. |
| Réponse LLM et baseline différentes | `cross_check="divergent"` ; cela signale une réponse à examiner. Ce n'est pas à lui seul un contrôle bloquant ici. |
| Question hors run sur snapshot modifié | `ask()` lève `ValueError` et refuse la question. |

## Tests et points à retenir

Les tests vérifient le calcul des KPI par rapport à une vérité synthétique, la présence de requêtes
liées aux valeurs, le masquage des petits groupes, le refus hors périmètre, le refus du SQL dangereux,
la rédaction de prompt sans lignes, le cross-check et le refus d'un snapshot modifié.
Voir [test_agents.py](../../tests/test_agents.py) et [test_extensions.py](../../tests/test_extensions.py).

**À retenir :** catalogue = définition revue, DuckDB = calcul, sandbox = exécution restreinte,
`queries` = preuve de provenance, `min_group_size` = protection des petits groupes. Les objectifs
SLA présents dans les données de référence peuvent être provisoires ; ne pas les présenter comme des
normes officielles sans validation métier.

