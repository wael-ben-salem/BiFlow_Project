# Agent Auditeur XAI (`auditor_xai`)

## Mission

L'auditeur est la dernière étape du pipeline nominal et un garde-fou avant publication. Il vérifie
par du code que les données, requêtes, KPI, insights et graphiques sont cohérents et reproductibles.
Il n'utilise pas un LLM pour décider si les chiffres sont corrects.

Chaque vérification produit un `AuditCheck`. Les écarts deviennent des `AuditFinding` associés à
l'étape d'origine. Un écart bloquant peut déclencher une boucle de correction ; si l'écart persiste
après le tour autorisé, le run est marqué `unreliable` et n'est pas publié.

## Fichiers liés

| Fichier | Responsabilité |
|---|---|
| [auditor_xai.py](../../src/biflow/agents/auditor_xai.py) | Modèles d'audit, comparaisons, recalcul pandas, vérifications et explication. |
| [graph.py](../../src/biflow/orchestrator/graph.py) | Routage des findings, retour à l'étape fautive, statut final et décision de publication. |
| [state.py](../../src/biflow/orchestrator/state.py) | `AuditFinding`, `RunStatus`, ordre d'étapes et corrections partagées. |
| [sandbox.py](../../src/biflow/semantic/sandbox.py) | Rejeu en lecture seule des requêtes enregistrées. |
| [snapshot.py](../../src/biflow/common/snapshot.py) | Hash contenu du snapshot et hash fichier de spec. |
| [numbers.py](../../src/biflow/analysis/numbers.py) | Contrôle des nombres des textes d'insight. |
| [test_agents.py](../../tests/test_agents.py) | Approbation nominale et détection d'un KPI incohérent. |
| [test_orchestrator.py](../../tests/test_orchestrator.py) | Boucle de correction réussie et résultat `unreliable` persistant. |

## Entrées et sortie

L'auditeur consomme les sorties du profiler, de l'ETL, des KPI, de l'analyste et du générateur de
dashboard, ainsi que les validations existantes dans le state.

`AuditOutput`, stocké sous `state.outputs["auditor_xai"]`, contient :

- `approved` : absence de finding bloquant détecté par l'audit ;
- `checks` : nom, résultat, étape cible et message de chaque vérification ;
- `findings` : écarts à corriger ou avertissements propagés des validations précédentes ;
- `explanation` : résumé lisible des volumes, requêtes, analyses et écarts ;
- champs communs de suivi et notes de nombre de contrôles réussis.

## Contrôles effectués

### Données et provenance

- le nombre de lignes profilées égale les lignes lues par l'ETL ;
- le hash recalculé du snapshot égale celui annoncé par l'ETL ;
- le nombre de lignes relues dans le snapshot égale `rows_out`.

Ces écarts sont adressés à `quality_etl`.

### KPI, requêtes et confidentialité

- les requêtes enregistrées ont le même `snapshot_hash` que le snapshot ETL ;
- chaque KPI égale le résultat de sa requête référencée ;
- chaque requête est rejouée dans le sandbox et son résultat comparé à celui enregistré ;
- certains KPI principaux sont recalculés indépendamment en pandas (`independent_kpis`) ;
- les ventilations ne doivent pas exposer une ligne en dessous de `min_group_size` ;
- les valeurs visibles de chaque ventilation doivent correspondre à la requête.

Ces écarts sont adressés à `kpi_semantic`.

### Insights et dashboard

- `unsupported_numbers()` vérifie que les nombres du texte d'insight sont présents dans ses preuves ;
- toute preuve qui référence un KPI doit égaler sa valeur ;
- le hash du fichier de spécification dashboard doit rester valide ;
- valeur des cartes KPI et valeurs des graphiques de ventilation comparées aux données sources.

Les écarts sont adressés à `analyste_bi` ou `dashboard_generator` selon leur origine.

## Fonctions importantes

- `same(a, b, rel)` : égalité profonde avec tolérance numérique configurable.
- `same_rows(a, b)` : comparaison de résultats SQL indépendante de leur ordre de lignes.
- `independent_kpis(clean)` : recalcul indépendant en pandas de volume, taux de clôture, médiane et
  conformité SLA quand les valeurs existent.
- `AuditorXAIAgent.run(state)` : exécute les contrôles, construit findings et produit le rapport.
- `explain(state, checks, findings, clean)` : synthèse compréhensible du chemin de calcul et des écarts.
- `validate(output, state)` : vérifie que les findings ciblent une étape valide et que `approved`
  est cohérent avec les findings bloquants.

## Boucle de feedback et publication

Dans `graph.py`, `_audit_routing()` filtre les findings de sévérité `error` :

1. Aucun finding bloquant : passage à `finalize`, statut `success`, publication permise.
2. Findings bloquants et tour disponible : sélection de l'étape en amont la plus proche, message
   copié dans `state.corrections[target_step]`, puis reprise à cette étape et réexécution de l'aval.
3. Finding toujours présent après le nombre maximum de tours (`max_feedback_rounds=1` par défaut) :
   statut `unreliable`, sans publication.

Un échec technique d'une étape est différent : il produit directement `failed` après les retries
gérés par `BaseAgent`. `write_run_files()` écrit le `run.json`, mais ne met à jour
`published.json` que si le run est publié.

## Erreurs, findings et avertissements

| Situation | Conséquence |
|---|---|
| Snapshot illisible, références inaccessibles ou exception inattendue pendant l'audit | Échec technique de l'étape, retries communs, puis run `failed` si persistant. |
| Requête enregistrée non rejouable ou résultat différent | Finding visant `kpi_semantic`; tentative de correction, puis `unreliable` si persistant. |
| KPI ou ventilation incohérent | Finding `kpi_semantic` ; même boucle de correction. |
| Nombre d'insight sans preuve ou preuve KPI divergente | Finding `analyste_bi`. |
| Hash dashboard incorrect ou valeur affichée divergente | Finding `dashboard_generator`. |
| Contrôle métier précédent en warning | Finding d'audit de sévérité `warning`, affiché dans le rapport mais sans renvoi bloquant. |
| Problème de simulation `flag_step` | Utilisé par les scénarios/tests pour démontrer la boucle ; ce n'est pas une règle d'audit de production. |

Les tests de scénarios inspectent les findings de sévérité `error`. À noter dans le code : le champ
`approved` est calculé dans `run()` à partir des findings bloquants issus des contrôles d'audit ;
les warnings hérités sont ajoutés ensuite et ne provoquent pas de renvoi.

## Tests et éléments à expliquer

- Le run nominal doit être approuvé et chaque contrôle doit passer.
- Un KPI modifié artificiellement est détecté ; l'auditeur demande une correction de l'étape KPI.
- Un défaut corrigé une fois peut mener à un second audit réussi.
- Un défaut persistant après le renvoi rend le run `unreliable` et empêche la publication.

Voir [test_agents.py](../../tests/test_agents.py) et [test_orchestrator.py](../../tests/test_orchestrator.py).

**À retenir :** l'auditeur reconstitue la chaîne de preuve, compare/rejoue les valeurs et ne confond
pas la réussite d'une étape avec l'autorisation de publier.

