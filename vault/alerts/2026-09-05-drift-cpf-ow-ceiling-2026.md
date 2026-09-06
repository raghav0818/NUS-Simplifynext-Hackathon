---
type: alert
raised: '2026-09-05'
rule: rules/cpf-ow-ceiling-2026.md
severity: medium
status: needs_human_check
drift: unverifiable
---

# What happened

`unverifiable` checking [CPF Ordinary Wage ceiling rises from S$7,400 to S$8,000](rules/cpf-ow-ceiling-2026.md) against https://www.cpf.gov.sg/service/article/what-is-the-ordinary-wage-ow-ceiling

page never mentions ['Ordinary', 'Wage', 'ceiling']; likely JS-rendered

# Next step

A human must open the source and re-confirm the figure, then update `verified:` in the rule note. The agent may not edit rules/.
