#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A fake Forgejo for tests/harness/run.sh: bare repositories served over smart HTTP.

    fake_forgejo.py setup --root DIR --repo-dir REPO --client CLIENT.yml --state DIR [--empty]
        Renders context/ (the templates of the context role, with the demo client's values)
        into bare repositories DIR/<client id>/{entreprise,direction,perso-<id>}.git, one
        first commit by « DokoPilot » each (--empty: entreprise stays empty), and writes the
        fake credential files the harness reads on the "server":
          STATE/forgejo/<id>.json                    {"user", "password", "token"}
          STATE/assistants/<assistant email>.json    {"user", "api_key", "api_secret"} (dirigeant)
          STATE/users.json                           accounts, tokens and repositories they may read

    fake_forgejo.py serve --root DIR --users STATE/users.json --port-file FILE --log FILE
        Serves DIR with `git http-backend` on 127.0.0.1 (port written to FILE). Basic
        authentication with the account's token, as Forgejo does; read only (receive-pack is
        refused); each request is logged as "<user> <method> <path>" (never the token).

Test data only; PyYAML and Jinja2 come with Ansible.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import jinja2
import yaml

EXCLUDE = re.compile(r"^(__pycache__|.*\.pyc|.*~|.*\.swp|\.DS_Store)$")
# Used only when context/ is missing from the repository.
STAND_IN = {
    "entreprise": {
        "AGENTS.md": "# Consignes des agents (substitut de test)\n\nRègles communes.\n",
        "CLAUDE.md": "@AGENTS.md\n",
        ".gitignore": "/direction/\n/perso/\n*.tmp\n",
        "00-Boite-de-reception/README.md": "# Boîte de réception\n",
    },
    "direction": {"AGENTS.md": "# Direction (substitut de test)\n", "CLAUDE.md": "@AGENTS.md\n"},
    "perso": {"AGENTS.md": "# Perso (substitut de test)\n", "CLAUDE.md": "@AGENTS.md\n", "memoire-vive.md": "# Mémoire vive\n"},
}


def token(seed: str) -> str:
    return hashlib.sha1(seed.encode()).hexdigest()


def render_tree(repo_dir: Path, kind: str, values: dict[str, Any], demo: bool) -> dict[str, bytes]:
    """Files of one repository, as the context role renders them (first folder wins)."""
    context = repo_dir / "context"
    if not context.is_dir():
        return {path: text.encode() for path, text in STAND_IN[kind].items()}
    env = jinja2.Environment(keep_trailing_newline=True, undefined=jinja2.ChainableUndefined)
    env.filters["bool"] = lambda value: str(value).lower() in ("true", "1", "yes", "on") if not isinstance(value, bool) else value
    files: dict[str, bytes] = {}
    sources = ([context / "demo" / kind] if demo and kind != "perso" else []) + [context / kind]
    for source in sources:
        if not source.is_dir():
            continue
        for path in sorted(source.rglob("*")):
            if not path.is_file() or any(EXCLUDE.match(part) for part in path.relative_to(source).parts):
                continue
            relative = str(path.relative_to(source))
            name = relative[:-3] if relative.endswith(".j2") else relative
            if name in files:
                continue
            if relative.endswith(".j2"):
                files[name] = env.from_string(path.read_text(encoding="utf-8")).render(**values).encode()
            else:
                files[name] = path.read_bytes()
    return files


def git(*args: str, cwd: Path | None = None) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_AUTHOR_NAME="DokoPilot", GIT_AUTHOR_EMAIL="dokopilot@demo.dokopilot.internal",
               GIT_COMMITTER_NAME="DokoPilot", GIT_COMMITTER_EMAIL="dokopilot@demo.dokopilot.internal",
               GIT_CONFIG_NOSYSTEM="1", HOME=str(cwd or "/"))
    subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True)


def bare_repo(root: Path, org: str, name: str, files: dict[str, bytes] | None) -> None:
    bare = root / org / f"{name}.git"
    bare.parent.mkdir(parents=True, exist_ok=True)
    git("init", "--quiet", "--bare", "--initial-branch=main", str(bare))
    if not files:
        return
    with tempfile.TemporaryDirectory() as work:
        tree = Path(work)
        git("init", "--quiet", "--initial-branch=main", cwd=tree)
        for relative, content in files.items():
            target = tree / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        git("add", "-A", cwd=tree)
        git("commit", "--quiet", "-m", "Contexte initial préparé par DokoPilot", cwd=tree)
        git("push", "--quiet", str(bare), "main", cwd=tree)


