# MiroFish integration

This directory vendors the complete MiroFish application source needed by the
news analysis runtime. The exact upstream revision and exclusions are recorded
in `SOURCE.json`; the upstream AGPL-3.0 license is preserved at
`upstream/LICENSE`.

Do not put credentials in the vendored tree. `mirofish_runtime.py install`
creates a project-root `.env.mirofish` file with mode `0600`. Runtime virtual
environments, caches, logs, mutable uploads, and the frontend build live under
`.runtime/mirofish`.
