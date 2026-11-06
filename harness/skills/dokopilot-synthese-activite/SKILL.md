---
name: dokopilot-synthese-activite
description: Rédiger la synthèse d'activité de la semaine (ou d'une autre période) à partir de Dokos — chiffre d'affaires, impayés, trésorerie, factures électroniques reçues à traiter, points d'attention. Lecture seule. À utiliser quand l'entrepreneur demande un point, un bilan, un tableau de bord, « où en est-on » ou une synthèse.
---
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->

# Synthèse d'activité

Une page, lisible en deux minutes. Vous ne créez ni ne modifiez rien.

## Étapes

1. **Fixer la période** : par défaut, du lundi au dimanche de la semaine écoulée, comparée à
   la semaine précédente. Donnez les dates en tête de la synthèse.
2. **Chiffre d'affaires** : `doc_list` (serveur `dokos`) sur `Sales Invoice`, `filters`
   `[["docstatus", "=", 1], ["posting_date", "between", ["<début>", "<fin>"]]]`, `fields`
   `["name", "customer_name", "posting_date", "base_net_total", "base_grand_total", "is_return"]`,
   `limit` 500 ; additionnez le HT (`base_net_total`, les avoirs sont négatifs). Le rapport
   « Sales Register » (`run_report`) sert de contrôle.
3. **Impayés** : `run_report` avec `report_name` « Accounts Receivable » (filtre `company`) ; à
   défaut, `doc_list` sur `Sales Invoice` avec `outstanding_amount` > 0. Donnez le total, la
   part échue et les trois plus gros retards.
4. **À payer** : de même avec « Accounts Payable » ou `Purchase Invoice` (`outstanding_amount` > 0,
   `due_date` dans les quinze jours).
5. **Trésorerie** : solde des comptes bancaires (rapport « General Ledger » ou « Trial Balance »
   filtré sur les comptes de type banque), et nombre de `Bank Transaction` non rapprochées.
6. **Factures électroniques reçues** : `doc_count` sur `Supplier Invoice`, filtre `status` dans
   `["Pending", "In Progress", "Ready"]`, et la plus ancienne en attente.
7. **Brouillons en attente de validation** : `doc_count` des `Purchase Invoice`, `Sales Invoice`,
   `Payment Entry` et `Quotation` avec `docstatus` = 0.
8. **Rédiger** : un tableau de chiffres clés (période, période précédente, écart), puis trois à
   cinq points d'attention concrets, chacun avec le lien Dokos utile et le geste proposé.

Si un rapport échoue ou qu'un chiffre vous paraît incohérent, dites-le et donnez la méthode
de calcul utilisée plutôt qu'un chiffre douteux.

## Ce que vous ne faites jamais

- Créer, modifier ou valider un document pendant une synthèse.
- Présenter une estimation comme un chiffre comptable, ni arrondir sans le dire.
- Envoyer la synthèse à quelqu'un, ou la publier hors de la conversation.
