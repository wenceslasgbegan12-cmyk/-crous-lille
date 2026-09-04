# -*- coding: utf-8 -*-
"""
crous_watch_lille.py — Surveillance des logements CROUS (trouverunlogement.lescrous.fr)
=========================================================================================
Zone : Lille (et métropole : Villeneuve-d'Ascq, Roubaix, Tourcoing).
Notifications envoyées via un bot Telegram dès qu'un nouveau logement apparaît.

Le site est une SPA React : on ne fait pas de scraping HTML, on appelle
directement son API interne de recherche en POST.

------------------------------------------------------------------------------
COMMENT RÉCUPÉRER / VÉRIFIER L'ID DE PHASE (SEARCH_ID) — à refaire si le
script se met à échouer en boucle (voir alerte automatique plus bas)
------------------------------------------------------------------------------
1. Ouvrir https://trouverunlogement.lescrous.fr/api/fr/tools dans un
   navigateur : cette page liste toutes les phases avec leurs dates.
   Repérer celle qui est actuellement ouverte.
2. Reporter son ID dans SEARCH_ID ci-dessous (ou dans le fichier .env).

------------------------------------------------------------------------------
COMMENT CRÉER LE BOT TELEGRAM
------------------------------------------------------------------------------
1. Sur Telegram, parler à @BotFather -> /newbot -> suivre les instructions.
   BotFather te donne un TOKEN du type "123456789:AAExxxxxxxxxxxxxxxxxxxxxxx".
2. Démarrer une conversation avec TON bot (cherche son nom, clique "Démarrer").
3. Récupérer ton CHAT_ID : ouvre dans un navigateur, après avoir envoyé un
   message à ton bot :
   https://api.telegram.org/bot<TON_TOKEN>/getUpdates
   Cherche la valeur "chat":{"id": ...} dans la réponse JSON.
4. Renseigne TELEGRAM_BOT_TOKEN et TELEGRAM_CHAT_ID dans le fichier .env
   (voir .env.example).

------------------------------------------------------------------------------
DÉPENDANCES : Python 3.11+, `pip install requests` (seule dépendance externe).
------------------------------------------------------------------------------
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

# =============================================================================
# CONFIGURATION
# Chaque valeur peut être surchargée par une variable d'environnement du même
# nom, ou par un fichier `.env` posé à côté du script (format KEY=VALUE).
# =============================================================================

# --- Chargement (optionnel) du fichier .env, sans dépendance externe --------
def _charger_dotenv() -> None:
    env_path = Path(__file__).with_name(".env")
    if not env_path.is_file():
        return
    for ligne in env_path.read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        if not ligne or ligne.startswith("#") or "=" not in ligne:
            continue
        cle, _, valeur = ligne.partition("=")
        # Les variables déjà définies dans l'environnement ont priorité.
        os.environ.setdefault(cle.strip(), valeur.strip())


_charger_dotenv()


def _env(nom: str, defaut: str) -> str:
    return os.environ.get(nom, defaut)


# --- API CROUS ---------------------------------------------------------------
# ID de la phase en cours (le nombre à la fin de /api/fr/search/XX).
# Vérifiable sur https://trouverunlogement.lescrous.fr/api/fr/tools
SEARCH_ID = int(_env("SEARCH_ID", "47"))
API_URL = f"https://trouverunlogement.lescrous.fr/api/fr/search/{SEARCH_ID}"
FICHE_URL = "https://trouverunlogement.lescrous.fr/tools/{search_id}/accommodations/{item_id}"

# Bounding box autour de Lille métropole : coin Nord-Ouest puis coin Sud-Est.
# Couvre Lille, Villeneuve-d'Ascq, Roubaix, Tourcoing. Élargir/réduire au besoin.
BBOX_LON_OUEST = float(_env("BBOX_LON_OUEST", "2.90"))
BBOX_LAT_NORD = float(_env("BBOX_LAT_NORD", "50.75"))
BBOX_LON_EST = float(_env("BBOX_LON_EST", "3.25"))
BBOX_LAT_SUD = float(_env("BBOX_LAT_SUD", "50.55"))


def construire_payload(page: int = 1) -> dict:
    return {
        "idTool": SEARCH_ID,
        "need_aggregation": True,
        "page": page,
        "pageSize": 200,
        "sector": None,
        "occupationModes": [],
        "location": [
            {"lon": BBOX_LON_OUEST, "lat": BBOX_LAT_NORD},  # coin Nord-Ouest
            {"lon": BBOX_LON_EST, "lat": BBOX_LAT_SUD},     # coin Sud-Est
        ],
        "residence": None,
        "precision": 7,
        "equipment": [],
        "price": {"min": 0, "max": 10000000},  # en centimes, très large
        "toolMechanism": "residual",
    }


# En-têtes proches d'un navigateur (le site n'exige pas de cookie/token).
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/126.0.0.0 Safari/537.36",
    "Origin": "https://trouverunlogement.lescrous.fr",
    "Referer": "https://trouverunlogement.lescrous.fr/",
}

# --- Notifications Telegram ---------------------------------------------------
TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN", "CHANGE-MOI")
TELEGRAM_CHAT_ID = _env("TELEGRAM_CHAT_ID", "CHANGE-MOI")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

# --- Comportement ------------------------------------------------------------
INTERVALLE_SECONDES = int(_env("INTERVALLE_SECONDES", "300"))  # 5 min par défaut
FICHIER_ETAT = Path(__file__).with_name(_env("FICHIER_ETAT", "logements_vus.json"))
TIMEOUT_HTTP = 30  # secondes, pour chaque appel réseau
SEUIL_ERREURS_4XX = 3  # nb d'erreurs 4xx consécutives avant l'alerte
# "ID de phase probablement périmé"

# RUN_ONCE=1 : effectue UN SEUL cycle puis se termine, au lieu de boucler.
# C'est le mode utilisé par GitHub Actions (le planificateur cron relance le
# script à intervalle régulier) ; le mode boucle reste le défaut en local.
RUN_ONCE = _env("RUN_ONCE", "0") == "1"


# =============================================================================
# UTILITAIRES
# =============================================================================

def log(message: str) -> None:
    """Affiche un message horodaté sur la console."""
    horodatage = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{horodatage}] {message}", flush=True)


def charger_etat() -> dict | None:
    """Charge le fichier d'état. Renvoie None si premier lancement."""
    try:
        if FICHIER_ETAT.is_file():
            return json.loads(FICHIER_ETAT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log(f"ATTENTION : fichier d'état illisible ({e}), il sera réinitialisé.")
    return None


def sauvegarder_etat(initialise: bool, ids_vus: set[int],
                      erreurs_4xx: int = 0, alerte_4xx_envoyee: bool = False) -> None:
    """
    Persiste l'état (écriture atomique via fichier temporaire).
    Le compteur d'erreurs 4xx est persisté lui aussi, pour que l'alerte
    "ID périmé" fonctionne même en mode RUN_ONCE (un processus par cycle).
    """
    try:
        contenu = json.dumps(
            {
                "search_id": SEARCH_ID,
                "initialise": initialise,
                "ids": sorted(ids_vus),
                "erreurs_4xx": erreurs_4xx,
                "alerte_4xx_envoyee": alerte_4xx_envoyee,
            },
            ensure_ascii=False, indent=2,
        )
        tmp = FICHIER_ETAT.with_suffix(".tmp")
        tmp.write_text(contenu, encoding="utf-8")
        tmp.replace(FICHIER_ETAT)
    except OSError as e:
        log(f"ERREUR : impossible d'écrire le fichier d'état : {e}")


def notifier(titre: str, message: str, lien: str | None = None) -> None:
    """
    Envoie une notification via le bot Telegram (sendMessage).
    Ne lève jamais d'exception : une erreur d'envoi est seulement journalisée.
    """
    texte = f"*{titre}*\n{message}"
    if lien:
        texte += f"\n{lien}"

    corps = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": texte,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
    }
    try:
        r = requests.post(TELEGRAM_API_URL, json=corps, timeout=TIMEOUT_HTTP)
        if r.status_code >= 400:
            log(f"ERREUR Telegram : HTTP {r.status_code} — {r.text[:200]}")
    except requests.RequestException as e:
        log(f"ERREUR Telegram : {e}")


