# Evidence Desk project

This project compares research papers. Every finding points to a source, a
page and an exact quotation, so it can be checked against the paper.

## Start here

1. **Add your papers.** Open the `inbox/` folder in this project's file list,
   then choose **Upload** in the file list toolbar and pick your PDF files.
   Upload adds files to the folder that is open, so open `inbox/` first.
2. **Ask the agent in this project's chat.** Use the suggested prompts or write
   your own: register the PDFs, record findings, check the quotations.
3. **Read the result in `synthesis.md`.** It is the readable research summary
   and comparison. The comparison view and `exports/` show the same evidence
   as a table.

## What you will see in the file list

| Location | Written by | What it is |
|---|---|---|
| `inbox/` | You | **Your papers, as PDF files. Upload them here.** |
| `synthesis.md` | The agent | **The research summary and comparison. Read this first.** |
| `exports/` | Evidence Desk | The comparison CSV and comparison view. Regenerated; do not edit. |
| `evidence/` | The agent | One file per paper with findings, page numbers and quotations. |
| `sources/` | Evidence Desk | One folder per registered paper (`S1`, `S2`, …). Do not edit. |
| `library/` | Evidence Desk | Papers you chose to save from arXiv searches: bibliographic metadata, not evidence. |
| `README.md` | Support file | This guide. |
| `desk.json` | Support file | Technical settings the agent maintains: the research question and which papers are compared. You do not need to edit it. |

## What the statuses mean

- **Reported**: supported by a page number and an exact quotation from the source.
- **Not reported**: checked in the available source material and absent.
- **Not assessed**: not checked yet.

A finding is never filled in from memory or guessed. If the paper does not
say it, the finding stays Not reported or Not assessed.
