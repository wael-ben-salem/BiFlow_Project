# Guide de compréhension du projet BIFlow

Ce guide est destiné à la préparation d'une validation orale ou technique. Il explique le parcours
des données, les agents, les fichiers à connaître, les contrôles, les erreurs et les artefacts produits.
Les noms et comportements décrits ici correspondent au code du dépôt.

## Documentation détaillée des agents

- [Orchestrateur](orchestrateur/README.md) — ordre des agents, état partagé, routage, retries,
  audit, traces et décision de publication.
- [Les quatre scénarios](scenarios/README.md) — origine, déroulement, code à modifier, tests et
  commandes pour le nominal, la panne, la correction et l'écart persistant.
- [Agent profiler](agent%20profiler/README.md) — lecture des données brutes, profil statistique,
  détection et signalement des défauts.
- [Agent ETL](agent%20etl/README.md) — nettoyage, minimisation, enrichissement, instantané Parquet
  et mise en quarantaine.
- [Agent KPI sémantique](agent%20kpi%20semantic/README.md) — indicateurs, questions métier en SQL,
  traçabilité, seuil de confidentialité et bac à sable.
- [Agent analyste BI](agent%20analyste%20bi/README.md) — tendances, anomalies, insights et contrôle
  des preuves chiffrées.
- [Agent dashboard](agent%20dashboard/README.md) — spécification des visualisations et liens aux
  requêtes qui produisent les chiffres.
- [Agent auditeur XAI](agent%20auditor/README.md) — rejeu, recalcul indépendant, findings et décision
  de renvoi avant publication.
- [Outil de test des agents](agent%20testing/README.md) — simulateur de panne utilisé par les tests
  et scénarios ; ce n'est pas une étape métier du pipeline.

## BIFlow en une minute

BIFlow prend un jeu NYC 311 brut, le contrôle, le transforme en données propres, calcule des
indicateurs, analyse les tendances, construit une spécification de tableau de bord puis fait vérifier
les résultats par un auditeur. Les calculs et les transformations importants sont réalisés par du code
Python/SQL déterministe ; le LLM n'est pas le moteur de nettoyage et ne doit pas inventer les chiffres.

```text
START -> data_profiler -> quality_etl -> kpi_semantic -> analyste_bi -> dashboard_generator -> auditor_xai -> finalize -> END
```

Le parcours canonique est défini par `STEP_ORDER` dans
[state.py](../src/biflow/orchestrator/state.py) et assemblé par
[graph.py](../src/biflow/orchestrator/graph.py). Le premier agent décrit les défauts ; le second
applique les règles et fabrique l'instantané que les étapes suivantes doivent réutiliser.
Chaque flèche signifie « puis », pas une branche parallèle. Après un finding bloquant, l'auditeur peut
renvoyer l'exécution vers l'étape fautive et ses étapes aval (une fois par défaut).

## Les six agents et leurs responsabilités

| Étape | Fichier principal | Responsabilité |
|---|---|---|
| `data_profiler` | [data_profiler.py](../src/biflow/agents/data_profiler.py) | Mesurer les données brutes, repérer et décrire des défauts sans modifier le jeu de données. |
| `quality_etl` | [quality_etl.py](../src/biflow/agents/quality_etl.py) | Nettoyer, minimiser, enrichir et versionner les données ; conserver les lignes supprimées en quarantaine. |
| `kpi_semantic` | [kpi_semantic.py](../src/biflow/agents/kpi_semantic.py) | Calculer des KPI et ventilations en SQL sur l'instantané, et répondre aux questions prises en charge. |
| `analyste_bi` | [analyste_bi.py](../src/biflow/agents/analyste_bi.py) | Étudier tendances et anomalies et produire des insights dont les chiffres sont rattachés à des preuves. |
| `dashboard_generator` | [dashboard_generator.py](../src/biflow/agents/dashboard_generator.py) | Générer les éléments et la spécification du tableau de bord à partir des sorties vérifiables. |
| `auditor_xai` | [auditor_xai.py](../src/biflow/agents/auditor_xai.py) | Vérifier les calculs, leur provenance et la cohérence du tableau de bord ; approuver ou demander une correction. |

L'instanciation et l'association nom -> instance se trouvent dans
[agents/__init__.py](../src/biflow/agents/__init__.py), dans `default_agents()`.
Le module [testing.py](../src/biflow/agents/testing.py) contient un wrapper de simulation de panne,
pas un septième agent métier enregistré par `default_agents()`.

## Carte des dossiers importants