# =============================================================================
# APPEL DE L'API ET EXTRACTION DES DONNÉES
# =============================================================================

class ErreurHttp4xx(Exception):
    """Levée quand l'API répond 4xx (ID de phase probablement périmé)."""


def recuperer_logements() -> list[dict]:
    """
    Interroge l'API (avec pagination) et renvoie la liste brute des items.
    Lève ErreurHttp4xx sur un statut 4xx, requests.RequestException sur un
    problème réseau, ValueError si la structure JSON est inattendue.
    """
    items: list[dict] = []
    page = 1
    while True:
        r = requests.post(API_URL, json=construire_payload(page),
                           headers=HEADERS, timeout=TIMEOUT_HTTP)
        if 400 <= r.status_code < 500:
            raise ErreurHttp4xx(f"HTTP {r.status_code} sur {API_URL}")
        r.raise_for_status()
        data = r.json()

        resultats = data.get("results")
        if not isinstance(resultats, dict) or "items" not in resultats:
            raise ValueError(f"Structure JSON inattendue : clés = {list(data)}")

        page_items = resultats.get("items") or []
        items.extend(page_items)

        total = resultats.get("total", 0)
        if isinstance(total, dict):
            total = total.get("value", 0)

        if not page_items or len(items) >= int(total) or page > 20:
            return items
        page += 1


