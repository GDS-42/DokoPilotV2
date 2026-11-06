#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""DokoPilot installation assistant (docs/plan-v0.2.md, C-5).

Asks its questions in French, in six steps, writes clients/<id>/client.yml and
client.local.yml, keeps the model API key on this computer only
(~/.config/dokopilot/<client>/model-api-key, 0600), then drives bin/dokopilot: init,
config, bootstrap (remote server), install, harness and check. It never runs Ansible
itself and never stores a sudo password: Ansible asks for it (-K).

Resumable: clients/<id>/.assistant-etat.json lists the completed steps and the answers,
never a secret. --answers FILE answers every question (installer/answers.example.yml);
secrets are then read from the environment variables or files the answers name.

Messages are in French (they are read by the person installing); code is in English.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import getpass
import io
import ipaddress
import json
import os
import platform
import pwd
import re
import shutil
import socket
import subprocess
import sys
import unicodedata
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
VENV = REPO / ".venv"

try:
    import yaml
except ImportError:  # pragma: no cover - depends on the computer
    if (VENV / "bin" / "python").exists() and Path(sys.prefix) != VENV:
        os.execv(str(VENV / "bin" / "python"), [str(VENV / "bin" / "python"), __file__, *sys.argv[1:]])
    sys.exit("PyYAML est introuvable : lancez d'abord « make setup » à la racine de DokoPilot.")

CLI = REPO / "bin" / "dokopilot"
SECRET_EDITOR = REPO / "installer" / "secret-editor.sh"
STATE_NAME = ".assistant-etat.json"
STATE_FORMAT = 1
# Same place as the harness role (harness_config_root): ~/.config/dokopilot/<client>/.
CONFIG_ROOT = Path.home() / ".config" / "dokopilot"

# Same rules as the config role (ansible/roles/config).
CLIENT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,30}$")
PERSON_ID = re.compile(r"^[a-z][a-z0-9-]{1,30}$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s.]+$")
UNIX_USER = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
NOT_NOMINATIVE = {"root", "admin", "debian", "ubuntu", "frappe"}
PROVIDER_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
MODEL = re.compile(r"^[a-z0-9][a-z0-9._-]*/[^\s/]\S*$")
SSH_KEY_TYPES = (
    "ssh-ed25519",
    "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521",
    "sk-ssh-ed25519@openssh.com",
    "sk-ecdsa-sha2-nistp256@openssh.com",
    "ssh-rsa",
)
SSH_KEY = re.compile(r"^(" + "|".join(re.escape(t) for t in SSH_KEY_TYPES) + r") [A-Za-z0-9+/=]{40,}( \S.*)?$")
HOSTNAME = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$")

# "Everything on this machine": same thresholds and sites as install.sh.
LOCAL_MIN_RAM_MB = int(os.environ.get("DOKOPILOT_LOCAL_MIN_RAM_MB", "7680"))
LOCAL_MIN_DISK_GB = int(os.environ.get("DOKOPILOT_LOCAL_MIN_DISK_GB", "30"))
SERVER_SITES = (
    "deb.debian.org",
    "security.debian.org",
    "download.docker.com",
    "registry.gitlab.com",
    "gitlab.com",
    "registry-1.docker.io",
    "codeberg.org",
    "registry.npmjs.org",
)

STEPS = [
    ("lieu", "Où installer"),
    ("profil", "Démo ou production, agent"),
    ("modeles", "Modèle d'IA"),
    ("entreprise", "Entreprise et personnes"),
    ("reseau", "Réseau, messagerie, agenda, import"),
]
ACTIONS = {
    "fichiers": "écriture de client.yml et client.local.yml",
    "init": "bin/dokopilot init (clé age et secrets chiffrés)",
    "secrets": "rangement des clés dans les secrets chiffrés",
    "config": "bin/dokopilot config (validation)",
    "bootstrap": "bin/dokopilot bootstrap (premier passage sur le serveur)",
    "install": "bin/dokopilot install (serveur complet)",
    "harness": "bin/dokopilot harness (agent sur ce poste)",
    "check": "bin/dokopilot check (contrôles)",
}
STOP_AFTER = ("config", "install", "harness")


# --------------------------------------------------------------------------- models


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    family: str
    needs_key: bool = True
    # Default models, given only where the provider's documentation lists the identifiers.
    defaults: dict[str, str] = field(default_factory=dict)
    models_page: str = ""


DIRECT = "API directe du fabricant"
GATEWAY = "Passerelle : une clé pour les modèles de plusieurs fabricants"
EU_CLOUD = "Nuage européen"
OPEN = "Modèle ouvert, sur une machine à vous"
PROVIDERS = (
    # Model IDs from https://platform.claude.com/docs/en/about-claude/models/overview
    # (read on 2026-11-06): Claude Opus 5.5, Sonnet 5.5, Haiku 5.5.
    Provider(
        "anthropic",
        "Anthropic (Claude)",
        DIRECT,
        defaults={"bureau": "claude-sonnet-5-5", "atelier": "claude-opus-5-5", "small": "claude-haiku-5-5"},
        models_page="https://platform.claude.com/docs/en/about-claude/models/overview",
    ),
    # Model ID from https://docs.mistral.ai/models/mistral-large-4-0 (read on 2026-11-06).
    # No small model is proposed: none of its identifiers could be checked.
    Provider(
        "mistral",
        "Mistral AI",
        DIRECT,
        defaults={"bureau": "mistral-large-4", "atelier": "mistral-large-4"},
        models_page="https://docs.mistral.ai/getting-started/models/",
    ),
    Provider("openai", "OpenAI", DIRECT, models_page="https://platform.openai.com/docs/models"),
    Provider("openrouter", "OpenRouter", GATEWAY, models_page="https://openrouter.ai/models"),
    Provider("vercel", "Vercel AI Gateway", GATEWAY, models_page="https://vercel.com/ai-gateway/models"),
    Provider(
        "scaleway",
        "Scaleway Generative APIs (hébergé en France)",
        EU_CLOUD,
        models_page="https://www.scaleway.com/en/docs/generative-apis/reference-content/supported-models/",
    ),
    Provider("ollama", "Ollama", OPEN, needs_key=False, models_page="https://ollama.com/library"),
)
ROLES = {
    "bureau": "Modèle Bureau (données de l'entreprise : choisissez-le pour la confidentialité)",
    "atelier": "Modèle Atelier (travail technique : choisissez-le pour la performance)",
    "small": "Petit modèle (titres et résumés des sessions)",
}


# --------------------------------------------------------------------------- input


class Invalid(Exception):
    """An answer that is refused; the message, in French, says why."""


