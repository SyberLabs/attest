<!-- source: https://github.com/cppalliance/runvil-docs/blob/dd8a2456f92961928f54f09c37ade0da1b956355/src/language/03-blocks-and-prose.md -->
# Blocks and prose

The H1 body and every section body of a prompt are made of two kinds of blocks that alternate in file order: Lua blocks, which hold the prompt's logic, and prose blocks, which hold Markdown text. This chapter shows how you write each kind, which prose a Lua block can read, how [list sections](#list-sections) hand a list to Lua, and how a library shares its code through [`require`](#sharing-code-with-require). Knowing where each block starts and ends tells you exactly which Lua runs and which text can reach a model.

## Lua fences and prose blocks

You can write `lua` fences and prose in the H1 body and in any section, and tell which blocks they become.

````markdown
---
name: fences
description: One Lua block between two prose blocks
runvil: 0
---

# Fences

## Main

Before.

```lua
return 'hello'
```

After.
````

````text
hello
````

The section `## Main` holds three blocks in file order: a prose block `Before.`, a Lua block, and a prose block `After.`. The Lua block returns `hello`, which becomes the run result (see [Returning values](04-how-a-prompt-runs.md#returning-values)), and neither prose block runs anything or calls a model.

A Lua fence is a fenced block that opens with a line of three backticks followed by `lua` and closes with a line of three backticks, and you usually place one under a section heading. A Lua block is the code one Lua fence holds, run as one unit (see [A Markdown file that is a program](01-what-a-prompt-is.md#a-markdown-file-that-is-a-program)). A prose block is a run of Markdown text outside the fences: before the first fence, between two fences, or after the last one.

### Where Lua and prose go

Lua lives only inside `lua` fences. Everything outside them, in the H1 body or in a section, is Markdown prose, so a fence with any other label, such as `python`, stays part of the prose. The exact shape a Lua fence must have is in [Exact fence rules and fence errors](#exact-fence-rules-and-fence-errors).

Each section holds Markdown prose, `lua` fences with the prompt's logic, or both. The H1 body and each section body can alternate fences and prose freely and in any number: each fence becomes a Lua block, and each run of text between fences becomes a prose block. Blocks keep their file order, so prose blocks and Lua blocks alternate exactly as you write them.

Blocks fill two places in a prompt file: the H1 body, which is everything between the H1 title and the first `##` heading (see [The H1 title and H1 body](02-file-structure.md#the-h1-title-and-h1-body)), and every section. You fill both in the same way:

````markdown
---
name: plan
description: Lua blocks and prose in the H1 body
runvil: 0
---

# Plan

```lua
local first = 1
```

Plan the work in two steps.

```lua
local second = 2
```

## Main

```lua
return 'planned'
```
````

````text
planned
````

An H1 body holds any mix of plain `lua` fences and prose paragraphs before the first `##` section, and each becomes its own block in file order, so this H1 body holds a Lua block, a prose block, and a Lua block. You can place a fence directly under the H1 title, as here, or inside a section such as a `##` section, and a Lua block is one `lua` fence in either place.

You typically write an explanatory paragraph under the H1 title, before the first section, to describe what the prompt does, such as `Returns the single input argument verbatim, entirely in Lua, with no model call.` under `# Echo`. Prose written under the `#` title is kept as an H1 prose block. An H1 title with no content under it, or only whitespace, has no blocks at all, and an H1 body holding only one `lua` fence is valid and has a single Lua block.

### Writing Lua inside a fence

Inside a fence you write ordinary Lua statements, such as the `local` declaration `local first = 1` or a function definition. This fragment is one section of a prompt:

````markdown
## Setup

```lua
function normalize(value)
    return string.lower(value)
end
```
````

A Lua block's source is exactly the text between its opening and closing fence lines. The fence lines are left out, multiple lines and inner indentation are kept as written, and trailing line breaks are dropped, so the source of this block is the three lines from `function normalize(value)` to `end`, with the four-space indent of the middle line kept.

### Prose blocks

You write a section's instruction as plain prose under its heading, such as `Say "Hello, world!"` under `## Greet`. Double quotes, commas, and exclamation marks go in verbatim, without escaping, and a paragraph may wrap across several source lines.

Which text becomes a prose block follows a few rules:

- When anything other than blank lines comes before the first `lua` fence in the H1 body or a section, that text becomes its own prose block ahead of the first Lua block. Blank or whitespace-only lines alone before the fence produce no block.
- Prose after the last `lua` fence becomes a trailing prose block only when it is not empty after trimming. Markdown after the last fence is never an error and stays an ordinary prose block.
- Every prose block is trimmed of leading and trailing whitespace.
- Apart from that trimming, a prose block holds the prompt file's Markdown as written, so text like `Plan {{ args }}.` keeps its `{{ args }}` placeholder (see [Placeholders in prose](07-substitution.md#placeholders-in-prose)) exactly as written when the prompt loads.
- A section with no `lua` fence is a single prose block.

A section made only of prose is valid, and the prompt still loads. It can sit beside sections that use fences:

````markdown
---
name: greeting
description: A prose-only section beside a section with a fence
runvil: 0
---

# Greeting

## Fallback

This section has prose only.

## Greet

Say "Hello, world!"

```lua
return 'greeted'
```
````

This prompt loads cleanly. When the run reaches `## Fallback`, the section runs no Lua and makes no model call.

### When Lua blocks compile and run

Every Lua block, in the H1 body or in a section, is compiled as its own Lua program when the prompt loads, before any block runs. Inside a section, every fence labeled exactly `lua` is compiled wherever it sits around the section's prose: a fence before the prose becomes the section's prologue and a fence after it becomes the epilog, as [Prologue, prose, and epilog](#prologue-prose-and-epilog) explains, so a section with a prologue and an epilog compiles two programs.

When the run reaches a section (see [The section walk](04-how-a-prompt-runs.md#the-section-walk)), its blocks run in file order. Each Lua block runs when its place in that order comes, and a prose block runs nothing and calls no model on its own.

Because compiling happens at load, a Lua syntax error in any block, in the H1 body or a section, fails the prompt load with parse error kind `Lua` before any block runs:

````markdown
---
name: transform
description: An epilog with a Lua syntax error
runvil: 0
---

# Draft

## Transform

Ask the model for a response.

```lua
local reply =
```
````

The message starts with `lua compilation error at {location} (line {N}): ` and ends with the Lua compiler's own diagnostic. `{location}` is the block's location label, such as ``H1 `{title}` lua`` for a block in the H1 body, and here a label containing ``section `Transform` epilog``. `{N}` is the line of the prompt file where the block's Lua source starts. [How errors name each block](#how-errors-name-each-block) lists every location label.

## Prologue, prose, and epilog

You can put Lua before and after a section's prose, and know what each position can do and when it runs.

````markdown
---
name: tides
description: A prologue, prose, and an epilog in one section
runvil: 0
models:
  writer: {}
---

# Tides

```lua
models.default('writer')
```

## Explain

```lua
var.topic = 'tides'
```

Explain {{ var.topic }} in one sentence.

```lua
return models.infer(prose)
```
````

The H1 body's fence calls `models.default('writer')` (see [The prompt-wide default with `models.default`](10-models.md#the-prompt-wide-default-with-modelsdefault)). In `## Explain`, the first fence is the section's prologue, the prose after it is the section prose, and the second fence is the epilog, so the block sequence is exactly Lua, prose, Lua. The model is asked `Explain tides in one sentence.`, and its reply is the run result.

This is the common section shape. The prologue runs before the prose and stores a value in `var` (see [Keeping state in `var`](05-lua-environment.md#keeping-state-in-var)). The prose uses that value through a `{{ var.topic }}` placeholder. The epilog runs after the prose, reads the pending prose through `prose`, and sends the finished prose to a model with `return models.infer(prose)` (see [Running a round with `models.infer`](10-models.md#running-a-round-with-modelsinfer)).

A prologue is a `lua` fence that opens a section, before any prose, and it runs before the prose. Blank lines before it are fine: blank lines and lines holding only spaces or tabs between the section heading and the first fence are discarded and never stop that fence from being the prologue. The prose after a prologue stays a separate prose block, and if no fence follows that prose, the section has no epilog.

An epilog is a `lua` fence after a section's prose, the trailing Lua block that ends the section, and it runs after the prose. When no fence comes before the prose, the section has no prologue. A section can have a prologue alone, an epilog alone, or both, and when it has both, the prologue runs first.

### Section shapes

Where the fences sit decides which blocks a section has and which of them are the prologue and the epilog:

| Section content | Blocks | Prologue | Epilog |
| --- | --- | --- | --- |
| Prose only | one prose block | none | none |
| One fence only | one Lua block | the fence | none |
| A fence, then prose | Lua, prose | the fence | none |
| Prose, then a fence | prose, Lua | none | the fence |
| A fence, prose, a fence | Lua, prose, Lua | first fence | second fence |
| Two back-to-back fences | Lua, empty prose, Lua | first fence | second fence |
| Prose, a fence, prose | prose, Lua, prose | none | none |
| Fences with prose between them | Lua, prose, Lua, and so on | first fence | last fence |

A section with no `lua` fence is a single prose block with no prologue and no epilog. A section whose only content is one `lua` fence, including a library section (see [Sharing code with `require`](#sharing-code-with-require)), treats that fence as its prologue and has no epilog, because an epilog needs prose before it, even empty prose. A `lua` fence with prose on both sides becomes its own Lua block in the middle of the section and is neither the prologue nor the epilog. Blank lines before a leading fence and empty text after the last fence produce no block.

### Back-to-back and empty fences

Text between two `lua` fences becomes a prose block, and two back-to-back fences with no prose between them still get an empty prose block. Both fences run, as the prologue and the epilog around that empty prose, in order and with no model call:

````markdown
---
name: phases
description: A prologue and an epilog around empty prose
runvil: 0
---

# Phases

## Main

```lua
var.phase = 'prologue'
```

```lua
return var.phase .. '-epilog'
```
````

````text
prologue-epilog
````

The same holds when only blank lines or lines of spaces and tabs sit between the two fences. Prose made only of spaces, tabs, and blank lines is trimmed to empty, so the next fence reads `prose` as an empty string.

A prologue does not have to do anything. An empty `lua` fence, an opener followed directly by its closer, is valid: it still produces a Lua block, with empty source, that does nothing, and it can stand as a section's prologue. A fence that holds only a Lua comment, such as `-- prologue`, is a valid prologue too:

````markdown
---
name: quiet
description: A comment-only prologue before an epilog
runvil: 0
---

# Quiet

## Main

```lua
-- prologue
```

```lua
return 'ok'
```
````

````text
ok
````

You can leave the prologue fence empty and do the section's work in the epilog, which then runs with empty prose, for example an epilog that calls `tasks.fanout` to run a worker section (see [Choosing the worker section](14-fanout.md#choosing-the-worker-section)).

### Several fences in one section

You can place several `lua` fences in one section, interleaved with prose, to split the section's work. They run top to bottom in file order as separate blocks of that one section. In a section that opens and closes with a fence, the first fence is the prologue, the last fence is the epilog, and every fence between them is neither:

````markdown
---
name: tour
description: Two questions in one section, the second using the first reply
runvil: 0
models:
  writer: {}
---

# Tour

```lua
models.default('writer')
```

## Landmark

```lua
var.city = 'Lisbon'
```

Name one landmark in {{ var.city }}.

```lua
var.landmark = models.infer(prose)
```

Write one sentence about {{ var.landmark }}.

```lua
return models.infer(prose)
```
````

The prologue sets `var.city`, and the middle fence sends `Name one landmark in Lisbon.` to the model and stores the reply in `var.landmark`. The prose after it reads `{{ var.landmark }}`, so the second question uses the first reply, and the epilog sends that second question and returns the reply. The section makes two model rounds, one for each `models.infer` call.

The H1 body works the same way: it can hold several Lua blocks interleaved with prose blocks, and they run in file order as one H1 pass (see [The H1 pass](04-how-a-prompt-runs.md#the-h1-pass)).

### One section VM per section

Each section, the H1 body's blocks, and every fanout arm (see [What an arm sees](14-fanout.md#what-an-arm-sees)) run in a section VM, a fresh Lua instance of its own. Several Lua blocks in one section run one after another on the same section VM, so a bare global that one Lua block assigns, called a section global, stays visible to later Lua blocks of the same section:

````markdown
---
name: greeting
description: A section global shared by two fences
runvil: 0
---

# Greeting

## Main

```lua
greeting = 'hello'
```

world

```lua
return greeting .. ', ' .. prose
```
````

````text
hello, world
````

Other state persists from one fence to the next in the same section too, such as the tools an earlier fence offers and the call counts in `tools.calls` (see [Calling tools from Lua and counting calls](12-tools.md#calling-tools-from-lua-and-counting-calls)).

### What a prologue and an epilog can do

A prologue prepares values for the prose, and an epilog acts on the finished prose. One section can hold all three together: the prologue runs before the prose, and the epilog runs after it and reads the pending prose through `prose`. An epilog can read `prose`, call the model, and write to the store (see [Writing and reading files](09-the-store.md#writing-and-reading-files)):

````markdown
---
name: save
description: An epilog that saves the model reply to the store
runvil: 0
models:
  writer: {}
---

# Save

```lua
models.default('writer')
```

## Ask

Name one ocean.

```lua
local reply = models.infer(prose)
store.write('reply.txt', reply)
return 'saved'
```
````

````text
saved
````

The epilog sends `Name one ocean.` to the model, writes the reply to `reply.txt` in the store, and returns `saved`. A Lua block can also call a tool with `tools.call` and return nothing, and the section then continues with the prose that follows.

The prologue and the epilog run as two separate Lua blocks, each with its own `lua_chunk_started` and `lua_chunk_succeeded` events, and a `models.infer` call in the epilog reports its model round between the epilog's own pair of events.

### How errors name each block

Error messages name each Lua block with a location label that says where the block sits:

| Block | Location label |
| --- | --- |
| A section's first block, when it is a Lua block | ``section `{name}` prologue`` |
| A section's last block, when it is a Lua block and the section also has prose | ``section `{name}` epilog`` |
| Any other Lua block in a section | ``section `{name}` lua {n}`` |
| Any block in the H1 body | ``H1 `{title}` lua`` |

`{name}` is the section name, the heading text without the `#` marks, and `{title}` is the H1 title. `{n}` is the block's 1-based place among the section's Lua blocks, counting the prologue as 1, so the third Lua block of `## lib` is ``section `lib` lua 3``, as in the library error in [Where a library error points](#where-a-library-error-points). In a section of just two back-to-back fences, the second fence is labeled `epilog`, because the empty prose between them counts as prose. A malformed epilog makes the prompt fail to parse with parse error kind `Lua` and a message that names the section and its epilog, as in ``section `Transform` epilog`` in [When Lua blocks compile and run](#when-lua-blocks-compile-and-run).

## Pending prose and the `prose` global

You can read the prose above a Lua block, know what it holds and when it renders, and handle the errors that reading or assigning `prose` raises.

````markdown
---
name: reader
description: Return the pending prose as the run result
runvil: 0
---

# Reader

## Main

Say hello to the reader.

```lua
return prose
```
````

````text
Say hello to the reader.
````

Prose written since the heading or the previous `lua` fence is pending prose, and it builds up for the next Lua block. A Lua block reads the pending prose written just above its fence in the same section through the read-only `prose` global. Inside the block, `prose` holds that pending prose as a plain Lua string with every `{{ }}` placeholder already filled in. It needs no assignment and is ready to pass to a model or to return.

A block that returns `prose` makes its rendered pending prose, the substituted text a model would get, the result, so `return prose` from a section makes the rendered prose the run's result text, as here.

### What a block's `prose` holds

Markdown written after a section heading accumulates as pending prose that the next ordinary `lua` fence consumes, so `Gather the facts.` followed by a fence holding `return 1` gives that fence the prose `Gather the facts.`. Prose written before a section's first fence is part of that fence's `prose`, as in the example above. Each fence consumes the pending prose, so every Lua block reads exactly the Markdown written since the previous fence in that section:

````markdown
---
name: steps
description: Each fence reads the prose written since the previous fence
runvil: 0
---

# Steps

## Main

First.

```lua
var.first = prose
```

Second.

```lua
return var.first .. ' ' .. prose
```
````

````text
First. Second.
````

The first block gets `First.` and the second gets `Second.`. Markdown written after an ordinary `lua` fence starts a fresh pending prose buffer, so a fence splits prose, and the text before and after it are separate prose blocks.

The string a block reads follows these rules:

- Pending prose is the Markdown body text only, trimmed of the blank lines around it and never including the heading, so `## Run`, a blank line, and `Done.` give the prose `Done.`.
- `prose` holds the prose just above the fence with its placeholders already substituted, without the surrounding blank lines or a trailing newline.
- Reading `prose` returns the section's prose text with its placeholders filled and without the code of its `lua` fences.
- Reading `prose` always gives a Lua string, and a Lua block with no pending Markdown before it reads `prose` as the empty string `''`.

Prose can hold any Markdown you mean as instruction text for the model, such as numbered lists and inline code spans: for example a `Do this:` line followed by items `1.` through `4.` that name `web_search` and `web_fetch`. Prose becomes useful when a `lua` fence below it in the same section reads it.

### Prose never calls a model on its own

You send prose to a model by passing `prose` to `models.infer` in a Lua block:

````markdown
---
name: hello
description: Ask the model to say hello
runvil: 0
models:
  writer: {}
---

# Hello

```lua
models.default('writer')
```

## Greet

Say "Hello, world!"

```lua
return models.infer(prose)
```
````

`models.infer(prose)` sends the pending prose above the current Lua block to the model, so this epilog sends `Say "Hello, world!"` as the message and returns the reply (see [Running a round with `models.infer`](10-models.md#running-a-round-with-modelsinfer)). The epilog runs after the section's prose and can call `models.infer(prose)` to send that prose to the model, and any other Lua block can do the same, as in `var.first = models.infer(prose)` in a middle fence.

Prose never calls a model on its own and only feeds the next Lua block. Nothing reaches a model until Lua passes the prose to an explicit `models.infer`, and only that call needs a configured model. Prose written above a fence waits in the pending buffer, so you use it only by passing `prose` to an explicit infer such as `var.draft = models.infer(prose)`, and the prose itself adds no model request beyond that call. A section with two prose paragraphs and two `models.infer(prose)` calls makes exactly two model rounds, and the prose adds none.

The H1 body and each section collect their own pending prose, so each part's prose reaches the model only through its own `models.infer(prose)` call, and those calls run in source order:

````markdown
---
name: twoquestions
description: H1 prose and section prose each feed their own model round
runvil: 0
models:
  writer: {}
---

# Two questions

```lua
models.default('writer')
```

Name one planet.

```lua
var.planet = models.infer(prose)
```

## Answer

Name one ocean.

```lua
return models.infer(prose)
```
````

The prompt asks `Name one planet.` first, during the H1 pass, and then `Name one ocean.` from `## Answer`, and the result is the ocean reply. The H1 pass and each section keep separate pending prose buffers, so each reads only its own prose through `prose`, and prose written between `lua` fences in the H1 body collects into a buffer that `prose` reads the same way. H1 prose never drives inference on its own: with no `models:` entry, a prompt whose H1 prose stays unread still runs, while an H1 block running `return models.infer(prose)` fails because no model is configured.

### What resets pending prose

Pending prose resets at three points: the nearest preceding heading, `lua` fence, or thematic break (`---`). Text above a `---`, such as `Draft notes.`, never reaches the next fence's `prose`, and the `---` itself is not part of it either. [Thematic breaks and setext headings](#thematic-breaks-and-setext-headings) covers breaks in full.

A heading ends the pending prose, and each section starts with empty pending prose, so prose one section leaves unread never reaches the next section's first fence:

````markdown
---
name: boundary
description: Pending prose stops at a section boundary
runvil: 0
---

# Boundary

## A

Prose for A.

## B

```lua
return '[' .. prose .. ']'
```
````

````text
[]
````

Pending prose resets at each section entry, so a section's unconsumed prose never reaches the next section. Because pending prose never moves data from one section to the next, data a later section needs travels through an explicit channel such as the store (see [Writing and reading files](09-the-store.md#writing-and-reading-files)).

### Commentary

Prose that no Lua block reads is commentary. It is dropped unread at the end of its section, without being rendered or sent anywhere, so you can keep notes for human readers in it:

````markdown
---
name: notes
description: Commentary that is never rendered
runvil: 0
---

# Notes

Notes for human readers can mention {{ var.omit }} safely.

## First

{{ var.missing }} is never read here.

## Second

An unclosed {{ placeholder and a {{ var.missing }} key.

```lua
return 'ok'
```

Trailing {{ var.missing }} commentary.
````

````text
ok
````

Every piece of prose in this prompt is commentary:

- Pending prose in the H1 body that no Lua block reads is discarded when the H1 pass ends, without error, even when it holds a placeholder like `{{ var.omit }}`. Unread H1 prose sends nothing to a model, so the prompt needs no `models:` entry, and H1 prose meant only for human readers can stay unread.
- Prose at the end of a section with no Lua fence after it is discarded when the section ends, without being rendered, so a placeholder naming a missing value, such as `{{ var.missing }}`, cannot fail the run.
- A block that never reads `prose` is untouched even by an unclosed `{{` in the prose above it.
- Markdown left after the final `lua` fence of a body is inert trailing commentary: no fence reads it, its placeholders are never filled, and it never raises an error, so `Trailing {{ var.missing }} commentary.` leaves the result `ok`. Prose left pending after a section's last fence is not a parse error either.

### When `prose` renders

`prose` renders lazily: its `{{ }}` placeholders render at the block's first read of `prose`, not when the block starts, so `var` and global changes made earlier in the same block show up in the text:

````markdown
---
name: word
description: prose renders at its first read
runvil: 0
---

# Word

## Main

The word is {{ var.word }}.

```lua
var.word = 'mutated'
return prose
```
````

````text
The word is mutated.
````

Reading the `prose` global returns the current block's prose text, rendered when the first read happens. A `prose` the block has not read yet renders with the `var` values current at the moment of that read, not the values the block started with or held at any earlier point. With `Second {{ var.word }}.` above a fence whose block starts with `var.word` set to `'changed'`, sets it to `'two'` while a library loads through `require` (see [The caller's `prose` after a `require`](#the-callers-prose-after-a-require)), and sets it to `'three'` just before the read, `prose` gives `Second three.`. A closing Lua block that returns `prose` returns the pending prose with its placeholders already substituted in this way.

`prose` renders at most once per block. After the first successful read, every later read in the same block returns the same string, even after `var` or a global changes:

````markdown
---
name: once
description: prose renders at most once per block
runvil: 0
---

# Once

## Main

The word is {{ var.word }}.

```lua
var.word = 'one'
local first = prose
var.word = 'two'
assert(prose == first)
return prose
```
````

````text
The word is one.
````

Each `lua` fence gets a fresh `prose` before its Lua block starts, replacing the previous block's. The new `prose` holds only the Markdown written since the previous fence, heading, or thematic break, and it renders against the state at its own first read. A string saved from an earlier block's `prose` in a local or a `var` key keeps its earlier rendering after a later block gets and renders its own `prose`:

````markdown
---
name: pairs
description: Each fence gets a fresh prose
runvil: 0
---

# Pairs

## Main

First: {{ var.word }}.

```lua
var.word = 'one'
var.first = prose
```

Second: {{ var.word }}.

```lua
var.word = 'two'
return var.first .. ' ' .. prose
```
````

````text
First: one. Second: two.
````

### When reading `prose` fails

A placeholder that fails to render makes the read of `prose` raise the substitution error at that read (see [Substitution error kinds](07-substitution.md#substitution-error-kinds)), and `pcall` catches it (see [Raising and catching errors](05-lua-environment.md#raising-and-catching-errors)). A failed render is not remembered, so reading `prose` again after you fix the cause, for example by setting the missing `var` key, renders again:

````markdown
---
name: retry
description: Catch a failed render and read prose again
runvil: 0
---

# Retry

## Main

Missing: {{ var.missing }}.

```lua
local ok = pcall(function() return prose end)
if not ok then
  var.missing = 'found'
end
return prose
```
````

````text
Missing: found.
````

Left uncaught, as in a bare `return prose` over `Missing: {{ var.missing }}.`, the substitution error fails the run with an error containing `missing {{ var.missing }}`.

Prose cannot refer to itself. A `{{ prose }}` placeholder inside prose makes the read of `prose` fail with an error naming `prose`, and that failure is catchable with `pcall` too.

### Keeping and reshaping the text

`prose` is read-only, so to keep or reshape the rendered text you copy it into a local, a `var` key, or a section global, as in `local before = prose` or `var.first = prose`, and work on the copy. Assigning to `prose` always raises a Lua runtime error, catchable with `pcall`:

````markdown
---
name: keep
description: Assigning to prose is refused
runvil: 0
---

# Keep

## Main

Keep this line.

```lua
local ok = pcall(function() prose = 'overwritten' end)
if ok then
  return 'changed'
end
return 'still: ' .. prose
```
````

````text
still: Keep this line.
````

The refused assignment raises an error with this message:

````text
prose is read-only: assign to `var` or a section global instead
````

Reading `prose` gives the block's rendered prose, and every write is refused, whatever the value, including `prose = nil`. The refusal applies at any time: before or after the first read, during the H1 pass, and in a library too. During the H1 pass, `argv` is writable (see [Reading parsed input from `argv`](06-arguments.md#reading-parsed-input-from-argv)), but `prose` stays read-only with the same refusal message. The refusal is a separate runtime error that has no substitution kind and no `[{Kind} at byte {offset}]` suffix, only the bare message.

`prose` is a reserved global in every section and in the H1 pass, and the Lua environment has no `rawget`, `rawset`, `rawequal`, `debug`, `load`, or other raw or code-loading route around it, so every read and write of `prose` gets the read-only, lazy behavior. A metatable you set on `_G`, including one with its own `__index` or `__newindex`, never sees or changes how `prose` reads or refuses assignment, because `prose` is served and refused before any metatable of yours runs. With a metatable on `_G`, including one a library sets, `prose` stays read-only and still renders at its first read, and the next fence still gets its fresh `prose` even when the metatable has a `__newindex` write hook.

`prose` reads `nil` in code that runs before any block has started, such as a [Pack prelude](12-tools.md#pack-preludes) while it installs.

## Thematic breaks and setext headings

You can drop working notes from pending prose with a thematic break, and write breaks that never make a line of prose into a heading.

````markdown
---
name: instructions
description: A thematic break drops the notes above it
runvil: 0
---

# Instructions

## Main

Working notes the model should never see.

---

Write the actual instructions here.

```lua
return prose
```
````

````text
Write the actual instructions here.
````

A thematic break is a line such as `---` on its own, with a blank line before it. It resets pending prose: the Markdown above it is dropped, only the Markdown below the last break before the next `lua` fence is captured as `prose`, and the break line itself is never part of `prose`. Headings, `lua` fences, and thematic breaks are all prose boundaries, and the text after a break starts a new prose block.

Breaks follow CommonMark, so any genuine CommonMark thematic break line works as the reset, such as `---`, `***`, or `___` on a line of its own after a blank line.

### Breaks between fences

Between two fences, a break drops the notes about the previous step that you write above it:

````markdown
---
name: stepnotes
description: A break between two fences
runvil: 0
---

# Step notes

## Main

```lua
var.a = 1
```

Notes on step one.

---

Pending for step two.

```lua
return prose
```
````

````text
Pending for step two.
````

A section can contain any number of thematic breaks, and only the Markdown below the last break stays pending. A break only affects the prose segment it sits in, so a break before one `lua` fence does not trim the prose after that fence. Each prose block is the pending Markdown for the fence that follows it and keeps only the text below the last break in its segment, so commentary written above a `---` is dropped from the block. The reset also applies to the trailing prose after the last `lua` fence and to a section with no `lua` fence at all. A `lua` fence that sits directly below a break, with no Markdown between them, receives empty pending prose.

### A break never ends a section

Resetting pending prose is all a break does. It has no control-flow meaning and never ends a section's or the H1 body's content, so `lua` fences, prose, and headings below it parse and run as usual:

````markdown
---
name: belowbreak
description: A section that opens with a break
runvil: 0
---

# Below

## Main

---

```lua
var.x = 1
```

Below the break.

```lua
return prose
```
````

````text
Below the break.
````

A `lua` fence below a break is still part of its section's content and stays live, because a break resets prose capture only and makes nothing reader-only, and prose below a break is ordinary pending Markdown. You can put a `---` break as a section's first content, as here, and the section runs like any other. A section body that holds a break followed by a `lua` fence still runs that fence when the section is called with `call` (see [Calling a section](08-jump-and-call.md#calling-a-section)), and a break inside a fanout worker section (see [Choosing the worker section](14-fanout.md#choosing-the-worker-section)) only resets its pending prose, as in any section. A heading below a break still starts a new section.

A break stays in its section's raw Markdown and never ends the section, so [`prompt.section`](#reading-sections-with-promptsection-and-promptlist) returns the `---` line and the text on both sides of it verbatim, even though prose capture drops what sits above it. In a [list section](#list-sections), a break resets list capture as well as prose, so only the markers below the last break become items, as [Thematic breaks in a list section](#thematic-breaks-in-a-list-section) shows.

### A break below the H1 title

You can set the prose under the H1 title off from the title with a `---` break on its own line, with a blank line on each side:

````markdown
---
name: summary
description: A break between the H1 title and its description
runvil: 0
---

# Summary

---

Returns a fixed greeting, entirely in Lua, with no model call.

## Main

```lua
return 'hello'
```
````

````text
hello
````

Plain prose under the H1 title describes what the prompt does, and because nothing sits above the break, the break drops nothing. A `---` line in the body with blank lines around it is a thematic break rather than a frontmatter delimiter or a setext heading underline. The H1 body splits into prose and `lua` blocks the same way a section does, so when Markdown does sit above the last break in the H1 body, it is left out of the H1 body's prose, and a following H1 `lua` block reads only the text below the break as `prose`.

### Breaks inside fenced code blocks

A `---` line inside a fenced code block is code, not a thematic break, and resets nothing. The same holds inside a `lua` fence, where a `---` line is a Lua comment, and it still holds below a leading break. A `text` fence stays whole inside the prose even when it holds a `---` line:

````markdown
---
name: livetext
description: A dash line inside a text fence is code
runvil: 0
---

# Live text

## Main

Live prose.

```text
---
```

Also live.

```lua
return prose
```
````

````text
Live prose.

```text
---
```

Also live.
````

### The setext heading trap

Headings follow CommonMark, so setext headings count too. A setext heading is a line of text underlined with `---` or `===`. A prose line directly followed by `---`, with no blank line between them, is a setext H2 underline: it makes the line a new section heading, and the Markdown after it belongs to the new section. This fragment is part of a prompt:

````markdown
## S

Some prose
---

More prose
````

The fragment holds two sections: `## S`, and an H2 named `Some prose` whose prose is `More prose`. A `---` line directly under a text line is a setext heading underline rather than a thematic break, so it is never a reset point, and the text line above it becomes a heading instead.

A break needs a blank line before it. With a blank line between `Some prose` and the `---`, the `---` is an ordinary break:

````markdown
## S

Some prose

---

More prose
````

Now `## S` is one section, and its pending prose is `More prose`.

A line of text underlined with `===` is a second H1, and a prompt must contain exactly one H1 title (see [The H1 title and H1 body](02-file-structure.md#the-h1-title-and-h1-body)), so it fails the parse with parse error kind `Structure`:

````markdown
---
name: twotitles
description: A setext underline that makes a second H1
runvil: 0
---

# Title

## S

Some prose
===
````

````text
prompt must contain exactly one H1 title, found 2 at lines 7, 11
````

The `{}` slots hold how many H1 titles the parser found and the lines they are on.

## Exact fence rules and fence errors

You can write `lua` fences the parser always accepts, and read the `Fence` errors it raises when a line comes close to a `lua` fence but misses.

````markdown
---
name: exact
description: An exact lua fence
runvil: 0
---

# Exact

## Main

```lua
return 'exact'
```
````

````text
exact
````

A `lua` block opens with a line of exactly three backticks followed by lowercase `lua`, starting at the first column, with no indent, no extra backticks, no space or tab before `lua`, and nothing after the label. It closes at the first later line that is exactly three backticks at column 1, unindented and with nothing else on the line. Opener and closer lines can end in CRLF as well as LF, so a prompt file with Windows line endings parses the same way.

A Lua block's source is every line between its opener and the first line that is exactly three backticks, so Lua source, including a multi-line string, cannot contain that line.

### Fences with other labels

Only a fence labeled exactly `lua` becomes an executable Lua block, and `lua` is the only Lua fence label: everything else in the H1 body and section content is prose. A fenced code block with no label, or with a label that does not start with `lua` in any letter case, such as `text`, `json`, `text lua`, or `moonlua`, is ordinary Markdown. It stays in the section's prose verbatim, fence lines and contents included, compiles no Lua, and creates no prologue or epilog, and the next Lua block reads it through `prose`:

````markdown
---
name: explain
description: A python fence stays in the prose
runvil: 0
---

# Explain

## Main

Explain what this code prints:

```python
print(1)
```

```lua
return prose
```
````

````text
Explain what this code prints:

```python
print(1)
```
````

### Showing a `lua` fence as an example

Lines inside the body of any other fenced code block are never checked against the Lua fence rules. To show a `lua` fence to a reader or a model as an example, even a near miss such as `` ```lua extra ``, nest it inside a longer fence, such as a four-backtick `markdown` fence or a tilde fence. It stays prose, with no Lua compiled and no `Fence` error, and near-miss lines inside the outer fence stay prose without errors too. A section that opens with such an outer fence has no Lua prologue, and the whole fence stays in the section's prose:

````markdown
---
name: teach
description: Show a lua fence to the model as an example
runvil: 0
---

# Teach

## Main

~~~markdown
```lua
return 1
```
~~~

```lua
return prose
```
````

````text
~~~markdown
```lua
return 1
```
~~~
````

### The four fence rules

The parser checks every fence against four rules, and breaking any of them fails the parse with parse error kind `Fence`:

| Rule | Broken by | Reported at |
| --- | --- | --- |
| Label | a fence label that starts with `lua` in any letter case but is not exactly `lua` | the opening line |
| Opener | a fence labeled exactly `lua` whose opening line is not exactly three backticks then `lua` | the opening line |
| Closer | a fence with an exact opening line whose closing line is not exactly three backticks | the closing line |
| Lookalike | a line outside every fence that looks like a `lua` opening line | that line |

Each rule below comes with one failing example.

### The Label rule

A fence whose label starts with `lua` in any letter case without being exactly `lua`, such as `Lua`, `LUA`, `luajit`, `luau`, or `lua extra`, breaks the Label rule:

````markdown
---
name: label
description: A fence label that is not exactly lua
runvil: 0
---

# Label

## S

Prose.

```lua extra
local a = 1
```
````

````text
unrecognized fence label `lua extra`; the only Lua fence label is `lua`
````

The parse fails with parse error kind `Fence`, and the message `` unrecognized fence label `{label}`; the only Lua fence label is `lua` `` names the offending label as written. The label is the whole info string the Markdown parser reports after the backticks, so a label that starts with `lua` but adds more, such as `luau`, `lua extra`, or an attribute after `lua`, breaks the Label rule rather than being read as plain code. The `lua` label is case-sensitive, so a fence labeled `Lua` or `LUA`, in the H1 body or a section, fails the same way.

Fence detection follows CommonMark as the Markdown parser reads it, so any line the parser treats as a fence opener is checked against the Label rule. When the parser reads a line as a fence opener, a near-miss label gets the label error whatever the opener's shape, rather than the opener error: a four-backtick fence with a near-miss label, closed by four backticks, fails with the unrecognized-label error, and so does a fence indented by one space. A near-miss label is reported even when the fence's closing line does not match its opener. The same line indented four spaces is no fence at all, and it gets the opener message as a lookalike, as [The Lookalike rule](#the-lookalike-rule) describes.

The label check covers every part of the body: the preface, which is the prose between the frontmatter and the H1 title; the H1 region between the `# Title` line and the first section, including a fence that follows a whitespace-only line; and fences inside a section body after the section's prose. A near-miss label fails the same way whether its fence opens the H1 body or follows an intro paragraph.

### The Opener rule

A fence labeled exactly `lua` whose opening line is not exactly `` ```lua `` breaks the Opener rule:

````markdown
---
name: tilde
description: A tilde fence around Lua
runvil: 0
---

# Tilde

## Main

~~~lua
local greeting = "hello"
~~~
````

````text
`lua` fence opening line must be exactly ```lua
````

The parse fails with parse error kind `Fence` at the opening line, so the prompt is rejected as invalid, and `lua` code needs backtick fences. Every opener failure has this one fixed message, which contains `fence opening line must be exactly`. Each of these opening lines breaks the rule:

- a trailing space or a trailing tab after `lua`
- a space between the backticks and `lua`
- an indent of one to three spaces, even three, which CommonMark accepts
- a tilde fence, `~~~lua`
- four or more backticks, so a `lua` fence uses exactly three
- a fence inside a block quote or a list item, because its opening line begins with the container marker, such as `>` or `-`

A fence inside a block quote or list item with a `lua`-like label, rather than exactly `lua`, breaks the Label rule instead. The Opener rule applies in the preface before the H1 title, in the H1 region, whether the fence opens it or follows an intro paragraph, and inside a section body, and a breaking fence fails whether it starts the section or follows intro prose. Every non-exact opener is rejected even when its fence closes with a line that matches its own opener, such as `~~~` after `~~~lua` or four backticks after a four-backtick opener.

### The Closer rule

A fence with an exact `` ```lua `` opener whose closing line is not exactly `` ``` `` breaks the Closer rule:

````markdown
---
name: closer
description: An indented closing line
runvil: 0
---

# Closer

## Main

```lua
return 'closed'
  ```
````

````text
`lua` fence closing line must be exactly ```
````

The parse fails with parse error kind `Fence` at that closing line, and the prompt is rejected as invalid. The message contains `fence closing line must be exactly`. A closing line breaks the rule when it has trailing spaces or tabs after the three backticks, an indent of one to three spaces, or four backticks. A bad closing line fails the parse no matter what follows it, whether another `lua` fence or prose with a `python` block.

### The Lookalike rule

Any line outside every fence that, after leading spaces or tabs, has three or more backticks or tildes, optional spaces or tabs, and then `lua` in any letter case looks like a `lua` opening line and breaks the Lookalike rule:

````markdown
---
name: lookalike
description: An indented line that looks like a lua opener
runvil: 0
---

# Lookalike

## Main

Intro.

    ```lua
    return 1
    ```
````

````text
`lua` fence opening line must be exactly ```lua
````

The parse fails with parse error kind `Fence`, and you see the lookalike reported with the Opener message. Instead of becoming an indented code block in the prose, the `` ```lua `` line indented by four spaces fails. The rule catches such lines inside indented code blocks and raw HTML blocks, so these lines fail the same way:

- an indented `~~~lua` tilde line
- indented fence lines with a space or a tab before `lua`, or with uppercase `LUA`
- a `` ```lua `` fence inside an HTML block such as `<div>` and `</div>`, which fails rather than passing through as HTML

Lines inside a `lua` fence's own body are never checked against the Lookalike rule, so Lua source can contain a line such as `~~~lua` or `` ```lua `` inside a multi-line string.

### Which error you see

For each fence, the Label, Opener, and Closer rules are checked in that order, and the parse reports only the first violation in source order. It points at the opening line for a Label or Opener break, at the closing line for a Closer break, and at the line itself for a Lookalike line. When several fences have bad labels, the parse reports only the first one in document order, and when a section has both a lookalike line and a bad closing line, it reports whichever comes first in the file.

A fence rule error points at the offending line: its line number is that line's 1-based number in the whole file, counting the frontmatter lines, and its column is 1. In the Label rule example, the error points at line 13, column 1, the `` ```lua extra `` line. Fence error messages name the label or the rule but never include the code inside the fence.

The Lua fence rules are checked across the whole body after the frontmatter, including the preface before the H1, before any content is split into blocks. A fence problem anywhere in the file fails the parse before the run starts and before any Lua compiles, so a bad label or a non-exact opener in a later section stops the parse even when the H1 body's Lua is valid. A fence error also wins over a Lua syntax error inside the same fence, so you see parse error kind `Fence` rather than a Lua compile error.

### Unclosed fences

A `lua` fence left open with no closing line fails the parse with parse error kind `Fence`:

````markdown
---
name: unclosed
description: A lua fence with no closing line
runvil: 0
---

# Unclosed

## S

```lua
return 1
````

````text
section `S` prologue `lua` fence is not closed
````

An unclosed `lua` fence that is the only fence and sits at the top of its content, left open to the end of the section, gets this prologue message. In the not-closed messages, the section name is the section's heading text without the `#` marks, such as `S` for `## S`. An unclosed fence that follows prose or another fence, left open to the end of the section, gets the epilog wording instead:

````text
section `{name}` epilog `lua` fence is not closed
````

An unclosed `lua` fence under the H1 uses the same section wording with the H1 title as the name, for example ``section `{title}` prologue `lua` fence is not closed``. A backtick line indented by four spaces, or three backticks followed by other text, cannot close a `lua` fence, so the fence stays open and the parse fails with the not-closed message. Unlike the rule errors, an unclosed fence error names its section and phase in the message but reports no line or column.

## List sections

You can write a list section, predict exactly which items it holds, and fix the one parse error a list section can raise.

````markdown
---
name: topic_list
description: Reads the items of a list section.
runvil: 0
---

# Topic list

## Main

```lua
return table.concat(prompt.list('## Topics'), ', ')
```

## Topics

- alpha
- beta
- gamma
````

````text
alpha, beta, gamma
````

`## Topics` is a list section. You build one from a heading followed by a bulleted list, writing one item per line, each line starting with a `- ` marker followed by the item text. A heading whose body is only a bullet list is a list section, and the parser treats it as a construct of its own. You fill its body with bullet items that each have text.

When the prompt file is parsed, the parser strips the marker from each line of a list section and keeps the remaining text as the section's items, in source order, one item per bullet line. Every other section has no items. `prompt.list` hands a section's items to Lua, as [Reading sections with `prompt.section` and `prompt.list`](#reading-sections-with-promptsection-and-promptlist) explains, and here it lets `## Main` return them joined with commas.

### Writing list markers

Each item line starts with one of four markers, and each marker includes its space:

- `- `, a dash and a space, as in `- item`
- `* `, an asterisk and a space, as in `* item`
- `N. `, digits, a period, and a space, as in `1. item`
- `N) `, digits, a closing parenthesis, and a space, as in `1) item`

In the two ordered markers, `N` is one or more digits. Every marker is stripped from the item text, so each of the four example lines gives the item `item`.

````markdown
---
name: mixed_markers
description: Mixes all four list markers in one list section.
runvil: 0
---

# Mixed markers

## Main

```lua
return table.concat(prompt.list('## Steps'), ', ')
```

## Steps

- alpha
* beta
7. gamma
3) delta
````

````text
alpha, beta, gamma, delta
````

One list can mix all four markers, so unordered bullets and ordered numbered markers sit side by side in one list section. Item numbers are never checked for order or starting value. Any run of ASCII digits works as the marker number, so ordered items need not start at 1 or run in sequence, and `7.` followed by `3)` is a fine list.

Only `-`, `*`, or a run of ASCII digits can open a list marker. A line that opens with any other character is prose, and that one line keeps its section from being a list section.

### How a line becomes item text

The parser trims each line's leading indentation and trailing whitespace before it checks for a marker, so an indented bullet still counts as a marker. An item's text is the rest of the line after the marker and its one separating space, so `- item` and `1. item` both give `item`.

````markdown
---
name: item_text
description: Shows how list lines turn into item text.
runvil: 0
---

# Item text

## Main

```lua
return '[' .. table.concat(prompt.list('## Notes'), '][') .. ']'
```

## Notes

- first
  - looks nested

-   wide
- 1. step
````

````text
[first][looks nested][  wide][1. step]
````

Each line in `## Notes` shows one rule:

- Indentation never nests list items. The indented `- looks nested` line is one more item in a single flat list.
- Blank lines and whitespace-only lines between items are ignored and produce no items.
- Only the first space after the marker is removed. Any extra spaces stay at the start of the item text, so `-   wide` gives an item that starts with two spaces.
- The marker is stripped once. An item whose text itself starts with another marker keeps that marker as text, so `- 1. step` gives the item `1. step`.

A marker needs a literal space between it and the item text. Each of these lines is ordinary prose, and any one of them in a section keeps that section from being a list section:

| Line | Why it is prose |
|---|---|
| `-item` | no space after the dash |
| `1.foo` | no space after the period |
| a marker followed by a tab, as in `-<tab>item` or `1.<tab>item` | a tab is not a space |
| `2026 was busy` | the digits are not followed by a period or a closing parenthesis |

### Which sections are list sections

A section is a list section exactly when it has no Lua blocks and every nonblank line of its prose is a list marker line. The same marker rules decide both whether a section is a list and what its items are, so a section detected as a list never fails because the two disagree about a marker.

List sections live under H2 through H6 headings. Their items are parsed when the prompt loads, before any code runs, at any heading depth, so Lua can read them by heading. A list H3 under an H2 works the same way as a top-level list, and its parent H2 can have its own prose before the list heading:

````markdown
---
name: kid_list
description: Reads a list H3 that has a child section of its own.
runvil: 0
---

# Kid list

## Main

Main reads the list in its child section.

```lua
return table.concat(prompt.list('### Kid'), ', ')
```

### Kid

- kid-one
- kid-two

#### Grand

- grand-item
````

````text
kid-one, kid-two
````

You fill a list H3 like `### Kid` with list marker lines only, with no other content. A list section can also have [child sections](02-file-structure.md#section-headings-and-nesting) of its own. Their content is not part of its body, so `### Kid` stays a list, and its items leave out the `- grand-item` bullet of its child `#### Grand`.

Every other section is a prose section with no items:

- A section whose body is ordinary prose with one incidental bullet line stays a prose section with no list items. Any other line keeps the whole section as prose, and the bullet line stays part of that section's prose text. The same holds for a section that mixes prose with a few bullets.
- A section that contains any Lua block is never a list section, however its prose looks. Adding a `lua` fence to a section that would otherwise be all bullets makes it an ordinary section that keeps its bullets as ordinary prose. A [worker section](14-fanout.md#choosing-the-worker-section) is ordinary in the same way: a `### Worker` holding a fence with `return item` and the line `Do work on {{ item }}.` has no items.
- A section whose prose is empty or only whitespace is an empty prose section, not a list section.
- The [H1 body](02-file-structure.md#the-h1-title-and-h1-body) is never a list section and has no items, so bullets there stay ordinary H1 prose.

Added to the previous prompt, this section is a prose section, bullet included:

````markdown
## Context

Here is context.
- one incidental bullet
More prose follows.
````

### Thematic breaks in a list section

A [thematic break](#thematic-breaks-and-setext-headings) resets list item capture, just as it resets pending prose. Marker lines above the last break are commentary, and only the marker lines below the last break become items:

````markdown
---
name: list_break
description: Keeps commentary out of a list with a thematic break.
runvil: 0
---

# List break

## Main

```lua
return table.concat(prompt.list('## Items'), ', ')
```

## Items

Draft notes about this list.

- alpha

---

- beta
- gamma
````

````text
beta, gamma
````

The every-line-is-a-marker rule applies only to the lines below the last break, because those are the only lines the section captures as prose. The prose line and the bullet above the break in `## Items` don't stop it from being a list section. A list that opens with a break, such as `---` followed by `- alpha` and `- beta`, keeps every marker line below the break as items, here `alpha` and `beta`, and still counts as a list section.

### The empty item error

A list section fails to load only when one of its items has no text:

````markdown
---
name: empty_item
description: A list H3 with an empty item between two good ones.
runvil: 0
---

# Empty item

## Parent

Parent prose.

### Items

- alpha
-
- gamma
````

The parse fails with kind `List` and this message:

````text
empty bullet item in list section `Items`
````

A bare marker with no text, such as `-`, `*`, `1.`, or `1)`, is an empty marker, and so is a marker followed only by whitespace, such as `1. ` or a `- ` line with nothing after it. A section made only of marker lines that includes an empty one is still a list section, so the parser reports the empty marker instead of quietly turning the section into prose. The message is `` empty bullet item in list section `{section}` `` whatever the section is named, with its name in place of `{section}`.

A prose line never causes this error, because a prose line makes the section ordinary prose instead. Parsing stops at the first empty item, so you see only the first one in a section. Because a list section's bullets are parsed into items when the prompt loads, an empty item stops the prompt before any of it runs.

### List sections at run time

The [section walk](04-how-a-prompt-runs.md#the-section-walk) still reaches a list section like any other section, and with no Lua in it, nothing runs there. A list section also never has to run for another section to read its items, because the items were parsed when the prompt loaded. In the first example, `## Main` returns before the walk ever reaches `## Topics`, yet it reads all three items.

A list section cannot be a [worker section](14-fanout.md#choosing-the-worker-section), so naming one as the worker section fails with a message that names it:

````text
section `Items` is a list section, not a worker template
````

## Reading sections with `prompt.section` and `prompt.list`

You can read another section's list items or raw Markdown from Lua, and know what each read returns when the section isn't there.

````markdown
---
name: prompt_reads
description: Reads a sibling list section and a child list section.
runvil: 0
---

# Prompt reads

## Main

```lua
local nums = prompt.list('## Nums')
local kids = prompt.list('### Kid')
return table.concat(nums, ',') .. '|' .. table.concat(kids, ',')
```

### Kid

- kid-one
- kid-two

#### Grand

- grand-item

## Nums

1. one
2. two
3. three
````

````text
one,two,three|kid-one,kid-two
````

You read other sections through the `prompt` global, a table that holds two functions, `prompt.section` and `prompt.list`. The Engine installs `prompt` in every section's Lua and in the H1's Lua. Each read names its target with a [heading reference](02-file-structure.md#heading-references) made of the target's `#` markers, a space, and its name, such as `'### Kid'` for a level-3 section or `'## Nums'` for a level-2 section.

### Reading list items with `prompt.list`

`prompt.list(heading)` returns a list section's items as a Lua sequence of strings in document order. The items were already parsed into separate strings when the prompt loaded, so the read hands them over as they are. A section that is not a list has no items.

`prompt.list` strips the marker from each item. It removes the `- ` from each item of a bulleted list, so `- kid-one` and `- kid-two` read as `'kid-one'` and `'kid-two'`, and it removes the number marker such as `1. ` from each item of a numbered list, so `1. one`, `2. two`, and `3. three` read as `'one'`, `'two'`, and `'three'`. In the example, `prompt.list('## Nums')` returns `{'one', 'two', 'three'}` and `prompt.list('### Kid')` returns `{'kid-one', 'kid-two'}`.

The list `prompt.list` returns is a plain Lua table, so `#`, indexing, and `table.concat` work on it, as in `#kids == 2 and kids[1] == 'kid-one'`. It holds only the section's own items and leaves out items in its child sections, so `### Kid` gives two items even though its child `#### Grand` holds `- grand-item`.

Because the result is an ordinary sequence of strings, `tasks.fanout` accepts it as its [collection](14-fanout.md#collections-and-member-order), as in `tasks.fanout("### Worker", prompt.list("### Topics"))`. Inside a fanout [arm](14-fanout.md#what-an-arm-sees), `prompt.list` reads a list section's items the same way, so a section whose prose is `- x` and `- y` reads as `{'x', 'y'}`.

### Reading raw Markdown with `prompt.section`

`prompt.section(heading)` returns a visible section's own raw Markdown body as a string. Replace the block of `## Main` in the previous prompt with this one:

````markdown
## Main

```lua
return prompt.section('### Kid')
```
````

````text
- kid-one
- kid-two
````

A section's own raw Markdown runs from just after its heading line to the next heading of any level. It leaves out the section's own heading line and its child sections, headings and content alike, and it trims leading blank lines and trailing whitespace. That is why `### Kid` reads as `'- kid-one\n- kid-two'`, with no heading text and nothing from its child `#### Grand`. On a list section, `prompt.section` returns the raw list Markdown with its markers, so `prompt.section('## Nums')` returns `'1. one\n2. two\n3. three'`.

Apart from that trimming, the body comes back byte for byte, and nothing in it runs or renders:

````markdown
---
name: raw_rules
description: Returns a section's raw Markdown unchanged.
runvil: 0
---

# Raw rules

## Main

```lua
return prompt.section('## Rules')
```

## Rules

  Indented first line.

- **Bold** and `code` stay raw.

```lua
var.never = 'run'
```

---

Closing line with {{ args }}.
````

````text
  Indented first line.

- **Bold** and `code` stay raw.

```lua
var.never = 'run'
```

---

Closing line with {{ args }}.
````

The result keeps the first line's leading indentation and the blank lines inside the body, and drops only the blank lines that separate the body from its heading and from the next heading. Inline Markdown such as `**bold**` and backtick code comes back raw, not rendered. The `lua` fence comes back as text, and the read does not run it. The `{{ args }}` [placeholder](07-substitution.md#placeholders-in-prose) comes back literally, without substitution. The thematic break stays in the Markdown too, even though the break resets the section's prose and list capture, as [A break never ends a section](#a-break-never-ends-a-section) explains.

The result is a Lua string that string methods work on, such as `text:find('Mine text.', 1, true)`.

### Which sections a read can see

A read searches only the caller's visible set, which is the caller's siblings, minus the caller itself, plus its own direct children. `prompt.section` and `prompt.list` find their target with the same heading lookup that other heading-taking features share, and [Resolving heading references](08-jump-and-call.md#resolving-heading-references) gives the full visible set rules.

````markdown
---
name: visible_reads
description: Shows which sections Main can read.
runvil: 0
---

# Visible reads

## Main

```lua
local seen = {}
for _, heading in ipairs({ '### Kid', '## Nums', '#### Grand', '### Niece', '## Main', '## Kid' }) do
  local text = prompt.section(heading)
  seen[#seen + 1] = heading .. ': ' .. (text and 'found' or 'nil')
end
return table.concat(seen, '\n')
```

### Kid

- kid-one
- kid-two

#### Grand

- grand-item

## Nums

1. one
2. two
3. three

## Other

Other prose.

### Niece

- niece-item
````

````text
### Kid: found
## Nums: found
#### Grand: nil
### Niece: nil
## Main: nil
## Kid: nil
````

From `## Main`, a sibling section and a direct child section are both visible, so the sibling `## Nums` and the child `### Kid` both return content. Everything else reads as `nil`, from `prompt.list` as well as from `prompt.section`:

- `#### Grand` is a grandchild, a child of `### Kid`.
- `### Niece` is a niece, meaning a child of a sibling, here of `## Other`. The read returns `nil` without raising, so a `pcall` around it returns ok.
- `## Main` is the caller's own heading.
- `## Kid` names the visible child `### Kid` at the wrong level. A read matches both the marker count and the name.

### Reads from the H1 and from libraries

Both reads work in the H1 body too, where the visible set is every top-level section and no child section:

````markdown
---
name: h1_reads
description: Reads sections from the H1 body.
runvil: 0
---

# H1 reads

```lua
var.nums = table.concat(prompt.list('## Nums'), ',')
var.main = tostring(prompt.section('## Main') ~= nil)
var.kid = tostring(prompt.section('### Kid'))
```

## Main

```lua
return var.nums .. '|' .. var.main .. '|' .. var.kid
```

### Kid

- kid-one
- kid-two

## Nums

1. one
2. two
3. three
````

````text
one,two,three|true|nil
````

From the H1, `prompt.list` reads the top-level list section `## Nums`, `prompt.section('## Main')` returns a top-level section's text, and `prompt.section('### Kid')` returns `nil` for a child section. The H1 keeps what it found in [`var`](05-lua-environment.md#keeping-state-in-var), and `## Main` reads it from there.

A read inside a [library](#sharing-code-with-require) section resolves over the visible set of the section that runs it. That holds both when the library loads and when a caller later calls a function the library defined:

````markdown
---
name: kid_reader
description: Reads a child list through a library from the H1 and from Main.
runvil: 0
---

# Kid reader

```lua
require '## kid_reader'
var.h1 = tostring(kids_at_load) .. '|' .. tostring(kid_items())
```

## Main

```lua
require '## kid_reader'
return var.h1 .. '|' .. table.concat(kids_at_load, ',') .. '|' .. table.concat(kid_items(), ',')
```

### Kid

- kid-one
- kid-two

## kid_reader

```lua
kids_at_load = prompt.list('### Kid')
function kid_items()
  return prompt.list('### Kid')
end
```
````

````text
nil|nil|kid-one,kid-two|kid-one,kid-two
````

`## kid_reader` calls `prompt.list('### Kid')` once while it loads and again inside `kid_items()`. Used from the H1, which sees no child section, both calls read `nil`. Used from `## Main`, the parent of `### Kid`, both read the items `kid-one` and `kid-two`.

### What a read returns for a miss

A read of a well-formed heading that names no visible section returns `nil` instead of raising a not-found error:

````markdown
---
name: read_misses
description: Shows that a missed read returns nil while a missed call raises.
runvil: 0
---

# Read misses

## Main

```lua
local found = {}
for _, read in ipairs({ prompt.section, prompt.list }) do
  local ok, value = pcall(read, '## Missing')
  found[#found + 1] = tostring(ok) .. ':' .. tostring(value)
end
local call_ok = pcall(call, '## Missing')
return table.concat(found, ',') .. '|' .. tostring(call_ok)
```
````

````text
true:nil,true:nil|false
````

`prompt.section` and `prompt.list` are plain functions, so you can store them in a table and pass them to `pcall` with a target argument, as this loop does. A heading that no section has, such as `'## Missing'`, reads as `nil` from both reads without raising, because a missing heading and a heading outside the visible set are not treated as mistakes.

A section outside the caller's visible set reads as `nil` the same way. A sibling of the caller's parent is one such section, so from a `### Worker` under `## Research`, the call `prompt.section('## house_style')` returns `nil`. `prompt.list` likewise returns `nil` instead of raising when no section text is found for the target.

This is where the reads differ from [`call`](08-jump-and-call.md#calling-a-section). `call` raises a not-found error when no visible section has the heading, so `pcall(call, '## Missing')` fails while `pcall(read, '## Missing')` returns ok with `nil`. [`jump`](08-jump-and-call.md#jumping-to-a-section) treats a miss the same way `call` does.

Because `prompt.list` returns `nil` when no visible section has the heading, passing its result straight to `tasks.fanout` raises the collection error, whose message says `use prompt.list(heading), which returns nil when no visible section has that heading`.

### Errors a read raises

An empty section is not a miss. It reads differently through each function:

````markdown
---
name: empty_reads
description: Reads an empty section both ways.
runvil: 0
---

# Empty reads

## Main

```lua
local text = prompt.section('## Empty')
local ok = pcall(prompt.list, '## Empty')
return '[' .. text .. ']|' .. tostring(ok)
```

## Empty
````

````text
[]|false
````

`prompt.section` on an empty section returns the empty string `''`, which differs from the `nil` a miss returns. `prompt.list` on the same section raises an error whose message is `` section `## Empty` has no pre-parsed items ``.

A read raises only when the call itself is wrong: a target that is not a string, a malformed heading, or `prompt.list` on a section without pre-parsed items. Each raises a Lua error that [`pcall`](05-lua-environment.md#raising-and-catching-errors) can catch:

- A target that is not a string raises an error whose message names the type it received, such as `section target must be a string, got integer` for a number passed to either read.
- A malformed heading raises an error. Malformed means a bare name with no `#` markers, a run of `#` markers with no name, or markers with no whitespace after them. A bare name gives a message that names the target, such as one containing `got bare name: Nums`, and markers with no whitespace after them give a message containing `must have whitespace after the ## markers`.
- `prompt.list` on a visible section that has no pre-parsed list items raises an error that names the section's trimmed heading, in the form `` section `{heading}` has no pre-parsed items ``.

A read trims surrounding whitespace from the target heading, so `prompt.list('  ## Rules  ')` resolves to `## Rules`, and its error message names the trimmed heading. When the Engine's section read fails inside `prompt.section` or `prompt.list`, the call raises a Lua error carrying the read's message.

`prompt.section` reads a section that `prompt.list` refuses. The `## Rules` section from earlier holds a bullet, a `lua` fence, a thematic break, and prose, so `prompt.list('## Rules')` raises `` section `## Rules` has no pre-parsed items ``, and after that `prompt.section('## Rules')` still returns its text.

## Sharing code with `require`

You can put Lua in one section and use it from any section that needs it.

````markdown
---
name: greet_library
description: Loads a library section and calls the function it defines.
runvil: 0
---

# Greet library

## Main

```lua
require '## helpers'
return greet('main')
```

## helpers

```lua
function greet(name)
  return 'hello ' .. name
end
```
````

````text
hello main
````

`## helpers` is a library: an ordinary section that other sections pull in with `require`. Nothing in the file marks it as a library. `require '## helpers'` runs the named section's Lua in the current block, and the globals it defines, such as the function `greet`, are callable in the requiring section right after the call.

A typical library section holds only a `lua` fence of global function definitions and sits after the section that returns the run result, as `## helpers` does here. A library section can sit later in the file than the H1 or the sections that require it. When the [section walk](04-how-a-prompt-runs.md#the-section-walk) does reach a library in document order, it runs it under the normal section rules, as [Libraries on the section walk](#libraries-on-the-section-walk) shows.

### Calling `require`

`require` names its library by the section's [heading reference](02-file-structure.md#heading-references), written exactly as the heading appears with its `#` marks included, as in `require '## helpers'` or `require '### helpers'`. Lua's string-call form without parentheses works, and so do `require('## helpers')` and `pcall(require, '## a')`. You can call `require` from the H1 body's block and from any section's block.

Any section with a Lua block can serve as a library, whether it holds only Lua or prose followed by a Lua block. `require` loads every `lua` fence of the named section and runs them one after another in document order.

`require` takes the heading of a library section within reach in the prompt. It loads sections only, never a file from disk or native code, and it reads its argument as a section heading every time, so Lua's `os` and `package` tables stay `nil` however you call it.

When a library section returns nothing, you call `require` as a bare statement purely for its effects. A `## setup` library that only runs [`models.default`](10-models.md#the-prompt-wide-default-with-modelsdefault) as `models.default('writer')` works this way.

### What `require` returns

A library shares code by returning a table of functions or values, as in `return M`. Every caller of `require` receives that table and can call its functions or index it directly:

````markdown
---
name: search_library
description: Loads a library that returns a table of functions.
runvil: 0
---

# Search library

## Main

```lua
local search = require '## search_tools'
return search.search_all(argv.prose)
```

## search_tools

```lua
local M = {}
function M.search_all(topic)
  return 'searched ' .. topic .. ' everywhere'
end
return M
```
````

Run it with the argument string `rust`, which arrives as [`argv.prose`](06-arguments.md#reading-parsed-input-from-argv):

````text
searched rust everywhere
````

`require` returns the value the library's code returns, whatever its type: a table, a function, a string, or `false`. A section can require a library that returns a scalar, such as a string, without failing. You can index the result in place, as in `(require '## lib').name`, and `return require '## loader'` makes the library's return value the caller's return value. Only the first value a library block returns becomes the library's value, and any extra return values are dropped.

A library's top-level statements run once, at the point it is required, and functions it defines can return values computed at that moment. A library table can mix a value computed at load time with a function that computes its result when called later, as in `lib.helped .. '|' .. lib.later()`.

A library with several Lua blocks runs them in order, and the first block that returns a value other than `nil` ends the library:

````markdown
---
name: block_returns
description: Runs a library's blocks until one returns a value.
runvil: 0
---

# Block returns

## Main

```lua
return require '## later'
```

## later

```lua
first = 'ran'
```

```lua
return nil
```

```lua
return first .. ' then third'
```
````

````text
ran then third
````

A block that returns `nil` lets the next block run, and the blocks after the one that returns a value do not run. A block that returns `false` ends the library, and `require` returns `false`, because only `nil` falls through to the next block. A library whose blocks all fall through without returning a value gives `true` to `require`, so a `## silent` section with the blocks `quiet = 1` and `return nil` makes `tostring(require '## silent')` read `true`.

A library loads successfully whether its code ends in a `return` value or is plain code with no `return`, such as `loaded = true`. Either way the run records `Lua library load succeeded: ## lib` for a library named `## lib`.

### Library code runs in the caller's environment

Library blocks run in the requiring section's one Lua environment, its [section VM](#one-section-vm-per-section). Globals the library sets are visible to the caller afterward, while the library's `local` variables stay inside the library:

````markdown
---
name: library_environment
description: Shows what a library shares with the section that requires it.
runvil: 0
---

# Library environment

## Main

```lua
style = 'terse'
require '## lib'
return exported .. '|' .. tostring(hidden) .. '|' .. seen_style .. '|' .. var.note
```

## lib

```lua
local hidden = 'local'
exported = 'global'
seen_style = style
var.note = 'library ran'
```
````

````text
global|nil|terse|library ran
````

Everything in that result follows from the shared environment:

- Globals a library defines at load, values and functions alike, including any function defined without `local`, are visible to and callable by the requiring code after `require` returns. The library's `local hidden` reads as `nil` to the caller.
- A library's code runs at the point of the `require` call and reads globals that the requiring block set before the call, as `seen_style` picks up `style`.
- Library code reads and writes the caller's [`var`](05-lua-environment.md#keeping-state-in-var) table, and its writes are visible to the caller after `require` returns.
- A library's `lua` blocks run one after another in file order when it loads, so a later block reads globals an earlier block set.
- A section reached only through `require` can set the metatable of `_G` for the code that required it, so one library sets up global defaults or strict checks once. A `## defaults` library that runs `setmetatable(_G, { __index = { tone = 'plain' } })` gives every section that requires it the same defaults.

Global functions defined by a required section stay callable for the rest of the requiring section, in its prologue and in its epilog alike:

````markdown
---
name: prologue_epilog_library
description: Calls a library function in the prologue and in the epilog.
runvil: 0
---

# Prologue and epilog

## Main

```lua
require '## helpers'
first = decorate('prologue')
```

Some prose for Main.

```lua
return first .. decorate('epilog')
```

## helpers

```lua
function decorate(value)
  return '<' .. value .. '>'
end
```
````

````text
<prologue><epilog>
````

### Effects a library makes belong to the caller

Code in a section loaded with `require` runs as part of the calling section, so every store, model, and tool call it makes while it loads is issued on the caller's behalf. A [`store.write`](09-the-store.md#writing-and-reading-files) lands exactly as if the caller had made it, and a [`tools.call`](12-tools.md#calling-tools-from-lua-and-counting-calls) returns the tool's result.

A library's top-level code runs at load time with every global the Engine provides already installed, so `args`, `store`, `log`, and the `tools` and `models` tables all work while it loads. A library loaded with `require` also sees the globals a [Pack prelude](12-tools.md#pack-preludes) installs, including while its top-level code runs at load time.

Library blocks run inside the calling block, so a library block can make every call the caller can, including calls that pause the block, such as `tools.call` and [`models.infer`](10-models.md#running-a-round-with-modelsinfer). Each one pauses the calling block like an ordinary call made in the caller:

- A library that calls [`call`](08-jump-and-call.md#calling-a-section) during its load pauses the caller's block until the called section returns, and the library receives the call's result.
- Library code that calls `models.infer` while it loads runs one model round under the model the caller is using.
- An H1 that calls `require` lets the library set prompt-wide state that every later section sees, such as the default model through `models.default`.

### Library functions resolve names when called

Library functions look up globals when they are called, not when they are defined. A library function called from a section reads that run's current values, such as `args`, and the store writes it makes take effect for the caller:

````markdown
---
name: read_args_library
description: Calls a library function that reads args when it runs.
runvil: 0
---

# Read args

## Result

```lua
require '## helpers'
return read_args()
```

## helpers

```lua
function read_args()
  return args
end
```
````

Run it with the argument string `later canned value`:

````text
later canned value
````

The heading-taking functions follow the same rule. A library function resolves `call`, `prompt.section`, and `require` from whichever section calls it, not from where the library is defined, so a library function called later reads the caller's child section through `prompt.section`. A prompt read in a library's load-time code resolves over the requiring section's visible set too. When more than one section requires the same library, each requiring section's load runs its prompt reads over that section's own visible set. The `## kid_reader` library in [Reads from the H1 and from libraries](#reads-from-the-h1-and-from-libraries) shows each of these, and [Requires made while a library loads](#requires-made-while-a-library-loads) shows how `require` itself resolves inside a library.

A library function that calls [`jump`](08-jump-and-call.md#jumping-to-a-section), when the caller calls it after the load finishes, transfers control for the caller's section. A library that defines `function go() jump '## next' end` lets a caller that runs `require '## a'` and then `go()` move its walk to `## next`.

### Prose inside a library

A library section can carry prose alongside its Lua blocks. To share a piece of prose between sections, write a library section with a prose block and a Lua block that does `return prose`, then call `require` to get the rendered text:

````markdown
---
name: house_style
description: Shares a piece of prose through a library section.
runvil: 0
---

# House style

## Main

First one.

```lua
style = 'terse'
local text = require '## house_style'
return text .. '|' .. prose
```

## house_style

Write in a {{ style }} voice.

```lua
return prose
```
````

````text
Write in a terse voice.|First one.
````

While a library block runs, the [`prose` global](#pending-prose-and-the-prose-global) reads that library block's own prose, not the caller's. The caller's `prose` is back in place once the library block finishes, which is why `## Main` still reads `First one.` after the `require`.

Each Lua block in a library section pairs with the prose block right before it, and that Markdown is what the block's `prose` renders from:

- A Lua block with no prose block right before it, including a `lua` fence that directly follows another `lua` fence, pairs with empty prose.
- Prose after the last Lua block of a library section pairs with nothing and is left out of the loaded library.

A library block's `prose` is lazy. It renders from that block's own prose when the block first reads it, at most once, against the live `sys` values at that moment, and prose with no placeholders reads back verbatim. Its [placeholders](07-substitution.md#placeholders-in-prose) render from the calling section's state, meaning its `var`, its live `sys` values, and its globals, never from a separate library state:

- `{{ style }}` in the example renders the global `style` that `## Main` set before calling `require`.
- `{{ var.x }}` in a library's prose renders the caller's `var.x`.
- When a fanout [arm](14-fanout.md#what-an-arm-sees) requires a library, the library's prose renders with that arm's `item`, so each arm gets its own text.

A library block can also put its prose to other uses:

- A library section's Lua block can read the section's preceding prose through `prose` and keep it in a closure for later calls.
- A library block can read its own `prose` while raising, so its error text can include the library's rendered prose, as in `error('fault: ' .. prose)`.
- Assigning to `prose` inside a library block raises the same `prose is read-only` error as in any block (see [Keeping and reshaping the text](#keeping-and-reshaping-the-text)), `pcall` catches it, and `prose` keeps its rendered value.

A library block's own `prose` is in place only while that block runs. A library function that reads `prose` when the caller calls it after `require` returns gets the caller's `prose`, so a library that needs its prose later copies it into a local first, as in `local seen = prose`:

````markdown
---
name: library_closure
description: Keeps a library's prose in a local for later calls.
runvil: 0
---

# Library closure

## Main

Caller prose.

```lua
local lib = require '## labeler'
return lib.label('alpha') .. '|' .. lib.late()
```

## labeler

Library prose.

```lua
local seen = prose
return {
  label = function(value) return value .. ': ' .. seen end,
  late = function() return prose end,
}
```
````

````text
alpha: Library prose.|Caller prose.
````

`label` uses the copy it took while the library loaded, while `late` reads `prose` when `## Main` calls it and gets `## Main`'s own prose.

### The caller's `prose` after a `require`

A `require` never disturbs the caller's own pending prose:

````markdown
---
name: caller_prose
description: Shows that a require leaves the caller's prose alone.
runvil: 0
---

# Caller prose

## Main

Mood is {{ var.mood }}.

```lua
var.mood = 'calm'
local before = prose
require '## bump'
return before .. '|' .. prose
```

## bump

```lua
var.mood = 'bright'
```
````

````text
Mood is calm.|Mood is calm.
````

The caller's `prose` is lazy and renders once, on its first read. `## Main` read it before the `require`, so the read after the `require` gets back the text it already rendered, without rendering again, even though the library changed `var.mood`. Now move the first read after the `require` by replacing the block of `## Main` with this one:

````markdown
## Main

Mood is {{ var.mood }}.

```lua
var.mood = 'calm'
require '## bump'
return prose
```
````

````text
Mood is bright.
````

When the caller has not read `prose` before calling `require`, its `prose` stays unrendered and renders at its first read after `require` returns, so it sees any `var` or global changes the library made.

The caller's `prose` comes back the same way however the load ends. After a `require` that finishes, even one whose library read and returned its own `prose`, and after a `require` that raises and is caught with `pcall`, the caller's `prose` is still its own pending prose. It keeps any value it already rendered, whether or not the caller read it before the `require`, and reads exactly as it did before the load. When the caller has not read `prose` before a failed `require`, reading it afterward renders the caller's own prose block, not the library's.

### `require` belongs to the Engine

`require` is Runvil's own section loader, not the Lua base library's `require`. The Engine installs it as a global for each section's whole lifetime, so it is available in every section's Lua and any block can call it, and `type(require)` is `function`. The Engine finds the named library section and renders its prose for it. Author code that rebinds `error`, `setmetatable`, or `table.concat` does not change how `require` behaves.

Because `require` is reserved as one of the Engine's own globals, a [Pack prelude](12-tools.md#pack-preludes) cannot define its own `require`, and trying fails with an error containing `which is reserved as an Engine global`. A Pack prelude cannot call `require` while it installs either, because the Engine installs `require` after the preludes. That call fails the run with a message that starts `` Pack `{id}`: its prelude failed to load: `` and contains `attempt to call a nil value (global 'require')`, where `{id}` is the Pack id.

## How `require` finds and caches libraries

You can predict which section a `require` names, which sections it can reach, and when a library's code runs again.

````markdown
---
name: require_reach
description: A called child section requires its parent's sibling.
runvil: 0
---

# Require reach

## Parent

```lua
require '## lib'
return call('### Child')
```

### Child

```lua
local read = tostring(prompt.section('## lib'))
local before = tostring(greet)
require '## lib'
return read .. '|' .. before .. '|' .. greet('child')
```

## lib

```lua
function greet(name)
  return 'hello ' .. name
end
```
````

````text
nil|nil|hello child
````

`### Child` cannot read `## lib` with `prompt.section`, because `## lib` is outside its visible set, yet `require '## lib'` reaches it. `### Child` also starts without the `greet` its parent loaded, so it calls `require` itself. What follows explains both: how far `require` reaches, and which `require` calls share a saved library.

### Matching the heading exactly

`require` finds a library by its heading exactly as written, with the same number of `#` markers and the same name, following the same heading rules as the other heading surfaces. A reachable section named with the wrong number of `#` markers does not resolve. `require '### lib'` loads an H3 subsection by its heading level and name, so a section can require its own nested subsection.

An exact heading string names at most one reachable section, because each part of the reach sits at its own heading level and [sibling section names must be unique](02-file-structure.md#sibling-name-uniqueness). A prompt with two same-named siblings fails to parse with a message that starts `` duplicate sibling section name `{name}` `` and says that sibling section names must be unique.

### Which sections `require` can reach

From a section the walk runs, `require` reaches that section's direct children, its siblings, and the siblings of each of its ancestors up to the top-level `##` sections. Take this section tree, seen from `#### Self`:

````text
## Top
## Grand
### Uncle
#### Cousin
### Parent
#### Sibling
##### Niece
#### Self
##### Child
###### Grandchild
## Last
````

| Heading | Reached from `#### Self` | Relationship |
|---|---|---|
| `#### Sibling` | yes | a sibling |
| `##### Child` | yes | a direct child |
| `### Uncle` | yes | a sibling of the parent |
| `## Top`, `## Last` | yes | siblings of the grandparent, which are the top-level sections |
| `#### Self` | no | the caller itself |
| `### Parent`, `## Grand` | no | ancestors |
| `###### Grandchild` | no | a grandchild |
| `##### Niece` | no | a sibling's child |
| `#### Cousin` | no | a child of an ancestor's sibling |

`require` cannot name the calling section itself or any of its ancestors, because the reach skips the caller and each ancestor. It does not reach below those levels either: not the current section's grandchildren or anything deeper, not a sibling's children, and not the children of an ancestor's sibling.

That reach is wider than the one `prompt.section`, `call`, and `jump` share, because `require` also reaches the siblings of each ancestor. From `#### Self`, those three cannot name `### Uncle`, but `require` can. A `require` from a nested section, including a fanout arm's worker section, can name any top-level section other than its own top-level ancestor, even one outside its own subtree, since the top-level sections are the siblings of its outermost ancestor. A `### Worker` under `## Research` can require a top-level `## helpers`, for example.

From the H1, `require` reaches every top-level `##` section. It cannot reach any nested section from the H1, and the lookup fails.

### Requires made while a library loads

A `require` made while a library is loading resolves its heading from that library's position, not from the section that required the library. Outside any library load, `require` resolves from the calling section's position:

````markdown
---
name: require_position
description: Shows where a require inside a library resolves from.
runvil: 0
---

# Require position

## Main

```lua
local lib = require '## lib'
return lib.helped .. '|' .. lib.later()
```

### mine

Mine text.

```lua
return 'mine'
```

## lib

```lua
local helped = require '### helper'
return {
  helped = helped,
  later = function()
    local text = prompt.section('### mine')
    local found = text ~= nil and text:find('Mine text.', 1, true) ~= nil
    local reached = pcall(require, '### helper')
    return table.concat({ require '### mine', call('### mine'), tostring(found), tostring(reached) }, ',')
  end,
}
```

### helper

```lua
return 'helped'
```
````

````text
helped|mine,mine,true,false
````

While `## lib` loads, its `require '### helper'` resolves from the position of `## lib` and reaches its child `### helper`. Later, `## Main` calls `lib.later()` after the load has finished, so every lookup inside it resolves from `## Main`. `require '### mine'` and `call('### mine')` both reach the child of `## Main` and return `mine`, `prompt.section('### mine')` finds `Mine text.`, and `pcall(require, '### helper')` returns `false` because the library's own child is no longer within reach.

So a library can reach its own child sections, and its helpers load the same way wherever it is required from. Two libraries that each require a nested `### helpers` each get their own helper:

````markdown
---
name: own_helpers
description: Two libraries each load their own nested helper.
runvil: 0
---

# Own helpers

## Main

```lua
return require('## a') .. '|' .. require('## b')
```

## a

```lua
return require '### helpers'
```

### helpers

```lua
return 'helper of a'
```

## b

```lua
return require '### helpers'
```

### helpers

```lua
return 'helper of b'
```
````

````text
helper of a|helper of b
````

Because a library's load resolves from the library's own position, a required library cannot `require` its own ancestor section, even when the code that loaded the library could reach that section.

### When `require` cannot find a section

A `require` heading that is malformed or does not resolve from the current position does not fail parsing. The prompt parses and runs, and `require` raises the error as a Lua error when the call runs, which [`pcall`](05-lua-environment.md#raising-and-catching-errors) can catch:

````markdown
---
name: require_miss
description: Catches a require of a section outside the reach.
runvil: 0
---

# Require miss

## Main

```lua
local ok = pcall(require, '### deep')
return tostring(ok)
```

## other

Some prose.

### deep

```lua
return 'deep'
```

## helpers

```lua
function greet(name)
  return 'hello ' .. name
end
```
````

````text
false
````

`### deep` is a sibling's child, so it is out of reach from `## Main`. A well-formed `require` heading that matches no reachable section fails with a Lua error that names the trimmed heading and lists the reachable sections. Here the message is:

````text
section heading `### deep` not found; available sections: ## other, ## helpers
````

The list holds only the reachable sections, in search order: siblings, then children, then each ancestor's siblings moving outward to the top level, with the current section left out. `## other` is listed even though it holds only prose, while `## Main` and `### deep` are not. From `#### Self` in the tree above, a miss on `## Missing` gives `` section heading `## Missing` not found; available sections: #### Sibling, ##### Child, ### Uncle, ## Top, ## Last ``.

Passing anything other than a string to `require` raises a Lua runtime error that names the argument's Lua type, and no section lookup happens. For a number, the message is:

````text
require takes a section heading string such as '## helpers', got integer
````

### The library cache

A second `require` of a library that already loaded returns the saved value without running the library's blocks again:

````markdown
---
name: library_cache
description: Requires the same library twice in one section.
runvil: 0
---

# Library cache

## Main

```lua
local first = require '## counter'
local second = require '## counter'
return tostring(first == second) .. ':' .. loads
```

## counter

```lua
loads = (loads or 0) + 1
return { count = loads }
```
````

````text
true:1
````

Every `require` of one library within a section gets the same saved value, so a table the library returns is the same table each time, and a change one caller in that section makes to it is seen by later callers in the same section. A library that returns `false` is saved like any other value, so a second `require` returns `false` again from the cache without rerunning the library. A `require` of a library that already loaded successfully reuses the saved value, runs nothing, and emits no library-load report.

A library's saved value lasts as long as the [section VM](#one-section-vm-per-section) of the section that loaded it, so every `require` in that section shares one cache. That includes the requires a library makes while it loads, so a library that the section or another library already loaded hands back its saved value without rerunning or reporting, whichever of the two loaded it first. A library's own code can call `require` to load another library, and the inner library finishes loading before the outer one does.

`require` keys each library by its section's position in the document, never by heading text. The same heading written from two places can name two different sections, and each position loads as its own library that runs once and keeps its own saved value, as the two `### helpers` sections in the earlier example do.

### Each section loads its own libraries

A library saved in one section is not reused by a later section. Each section that requires the library runs it again on its first `require`, repeating its top-level calls and recording a new library-load start and success. Several places in one run, such as the H1, a parent section and its child, a section entered with `call`, a fanout arm's section, or a later section, can each `require` the same library section without error, and each section that uses the library calls `require` for it itself.

Global functions a library defines exist only in the sections that ran it, so a later section that never required the library sees them as `nil`. A child section entered with [`call`](08-jump-and-call.md#calling-a-section) starts without its parent's libraries and loads them itself with its own `require`. That is why `### Child` in the `require_reach` prompt reads `greet` as `nil` until its own `require '## lib'` runs.

Each fanout [arm](14-fanout.md#what-an-arm-sees) loads a required library in its own section VM, so the library load runs once per arm instead of being shared across arms, and each arm records its own load entry, such as `Lua library load succeeded: ## helpers`. A library's top-level code runs under each requiring arm's own identity, so its load-time effects count as that arm's. A library that writes the same store file at load time with `store.write`, required by two arms, fails the run because two arms wrote the same store file, even when the library wraps the write in `pcall`.

### Libraries on the section walk

When the section walk reaches a required library in its normal place, the walk runs it as a section under the same rules as any other section. A library that a section requires and that also sits on the walk runs twice, once inside the requiring section and once as its own section when the walk reaches it. A library that only defines globals runs harmlessly, and the walk then falls through to the next section:

````markdown
---
name: library_walk
description: Lets the walk reach a library that only defines globals.
runvil: 0
---

# Library walk

## Main

```lua
require '## helpers'
var.greeting = greet('main')
```

## helpers

```lua
function greet(name)
  return 'hello ' .. name
end
```

## Last

```lua
return var.greeting .. '|' .. tostring(greet)
```
````

````text
hello main|nil
````

`## Main` requires `## helpers` and falls through, the walk then runs `## helpers` as a section, and `## Last` runs in a section VM that never required the library, so `greet` is `nil` there.

A library that returns something follows the same rules as any section that returns, which [Returning values](04-how-a-prompt-runs.md#returning-values) covers:

- When the walk reaches a library that returns a scalar, that scalar becomes the run result, and later sections never run.
- When the walk reaches a library that returns a table, the run fails with a Lua error whose message contains `cannot return a table as a result`.

Requiring a table-returning library from inside a section succeeds, because that return rule applies only when the walk runs the library as a section. You keep a returning library off the walk in one of two ways. You can place it after a section that returns, since the walk ends at that return before it reaches the library, as `## search_tools` sits after `## Main` in [What `require` returns](#what-require-returns). Or you can nest it as a subsection under the section that requires it:

````markdown
---
name: nested_library
description: Keeps a table-returning library off the walk by nesting it.
runvil: 0
---

# Nested library

## Main

```lua
var.name = (require '### lib').name
```

### lib

```lua
return { name = 'nested' }
```

## Last

```lua
return var.name
```
````

````text
nested
````

The same section can be used with both `require` and `call`, and each returns that section's block value, as `### mine` gave `mine` to both in [Requires made while a library loads](#requires-made-while-a-library-loads).

## Library errors, cycles, and load events

You can catch a failing `require`, read where a library error points, and recognize the require cycle error and the library-load events.

````markdown
---
name: flaky_library
description: Catches a failing library load and retries it.
runvil: 0
---

# Flaky library

## Main

```lua
var.attempts = 0
local first_ok = pcall(require, '## flaky')
local second = require '## flaky'
return tostring(first_ok) .. '|' .. second .. '|' .. var.attempts
```

## flaky

```lua
var.attempts = var.attempts + 1
if var.attempts == 1 then
  error('the first load fails')
end
return 'loaded'
```
````

````text
false|loaded|2
````

### Ways a `require` fails

A `require` fails in one of five ways, and each one raises a Lua error in the requiring block:

- The argument is not a string. [When `require` cannot find a section](#when-require-cannot-find-a-section) gives the message.
- The heading is malformed or names no section within reach, which gives the not-found message described in the same place.
- The library's code raises an error while it loads.
- The library's code calls `jump` while it loads.
- The library is already loading, which makes a require cycle.

### Catching a failed load

An error that library code raises while loading travels out of `require` into the block that required it. You catch it with `pcall(require, heading)`, which returns `false` along with the error, and the section keeps running and can still return normally, such as with `return 'done'`. The error reaches the caller as the same value, unchanged, whether the library raised a string or a table.

A library load that raises is not saved, so every later `require` of that library runs its code again from the top and raises again if the fault remains. Once the fault is gone, the retry returns the library's value, and the library's code has run twice. In the example, `## flaky` fails only while `var.attempts` is `1`, so the caught first load and the successful retry give `false|loaded|2`.

What a failed load already did stays done:

- [`var`](05-lua-environment.md#keeping-state-in-var) writes a library makes before it raises stay in place after the error reaches the caller. That is why `var.attempts` is still `1` when the retry starts. Likewise, a library that sets `var.broken_saw = prose` before `error('the style broke')` still leaves `var.broken_saw` readable by the caller.
- Globals a library set before its load failed stay set in the caller's environment, so `partial = 'kept'` written before `error('library fault')` still reads `kept` in the caller.

A required library runs inside the Lua block that requires it, so an uncaught raise in library code is that block's own failure. A failing library required in the [H1 pass](04-how-a-prompt-runs.md#the-h1-pass) fails the H1 pass, so the run stops before the walk starts, with an error whose cause contains the library's message, such as `library boom`.

### Where a library error points

A caught library error and an uncaught one point at the fault in different forms. This prompt catches one:

````markdown
---
name: library_fault
description: Shows where a caught library error points.
runvil: 0
---

# Library fault

## Main

```lua
local ok, err = pcall(require, '## lib')
return tostring(err)
```

## lib

```lua
error('load fault')
```
````

````text
[string "line 19"]:1: load fault
````

A library error caught with `pcall(require, heading)` comes back in standard Lua form, `[string "line {N}"]:{k}: {message}`. `N` is the prompt line of the library block's first Lua line, here line 19, and `k` is the line within that block, here its first. For an `error` call on the third line of a block that starts at prompt line 100, the caught error reads `[string "line 100"]:3: library fault`.

Only an uncaught failure is rewritten to the library's location and prompt line. Replace the block of `## Main` with one that calls `require` without `pcall`, keeping the same number of lines:

````markdown
## Main

```lua
require '## lib'
return 'done'
```
````

The run fails, and its error message contains:

````text
section `lib` prologue:19: load fault
````

An uncaught Lua error raised while a library loads is reported as `` section `{name}` {block}:{prompt line}: {message} ``. It names the library section by its bare name and gives the failing statement's prompt-file line, at the library block's location rather than at the `require` call site. The location names the full section title even when the title is long, such as `` section `Research Helper Functions For Every Worker` prologue:19: load fault ``. In a library with more than one Lua block, an uncaught error maps to the block that raised it and that block's own prompt lines, using the labels in [How errors name each block](#how-errors-name-each-block), such as `` section `lib` lua 3:21: second fault `` for a raise on the second line of the section's third Lua block, which starts at prompt line 20.

An uncaught error from a function the library defined points at the library too, even when the caller calls that function after the load:

````markdown
---
name: later_fault
description: A library function fails after the load.
runvil: 0
---

# Later fault

## Main

```lua
require '## lib'
boom()
```

## lib

```lua
function boom()
  error('later fault')
end
```
````

The run fails, and its error message contains both of these locations:

````text
section `lib` prologue:20: later fault
section `Main` prologue:13:
````

The library's line 20 is where `error` runs. The caller's own frames in the traceback map to the caller's location and prompt lines as well, each frame mapped once with no raw `[string "..."]` marker left, so the message also shows line 13 of `## Main`, the caller's `boom()` line.

### `jump` during a library load

Load code cannot transfer control. Calling [`jump`](08-jump-and-call.md#jumping-to-a-section) from library code while the library loads through `require` raises a Lua error with this message:

````text
jump is unavailable while a library loads: return a value from the library and call jump from the section after require returns
````

The refusal ends with the load, so a caller that catches the failed load with `pcall` can still `jump` afterward, as in `pcall(require, '## a')` followed by `jump '## after'`. A library function that calls `jump` after the load has finished transfers control normally, as [Library functions resolve names when called](#library-functions-resolve-names-when-called) shows.

### The require cycle error

A `require` of a library that is still loading raises a Lua error before anything runs again:

````markdown
---
name: require_cycle
description: Catches a require cycle between two libraries.
runvil: 0
---

# Require cycle

## Main

```lua
local ok, err = pcall(require, '## a')
return err.kind .. '|' .. tostring(err)
```

## a

```lua
require '## b'
```

## b

```lua
require '## a'
```
````

````text
lua|require cycle: ## a -> ## b -> ## a
````

The cycle can come from the library's own blocks or through other libraries it requires, as here, where `## a` requires `## b` and `## b` requires `## a`. The message starts with `require cycle:` and a space, followed by the library headings with their markers in load order, joined by `->` with a space on each side and ending with the heading required again. It starts at the repeated library's own load and lists only loads from there inward, leaving out outer libraries that started loading earlier.

The require cycle error is a structured [error value](05-lua-environment.md#raising-and-catching-errors) with kind `lua` and a `message` field holding the cycle text, which `tostring(err)` also returns.

### Library-load events

The first `require` of a library emits a `lua_library_load_started` report as the load begins, before the library's code runs, and a `lua_library_load_succeeded` report when that code finishes without error, in that order, each naming the heading loaded. In readable form they appear as:

````text
Lua library load started: ## helpers
Lua library load succeeded: ## helpers
````

Each phase of a library load is its own event, of kind `lua_library_load_started`, `lua_library_load_succeeded`, or `lua_library_load_failed`. Each event carries the requiring section in `section` and the library heading in `library`:

- `section` is the name of the section running the load, without `#` markers, such as `Worker` for `### Worker`.
- `library` is the library section's own heading in canonical form, the `#` markers, one space, then the name, such as `## helpers`. It is rebuilt from the section rather than copied from the string you passed to `require`.

A library whose code raises during its load emits `lua_library_load_started` and then `lua_library_load_failed`, even when the caller catches the error with `pcall`, and every retry of a failing library emits a fresh pair. A load counts as succeeded only when all its blocks finish. The `flaky_library` prompt reports these four events, each with `section` set to `Main` and `library` set to `## flaky`:

1. `lua_library_load_started`
2. `lua_library_load_failed`
3. `lua_library_load_started`
4. `lua_library_load_succeeded`

Because the `section` field names the section running the load, the same library loaded by two sections reports once under each, such as once under `First` and once under `Second`. A child section that requires a library its parent already loaded runs its own load, with its own started and succeeded events under the child's section name, so `Parent` events for `## lib` are followed by `Child` events. A `require` answered from the cache reports nothing.
