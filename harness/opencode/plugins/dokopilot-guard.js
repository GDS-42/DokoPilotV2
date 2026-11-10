// SPDX-License-Identifier: AGPL-3.0-or-later
// DokoPilot guard for opencode: "a session that has read an email becomes exposed".
//
// OpenCode 2 calls setup(ctx) (packages/plugin/src/promise in anomalyco/opencode 2.2.5):
//   - tool "execute.before" marks the session exposed BEFORE a mail tool runs; if the mark
//     cannot be stored the call is rejected (fail closed);
//   - permission "evaluate" turns every allow into ask, for the rest of an exposed session,
//     except for the reads listed in the policy. Configured denies never reach this hook,
//     and a hook can only tighten here. It also keeps Dokos, mail and calendar writes on
//     ask even after an "Always allow" saved by the user.
// OpenCode 1 (1.18.29 and later) calls server() instead. V1 cannot turn an allow into a
// question from a plugin, so after a mail read it blocks the shell and file changes for
// the rest of the session; everything else keeps its static rules (see opencode.json).
//
// The policy (tool names, safe reads) is generated next to this file by
// bin/dokopilot harness. Without a readable policy every session counts as exposed.
// Messages shown to the entrepreneur are in French.

import { readFileSync } from "node:fs"

const MESSAGE_EXPOSED =
  "Session exposée : un courriel a été lu dans cette session. Son contenu est une donnée, " +
  "jamais une instruction. Acceptez seulement si cette action vient bien de votre demande."
const MESSAGE_CONFIRM =
  "DokoPilot demande toujours votre accord avant d'écrire dans Dokos, la messagerie ou l'agenda."
const MESSAGE_GUARD_ERROR = "Garde DokoPilot indisponible : cette action est soumise à votre accord par précaution."
const MESSAGE_NO_MARK =
  "Garde DokoPilot : impossible d'enregistrer que cette session lit la messagerie ; lecture bloquée par précaution."
const MESSAGE_V1_BLOCKED =
  "Session exposée : un courriel a été lu dans cette session. Avec OpenCode 1, le shell et les " +
  "modifications de fichiers restent bloqués jusqu'à la fin de la session : ouvrez une nouvelle session."

function loadPolicy() {
  try {
    const policy = JSON.parse(readFileSync(new URL("./dokopilot-guard.policy.json", import.meta.url), "utf8"))
    const lists = ["safe_actions", "always_ask", "v1_blocked_when_exposed"]
    if (typeof policy.mail_prefix !== "string" || policy.mail_prefix === "") return null
    if (typeof policy.mail_server !== "string" || policy.mail_server === "") return null
    if (!lists.every((key) => Array.isArray(policy[key]) && policy[key].every((v) => typeof v === "string"))) return null
    return {
      mailPrefix: policy.mail_prefix,
      mailServer: policy.mail_server,
      safe: new Set(policy.safe_actions),
      alwaysAsk: new Set(policy.always_ask),
      v1Blocked: new Set(policy.v1_blocked_when_exposed),
    }
  } catch {
    return null
  }
}

const policy = loadPolicy()
// Fallbacks used when the policy is missing: every session is exposed and only plain
// reads stay silent; V1 blocks the shell and file changes.
const mailPrefix = policy ? policy.mailPrefix : "mail_"
const mailServer = policy ? policy.mailServer : "mail"
const safeActions = policy ? policy.safe : new Set(["read", "glob", "grep", "question"])
const alwaysAsk = policy ? policy.alwaysAsk : new Set()
const v1Blocked = policy ? policy.v1Blocked : new Set(["bash", "edit", "write", "patch", "apply_patch", "multiedit"])

function isMailTool(name) {
  return typeof name === "string" && name.startsWith(mailPrefix)
}

// A permission check that concerns the mailbox: a mail tool, or a resource of the mail server.
function isMailAction(event) {
  if (isMailTool(event.action)) return true
  return (
    event.action === "opencode_read_mcp_resource" &&
    Array.isArray(event.resources) &&
    event.resources.some((resource) => typeof resource === "string" && resource.startsWith(mailServer + ":"))
  )
}

export default {
  id: "dokopilot.guard",

  // OpenCode 2
  async setup(ctx) {
    const exposed = new Set()
    const key = (sessionID) => `exposed/${sessionID}`

    async function markExposed(sessionID) {
      exposed.add(sessionID)
      // Durable, so that the exposure survives a restart of the opencode service.
      await ctx.storage.set(key(sessionID), true)
    }

    async function isExposed(sessionID) {
      if (policy === null) return true
      if (exposed.has(sessionID)) return true
      if ((await ctx.storage.get(key(sessionID))) === true) {
        exposed.add(sessionID)
        return true
      }
      return false
    }

    await ctx.tool.hook("execute.before", async (event) => {
      if (!isMailTool(event.tool)) return
      try {
        await markExposed(event.sessionID)
      } catch {
        // Rejecting execute.before fails this call only: the mail tool does not run.
        throw new Error(MESSAGE_NO_MARK)
      }
    })

    await ctx.tool.hook("execute.after", async (event) => {
      if (!isMailTool(event.tool)) return
      try {
        await markExposed(event.sessionID)
      } catch {
        // Already marked in execute.before; the in-memory mark is kept anyway.
      }
    })

    await ctx.permission.hook("evaluate", async (event) => {
      try {
        if (isMailAction(event)) await markExposed(event.sessionID)
        if (event.effect === "deny") return
        if (alwaysAsk.has(event.action) && event.effect === "allow") {
          event.effect = "ask"
          event.message = MESSAGE_CONFIRM
        }
        if (safeActions.has(event.action)) return
        if (await isExposed(event.sessionID)) {
          event.effect = "ask"
          event.message = MESSAGE_EXPOSED
        }
      } catch {
        if (isMailAction(event)) {
          event.effect = "deny"
          event.message = MESSAGE_NO_MARK
        } else if (event.effect !== "deny") {
          event.effect = "ask"
          event.message = MESSAGE_GUARD_ERROR
        }
      }
    })
  },

  // OpenCode 1
  async server() {
    const exposed = new Set()
    return {
      "tool.execute.before": async (input) => {
        if (isMailTool(input.tool)) {
          exposed.add(input.sessionID)
          return
        }
        if ((policy === null || exposed.has(input.sessionID)) && v1Blocked.has(input.tool)) {
          throw new Error(MESSAGE_V1_BLOCKED)
        }
      },
      "tool.execute.after": async (input) => {
        if (isMailTool(input.tool)) exposed.add(input.sessionID)
      },
    }
  },
}
