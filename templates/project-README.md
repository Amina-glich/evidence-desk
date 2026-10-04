# Evidence Desk project

This project compares research papers. Every finding points to a source, a
page and an exact quotation, so it can be checked against the paper.

## Where things go

| Location | Written by | Contents |
|---|---|---|
| `inbox/` | You | The papers to compare, as PDF files. Upload them here. |
| `sources/` | Evidence Desk | One folder per registered paper (`S1`, `S2`, …). Do not edit. |
| `evidence/` | The agent | One file per source with findings, page numbers and quotations. |
| `desk.json` | The agent | The research question and which sources are compared. |
| `synthesis.md` | The agent | The research brief. |
| `exports/` | Evidence Desk | The comparison CSV and comparison view. Regenerated; do not edit. |
| `library/` | Evidence Desk | Papers you chose to save from arXiv searches: bibliographic metadata, not evidence. |

## What the statuses mean

- **Reported**: supported by a page number and an exact quotation from the source.
- **Not reported**: checked in the available source material and absent.
- **Not assessed**: not checked yet.

A finding is never filled in from memory or guessed. If the paper does not
say it, the finding stays Not reported or Not assessed.
