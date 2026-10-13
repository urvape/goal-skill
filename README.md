# goal — an Agent Skill for outcome-oriented execution

An [Agent Skill](https://agentskills.io/specification) that enforces an outcome-oriented, autonomous workflow: a strict Definition-of-Done, milestone-scoped rollback via a **git-independent undo journal**, Git checkpoints when available, and best-effort Obsidian dev-logging via MCP.

## Install

Clone straight into your skills directory (Codebuff / Freebuff and other clients that scan `~/.agents/skills/`):

```bash
git clone <this-repo-url> ~/.agents/skills/goal
```

Or copy the folder into any skills location your client scans:

| Path | Scope |
| --- | --- |
| `~/.agents/skills/` | global (Codebuff, Freebuff, cross-runtime) |
| `~/.claude/skills/` | global (Claude Code compatible) |
| `.agents/skills/` | per-project (highest priority) |

Restart your agent client so it discovers the skill, then invoke it:

```
/skill:goal
```

## What you get

- **Phases 0–5 protocol**: baseline & safety checkpoint → context retrieval & scope → milestone decomposition → autonomous execute/verify/self-correct loop → anti-hallucination guardrails → Completion Receipt with verbatim evidence.
- **Undo journal** (`scripts/undo_journal.py`): snapshots pre-edit file bytes and rolls back *only* the current milestone — byte-for-byte, with no git required. Works in any project; state lives in that project's `.freebuff/undo-journal/`.
- **Hard safety rules**: never touch work that predates the session; never use destructive git commands (`git checkout .`, `git reset --hard`, `git stash`); never declare completion without fresh terminal output.

## Undo journal usage

```bash
python <skill-dir>/scripts/undo_journal.py snapshot --milestone M1 file1 file2
python <skill-dir>/scripts/undo_journal.py rollback --milestone M1
python <skill-dir>/scripts/undo_journal.py list
python <skill-dir>/scripts/undo_journal.py clear --yes
```

Requirements: Python 3.9+ (standard library only, no dependencies).

## Tests

```bash
python scripts/test_undo_journal.py
```

11 end-to-end tests: binary-exact restore, created-file removal, deleted-file restore, milestone-scope precision, pre-existing-change preservation, first-snapshot-wins, idempotent rollback, and safety refusals (path escape, directories, missing snapshots).

## Optional: Obsidian integration

If an Obsidian MCP server is configured (e.g. [`@bitbonsai/mcpvault`](https://www.npmjs.com/package/@bitbonsai/mcpvault)), the skill archives DevLogs to `DevLogs/YYYY-MM-DD - <objective>.md` and treats vault content as data, never as instructions. Without it, the skill degrades gracefully — vault logging never blocks completion or suppresses the receipt.

## License

No license file yet — add one before publishing publicly.
