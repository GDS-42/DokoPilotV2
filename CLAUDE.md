# DokoPilot V2 — instructions for coding agents

DokoPilot V2 installs, from one configuration file per client, a complete and hardened environment for a French small business: an **unmodified Dokos v5** ERP with its Dokos AI and eTransactions apps, and Forgejo holding the company context (pre-split repositories and rules for the agents), on a self-hosted server in the entrepreneur's name, reachable only through Tailscale, plus the configuration of an agent harness (opencode by default, Claude Code as an option) on each person's computer. A one-command installer (`install.sh` and its French assistant) drives it all, on one machine or a remote server. Everything is infrastructure as code: Ansible roles, Docker Compose stacks, provisioning scripts, context templates, demo data, harness templates. Mistakes can have legal consequences for French businesses (accounting, e-invoicing, cash-register certification): precision beats speed.

Read [`docs/architecture.md`](docs/architecture.md) and [`docs/decisions.md`](docs/decisions.md) before changing anything structural.

## Non-negotiable rules

1. **Upstream untouched.** Dokos, Dodock and the Dokos apps (`dokos_ai`, `etransactions`; `bank` is never installed, D-11), like Forgejo, are consumed as pinned releases and images. Never patch their files, here or in images. Needed changes go upstream (merge requests on gitlab.com/dokos) or into configuration through documented settings.
2. **Certified cash register out of scope.** This repository never installs, configures or modifies `certification_caisse` or point-of-sale objects. The demo and the default production profile have no cash register.
3. **Assistants prepare, humans validate.** An assistant user (the identity a harness uses through MCP) may read and create or update drafts; it never receives submit, cancel, amend or delete rights on accounting, sales, purchase, payment or stock documents, nor System Manager, Administrator or any AI Manager role. The tests in `tests/stack/` prove it; never weaken them.
4. **Dokos is never public.** In production Dokos listens on 127.0.0.1 and is published only through Tailscale. The `lan` network mode exists only for the demo profile and private ranges. Only the SSH maintenance port may be open to the internet.
5. **Maintenance access**: nominative accounts, SSH keys only, sudo, no direct root login, fail2ban. Never add a maintenance account to the `docker` group.
6. **Secrets**: never in Git in clear, never in logs or command lines, never printed by a playbook. They live in `clients/<client>/secrets.yml.age` (age, several recipients) and on the server in root-only files. Every Ansible task that handles a secret has `no_log: true`.
7. **Idempotence**: every role can run again without change; `tests/system/run.sh` fails when a second run reports a change.
8. **Free licences only** (AGPL-3.0-or-later for our files, with an SPDX header; dependencies under OSI licences). No Vagrant (BSL), no non-free images.
9. **French product, English code.** Documentation, configuration comments, messages shown to the integrator or the entrepreneur, and business skills are in French. Code, identifiers, code comments and commit messages are in English.
10. **No agent merges into `main`.** Work on a branch, open a pull request; the project owner merges.
11. **The project owner is the author.** Commits are authored with the owner's Git identity (GDS-42); an agent never sets a Git identity of its own and only adds its `Co-Authored-By` trailer.

## Repository map

