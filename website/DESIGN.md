# Website design system and fidelity ledger

## Visual system

- Canvas: true white, with pale cool-blue surfaces only where a diagram or data boundary
  needs separation.
- Primary color: deep navy for editorial hierarchy and research blue for control/data
  flow.
- Secondary color: sage green for parallel workers and positive evidence.
- Warning color: restrained orange, reserved for incomplete infrastructure or evidence
  boundaries.
- Typography: system sans-serif for UI and Georgia-compatible serif for research
  headings; no externally hosted fonts.
- Geometry: thin rules, 4–8 px corners, open grids, and execution rails; no gradients,
  glass effects, or generic card collage.
- Accessibility: semantic landmarks and headings, visible focus rings, 44 px primary
  touch targets, a skip link, reduced-motion handling, and no color-only status labels.

## Fidelity ledger

| Concept decision | Production implementation |
|---|---|
| Current preview and paper ParaGUI shown as separate execution states | Responsive semantic HTML/CSS diagram in `RuntimeArchitectureDiagram.jsx`; no raster dependency |
| Generic GUI workers connected to isolated desktops | Browser, desktop apps, task assets, and the shared directory remain environment capabilities/resources |
| Adaptive round-based ParaGUI | Planner dispatches a batch, receives summaries at a round barrier, updates history, and either starts another round or terminates |
| Six benchmark categories in a structured legend | Counts generated from the canonical taxonomy and rendered by `BenchmarkOverview.jsx` |
| Public source ownership distinct from runtime flow | Module-boundary cards in `Architecture.jsx`; Framework contains contracts/scheduling but not Planner, VM creation, or evaluator logic |
| Dense task table rather than cards | Searchable, filterable, paginated table in `TaskExplorer.jsx`; stacked rows on narrow screens |
| Core and OSWorld installation tracks | Accessible tabs and copyable commands in `Quickstart.jsx` |
| Paper results separated from package readiness | Two-column evidence section in `Results.jsx` |
| Bilingual project site | English/Chinese content dictionary with a persistent local-only language preference |
| Static, privacy-preserving delivery | No backend or analytics; public data is an allowlisted deterministic projection |

Concept images remain design references and are not shipped in the Pages artifact.
