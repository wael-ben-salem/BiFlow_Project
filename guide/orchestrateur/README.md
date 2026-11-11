# Orchestrateur BIFlow

## 1. Rôle

L'orchestrateur coordonne le pipeline complet : il construit le graphe LangGraph, passe l'état aux
agents dans le bon ordre, route les étapes après succès ou erreur, pilote les retries et le retour
de l'auditeur, puis finalise le run et décide s'il peut être publié.

Il ne réalise pas lui-même le profilage, le nettoyage ou les calculs KPI. Ces responsabilités sont
déléguées aux agents ; l'orchestrateur prend en charge le déroulement et la cohérence du run.

## 2. Fichiers principaux

| Fichier | Responsabilité |
|---|---|
| [graph.py](../../src/biflow/orchestrator/graph.py) | Construction du graphe, routage des nœuds, boucle de l'auditeur, finalisation et point d'entrée `run_pipeline()`. |
| [state.py](../../src/biflow/orchestrator/state.py) | Modèles de données transmis entre les étapes : `PipelineState`, erreurs, validations, historique et findings. |
| [base.py](../../src/biflow/agents/base.py) | Exécution commune de chaque agent : conversion de sortie, validations, retries et trace par tentative. |
| [tracing.py](../../src/biflow/orchestrator/tracing.py) | Protocole et implémentations de trace mémoire/PostgreSQL. |
| [validation.py](../../src/biflow/orchestrator/validation.py) | Contrôles réutilisables du schéma, des valeurs et du jeu brut. |
| [report.py](../../src/biflow/orchestrator/report.py) | Résumé lisible du run pour la CLI : résultats, durée, erreurs, findings et statut final. |
| [run.py](../../src/biflow/run.py) | Point d'entrée CLI : options, sélection de données et de trace, simulations, code de sortie. |
| [agents/__init__.py](../../src/biflow/agents/__init__.py) | Construction du registre standard des six agents. |
| [test_orchestrator.py](../../tests/test_orchestrator.py) | Tests du pipeline complet, des traces, des retries, des échecs et de la boucle d'audit. |
| [test_api.py](../../tests/test_api.py) | Vérifie les scénarios du pipeline exposés par l'API. |

## 3. Graphe d'exécution

`STEP_ORDER` est défini dans `state.py`. `build_graph()` ajoute un nœud par agent, puis un nœud
`finalize`, dans cet ordre :

```text
START
  -> data_profiler
  -> quality_etl
  -> kpi_semantic
  -> analyste_bi
  -> dashboard_generator
  -> auditor_xai
  -> finalize
  -> END
```

Le graphe est séquentiel. `_make_agent_node()` enveloppe chaque agent et place dans
`state.next_step` la destination suivante :

- étape ordinaire réussie : agent suivant dans `STEP_ORDER` ;
- étape échouée après toutes ses tentatives : `finalize` ;
- auditeur : `_audit_routing()` choisit entre `finalize`, une reprise d'étape, ou un run
  `unreliable`.

`build_graph()` refuse de construire le graphe si un agent attendu manque, avec une `ValueError`
qui liste les identifiants manquants. Les routes conditionnelles sont déterminées par
`state.next_step`.

## 4. État partagé : le contrat entre nœuds

`PipelineState` est le conteneur Pydantic transmis par LangGraph. Un agent ne doit pas modifier
l'état lui-même : il renvoie sa sortie et `BaseAgent.execute()` forme la mise à jour du state.

| Champ | Rôle |
|---|---|
| `run_id` | Identifiant unique du run. |
| `dataset` | Référence vers une table, une fenêtre de dates ou un fichier via `DatasetRef`. Le DataFrame n'est pas stocké dans l'état. |
| `workspace` | Dossier des artefacts du run. |
| `questions` | Questions métier données au pipeline. |
| `outputs` | Sortie JSON par nom d'étape, par exemple `outputs["quality_etl"]`. |
| `validations` | Résultats de validation par agent. |
| `errors` | Erreurs de chaque tentative échouée. |
| `history` | Entrée de réussite/échec par exécution d'étape, avec durée, essais et tour de feedback. |
| `corrections` | Messages de l'auditeur à transmettre aux agents relancés. |
| `feedback_rounds` | Nombre de boucles de correction d'audit déjà exécutées. |
| `findings` | Historique complet des écarts remontés par l'auditeur. |
| `status` | `running`, `success`, `failed` ou `unreliable`. |
| `next_step` | Prochaine destination lue par le routeur LangGraph. |
| `published` | Booléen final qui indique si le run a été publié. |