def extraire_infos(item: dict) -> tuple[int | None, str, str, str]:
    """
    Extrait (id, nom, résidence, loyer) d'un item, en restant tolérant aux
    variations de structure : une clé absente donne un champ '?' plutôt
    qu'un crash.
    """
    item_id = item.get("id")
    nom = item.get("label") or "Logement sans nom"

    residence = "?"
    res = item.get("residence")
    if isinstance(res, dict):
        residence = res.get("label") or "?"

    def _euros(centimes: float) -> str:
        v = centimes / 100
        return f"{v:.2f}".rstrip("0").rstrip(".").replace(".", ",")

    loyer = "?"
    try:
        modes = item.get("occupationModes") or []
        if modes and isinstance(modes[0], dict):
            rent = modes[0].get("rent") or {}
            rmin, rmax = rent.get("min"), rent.get("max")
            if rmin is not None:
                rmax = rmax if rmax is not None else rmin
                loyer = (f"{_euros(rmin)} €" if rmin == rmax
                         else f"{_euros(rmin)}–{_euros(rmax)} €")
        if loyer == "?" and item.get("rentRange"):
            rr = item["rentRange"]
            loyer = (f"{rr[0]:.0f} €" if rr[0] == rr[-1]
                     else f"{rr[0]:.0f}–{rr[-1]:.0f} €")
    except (TypeError, ValueError, IndexError, KeyError):
        pass  # on garde '?' : mieux vaut une notif incomplète que pas de notif

    return item_id, str(nom), str(residence), loyer


# =============================================================================
# BOUCLE PRINCIPALE
# =============================================================================

