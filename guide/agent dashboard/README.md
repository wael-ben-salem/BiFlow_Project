# Agent générateur de dashboard (`dashboard_generator`)

## Mission

Le générateur met en forme les sorties des KPI et de l'analyse en une spécification JSON de
visualisation. Il choisit le type d'affichage à partir de règles simples, garde les identifiants des
requêtes à l'origine des chiffres et écrit `dashboard_spec.json` dans le dossier du run.

Cet agent ne publie pas le run. La publication est décidée par l'orchestrateur uniquement après un
audit réussi (`success`).

## Fichiers importants

| Fichier | Responsabilité |
|---|---|
| [dashboard_generator.py](../../src/biflow/agents/dashboard_generator.py) | `ChartSpec`, `DashboardOutput`, `build_charts()`, génération du JSON et validations. |
| [app.py](../../src/biflow/dashboard/app.py) | Application qui lit et affiche la spécification publiée. |
| [snapshot.py](../../src/biflow/common/snapshot.py) | Fonction `file_hash()` pour l'empreinte du fichier spec. |
| [graph.py](../../src/biflow/orchestrator/graph.py) | Exécute l'auditeur après le générateur et gère publication/renvoi. |
| [test_agents.py](../../tests/test_agents.py) | Vérifie hash du snapshot et traçabilité des graphiques numériques. |
| [test_dashboard.py](../../tests/test_dashboard.py) | Vérifie affichage publié et refus d'une spec altérée. |

## Entrées et sortie

Entrées : sorties `kpi_semantic` et `analyste_bi`.

`DashboardOutput` comprend le titre, la liste `charts`, le chemin de l'application, le chemin du JSON
et `spec_hash`. Chaque `ChartSpec` contient un identifiant, un titre, un type de graphique, les KPI
référencés, les `query_ids`, l'unité, une valeur optionnelle et les données de visualisation.

Le JSON contient aussi `run_id`, titre, `snapshot_hash`, référence du dataset et graphiques.
L'empreinte (`file_hash`) est calculée après écriture du fichier.

## Choix des visualisations

`build_charts(kpi_out, analysis)` applique les règles suivantes :

- chaque KPI devient une carte `kpi_card` avec valeur et requête d'origine ;
- une ventilation `created_day` devient une ligne `line` ; les jours correspondant aux anomalies
  de ville (`zone == "ALL"`) sont marqués ;
- une dimension catégorielle avec au plus six modalités devient un graphique `bar` ;
- plus de six modalités devient `bar_h`, tronqué aux quinze premières lignes ;
- les réponses métier deviennent une table `table`, avec SQL, statut, lignes ou erreur/refus ;
- les insights deviennent un bloc de texte `text`, avec références KPI associées.

`MAX_BAR_CATEGORIES=6`, `MAX_ROWS_DISPLAYED=15`, `TEMPORAL_DIMENSIONS={"created_day"}` et
`NUMERIC_CHARTS` sont les constantes qui cadrent la construction et la validation.

## Validations et erreurs

`validate()` effectue trois contrôles :

- `charts_not_empty` : il doit y avoir au moins un élément ;
- `chart_kpi_refs_exist` : les références KPI doivent exister dans les sorties KPI ;
- `charts_traced` : chaque graphique numérique doit contenir au moins un `query_id`.

| Situation | Conséquence |
|---|---|
| Sortie KPI ou analyse absente | `state.output()` lève `KeyError` ; l'étape est retentée, puis le run échoue si la sortie reste absente. |
| Workspace absent | `require_workspace()` lève `ValueError` ; retries puis échec si persistant. |
| Écriture JSON impossible (chemin, droits, disque) | Exception propagée au mécanisme commun ; retries puis `failed` si persistant. |
| Aucun graphique, référence inconnue ou graphe numérique non tracé | Validation bloquante ; retry, puis échec du run si le problème persiste. |
| Fichier modifié après génération/audit | Le hash ne concorde plus ; l'auditeur signale un finding visant `dashboard_generator`. La page dashboard vérifie aussi la spécification avant affichage. |

La validation de l'agent garantit la présence de références au moment de la génération ; l'auditeur
compare aussi les valeurs effectivement exposées avec les KPI/ventilations. Ces contrôles sont
complémentaires.

## Tests et point à retenir

`test_dashboard_spec_is_traced` contrôle que les graphiques numériques ont des `query_ids` et que la
spec reprend le hash du snapshot. Les tests de la page dashboard contrôlent qu'une spec altérée n'est
pas affichée.

**À retenir :** cet agent construit une spécification traçable, mais l'auditeur valide les données et
l'orchestrateur — pas l'agent dashboard — accorde ou refuse la publication.

