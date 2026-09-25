# Display Blocks Demo (`_display`)

A miniature "error journal" Executa plugin demonstrating the **verbatim
display channel**: tool-result blocks that the Anna host UI renders
**byte-for-byte**, instead of letting the model paraphrase them.

Design doc: `matrix-nexus/docs/design/executa-display-blocks.md`.

## The problem this solves

Normally a tool result becomes a ToolMessage that the LLM re-synthesizes into
prose. Wording, ordering, and whole fields are probabilistic — instructions
like *"print the `headline` field verbatim"* in `system_prompt_addendum` or
SKILL.md are honored inconsistently (and not at all when the app isn't
`#`-mentioned). If your app's core value is a specific sentence — here,
*"this is the 6th time you have hit this error"* — that's not good enough.

## The contract

Put presentation-critical content under the reserved top-level `_display`
key of the tool result:

```json
{
  "fingerprint": "sha256:71bf...",
  "occurrence_count": 6,
  "fix_steps": ["...", "..."],

  "_display": {
    "blocks": [
      { "type": "markdown", "text": "📒 **This is the 6th time you have hit this** — first seen 20 Aug." },
      { "type": "markdown", "text": "_Tell me if this fixes it and it goes in your logbook._" }
    ]
  }
}
```

What happens:

| Consumer | Sees |
|---|---|
| **User (chat UI)** | Blocks rendered as attributed cards at the tool-call position — verbatim, in order, 100% of the time |
| **Model** | `_display` replaced with a note: *"[2 blocks already rendered verbatim — do not restate...]"* plus short previews, so its surrounding prose stays coherent |
| **Structured fields** | Delivered to the model unchanged — it still reasons about and narrates the diagnosis |

Rules:

- 1–8 blocks, ≤ 4000 chars each, ≤ 16000 chars total; v1 type is
  `"markdown"` only.
- Over-limit or unknown types ⇒ the whole `_display` is dropped (with a
  `_display_rejected` reason visible in the tool trace) — never silently
  truncated.
- **Omit-if-null is just code**: this demo only emits the follow-up block
  from the 3rd occurrence onward (`FOLLOW_UP_THRESHOLD`). No prompt logic.

## What the demo does

- `diagnose_error(error_text)` — fingerprints the error, counts occurrences
  in an in-memory journal, returns a structured diagnosis **plus** a verbatim
  headline (and, from the 3rd hit, a verbatim follow-up).
- `journal_stats()` — one summary block.

Counts are in-memory and reset when the plugin restarts; a real app would use
Anna Persistent Storage (see [../storage-notebook](../storage-notebook)).

## Try it

```bash
cd examples/python/display-blocks-demo
pnpm anna-app dev
```

Then in the harness / chat, paste the same error three times:

```
Traceback (most recent call last):
  File "app.py", line 3, in <module>
    import requests
ModuleNotFoundError: No module named 'requests'
```

Watch the sequence:

1. **1st paste** — headline card: *"First time in your journal"*. No follow-up.
2. **2nd paste** — *"This is the 2nd time you have hit this"*. Still no follow-up.
3. **3rd paste** — *"This is the 3rd time..."* **plus** the follow-up card.

Every card is byte-identical to what the plugin computed — try the same test
without `_display` (move the strings into a normal field and ask the model to
print them "verbatim") to see the difference: rewording, dropped fields, and
invented content, varying paste to paste.

## When to use which channel

| Need | Channel |
|---|---|
| Exact sentence must reach the user | `_display` blocks |
| Data the model should reason about / summarize | Normal result fields |
| Behavioral guidance ("call X first, then Y") | `system_prompt_addendum` / SKILL.md |
| Rich interactive UI | App UI bundle (`ui.views`) |
