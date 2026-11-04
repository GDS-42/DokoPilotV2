---
name: rapprochement-bancaire
description: Proposer le rapprochement des mouvements bancaires non rapprochés (Bank Transaction) avec les factures clients et fournisseurs, et préparer au plus des paiements en brouillon (Payment Entry), sans rien rapprocher ni valider. À utiliser quand l'entrepreneur parle de banque, de relevé, de rapprochement, de paiements reçus ou de virements à identifier.
---
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->

# Rapprochement bancaire

Les relevés sont importés dans Dokos par l'entrepreneur (OFX, CSV, MT940). Vous proposez à
quelle facture correspond chaque mouvement ; le rapprochement et la validation des paiements
restent des gestes de l'entrepreneur dans Dokos.

## Étapes

1. **Lister les mouvements à rapprocher** avec `doc_list` (serveur `dokos`) :
   - `doctype` : `Bank Transaction`
   - `filters` : `[["docstatus", "=", 1], ["status", "=", "Unreconciled"]]`
   - `fields` : `["name", "date", "bank_account", "description", "reference_number", "deposit", "withdrawal", "unallocated_amount", "currency", "party_type", "party"]`
   - `order_by` : `"date asc"`, `limit` : 500
2. **Chercher les candidats** :
   - encaissement (`deposit`) : `Sales Invoice` validées avec `outstanding_amount` > 0 ;
   - décaissement (`withdrawal`) : `Purchase Invoice` validées avec `outstanding_amount` > 0 ;
   - indices, du plus fort au plus faible : numéro de facture ou référence dans la
     `description` ou le `reference_number`, montant exact, nom du tiers, date proche de
     l'échéance.
3. **Classer chaque proposition** : sûre (référence et montant), probable (montant et tiers),
   à vérifier (montant seul, paiement partiel, plusieurs factures, frais bancaires).
4. **Présenter un tableau** : mouvement (date, libellé, montant), document proposé (lien
   `/app/sales-invoice/<name>` ou `/app/purchase-invoice/<name>`), niveau de confiance, raison.
   Listez à part les mouvements sans candidat (frais, impôts, salaires, prélèvements).
5. **Préparer un paiement en brouillon**, seulement si l'entrepreneur le demande et pour une
   proposition sûre :
   - `meta_get` sur `Payment Entry`, et lisez le compte comptable du `Bank Account` ;
   - `doc_create` avec `{"doctype": "Payment Entry", "payment_type": "Receive" ou "Pay", "party_type": "Customer" ou "Supplier", "party": …, "company": …, "posting_date": <date du mouvement>, "paid_amount": …, "received_amount": …, "reference_no": <référence du mouvement>, "reference_date": <date du mouvement>, "references": [{"reference_doctype": …, "reference_name": …, "allocated_amount": …}]}`, en laissant Dokos compléter les comptes, ou en les reprenant de `meta_get` ;
   - ne renseignez jamais `docstatus`.
6. **Conclure** : dans Dokos, l'entrepreneur valide les paiements en brouillon puis rapproche
   les mouvements (outil de rapprochement bancaire).

## Ce que vous ne faites jamais

- Rapprocher un mouvement ou modifier une `Bank Transaction` : ce sont des documents validés.
- Valider un paiement, ni lancer une pipeline Dokos AI qui le ferait.
- Deviner un tiers ou forcer une affectation douteuse : posez la question.
- Communiquer un relevé ou un solde hors de la conversation avec l'entrepreneur.