def setup(args: argparse.Namespace) -> int:
    client = yaml.safe_load(Path(args.client).read_text(encoding="utf-8"))
    repo_dir, root, state = Path(args.repo_dir), Path(args.root), Path(args.state)
    org = client["client"]["id"]
    people = client["users"]["people"]
    for person in people:
        person.setdefault("assistant", True)
        person.setdefault("last_name", "")
        person.setdefault("assistant_email", f"assistant.{person['id']}@{person['email'].split('@')[1]}")
    dirigeant = next(p for p in people if p["role"] == "dirigeant")
    defaults = yaml.safe_load((repo_dir / "ansible/roles/context/defaults/main.yml").read_text(encoding="utf-8")) \
        if (repo_dir / "ansible/roles/context/defaults/main.yml").exists() else {}
    dp = {"client": {"country": "France", "currency": "EUR", "fiscal_year_start": "01-01", "siren": "", **client["client"]},
          "context": {"direction": True}, "users": {"people": people, "dirigeant": dirigeant}}
    values = {"dp": dp, "person": {}, "forgejo_assistant_prefix": "assistant-", "context_company": client["client"]["company_name"],
              "context_org": org, "context_people": people, "context_dirigeant": dirigeant,
              "context_forgejo_url": "http://forgejo.test:3000/", "context_dokos_link": "http://dokos.test",
              "context_date": date.today().isoformat(), "context_mail": False, "context_calendar": False,
              "context_functions": defaults.get("context_functions", [])}
    demo = bool(client.get("demo", {}).get("load_test_data", False))
    bare_repo(root, org, "entreprise", None if args.empty else render_tree(repo_dir, "entreprise", values, demo))
    bare_repo(root, org, "direction", render_tree(repo_dir, "direction", values, demo))
    users: dict[str, Any] = {}
    (state / "forgejo").mkdir(parents=True, exist_ok=True)
    (state / "assistants").mkdir(parents=True, exist_ok=True)
    for person in people:
        bare_repo(root, org, f"perso-{person['id']}", render_tree(repo_dir, "perso", {**values, "person": person}, demo))
        secret = token(f"{org}-{person['id']}-token")
        repos = [f"{org}/entreprise", f"{org}/perso-{person['id']}"] + ([f"{org}/direction"] if person["role"] == "dirigeant" else [])
        users[person["id"]] = {"token": secret, "repos": repos}
        credentials = {"user": person["id"], "password": f"fake-forgejo-Pa55-{person['id']}", "token": secret}
        (state / "forgejo" / f"{person['id']}.json").write_text(json.dumps(credentials), encoding="utf-8")
    assistant = {"user": dirigeant["assistant_email"], "api_key": "fakeapikey0123456789", "api_secret": "fakeapisecret9876543210"}
    (state / "assistants" / f"{dirigeant['assistant_email']}.json").write_text(json.dumps(assistant), encoding="utf-8")
    (state / "users.json").write_text(json.dumps(users, indent=2), encoding="utf-8")
    return 0


class Handler(BaseHTTPRequestHandler):
    root: Path
    users: dict[str, Any]
    log_file: Path

    def log_message(self, *_: Any) -> None:  # quiet
        return

    def record(self, user: str) -> None:
        with open(self.log_file, "a", encoding="utf-8") as handle:
            handle.write(f"{user} {self.command} {self.path}\n")

    def refuse(self, code: int, message: str, challenge: bool = False) -> None:
        self.send_response(code)
        if challenge:
            self.send_header("WWW-Authenticate", 'Basic realm="Gitea"')
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(message.encode())

    def body(self) -> bytes:
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            data = b""
            while True:
                size = int(self.rfile.readline().strip(), 16)
                if size == 0:
                    self.rfile.readline()
                    return data
                data += self.rfile.read(size)
                self.rfile.readline()
        return self.rfile.read(int(self.headers.get("Content-Length", "0") or 0))

    def handle_git(self) -> None:
        path, _, query = self.path.partition("?")
        match = re.match(r"^/([^/]+/[^/]+)\.git(/.*)$", path)
        if not match:
            self.refuse(404, "not found")
            return
        if "receive-pack" in path or "service=git-receive-pack" in query:
            self.refuse(403, "read only")
            return
        user = ""
        header = self.headers.get("Authorization", "")
        if header.startswith("Basic "):
            name, _, secret = base64.b64decode(header[6:]).decode().partition(":")
            if name in self.users and self.users[name]["token"] == secret:
                user = name
        self.record(user or "-")
        if not user:
            self.refuse(401, "authentication required", challenge=True)
            return
        if match.group(1) not in self.users[user]["repos"]:
            self.refuse(404, "repository not found")
            return
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "GIT_PROJECT_ROOT": str(self.root), "GIT_HTTP_EXPORT_ALL": "1",
               "PATH_INFO": path, "QUERY_STRING": query, "REQUEST_METHOD": self.command, "REMOTE_USER": user,
               "REMOTE_ADDR": "127.0.0.1", "CONTENT_TYPE": self.headers.get("Content-Type", ""),
               "GIT_PROTOCOL": self.headers.get("Git-Protocol", ""), "HTTP_CONTENT_ENCODING": self.headers.get("Content-Encoding", "")}
        data = self.body() if self.command == "POST" else b""
        env["CONTENT_LENGTH"] = str(len(data))
        result = subprocess.run(["git", "http-backend"], input=data, env=env, capture_output=True)
        head, _, payload = result.stdout.partition(b"\r\n\r\n")
        status, headers = 200, []
        for line in head.decode("latin-1").split("\r\n"):
            key, _, value = line.partition(":")
            if key.lower() == "status":
                status = int(value.strip().split()[0])
            elif key:
                headers.append((key, value.strip()))
        self.send_response(status)
        for key, value in headers:
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = handle_git
    do_POST = handle_git


def serve(args: argparse.Namespace) -> int:
    Handler.root = Path(args.root)
    Handler.users = json.loads(Path(args.users).read_text(encoding="utf-8"))
    Handler.log_file = Path(args.log)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    Path(args.port_file).write_text(str(server.server_address[1]), encoding="utf-8")
    server.serve_forever()
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sp = sub.add_parser("setup")
    sp.add_argument("--root", required=True)
    sp.add_argument("--repo-dir", required=True)
    sp.add_argument("--client", required=True)
    sp.add_argument("--state", required=True)
    sp.add_argument("--empty", action="store_true")
    sp.set_defaults(func=setup)
    sp = sub.add_parser("serve")
    sp.add_argument("--root", required=True)
    sp.add_argument("--users", required=True)
    sp.add_argument("--port-file", required=True)
    sp.add_argument("--log", required=True)
    sp.set_defaults(func=serve)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
