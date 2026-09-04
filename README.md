# Surveillance logements CROUS — Lille (alertes Telegram)

Surveille [trouverunlogement.lescrous.fr](https://trouverunlogement.lescrous.fr) et
envoie une alerte **Telegram** dès qu'un nouveau logement apparaît sur la zone
de Lille (Lille, Villeneuve-d'Ascq, Roubaix, Tourcoing).

## 1. Créer le bot Telegram (2 minutes)

1. Ouvre Telegram, cherche **@BotFather**, envoie `/newbot` et suis les
   instructions (nom du bot, puis un identifiant se terminant par `bot`).
2. BotFather te donne un **token** du type
   `123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`. Garde-le secret.
3. Cherche **ton bot** dans Telegram (par son identifiant) et clique
   **Démarrer** (ou envoie-lui n'importe quel message).
4. Récupère ton **chat_id** : ouvre dans un navigateur
   `https://api.telegram.org/bot<TON_TOKEN>/getUpdates`
   (remplace `<TON_TOKEN>` par le token de l'étape 2), et cherche
   `"chat":{"id": ...}` dans la réponse.

## 2. Installer

```bash
pip install requests
cp .env.example .env
```

Puis édite `.env` et renseigne `TELEGRAM_BOT_TOKEN` et `TELEGRAM_CHAT_ID`.

## 3. Lancer

```bash
python crous_watch_lille.py
```

Le script tourne en boucle (par défaut toutes les 5 minutes). Le premier
lancement enregistre les logements déjà en ligne **sans** notifier (juste
une confirmation), pour éviter une avalanche de messages au démarrage.
Ensuite, seule une **nouvelle** annonce déclenche une alerte.

Pour l'arrêter : `Ctrl+C`.

## 4. Faire tourner en continu sans laisser un PC allumé (optionnel)

Le script fonctionne aussi en mode "un seul passage" (`RUN_ONCE=1` dans
`.env`), pour être appelé périodiquement par un planificateur externe
(cron, tâche planifiée Windows, GitHub Actions...). Dans ce cas, le fichier
`logements_vus.json` doit être conservé entre deux exécutions (c'est lui qui
retient les logements déjà vus).

## Points d'attention

- **Zone couverte** : la zone Lille est définie par une bounding box
  (`BBOX_LON_OUEST`, `BBOX_LAT_NORD`, `BBOX_LON_EST`, `BBOX_LAT_SUD` dans
  `.env` ou dans le script). Tu peux l'ajuster si besoin.
- **Changement de phase** : l'ID de la phase en cours (`SEARCH_ID`) change à
  chaque tour d'attribution. La liste à jour est publique sur
  <https://trouverunlogement.lescrous.fr/api/fr/tools>. Si l'API se met à
  répondre en erreur, le script envoie automatiquement une alerte Telegram
  "vérifie l'ID de l'endpoint".
- **Ne pas lancer deux instances** avec le même `.env`/même dossier en même
  temps, sinon notifications en double.
