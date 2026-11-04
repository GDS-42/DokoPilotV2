# Configurations des clients

Chaque client a son dossier `clients/<client>/` :

| Fichier | Contenu | Dans Git ? |
| --- | --- | --- |
| `client.yml` | Tous les réglages, sans secret (modèle commenté : `config/client.example.yml`) | Non, sauf `demo` |
| `client.local.yml` | Réglages propres à votre poste (adresse de la VM, compte de maintenance) | Non |
| `secrets.yml.age` | Mots de passe et jetons, chiffrés avec age | Non |
| `recipients.txt` | Clés publiques age autorisées à déchiffrer les secrets | Non |

Les configurations réelles contiennent des données personnelles (noms, adresses, adresses IP) :
gardez-les dans un dépôt privé par client, jamais dans ce dépôt public. Pour utiliser un autre
dossier, définissez `DOKOPILOT_CLIENTS_DIR`.

Seul le client `demo` est versionné ici, pour tester l'installation sur une VM locale.