class Stop(Exception):
    """Ends the assistant with a French message."""

    def __init__(self, message: str, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


MISSING = object()


@dataclass(frozen=True)
class Option:
    value: str
    label: str
    aliases: tuple[str, ...] = ()
    group: str = ""


def fold(text: str) -> str:
    """Lower case without accents, for comparing typed choices."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().strip().lower()


def as_text(value: Any) -> str:
    if isinstance(value, bool):
        return "oui" if value else "non"
    if value is None:
        return ""
    return str(value)


def parse_bool(text: str) -> bool:
    folded = fold(text)
    if folded in {"o", "oui", "y", "yes", "true", "vrai", "1"}:
        return True
    if folded in {"n", "non", "no", "false", "faux", "0"}:
        return False
    raise Invalid("répondez oui ou non.")


class Ui:
    """Questions on the terminal, or answers read from a file (--answers)."""

    def __init__(self, answers: dict[str, Any] | None, source: str, assume_yes: bool) -> None:
        self.answers = answers
        self.source = source
        self.assume_yes = assume_yes or answers is not None

    @property
    def scripted(self) -> bool:
        return self.answers is not None

    def lookup(self, key: str) -> Any:
        node: Any = self.answers
        for part in key.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
                node = node[int(part)]
            else:
                return MISSING
        return node

    def ask(
        self,
        key: str,
        question: str,
        *,
        default: str | None = None,
        parse: Callable[[str], Any] | None = None,
        hint: str | None = None,
    ) -> Any:
        """Ask one question. default "" means optional (Enter gives an empty answer)."""
        parse = parse or (lambda text: text)
        if self.scripted:
            raw = self.lookup(key)
            if raw is MISSING or raw is None:
                if default is None:
                    raise Stop(f"Réponse manquante dans {self.source} : « {key} » ({question}).", 2)
                text = default
            else:
                text = as_text(raw)
            try:
                value = parse(text.strip())
            except Invalid as error:
                raise Stop(f"Réponse « {key} » refusée dans {self.source} : {error}", 2) from None
            print(f"  {question} : {as_text(value) if not isinstance(value, list) else ', '.join(map(str, value))}")
            return value
        shown = hint if hint is not None else default
        suffix = f" [{shown}]" if shown else ""
        while True:
            try:
                text = input(f"  {question}{suffix} : ").strip()
            except EOFError:
                raise Stop("Saisie interrompue : relancez l'assistant, il reprendra où vous en étiez.", 130) from None
            if not text and default is not None:
                text = default
            try:
                return parse(text)
            except Invalid as error:
                print(f"    ✗ {error}")

    def choice(self, key: str, question: str, options: list[Option], default: str) -> str:
        def parse(text: str) -> str:
            folded = fold(text)
            if folded.isdigit() and 1 <= int(folded) <= len(options):
                return options[int(folded) - 1].value
            for option in options:
                if folded == fold(option.value) or folded in {fold(alias) for alias in option.aliases}:
                    return option.value
            raise Invalid(f"choisissez un numéro de 1 à {len(options)}, ou l'un de : {', '.join(o.value for o in options)}.")

        if self.scripted:
            return self.ask(key, question, default=default, parse=parse)
        print(f"  {question}")
        group = ""
        for number, option in enumerate(options, 1):
            if option.group and option.group != group:
                group = option.group
                print(f"    {group}")
            indent = "      " if option.group else "    "
            print(f"{indent}{number}. {option.label}  [{option.value}]")
        index = next(n for n, option in enumerate(options, 1) if option.value == default)
        return self.ask(key, "Votre choix", default=str(index), parse=parse)

    def yes_no(self, key: str, question: str, default: bool) -> bool:
        return bool(self.ask(key, question, default="oui" if default else "non", parse=parse_bool, hint="O/n" if default else "o/N"))

    def secret(self, key: str, question: str, *, required: bool, check: Callable[[str], None] | None = None) -> str:
        """A secret: masked typing, or <key>_env / <key>_file in the answers. Never echoed."""
        if self.scripted:
            env_name, file_name = self.lookup(f"{key}_env"), self.lookup(f"{key}_file")
            if self.lookup(key) is not MISSING:
                raise Stop(
                    f"« {key} » dans {self.source} : un secret ne s'écrit pas dans le fichier de réponses ; "
                    f"donnez plutôt {key}_env (nom d'une variable d'environnement) ou {key}_file (chemin d'un fichier).",
                    2,
                )
            if env_name is not MISSING:
                value = os.environ.get(str(env_name), "").strip()
                origin = f"variable {env_name}"
                if not value:
                    raise Stop(f"La variable d'environnement {env_name} (réponse « {key}_env ») est vide ou absente.", 2)
            elif file_name is not MISSING:
                path = Path(str(file_name)).expanduser()
                try:
                    value = path.read_text(encoding="utf-8").strip()
                except OSError as error:
                    raise Stop(f"Fichier illisible pour « {key}_file » : {path} ({error.strerror}).", 2) from None
                origin = f"fichier {path}"
                if not value:
                    raise Stop(f"Le fichier {path} (réponse « {key}_file ») est vide.", 2)
            else:
                if required:
                    raise Stop(f"Secret manquant dans {self.source} : « {key}_env » ou « {key}_file » ({question}).", 2)
                return ""
            if check:
                try:
                    check(value)
                except Invalid as error:
                    raise Stop(f"Secret « {key} » refusé ({origin}) : {error}", 2) from None
            print(f"  {question} : lue depuis la {origin}")
            return value
        while True:
            try:
                value = getpass.getpass(f"  {question} (saisie masquée) : ").strip()
            except EOFError:
                raise Stop("Saisie interrompue : relancez l'assistant, il reprendra où vous en étiez.", 130) from None
            if not value:
                if not required:
                    return ""
                print("    ✗ une valeur est nécessaire.")
                continue
            if check:
                try:
                    check(value)
                except Invalid as error:
                    print(f"    ✗ {error}")
                    continue
            return value

    def confirm(self, question: str, default: bool = True) -> bool:
        # --yes (and --answers) accept the confirmations that are yes by default; a question
        # whose default is no (replacing an existing installation) keeps its default.
        if self.assume_yes:
            print(f"  {question} {'oui' if default else 'non'} (automatique)")
            return default
        return parse_bool(self.ask("", question, default="oui" if default else "non", hint="O/n" if default else "o/N"))


# --------------------------------------------------------------------------- validation


def check_client_id(text: str) -> str:
    if not CLIENT_ID.match(text):
        raise Invalid("minuscules, chiffres et tirets, de 2 à 31 caractères, sans tiret au début (ex. atelier-martin).")
    if text == "demo" or tracked(clients_dir() / text / "client.yml"):
        raise Invalid(f"« {text} » est l'exemple versionné du dépôt (clients/{text}/client.yml) : choisissez un autre identifiant, par exemple atelier-demo.")
    return text


# Ids that name an account or a route of Forgejo (as dokopilot_reserved_person_ids in the
# config role, which checks them again).
RESERVED_PERSON_IDS = frozenset(
    "admin api assets attachments avatar avatars captcha commits debug dokopilot-admin error explore forgejo-actions "
    "ghost gitea-actions issues login metrics milestones new notifications org pulls raw repo repo-avatars search user v2".split()
)


def check_person_id(text: str) -> str:
    if not PERSON_ID.match(text):
        raise Invalid("une minuscule, puis minuscules, chiffres ou tirets (2 à 31 caractères).")
    if text in RESERVED_PERSON_IDS or text.startswith("assistant-"):
        raise Invalid(f"« {text} » désigne déjà un compte ou une page de Forgejo : choisissez un autre identifiant (le prénom, par exemple).")
    return text


def check_email(text: str) -> str:
    if not EMAIL.match(text):
        raise Invalid("adresse de courriel invalide (ex. camille.martin@exemple.fr).")
    return text.lower()


def check_name(text: str) -> str:
    if not text or len(text) > 100 or any(ord(char) < 32 for char in text):
        raise Invalid("indiquez un nom (100 caractères au plus).")
    return text


def check_optional_name(text: str) -> str:
    return check_name(text) if text else ""


def check_abbr(text: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9]{2,5}", text):
        raise Invalid("de 2 à 5 lettres ou chiffres, sans espace (ex. AD).")
    return text.upper()


def luhn(digits: str) -> bool:
    total = 0
    for position, char in enumerate(reversed(digits)):
        value = int(char) * (2 if position % 2 else 1)
        total += value - 9 if value > 9 else value
    return total % 10 == 0


def check_siren(text: str) -> str:
    digits = re.sub(r"\s", "", text)
    if not re.fullmatch(r"[0-9]{9}", digits):
        raise Invalid("le SIREN compte 9 chiffres.")
    # La Poste's SIREN is the only one without a valid check digit.
    if digits != "356000000" and not luhn(digits):
        raise Invalid(f"le SIREN {digits} n'est pas valide (clé de contrôle) : vérifiez-le sur un extrait Kbis.")
    return digits


def check_host(text: str) -> str:
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        pass
    if not HOSTNAME.match(text):
        raise Invalid("indiquez une adresse IP ou un nom de machine (ex. 192.168.1.20 ou serveur.exemple.fr).")
    return text.lower()


def check_port(text: str) -> int:
    if not text.isdigit() or not 1 <= int(text) <= 65535:
        raise Invalid("un numéro de port, de 1 à 65535.")
    return int(text)


def check_login(text: str) -> str:
    if not UNIX_USER.match(text):
        raise Invalid("un nom de compte Linux : minuscules, chiffres, tirets ou soulignés (32 caractères au plus).")
    return text


def check_maintenance_user(text: str) -> str:
    check_login(text)
    if text in NOT_NOMINATIVE:
        raise Invalid(f"« {text} » n'est pas un compte nominatif : prenez par exemple votre prénom.")
    return text


def check_provider_id(text: str) -> str:
    if not PROVIDER_ID.match(text):
        raise Invalid("un identifiant de fournisseur opencode, en minuscules (ex. deepseek).")
    return text


def model_parser(provider: str) -> Callable[[str], str]:
    def parse(text: str) -> str:
        if not text:
            return ""
        value = text if text.startswith(f"{provider}/") else f"{provider}/{text}"
        if not MODEL.match(value):
            raise Invalid(f"un identifiant de modèle, sans espace (ex. {provider}/<modèle>).")
        return value

    return parse


def check_api_key(text: str) -> None:
    if len(text) < 8 or any(char.isspace() for char in text):
        raise Invalid("une clé d'API compte au moins 8 caractères, sans espace.")


def check_tailscale_key(text: str) -> None:
    if not text.startswith("tskey-") or any(char.isspace() for char in text):
        raise Invalid("une clé d'authentification Tailscale commence par « tskey- ».")


def check_not_empty(text: str) -> None:
    if not text:
        raise Invalid("une valeur est nécessaire.")


def check_https_url(text: str) -> str:
    if not re.fullmatch(r"https://[^\s/]+(/\S*)?", text):
        raise Invalid("une adresse en https:// (ex. https://agenda.exemple.fr/dav/).")
    return text


def check_import_dir(text: str) -> str:
    if not text:
        return ""
    path = Path(text).expanduser()
    if not path.is_dir():
        raise Invalid(f"le dossier {path} n'existe pas sur ce poste.")
    return str(path.resolve())


def check_backup_repository(text: str) -> str:
    if not re.match(r"^[a-z0-9]+:", text) or re.search(r"\.(invalid|example)(/|:|$)", text):
        raise Invalid("un dépôt restic distant, ex. s3:https://s3.fr-par.scw.cloud/mon-compartiment/dokopilot, sftp:… ou rest:….")
    return text


def ssh_key_from_text(text: str) -> str:
    """A public key line without its authorized_keys options, or '' when there is none."""
    parts = text.split()
    for index, part in enumerate(parts):
        if part in SSH_KEY_TYPES and index + 1 < len(parts):
            key = " ".join(parts[index:])
            if SSH_KEY.match(key):
                return key
    return ""


def check_key_source(text: str) -> str:
    """A public key, typed or read from a .pub file; '' when nothing is given."""
    if not text:
        return ""
    candidate = Path(text).expanduser()
    if candidate.is_file():
        key = ssh_key_from_text(candidate.read_text(encoding="utf-8", errors="replace").strip())
        if not key:
            raise Invalid(f"{candidate} ne contient pas de clé SSH publique.")
        return key
    key = ssh_key_from_text(text)
    if not key or key.split()[0] != text.split()[0]:
        raise Invalid("collez une clé publique complète (« ssh-ed25519 AAAA… ») ou le chemin d'un fichier .pub.")
    return key


def check_pub_path(text: str) -> str:
    if not text.endswith(".pub"):
        raise Invalid("le chemin d'une clé publique, qui finit par .pub (ex. ~/.ssh/id_ed25519.pub).")
    return text


# --------------------------------------------------------------------------- helpers


def clients_dir() -> Path:
    return Path(os.environ.get("DOKOPILOT_CLIENTS_DIR", REPO / "clients")).expanduser()


def tracked(path: Path) -> bool:
    try:
        relative = path.resolve().relative_to(REPO)
    except ValueError:
        return False
    result = subprocess.run(["git", "-C", str(REPO), "ls-files", "--error-unmatch", str(relative)], capture_output=True, check=False)
    return result.returncode == 0


def slug(*candidates: str) -> str:
    for candidate in candidates:
        value = re.sub(r"[^a-z0-9]+", "-", fold(candidate or "")).strip("-")[:31].strip("-")
        if PERSON_ID.match(value):
            return value
    return ""


SMALL_WORDS = {"de", "du", "des", "la", "le", "les", "l", "d", "et", "a", "au", "aux", "en", "sur", "pour"}


def abbreviation(name: str) -> str:
    words = re.findall(r"[a-z0-9]+", fold(name))
    initials = "".join(word[0] for word in words if word not in SMALL_WORDS).upper()[:5]
    if len(initials) < 2:
        initials = "".join(words).upper()[:3]
    return initials if re.fullmatch(r"[A-Z0-9]{2,5}", initials) else ""


def current_user() -> pwd.struct_passwd:
    return pwd.getpwuid(os.getuid())


def full_name_of(entry: pwd.struct_passwd) -> str:
    return entry.pw_gecos.split(",")[0].strip() or entry.pw_name


def os_release() -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"')
    except OSError:
        pass
    return values


def memory_mb() -> int:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


def unreachable(hosts: tuple[str, ...]) -> list[str]:
    """The hosts that give no HTTPS answer at all (an HTTP error status counts as an answer)."""

    def answers(host: str) -> bool:
        try:
            urllib.request.urlopen(urllib.request.Request(f"https://{host}/", method="HEAD"), timeout=10)
        except urllib.error.HTTPError:
            return True
        except (OSError, ValueError):
            return False
        return True

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(answers, hosts))
    return [host for host, reached in zip(hosts, results) if not reached]


def local_mode_problems() -> list[str]:
    """Why this machine cannot be the server too ("everything on this machine")."""
    problems = []
    release = os_release()
    if release.get("ID") != "debian" or release.get("VERSION_ID") != "13":
        problems.append(f"le système est {release.get('PRETTY_NAME', 'inconnu')} : il faut Debian 13 (trixie)")
    if platform.machine() != "x86_64":
        problems.append(f"l'architecture est {platform.machine()} : il faut amd64 (x86_64), seule cible de l'image de Dokos")
    ram = memory_mb()
    if ram < LOCAL_MIN_RAM_MB:
        problems.append(f"la mémoire est de {ram / 1024:.1f} Go : il en faut 8".replace(".", ","))
    free = shutil.disk_usage("/var").free // 1024**3 if Path("/var").exists() else 0
    if free < LOCAL_MIN_DISK_GB:
        problems.append(f"{free} Go libres dans /var : il en faut au moins {LOCAL_MIN_DISK_GB}, 40 conseillés")
    if not shutil.which("sudo"):
        problems.append("sudo est absent")
    user = current_user().pw_name
    if os.getuid() == 0:
        problems.append("l'assistant est lancé en root : lancez-le avec votre compte personnel, qui deviendra le compte de maintenance")
    elif user in NOT_NOMINATIVE or not UNIX_USER.match(user):
        problems.append(
            f"le compte « {user} » n'est pas nominatif : créez le vôtre (sudo adduser prenom && sudo adduser prenom sudo), connectez-vous avec lui et relancez"
        )
    down = unreachable(SERVER_SITES)
    if down:
        problems.append(f"sites injoignables depuis cette machine : {', '.join(down)}")
    return problems


def authorized_keys(path: Path) -> tuple[list[str], int]:
    """The keys of an authorized_keys file, and how many lines had options (dropped)."""
    keys: list[str] = []
    with_options = 0
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return [], 0
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key = ssh_key_from_text(line)
        if key:
            if not line.startswith(key.split()[0]):
                with_options += 1
            if key not in keys:
                keys.append(key)
    return keys, with_options


def create_ssh_key(private: Path, *, passphrase: bool) -> None:
    private.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    comment = f"{current_user().pw_name}@{socket.gethostname()} (DokoPilot)"
    cmd = ["ssh-keygen", "-t", "ed25519", "-C", comment, "-f", str(private)]
    if not passphrase:
        cmd[1:1] = ["-q", "-N", ""]
    if subprocess.run(cmd, check=False).returncode != 0 or not private.with_suffix(".pub").exists():
        raise Stop("La création de la clé SSH a échoué (ssh-keygen).")
    print(f"  Clé SSH créée : {private.with_suffix('.pub')}")


def write_private(path: Path, content: str) -> None:
    """Write a file only this account can read (0600), in a folder only it can open (0700)."""
    for directory in (CONFIG_ROOT, path.parent):
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o700)
    temporary = path.with_name(f".{path.name}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)
    temporary.replace(path)


class IndentedDumper(yaml.SafeDumper):
    """Indents block sequences, as yamllint's default configuration expects."""

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        super().increase_indent(flow, False)


def dump_yaml(data: Any) -> str:
    return yaml.dump(data, Dumper=IndentedDumper, allow_unicode=True, sort_keys=False, width=120)


def header(number: int, title: str) -> None:
    print(f"\nÉtape {number} sur 6 — {title}")


# --------------------------------------------------------------------------- state


@dataclass
class State:
    """Completed steps and answers (never a secret), saved once the client is known."""

    completed: list[str] = field(default_factory=list)
    answers: dict[str, Any] = field(default_factory=dict)
    # Secrets typed during this run, kept in memory until they are stored.
    pending: dict[str, Any] = field(default_factory=dict)

    @property
    def client(self) -> str:
        return str(self.answers.get("client_id", ""))

    @property
    def path(self) -> Path | None:
        return clients_dir() / self.client / STATE_NAME if self.client else None

    def done(self, name: str) -> bool:
        return name in self.completed

    def mark(self, name: str) -> None:
        if name not in self.completed:
            self.completed.append(name)
        self.save()

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "format": STATE_FORMAT,
            "client": self.client,
            "completed": self.completed,
            "answers": self.answers,
            "updated": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        temporary = self.path.with_name(f".{STATE_NAME}.tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        temporary.replace(self.path)

    @classmethod
    def load(cls, path: Path) -> State | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or data.get("format") != STATE_FORMAT or not isinstance(data.get("answers"), dict):
            return None
        return cls(completed=[str(step) for step in data.get("completed", [])], answers=data["answers"])

    def describe(self) -> str:
        titles = [title for name, title in STEPS if name in self.completed]
        titles += [name for name in ACTIONS if name in self.completed]
        return " ; ".join(titles) or "aucune"


def choose_state(ui: Ui) -> State:
    """Resume an installation that was started, or start a new one."""
    if ui.scripted:
        client = ui.lookup("client_id")
        candidates = [clients_dir() / str(client) / STATE_NAME] if client is not MISSING else []
    else:
        candidates = sorted(clients_dir().glob(f"*/{STATE_NAME}"))
    for path in candidates:
        state = State.load(path)
        if state is None or not state.client:
            continue
        if not ui.scripted and state.done("check"):
            continue
        print(f"\nUne installation de « {state.client} » a été commencée (fait : {state.describe()}).")
        if ui.yes_no("resume", f"Reprendre l'installation de « {state.client} » ?", True):
            print(f"Reprise de l'installation de « {state.client} » : les étapes déjà faites sont passées.")
            return state
        if ui.scripted:
            # resume: non in the answers: start this client again from the first step.
            path.unlink()
            print(f"L'installation de « {state.client} » recommence depuis le début.")
    return State()


# --------------------------------------------------------------------------- steps


def step_where(ui: Ui, state: State) -> None:
    a = state.answers
    me = current_user()
    while True:
        where = ui.choice(
            "where",
            "Où installer DokoPilot ?",
            [
                Option("local", "tout sur cette machine (Debian 13 amd64, 8 Go de mémoire, 40 Go de disque)", ("cette machine",)),
                Option("distant", "sur un serveur distant, piloté en SSH depuis cette machine", ("remote", "serveur")),
            ],
            "distant",
        )
        if where == "distant":
            break
        print("  Vérification de cette machine…")
        problems = local_mode_problems()
        if not problems:
            break
        print("  « Tout sur cette machine » est impossible ici :")
        for problem in problems:
            print(f"    - {problem}")
        if ui.scripted:
            raise Stop("Choisissez un serveur distant (where: distant) ou corrigez ces points.", 2)
        print("  Choisissez un serveur distant, ou corrigez ces points puis relancez.")
    a["where"] = where
    if where == "local":
        a["maintenance_user"] = me.pw_name
        a["full_name"] = full_name_of(me)
        hostname = socket.gethostname()
        a["hostname"] = hostname if HOSTNAME.match(hostname) else ""
        print(
            f"  Votre compte « {me.pw_name} » devient le compte de maintenance : son mot de passe ne change pas, "
            "et sudo vous le demandera (invite « BECOME password » d'Ansible)."
        )
        keys, with_options = authorized_keys(Path(me.pw_dir) / ".ssh" / "authorized_keys")
        a.pop("ssh_public_key_files", None)
        a.pop("ssh_public_keys", None)
        if keys:
            print(
                f"  Les {len(keys)} clé(s) SSH déjà autorisées pour « {me.pw_name} » (~/.ssh/authorized_keys) le restent : "
                "ce seront les seules à ouvrir une session SSH à distance."
            )
            if with_options:
                print(f"  ! {with_options} de ces lignes avaient des options (from=…, command=…) : elles sont reprises sans ces options.")
            a["ssh_public_keys"] = keys
        else:
            key = ui.ask(
                "ssh_public_key",
                "Clé SSH publique de l'ordinateur depuis lequel vous administrerez cette machine à distance "
                "(texte « ssh-ed25519 … » ou chemin d'un fichier .pub ; Entrée : aucune)",
                default="",
                parse=check_key_source,
            )
            if key:
                a["ssh_public_keys"] = [key]
            else:
                private = Path(me.pw_dir) / ".ssh" / "id_ed25519"
                if not private.with_suffix(".pub").exists():
                    print("  Aucune clé pour l'accès à distance : une clé SSH propre à cette machine est créée (le compte doit en avoir une).")
                    create_ssh_key(private, passphrase=False)
                a["ssh_public_key_files"] = ["~/.ssh/id_ed25519.pub"]
                print(
                    "  Pour administrer cette machine depuis un autre ordinateur, ajoutez plus tard sa clé publique "
                    "dans client.local.yml (maintenance.users), puis relancez bin/dokopilot install."
                )
        return

    a["server_address"] = ui.ask("server.address", "Adresse du serveur (adresse IP ou nom)", parse=check_host)
    a["ssh_port"] = ui.ask("server.ssh_port", "Port SSH du serveur", default="22", parse=check_port)
    a["bootstrap_user"] = ui.ask(
        "server.bootstrap_user",
        "Compte initial du serveur, donné par l'hébergeur ou créé à l'installation de Debian (sert une seule fois)",
        default="debian",
        parse=check_login,
    )
    default_user = me.pw_name if me.pw_name not in NOT_NOMINATIVE and UNIX_USER.match(me.pw_name) else None
    a["maintenance_user"] = ui.ask(
        "maintenance_user", "Votre compte de maintenance sur le serveur (nominatif, il sera créé)", default=default_user, parse=check_maintenance_user
    )
    a["full_name"] = full_name_of(me) if a["maintenance_user"] == me.pw_name else a["maintenance_user"]
    public = ui.ask("ssh_public_key", "Votre clé SSH publique (pour vous connecter au serveur)", default="~/.ssh/id_ed25519.pub", parse=check_pub_path)
    path = Path(public).expanduser()
    if not path.exists():
        if not ui.yes_no("ssh_key_create", f"La clé {public} n'existe pas : la créer maintenant ?", True):
            raise Stop(f"Il faut une clé SSH : créez-la (ssh-keygen -t ed25519 -f {path.with_suffix('')}), puis relancez.")
        if ui.scripted:
            print("  ! Clé créée sans phrase de passe : protégez-la ensuite si vous le souhaitez (ssh-keygen -p).")
        create_ssh_key(path.with_suffix(""), passphrase=not ui.scripted)
    if not check_key_source(str(path)):
        raise Stop(f"{path} ne contient pas de clé SSH publique.")
    a["ssh_public_key_files"] = [public]
    a.pop("ssh_public_keys", None)


def step_profile(ui: Ui, state: State) -> None:
    a = state.answers
    a["profile"] = ui.choice(
        "profile",
        "Démo ou production ?",
        [
            Option("demo", "démo : l'Atelier Démo, entreprise fictive avec ses données de test, pour essayer", ("démo", "test")),
            Option("production", "production : votre entreprise, ses vraies données, Tailscale et sauvegarde distante", ("prod",)),
        ],
        "demo",
    )
    client = ui.ask(
        "client_id",
        "Identifiant court de cette installation (minuscules, chiffres, tirets : dossier clients/<id>, organisation Forgejo)",
        default="atelier-demo" if a["profile"] == "demo" else None,
        parse=check_client_id,
    )
    existing = State.load(clients_dir() / client / STATE_NAME)
    replace = f"Une installation de « {client} » existe déjà (fait : {existing.describe() if existing else ''}) : la remplacer ?"
    if existing is not None and not ui.scripted and not ui.confirm(replace, default=False):
        raise Stop("Rien n'a été changé. Relancez l'assistant et choisissez de reprendre, ou prenez un autre identifiant.", 0)
    a["client_id"] = client
    harness = ui.choice(
        "harness",
        "Quel agent utiliser sur ce poste ?",
        [
            Option("opencode", "opencode : logiciel libre, fonctionne avec le fournisseur de modèle de votre choix"),
            Option("claude-code", "Claude Code : avec un abonnement Claude ou une clé d'API Anthropic", ("claude",)),
            Option("les-deux", "les deux (opencode par défaut)", ("deux", "both")),
        ],
        "opencode",
    )
    a["harness_enabled"] = ["opencode", "claude-code"] if harness == "les-deux" else [harness]
    a["harness_default"] = a["harness_enabled"][0]


def provider_options() -> list[Option]:
    options = [Option(p.id, p.label, group=p.family) for p in PROVIDERS]
    options.append(Option("autre", "un autre fournisseur pris en charge par opencode (identifiant à indiquer)", group="Autre"))
    return options


def model_key_path(client: str) -> Path:
    return CONFIG_ROOT / client / "model-api-key"


def step_models(ui: Ui, state: State) -> None:
    a = state.answers
    enabled = a["harness_enabled"]
    provider: Provider | None = None
    if "opencode" in enabled:
        chosen = ui.choice("models.provider", "Fournisseur du modèle d'IA (sa clé reste sur cette machine)", provider_options(), "anthropic")
        if chosen == "autre":
            identifier = ui.ask("models.provider_id", "Identifiant opencode du fournisseur", parse=check_provider_id)
            needs_key = ui.yes_no("models.provider_needs_key", "Ce fournisseur demande-t-il une clé d'API ?", True)
            provider = Provider(identifier, identifier, "Autre", needs_key=needs_key)
        else:
            provider = next(p for p in PROVIDERS if p.id == chosen)
    else:
        provider = PROVIDERS[0]
        print("  Claude Code utilise les modèles d'Anthropic.")
    a["provider"] = provider.id
    if provider.models_page:
        print(f"  Identifiants des modèles : {provider.models_page}")
    if provider.id == "ollama":
        print("  Ollama doit tourner sur une machine à vous ; opencode s'y connecte sans clé.")
    for role, question in ROLES.items():
        default = provider.defaults.get(role, "")
        if role == "small" and not default:
            hint = "Entrée : le modèle Bureau"
        elif not default:
            hint = "Entrée : choisi plus tard dans l'agent"
        else:
            hint = f"{provider.id}/{default}"
        a[role] = ui.ask(f"models.{role}", question, default=default, parse=model_parser(provider.id), hint=hint)

    a["claude_code_auth"] = "subscription"
    if "claude-code" in enabled:
        if "opencode" in enabled and provider.id != "anthropic":
            print(f"  Claude Code utilisera l'abonnement Claude (connexion par /login) : la clé de ce poste est celle de {provider.label}.")
        else:
            auth = ui.choice(
                "claude_code_auth",
                "Claude Code : abonnement Claude ou clé d'API Anthropic ?",
                [
                    Option("abonnement", "abonnement Claude (Pro, Max, Team…) : connexion par /login dans Claude Code", ("subscription",)),
                    Option("cle-api", "clé d'API Anthropic, gardée sur ce poste", ("api_key", "api", "cle")),
                ],
                "abonnement",
            )
            a["claude_code_auth"] = "api_key" if auth == "cle-api" else "subscription"

    needs_key = ("opencode" in enabled and provider.needs_key) or a["claude_code_auth"] == "api_key"
    a["model_key"] = needs_key
    if not needs_key:
        return
    path = model_key_path(state.client)
    if path.exists() and path.stat().st_size > 0:
        if ui.scripted and ui.lookup("models.api_key_env") is MISSING and ui.lookup("models.api_key_file") is MISSING:
            print(f"  Clé d'API déjà enregistrée dans {path} : conservée.")
            return
        if not ui.scripted and ui.yes_no("models.keep_api_key", f"Une clé d'API est déjà enregistrée dans {path} : la garder ?", True):
            return
    key = ui.secret("models.api_key", f"Clé d'API {provider.label}", required=True, check=check_api_key)
    write_private(path, key)
    print(f"  Clé enregistrée dans {path} (lisible par vous seul). Elle n'est écrite ni dans clients/ ni dans les secrets.")


def ask_person(ui: Ui, index: int, role: str, taken: set[str], demo: bool) -> dict[str, Any]:
    prefix = f"people.{index}"
    dirigeant = role == "dirigeant"
    example = demo and dirigeant
    first = ui.ask(f"{prefix}.first_name", "Prénom", default="Camille" if example else None, parse=check_name)
    last = ui.ask(f"{prefix}.last_name", "Nom", default="Martin" if example else "", parse=check_optional_name)
    email = ui.ask(f"{prefix}.email", "Courriel", default="camille.martin@atelier-demo.test" if example else None, parse=check_email)

    def unique_id(text: str) -> str:
        check_person_id(text)
        if text in taken:
            raise Invalid(f"l'identifiant « {text} » est déjà pris.")
        return text

    suggestion = next((s for s in (slug(first), slug(f"{first}-{last}"), slug(email.split("@")[0])) if s and s not in taken), None)
    person_id = ui.ask(f"{prefix}.id", "Identifiant (comptes et dépôt perso-<id>)", default=suggestion, parse=unique_id)
    if dirigeant:
        # The dirigeant's assistant is the identity of the agent of this computer: always there.
        given = ui.lookup(f"{prefix}.assistant")
        if given is not MISSING and fold(str(given)) in {"n", "non", "no", "false", "faux", "0"}:
            raise Stop(f"{ui.source} : le dirigeant a toujours un assistant (people.0.assistant: oui) : c'est l'identité de son agent.", 2)
        print(f"  {first} a un assistant : l'agent qui lit et prépare des brouillons dans Dokos et le contexte, sans jamais rien valider.")
        assistant = True
    else:
        question = f"Un assistant pour {first} dans le contexte (Forgejo) ? En version 0.2, seul l'assistant du dirigeant entre dans Dokos"
        assistant = ui.yes_no(f"{prefix}.assistant", question, True)
    taken.add(person_id)
    return {"id": person_id, "email": email, "first_name": first, "last_name": last, "role": role, "assistant": assistant}


def step_company(ui: Ui, state: State) -> None:
    a = state.answers
    demo = a["profile"] == "demo"
    a["company_name"] = ui.ask(
        "company.name", "Raison sociale (telle qu'elle apparaîtra dans Dokos)", default="Atelier Démo" if demo else None, parse=check_name
    )
    a["company_abbr"] = ui.ask(
        "company.abbr", "Abréviation dans Dokos (2 à 5 lettres ou chiffres)", default=abbreviation(a["company_name"]) or None, parse=check_abbr
    )
    a["siren"] = "" if demo else ui.ask("company.siren", "SIREN (9 chiffres)", parse=check_siren)
    people: list[dict[str, Any]] = []
    taken: set[str] = set()
    print("  Le dirigeant (il valide dans Dokos ce que les agents préparent) :")
    people.append(ask_person(ui, 0, "dirigeant", taken, demo))
    while True:
        index = len(people)
        if ui.scripted:
            more = isinstance(ui.lookup("people"), list) and index < len(ui.lookup("people"))
        else:
            more = ui.yes_no("", "Ajouter une autre personne de l'entreprise ?", False)
        if not more:
            break
        print(f"  Personne {index + 1} (membre : comptes dans Forgejo pour le contexte) :")
        people.append(ask_person(ui, index, "membre", taken, demo))
    emails = [person["email"] for person in people]
    if len(set(emails)) != len(emails):
        raise Stop("Deux personnes ont le même courriel : relancez l'assistant et corrigez-les.", 2)
    a["people"] = people


def step_network(ui: Ui, state: State) -> None:
    a = state.answers
    client = state.client
    if a["profile"] == "production":
        print("  En production, Dokos n'est joignable que par Tailscale, votre réseau privé chiffré.")
        mode = "tailscale"
    else:
        mode = ui.choice(
            "network",
            "Comment joindre Dokos et Forgejo ?",
            [
                Option("lan", "réseau local : depuis les adresses privées (box, VM), en HTTP", ("local", "reseau local")),
                Option("tailscale", "Tailscale : réseau privé chiffré, depuis vos appareils où qu'ils soient"),
            ],
            "lan",
        )
    a["network"] = mode
    a["tailscale_key_pending"] = False
    if mode == "tailscale":
        print("  Créez une clé d'authentification dans la console Tailscale (Settings › Keys › Generate auth key).")
        key = ui.secret("tailscale_auth_key", "Clé d'authentification Tailscale (tskey-auth-…)", required=True, check=check_tailscale_key)
        state.pending["tailscale_auth_key"] = key
        a["tailscale_key_pending"] = True

    a["backup_repository"] = ""
    a["backup_s3_pending"] = False
    if a["profile"] == "production":
        print("  La sauvegarde chiffrée part chaque nuit vers un dépôt distant (une copie sur le serveur n'en est pas une).")
        a["backup_repository"] = ui.ask("backup.repository", "Dépôt de sauvegarde restic", parse=check_backup_repository)
        if a["backup_repository"].startswith("s3:"):
            key_id = ui.secret("backup.s3_access_key_id", "Identifiant de la clé d'accès S3", required=True, check=check_not_empty)
            secret = ui.secret("backup.s3_secret_access_key", "Clé secrète d'accès S3", required=True, check=check_not_empty)
            state.pending["restic_env"] = {"AWS_ACCESS_KEY_ID": key_id, "AWS_SECRET_ACCESS_KEY": secret}
            a["backup_s3_pending"] = True
        else:
            print(f"  Identifiants du dépôt : ajoutez-les ensuite avec « bin/dokopilot secrets {client} edit » (clé restic_env).")

    mail: dict[str, Any] = {"enabled": False}
    if ui.yes_no("mail.enabled", "Brancher une messagerie (lecture et brouillons seulement, jamais d'envoi) ?", False):
        host = ui.ask("mail.imap_host", "Serveur IMAP (ex. imap.exemple.fr)", parse=check_host)
        port = ui.ask("mail.imap_port", "Port IMAP", default="993", parse=check_port)
        tls = ui.choice(
            "mail.imap_tls",
            "Chiffrement IMAP",
            [Option("ssl", "SSL/TLS (port 993)"), Option("starttls", "STARTTLS (port 143)")],
            "ssl" if port == 993 else "starttls",
        )
        username = ui.ask("mail.username", "Identifiant de connexion", parse=check_name)
        email = ui.ask("mail.email", "Adresse affichée", default=username if EMAIL.match(username) else None, parse=check_email)
        mail = {"enabled": True, "imap_host": host, "imap_port": port, "imap_tls": tls, "username": username, "email": email}
        store_password(ui, "mail.password", CONFIG_ROOT / client / "mail-password", f"Mot de passe de la messagerie {username}")
    a["mail"] = mail

    calendar: dict[str, Any] = {"enabled": False}
    if ui.yes_no("calendar.enabled", "Brancher un agenda CalDAV (lecture ; événements sur accord) ?", False):
        url = ui.ask("calendar.caldav_url", "Adresse CalDAV (https://…)", parse=check_https_url)
        username = ui.ask("calendar.username", "Identifiant de l'agenda", parse=check_name)
        calendar = {"enabled": True, "caldav_url": url, "username": username}
        store_password(ui, "calendar.password", CONFIG_ROOT / client / "caldav-password", f"Mot de passe de l'agenda {username}")
    a["calendar"] = calendar

    a["import_path"] = ui.ask(
        "import_path",
        "Dossier de ce poste à importer dans la boîte de réception du contexte (ex. un coffre Obsidian ; Entrée : aucun)",
        default="",
        parse=check_import_dir,
    )


def store_password(ui: Ui, key: str, path: Path, question: str) -> None:
    """Mail and calendar passwords: on this computer only, where the harness role reads them."""
    if path.exists() and path.stat().st_size > 0:
        if ui.scripted and ui.lookup(f"{key}_env") is MISSING and ui.lookup(f"{key}_file") is MISSING:
            print(f"  Mot de passe déjà enregistré dans {path} : conservé.")
            return
        if not ui.scripted and ui.yes_no("", f"Un mot de passe est déjà enregistré dans {path} : le garder ?", True):
            return
    value = ui.secret(key, question, required=True, check=check_not_empty)
    # Exact content, without a final newline: the harness role reads the file as is.
    write_private(path, value)
    print(f"  Mot de passe enregistré dans {path} (lisible par vous seul).")


STEP_FUNCTIONS: dict[str, Callable[[Ui, State], None]] = {
    "lieu": step_where,
    "profil": step_profile,
    "modeles": step_models,
    "entreprise": step_company,
    "reseau": step_network,
}


# --------------------------------------------------------------------------- client files


def client_settings(a: dict[str, Any]) -> dict[str, Any]:
    settings: dict[str, Any] = {
        "profile": a["profile"],
        "client": {"id": a["client_id"], "company_name": a["company_name"], "company_abbr": a["company_abbr"], "siren": a["siren"]},
        "network": {"mode": a["network"]},
        "users": {"people": a["people"]},
        "context": {"enabled": True, "direction": True, "import_path": a["import_path"]},
        "models": {
            "provider": a["provider"],
            "bureau": a["bureau"],
            "atelier": a["atelier"],
            "small": a["small"],
            "claude_code_auth": a["claude_code_auth"],
        },
        "harness": {"default": a["harness_default"], "enabled": a["harness_enabled"], "mail": a["mail"], "calendar": a["calendar"]},
    }
    if a["profile"] == "production":
        settings["backup"] = {"repository": a["backup_repository"]}
    return settings


def local_settings(a: dict[str, Any]) -> dict[str, Any]:
    account: dict[str, Any] = {"name": a["maintenance_user"], "full_name": a["full_name"]}
    if a.get("ssh_public_keys"):
        account["ssh_public_keys"] = a["ssh_public_keys"]
    else:
        account["ssh_public_key_files"] = a["ssh_public_key_files"]
    if a["where"] == "local":
        server: dict[str, Any] = {"local": True, "address": "localhost"}
        if a.get("hostname"):
            # Keep the name of this machine: the base role would otherwise rename it.
            server["hostname"] = a["hostname"]
    else:
        server = {"local": False, "address": a["server_address"], "ssh_port": a["ssh_port"], "bootstrap_user": a["bootstrap_user"]}
    return {"server": server, "maintenance": {"users": [account]}}


def write_client_files(ui: Ui, state: State) -> None:
    target = clients_dir() / state.client
    target.mkdir(parents=True, exist_ok=True)
    now = datetime.now().astimezone()
    stamp = now.strftime("%Y%m%d-%H%M%S")
    files = {
        "client.yml": (
            (
                f"# Configuration de « {state.answers['company_name']} », écrite par l'assistant d'installation le {now:%Y-%m-%d}.\n"
                "# Référence de chaque réglage : config/client.example.yml. Aucun secret ici : les mots de passe sont dans\n"
                "# secrets.yml.age (chiffré), la clé du modèle sur le poste (~/.config/dokopilot).\n"
            ),
            client_settings(state.answers),
        ),
        "client.local.yml": (
            "# Réglages propres à ce poste (ignoré par Git), écrits par l'assistant d'installation.\n",
            local_settings(state.answers),
        ),
    }
    for name, (comment, data) in files.items():
        path = target / name
        if path.exists():
            backup = path.with_name(f"{name}.{stamp}.bak")
            if not ui.scripted and not ui.confirm(f"{path} existe déjà : le remplacer (l'ancien est gardé dans {backup.name}) ?"):
                raise Stop("Rien n'a été écrit. Relancez l'assistant quand vous le souhaitez.", 0)
            shutil.copy2(path, backup)
            print(f"  Ancien fichier gardé : {backup}")
        path.write_text(comment + dump_yaml(data), encoding="utf-8")
        print(f"  Écrit : {path}")


def print_recap(state: State) -> None:
    a = state.answers
    where = "tout sur cette machine" if a["where"] == "local" else f"serveur distant {a['server_address']} (port {a['ssh_port']})"
    print("\n  Récapitulatif")
    print(f"    Installation      : {a['client_id']} ({'démo' if a['profile'] == 'demo' else 'production'}), {where}")
    print(f"    Maintenance       : compte « {a['maintenance_user']} »")
    print(f"    Agent             : {' et '.join(a['harness_enabled'])}")
    unset = {"bureau": "(choisi dans l'agent)", "atelier": "(choisi dans l'agent)", "small": "(le modèle Bureau)"}
    models = ", ".join(f"{role} {a[role] or unset[role]}" for role in ROLES)
    print(f"    Modèles           : {a['provider']} — {models}")
    print(f"    Entreprise        : {a['company_name']} ({a['company_abbr']}){' — SIREN ' + a['siren'] if a['siren'] else ''}")
    for person in a["people"]:
        assistant = " + assistant" if person["assistant"] else ""
        print(f"    {person['role'].capitalize():<17} : {person['first_name']} {person['last_name']} <{person['email']}> ({person['id']}{assistant})")
    print(f"    Réseau            : {'réseau local' if a['network'] == 'lan' else 'Tailscale'}")
    print(f"    Messagerie, agenda: {'oui' if a['mail']['enabled'] else 'non'}, {'oui' if a['calendar']['enabled'] else 'non'}")
    if a["import_path"]:
        print(f"    Import            : {a['import_path']}")


# --------------------------------------------------------------------------- bin/dokopilot


def python() -> str:
    candidate = VENV / "bin" / "python"
    return str(candidate) if candidate.exists() else sys.executable


def run_cli(state: State, action: str, args: list[str], *, stdin: str | None = None, env: dict[str, str] | None = None) -> None:
    print(f"\n  → bin/dokopilot {' '.join(args)}")
    result = subprocess.run([python(), str(CLI), *args], cwd=REPO, input=stdin, text=True, env=env, check=False)
    if result.returncode != 0:
        raise Stop(
            f"L'étape « {ACTIONS[action]} » a échoué (code {result.returncode}). Corrigez la cause indiquée plus haut, "
            f"puis relancez l'assistant ({REPO}/.venv/bin/python {REPO}/installer/wizard.py) : il reprendra à cette étape.",
            result.returncode,
        )


def sudo_note(state: State) -> list[str]:
    """Options of the playbook commands; on this machine, sudo uses the person's own password (-K)."""
    if state.answers["where"] != "local":
        return []
    print(f"  Ansible va demander votre mot de passe sudo (invite « BECOME password ») : celui de votre compte « {state.answers['maintenance_user']} ».")
    return ["--local", "-K"]


def store_secrets(ui: Ui, state: State) -> None:
    """Tailscale key and backup credentials go into the encrypted secrets, through
    « bin/dokopilot secrets <client> edit » with an editor that reads them on its standard input."""
    a = state.answers
    values: dict[str, Any] = {}
    if a.get("tailscale_key_pending"):
        if "tailscale_auth_key" not in state.pending:
            state.pending["tailscale_auth_key"] = ui.secret(
                "tailscale_auth_key", "Clé d'authentification Tailscale (tskey-auth-…)", required=True, check=check_tailscale_key
            )
        values["tailscale_auth_key"] = state.pending["tailscale_auth_key"]
    if a.get("backup_s3_pending"):
        if "restic_env" not in state.pending:
            key_id = ui.secret("backup.s3_access_key_id", "Identifiant de la clé d'accès S3", required=True, check=check_not_empty)
            secret = ui.secret("backup.s3_secret_access_key", "Clé secrète d'accès S3", required=True, check=check_not_empty)
            state.pending["restic_env"] = {"AWS_ACCESS_KEY_ID": key_id, "AWS_SECRET_ACCESS_KEY": secret}
        values["restic_env"] = state.pending["restic_env"]
    if not values:
        return
    env = dict(os.environ, EDITOR=str(SECRET_EDITOR))
    run_cli(state, "secrets", ["secrets", state.client, "edit"], stdin=json.dumps(values), env=env)
    a["tailscale_key_pending"] = False
    a["backup_s3_pending"] = False
    state.pending.clear()


def check_ssh_access(ui: Ui, state: State) -> None:
    """Before bootstrap: the initial account must accept this computer's key."""
    a = state.answers
    port = str(a["ssh_port"])
    target = f"{a['bootstrap_user']}@{a['server_address']}"
    public = Path(a["ssh_public_key_files"][0]).expanduser()
    probe = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=accept-new", "-p", port, target, "true"]
    advice = (
        f"Copiez votre clé sur le serveur (ssh-copy-id -p {port} -i {public} {target}), vérifiez « ssh -p {port} {target} true », puis relancez l'assistant."
    )
    if subprocess.run(probe, stdin=subprocess.DEVNULL, check=False).returncode == 0:
        return
    print(f"  Le compte {target} n'accepte pas (encore) votre clé SSH, ou le serveur ne répond pas.")
    if ui.scripted or not ui.yes_no("", "Y copier votre clé maintenant (ssh-copy-id demandera le mot de passe de ce compte) ?", True):
        raise Stop(advice)
    copied = subprocess.run(["ssh-copy-id", "-p", port, "-i", str(public), target], check=False).returncode == 0
    if not copied or subprocess.run(probe, stdin=subprocess.DEVNULL, check=False).returncode != 0:
        raise Stop(f"La connexion SSH au serveur échoue toujours. {advice}")


def installation(ui: Ui, state: State, stop_after: str) -> None:
    a = state.answers
    client = state.client
    if not state.done("fichiers"):
        print_recap(state)
        if not ui.confirm("Écrire cette configuration et lancer l'installation ?"):
            raise Stop("Rien n'a été écrit. Relancez l'assistant pour reprendre.", 0)
        write_client_files(ui, state)
        state.mark("fichiers")
    if not state.done("init"):
        run_cli(state, "init", ["init", client, "--profile", a["profile"]])
        state.mark("init")
    if not state.done("secrets"):
        store_secrets(ui, state)
        state.mark("secrets")
    if not state.done("config"):
        run_cli(state, "config", ["config", client])
        state.mark("config")
    if stop_after == "config":
        print(f"\nConfiguration de « {client} » validée. Pour installer le serveur, relancez l'assistant : il reprendra ici.")
        return
    if a["where"] != "local" and not state.done("bootstrap"):
        check_ssh_access(ui, state)
        print(
            f"  Ansible va demander le mot de passe sudo du compte « {a['bootstrap_user']} » (invite « BECOME password ») ; "
            "s'il n'en a pas (root, ou sudo sans mot de passe), appuyez sur Entrée."
        )
        run_cli(state, "bootstrap", ["bootstrap", client, "-K"])
        state.mark("bootstrap")
    for action, args in (("install", ["install", client]), ("harness", ["harness", client]), ("check", ["check", client])):
        if not state.done(action):
            if action == "install":
                print("  L'installation complète dure de 30 à 60 minutes ; elle se relance sans risque.")
            run_cli(state, action, args + sudo_note(state))
            state.mark(action)
        if stop_after == action:
            print(f"\nÉtape « {ACTIONS[action]} » faite pour « {client} ». Relancez l'assistant pour continuer.")
            return
    print_summary(state)


def lan_addresses() -> list[str]:
    try:
        output = subprocess.run(["ip", "-4", "-o", "addr", "show", "scope", "global"], capture_output=True, text=True, check=False).stdout
    except OSError:
        return []
    return re.findall(r"inet (\d+\.\d+\.\d+\.\d+)/", output)


def print_summary(state: State) -> None:
    a = state.answers
    client = state.client
    dirigeant = a["people"][0]
    domain = dirigeant["email"].split("@")[1]
    config_dir = f"~/.config/dokopilot/{client}"
    if a["network"] == "tailscale":
        dokos = f"https://dokos-{client}.<votre réseau Tailscale>.ts.net (depuis vos appareils Tailscale)"
        forgejo = f"https://dokos-{client}.<votre réseau Tailscale>.ts.net:8443/"
    elif a["where"] == "local":
        others = ", ".join(f"http://{ip}" for ip in lan_addresses())
        dokos = "http://localhost" + (f" (depuis le réseau local : {others})" if others else "")
        forgejo = "http://localhost:3000/"
    else:
        dokos = f"http://{a['server_address']}"
        forgejo = f"http://{a['server_address']}:3000/"
    print(f"\nInstallation terminée pour « {a['company_name']} » ({client}).\n")
    print(f"  Dokos      {dokos}")
    print(f"  Forgejo    {forgejo}")
    print(f"  Dirigeant  {dirigeant['email']} — mot de passe initial de Dokos : bin/dokopilot secrets {client} show entrepreneur_password")
    print(f"             Forgejo : compte « {dirigeant['id']} », mot de passe initial dans {config_dir}/forgejo-password (à changer)")
    if dirigeant["assistant"]:
        assistant = f"assistant.{dirigeant['id']}@{domain}"
        print(f"  Assistant  {assistant} (Dokos et Forgejo ; son jeton reste sur ce poste, hors de l'espace de travail)")
    for person in a["people"][1:]:
        extra = " et celui de son assistant" if person["assistant"] else ""
        print(f"  Membre     {person['first_name']} ({person['id']}) : compte Forgejo{extra}, pour le contexte")
    if len(a["people"]) > 1:
        print("             Dans Dokos, le dirigeant crée lui-même les comptes des salariés, avec les rôles qui conviennent.")
    print(f"  Espace     ~/DokoPilot/{client} (copies du contexte ; l'agent y travaille)")
    print("\nProchaines étapes :")
    print("  1. Sauvegardez ~/.config/dokopilot/age-identity.txt dans votre gestionnaire de mots de passe : sans elle, les secrets sont illisibles.")
    agent = "opencode" if a["harness_default"] == "opencode" else "claude"
    login = " (Claude Code : /login au premier lancement)" if "claude-code" in a["harness_enabled"] and a["claude_code_auth"] == "subscription" else ""
    print(f"  2. Lancez l'agent : cd ~/DokoPilot/{client} && {agent}{login}")
    print(f"  3. Relisez et poussez le travail de l'agent : bin/dokopilot sync {client} (vous poussez, jamais l'agent).")
    if a["profile"] == "production":
        print(f"  4. Identifiants de la plateforme agréée et du dépôt de sauvegarde : bin/dokopilot secrets {client} edit.")
    print(f"  Contrôles à tout moment : bin/dokopilot check {client}{' --local -K' if a['where'] == 'local' else ''}")
    missing = missing_tools(a)
    if missing:
        print(f"\nÀ installer sur ce poste (commandes dans {REPO}/docs/poste-entrepreneur.md, « 1. Préparer l'ordinateur ») :")
        for tool, purpose in missing:
            print(f"  · {tool} : {purpose}")


def missing_tools(a: dict[str, Any]) -> list[tuple[str, str]]:
    """Programs of the workstation the harness uses and that this computer lacks. The
    assistant does not install them: the agents come from their publishers' installers."""
    wanted: list[tuple[str, str, str]] = []
    if "opencode" in a["harness_enabled"]:
        wanted.append(("opencode", "opencode", "le harnais par défaut"))
    if "claude-code" in a["harness_enabled"]:
        wanted.append(("claude", "Claude Code", "le harnais en option"))
        if sys.platform.startswith("linux"):
            wanted += [("bwrap", "bubblewrap", "bac à sable des commandes de Claude Code"),
                       ("socat", "socat", "bac à sable des commandes de Claude Code")]
    if a["mail"].get("enabled"):
        wanted.append(("uvx", "uv (uvx)", "connecteur de messagerie"))
    if a["calendar"].get("enabled"):
        wanted.append(("npx", "Node.js (npx)", "connecteur d'agenda"))
    return [(name, purpose) for command, name, purpose in wanted if not shutil.which(command)]


# --------------------------------------------------------------------------- secrets editor


EDITABLE_SECRETS = {"tailscale_auth_key", "restic_env"}


def edit_secrets_file(path: Path) -> int:
    """Editor mode (installer/secret-editor.sh): merge the values read on standard input
    (JSON) into the decrypted secrets file that bin/dokopilot hands to its editor."""
    try:
        values = json.loads(sys.stdin.read() or "null")
    except ValueError:
        values = None
    if not isinstance(values, dict) or not values or set(values) - EDITABLE_SECRETS:
        print("Éditeur de l'assistant : aucune valeur reçue, rien n'est changé.", file=sys.stderr)
        return 3
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    secrets = data.get("secrets") if isinstance(data, dict) else None
    if not isinstance(secrets, dict):
        print("Éditeur de l'assistant : le fichier n'a pas de clé « secrets », rien n'est changé.", file=sys.stderr)
        return 3
    for key, value in values.items():
        if isinstance(value, dict) and isinstance(secrets.get(key), dict):
            secrets[key] = {**secrets[key], **value}
        else:
            secrets[key] = value
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return 0


# --------------------------------------------------------------------------- main


def load_answers(path: str) -> dict[str, Any]:
    file = Path(path).expanduser()
    try:
        data = yaml.safe_load(file.read_text(encoding="utf-8"))
    except OSError as error:
        raise Stop(f"Fichier de réponses illisible : {file} ({error.strerror}).", 2) from None
    except yaml.YAMLError as error:
        raise Stop(f"Fichier de réponses invalide : {file} ({error}).", 2) from None
    if not isinstance(data, dict):
        raise Stop(f"{file} doit contenir un dictionnaire YAML (voir installer/answers.example.yml).", 2)
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wizard.py", description="Assistant d'installation de DokoPilot.")
    parser.add_argument("--answers", help="fichier de réponses (voir installer/answers.example.yml)")
    parser.add_argument("--yes", action="store_true", help="accepter les confirmations")
    parser.add_argument("--secret-editor", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    # Our lines and those of bin/dokopilot stay in order, in a terminal as in a log.
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(line_buffering=True)
    if args.secret_editor:
        return edit_secrets_file(Path(args.secret_editor))
    try:
        answers = load_answers(args.answers) if args.answers else None
        ui = Ui(answers, args.answers or "", args.yes)
        stop_after = as_text(ui.lookup("stop_after")) if ui.scripted and ui.lookup("stop_after") is not MISSING else ""
        if stop_after and stop_after not in STOP_AFTER:
            raise Stop(f"stop_after vaut {', '.join(STOP_AFTER)} ou rien ({args.answers}).", 2)
        print("Assistant d'installation de DokoPilot : six étapes, vos réponses sont gardées à chaque étape.")
        state = choose_state(ui)
        for number, (name, title) in enumerate(STEPS, 1):
            if state.done(name):
                print(f"\nÉtape {number} sur 6 — {title} : déjà faite.")
                continue
            header(number, title)
            STEP_FUNCTIONS[name](ui, state)
            state.mark(name)
        header(6, "Écriture de la configuration et installation")
        installation(ui, state, stop_after)
        return 0
    except Stop as stop:
        print(f"\n{stop}", file=sys.stderr if stop.code else sys.stdout)
        return stop.code
    except KeyboardInterrupt:
        print("\nInterrompu : relancez l'assistant, il reprendra où vous en étiez.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