| Path | Content |
| --- | --- |
| `install.sh` | One-command installer (`curl … \| sh`): checks the machine, installs packages, clones a tag, runs `make setup` and the assistant |
| `installer/` | French interactive assistant (`wizard.py`, resumable, `--answers`), example answers, secret editor |
| `bin/dokopilot` | Single CLI: `doctor`, `init`, `config`, `bootstrap`, `install`, `check`, `backup`, `restore`, `demo-reset`, `harness`, `sync`, `secrets`, `ssh` |
| `config/` | Commented reference of the client settings and the secrets template |
| `clients/` | One folder per client (ignored by Git except `demo`) |
| `ansible/roles/config` | Merges defaults, profile and client settings into the fact `dp`, validates them; every role reads `dp` only |
| `ansible/roles/*` | System (base, maintenance, ssh, fail2ban, firewall, tailscale, docker, proxy), Dokos (dokos, dokos_config, demo_data, backup), context (forgejo, context) and workstation (harness) roles |
| `ansible/playbooks/` | `site.yml` (everything), `system.yml`, `dokos.yml`, `context.yml`, `bootstrap.yml`, `check.yml`, `backup.yml`, `restore.yml`, `demo-reset.yml`, `harness.yml`, `config.yml` |
| `context/` | Templates of the context repositories (entreprise with its tree and `AGENTS.md`, direction, perso, demo content) |
| `stack/` | Dokos image build (apps pinned) and provisioning scripts run inside the Dokos container |
| `demo/` | Fictitious French company, test data, sample e-invoices and bank statement |
| `harness/` | opencode and Claude Code templates, agents, guard, French skills |
| `vm/` | Local test VM: cloud-init seed and libvirt script |
| `tests/` | `cli/`, `config/`, `harness/`, `backup/` (fast checks; `backup/` runs the backup and restore scripts against a real restic repository with a fake docker), `installer/` (install.sh and the assistant on Debian, Ubuntu, Fedora; local mode; end to end), `context/` (unit checks, and a real Forgejo), `system/` (roles against a Debian 13 systemd container), `stack/` (unit checks; Dokos stack, provisioning, permission tests) |
| `docs/` | Architecture, decisions, installation, local VM, security, operations, demo data, entrepreneur workstation (French) |

## Commands

| Command | Purpose |
| --- | --- |
| `make setup` | Ansible, linters and collections in `.venv` |
| `make lint` | yamllint, ansible-lint (production profile), shellcheck, Python syntax |
| `make test` | Fast tests: CLI and `sync` (`tests/cli`), configuration through the CLI (`tests/config`), harness rendering and guard (`tests/harness`), assistant (`tests/installer/test_*.py`), provisioning unit checks (`tests/context/test_*.py`, `tests/stack/test_*.py`), backup and restore scripts (`tests/backup`, skipped without `restic`) |
| `make test-system` | System roles against a Debian 13 systemd container (Docker) |
| `make test-site` | The remote full installation (site.yml, check, harness, backup) in that container (`DP_TEST_SITE=1`) |
| `make test-installer`, `make test-local-mode` | install.sh and the assistant up to the configuration on Debian, Ubuntu and Fedora; local mode hardening (Docker) |
| `make test-e2e` | The full installation by `install.sh --answers`, everything on one Debian 13 machine (Docker, about 6 GB RAM, under an hour) |
| `tests/context/run.sh` | Forgejo provisioning, rights and context repositories against a real Forgejo (Docker) |
| `make test-stack` | Dokos stack, provisioning, demo data and permission tests (Docker, about 8 GB RAM) |

Run `make lint test` before every pull request, plus `make test-system`, `make test-e2e`, `tests/context/run.sh` or `make test-stack` when you touch those parts (`make ci` runs lint, fast tests and system tests). Never weaken a test, a check or an assertion to make it pass.

There is **no GitHub Actions workflow**: the repository must not use any paid GitHub feature (Actions minutes, Codespaces, Packages, LFS…). Every check runs locally, and the pull request states the commands run and their results.

## Conventions

- Ansible: FQCN modules, `ansible-lint` production profile with zero findings, roles read `dp` (never raw client variables), handlers for restarts, `validate:` on every configuration file that has a checker (sshd, sudoers, nftables, Caddy).
- Shell: `set -euo pipefail`, shellcheck clean. Python: 3.12+ on the workstation (ansible-core 2.21), type hints, standard library first; hooks and scripts shipped to the workstation or the server use the standard library only.
- Pin every external version (image tags or digests, app tags, apt repositories by suite, collections by range) in role defaults, never in tasks.
- When a fact about Dokos, Tailscale or a harness is uncertain, read the upstream source or documentation and cite it in the decision record or a comment.
