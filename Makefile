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
	$(VENV)/bin/ansible-galaxy collection install -r ansible/requirements.yml -p .collections
	@bin/dokopilot doctor || true

.PHONY: lint
lint: ## Vérifier YAML, rôles Ansible, scripts shell et Python
	$(VENV)/bin/yamllint -s .
	cd ansible && ANSIBLE_CONFIG=ansible.cfg ../$(VENV)/bin/ansible-lint playbooks roles
	@if command -v shellcheck >/dev/null; then shellcheck $$(git ls-files '*.sh'); else echo "shellcheck absent : contrôle des scripts shell ignoré"; fi
	$(PY) -m py_compile bin/dokopilot

.PHONY: test
test: ## Tests rapides (configuration, CLI), sans serveur
	tests/config/run.sh

.PHONY: test-system
test-system: ## Rôles système contre un conteneur Debian 13 systemd (Docker requis)
	tests/system/run.sh

.PHONY: test-stack
test-stack: ## Pile Dokos, provisionnement et données de test dans Docker (Docker requis, ~8 Go de RAM)
	tests/stack/run.sh

.PHONY: vm
vm: ## Créer la VM locale de démonstration (libvirt/QEMU, Linux) — voir docs/vm-locale.md
	vm/create-libvirt-vm.sh

.PHONY: install
install: ## Installer le client CLIENT (défaut : demo)
	bin/dokopilot install $(CLIENT)
