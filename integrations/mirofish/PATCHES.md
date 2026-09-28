# Runtime-only compatibility patches

The files under `upstream/` are unchanged from the pinned source revision.
`mirofish_runtime.py install` makes an isolated mutable copy and applies these
small integration patches there:

1. Backend CORS origins are restricted to the local MiroFish and news UI
   origins instead of accepting requests from every website. A Host/Origin
   request guard prevents cross-site form requests from executing, and error
   responses omit internal traceback fields.
2. The frontend report-status helper uses the backend's documented POST JSON
   endpoint instead of an incompatible GET query.
3. The runtime frontend pins transitive `nanoid` to `3.3.18` and `postcss` to
   `8.5.28`. These are API-compatible security updates for advisories present
   in the upstream lock file; the runtime regenerates its own lock file before
   `npm ci`.

These patches are deliberately applied after copying so the complete AGPL
source bundle remains attributable and directly comparable with upstream.

Mutable `backend/uploads` state is stored at `.runtime/mirofish/uploads` and
linked into each fresh worktree. Reinstall therefore preserves projects,
simulations, reports, and uploaded seed documents.
