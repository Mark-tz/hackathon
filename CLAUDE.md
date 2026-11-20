# Development Guidelines

## Communication

- Reply in Chinese by default.
- Keep code, identifiers, API names, command names, logs, and technical terms in their original language when appropriate.
- Use English only when explicitly requested or when the output itself must be in English.
- Keep status updates concise and outcome-oriented. Do not narrate every intermediate command or investigation step.

## Core principle

Treat the main session context as scarce, long-lived state.

Keep the main session focused on:
- current goals,
- important decisions,
- architectural constraints,
- implementation state,
- cross-repository contracts,
- blockers,
- concise validation results.

Do not accumulate large intermediate outputs in the main session.

Stable project knowledge should live in Git, repo docs, tracking/decision files, interface contracts, and handoff files rather than only in conversation history.

The main agent acts as the repository owner, integrator, and decision holder. High-volume intermediate investigation should be isolated whenever practical.

## Code exploration

Use Serena symbolic tools as the default approach for understanding and modifying source code.

- Start with symbol overview, symbol lookup, and reference search.
- Prefer `find_symbol`, `find_referencing_symbols`, `find_declaration`, and symbol overview over broad grep for code relationships.
- Do not read entire source files unless symbol-level inspection is insufficient.
- Read only the smallest relevant code region needed for the current task.
- Prefer symbol-level edits when modifying existing functions, classes, or methods.
- Use Grep/Glob mainly for filenames, configuration, text assets, generated files, or when Serena cannot answer the query.
- If Serena fails because the relevant language server or file type is unsupported, fall back to targeted shell/file inspection instead of repeatedly retrying the same failing call.

## Context efficiency

Minimize unnecessary context and tool output.

- Do not scan the whole repository unless explicitly required.
- Do not dump large files, logs, build output, screenshots, or full diffs into the main context.
- Use targeted commands such as `rg`, `head`, `tail`, `sed -n`, filtered logs, and scoped tests.
- Avoid repeatedly reading information already available in the current context, handoff, repo docs, or Git history.
- Prefer incremental exploration:
  architecture → subsystem → symbols → implementation details.
- Do not read entire large Markdown documents when targeted search, headings, or a small line range are sufficient.
- If a command may produce large output, filter it before execution.
- If large raw output is necessary for diagnosis, prefer processing it in a subagent and returning only the conclusion and evidence.

## Subagent usage

Use subagents deliberately to isolate high-volume, parallel, or independently solvable work.

Prefer a subagent for:
- broad codebase exploration across multiple files or directories;
- large logs, test output, build output, or diagnostics;
- browser QA, screenshots, repeated UI inspection, and visual verification;
- reading or comparing multiple large documents;
- root-cause investigations requiring many exploratory steps;
- independent research;
- parallel validation of independent components;
- broad repository searches where the main agent only needs the final conclusion.

Prefer the main agent for:
- small edits in known files or known symbols;
- simple searches or commands requiring only a few tool calls;
- core architectural decisions;
- final integration across findings;
- cross-repository decisions and interface coordination;
- tasks whose steps strongly depend on shared state already present in the main session.

Before starting work likely to require any of the following, consider delegating the investigation first:
- more than a few large files;
- more than about 200 lines of logs or command output;
- screenshots or repeated browser inspection;
- broad repository searches;
- three or more independent investigations.

Do not spawn a subagent merely to avoid one or two cheap tool calls.

When delegating:
- give the subagent a narrow, explicit objective;
- pass only the context it actually needs;
- define clear completion criteria;
- prefer short-lived subagents for context-heavy work;
- avoid overlapping subagents for the same question unless independent verification is genuinely useful.

Subagents should return a concise handback containing only:
- conclusion,
- key evidence,
- relevant file / symbol / log / artifact paths,
- unresolved risks,
- recommended next action.

Do not return raw logs, screenshots, large diffs, or full file contents to the main session unless necessary.

The goal is context isolation and parallelism, not maximizing agent count.

