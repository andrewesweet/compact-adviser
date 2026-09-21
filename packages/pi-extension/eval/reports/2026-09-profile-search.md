# Numeric profile search: no recommended change

Keep the shipped profile and hint mode.
The selected candidate did not earn adoption.
It matched the best flat-floor baseline, lost primary-supervision hints, and showed no clear improvement across session types.
This experiment does not establish worker-wide safety or readiness for auto mode.

## Data and labels

The earlier selected dataset contained 100 primary-supervision checkpoints.
It had 98 safe labels, two older-context negatives, and three unfinished but safe rows.
It contained no worker or secondary-supervision examples.
That class imbalance limited what its earlier floor comparisons established.

This experiment combined 135 previous agreements with 120 fresh candidates.
The frozen set contained 255 checkpoints from 107 session groups.
Linked copies stayed in one split.
A session group contains related copies of one conversation.

Fable high and Astra high received identical prompts, with separate, bounded future evidence for labels.
The runtime judge did not receive that future evidence.
Initial labels agreed jointly on phase and safety for 108 of 120 fresh candidates.
A seeded second pass reconsidered eight disagreements and resolved four.
The other eight remained unresolved.

The resulting 247 accepted rows covered 100 session groups:

| Source | Rows | Groups | Safe completion | Unfinished | Older-context negative | Unknown safety |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Primary supervision, Claude | 135 | 4 | 124 | 9 | 2 | 0 |
| Coding workers, Claude | 43 | 42 | 5 | 13 | 6 | 22 |
| Coding workers, Pi | 51 | 38 | 35 | 0 | 0 | 16 |
| Secondary supervision, Pi | 18 | 16 | 12 | 0 | 0 | 6 |
| Total | 247 | 100 | 176 | 22 | 8 | 44 |

Three Claude-worker rows were both unfinished and dependent on older context.
These columns overlap and do not sum to the row count.
Unknown safety is not a negative label.
Auxiliary labels, including task boundaries, required agreement for claims that used them.

The frozen split contained 105 training, 66 validation, and 84 holdout checkpoints.
A holdout is data excluded from fitting and selection.
After unresolved rows were excluded, those counts were 103, 61, and 83.
The primary-supervision holdout contained one previously observed session.
Earlier aggregate comparisons were known, so this was a qualified retrospective holdout, not an independent new session.

## Method

The baseline used shipped behavior at upstream commit `b2a27b5`, after the denominator fix.
All four hosts retained their runtime defaults.
The minimum context, conversation gate, cooldowns, and recovery rules did not change.
The replay measured the score gate, not a complete chronology of runtime suppression.

The headline positive class was completed and safe.
The negative class combined unfinished work and unsafe compaction.
We call this the union definition.
Product truth used safety alone, while contract truth compared safe completion with unfinished work.

The frozen numeric family contained 390 profiles.
It varied coordination weight and decreasing floor endpoints.
Training retained four candidates per weight.
Validation selected among these, the shipped profile, and a separately selected flat-floor baseline.
The flat baseline searched every distinct validation score, plus zero and one, using the shipped weight.

Selection maximized mean recall across session types with positive examples.
It required at least 95% validation union precision, no additional older-context false positives, and no worker precision regression.
Precision measures correct hints among hints with known truth.
Recall measures detected positives among known positives.
The final selection was frozen before holdout scoring.
No question rewrites or model-based reflection calls ran.

The selected policy kept coordination weight 0.5 and used a constant floor of 0.86.
It was identical to the best flat-floor baseline.
This is an evaluated candidate, not a recommended configuration.
On validation, it found 11 of 40 safe completions, versus eight for shipped behavior, with no known false positives.

## Holdout result

The 83 accepted holdout rows contained 57 safe completions, five union negatives, and 21 unknown-safety rows.
All five negatives came from Claude workers.
Only one depended on older context, and it was also unfinished.
Pi workers and both supervision groups supplied no negatives.
One additional Claude-worker candidate remained unresolved.

| Union metric | Shipped | Best flat | Selected |
| --- | ---: | ---: | ---: |
| True positive | 13 | 9 | 9 |
| False positive | 0 | 0 | 0 |
| False negative | 44 | 48 | 48 |
| True negative | 5 | 5 | 5 |
| Precision on known truth | 100% | 100% | 100% |
| Recall | 22.8% | 15.8% | 15.8% |
| Mean recall across session types | 30.1% | 38.6% | 38.6% |
| All hints on accepted rows | 25 | 21 | 21 |
| Hints on unknown safety | 12 | 12 | 12 |
| Agreed task-boundary recall | 4/10 | 0/10 | 0/10 |

