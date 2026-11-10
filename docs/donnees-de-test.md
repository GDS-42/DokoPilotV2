<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Données de test : l'« Atelier Démo »

Le profil `demo` charge dans Dokos une entreprise **fictive** pour essayer DokoPilot sans risque : l'« Atelier Démo », petite menuiserie-ébénisterie du Jura qui fabrique des meubles sur mesure. Les données ne sont jamais chargées en production (le rôle `demo_data` refuse tout autre profil que `demo`).

> **Tout est inventé.** Les raisons sociales contiennent toutes « Démo » ; les adresses, courriels (domaines réservés `.test`), numéros de téléphone, SIREN, numéros de TVA et IBAN sont fictifs. Les SIREN commencent par `0000`, préfixe qui n'est attribué à aucune entreprise : ils respectent seulement la clé de Luhn exigée par Dokos. Les IBAN désignent une banque `99999` qui n'existe pas. Les factures électroniques portent la mention « Facture FICTIVE générée pour la démonstration DokoPilot : aucune valeur juridique ni fiscale ».

Les dates sont relatives au jour du chargement (« il y a 30 jours ») : la démo reste vraisemblable quel que soit le jour de l'installation. Si le chargement tombe en début d'année, l'exercice précédent est créé automatiquement.

## Où se trouvent les données

| Fichier | Contenu |
| --- | --- |
| `demo/data/atelier-demo.yml` | La société, les tiers, les articles, les commandes, les factures et les factures reçues, avec des commentaires |
| `demo/bank/releve-demo.csv` | Le relevé bancaire d'exemple (7 opérations), aussi utilisable pour un essai d'import manuel |
| `demo/einvoices/generate.py` | Le générateur de factures électroniques reçues (CII ou UBL, norme EN 16931) |
| `demo/demo_loader.py` | Le chargeur, exécuté dans le conteneur Dokos par le rôle Ansible `demo_data` |

Le chargement est une seule transaction : en cas d'erreur, rien n'est gardé. Une fois fait, il n'est jamais refait (une marque est posée sur le site) ; pour repartir de zéro, voir [Recharger la démo](#recharger-la-démo).

## Comptes de démonstration

| Compte | Usage | Mot de passe |
| --- | --- | --- |
| `camille.martin@atelier-demo.test` | Camille Martin, la dirigeante : gère l'entreprise et **valide** les documents | `bin/dokopilot secrets demo show entrepreneur_password` |
| `assistant.camille@atelier-demo.test` | L'assistant de Camille, utilisé par son agent par MCP : lit et prépare des brouillons, ne valide rien | aucun (jeton d'API seulement, dans `/etc/dokopilot/assistants/` sur le serveur) |
| `Administrator` | Opérations d'installation uniquement | `bin/dokopilot secrets demo show dokos_admin_password` |

## Ce que contient la démo

**La société** : « Atelier Démo » (abréviation AD), France, euro, plan comptable général, exercice civil, SIREN `000000018`, TVA `FR66000000018`, 12 rue des Copeaux, 39000 Lons-le-Saunier. Modèles de TVA (20 %, 10 %, 5,5 %, 2,1 %), réglages de TVA et modèles d'écritures créés par la configuration française de Dokos.

**La banque** : compte courant « Banque Démo » (IBAN `FR76 9999 9000 0100 0000 0010 106`), relié au compte 5121, avec son journal de banque `BQ`.

**Les articles (12)**

| Code | Désignation | Type | Prix |
| --- | --- | --- | --- |
| PLANCHE-CHENE | Planche de chêne massif 27 mm | matière, stockée | achat 45,00 € |
| PANNEAU-CP18 | Panneau contreplaqué bouleau 18 mm | matière, stockée | achat 38,50 € |
| CHARNIERE-INV | Charnières invisibles (lot de 10) | fourniture, stockée | achat 24,00 € |
| VIS-INOX | Vis inox 4x40 (boîte de 500) | fourniture, stockée | achat 18,90 € |
| VERNIS-EAU | Vernis à l'eau incolore 5 L | fourniture, stockée | achat 62,00 € |
| COLLE-BOIS | Colle vinylique 1 kg | fourniture, stockée | achat 9,80 € |
| TABLE-CHENE | Table de salle à manger en chêne | produit fini | vente 1 450,00 € |
| CHAISE-CHENE | Chaise en chêne | produit fini | vente 280,00 € |
| BIBLIO-MESURE | Bibliothèque sur mesure | produit fini | vente 2 400,00 € |
| POSE | Pose et installation (heure) | prestation | vente 55,00 € |
| LIVRAISON | Livraison | prestation | vente 90,00 € |
| ETUDE-PLAN | Étude et plan sur mesure | prestation | vente 350,00 € |