| Dossier/fichier | À quoi il sert |
|---|---|
| [src/biflow/agents/](../src/biflow/agents) | Un module par agent, modèles de sortie et classe de base. |
| [src/biflow/orchestrator/](../src/biflow/orchestrator) | État partagé, graphe d'exécution, validation, journalisation et rapport du pipeline. |
| [src/biflow/common/](../src/biflow/common) | Configuration, schéma partagé, chargement des données, références métier et instantanés. |
| [src/biflow/semantic/](../src/biflow/semantic) | Catalogue sémantique, traduction de questions en SQL et exécution contrôlée des requêtes. |
| [src/biflow/analysis/](../src/biflow/analysis) | Algorithmes d'analyse, dont la détection d'anomalies et le contrôle des nombres d'insights. |
| [src/biflow/db/](../src/biflow/db) | Connexion, initialisation, chargement et modèles de la base de données. |
| [src/biflow/ingestion/](../src/biflow/ingestion) | Import et collecte de données NYC Open Data / Socrata. |
| [src/biflow/api/](../src/biflow/api) | API FastAPI utilisée par l'interface web et le suivi en direct. |
| [frontend/](../frontend) | Interface React/TypeScript : scénarios, dashboard, audit, questions et historique. |
| [tests/](../tests) | Tests unitaires et tests du pipeline complet, de l'API et de l'interface. |
| [data/raw/](../data/raw) | Données d'entrée locales ; certains jeux de données peuvent être fournis séparément. |
| [data/processed/](../data/processed) | Artefacts des runs : instantanés, quarantaine, spécification, journal `run.json` et pointeur de publication. |
| [scripts/](../scripts) | Scripts d'aide pour préparer les données et lancer des tâches/démonstrations. |
| [docs/](../docs) et [livrables/](../livrables) | Scénarios, documentation de démonstration et livrables de présentation. |

Pour le contexte fonctionnel général et le démarrage du projet, lire aussi le
[README principal](../README.md).

## Comment un agent est exécuté

Tous les agents suivent le contrat de [BaseAgent](../src/biflow/agents/base.py) :

1. L'orchestrateur appelle `execute(state, ctx)`.
2. `run(state)` réalise le travail de l'agent et renvoie un modèle Pydantic défini par `output_model`.
3. La sortie est convertie/validée selon le modèle ; `validate(output, state)` ajoute les contrôles métier.
4. Une exception ou un contrôle bloquant échoué est enregistré dans `state.errors` et dans la trace.
5. Par défaut, le premier échec est suivi de deux nouvelles tentatives (`max_retries=2`), avec un délai
   linéaire configurable. Si elles échouent toutes, le run devient `failed` et le graphe s'arrête.
6. En cas de succès, la sortie JSON est rangée dans `state.outputs[agent_name]`; la validation et
   l'historique sont ajoutés au state.

L'état et les types (`RunStatus`, `StepError`, `StepRun`, `Check`) sont définis dans
[state.py](../src/biflow/orchestrator/state.py). Les contrôles partagés du schéma et des plages se
trouvent dans [validation.py](../src/biflow/orchestrator/validation.py).

Une exception non résolue n'est pas équivalente à un refus de l'auditeur :

- `failed` : une étape a échoué malgré ses tentatives ; les étapes suivantes ne sont pas exécutées ;
- `unreliable` : le pipeline a terminé mais l'auditeur a encore un écart bloquant après l'unique
  tour de correction autorisé par défaut ;
- `success` : l'auditeur n'a pas d'écart bloquant ; le run est publiable.

Le routage de l'auditeur est dans `_audit_routing()` et la décision finale dans
`_make_finalize_node()` de [graph.py](../src/biflow/orchestrator/graph.py). Un finding peut renvoyer
le flux à l'étape fautive, qui ainsi que toutes les étapes en aval est alors réexécutée. Les runs non
réussis ne remplacent pas le pointeur publié.

## Artefacts d'un run

Le `workspace` est généralement `data/processed/<run_id>/`. Il peut contenir :

- `run.json` : état final sérialisé, sorties des agents, erreurs, validations, findings et historique ;
- `nyc311_clean.parquet` : instantané nettoyé de la couche ETL ;
- `quarantine.parquet` : lignes supprimées avec leur motif, si des lignes ont été écartées ;
- la spécification du tableau de bord, produite par l'agent correspondant.

Après un run `success`, `data/processed/published.json` référence le run publié. Un run `failed` ou
`unreliable` n'est pas publié. Le calcul de l'empreinte de l'instantané est implémenté dans
[snapshot.py](../src/biflow/common/snapshot.py).

## Tests utiles pour la validation

- [test_agents.py](../tests/test_agents.py) vérifie la détection des défauts du profiler, le nettoyage,
  les KPI, l'analyse, le dashboard et l'approbation de l'auditeur.
- [test_orchestrator.py](../tests/test_orchestrator.py) vérifie les sorties du pipeline, la trace,
  les nouvelles tentatives, les échecs et les renvois de l'auditeur.
- [test_extensions.py](../tests/test_extensions.py) couvre notamment la quarantaine et les extensions
  du flux de données.

Exécuter les tests Python depuis la racine du dépôt avec `pytest`. Pour vérifier l'ensemble du projet,
consulter les commandes de démarrage et de test dans [README.md](../README.md).

## Questions de révision

1. Quelle différence y a-t-il entre profiler et nettoyer ?
2. Où une sortie d'agent est-elle enregistrée et qui gère les retries ?
3. Qu'est-ce qui rend un run publiable ?
4. À quoi servent `snapshot_hash` et `quarantine.parquet` ?
5. Si un agent échoue définitivement, pourquoi le dashboard n'est-il pas remplacé ?
