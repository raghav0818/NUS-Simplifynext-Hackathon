---
type: index
title: Rule Index
description: One row per rule. The agent reads this first to orient, then opens only the notes it needs.
---

# Rule Index

Read this to orient. Open the individual note for the full text, cost and next step.

| Rule | Clock | Effective | Applies to | Severity |
|---|---|---|---|---|
| [GST registration threshold](gst-registration-threshold.md) | growth | on crossing | company | high |
| [S Pass qualifying salary](s-pass-qualifying-salary-2027.md) | law | 2027-01-01 | s_pass_holder | high |
| [EP qualifying salary + COMPASS](ep-qualifying-salary-2027.md) | law | 2027-01-01 | ep_holder | high |
| [CPF Ordinary Wage ceiling](cpf-ow-ceiling-2026.md) | law | 2026-01-01 | all_employees | medium |
| [CPF senior worker rates](cpf-senior-worker-rates-2027.md) | law | 2027-01-01 | employees_over_55 | medium |
| [ACRA annual return](acra-annual-return.md) | recurring | FYE + 7 months | company | medium |

## The two clocks

- **`law`** — the rule moved. Same company, new number.
- **`growth`** — the company moved. Same rule, and you just crossed into it.
- **`recurring`** — neither moved; a date derived from your own FYE arrived.

All figures verified against primary `.gov.sg` sources on 2026-09-04.
