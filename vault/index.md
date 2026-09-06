---
type: index
title: Ante Knowledge Vault
description: The knowledge base an agent reads to warn a Singapore startup founder about obligations that are about to bite.
---

# Ante Knowledge Vault

**Ante** is the second brain for an agent that warns **Singapore startup founders** about obligations
that are about to bite — either because the law moved, or because the startup grew past a
threshold it did not know existed.

Structured as an **[Open Knowledge Format](https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing) v0.1 bundle**:
markdown files with YAML frontmatter, readable by an agent and auditable by a human.

## Folders

| Folder | Who writes it | What it holds |
|---|---|---|
| [rules/](rules/index.md) | human | The curated Singapore rule base. Every figure cited to a primary `.gov.sg` page. |
| [company/](company/index.md) | human | The startup's own facts — Harborlight Analytics, the demo customer |
| [people/](people/index.md) | human | The roster — **synthetic** |
| [counterparties/](counterparties/index.md) | **agent** | Watched entities and their ACRA status |
| alerts/ | **agent** | Decisions raised, with dollar impact and deadline |
| [log.md](log.md) | **agent** | Chronological run history |

## Rules of this vault

1. Every `resource:` is a primary government source — `mom.gov.sg`, `cpf.gov.sg`,
   `iras.gov.sg`, `acra.gov.sg`, `enterprisesg.gov.sg`. Never a blog.
2. `# Next step` in a rule note is **written by a human**. The agent recommends what the
   note says; it does not invent regulatory advice.
3. All person records are synthetic.
4. Links are standard markdown, never `[[wikilinks]]` — see [_SCHEMA.md](_SCHEMA.md).
