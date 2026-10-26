# Runvil documentation fixtures

Byte copies of chapters from [cppalliance/runvil-docs](https://github.com/cppalliance/runvil-docs),
each with one added first line, `<!-- source: <blob URL at the pinned commit> -->`, that names the
upstream path and commit. Everything after that line is the upstream file unchanged, so line numbers
in Attest's output are the upstream line numbers plus one.

| File | Upstream | Commit | Used by |
| --- | --- | --- | --- |
| `language-03-blocks-and-prose.md` | `src/language/03-blocks-and-prose.md` | `dd8a2456f92961928f54f09c37ade0da1b956355` | `tests/test_compiler_rules.py`: prompt and result fence pairing by rule, four-backtick nesting. Proven against `runvil-docs-host` (NOTES.md, M5) |
| `language-08-jump-and-call.md` | `src/language/08-jump-and-call.md` | `dd8a2456f92961928f54f09c37ade0da1b956355` | `tests/test_compiler_rules.py`: 33 prompts, all paired; proven 33 of 33 against the Host |
| `language-09-the-store.md` | `src/language/09-the-store.md` | `dd8a2456f92961928f54f09c37ade0da1b956355` | `tests/test_compiler_rules.py`: 46 prompts, 44 paired; 40 proven against the Host, 6 need a Host context or a message fence |
| `gateway-01-install-and-run.md` | `src/gateway/01-install-and-run.md` | `dd8a2456f92961928f54f09c37ade0da1b956355` | `tests/test_cli_config.py`: the shell path with a sidecar file |

Counts the tests pin for the Language chapter 3. The spec quotes 75 prompt examples and 60 result
fences, which counts every `markdown` fence; seven of those are fragments with no frontmatter and
compile to prose, and two of the 60 result fences follow such a fragment. A result fence pairs
with the prompt before it across blank lines, or across one introducing line that ends in a
colon ("and this message:", "the run result is:"), the guides' house style:

| What | Count |
| --- | --- |
| fences, all four-backtick | 148 |
| `markdown` fences | 75 |
| of which complete prompt files (frontmatter with `runvil:`) | 68 |
| complete prompt files with a `text` result fence (58 directly after, 4 after an introducing line) | 62 |
| `text` fences not paired with a complete prompt file (two follow a fragment) | 11 |

No test here fetches the network. To refresh a copy, download the raw file at a new commit, prepend
the source line and update the commit in this table and in the test that pins the counts.
