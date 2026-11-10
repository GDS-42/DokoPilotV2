---
name: dokopilot-relances-clients
description: Préparer les relances des factures clients échues et impayées (Sales Invoice) — liste par client, niveau de relance proposé, puis brouillons de courriels dans la messagerie, jamais envoyés. À utiliser quand l'entrepreneur parle d'impayés, de retards de paiement, de relances ou de recouvrement.
---
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->

# Relances clients

Vous préparez les relances ; l'entrepreneur choisit qui relancer, relit et envoie lui-même
depuis sa messagerie.

## Étapes

1. **Lister les factures échues** avec `doc_list` (serveur `dokos`) :
   - `doctype` : `Sales Invoice`
   - `filters` : `[["docstatus", "=", 1], ["outstanding_amount", ">", 0], ["due_date", "<", "<date du jour, AAAA-MM-JJ>"]]`
   - `fields` : `["name", "customer", "customer_name", "posting_date", "due_date", "grand_total", "outstanding_amount", "currency", "contact_email"]`
   - `order_by` : `"due_date asc"`, `limit` : 500
2. **Écarter les paiements en cours** : cherchez les `Bank Transaction` non rapprochées
   (`status` = `Unreconciled`) d'un montant égal au reste dû, et les `Payment Entry` en
   brouillon du client. Signalez-les au lieu de relancer.
3. **Regrouper par client** et proposer un niveau :
   - niveau 1, jusqu'à 15 jours de retard : rappel courtois ;
   - niveau 2, de 16 à 45 jours : relance ferme, avec la liste des factures ;
   - niveau 3, au-delà de 45 jours : dernier rappel ; la suite (mise en demeure, injonction
     de payer) est une décision de l'entrepreneur.
4. **Présenter un tableau** (client, factures, montant dû, jours de retard, niveau proposé,
   adresse de contact) et attendre que l'entrepreneur valide la liste.
5. **Préparer les brouillons**, un par client retenu :
   - adresse : `contact_email` de la facture, sinon `email_id` du `Customer` ; sans adresse,
     signalez-le ;
   - objet : « Rappel — facture <numéro> » (ou « Rappel — factures en attente ») ;
   - corps : ton du niveau retenu, numéros, dates, montants restant dus, moyens de paiement,
     signature de l'entrepreneur. Pour un client professionnel, et seulement si
     l'entrepreneur le veut : pénalités de retard et indemnité forfaitaire de 40 € pour frais
     de recouvrement (article L441-10 du Code de commerce).
   - Si la messagerie est configurée : `save_draft` du serveur `mail`, compte `principal`,
     `recipients`, `subject`, `body`. Sinon : donnez le texte à copier.
6. **Conclure** : la liste des brouillons enregistrés (dossier Brouillons de la messagerie) et
   le rappel que rien n'est parti.

## Ce que vous ne faites jamais

- Envoyer un courriel, ni demander à un autre outil de le faire.
- Marquer une facture comme payée, créer un paiement validé ou modifier une facture.
- Menacer de poursuites sans décision explicite de l'entrepreneur.
- Mentionner la dette d'un client à une autre personne que ce client.
