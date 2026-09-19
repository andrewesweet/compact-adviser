# Session-grouped datasets and independent labels

These optional tools build private evaluation datasets for Claude Code and Pi. They do not change the runtime judge. Run commands from `packages/pi-extension`.

The tools require the package's existing Node dependencies and Python 3. They add no Python dependencies. Live labelling also requires the Claude and Pi CLIs plus `quota-axi` for quota evidence. Synthetic tests make no model calls.

## Inventory and build

All output must pass `git check-ignore` before the tools write it. Keep real transcripts, manifests, worksheets, labels, provider responses, and per-checkpoint results in `eval/local/`. Output files are created exclusively: an existing file is not overwritten.

Inventory one host's project directories:

```sh
node --import tsx eval/tools/dataset.ts census /path/to/host/projects eval/local/census.json claude
```

The command reads top-level `*.jsonl` files in each project directory. It counts settled turns and replay eligibility. It does not include nested subagent files. A directory name does not prove the session's role. Classify sources explicitly before sampling.

Create a private `eval/local/sources.json` array:

```json
[
  { "host": "claude", "stratum": "coding", "file": "/path/to/claude-session.jsonl" },
  { "host": "pi", "stratum": "coding", "file": "/path/to/pi-session.jsonl" }
]
```

Build a bank of eligible checkpoints:

```sh
node --import tsx eval/tools/dataset.ts build eval/local/sources.json eval/local/bank
```

The builder calls the production snapshot implementations. Eligibility means at least 40k context tokens and more than 20k conversation tokens. These counts do not include runtime cooldown or duplicate-request suppression. Duplicate checkpoint keys within one session produce only one row.

The bank contains `checkpoints.jsonl` and `sessions.json`. Session metadata includes event identities and snapshot hashes. The splitter uses these identities to keep resumed sessions and copied branches together. It also groups identical snapshots across files. This conservative grouping can reduce the number of independent sessions.

## Freeze the split before fitting

Create a private plan with a fixed seed and candidate targets:

```json
{
  "seed": "session-split-v1",
  "targets": {
    "coding": { "train": 24, "validation": 12, "holdout": 12 }
  }
}
```

```sh
python3 eval/tools/split.py eval/local/bank eval/local/plan.json eval/local/manifest.json
```

A holdout is data withheld from fitting and model selection. The splitter assigns whole linked-session groups, stratified by their source types. It assigns approximately half to training, one quarter to validation, and one quarter to holdout. Small strata can lack a validation or holdout group. Do not split a single session to fill that gap.

Within each split, the sampler draws across sessions rather than allowing one long session to dominate. It selects the spread arm first. A separate difficult arm then seeks settled checkpoints with continuation evidence and unresolved-work language. This is a sampling heuristic, not a truth label. If the miner finds too few examples, the output retains a visible deficit.

Half of training and validation targets are reserved for difficult examples. One third of the holdout target is reserved for them. Report spread and difficult arms separately. Precision on an enriched difficult arm does not estimate production precision.

The manifest records the bank hashes and each selected row's group, split, and sampling arm. Materialize only the selected rows by joining manifest ids to the bank:

```sh
python3 eval/tools/materialize.py eval/local/bank/checkpoints.jsonl \
  eval/local/manifest.json eval/local/checkpoints.jsonl
```

If a revised budget requires fewer training rows, `reduce.py` preserves validation and holdout verbatim. Supply per-stratum training targets and the pilot ids to retain:

```sh
python3 eval/tools/reduce.py eval/local/manifest.json eval/local/training-targets.json \
  eval/local/pilot-ids.json eval/local/active-manifest.json
```

The derived manifest records its parent hash and deterministic selection rule. Do not change the selection after reading holdout labels. Keep the holdout results inaccessible to the optimizer until its final candidate is frozen.

For a previously observed corpus, `reuse.py` selects a qualified holdout group by minimum seeded hash. The next group is validation and the others are training:

```sh
python3 eval/tools/reuse.py eval/local/previous eval/local/bank/sessions.json \
  eval/local/reused --seed session-split-v1 --stratum supervision
```

The previous directory needs `checkpoints.jsonl`, `labels-fable.jsonl`, and `labels-astra.jsonl`. The tool retains all phase-and-safety agreements, rather than the first fixed number. Its manifest records the prior-observation caveat. Prior exposure cannot be erased by changing a split label.

## Worksheets and truth

Materialize the selected checkpoint rows in `eval/local/checkpoints.jsonl`. Generate their worksheets:

```sh
node --import tsx eval/tools/dataset.ts worksheets eval/local/checkpoints.jsonl eval/local/worksheet
```

Each worksheet contains the complete bounded judge state and a separate hindsight section. The future section is redacted before clipping. Claude hindsight follows an actual descendant branch, never a sibling rewind. The Pi builder uses the active branch. A marker records truncated future evidence.

The runtime judge never receives hindsight. The labellers receive identical worksheet and prompt bytes, with the same short system instruction. Their tools, project instructions, extensions, and persistent sessions are disabled. They run from an existing scratch directory outside the repository. Only Anthropic and OpenAI receive labelling prompts.

The label rubric extends the existing schema with `context_need: unknown` and nullable `safe_to_compact`. Unknown evidence is not safe evidence. An autonomous worker's next assistant or tool episode can supply hindsight without another user prompt. If that evidence is absent or insufficient, retain unknown safety and label phase independently.

