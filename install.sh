#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# DokoPilot V2: installation in one command.
#
#   curl -fsSL https://raw.githubusercontent.com/GDS-42/DokoPilotV2/v0.2.0/install.sh | sh
#
# or download it, read it, then run it:
#
#   curl -fsSLO https://raw.githubusercontent.com/GDS-42/DokoPilotV2/v0.2.0/install.sh
#   less install.sh
#   sh install.sh
#
# What it does (docs/plan-v0.2.md, C-5):
#   1. checks this machine: system, architecture, memory, free space, sudo, network;
#   2. installs the missing packages with apt or dnf (git, Python 3.12+ and venv, age,
#      OpenSSH client, make, curl), through sudo, after asking;
#   3. fetches DokoPilot at one release tag (git clone --depth 1 --branch);
#   4. runs `make setup` (Ansible in .venv), then the French assistant installer/wizard.py,
#      which asks the questions, writes the client settings and runs bin/dokopilot.
# Neither this script nor the assistant runs Ansible directly or stores a password: sudo
# passwords are typed when asked, the model API key stays in ~/.config/dokopilot.
#
# Environment variables, for tests and mirrors only:
#   DOKOPILOT_REPO_URL           Git repository to clone (default: the GitHub repository)
#   DOKOPILOT_LOCAL_MIN_RAM_MB   memory required for "everything on this machine" (7680)
#   DOKOPILOT_LOCAL_MIN_DISK_GB  free space required on /var for it, in GiB (30)
#
# Everything below is functions; the last line calls main, so a truncated download runs
# nothing. Messages are in French (they are read by the person installing).

set -eu

DOKOPILOT_VERSION=v0.2.0
DEFAULT_REPO_URL=https://github.com/GDS-42/DokoPilotV2.git

# Thresholds. Memory is compared with MemTotal, which is a little below the memory given to
# a machine (8 GiB shows as about 7.8 GiB). "Everything on this machine" builds the Dokos
# image and runs Dokos, MariaDB, Valkey and Forgejo; 40 GiB of disk are recommended, 30 GiB
# free are required (a 40 GiB virtual machine has about 37 GiB free after Debian).
LOCAL_MIN_RAM_MB=${DOKOPILOT_LOCAL_MIN_RAM_MB:-7680}
LOCAL_MIN_DISK_GB=${DOKOPILOT_LOCAL_MIN_DISK_GB:-30}
WORKSTATION_MIN_RAM_MB=1792
WORKSTATION_MIN_DISK_GB=2

# Sites reached by this script and `make setup`, then by a server installed on this machine.
WORKSTATION_SITES="pypi.org files.pythonhosted.org galaxy.ansible.com"
SERVER_SITES="deb.debian.org security.debian.org download.docker.com registry.gitlab.com gitlab.com registry-1.docker.io codeberg.org registry.npmjs.org"

usage() {
	cat <<EOF
DokoPilot V2 — installation en une commande

Usage : sh install.sh [options]

  --version <étiquette>  version de DokoPilot à installer (défaut : $DOKOPILOT_VERSION)
  --dir <dossier>        dossier de DokoPilot sur ce poste (défaut : ~/DokoPilotV2)
  --check                vérifier cette machine seulement, sans rien installer
  --answers <fichier>    répondre aux questions avec ce fichier, sans rien demander
                         (modèle commenté : installer/answers.example.yml)
  --yes                  répondre oui aux confirmations dont la réponse proposée est oui
  --help                 afficher cette aide

Le script vérifie la machine, installe ce qui manque (apt ou dnf, avec sudo), récupère
DokoPilot, prépare ses outils (make setup), puis lance l'assistant qui pose les questions,
écrit la configuration et installe tout avec bin/dokopilot.
EOF
}

# ------------------------------------------------------------------ messages

