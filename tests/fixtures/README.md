# Fixture corpus

`corpus.json` holds 40 hand-labelled claims used to test the verdict rules and to
tune `label_match` in `rules.yaml`.

| Group | Count | Shape |
| --- | --- | --- |
| clean | 25 | 16 browser observations that passed with a matching label, 9 shell results with exit 0 |
| stale by label | 8 | browser observations that passed but read a different label than the doc promised |
| broken | 4 | shell exit 1, shell timeout, shell exit 127, one failed browser step |
| prose | 3 | claims with nothing observable |

Browser items are neutral `BrowserObservation` records (`status`, `observed`,
`screenshot`, `note`). Shell items are shell results (`exit_code`, `timed_out`,
`stdout_tail`). Nothing in this directory is shaped like any browser tool's output.

## Threshold tuning

`python tests/fixtures/tune.py` sweeps `label_match` from 0.50 to 0.95 and prints
a confusion matrix per value. Run on 2026-10-23:

```
threshold  proven->proven  proven->stale  stale->stale  stale->proven
  0.50           25              0             3              5
  0.55           25              0             3              5
  0.60           25              0             3              5
  0.65           25              0             5              3
  0.70           25              0             6              2
  0.75           25              0             7              1
  0.80           25              0             8              0  <- clean
  0.85           24              1             8              0
  0.90           24              1             8              0
  0.95           24              1             8              0
```

Chosen value: **0.80**, recorded in `rules.yaml`. It is the only sweep value with
zero misses. The cases that pin it:

| Item | Label | Score | Doc says | Page says |
| --- | --- | --- | --- | --- |
| stale-b26 | stale | 0.778 | Create project | New project |
| stale-b33 | stale | 0.743 | Enable secret scanning | Enable Secret Protection |
| clean-b12 | proven | 0.848 | Sign in with GitHub | Sign in using GitHub |

Confusion matrix at 0.80 over the whole corpus (rows are hand labels, columns are
rule output):

| | proven | stale | broken | unverifiable |
| --- | --- | --- | --- | --- |
| proven (25) | 25 | 0 | 0 | 0 |
| stale (8) | 0 | 8 | 0 | 0 |
| broken (4) | 0 | 0 | 4 | 0 |
| prose (3) | 0 | 0 | 0 | 3 |

A note on the metric: `token_set_ratio` scores 1.0 whenever one label's tokens are
a subset of the other's, so a page label that carries an extra counter or icon word
("Pull requests 3") still proves a doc that says "Pull requests". The threshold only
matters when both sides carry tokens the other lacks.
