# Les quatre scénarios de démonstration BIFlow

## 1. À quoi servent ces scénarios ?

Les quatre scénarios permettent de démontrer le vrai pipeline BIFlow dans quatre situations :

1. exécution normale et publication ;
2. panne persistante d'une étape et arrêt sécurisé ;
3. écart signalé par l'auditeur, reprise, nouvel audit réussi et publication ;
4. écart toujours présent après la reprise et refus de publication.

Ils ne sont pas quatre pipelines différents et ne reposent pas sur des résultats pré-calculés. Ils
utilisent le même graphe LangGraph, les mêmes agents et le jeu de données sélectionné. Les scénarios
de panne et d'audit injectent seulement un comportement de démonstration dans les agents.

## 2. Où les scénarios sont définis exactement ?

### Définition des choix et des résultats attendus

La source principale des scénarios de l'interface web est
[live.py](../../src/biflow/api/live.py) :

- `SCENARIOS` décrit pour chaque scénario son identifiant (`id`), son titre, son résumé, le statut
  attendu, les étapes sélectionnables et l'étape par défaut ;
- `SCENARIO_IDS` construit l'ensemble des identifiants acceptés ;
- `build_agents(scenario, step)` configure les agents selon le scénario ;
- `RunManager.start(...)` valide les paramètres, choisit l'étape par défaut, limite le ralentissement
  de démonstration, puis lance le run ;
- `RunManager._execute(...)` appelle le vrai `run_pipeline(...)` ;
- `_instrument(...)` ajoute les événements de progression utilisés par l'interface ;
- `LiveTraceLogger.log(...)` convertit les traces d'étapes et la trace finale en événements live.

Le fonctionnement du pipeline, lui, est défini par :

- [graph.py](../../src/biflow/orchestrator/graph.py) : graphe, `run_pipeline`, routage, feedback et
  publication ;
- [state.py](../../src/biflow/orchestrator/state.py) : `STEP_ORDER`, état, statuts et findings ;
- [base.py](../../src/biflow/agents/base.py) : exécution, validations et retries ;
- [testing.py](../../src/biflow/agents/testing.py) : wrapper `FailingAgent` utilisé pour simuler
  l'échec d'une étape ;
- [auditor_xai.py](../../src/biflow/agents/auditor_xai.py) : options de simulation `flag_step` et
  `persistent` de l'auditeur.

### Interface, API et documentation existante

- [Demo.tsx](../../frontend/src/pages/Demo.tsx) affiche les scénarios reçus de l'API, permet de
  sélectionner une étape, un jeu de données, des questions et le ralentissement, puis démarre un run.
- [app.py](../../src/biflow/api/app.py) définit `RunRequest`, publie les scénarios via `GET /api/meta`
  et reçoit `POST /api/runs`.
- [SCENARIOS.md](../../docs/SCENARIOS.md) explique le pipeline et les règles de chaque agent ; il
  contient aussi des commandes de démonstration pour les trois scénarios de robustesse.
- [DEMO.md](../../docs/DEMO.md) contient le déroulé conseillé pour la soutenance.

Les scénarios de robustesse sont aussi disponibles en ligne de commande. Le CLI n'envoie pas de
`scenario` textuel : les options `--simulate-failure`, `--simulate-audit-flag` et `--persistent`
configurent directement les mêmes simulations dans [run.py](../../src/biflow/run.py).

## 3. Résumé comparatif

| Identifiant web | Nom | Injection | Statut attendu | Publié ? |
|---|---|---|---|---|
| `nominal` | Run nominal | Aucune | `success` | Oui |
| `panne` | Panne d'un agent | `FailingAgent` sur l'étape choisie | `failed` | Non |
| `correction` | Écart corrigé par l'Auditeur | L'auditeur signale une étape au premier audit seulement | `success` après reprise | Oui |
| `non_fiable` | Écart persistant | L'auditeur signale la même étape aux deux audits | `unreliable` | Non |

`STEP_ORDER` est :

```text
data_profiler -> quality_etl -> kpi_semantic -> analyste_bi
              -> dashboard_generator -> auditor_xai
```

Le scénario nominal exécute les six étapes une fois. Le scénario de panne arrête le pipeline au point
de panne. Une reprise d'audit démarre à l'étape signalée et exécute tout son aval, auditeur compris.

## 4. Scénario 1 — Run nominal

### But

Montrer que, sur un jeu de données choisi, les six agents s'exécutent dans l'ordre et que le run n'est
publié qu'après validation par l'auditeur.

### Déroulement dans le code

