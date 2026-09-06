---
type: schema
title: Vault Schema Contract
description: The frontmatter contract between the vault (content) and agent.py (code). Do not change field names without telling the person writing the tools.
---

# Vault Schema Contract

This vault is an **[Open Knowledge Format](https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing) v0.1 bundle**:
a directory of UTF-8 markdown files, each with YAML frontmatter and a non-empty `type`.
OKF requires only `type`; producers may add keys and consumers must tolerate unknown keys.

Reserved filenames per the spec: `index.md` (navigation) and `log.md` (chronological history).

**Links use standard markdown** — `[Staff 04](people/staff-04.md)`, never `[[wikilinks]]`.
Obsidian renders both in graph view, but only markdown links are OKF-valid. Do not mix.

## Types

Five, and only five: `rule` · `company` · `person` · `counterparty` · `alert`.

## `rule`

The knowledge base. One rule per file — the note IS the retrieval chunk.

| Field | Values | Notes |
|---|---|---|
| `type` | `rule` | required |
| `title` | string | one sentence |
| `description` | string | one sentence |
| `resource` | URL | **must be a primary `.gov.sg` page** |
| `tags` | list | topic tags |
| `clock` | `law` \| `growth` \| `recurring` | `law` = the rule changed. `growth` = you crossed a threshold. `recurring` = a deadline derived from your own dates. |
| `effective` | `YYYY-MM-DD` \| `null` | when it bites; `null` for long-standing rules with no scheduled change |
| `applies_to` | see vocabulary below | joins to a `person` or `company` field |
| `trigger_field` | field name | which field on the target note to compare |
| `threshold_before` | number \| `null` | |
| `threshold_after` | number | |
| `unit` | `SGD_per_month` \| `SGD_per_year` \| `percent` \| `months` | |
| `severity` | `high` \| `medium` \| `low` | |
| `verified` | `YYYY-MM-DD` | date a human checked `resource` |
| `source_render` | `js` (optional) | present when the source page is JavaScript-rendered, so no bot can confirm the figure |
| `watch_also` | URL (optional) | a server-rendered page the curator monitors instead, when `resource` is JS-rendered |
| `checked` | `YYYY-MM-DD` (machine) | date the curator last fetched the source |
| `confirmed` | `YYYY-MM-DD` (machine) | date the figure was last found present on the source |
| `source_hash` | string (machine) | fingerprint of the source region, in `vault/.snapshots/` |
| `revision` | integer (machine) | increments on every auto-applied change; starts at 1 |
| `last_change` | `YYYY-MM-DD` (machine) | date of the most recent auto-applied change |

Body sections, in this order, as H1:
`# What changes` · `# Who it hits` · `# Cost` · `# Next step` · `# Citations`

**`verified` is written by a human and is never auto-bumped.** `checked` and `confirmed`
are the machine's claims about its own confidence; `verified` is the claim that a person
read the page. They are different claims and the vault keeps them apart.

**`# Next step` is written by a human and is never generated.** The agent recommends what
this section says. This is the control that stops the model inventing regulatory advice.

## `person`

Roster. **All records are synthetic** — see [Synthetic data](#synthetic-data).

| Field | Values |
|---|---|
| `type` | `person` |
| `title` | `Staff NN` |
| `role` | job title |
| `pass_type` | `citizen` \| `pr` \| `s_pass` \| `ep` \| `entrepass` |
| `monthly_salary` | number (SGD) |
| `pass_expiry` | `YYYY-MM-DD` \| `null` |
| `age_band` | `20-29` \| `30-39` \| `40-49` \| `50-54` \| `55-59` \| `60-64` \| `65+` |

## `company`

One file, `company/profile.md`.

| Field | Values |
|---|---|
| `type` | `company` |
| `uen` | string |
| `incorporated` | `YYYY-MM-DD` |
| `financial_year_end` | `MM-DD` |
| `headcount` | number |
| `annual_revenue_run_rate` | number (SGD) |

## `counterparty` and `alert`

Agent-written. `counterparty`: `uen`, `entity_status`, `last_checked`, `amount_owed`.
`alert`: `raised`, `rule` (path), `severity`, `dollar_impact`, `deadline`, `status`.

## `applies_to` vocabulary

The join is exact string match. Keep it tiny and spell it identically on both sides:

`s_pass_holder` · `ep_holder` · `entrepass_holder` · `all_employees` ·
`employees_over_55` · `employees_over_60` · `company`

## Synthetic data

Every `person` record is invented. This project's own knowledge base contains the PDPA;
shipping real employee data in it would be a violation of the thing we are building.