def main() -> None:
    log("=== Surveillance logements CROUS — zone Lille ===")
    log(f"API : {API_URL}")
    log(f"Mode : {'un seul cycle (RUN_ONCE)' if RUN_ONCE else f'boucle toutes les {INTERVALLE_SECONDES} s'}")
    log(f"Fichier d'état: {FICHIER_ETAT}")

    if "CHANGE-MOI" in TELEGRAM_BOT_TOKEN or "CHANGE-MOI" in TELEGRAM_CHAT_ID:
        log("ATTENTION : TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID non configurés "
            "(voir .env.example).")

    etat = charger_etat() or {}

    # Si l'ID de phase a changé depuis la dernière exécution, on repart de
    # zéro (les IDs d'items d'une ancienne phase ne sont plus comparables).
    if etat and etat.get("search_id") != SEARCH_ID:
        log("ID de phase différent de celui du fichier d'état : réinitialisation.")
        etat = {}

    initialise = bool(etat) and bool(etat.get("initialise", True))
    ids_vus: set[int] = set(etat.get("ids", []))
    erreurs_4xx_consecutives = int(etat.get("erreurs_4xx", 0))
    alerte_4xx_envoyee = bool(etat.get("alerte_4xx_envoyee", False))

    while True:
        cycle_reussi = False
        try:
            items = recuperer_logements()
            cycle_reussi = True
        except ErreurHttp4xx as e:
            erreurs_4xx_consecutives += 1
            log(f"ERREUR API (4xx) : {e} "
                f"[{erreurs_4xx_consecutives}/{SEUIL_ERREURS_4XX}]")

            if (erreurs_4xx_consecutives >= SEUIL_ERREURS_4XX
                    and not alerte_4xx_envoyee):
                notifier(
                    "CROUS : vérifie l'ID de l'endpoint",
                    f"L'API répond en erreur 4xx depuis "
                    f"{erreurs_4xx_consecutives} cycles ({e}).\n"
                    "L'ID de phase (SEARCH_ID) est probablement périmé : "
                    "consulte https://trouverunlogement.lescrous.fr/api/fr/tools "
                    "pour trouver le nouveau.",
                )
                alerte_4xx_envoyee = True

            sauvegarder_etat(initialise, ids_vus,
                              erreurs_4xx_consecutives, alerte_4xx_envoyee)
        except requests.RequestException as e:
            log(f"ERREUR réseau : {e} — nouvelle tentative au prochain cycle.")
        except (ValueError, KeyError, TypeError) as e:
            log(f"ERREUR de structure JSON : {e} — l'API a peut-être changé. "
                "Nouvelle tentative au prochain cycle.")
        except Exception as e:  # filet de sécurité : ne jamais crasher
            log(f"ERREUR inattendue : {type(e).__name__}: {e}")

        if cycle_reussi:
            if alerte_4xx_envoyee:
                log("L'API répond de nouveau normalement.")
            erreurs_4xx_consecutives = 0
            alerte_4xx_envoyee = False

            ids_actuels = {i.get("id") for i in items if i.get("id") is not None}

            if not initialise:
                # Premier lancement : on enregistre l'existant SANS notifier
                # chaque logement, juste une confirmation que tout fonctionne.
                ids_vus = ids_actuels
                initialise = True
                log(f"Premier lancement : {len(ids_vus)} logement(s) déjà en "
                    "ligne, enregistrés sans notification.")
                notifier(
                    "Surveillance CROUS active",
                    f"Surveillance de la zone Lille démarrée : "
                    f"{len(ids_vus)} logement(s) actuellement en ligne.",
                    lien="https://trouverunlogement.lescrous.fr/",
                )
            else:
                nouveaux = ids_actuels - ids_vus
                if nouveaux:
                    log(f"{len(nouveaux)} NOUVEAU(X) logement(s) détecté(s) !")
                    for item in items:
                        item_id, nom, residence, loyer = extraire_infos(item)
                        if item_id not in nouveaux:
                            continue
                        lien = FICHE_URL.format(search_id=SEARCH_ID, item_id=item_id)
                        log(f"  -> {nom} | {residence} | {loyer} | {lien}")
                        notifier(
                            f"Nouveau logement CROUS : {nom}",
                            f"Résidence : {residence}\nLoyer : {loyer}\n"
                            "Fonce, les places partent vite !",
                            lien=lien,
                        )
                    ids_vus |= nouveaux
                else:
                    log(f"Aucun nouveau logement ({len(ids_actuels)} en ligne).")

            sauvegarder_etat(initialise, ids_vus)

        if RUN_ONCE:
            log("Cycle terminé (mode RUN_ONCE), arrêt.")
            return

        time.sleep(INTERVALLE_SECONDES)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Arrêt demandé (Ctrl+C). À bientôt !")
        sys.exit(0)