say() { printf '%s\n' "$*"; }
title() { printf '\n%s\n' "$*"; }
ok() { printf '  ✓ %s\n' "$*"; }
note() { printf '  · %s\n' "$*"; }
warn() { printf '  ! %s\n' "$*"; }
# A problem that stops the installation; --check reports it and goes on.
blocker() {
	printf '  ✗ %s\n' "$*"
	blocking=1
}
die() {
	printf '\nErreur : %s\n' "$*" >&2
	exit 1
}

# confirm QUESTION: yes with --yes or --answers, otherwise asks on the terminal (default yes).
confirm() {
	if [ "$assume_yes" = 1 ]; then
		say "$1 oui (--yes)"
		return 0
	fi
	[ "$have_tty" = 1 ] || die "aucun terminal pour répondre à « $1 » : relancez avec --yes ou --answers <fichier>."
	printf '%s [O/n] ' "$1"
	read -r reply </dev/tty || reply=n
	case $reply in
	'' | o | O | oui | Oui | OUI | y | Y | yes) return 0 ;;
	*) return 1 ;;
	esac
}

# ------------------------------------------------------------------ options

parse_args() {
	version=$DOKOPILOT_VERSION
	dir=""
	check_only=0
	answers=""
	assume_yes=0
	while [ $# -gt 0 ]; do
		case $1 in
		--version | --dir | --answers)
			[ $# -ge 2 ] || die "l'option $1 attend une valeur (voir --help)."
			option_value "$1" "$2"
			shift 2
			;;
		--version=* | --dir=* | --answers=*)
			option_value "${1%%=*}" "${1#*=}"
			shift
			;;
		--check) check_only=1 && shift ;;
		--yes | -y) assume_yes=1 && shift ;;
		-h | --help) usage && exit 0 ;;
		*) die "option inconnue : $1 (voir --help)." ;;
		esac
	done
	case $version in
	'' | -* | *[!A-Za-z0-9._/-]*) die "version invalide : « $version » (une étiquette comme $DOKOPILOT_VERSION)." ;;
	esac
	[ -n "${HOME:-}" ] || die "la variable HOME est vide."
	[ -n "$dir" ] || dir=$HOME/DokoPilotV2
	case $dir in
	\~) dir=$HOME ;;
	\~/*) dir=$HOME/${dir#\~/} ;;
	/*) ;;
	*) dir=$(pwd)/$dir ;;
	esac
	if [ -n "$answers" ]; then
		case $answers in
		\~/*) answers=$HOME/${answers#\~/} ;;
		/*) ;;
		*) answers=$(pwd)/$answers ;;
		esac
		if [ ! -f "$answers" ] || [ ! -r "$answers" ]; then die "fichier de réponses illisible : $answers"; fi
		# No question at all: confirmations are answered yes.
		assume_yes=1
	fi
	repo_url=${DOKOPILOT_REPO_URL:-$DEFAULT_REPO_URL}
}

option_value() {
	case $1 in
	--version) version=$2 ;;
	--dir) dir=$2 ;;
	--answers) answers=$2 ;;
	esac
}

# ------------------------------------------------------------------ 1. checks

# os_field KEY: value of KEY in /etc/os-release, without quotes.
os_field() {
	sed -n "s/^$1=//p" /etc/os-release 2>/dev/null | head -n 1 | sed 's/^"\(.*\)"$/\1/'
}

check_system() {
	[ -r /etc/os-release ] || die "système inconnu : /etc/os-release est absent. DokoPilot s'installe depuis Debian, Ubuntu ou Fedora."
	os_id=$(os_field ID)
	os_version=$(os_field VERSION_ID)
	os_name=$(os_field PRETTY_NAME)
	os_major=${os_version%%.*}
	arch=$(uname -m)
	pkg_manager=""
	case $os_id in
	debian)
		case $os_version in
		13) pkg_manager=apt ;;
		esac
		;;
	ubuntu)
		if is_number "$os_major" && [ "$os_major" -ge 24 ]; then pkg_manager=apt; fi
		;;
	fedora)
		if is_number "$os_major" && [ "$os_major" -ge 41 ]; then pkg_manager=dnf; fi
		;;
	esac
	if [ -n "$pkg_manager" ]; then
		ok "Système : ${os_name:-$os_id $os_version}"
	else
		blocker "Système non pris en charge : ${os_name:-$os_id $os_version}. Il faut Debian 13, Ubuntu 24.04 ou plus récent, ou Fedora 41 ou plus récent (Debian 12 fournit Python 3.11, trop ancien pour Ansible 2.21)."
	fi
	case $arch in
	x86_64 | aarch64) ok "Architecture : $arch" ;;
	*) blocker "Architecture non prise en charge : $arch (x86_64 ou aarch64)." ;;
	esac

	ram_mb=$(awk '/^MemTotal:/ { print int($2 / 1024) }' /proc/meminfo 2>/dev/null || echo 0)
	if [ "$ram_mb" -ge "$WORKSTATION_MIN_RAM_MB" ]; then
		ok "Mémoire : $(gigabytes "$ram_mb") Go"
	else
		blocker "Mémoire : $(gigabytes "$ram_mb") Go ; il faut au moins 2 Go."
	fi

	dir_parent=$dir
	while [ ! -d "$dir_parent" ]; do dir_parent=$(dirname "$dir_parent"); done
	disk_gb=$(free_gb "$dir_parent")
	disk_gb=${disk_gb:-0}
	if [ "$disk_gb" -ge "$WORKSTATION_MIN_DISK_GB" ]; then
		ok "Espace libre : $disk_gb Go dans $dir_parent"
	else
		blocker "Espace libre : $disk_gb Go dans $dir_parent ; il faut au moins $WORKSTATION_MIN_DISK_GB Go."
	fi

	if [ "$(id -u)" = 0 ]; then
		blocker "Ce script se lance avec votre compte personnel, pas en root : ce compte devient votre compte de maintenance, et sudo est demandé quand il le faut."
	elif ! command -v sudo >/dev/null 2>&1; then
		blocker "sudo est absent. En root (su -), installez-le et donnez-vous le droit de l'utiliser : $(sudo_hint), puis reconnectez-vous."
	elif sudo -n true 2>/dev/null; then
		ok "sudo : disponible"
	elif id -nG | tr ' ' '\n' | grep -Eqx 'sudo|wheel|admin|dokopilot-maint'; then
		ok "sudo : disponible (votre mot de passe sera demandé)"
	else
		warn "sudo : votre compte ne semble pas autorisé (groupe sudo ou wheel). Si l'installation échoue sur sudo : $(sudo_hint)."
	fi
}

sudo_hint() {
	case ${os_id:-} in
	fedora) printf '%s' "dnf install sudo && usermod -aG wheel $(id -un)" ;;
	*) printf '%s' "apt install sudo && adduser $(id -un) sudo" ;;
	esac
}

is_number() {
	case $1 in
	'' | *[!0-9]*) return 1 ;;
	*) return 0 ;;
	esac
}

# gigabytes MB: "7,8" (French decimal comma).
gigabytes() { awk -v mb="$1" 'BEGIN { printf "%.1f", mb / 1024 }' | tr . ,; }

# free_gb PATH: free space of the file system holding PATH, in GiB (rounded down).
free_gb() { df -Pk "$1" 2>/dev/null | awk 'NR == 2 { print int($4 / 1048576) }'; }

# Packages: the commands DokoPilot needs on this computer, by package manager.
required_packages() {
	case $pkg_manager in
	apt) echo "git python3 python3-venv age openssh-client make curl ca-certificates" ;;
	dnf) echo "git python3 age openssh-clients make curl ca-certificates" ;;
	esac
}

package_installed() {
	# shellcheck disable=SC2016 # ${Status} is a dpkg-query field, not a shell variable
	case $pkg_manager in
	apt) dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q 'install ok installed' ;;
	dnf) rpm -q "$1" >/dev/null 2>&1 ;;
	esac
}

check_packages() {
	missing=""
	[ -n "$pkg_manager" ] || return 0
	for package in $(required_packages); do
		package_installed "$package" || missing="$missing $package"
	done
	missing=${missing# }
	if [ -n "$missing" ]; then
		note "Paquets à installer : $missing"
	else
		ok "Paquets : tous présents"
	fi
	check_python
}

# Python 3.12 or later is required by Ansible 2.21. Debian 12 ships Python 3.11.
check_python() {
	python_problem=""
	if command -v python3 >/dev/null 2>&1; then
		python_version=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "?")
		if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
			ok "Python : $python_version"
			return 0
		fi
		python_problem="Python $python_version"
	elif [ "${os_id:-}" = debian ] && [ "${os_version:-}" = 12 ]; then
		python_problem="Debian 12 fournit Python 3.11"
	fi
	if [ -n "$python_problem" ]; then
		blocker "$python_problem : DokoPilot demande Python 3.12 ou plus récent (Ansible 2.21). Utilisez Debian 13, Ubuntu 24.04 ou plus, ou Fedora 41 ou plus."
	fi
}

# probe HOST: true when https://HOST/ answers, whatever the HTTP status.
probe() {
	if command -v curl >/dev/null 2>&1; then
		code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "https://$1/" 2>/dev/null) || true
		[ -n "$code" ] && [ "$code" != 000 ]
	elif command -v wget >/dev/null 2>&1; then
		# 8: the server answered with an HTTP error, so it is reachable.
		wget -q --spider --timeout=10 --tries=1 "https://$1/" >/dev/null 2>&1 || [ $? -eq 8 ]
	else
		python3 - "$1" <<'EOF' >/dev/null 2>&1
import sys, urllib.error, urllib.request
try:
    urllib.request.urlopen(urllib.request.Request("https://" + sys.argv[1] + "/", method="HEAD"), timeout=10)
except urllib.error.HTTPError:
    pass
EOF
	fi
}

can_probe() {
	command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1 || command -v python3 >/dev/null 2>&1
}

# unreachable HOST...: prints the hosts that do not answer (probed in parallel).
unreachable() {
	mkdir -p "$tmp/probe"
	for host in "$@"; do
		(if probe "$host"; then : >"$tmp/probe/$host"; fi) &
	done
	wait
	for host in "$@"; do
		[ -e "$tmp/probe/$host" ] || printf '%s ' "$host"
	done
	rm -rf "$tmp/probe"
}

repo_host() {
	case $repo_url in
	https://*)
		host=${repo_url#https://}
		host=${host%%/*}
		printf '%s' "${host##*@}"
		;;
	esac
}

# check_network: workstation sites (blocking), then the sites a server on this machine needs.
check_network() {
	network_checked=0
	server_unreachable=""
	if ! can_probe; then
		note "Réseau : non vérifié pour l'instant (curl absent : il sera installé)"
		return 0
	fi
	network_checked=1
	sites="$(repo_host) $WORKSTATION_SITES"
	sites=${sites# }
	# shellcheck disable=SC2086 # word splitting of the site lists is intended
	down=$(unreachable $sites)
	if [ -n "$down" ]; then
		blocker "Réseau : sites injoignables : ${down% }. Vérifiez la connexion à Internet (ou le proxy : variable https_proxy)."
	else
		ok "Réseau : $sites"
	fi
	# The version itself: a missing tag or a private repository fails here, not after the
	# packages are installed (git may be missing at this point: the clone then tells).
	if command -v git >/dev/null 2>&1; then
		if GIT_TERMINAL_PROMPT=0 git ls-remote --exit-code "$repo_url" "refs/tags/$version" "refs/heads/$version" >/dev/null 2>&1 </dev/null; then
			ok "Version $version : disponible"
		else
			blocker "Version $version introuvable sur $repo_url : l'étiquette n'existe pas, ou le dépôt n'est pas public."
		fi
	fi
	# shellcheck disable=SC2086
	server_unreachable=$(unreachable $SERVER_SITES)
	server_unreachable=${server_unreachable% }
}

# Can this machine also be the server ("everything on this machine")? Informative here: the
# assistant checks it again when this mode is chosen.
report_local_mode() {
	reasons=""
	if [ "${os_id:-}" != debian ] || [ "${os_version:-}" != 13 ]; then
		reasons="$reasons; le système n'est pas Debian 13"
	fi
	[ "$arch" = x86_64 ] || reasons="$reasons; l'architecture n'est pas amd64 (x86_64), seule cible de l'image de Dokos"
	if [ "$ram_mb" -lt "$LOCAL_MIN_RAM_MB" ]; then
		reasons="$reasons; mémoire $(gigabytes "$ram_mb") Go, il en faut 8"
	fi
	var_gb=$(free_gb /var)
	var_gb=${var_gb:-0}
	if [ "$var_gb" -lt "$LOCAL_MIN_DISK_GB" ]; then
		reasons="$reasons; $var_gb Go libres dans /var, il en faut au moins $LOCAL_MIN_DISK_GB, 40 conseillés"
	fi
	if [ -n "${server_unreachable:-}" ]; then
		reasons="$reasons; sites injoignables : $server_unreachable"
	fi
	if [ -z "$reasons" ]; then
		checked_sites=""
		if [ "$network_checked" = 1 ]; then checked_sites=", sites du serveur joignables"; fi
		say "  « Tout sur cette machine » : possible (Debian 13 amd64, mémoire et espace suffisants$checked_sites)."
	else
		say "  « Tout sur cette machine » : impossible ici (${reasons#; }). Cette machine peut piloter un serveur distant."
	fi
}

# ------------------------------------------------------------------ 2. packages

# logged COMMAND...: run COMMAND with its output in a log; print the end of it on failure.
logged() {
	if "$@" </dev/null >"$tmp/packages.log" 2>&1; then
		return 0
	fi
	tail -n 25 "$tmp/packages.log" | sed 's/^/    /'
	return 1
}

install_packages() {
	title "2. Paquets du système"
	if [ -z "$missing" ]; then
		ok "Rien à installer"
		return 0
	fi
	confirm "  Installer avec sudo : $missing ?" || die "installation annulée : installez ces paquets vous-même, puis relancez."
	# sudo may ask for the password first, on the terminal; the package manager's own output
	# goes to a log, shown only when something fails.
	# Not « sudo -v »: it asks for the password unless every rule of the account is NOPASSWD.
	sudo true </dev/null || die "sudo refuse : votre compte doit pouvoir utiliser sudo ($(sudo_hint))."
	say "  Installation en cours…"
	case $pkg_manager in
	apt)
		logged sudo apt-get update || die "« apt-get update » a échoué (fin du journal ci-dessus)."
		# shellcheck disable=SC2086 # one word per package
		logged sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends $missing ||
			die "l'installation des paquets a échoué (fin du journal ci-dessus)."
		;;
	dnf)
		# shellcheck disable=SC2086
		logged sudo dnf install -y $missing || die "l'installation des paquets a échoué (fin du journal ci-dessus)."
		;;
	esac
	for package in $missing; do
		package_installed "$package" || die "le paquet $package n'est toujours pas installé."
	done
	ok "Installés : $missing"
}

# ------------------------------------------------------------------ 3. DokoPilot

fetch_repository() {
	title "3. DokoPilot $version"
	if [ -d "$dir/.git" ]; then
		origin=$(git -C "$dir" remote get-url origin 2>/dev/null || true)
		case $(printf '%s' "$origin" | tr '[:upper:]' '[:lower:]') in
		"$(printf '%s' "$repo_url" | tr '[:upper:]' '[:lower:]')" | *dokopilotv2*) ;;
		*) die "« $dir » est une copie d'un autre dépôt (${origin:-sans origine}) : choisissez un autre dossier avec --dir." ;;
		esac
		if [ -n "$(git -C "$dir" status --porcelain --untracked-files=no 2>/dev/null)" ]; then
			# Never mix a version with local changes without saying so: the copy is kept as it is.
			warn "« $dir » contient des fichiers modifiés (git -C $dir status) : la copie reste telle quelle, à $(git -C "$dir" describe --tags --always 2>/dev/null), et n'est pas passée à la version $version. Annulez ces modifications, ou choisissez un autre dossier avec --dir, pour installer la version $version."
			return 0
		fi
		say "  Copie existante dans $dir : passage à la version $version."
		git -C "$dir" fetch --quiet --depth 1 origin "$version" </dev/null ||
			die "impossible de récupérer la version $version depuis $origin."
		git -C "$dir" -c advice.detachedHead=false checkout --quiet --detach FETCH_HEAD </dev/null ||
			die "impossible de passer à la version $version (voir « git -C $dir status »)."
	elif [ -e "$dir" ] && [ -n "$(ls -A "$dir" 2>/dev/null)" ]; then
		die "« $dir » existe déjà et n'est pas une copie de DokoPilot : choisissez un autre dossier avec --dir."
	else
		say "  Récupération de $repo_url dans $dir"
		git -c advice.detachedHead=false clone --quiet --depth 1 --branch "$version" "$repo_url" "$dir" </dev/null ||
			die "récupération impossible depuis $repo_url : la version $version existe-t-elle, et le dépôt est-il public ? (Accès à Internet ou proxy : variable https_proxy.)"
	fi
	ok "DokoPilot $version ($(git -C "$dir" rev-parse --short HEAD)) dans $dir"
}

setup_tools() {
	title "4. Outils de DokoPilot (make setup : Ansible et ses collections dans $dir/.venv)"
	(cd "$dir" && make setup) </dev/null ||
		die "« make setup » a échoué : voir les messages ci-dessus (accès à pypi.org et galaxy.ansible.com ?)."
	[ -x "$dir/.venv/bin/python" ] || die "$dir/.venv/bin/python est introuvable après « make setup »."
}

run_wizard() {
	title "5. Assistant d'installation"
	set -- "$dir/installer/wizard.py"
	if [ -n "$answers" ]; then set -- "$@" --answers "$answers"; fi
	if [ "$assume_yes" = 1 ]; then set -- "$@" --yes; fi
	rm -rf "$tmp"
	trap - EXIT
	# Piped into sh, this script's standard input is the download: the assistant reads the
	# terminal instead.
	if [ "$have_tty" = 1 ]; then
		exec "$dir/.venv/bin/python" "$@" </dev/tty
	fi
	exec "$dir/.venv/bin/python" "$@" </dev/null
}

# ------------------------------------------------------------------ main

main() {
	parse_args "$@"
	have_tty=0
	if (: </dev/tty) 2>/dev/null; then have_tty=1; fi
	blocking=0
	missing=""
	tmp=$(mktemp -d)
	trap 'rm -rf "$tmp"' EXIT

	say "DokoPilot V2 — installation (version $version)"
	title "1. Vérification de cette machine"
	check_system
	check_packages
	check_network
	report_local_mode

	if [ "$check_only" = 1 ]; then
		if [ "$blocking" = 0 ]; then
			if [ -n "$missing" ]; then
				title "Cette machine convient comme poste ; les paquets manquants seront installés."
			else
				title "Cette machine convient comme poste."
			fi
			exit 0
		fi
		title "Cette machine ne convient pas en l'état : corrigez les points marqués ✗."
		exit 1
	fi
	[ "$blocking" = 0 ] || die "cette machine ne convient pas en l'état : corrigez les points marqués ✗, puis relancez."

	install_packages
	check_python
	[ "$blocking" = 0 ] || die "Python 3.12 ou plus récent est nécessaire."
	if [ "$network_checked" = 0 ]; then
		title "Accès au réseau"
		check_network
		[ "$blocking" = 0 ] || die "des sites nécessaires sont injoignables."
	fi
	fetch_repository
	setup_tools
	run_wizard
}

main "$@"
