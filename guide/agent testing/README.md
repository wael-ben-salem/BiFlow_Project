# Outil de simulation des pannes d'agents

## Rôle

`FailingAgent` est un outil pour les tests automatisés et les démonstrations de panne. Il enveloppe
un vrai agent et provoque des exceptions pendant un nombre d'appels choisi, afin de vérifier les
retries de l'orchestrateur et l'arrêt du pipeline. Ce n'est pas un agent métier et il n'est pas dans
le registre normal `default_agents()`.

## Fichier et fonctions

- [testing.py](../../src/biflow/agents/testing.py) : implémentation de `FailingAgent`.
- [base.py](../../src/biflow/agents/base.py) : classe et politique d'exécution communes auxquelles le
  wrapper délègue.
- [test_orchestrator.py](../../tests/test_orchestrator.py) : vérifie panne transitoire, panne
  persistante et paramétrage du nombre d'essais.

Le constructeur `FailingAgent(wrapped, fail_times)` reprend le nom et le modèle de sortie de l'agent
enveloppé. À chaque `run()` :

- si `fail_times` vaut `None`, il échoue toujours ;
- si `fail_times` vaut un entier, il échoue sur les premiers appels jusqu'à ce nombre, puis appelle
  `wrapped.run(state)` ;
- `validate()` délègue la validation à l'agent enveloppé.

Une panne simulée lève `RuntimeError` avec le nom de l'agent et le numéro d'appel. Elle traverse
ensuite le mécanisme de `BaseAgent.execute()` : tentative tracée, délai/retry selon `RunContext`, puis
statut `failed` si toutes les tentatives sont épuisées. En cas de succès après panne transitoire,
l'orchestrateur reprend le graphe normalement.

## Utilisation dans les tests

Les tests remplacent temporairement un agent dans le dictionnaire d'instances, par exemple :

```python
agents["quality_etl"] = FailingAgent(agents["quality_etl"], fail_times=1)
```

Le pipeline utilise alors le vrai ETL après la panne simulée. La classe permet de tester les
comportements d'erreur sans provoquer de vraie panne de base, de disque ou de dépendance externe.