The 100% precision values apply only to known truth.
They exclude 12 hints with unknown safety for each policy.
Allowing unresolved and truth-unknown rows either binary label gives worst-case union precision of 52.0% shipped and 42.9% selected.
Those are sensitivity extremes, not confidence intervals.

| Source | Safe completions | Union negatives | Shipped true hints | Selected true hints |
| --- | ---: | ---: | ---: | ---: |
| Primary supervision | 40 | 0 | 7 | 0 |
| Claude workers | 3 | 5 | 0 | 1 |
| Pi workers | 11 | 0 | 4 | 6 |
| Secondary supervision | 3 | 0 | 2 | 2 |

Both policies had zero known false positives in each source.
Shipped Claude-worker precision and selected primary-supervision precision were undefined because neither had a hint with known truth in that source.
The selected policy improved worker recall but removed all primary-supervision true hints.

The paired bootstrap resampled session groups within each source 2,000 times, using seed 7.
Its 95% interval for the change in mean recall was -4.4 to +26.5 percentage points.
The point change was +8.5 points, and 82.5% of resamples had a positive change.
The interval for pooled recall was -13.2 to -1.5 points, around a -7.0-point change.
The primary-supervision group stayed fixed, so these intervals do not estimate uncertainty across new primary-supervision sessions.

Score rankings did not change because the selected weight and questions did not change.
Union average precision was 0.952 and ROC AUC was 0.663 for every policy.
These statistics measure ranking quality across thresholds.
Product average precision and AUC were both 1.000, but product truth had only one negative.
All ranking differences against the best flat baseline were zero.
The legacy `earn.py` comparison also found zero AP and AUC differences.

## Sensitivity and missing evidence

Forty accepted holdout rows had historical Claude threshold inferences.
The other 43 had no usable historical denominator and received the strictest floor.
No inferred threshold was described as observed configuration.
Pi's baseline retained its full-window denominator rather than a Claude threshold convention.

Placing unknown usage at the loose end changed shipped union precision to 92.0% and recall to 40.4%.
The selected constant floor remained at 100% conditional precision and 15.8% recall.
This uncertainty prevents a precise claim about production hint rates.

The spread arm had 47 positives and three negatives, plus all 21 unknown-safety rows.
Shipped recall was 17.0%, versus 4.3% selected.
The difficult arm had ten positives and two negatives.
Its recalls were 50.0% shipped and 70.0% selected.
Precision on this enriched sampling does not estimate ordinary traffic precision.

On agreed non-pivot rows, selected recall was 18.0%.
Four rows had no agreed pivot label and were excluded from that slice.
Unknown labels, scarce older-context negatives, and the single primary-supervision holdout group remain limitations.
Zero errors on one older-context negative cannot establish a low context-loss rate.

## Calls and cost evidence

All 255 checkpoints received valid Jev judgments on `jev-1.13.0`.
There were 256 paid attempts because one invalid development reply needed a retry.
Its original token usage was unavailable and received an explicit estimated $0.01 debit.
The request cache now retains bounded responses and separate accounting for failures.
All 84 holdout judgments succeeded, and none exceeded the production two-second timeout.

| Work | Attempts | Cost evidence |
| --- | ---: | --- |
| Fable labels and second passes | 129 | $49.366160, CLI-reported |
| Astra labels and second passes | 128 | $7.471220, token-price estimate |
| Jev development and holdout | 256 | $0.101729638, input-price proxy and failure debit |
| Question reflection or paid optimizer | 0 | $0 |
| Experiment total | | Approximately $56.94 |

Fable reported 258 input tokens, 2,197,864 cache-creation tokens, and 108,126 output tokens.
Astra reported 1,403,334 input tokens and 18,182 output tokens, including 6,367 reasoning tokens.
Its estimate assumes $5 per million input tokens and $25 per million output tokens.
Jev reported 2,184,039 input and 20,837 output tokens, excluding the one unknown attempt.
Its proxy assumes $0.042 per million input tokens, with no exposed output tariff.
These estimates are not invoices.
Implementation validation costs are separate from the experiment table.

## Recommendation and reproduction

Do not deploy the evaluated 0.86 floor.
Keep the empty `profile` setting and `mode: "hint"`.
The [profile mechanism](../../../../docs/judge-profiles.md) remains available for future evidence-based experiments.
This contribution ships no tuned profile and changes no runtime default.

The [optimization workflow](../optimisation.md) documents replay, agreement, selection, and reporting.
Synthetic tests exercise the complete workflow without network calls.
Private transcripts, labels, session identities, and per-checkpoint results are not published.
The public aggregates alone do not permit independent reproduction of the model judgments.

A follow-up needs genuine Pi-worker negatives and multiple independent supervision sessions.
It also needs better evidence for historical usage and currently unknown safety.
Before auto mode, a separate study must run actual compaction and test continuation, recovery, preserved constraints, and task success.