Safety is derived: `older` means false, `unknown` means null, and other context-source labels mean true. A mid-task row can be safe. A completed row can be unsafe. Report these classes separately.

The existing `metrics.py`, `schedule.py`, and `earn.py` assume resolved boolean product labels. Export only rows with known safety for those product reports and report the excluded count. For task-boundary slices, also require an agreed boolean boundary label. Do not cast null to false. Report phase and contract negatives from the full phase-labelled set separately, including unfinished rows whose product safety remains unknown.

## Paid calls and recovery

Put at most 20 selected ids in a JSON array, then prepare prompts without making paid calls:

```sh
python3 eval/tools/label.py eval/local --ids eval/local/batch-ids.json
```

Before paid execution, estimate the whole exercise and obtain the required budget decision. The runner's defaults are a conservative example budget, not provider prices or standing permission to spend:

```sh
python3 eval/tools/label.py eval/local --ids eval/local/batch-ids.json --execute
```

The runner uses Fable high through Claude Code and Astra high through Pi. It reads fresh quota evidence before and after each batch of at most five checkpoint pairs. Its example reserves are eight Claude weekly points, fourteen Fable weekly points, and five Codex weekly points. It refuses missing or stale quota evidence and projections below a 20% weekly remainder. Calls run serially.

Fable dollars come from the CLI. Astra dollars use an explicit assumption of $5 per million input tokens and $25 per million output tokens, including reported reasoning. Cache input receives no discount in that conservative estimate. Provider tokenization differs. These estimates are not invoices.

The default projection reserves $22 for other work and allows at most 180 calls per provider. It projects remaining labels from the greater of the observed mean and the historical per-call allowance. A projection reaching $96.48 stops the runner. Configure the call count and reserve only after recording a revised estimate. If an explicit decision sets a different forecast limit, `--projection-limit` changes that comparison only. Recorded spend plus the reserve still stops at `--allowance`. This distinction does not authorize spending an unused budget margin.

An explicitly approved calibration slice can have its own call caps and dollar allowance:

```sh
python3 eval/tools/label.py eval/local --ids eval/local/calibration-ids.json --execute \
  --calibration-cap-usd 7 --fable-cap 11 --astra-cap 10
```

Calibration is restricted to training rows in the frozen manifest. Caps include existing cached calls. The runner stops after the supplied slice and does not authorize the rest of the exercise. Record measured rates and a new projection before continuing.

Before each request, the runner writes a durable started marker. It streams stdout and stderr to private files, then records the parsed label and usage. A completed response is reused only when its prompt hash matches. Incomplete, failed, malformed, or timed-out calls require reconciliation from their raw files. They are never automatically repeated. A crash can leave an incomplete JSON file, which deliberately stops recovery rather than causing another paid request.

Quota deltas include unrelated activity on shared accounts. Report those deltas separately from per-call token and cost records. A dollar projection does not guarantee that an unusually long individual response cannot exceed its estimate. Inspect actual spend after every batch and stop at the approved ceiling.

## Agreement and adjudication

```sh
python3 eval/tools/collate.py eval/local eval/local/agreement.json
```

Acceptance requires agreement on phase and safety. The output retains disagreements and unpaired rows. An auxiliary field is null when the labellers disagree, rather than silently copying one provider's value. Null safety remains unknown even when both providers agree that evidence is insufficient.

For adjudication, prepare a JSON mapping from each disputed id to its two anonymous previous labels. Use a distinct round name so the original responses remain intact:

```sh
python3 eval/tools/label.py eval/local --ids eval/local/disputed-ids.json \
  --round-id adjudication --rationales eval/local/rationales.json --execute
python3 eval/tools/collate.py eval/local eval/local/adjudication.json --round-id adjudication
```

Both providers receive the same original worksheet and both rationales. Accept only renewed agreement. Preserve unresolved cases and report their effect on the result. An adjudication call counts against the same provider ledger and budget.

## Historical usage evidence

`evidence.py` generalizes the transcript audit used in an earlier denominator comparison. It records model ids, context tokens, and observed compaction events:

```sh
python3 eval/tools/evidence.py eval/local/checkpoints.jsonl eval/local/usage-evidence.json
```

Pre-compaction token counts are not exact configuration thresholds. Keep observed, inferred, and unavailable thresholds distinct.

`compare.ts` re-gates stored answers through the production score and floor functions. Supply a private JSON object keyed by session, with `window`, optional `autoCompactThreshold`, optional `autoCompactEnabled`, and a structural `source` note:

```sh
node --import tsx eval/tools/compare.ts eval/local/checkpoints.jsonl eval/local/results.jsonl \
  eval/local/session-settings.json eval/local/comparison.jsonl
```

This reproduces full-window and threshold-normalized decisions without a network call. For Pi, threshold normalization is a sensitivity analysis, not the current shipped denominator.

If transcript recording was disabled, an optional local MLflow inventory can establish whether a second source exists:

```sh
python3 eval/tools/audit_traces.py eval/local/trace-inventory.json \
  --since 2026-01-01T00:00:00Z --experiment 1
```

The command makes one read-only request to a local server. It retains session identity, working directory, and timestamp, but does not fetch span artifacts. A paginated response is marked incomplete. No matching traces is missing evidence, not evidence that compaction was safe.