Les prix sont hors taxes ; toutes les factures portent la TVA à 20 %.

**Les clients (6)** : Restaurant Démo Le Chêne Vert, Hôtel Démo des Tilleuls, Agence Démo Architecture Intérieure, Mairie de Démoville (secteur public), Cabinet Démo Dentaire, et un particulier, Jean Démo. Chacun a une adresse de facturation et un contact avec courriel.

**Les fournisseurs (6)** : Scierie Démo du Jura (bois massif), Panneaux Démo Bois (panneaux), Quincaillerie Démo Pro (quincaillerie), Peintures et Vernis Démo (finitions), Transports Démo Express (transport), Maintenance Machines Démo (entretien des machines). Chacun a son SIREN, son numéro de TVA et un compte bancaire (IBAN).

**Les achats**

| Commande | Fournisseur | Il y a | Contenu (HT) | Réception |
| --- | --- | --- | --- | --- |
| 1 | Scierie Démo du Jura | 70 j | 20 planches de chêne à 45,00 € = 900,00 € | complète, il y a 62 j |
| 2 | Panneaux Démo Bois | 45 j | 15 panneaux à 38,50 € = 577,50 € | complète, il y a 40 j |
| 3 | Quincaillerie Démo Pro | 30 j | 10 lots de charnières à 24,00 € + 4 boîtes de vis à 18,90 € = 315,60 € | **partielle** (charnières seulement), il y a 25 j |
| 4 | Peintures et Vernis Démo | 20 j | 6 vernis à 62,00 € + 10 colles à 9,80 € = 470,00 € | pas encore reçue |

Une facture d'achat plus ancienne (Peintures et Vernis Démo, n° PV-0412, il y a 85 jours, 297,60 € TTC) est déjà validée et payée.

**Les ventes** : 8 factures validées, échéance à 30 jours.

| Facture | Client | Il y a | Montant TTC | Situation |
| --- | --- | --- | --- | --- |
| 1 | Restaurant Démo Le Chêne Vert | 80 j | 3 744,00 € | payée (virement il y a 45 j) |
| 2 | Hôtel Démo des Tilleuls | 72 j | 3 300,00 € | **impayée, échue** |
| 3 | Agence Démo Architecture Intérieure | 60 j | 2 016,00 € | payée (virement il y a 30 j) |
| 4 | Mairie de Démoville | 50 j | 6 288,00 € | **impayée, échue** |
| 5 | Cabinet Démo Dentaire | 40 j | 1 848,00 € | acompte de 800,00 € ; **reste 1 048,00 € échu** |
| 6 | Jean Démo | 25 j | 780,00 € | non échue |
| 7 | Restaurant Démo Le Chêne Vert | 15 j | 2 688,00 € | non échue ; le virement est sur le relevé, mais le paiement n'est pas saisi |
| 8 | Hôtel Démo des Tilleuls | 8 j | 396,00 € | non échue |