1. L'interface soumet `scenario="nominal"` sans étape à signaler.
2. `RunManager.start()` vérifie que l'identifiant figure dans `SCENARIO_IDS`. Comme `step_choices` est
   vide, il force `step=None`.
3. `build_agents("nominal", None)` retourne les agents réels de `default_agents()`, sans wrapper de
   panne ni auditeur de simulation.
4. `RunManager._execute()` appelle `run_pipeline()` avec le fichier choisi, les questions et un
   `RunContext` établi depuis la configuration.
5. LangGraph exécute les six agents ; l'auditeur n'ayant pas de finding `error`, `finalize` fixe
   `success` et la publication.

### Fonctions à regarder / modifier

- Texte, statut attendu et choix d'étapes disponibles : `SCENARIOS` dans
  [live.py](../../src/biflow/api/live.py).
- Valeurs par défaut des questions : `DEFAULT_QUESTIONS` dans le même fichier.
- Registre réel des agents : `default_agents()` dans
  [agents/__init__.py](../../src/biflow/agents/__init__.py).
- Ordre d'exécution : `STEP_ORDER` dans [state.py](../../src/biflow/orchestrator/state.py).
- Sélection du jeu, questions et vitesse dans le navigateur : fonction `start()` dans
  [Demo.tsx](../../frontend/src/pages/Demo.tsx).

### Vérifications

Le test `test_nominal_run_is_published_and_served` dans
[test_api.py](../../tests/test_api.py) contrôle le statut, la publication, la réussite de chaque
étape et le dashboard servi ensuite.

## 5. Scénario 2 — Panne d'un agent

### But

Montrer qu'une panne d'étape ne permet pas de publier des résultats partiels et que les étapes aval
ne démarrent pas.

### Déroulement dans le code

1. `SCENARIOS` autorise la sélection d'une étape dans `STEP_ORDER`; `quality_etl` est l'étape proposée
   par défaut.
2. `RunManager.start()` refuse une étape qui ne fait pas partie des choix autorisés.
3. `build_agents("panne", step)` enveloppe l'agent choisi dans `FailingAgent(agent)`.
4. `FailingAgent` est configuré sans `fail_times` ; conformément à
   [testing.py](../../src/biflow/agents/testing.py), il lève donc `RuntimeError` à chacun de ses appels.
5. `BaseAgent.execute()` applique le nombre de retries configuré. Avec `max_retries=2`, il y a un
   essai initial plus deux retries, soit trois tentatives.
6. Après l'échec final, l'étape est marquée `FAILED`; le graphe route directement à `finalize`.
   Les étapes en aval ne tournent pas et le pointeur publié n'est pas remplacé.

La panne est simulée, mais ce sont les vrais mécanismes d'exécution, de retry, de trace et de
finalisation qui la traitent.

### Fonctions à regarder / modifier

- Étapes disponibles et étape par défaut : entrée `panne` dans `SCENARIOS` de
  [live.py](../../src/biflow/api/live.py).
- Injection de la panne : branche `scenario == "panne"` dans `build_agents()`.
- Modifier ce qui échoue et combien de fois : constructeur et méthode `run()` de
  [FailingAgent](../../src/biflow/agents/testing.py).
- Nombre de tentatives et délai : `RunContext` dans
  [base.py](../../src/biflow/agents/base.py), configuration dans [config.py](../../src/biflow/common/config.py)
  et options `--max-retries` / `--retry-delay` dans
  [run.py](../../src/biflow/run.py).
- Arrêt après échec et absence de publication : `_make_agent_node()` et
  `_make_finalize_node()` dans [graph.py](../../src/biflow/orchestrator/graph.py).

### Vérifications

`test_failure_scenario_retries_then_stops` dans
[test_api.py](../../tests/test_api.py) vérifie trois débuts d'essai pour `quality_etl`, le statut
`failed` et l'absence de démarrage de `kpi_semantic`. Le test
`test_persistent_failure_marks_run_failed_and_stops` dans
[test_orchestrator.py](../../tests/test_orchestrator.py) vérifie également l'arrêt et l'absence de
publication au niveau du graphe.

## 6. Scénario 3 — Écart corrigé par l'Auditeur

### But

Montrer que l'auditeur peut demander une reprise ciblée, puis que le run peut être publié si le
second audit n'a plus de finding bloquant.

### Déroulement dans le code

1. L'utilisateur choisit une étape parmi `STEP_ORDER[:-1]` : l'auditeur lui-même ne peut pas être la
   cible.
