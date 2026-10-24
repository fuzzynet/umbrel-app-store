# AGENTS.md

fuzzynet Umbrel community app store.

## Design rules

- Cut settings pages, filter bars and mode switches. If a feature needs ten knobs to be useful, pick the right default and hide the rest.
- Never solve a design problem by adding more UI, or a code problem by adding another layer. Remove parts until the right action is the only obvious one.

## Security and data safety

- Never trust the frontend for permissions, pricing, or limits. Every check that matters must happen on the server, scoped to the logged-in user.
- Before running any migration or query that changes existing rows, show the exact SQL and what could be lost, and wait for approval.
