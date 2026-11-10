// SPDX-License-Identifier: AGPL-3.0-or-later
// Loads the rendered opencode guard (with its policy next to it) against a fake plugin
// context and checks, without opencode: the Git identity given to the agent's shell (V2 hook
// "create.before", V1 "shell.env"), and the mail taint (an allow becomes ask after a mail
// read). Usage: node plugin_check.mjs <workspace>/.opencode/plugins/dokopilot-guard.js <name> <email>
import { pathToFileURL } from "node:url"

const [pluginPath, name, email] = process.argv.slice(2)
const plugin = (await import(pathToFileURL(pluginPath).href)).default
let failures = 0
const check = (condition, label) => {
  console.log(`${condition ? "ok   " : "FAIL "} plugin: ${label}`)
  if (!condition) failures++
}

const hooks = {}
const stored = new Map()
const register = (prefix) => ({
  async hook(hookName, callback) {
    hooks[`${prefix}.${hookName}`] = callback
    return { dispose: async () => {} }
  },
})
await plugin.setup({
  storage: { get: async (key) => stored.get(key), set: async (key, value) => stored.set(key, value) },
  tool: register("tool"),
  permission: register("permission"),
  shell: register("shell"),
})

const identity = (env) =>
  env.GIT_AUTHOR_NAME === name && env.GIT_AUTHOR_EMAIL === email && env.GIT_COMMITTER_NAME === name && env.GIT_COMMITTER_EMAIL === email
const shellEvent = { command: "git commit -m x", cwd: "/", timeout: 0, shell: "/bin/sh", env: { PATH: "/usr/bin" } }
await hooks["shell.create.before"](shellEvent)
check(identity(shellEvent.env) && shellEvent.env.PATH === "/usr/bin", "V2 shell gets the assistant's Git identity")

const v1 = await plugin.server()
const output = { env: {} }
await v1["shell.env"]({ cwd: "/" }, output)
check(identity(output.env), "V1 shell.env gives the assistant's Git identity")

const fresh = { sessionID: "s-fresh", action: "edit", effect: "allow", resources: ["00-Boite-de-reception/x.md"] }
await hooks["permission.evaluate"](fresh)
check(fresh.effect === "allow", "an allowed edit stays allowed in a fresh session")
await hooks["permission.evaluate"]({ sessionID: "s-mail", action: "mail_get_emails_content", effect: "allow", resources: [] })
const tainted = { sessionID: "s-mail", action: "edit", effect: "allow", resources: ["00-Boite-de-reception/x.md"] }
await hooks["permission.evaluate"](tainted)
check(tainted.effect === "ask", "an allowed edit is asked after a mail read")
const denied = { sessionID: "s-mail", action: "shell", effect: "deny", resources: ["git push"] }
await hooks["permission.evaluate"](denied)
check(denied.effect === "deny", "a denied git push stays denied")

process.exit(failures > 0 ? 1 : 0)