2. `build_agents("correction", step)` remplace l'auditeur normal par
   `AuditorXAIAgent(flag_step=step, persistent=False)`.
3. Au premier audit (`feedback_rounds == 0`), le mode simulation ajoute un finding `error` ciblant
   l'étape choisie.
4. `_audit_routing()` dans [graph.py](../../src/biflow/orchestrator/graph.py) choisit la cible la
   plus en amont parmi les findings bloquants, écrit le message dans `state.corrections` et augmente
   `feedback_rounds`.
5. Le graphe reprend à l'étape ciblée puis exécute à nouveau toutes les étapes en aval.
6. L'auditeur simulé ne réinjecte pas le finding au second audit, car `persistent=False`.
7. L'absence de finding bloquant mène à `success` et à la publication.

Le finding du premier audit est une injection de démonstration, pas la réparation automatique d'une
véritable anomalie. La simulation montre que le mécanisme de retour fonctionne ; lors d'un vrai
finding, il revient à l'étape visée avec le message de correction disponible dans
`state.corrections[step]`.

### Fonctions à regarder / modifier

- Étapes proposées comme cibles : entrée `correction` dans `SCENARIOS`.
- Injection du finding : branche `scenario in ("correction", "non_fiable")` de `build_agents()`.
- Conditions de simulation : `AuditorXAIAgent.run()` dans
  [auditor_xai.py](../../src/biflow/agents/auditor_xai.py), paramètres `flag_step` et `persistent`.
- Choix de la cible, stockage du feedback et routage : `_audit_routing()` dans
  [graph.py](../../src/biflow/orchestrator/graph.py).
- Lecture du message par l'agent relancé : `state.corrections.get(self.name)` dans les agents
  concernés.
- Nombre de boucles autorisées : `max_feedback_rounds` dans `RunContext` et configuration.

### Vérifications

`test_audit_feedback_loop` dans [test_api.py](../../tests/test_api.py) attend deux événements d'audit
avec verdicts `[False, True]` et statut final `success`. Le test
`test_auditor_feedback_loop_corrects_once` dans
[test_orchestrator.py](../../tests/test_orchestrator.py) vérifie que les étapes ciblées et aval sont
effectivement rejouées.

## 7. Scénario 4 — Écart persistant / run non fiable

### But

Montrer que le système n'autorise pas une publication si le finding bloquant demeure après la
tentative de correction.

### Déroulement dans le code

Le démarrage est identique au scénario de correction, mais `build_agents()` construit
`AuditorXAIAgent(flag_step=step, persistent=True)`. Il signale donc la même étape au premier et au
second audit :

1. Premier finding : `_audit_routing()` demande la reprise ciblée (si le budget le permet).
2. Reprise de l'étape et de son aval.
3. Second finding toujours présent ; le budget de feedback est épuisé.
4. `_audit_routing()` fixe le statut `UNRELIABLE` et route à `finalize`.
5. `finalize` écrit l'état du run, mais n'actualise pas `published.json`.
6. Le tableau de bord conserve la référence au dernier run publié valide.

### Fonctions à regarder / modifier

- Résumé et statut attendu : entrée `non_fiable` dans `SCENARIOS`.
- Persistance simulée : `persistent=scenario == "non_fiable"` dans `build_agents()`.
- Décision `UNRELIABLE` : branche du budget épuisé dans `_audit_routing()`.
- Mise à jour conditionnelle du pointeur : `write_run_files()` dans
  [graph.py](../../src/biflow/orchestrator/graph.py).

### Vérifications

Le test `test_audit_feedback_loop` dans [test_api.py](../../tests/test_api.py) vérifie que le statut
est `unreliable`, que la publication est fausse et que le pointeur reste celui du run corrigé
précédent. `test_auditor_persistent_issue_marks_run_unreliable` dans
[test_orchestrator.py](../../tests/test_orchestrator.py) vérifie l'historique de reprise.

## 8. Comment les scénarios passent par l'interface web

1. `GET /api/meta`, défini dans [app.py](../../src/biflow/api/app.py), transmet au front `SCENARIOS`,
   les étapes, les jeux de données, les questions par défaut et les limites configurées.
2. [Demo.tsx](../../frontend/src/pages/Demo.tsx) affiche les options reçues, puis appelle `api.start()`.
3. `POST /api/runs` valide le jeu choisi et appelle `RunManager.start()`.
4. `RunManager` démarre un fil d'exécution pour ne pas bloquer l'API.
5. `_instrument()` émet les événements `started` et `audit`; `LiveTraceLogger` publie les événements
   de succès, retry, échec et résumé de run.
