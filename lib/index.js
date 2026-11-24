/**
 * `/code-review` bundled skill provider.
 *
 * Registers one packaged skill whose instructions live in `assets/code-review.md`.
 * The body is re-read on every load, so editing the Markdown needs no plugin reload.
 *
 * @module dsh-code-review
 */

import { readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'

/** Cordis plugin name. */
export const name = 'code-review'
/** The skill registry is the only service this plugin contributes to. */
export const inject = ['skills']

const PROVIDER_NAME = 'code-review'
const SKILL_NAME = 'code-review'
const BUNDLED_RANK = 600
const BODY_URL = new URL('../assets/code-review.md', import.meta.url)
const RESOURCE_BASE = {
  kind: 'directory',
  path: fileURLToPath(new URL('../assets/', import.meta.url)),
}
const INVOCATION = { modelInvocable: true, userInvocable: true }
const DESCRIPTION =
  'Review the current branch, a pull request, a revision range, or a path for high-signal bugs and guideline violations. Uses independent parallel reviewers and confidence-based filtering, then reports only verified findings. Use when the user types /code-review or asks for a review of local changes or a PR.'
const WHEN_TO_USE =
  'Use when the user invokes /code-review (or /review), or asks to review the current branch, uncommitted changes, a pull request, a commit range, or specific files before merging.'

const CANDIDATE = {
  name: SKILL_NAME,
  description: DESCRIPTION,
  whenToUse: WHEN_TO_USE,
  invocation: INVOCATION,
  provider: PROVIDER_NAME,
  source: 'bundled',
  resourceBase: RESOURCE_BASE,
  rank: BUNDLED_RANK,
  locator: BODY_URL,
}

/** @type {import('@deepseek-ai/dsh-skill').SkillProvider} */
const provider = {
  name: PROVIDER_NAME,
  list: () => Promise.resolve([CANDIDATE]),
  async get() {
    return {
      name: CANDIDATE.name,
      description: CANDIDATE.description,
      whenToUse: CANDIDATE.whenToUse,
      invocation: CANDIDATE.invocation,
      provider: CANDIDATE.provider,
      source: CANDIDATE.source,
      resourceBase: CANDIDATE.resourceBase,
      content: await readFile(BODY_URL, 'utf8'),
    }
  },
}

/**
 * Register the packaged `code-review` skill on `ctx.skills`.
 * @param {import('@deepseek-ai/cordis').Context} ctx - host context carrying the skill registry.
 */
export function apply(ctx) {
  ctx.skills.registerProvider(() => provider)
}