`state.output(step)` retourne la sortie précédente ou lève un `KeyError` explicite si elle manque.
`state.require_workspace()` lève une `ValueError` si aucun workspace n'est défini.

## 5. Cycle d'exécution d'une étape et reprises

La logique commune réside dans `BaseAgent.execute(state, ctx)` :

1. Appelle `agent.run(state)`.
2. Vérifie/convertit le résultat selon le modèle Pydantic `output_model`.
3. Appelle `agent.validate(output, state)`.
4. Si les contrôles bloquants passent, écrit `output.model_dump(mode="json")` dans
   `state.outputs[agent.name]`, enregistre la validation et ajoute une étape réussie à `history`.
5. Si `run()` lève une exception ou si la validation échoue, ajoute un `StepError`, écrit une trace
   d'échec/retry et tente de nouveau.
6. Après l'échec final, ajoute une étape `FAILED` à l'historique et fixe le statut du run à `FAILED`.
   Le graphe va à `finalize`; aucun agent aval n'est lancé.

`RunContext` configure :

- `max_retries=2` : nombre de nouveaux essais après le premier, donc trois tentatives au total par
  défaut ;
- `retry_delay_seconds=1.0` : attente de base entre tentatives ; le délai est multiplié par le numéro
  de la tentative échouée ;
- `max_feedback_rounds=1` : nombre maximal de renvois correctifs par l'auditeur ;
- `sleep` : fonction d'attente remplaçable dans les tests.

Les contrôles de sévérité `error` bloquent l'étape. Un `warning` qui échoue est enregistré, mais ne
fait pas échouer `ValidationResult.passed`.

## 6. Boucle de correction de l'auditeur

Après l'étape `auditor_xai`, `_audit_routing()` traite ses findings :

1. **Aucun finding `error`** : `next_step="finalize"` ; les éventuels warnings restent dans le rapport.
2. **Finding bloquant et tour encore disponible** : choisit l'étape la plus en amont parmi les cibles
   signalées, copie les messages dans `corrections`, incrémente `feedback_rounds` et route à cette
   étape.
3. **Reprise** : l'étape ciblée et toutes les étapes suivantes sont exécutées de nouveau ; l'auditeur
   est donc appelé une nouvelle fois. Les nouvelles sorties remplacent celles de ces étapes dans
   `outputs`, tandis que `history` et les findings gardent la trace de l'historique.
4. **Finding toujours présent après le nombre maximum de tours** : fixe `UNRELIABLE`, puis finalise
   sans publier.

La correction est un contexte/message remis à l'agent, pas un patch automatique garanti. Une
correction qui ne résout pas réellement le problème conduit au statut `unreliable`.

## 7. Statuts et publication

| Statut | Cause | Publication |
|---|---|---|
| `running` | Statut initial, tant que le pipeline s'exécute. | Non. |
| `success` | Toutes les étapes ont réussi et l'auditeur n'a pas d'écart bloquant. | Oui. |
| `failed` | Une étape a échoué techniquement ou à ses contrôles après épuisement des retries. | Non ; les étapes suivantes ne tournent pas. |
| `unreliable` | L'auditeur garde un finding bloquant après la boucle de correction permise. | Non. |

`_make_finalize_node()` convertit `running` en `success` lorsque le parcours normal est terminé.
Seul `success` produit `published=True`. `write_run_files()` écrit `<workspace>/run.json` ; si le
run est publié, il met également à jour `published.json` dans le dossier parent. Les échecs ne
remplacent donc pas le pointeur du dernier run publié.

## 8. Traces et rapport

`TraceRecord` est une ligne logique par tentative, contenant run, étape, numéro de tentative, statut,
dates, volumes, message et détails. `BaseAgent` trace les succès, retries et échecs. `finalize`
ajoute la trace récapitulative de run nommée `__run__`.

Deux implémentations de `TraceLogger` :

- `MemoryTraceLogger` : trace en mémoire ; utilisée par les tests et les exécutions sans base ;
- `DbTraceLogger` : insertion dans `audit.trace_log` et lecture des traces avec `fetch_run()`.