6. Le front interroge `GET /api/runs/{run_id}/live` pour animer la ligne, puis lit le `run.json`
   final.

`pace_s` ne change pas la logique métier : c'est seulement une pause de présentation avant l'exécution
de chaque agent. Le gestionnaire API la limite entre 0 et 3 secondes.

## 9. Lancer les quatre scénarios

### Depuis l'application

```powershell
.\scripts\tasks.ps1 web
```

Ouvrir `http://localhost:8000`, puis la page **Scénarios**. Choisir le scénario, l'étape si nécessaire,
un extrait et les questions, puis lancer. Pour accélérer les scénarios de robustesse, décocher
**Ralentir pour la présentation**.

### Depuis la ligne de commande

Les options de simulation sont exposées par `python -m biflow.run` :

```powershell
# Nominal
python -m biflow.run --no-db --file data/raw/nyc311_2023-01_2026-09_echantillon.parquet

# Panne persistante d'une étape (ici ETL), trois tentatives par défaut
python -m biflow.run --no-db --file data/raw/nyc311_2023-01_2026-09_echantillon.parquet --simulate-failure quality_etl

# Finding d'audit injecté puis retiré au second audit
python -m biflow.run --no-db --file data/raw/nyc311_2023-01_2026-09_echantillon.parquet --simulate-audit-flag kpi_semantic

# Même finding réinjecté après la reprise
python -m biflow.run --no-db --file data/raw/nyc311_2023-01_2026-09_echantillon.parquet --simulate-audit-flag kpi_semantic --persistent
```

Dans PowerShell, la commande de l'environnement virtuel peut être utilisée sous la forme
`.\.venv\Scripts\python -m biflow.run ...`, comme dans [DEMO.md](../../docs/DEMO.md).

## 10. Modifier ou ajouter un scénario sans oublier les surfaces liées

Pour ajuster un scénario existant :

1. Modifier sa ligne dans `SCENARIOS` : l'`id` sert de contrat API, `expected` sert à l'interface et
   `step_choices` valide les cibles.
2. Modifier `build_agents()` pour changer l'injection ; utiliser `FailingAgent` pour un échec,
   `AuditorXAIAgent` pour les findings simulés.
3. Vérifier les limites d'étape et les valeurs par défaut dans `RunManager.start()`.
4. Mettre à jour le texte de la page web seulement si le rendu générique de `Demo.tsx` ne suffit pas.
   Éviter de recopier la liste des scénarios dans le front : elle est fournie par `/api/meta`.
5. Mettre à jour les tests d'API et les documents de démonstration sémantiquement concernés.

Pour créer un nouveau scénario, ajouter au minimum une entrée dans `SCENARIOS`, le comportement
correspondant dans `build_agents()` (ou une configuration spécifique équivalente), puis un test dans
[test_api.py](../../tests/test_api.py). Si le nouveau comportement change le pipeline au-delà du cas
de démonstration, ajouter aussi un test au niveau de [test_orchestrator.py](../../tests/test_orchestrator.py).
Mettre à jour [DEMO.md](../../docs/DEMO.md) et [SCENARIOS.md](../../docs/SCENARIOS.md) pour garder la
documentation de soutenance synchronisée.

## 11. Tests essentiels

Les tests d'API utilisent un jeu synthétique isolé, `pace_s=0` et des retries sans délai :

- `test_meta_lists_scenarios_and_dataset` : contrat de la liste des quatre ids ;
- `test_nominal_run_is_published_and_served` : succès et publication ;
- `test_failure_scenario_retries_then_stops` : retries, arrêt et absence d'aval ;
- `test_audit_feedback_loop` : correction réussie et problème persistant ;
- `test_invalid_requests` : scénario inconnu et étape non permise sont refusés ;
- `test_failed_start_releases_lock` : l'API libère son verrou si le lancement du fil échoue.

Lancer les tests ciblés depuis la racine du dépôt :

```powershell
pytest tests/test_api.py tests/test_orchestrator.py
```

## 12. À retenir pour la soutenance

- Les quatre scénarios utilisent les **mêmes vrais agents et le même vrai orchestrateur**.
- La panne et l'écart d'audit sont injectés volontairement pour démontrer les garde-fous.
- Une panne après retries donne `failed`; un écart d'audit encore présent donne `unreliable`.
- Seul `success` actualise le pointeur du dashboard publié.
- Pour changer le comportement : `SCENARIOS` décrit le contrat, `build_agents()` injecte le cas,
  puis les tests d'API et de l'orchestrateur vérifient le résultat.

