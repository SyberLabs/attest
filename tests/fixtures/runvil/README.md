# Runvil documentation fixtures

Byte copies of chapters from [cppalliance/runvil-docs](https://github.com/cppalliance/runvil-docs),
each with one added first line, `<!-- source: <blob URL at the pinned commit> -->`, that names the
upstream path and commit. Everything after that line is the upstream file unchanged, so line numbers
in Attest's output are the upstream line numbers plus one.

| File | Upstream | Commit | Used by |
| --- | --- | --- | --- |
| `language-03-blocks-and-prose.md` | `src/language/03-blocks-and-prose.md` | `dd8a2456f92961928f54f09c37ade0da1b956355` | `tests/test_compiler_rules.py`: prompt and result fence pairing by rule, four-backtick nesting. Proven against `runvil-docs-host` (NOTES.md, M5) |
| `gateway-01-install-and-run.md` | `src/gateway/01-install-and-run.md` | `dd8a2456f92961928f54f09c37ade0da1b956355` | `tests/test_cli_config.py`: the shell path with a sidecar file |

Counts the tests pin for the Language chapter. The spec quotes 75 prompt examples and 60 result
fences, which counts every `markdown` fence; seven of those are fragments with no frontmatter and
compile to prose, and two of the 60 result fences follow such a fragment:

| What | Count |
| --- | --- |
| fences, all four-backtick | 148 |
| `markdown` fences | 75 |
| of which complete prompt files (frontmatter with `runvil:`) | 68 |
| complete prompt files followed directly by a `text` result fence | 58 |
| `text` fences not paired with a complete prompt file (two follow a fragment) | 15 |

No test here fetches the network. To refresh a copy, download the raw file at a new commit, prepend
the source line and update the commit in this table and in the test that pins the counts.