Particularité de résilience : `DbTraceLogger.log()` capture et journalise une exception d'écriture
de trace plutôt que de faire échouer le pipeline. Une trace DB peut donc manquer même si le calcul
du run a continué. Le journal de sortie `run.json`, lui, est écrit au nœud de finalisation si le
workspace est défini.

`format_summary()` transforme le state final en résumé CLI : étapes, essais, durées, validations,
KPI, questions, insights, audit, erreurs, findings, publication et chemins d'artefacts.

## 9. Démarrage en ligne de commande

Le point d'entrée est `python -m biflow.run`. Les options usuelles :

| Option | Effet |
|---|---|
| `--file <chemin>` | Fichier CSV/Parquet à la place de la table brute. |
| `--start`, `--end` | Fenêtre de dates de la table. |
| `--question <texte>` | Question métier ; répétable. |
| `--no-db` | Utilise une trace mémoire et un fichier de données (fourni ou choisi par le CLI). |
| `--max-retries`, `--retry-delay` | Paramètres de reprises d'étapes. |
| `--max-feedback-rounds` | Nombre de boucles de correction de l'auditeur. |
| `--output-dir` | Dossier parent des artefacts. |
| `--simulate-failure <étape>` | Démo/test d'une panne persistante de l'étape. |
| `--simulate-audit-flag <étape>` | Démo/test d'un finding de l'auditeur. |
| `--persistent` | Avec le flag d'audit, conserve le finding après la reprise pour produire `unreliable`. |

Codes de retour CLI : `0` pour succès, `1` pour run échoué, `2` pour run non fiable. Le CLI tente la
connexion PostgreSQL et initialise la base si elle est disponible ; sinon, il peut utiliser un
extrait local. Si ni base disponible ni extrait ne sont trouvés, il affiche une erreur et retourne 1
avant le lancement du pipeline.

## 10. Erreurs fréquentes et où chercher

| Symptôme | Cause probable / comportement | Fichier à consulter |
|---|---|---|
| `Agents manquants` à la construction | Dictionnaire d'agents incomplet ou mauvais identifiant. | `graph.py`, `agents/__init__.py` |
| `Sortie de ... absente du state` | Une dépendance n'a pas été exécutée ou son nom de sortie est incorrect. | `state.py`, agent consommateur, historique dans `run.json` |
| `state.workspace non défini` | Exécution manuelle avec un state incomplet ; `run_pipeline()` le définit normalement. | `state.py`, `graph.py` |
| `StepValidationError` | Un contrôle bloquant de sortie a échoué ; vérifier son nom/message dans `errors`, `validations` et la trace. | `base.py`, agent concerné |
| Run `failed` | Exception ou validation bloquante répétée ; les étapes aval ne s'exécutent pas. | `run.json`, `history`, `errors`, logs |
| Run `unreliable` | Findings bloquants encore présents après reprise d'audit. | `findings`, sorties `auditor_xai`, `feedback_rounds` |
| Trace PostgreSQL absente mais run terminé | Écriture dans `audit.trace_log` échouée et journalisée ; cette erreur est volontairement non bloquante. | `tracing.py`, logs |
| CLI termine avant le run | PostgreSQL indisponible et aucun fichier brut sélectionnable. | `run.py`, configuration et `data/raw/` |
| Reprise au mauvais endroit ou non déclenchée | Cible invalide, finding non bloquant, ou budget de feedback épuisé. | `graph.py`, `findings`, `max_feedback_rounds` |

## 11. Tests utiles

[test_orchestrator.py](../../tests/test_orchestrator.py) couvre le succès bout en bout, le
`run.json`, les runs non publiés, les retries transitoires, l'échec persistant, les validations,
l'absence d'agent, le renvoi correctif et le finding persistant. [conftest.py](../../tests/conftest.py)
fournit les fixtures de pipeline et de contexte.

Pour lancer la suite de tests du projet depuis sa racine : `pytest`.

## 12. Explication courte pour une validation orale

> « L'orchestrateur reçoit une référence de données, construit un state de run et exécute les six
> agents séquentiellement avec LangGraph. `BaseAgent` applique à chaque étape son contrat Pydantic,
> ses contrôles, ses retries et sa trace. Une panne persistante arrête le pipeline en `failed`.
> Après les étapes métier, l'auditeur peut demander une seule reprise ciblée ; si l'écart persiste,
> le run est `unreliable`. Seul un run `success` écrit le pointeur de publication. »

