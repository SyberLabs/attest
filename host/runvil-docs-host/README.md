# runvil-docs-host

Attest's Host for runvil-docs (docs/CLI-RUNNER-SPEC.md, milestone 5): one process per prompt
claim, a JSON request on stdin, a `PromptObservation` on stdout. `attest/runvil_runner.py` is
the client. It runs prompts only through Runvil's Harness, as Runvil's AGENTS.md asks of a Host.

Layout: the Runvil checkout is expected beside the attest repo (`../runvil` from the repo root,
pinned by hand to one commit); the two path dependencies in `Cargo.toml` say so.

Build, from this folder, with the MSVC toolchain on Windows (the GNU toolchain's llvm-mingw
shim does not link):

    cargo +stable-x86_64-pc-windows-msvc build --release

Then point Attest at it: `ATTEST_RUNVIL_HOST=<path to target/release/runvil-docs-host.exe>`.

What it does not do yet: model replies are played from `script`, which Attest never fills
today, so any prompt that asks a model ends `needs_script`; no Pack is installed, so a prompt
that declares one ends `refused`; the request's `seed` and `started_at` are accepted but the
Harness draws both itself.
