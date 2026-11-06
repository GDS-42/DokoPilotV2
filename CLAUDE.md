# DokoPilot V2 — instructions for coding agents

DokoPilot V2 installs, from one configuration file per client, a complete and hardened environment for a French small business: an **unmodified Dokos v5** ERP with its Dokos AI, eTransactions and bank apps on a self-hosted server in the entrepreneur's name, reachable only through Tailscale, plus the configuration of an agent harness (opencode by default, Claude Code as an option) on the entrepreneur's computer. Everything is infrastructure as code: Ansible roles, a Docker Compose stack, provisioning scripts, demo data, harness templates. Mistakes can have legal consequences for French businesses (accounting, e-invoicing, cash-register certification): precision beats speed.

Read [`docs/architecture.md`](docs/architecture.md) and [`docs/decisions.md`](docs/decisions.md) before changing anything structural.

## Non-negotiable rules

1. **Upstream untouched.** Dokos, Dodock and the Dokos apps (`dokos_ai`, `etransactions`, `bank`) are consumed as pinned releases and images. Never patch their files, here or in images. Needed changes go upstream (merge requests on gitlab.com/dokos) or into configuration through documented settings.
2. **Certified cash register out of scope.** This repository never installs, configures or modifies `certification_caisse` or point-of-sale objects. The demo and the default production profile have no cash register.
3. **Assistants prepare, humans validate.** An assistant user (the identity a harness uses through MCP) may read and create or update drafts; it never receives submit, cancel, amend or delete rights on accounting, sales, purchase, payment or stock documents, nor System Manager, Administrator or any AI Manager role. The tests in `tests/stack/` prove it; never weaken them.
4. **Dokos is never public.** In production Dokos listens on 127.0.0.1 and is published only through Tailscale. The `lan` network mode exists only for the demo profile and private ranges. Only the SSH maintenance port may be open to the internet.
5. **Maintenance access**: nominative accounts, SSH keys only, sudo, no direct root login, fail2ban. Never add a maintenance account to the `docker` group.
6. **Secrets**: never in Git in clear, never in logs or command lines, never printed by a playbook. They live in `clients/<client>/secrets.yml.age` (age, several recipients) and on the server in root-only files. Every Ansible task that handles a secret has `no_log: true`.
7. **Idempotence**: every role can run again without change; `tests/system/run.sh` fails when a second run reports a change.
8. **Free licences only** (AGPL-3.0-or-later for our files, with an SPDX header; dependencies under OSI licences). No Vagrant (BSL), no non-free images.
9. **French product, English code.** Documentation, configuration comments, messages shown to the integrator or the entrepreneur, and business skills are in French. Code, identifiers, code comments and commit messages are in English.
10. **No agent merges into `main`.** Work on a branch, open a pull request; the project owner merges.

## Repository map

| Path | Content |
| --- | --- |
| `bin/dokopilot` | Single CLI: `init`, `config`, `bootstrap`, `install`, `check`, `backup`, `restore`, `demo-reset`, `harness`, `secrets`, `ssh` |
| `config/` | Commented reference of the client settings and the secrets template |
| `clients/` | One folder per client (ignored by Git except `demo`) |
| `ansible/roles/config` | Merges defaults, profile and client settings into the fact `dp`, validates them; every role reads `dp` only |
| `ansible/roles/*` | System (base, maintenance, ssh, firewall, tailscale, docker, proxy) and Dokos (dokos, dokos_config, demo_data, backup) roles |
| `ansible/playbooks/` | `site.yml` (everything), `bootstrap.yml`, `check.yml`, `backup.yml`, `restore.yml`, `demo-reset.yml`, `harness.yml`, `config.yml` |
| `stack/` | Dokos image build (apps pinned) and provisioning scripts run inside the Dokos container |
| `demo/` | Fictitious French company, test data, sample e-invoices and bank statement |
| `harness/` | opencode and Claude Code templates, agents, guard, French skills |
| `vm/` | Local test VM: cloud-init seed and libvirt script |
| `tests/` | `config/` (fast checks), `system/` (roles against a Debian 13 systemd container), `stack/` (Dokos stack, provisioning, permission tests) |
| `docs/` | Architecture, decisions, installation, local VM, security, operations, demo data, entrepreneur workstation (French) |

## Commands

| Command | Purpose |
| --- | --- |
| `make setup` | Ansible, linters and collections in `.venv` |
| `make lint` | yamllint, ansible-lint (production profile), shellcheck, Python syntax |
| `make test` | Fast tests: CLI (`tests/cli`), configuration through the CLI (`tests/config`), harness rendering and guard (`tests/harness`) |
| `make test-system` | System roles against a Debian 13 systemd container (Docker); `DP_TEST_SITE=1` adds the full installation (site.yml, check, harness, backup) |
| `make test-stack` | Dokos stack, provisioning, demo data and permission tests (Docker, about 8 GB RAM) |

Run `make lint test` before every pull request, plus `make test-system` or `make test-stack` when you touch those parts (`make ci` runs lint, fast tests and system tests). Never weaken a test, a check or an assertion to make it pass.

There is **no GitHub Actions workflow**: the repository is private and must not use any paid GitHub feature (Actions minutes, Codespaces, Packages, LFS…). Every check runs locally, and the pull request states the commands run and their results.

## Conventions

- Ansible: FQCN modules, `ansible-lint` production profile with zero findings, roles read `dp` (never raw client variables), handlers for restarts, `validate:` on every configuration file that has a checker (sshd, sudoers, nftables, Caddy).
- Shell: `set -euo pipefail`, shellcheck clean. Python: 3.12+ on the workstation (ansible-core 2.21), type hints, standard library first; hooks and scripts shipped to the workstation or the server use the standard library only.
- Pin every external version (image tags or digests, app tags, apt repositories by suite, collections by range) in role defaults, never in tasks.
- When a fact about Dokos, Tailscale or a harness is uncertain, read the upstream source or documentation and cite it in the decision record or a comment.
