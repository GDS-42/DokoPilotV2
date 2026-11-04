# Installer chez un client (production)

Ce guide décrit une mise en production complète, de la commande du serveur à la remise des accès. Il suppose que vous avez déjà essayé la démo sur une VM ([vm-locale.md](vm-locale.md)).

## 1. Ce que l'entrepreneur prépare, à son nom

| Élément | Recommandation |
| --- | --- |
| Serveur | VPS Debian 13 **amd64**, 4 vCPU, 8 Go de RAM (4 Go au minimum, une partition d'échange est alors créée), 80 Go de disque, hébergé en France ou dans l'Union européenne, contrat et facturation au nom de l'entreprise |
| Réseau privé | Un réseau (« tailnet ») Tailscale dont l'entrepreneur est propriétaire ; l'intégrateur y est invité. Vérifiez sur tailscale.com/pricing que le plan choisi convient à un usage professionnel |
| Sauvegardes | Un compartiment S3 (ou un serveur SFTP ou REST restic) chez un autre fournisseur que le serveur, au nom de l'entreprise, avec une clé d'accès limitée à ce compartiment |
| Facturation électronique | Le contrat Super PDP et ses identifiants (eTransactions) |
| Modèle d'IA | Un compte chez le fournisseur de modèle choisi, avec un plafond de dépenses, pour le harnais du poste |

Ce découpage garde l'entrepreneur maître de ses données et libre de changer d'intégrateur.

## 2. Régler Tailscale

Dans la console d'administration du tailnet :

1. **DNS** : activez MagicDNS puis « Enable HTTPS ». Les noms de machines apparaissent dans les journaux publics de certificats : choisissez un nom neutre (`network.tailscale.hostname`, par défaut `dokos-<client>`).
2. **Politique d'accès** : déclarez une étiquette pour le serveur et n'ouvrez que le port 443, par exemple :

   ```jsonc
   {
     "tagOwners": { "tag:dokopilot": ["autogroup:admin"] },
     "grants": [
       { "src": ["autogroup:member"], "dst": ["tag:dokopilot"], "ip": ["tcp:443"] }
     ],
     "tests": [
       { "src": "dirigeant@exemple.fr", "accept": ["tag:dokopilot:443"], "deny": ["tag:dokopilot:22"] }
     ]
   }
   ```

   Retirez l'attribut `funnel` des `nodeAttrs` s'il est présent : Funnel publierait un service sur Internet.
3. **Appareils** : exigez l'approbation manuelle des nouveaux appareils.
4. **Clé d'authentification** : créez une clé à usage unique, pré-approuvée, avec l'étiquette `tag:dokopilot`, valable quelques jours. Elle ne sert qu'au premier passage.

## 3. Préparer la configuration sur votre poste

```bash
bin/dokopilot init acme --profile production \
  --recipient age1…entrepreneur… --recipient age1…secours…
```

- `--recipient` ajoute la clé age publique de l'entrepreneur (`age-keygen` sur son poste) et une clé de secours imprimée et rangée en lieu sûr : chacune peut déchiffrer les secrets.
- Complétez `clients/acme/client.yml` en suivant les commentaires de [`config/client.example.yml`](../config/client.example.yml) : raison sociale, SIREN, adresse du serveur, comptes de maintenance et leurs clés, personnes et assistants, `network.tailscale.advertise_tags: ["tag:dokopilot"]`, `backup.repository`.
- Complétez les secrets : `bin/dokopilot secrets acme edit` (clé Tailscale, `restic_env` avec les clés S3, identifiants Super PDP).
- Vérifiez : `bin/dokopilot config acme`. La validation refuse une configuration de production sans Tailscale, avec des données de test, ou avec une sauvegarde locale.
- Rangez `clients/acme/` dans un **dépôt Git privé** propre au client (il contient des données personnelles) ; `DOKOPILOT_CLIENTS_DIR` indique où il se trouve.

## 4. Installer

```bash
bin/dokopilot bootstrap acme --user root     # compte fourni par l'hébergeur (root, debian…)
bin/dokopilot install acme                   # avec votre compte de maintenance
bin/dokopilot check acme
```

Le premier passage crée les comptes de maintenance et ferme SSH à tout autre compte : gardez ouverte la console de secours de l'hébergeur jusqu'à ce que `bin/dokopilot ssh acme` fonctionne. L'installation complète prend de 20 à 40 minutes ; elle se relance sans risque.

À la fin, Dokos est joignable à `https://dokos-acme.<tailnet>.ts.net` depuis les appareils du tailnet, et nulle part ailleurs.

## 5. Remettre les accès

- L'entrepreneur se connecte avec son adresse et le mot de passe initial (`bin/dokopilot secrets acme show entrepreneur_password`), puis le change.
- Installez Tailscale et le harnais sur son poste : [poste-entrepreneur.md](poste-entrepreneur.md).
- Testez une restauration : `bin/dokopilot backup acme`, puis vérifiez la présence de l'instantané (`sudo restic snapshots` sur le serveur, avec `/etc/dokopilot/restic.env`).
- Consignez le traitement : l'entrepreneur est responsable de traitement ; l'intégrateur, sous-traitant au sens de l'article 28 du RGPD, signe un contrat de sous-traitance qui décrit ses accès de maintenance.

## 6. Ensuite

Mises à jour, supervision et restauration : [exploitation.md](exploitation.md). Pour retirer un intervenant, supprimez son compte de `maintenance.users` et relancez `bin/dokopilot install acme` : son compte est verrouillé et ses clés retirées.