## Task execution

Before editing:

1. Identify the smallest relevant subsystem.
2. Decide whether investigation belongs in the main agent or a subagent.
3. Locate relevant symbols and references.
4. Understand the existing interface and behavior.
5. Form a concrete hypothesis or implementation plan.
6. Make the smallest coherent change.
7. Run targeted validation before broader tests.

For debugging:
- form a hypothesis first;
- gather only the evidence needed to confirm or reject it;
- avoid open-ended exploration in the main session;
- move broad, log-heavy, screenshot-heavy, or multi-path investigation to a short-lived subagent.

Do not re-derive facts already established in the current session or handoff unless repository state indicates they may have changed.

## Multi-repository work

Do not inspect unrelated repositories by default.

Each repo owner should remain focused on its own repository and explicit cross-repository contracts.

When coordination with another repository is required, exchange only:
- interface contracts,
- relevant types or schemas,
- decisions,
- assumptions,
- blockers,
- concise implementation summaries,
- exact evidence paths when useful.

Do not copy large amounts of code, logs, screenshots, or document content between sessions.

For cross-session communication:
- contact the relevant repo owner directly;
- send one concise message whenever possible;
- state:
  - what is needed,
  - why it is needed,
  - the relevant interface or evidence,
  - what is blocked;
- avoid acknowledgement-only messages such as "received" or "ok";
- do not take over another repo's work unless explicitly necessary;
- if the dependency does not block current work, continue with the highest-priority local task.

Keep only final contracts, decisions, blockers, and concise implementation summaries in the long-lived main context, not the full cross-session discussion.

## Commands and testing

Prefer targeted commands over broad commands.

- Run focused tests first.
- Limit verbose command output.
- Use `git diff --stat` or scoped diffs before full diffs.
- Avoid full build/test suites until local changes pass focused validation.
- For large test output, keep only pass/fail counts and relevant failures in the main session.
- Delegate large failure analysis to a subagent when practical.

## Browser and visual QA

Browser QA is especially likely to pollute the main context.

Prefer a short-lived subagent for:
- repeated browser navigation;
- screenshot-heavy verification;
- visual regression inspection;
- console/network inspection;
- repeated UI experiments.

The subagent should return only:
- PASS / FAIL,
- failed checks,
- key textual or numerical evidence,
- screenshot / artifact paths,
- recommended next action.

Do not return screenshots to the main session unless the user explicitly needs to inspect them.

## Documentation and large text

For Markdown, logs, and other non-symbolic text:

- search first;
- read only relevant headings or line ranges;
- avoid whole-document reads unless the task explicitly requires full review;
- for multi-document synthesis, prefer a subagent that returns a compact summary with source paths;
- keep stable project knowledge in repo documentation instead of repeatedly reconstructing it in conversation.

## Session lifecycle

Do not treat a single Claude conversation as permanent project memory.

Stable state belongs in:
- Git,
- project documentation,
- tracking / decision records,
- interface contracts,
- `docs/agent-handoff.md`.

When the main session becomes large or a major milestone finishes:
- consolidate stable decisions into repo files;
- create or refresh `docs/agent-handoff.md`;
- start a new session instead of indefinitely extending the old one.

A handoff should preserve:
- current objective,
- completed work,
- in-progress work,
- blockers,
- important decisions,
- interfaces and assumptions,
- relevant files and symbols,
- test status,
- cross-session agreements,
- next actions.

Keep handoffs concise and execution-oriented. Do not use them as transcript archives.

## Serena fallback

Serena is preferred for source-code exploration, but do not force it when another tool is clearly cheaper or more appropriate.

Use normal shell/file tools for:
- logs,
- configuration files,
- documentation,
- generated artifacts,
- build output,
- simple known-file operations.

If Serena is available and the task concerns source-code structure or symbol relationships, prefer Serena before broad Read/Grep/Bash.

The goal is efficient context use, not maximizing MCP tool calls.