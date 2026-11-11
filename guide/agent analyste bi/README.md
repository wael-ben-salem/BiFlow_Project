# Agent Analyste BI (`analyste_bi`)

## Mission

L'analyste transforme les KPI et l'instantané nettoyé en tendances, détections d'anomalies et
insights lisibles. Le code calcule les faits et associe chaque insight à des éléments `evidence`.
Le LLM est facultatif et sert uniquement à reformuler ces textes ; ses nombres sont contrôlés, et
un texte dont un chiffre n'est pas justifié est rejeté au profit du gabarit calculé.

## Fichiers importants

| Fichier | Responsabilité |
|---|---|
| [analyste_bi.py](../../src/biflow/agents/analyste_bi.py) | Modèles `Insight`/`AnalysisOutput`, construction des faits, rédaction LLM, validation de preuves. |
| [anomalies.py](../../src/biflow/analysis/anomalies.py) | Série quotidienne, STL/z-score robuste, contribution principale, calcul de tendance. |
| [numbers.py](../../src/biflow/analysis/numbers.py) | Extraction des nombres français et détection des chiffres non justifiés. |
| [kpi_semantic.py](../../src/biflow/agents/kpi_semantic.py) | KPI et ventilations que l'analyste utilise comme sources de preuves. |
| [snapshot.py](../../src/biflow/common/snapshot.py) | Relecture de l'instantané nettoyé utilisé pour l'analyse. |
| [schema.py](../../src/biflow/common/schema.py) | Liste des cinq boroughs sur lesquels les analyses géographiques s'appuient. |
| [test_agents.py](../../tests/test_agents.py), [test_extensions.py](../../tests/test_extensions.py) | Détection synthétique, preuves numériques, anomalies et réserves de provenance. |

## Entrées et sortie

Entrées : `state.outputs["kpi_semantic"]`, le snapshot indiqué par
`state.outputs["quality_etl"]["snapshot_path"]`, la provenance remontée par le profiler et les
réglages (notamment `min_group_size`).

`AnalysisOutput` contient :

- `insights` : titre, texte, références KPI, preuves numériques, catégorie et origine de rédaction
  (`template` ou `llm`) ;
- `anomalies` : anomalies sous forme de dictionnaires, avec zone et jour ;
- `detection_method` : méthode retenue ou `insuffisant` ;
- `trend_pct` : évolution calculée ou `null` ;
- `llm_model`, compteurs d'entrée et `notes`.

## Fonctions et logique

- `fmt(value)` : format numérique français.
- `_breakdown(kpi_out, name)` : récupère seulement les valeurs visibles, non nulles, en excluant
  `UNSPECIFIED`.
- `explain_anomaly(clean, zone, day)` : compare les types observés le jour de l'anomalie à leur
  niveau habituel pendant les 28 jours précédents ; c'est une piste, pas une explication causale.
- `build_insights(...)` : construit les insights de volume, délai, SLA, demandes par habitant,
  tendance et anomalies. Les séries sont calculées sur la ville puis par borough ; les zones dont
  l'activité moyenne est sous le seuil sont ignorées.
- `rewrite_with_llm(...)` : demande au LLM de reformuler les textes et récupère une liste JSON.
- `AnalysteBIAgent.run()` : lit les entrées, calcule les faits, tente éventuellement la reformulation
  et conserve les sorties.
- `validate()` : requiert au moins un insight, vérifie les références KPI et que chaque nombre du
  texte est soutenu par `evidence`.

Les constantes importantes sont `MAX_ANOMALY_INSIGHTS=5` et `CONTEXT_DAYS=28`.
`detect_anomalies` et `trend_pct` gèrent les périodes selon leur longueur ; une période trop courte
ne doit pas être interprétée comme l'absence certaine d'anomalies.

## Place du LLM et sécurité des nombres

Le prompt demande une ou deux phrases en français, sans calcul ni nouvelle cause, à partir des
`evidence` fournies. Le code vérifie ensuite `unsupported_numbers(text, evidence)`. Les nombres
invérifiables, une réponse vide ou une erreur du LLM gardent le texte du gabarit. La reformulation
étant facultative, son échec n'empêche pas l'étape de produire ses insights calculés.

Les réserves de provenance d'un extrait sont ajoutées au texte lorsque pertinent. Les objectifs SLA
sont explicitement décrits comme provisoires dans les insights liés aux SLA.

## Erreurs et comportement

| Situation | Conséquence |
|---|---|
| Sortie KPI manquante ou snapshot impossible à lire | Exception de l'agent ; retry commun, puis run `failed` si l'erreur persiste. |
| Erreur dans le calcul de série, données de colonne incomplètes ou incident Python | Échec de l'exécution, tracé et retenté par `BaseAgent`. |
| LLM indisponible, réponse malformée ou mauvais nombre de textes | Exception capturée dans `run()` ; gabarits conservés et note ajoutée. Pas d'échec de l'agent pour cette seule raison. |
| Le texte reformulé invente un nombre | Texte LLM rejeté, gabarit préservé ; compte de rejets dans `notes`. |
| Aucun insight | `insights_not_empty` échoue en validation ; retries puis run échoué si persistant. |
| Référence vers un KPI inconnu ou nombre non prouvé dans le texte final | Contrôle bloquant ; retry puis échec du run. Le validateur commun empêchera la progression normale. |
| Peu de jours pour l'analyse | L'agent ajoute une information « période trop courte » et la méthode `insuffisant` ; pas nécessairement une erreur technique. |

## Tests et réponse de validation

Les tests associés vérifient que l'anomalie injectée est détectée, qu'une série trop courte est
signalée comme insuffisante, que les nombres des insights sont prouvés et que le type le plus en
hausse est présenté comme une piste à vérifier, non comme une cause. Voir
[test_agents.py](../../tests/test_agents.py) et [test_extensions.py](../../tests/test_extensions.py).

**À retenir :** les faits sont calculés avant le LLM ; `evidence` est la justification ; toute
reformulation est jetable si elle viole les garde-fous.

