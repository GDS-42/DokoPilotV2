# SPDX-License-Identifier: AGPL-3.0-or-later
# DokoPilot — workstation targets. `make help` lists them.

SHELL := /bin/bash
VENV ?= .venv
PY := $(VENV)/bin/python
CLIENT ?= demo

.DEFAULT_GOAL := help

.PHONY: help
help: ## Liste des cibles
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

.PHONY: setup
setup: ## Installer Ansible et ses collections dans .venv (poste de l'intégrateur)
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install --quiet --upgrade pip
	$(VENV)/bin/pip install --quiet -r requirements.txt
	ANSIBLE_CONFIG=ansible/ansible.cfg $(VENV)/bin/ansible-galaxy collection install -r ansible/requirements.yml -p .collections
	@bin/dokopilot doctor || true

.PHONY: lint
lint: ## Vérifier YAML, rôles Ansible, scripts shell et Python
	$(VENV)/bin/yamllint -s .
	cd ansible && ANSIBLE_CONFIG=ansible.cfg ../$(VENV)/bin/ansible-lint playbooks roles
	@if command -v shellcheck >/dev/null; then shellcheck $$(git ls-files '*.sh'); else echo "shellcheck absent : contrôle des scripts shell ignoré"; fi
	$(PY) -m py_compile bin/dokopilot
	$(PY) -m compileall -q stack demo harness tests ansible/roles installer

.PHONY: test
test: ## Tests rapides (CLI, configuration, installateur, harnais, scripts de sauvegarde), sans serveur
	$(PY) -m unittest discover -s tests/cli
	$(PY) -m unittest discover -s tests/installer -p 'test_*.py'
	$(PY) -m unittest discover -s tests/context -p 'test_*.py'
	tests/config/run.sh
	tests/harness/run.sh
	tests/backup/run.sh

.PHONY: test-system
test-system: ## Rôles système contre un conteneur Debian 13 systemd (Docker requis)
	tests/system/run.sh

.PHONY: test-e2e
test-e2e: ## Installation complète par install.sh, tout sur la machine, comme sur une VM neuve (Docker, ~6 Go de RAM, 1 h)
	tests/installer/e2e-local.sh

.PHONY: test-site
test-site: ## Installation complète d'un serveur distant par Ansible (site, contrôles, harnais, sauvegarde), conteneur Debian 13 (~6 Go de RAM)
	DP_TEST_SITE=1 tests/system/run.sh

.PHONY: test-installer
test-installer: ## install.sh et l'assistant sur Debian 13, Ubuntu 24.04 et Fedora, jusqu'à la configuration (Docker)
	tests/installer/run.sh

.PHONY: test-local-mode
test-local-mode: ## Mode « tout sur cette machine » : durcissement lancé par le compte lui-même (Docker)
	tests/installer/local-mode.sh

.PHONY: test-stack
test-stack: ## Pile Dokos, provisionnement et données de test dans Docker (Docker requis, ~8 Go de RAM)
	tests/stack/run.sh

.PHONY: vm
vm: ## Créer la VM locale de démonstration (libvirt/QEMU, Linux) — voir docs/vm-locale.md
	vm/create-libvirt-vm.sh

.PHONY: ci
ci: lint test test-system ## Vérifications complètes avant une pull request, sur ce poste (pas de CI GitHub)

.PHONY: install
install: ## Installer le client CLIENT (défaut : demo)
	bin/dokopilot install $(CLIENT)