En validant ces factures, eTransactions a aussi préparé leurs factures électroniques sortantes (Factur-X) ; elles ne sont envoyées à aucune plateforme (aucune n'est configurée).

**Le relevé bancaire** (7 opérations, non rapprochées)

| Il y a | Libellé | Montant | Correspond à |
| --- | --- | --- | --- |
| 59 j | VIR SEPA PEINTURES ET VERNIS DEMO FACT PV-0412 | −297,60 € | paiement de la facture d'achat PV-0412 (saisi) |
| 44 j | VIR SEPA RESTAURANT DEMO LE CHENE VERT | +3 744,00 € | paiement de la facture de vente 1 (saisi) |
| 29 j | VIR SEPA AGENCE DEMO ARCHITECTURE INTERIEURE | +2 016,00 € | paiement de la facture de vente 3 (saisi) |
| 28 j | FRAIS TENUE DE COMPTE OCTOBRE | −12,50 € | frais bancaires, aucune écriture |
| 11 j | VIR SEPA CABINET DEMO DENTAIRE ACOMPTE | +800,00 € | acompte sur la facture de vente 5 (saisi) |
| 3 j | VIR SEPA RESTAURANT DEMO LE CHENE VERT 8 CHAISES | +2 688,00 € | facture de vente 7, **paiement non saisi** |
| 2 j | PRLV SEPA TRANSPORTS DEMO EXPRESS TDE-2026-0944 | −222,00 € | facture reçue de Transports Démo Express, **pas encore traitée** |

**Les factures électroniques reçues** : 5 factures générées par `demo/einvoices/generate.py`, importées par le chemin de réception d'eTransactions (comme si la plateforme agréée les avait transmises). Elles apparaissent dans Dokos comme des **factures fournisseurs à traiter** (eTransactions › Factures fournisseurs), chacune avec sa facture électronique d'origine.

| N° | Fournisseur | Il y a | Format | Montant TTC | Particularité |
| --- | --- | --- | --- | --- | --- |
| SDJ-2026-0187 | Scierie Démo du Jura | 60 j | CII (Factur-X) | 1 080,00 € | conforme à la commande 1 et à sa réception |
| PDB-11872 | Panneaux Démo Bois | 38 j | CII | 756,00 € | **écart de prix** : 42,00 € facturés contre 38,50 € commandés (+52,50 € HT) |
| QDP-F-30512 | Quincaillerie Démo Pro | 24 j | CII | 378,72 € | facture toute la commande 3, alors que les vis **ne sont pas reçues** |
| TDE-2026-0944 | Transports Démo Express | 5 j | CII | 222,00 € | prestation **sans commande** ; déjà prélevée sur le relevé |
| MMD-7731 | Maintenance Machines Démo | 3 j | UBL (Peppol BIS 3) | 384,00 € | prestation **sans commande** |

Toutes passent la validation EN 16931 d'eTransactions (« Facture électronique conforme »). Le numéro de commande cité par une facture se lit sur la facture électronique (champ « Bon de commande ») : eTransactions ne le reporte pas sur la facture fournisseur, c'est le rapprochement à faire. Aucune facture d'achat n'est créée ni validée automatiquement (`auto_submit_purchase_invoices` est forcé à 0).

## Scénarios à essayer avec l'agent

L'agent travaille sous l'identité de l'assistant : il lit tout ce qui est utile et prépare des **brouillons** ; c'est Camille qui valide dans Dokos. Toute demande de validation, d'annulation ou de suppression est refusée par Dokos.

**« Traite les factures fournisseurs reçues »**
- L'agent liste les 5 factures fournisseurs à traiter, retrouve pour chacune la commande citée et la réception.
- Il doit relever l'**écart de prix** de Panneaux Démo Bois (42,00 € au lieu de 38,50 €, soit 52,50 € HT de plus) et la **réception incomplète** de Quincaillerie Démo Pro (vis facturées mais non reçues), et proposer de les signaler au fournisseur plutôt que de les accepter.
- Pour la Scierie, conforme, il prépare la facture d'achat en brouillon, rattachée à la commande et à la réception.
- Pour les deux prestations sans commande, il propose un article ou un compte de charge (transport, entretien) et prépare les brouillons.
- Camille vérifie les brouillons et les valide dans Dokos.

**« Prépare les relances des impayés »**
- L'agent trouve les factures échues : Hôtel Démo des Tilleuls (3 300,00 €, échue depuis plus d'un mois), Mairie de Démoville (6 288,00 €) et le reste dû par le Cabinet Démo Dentaire (1 048,00 €, après un acompte).
- Il rédige pour chacune une relance adaptée (ton, montant, références des factures, coordonnées bancaires) en brouillon de courriel ; il peut aussi préparer une relance (« Dunning ») en brouillon dans Dokos. Rien n'est envoyé sans l'accord de Camille.
- La facture 7 du Restaurant n'est pas échue et a déjà été payée selon le relevé : l'agent ne doit pas la relancer.

**« Propose le rapprochement bancaire »**
- L'agent compare les 7 opérations du relevé aux paiements et factures : quatre opérations correspondent à des paiements déjà saisis, une à la facture 7 dont le paiement manque, une à la facture reçue de Transports Démo Express, et une aux frais bancaires.
- Il propose le rapprochement et prépare en brouillon le paiement manquant (facture 7) et l'écriture des frais bancaires (12,50 €). Le rapprochement lui-même et la validation des paiements restent des gestes de Camille.

## Recharger la démo

```bash
bin/dokopilot demo-reset demo
```

Le site de démonstration est **supprimé** (base et fichiers), puis recréé et rechargé avec des dates relatives au jour de la commande. Les assistants gardent leur jeton d'API : le harnais du poste continue de fonctionner. La commande est refusée avec le profil `production`, et refusée aussi sur un site qui n'a pas été chargé avec les données de test.

## Essais d'import manuels

- **Relevé bancaire** : `demo/bank/releve-demo.csv` (séparateur virgule, dates `AAAA-MM-JJ`, point décimal ; colonnes `date`, `description`, `deposit`, `withdrawal`, `reference_number`, `currency`) s'importe dans Dokos par Comptabilité › Import de relevé bancaire. Ses dates sont celles du fichier (vers le 1er novembre 2026) ; importez-le sur un autre compte bancaire que « Banque Démo », qui contient déjà ces opérations.
- **Factures électroniques** : `python3 demo/einvoices/generate.py /tmp/factures` écrit deux factures d'exemple (CII et UBL) de Transports Démo Express, à déposer dans eTransactions › Corbeille de factures fournisseurs.
