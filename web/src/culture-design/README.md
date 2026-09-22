# culture-design

Gwen's dashboard design layer, extracted from agentculture/org's site design
system at a pinned commit — the same extraction pattern culture-nodes uses
(`/home/spark/git/culture-nodes/web/src/culture-design/`,
`docs/adr/0001-culture-design-source.md` in that repo), adapted here to a
single Python stdlib checker (`scripts/check-culture-design.py`) instead of
the Node one culture-nodes ships.

The org repo (`/home/spark/git/org`) is developed independently of
embodiment and has no versioned package this repo depends on; it is a
sibling repo on this machine, checked out read-only for this purpose.
embodiment never modifies the org repo, and never imports from it at build
or run time — everything needed is copied here, once, at one pinned commit.

## Pinned commit

```text
org repo:     /home/spark/git/org (agentculture/org)
pin:          b4d939ba0aa354a5ae53065319a773e0013de698
source path:  site-astro/src/styles/global.css
```

Obtained with:

```bash
git -C /home/spark/git/org rev-parse HEAD
```

at the time task t17 was executed (2026-09-22). org's own HEAD is free to
move on without this pin changing — re-pinning is a deliberate, manual act
(see below), never automatic.

## Contents

- `tokens.css` — `global.css` copied **verbatim** from the org repo at the
  pin above (framework-agnostic CSS custom properties: color tokens for
  light and `@media (prefers-color-scheme: dark)`, the mesh palette, type
  scale, layout rail widths, motion easings, and the reduced-motion kill
  switch). Everything from the header comment's closing `*/` to end of file
  is byte-identical to the pinned org source; do not hand-edit it — re-run
  the extraction against a new pin instead.

Only `tokens.css` was copied for this task. `mark.tsx`, `palette.ts` and
`edges.ts` — which culture-nodes also extracts from org — are not used by
Gwen's dashboard and were left out rather than copied speculatively; add
them (and extend `scripts/check-culture-design.py`'s checks to match) only
when a component actually needs them.

Fonts are **not** copied from org here: like culture-nodes, Gwen's
dashboard self-hosts the same two npm packages the org site itself ships
(`@fontsource-variable/fraunces`, `@fontsource-variable/albert-sans` — the
faces `tokens.css` names in `--font-display` / `--font-body`), imported once
in `src/main.tsx`. No CDN, no `<link>` to a remote font host.

## Verification

`scripts/check-culture-design.py` (repo root) verifies this layer stays
faithful to its pinned source: `tokens.css`'s copied body byte-matches the
pinned org file, read via `git show <pin>:<path>` — never org's working
tree, so the check is stable even after org's HEAD moves on. Run it with:

```bash
uv run python scripts/check-culture-design.py
```

## Re-pin procedure

1. `git -C /home/spark/git/org rev-parse HEAD` for the new commit.
2. Re-copy `site-astro/src/styles/global.css` into `tokens.css`, keeping the
   header comment but updating its `Pinned commit:` line to the new hash.
   The copied body below the header must stay byte-identical to the org
   source — do not hand-edit it.
3. Update the `pin:` line in this README (both places above).
4. Run `uv run python scripts/check-culture-design.py` — it re-derives the
   pin from this README, re-fetches the org source at that pin, and fails
   loudly on any drift.

## License note

The org repo is licensed Apache License 2.0 (see its `LICENSE` file), which
grants a copyright license broad enough to cover copying this file into
embodiment (also Apache-2.0), including the required verbatim-notice
handling. Apache-2.0 does **not** grant any trademark license (License §6)
— "AgentCulture" as a name/brand is not licensed for use as a trademark by
this extraction.

## Dark mode

`tokens.css`'s dark values live entirely under
`@media (prefers-color-scheme: dark)` — there is no light/dark toggle
anywhere in the org site, and none is introduced here. Gwen's dashboard
follows the same rule: dark mode is derived purely from the visitor's
OS/browser preference.
