<!-- source: https://github.com/cppalliance/runvil-docs/blob/dd8a2456f92961928f54f09c37ade0da1b956355/src/language/08-jump-and-call.md -->
# Jump and call

`jump` and `call` are the control functions, the two Lua globals that let a block decide which section runs next. `jump` moves the walk to another section and never comes back, while `call` runs a section like a subroutine and hands its value back to the block that called it. Both name their target with a heading reference such as `'## Help'`, and this chapter covers how that reference resolves, which sections it can name, and what each function does in every place a section can run.

## Jump and call at a glance

After this section you can send the walk to another section with `jump` and run a section for its value with `call`.

````markdown
---
name: jump-and-call
description: Calls a child section for a value, then jumps past a sibling.
runvil: 0
---

# Jump and call

## Check

```lua
var.answer = call('### Answer')
jump('## Help')
error('unreachable')
```

### Answer

```lua
return 'forty-two'
```

## Accept

```lua
return 'accepted'
```

## Help

```lua
return 'helped:' .. var.answer
```
````

````text
helped:forty-two
````

The [section walk](04-how-a-prompt-runs.md#the-section-walk) enters `## Check` first. Its `call('### Answer')` runs the child section `### Answer`, waits for it, and gets back the string `forty-two`, which the block keeps in [`var`](05-lua-environment.md#keeping-state-in-var). The `jump('## Help')` on the next line ends the block on the spot, so `error('unreachable')` never runs. The walk then continues at `## Help`, skipping `## Accept` and its `return 'accepted'`, and `## Help` returns the run result. So `call` comes back to the line after it, and `jump` never comes back at all.

### Two globals in every section

`jump` and `call` are the control functions. The Engine installs both as Lua globals while it sets up every section, so every block can use them, and once setup finishes neither one is ever `nil`.

Each one takes the target section's heading reference as its first argument, written as a Lua string, as in `call('### Sub')` and `jump('## B')`. A heading reference is the full heading with its `#` markers (see [Heading references](02-file-structure.md#heading-references)). Either quote style works, as in `call('## Inner')` or `call("## Child")`, and that exact string becomes the target. `call('## Answer')` names the section headed `## Answer`, `jump('## Target')` names `## Target`, and from `## A`, `call('## S1')` runs the sibling `## S1`. Both `call('## Sub', '{"query":"chain"}')` and `call('## Sub')` reach the same `## Sub` section, because only the first argument names the target.

### What `jump` does

`jump(heading)` transfers control outright: the block ends at the `jump`, and the walk continues at the target section. `jump` returns nothing to the block, because the block is over, and code after it in the same block never runs. In a block holding `jump("## Target")` followed by `error("unreachable")`, the `error` is never reached. In the opening prompt, the line after `jump('## Help')` never runs, `## Accept` is skipped, and the run ends with `helped:forty-two`. A heading reference at a child level works the same way, as in `jump('### S1')`. [Jumping to a section](#jumping-to-a-section) covers sibling moves and backward-jump loops in full.

### What `call` does

`call(target, input?)` runs another section as a contained chain: a chain that starts at the target and [falls through](04-how-a-prompt-runs.md#the-section-walk) the target's following siblings until a section returns or the level runs out. The second argument is an optional string, as in `call('## Sub', 'chain-args')`, `call("## Child", "override")`, or `call('## Sub', '{"query":"chain"}')`. Leaving it out, as in `call('## Child')`, passes no call input. [Call input and `var`](#call-input-and-var) covers what the called sections see in each case.

`call` runs the section like a function and returns the contained chain's final text to the block as a plain Lua string. Here `## Main` calls its child `### Sub`, which falls through to `### After`:

````markdown
---
name: call-child
description: Calls a child section that falls through to its next sibling.
runvil: 0
---

# Call child

## Main

```lua
local r = call('### Sub')
return 'got:' .. r
```

### Sub

```lua
var.trail = 'Sub '
```

### After

```lua
return var.trail .. 'After'
```
````

````text
got:Sub After
````

`call('### Sub')` runs `### Sub` first. It has no `return`, so the contained chain falls through to the next sibling, `### After`, whose return ends the chain. That return value comes back as `r`, and `## Main` finishes its block with it. [Calling a section](#calling-a-section) covers contained chains in full.

### Children run only when named

A walk moves only across siblings, so a child section never runs by plain fall-through:

````markdown
---
name: child-stays-put
description: Shows that the walk skips a child section.
runvil: 0
---

# Child stays put

## A

```lua
var.trail = 'A'
```

### Child

```lua
error('a child must not run by fall-through')
```

## B

```lua
return var.trail .. ' B'
```
````

````text
A B
````

The walk runs `## A`, then moves to its next sibling, `## B`, and `### Child` with its `error` never runs. A child runs only when something names it by heading reference. `jump('### Child')` from its parent starts a walk over the children, and `call('### Child')` runs it, along with the children after it, and comes back. When `## A` runs `jump('### X')` over children `### X` and `### Y`, the walk runs `### X` and `### Y` and then resumes at `## B`, in the order A, X, Y, B. Without that `jump`, `### X` and `### Y` never run and only A and B do. When `## Main` runs `call('### Sub')`, `### Sub` and `### After` run, and then `## Main` resumes with the reply, as in the previous prompt.

## Resolving heading references

After this section you can write a heading reference that `jump`, `call`, and the other heading surfaces resolve, and read the error each malformed one gives.

````markdown
---
name: levels
description: Calls a child section by its heading reference.
runvil: 0
---

# Levels

## Main

```lua
return call('### Worker')
```

### Worker

```lua
return 'worker ran'
```

### Topics

```lua
return 'topics ran'
```
````

````text
worker ran
````

The heading reference `'### Worker'` resolves to the section with exactly that name at exactly that level, so among the level-3 siblings `Worker` and `Topics` it finds `Worker`. Change the call to `call('## Worker')` and the lookup fails, because no section that `## Main` can name sits at level 2 with the name `Worker`. The call raises the not-found error, whose message lists what `## Main` can name:

````text
section heading `## Worker` not found; available sections: ### Worker, ### Topics
````

### Level markers and exact names

The heading level markers are the leading `#` characters of a heading reference, and their count sets the level the reference matches, so `"## Name"` and `"### Name"` name different sections. A heading reference resolves only by an exact match on both the section's heading level and its section name. The name is compared as an exact string, with no prefix, fuzzy, or case-insensitive matching, and a reference whose `#` count differs from the section's level never finds it: `"## Worker"` does not find a level-3 section named `Worker` and fails with a not-found error, while `"### Worker"` finds it.

Write the markers, then whitespace, then the name. Any whitespace character separates the markers from the name, such as a space or a tab, and more than one is allowed. Leading and trailing whitespace around the whole string, and around the name after the markers, is ignored. Spaces inside a name are kept, so `"## My Section"` names a multi-word section with its inner spacing intact, and only the ends of the name are trimmed.

### One lookup for every heading surface

You name another section from Lua by its heading reference in six places: `call`, `jump`, [`prompt.section`](03-blocks-and-prose.md#reading-sections-with-promptsection-and-promptlist), [`prompt.list`](03-blocks-and-prose.md#reading-sections-with-promptsection-and-promptlist), [`tasks.spawn`](15-tasks.md#starting-a-task-with-tasksspawn), and `tasks.fanout` (see [What an arm sees](14-fanout.md#what-an-arm-sees)). All six look the string up the same way, by an exact match on heading level and name. `call`, `jump`, [`require`](03-blocks-and-prose.md#sharing-code-with-require), `tasks.spawn`, and `tasks.fanout` all write the target with its `#` markers, such as `'## Help'`, `'### X'`, or `'#### P'`, so `jump('## Help')` written in section `## Check` runs `## Help`.

`call`, `jump`, `tasks.spawn`, `prompt.section`, and `prompt.list` take their target as a Lua string and check its type with one shared rule before any heading lookup. Any value other than a string is an error.

A `call` target is looked up when the call runs, not when the prompt is parsed. A prompt containing `call('## Missing')` still parses and starts running, and the call fails only when it runs, where [`pcall`](05-lua-environment.md#raising-and-catching-errors) can catch the failure.

### Malformed heading references

A malformed heading reference is a string that is not one or more `#` markers, then whitespace, then a non-empty name. It is always an error, and it is never read as a bare name or as a different level. An unresolvable heading reference fails with a Lua error, and the checks run in a fixed order, so the first check that fails sets the message:

| Order | Check | Message |
| --- | --- | --- |
| 1 | The target is a string | `section target must be a string, got {type}` |
| 2 | The string starts with `#` markers | `section heading must include ### markers, got bare name: {stripped}` |
| 3 | A name follows the markers | `section heading has no name: {stripped}` |
| 4 | Whitespace follows the markers | `section heading must have whitespace after the {markers} markers: {stripped}` |
| 5 | A section matches | the not-found error, covered in [The not-found error](#the-not-found-error) |

In each message, `{stripped}` is the heading reference you wrote, with its surrounding whitespace trimmed.

- A non-string target raises a Lua runtime error at the call itself. `{type}` names the Lua type passed: `integer` for `42`, `number` for a float, `nil`, or `table`. A `pcall` around the call catches it, so `pcall(call, 42)` returns `false`.
- A target with no `#` markers, such as `"Worker"`, gives `section heading must include ### markers, got bare name: Worker`. The message says `###` whatever level you meant.
- A target made only of markers, such as `"###"` or `"### "`, gives `section heading has no name: {stripped}`. This check runs before the whitespace check, so `"###"` gives this message and not the whitespace one.
- A target with no whitespace between the markers and the name gives the whitespace message, where `{markers}` repeats `#` to the parsed level, as in `section heading must have whitespace after the ### markers: ###Name`.

### Prompt reads return `nil` on a miss

`prompt.section` and `prompt.list` look a heading up over the same visible sections, with the same parse rules, as `call` and `jump`, and they raise the same type and malformed-heading errors. A well-formed heading that matches no visible section returns `nil` from them instead of raising. So `42`, `'Nums'`, and `'##Nums'` raise errors containing `got integer`, `got bare name: Nums`, and `must have whitespace after the ## markers`, while `'## Missing'` and `'### Niece'` read as `nil`. `pcall(call, '## Missing')` still raises.

### Each heading reference names at most one section

A heading reference matches at most one section among the sections it can name. The parser rejects two sibling sections with the same name, with the error ``duplicate sibling section name `{name}` ... sibling section names must be unique`` (see [Sibling name uniqueness](02-file-structure.md#sibling-name-uniqueness)). It also rejects any heading that skips a level as an orphan heading (see [Section headings and nesting](02-file-structure.md#section-headings-and-nesting)), so a child section sits exactly one level deeper than its parent.

## The visible set

After this section you can tell which sections a given section can name, using its place in the section tree, and read the not-found error when a heading reference falls outside that set.

````markdown
---
name: visible-set
description: Probes which headings a section can name.
runvil: 0
---

# Visible set

## Main

```lua
local seen = {}
for _, h in ipairs({'## Sibling', '## Other', '### Kid', '## Main', '#### Grand', '### Niece'}) do
  seen[#seen + 1] = h .. '=' .. tostring(prompt.section(h) ~= nil)
end
return table.concat(seen, ', ')
```

### Kid

Kid notes.

#### Grand

Grand notes.

## Sibling

Sibling notes.

## Other

Other notes.

### Niece

Niece notes.
````

````text
## Sibling=true, ## Other=true, ### Kid=true, ## Main=false, #### Grand=false, ### Niece=false
````

From `## Main`, the headings `'## Sibling'`, `'## Other'`, and `'### Kid'` resolve, while `'## Main'` itself, its grandchild `'#### Grand'`, and the sibling's child `'### Niece'` read `nil`. `prompt.list` gives the same answers, and every other heading surface resolves the same headings.

### The visible set rule

The visible set is the set of sections a heading reference can name from the current section (see [Reading sections with `prompt.section` and `prompt.list`](03-blocks-and-prose.md#reading-sections-with-promptsection-and-promptlist)). From a walked section, it is that section's sibling sections at the same level, not counting itself, plus its own direct children. Every walked section's visible set follows that one rule. A block in the H1 body sees every top-level section and no nested section instead, so from the H1 body `prompt.section('## Main')` finds the top-level `## Main` and `prompt.section('### Kid')` reads `nil` (see [Jump and call in the H1 pass](#jump-and-call-in-the-h1-pass)).

A walked section's `jump`, `call`, `tasks.spawn`, and `tasks.fanout` targets, and its `prompt.section` and `prompt.list` reads, can name only its visible set: the other sections at its own level under the same parent, plus its own direct children. So a top-level `## A` can `jump('## B')` to a sibling or `jump('### X')` to one of its own children. All six surfaces resolve a heading through the same visible set, so from a given section they accept the same headings, and `pcall(tasks.spawn, '## Missing')` and `pcall(call, '## Missing')` fail with the same error text.

For the prompt above, this is what `## Main` can name:

| Heading | Relation to `## Main` | In the visible set |
| --- | --- | --- |
| `## Sibling`, `## Other` | siblings | yes |
| `### Kid` | direct child | yes |
| `## Main` | itself | no |
| `#### Grand` | grandchild | no |
| `### Niece` | a sibling's child | no |

A section's visible set never includes the section itself, so a section cannot name itself through `call`, `jump`, or the other surfaces. `## Self` running `call('## Self')` or `jump('## Self')` fails the run with an error whose message says `not found`, and `## Main` reading `prompt.section('## Main')` or `prompt.list('## Main')` gets `nil`.

A section can name a child heading one level below it, not just its siblings. `## Sub` can `jump('### S1')` to its child `### S1`, and `## Parent` can run `tasks.fanout('### Worker', ...)` on its child `### Worker`.

### What a nested section can name

A running child section has a visible set of its own: its own siblings plus its own children.

````markdown
---
name: child-reach
description: A child section calls its own child, then jumps to its sibling.
runvil: 0
---

# Child reach

## A

```lua
jump('### X')
```

### X

```lua
local r = call('#### Grand')
var.trail = 'X:' .. r .. ' '
jump('### Y')
```

#### Grand

```lua
return 'grand-ran'
```

### Y

```lua
var.trail = var.trail .. 'Y'
```

## B

```lua
return var.trail
```
````

````text
X:grand-ran Y
````

`## A` jumps into its child `### X`, which starts a [child walk](#child-walks). `### X` then calls its own child `#### Grand` and jumps to its own sibling `### Y`, both inside its own visible set. This is also how you reach a deeper section: a section cannot name its grandchildren directly, so it goes through the direct child that contains them, as `## A` reaches `#### Grand` here through `### X`. From `## Main` in the earlier prompt, `prompt.section('#### Grand')` and `prompt.list('#### Grand')` read `nil`, while its child `### Kid` reads its own text.

A section's grandchildren, its parent, and its parent's siblings are not in its visible set, because only its own level and its direct children are searched:

- A nested section cannot name its parent as a `jump`, `call`, `tasks.fanout`, or prompt-read target. `### Worker` running `jump('## Parent')` ends the run with [run error kind](16-limits-and-errors.md#run-error-kinds) `Lua` and a message containing `not found`, `pcall(call, '## Parent')` fails with `not found`, and `prompt.list('## Parent')` reads `nil`.
- A nested section cannot name its parent's siblings, its aunts and uncles. A running child that jumps to a section at its parent's level fails the run with a `not found` error, as when `### X` runs `jump('## B')` and `## B` sits at the level of `### X`'s parent.
- A section cannot name the children of its siblings, its nieces and nephews. `## A` running `jump('### Niece')` while `### Niece` sits under the sibling `## B` fails the run with a `not found` error, and `## Main` running `tasks.fanout('### Niece', {'x'})` while `### Niece` sits under `## Other` fails the same way.

[`require`](03-blocks-and-prose.md#how-require-finds-and-caches-libraries) reaches wider than this: it reaches the siblings of each ancestor. So `call` and `jump` from a nested section do not see that section's parent's sibling, while `require` does, as when `#### Self` cannot `call` or `jump` to `### Uncle` but `require` reaches it.

A heading that names a section outside the visible set behaves exactly like a heading that names no section anywhere, because only the visible set is searched. `'## Missing'` and `'### Niece'` both read `nil` from `prompt.section` and `prompt.list`, and `### X` running `jump('## B')` fails with `not found` even though `## B` exists.

### The not-found error

The not-found error is the Lua error a well-formed heading reference raises when it matches nothing in the visible set:

````markdown
---
name: missing-call
description: Calls a heading that no visible section has.
runvil: 0
---

# Missing call

## Main

```lua
return call('## Missing')
```

### Kid

Kid notes.

## Other

Other notes.
````

The run fails with run error kind `Lua`, and the not-found error's message names the missing heading and lists what `## Main` can name:

````text
section heading `## Missing` not found; available sections: ## Other, ### Kid
````

The message always has this form:

````text
section heading `{heading}` not found; available sections: {list}
````

`{heading}` is the heading reference with its surrounding whitespace trimmed. `{list}` shows each section in the visible set as its full heading reference with its `#` markers, such as `### Worker`, joined by a comma and a space, in visible-set order: the siblings in document order with the caller skipped, then the caller's children. So with only `### Worker` visible, `'## Worker'` gives ``section heading `## Worker` not found; available sections: ### Worker``.

The list names only the caller's visible set, its siblings and direct children. It never names the caller itself, a niece, a grandchild, or anything else in the document, so a not-found message shows exactly what a section can name without showing the rest of the document's structure. In the prompt above, the list names `## Other` and `### Kid` and leaves out `## Main`. A fanout worker section's failed `jump('## Parent')` lists only the worker section's own sibling `### Items`. Because the list is exactly the visible set, a not-found message is a quick way to see what a section can name.

Each surface treats a heading outside the visible set in one of two ways:

- `call`, `jump`, `tasks.spawn`, and `tasks.fanout` fail with the not-found error. All four report an unknown target through the same error, whose message includes the target heading, such as `## Nope`, and for the same heading the `tasks.spawn` message is exactly the one `call` produces.
- `prompt.section` and `prompt.list` read it as `nil` without raising. So in a fanout worker section, `pcall(call, '## Parent')` fails with `not found` while `prompt.list('## Parent')` reads `nil`.

Calling `call` or `jump` with a well-formed heading that matches no visible section fails with the not-found error, so `pcall(call, '## Missing')` returns `false` with a message containing ``section heading `## Missing` not found`` and `available sections:`. An uncaught heading error in a walked section ends the run with run error kind `Lua`, as when an unresolved `jump('## Parent')` ends the run.

## Jumping to a section

After this section you can move the walk to a sibling with `jump`, build a backward-jump loop, and know what happens to the rest of the jumping block.

````markdown
---
name: sibling-move
description: Jumps over a section to a later sibling.
runvil: 0
---

# Sibling move

## A

```lua
var.from_a = 'a'
jump('## C')
```

## B

```lua
error('the jump must skip B')
```

## C

```lua
var.from_c = 'c'
```

## D

```lua
return var.from_a .. var.from_c
```
````

````text
ac
````

### Sibling moves

Calling `jump('## Heading')` from a `lua` block moves the walk to another section within the same chain. A `jump` to a sibling, another section at the jumping section's own level, is a sibling move. The walk stays within the jumper's own group of siblings, every sibling except the jumper is a valid target, and the target starts as a fresh section entry and falls through from there.

After a sibling move the walk continues at the target, so the sections between the jumper and the target do not run, even one that would `return 'accepted'` or call `error('the jump must skip B')`, as `## B` does above. When the target finishes without a `return`, the walk falls through to the section that follows the target, not the one that follows the jumper, which is why `## D` runs after `## C`. When the target returns a value, that value becomes the run's text: a `jump('## Other')` to a `## Other` that does `return 'jumped'` ends the run with `jumped`.

A `jump` target shares the walk's `var`, so it sees every value written before the jump, including the jumping section's own writes, as `## C` and `## D` see `var.from_a`. Values the target writes to `var` stay visible to the sections the walk falls through to afterward, as `## D` sees `var.from_c`.

### Backward-jump loops

A sibling target can sit before or after the jumping section. A jump to an earlier sibling runs that sibling and every section after it again, including the jumper, so a backward jump makes a backward-jump loop:

````markdown
---
name: redraft
description: Jumps back to redraft until the draft has run three times.
runvil: 0
---

# Redraft

## Draft

```lua
var.trail = (var.trail or '') .. 'D'
```

## Review

```lua
if #var.trail < 3 then jump('## Draft') end
return 'passes: ' .. var.trail
```
````

````text
passes: DDD
````

Each time `## Draft` runs, it appends a `D`, and `## Review` jumps back while the trail is shorter than three characters. You end a backward-jump loop by guarding the `jump` with a value kept in `var`, as `if #var.trail < 3 then jump('## Draft') end` does here. Each time around starts from the `var` the previous time left, so the guard sees the count grow, and the third time `## Review` runs it reaches the `return`.

### What a jump ends

Code after a `jump` call in the same `lua` block never runs, because `jump` does not return to the jumper. A jump also skips every remaining block of the jumping section, so that section's later `lua` blocks never run:

````markdown
---
name: jump-ends
description: Shows that a jump skips the rest of its section.
runvil: 0
---

# Jump ends

## Check

```lua
var.checked = 'yes'
jump('## Help')
error('unreachable')
```

This prose is left behind.

```lua
error('the second block never runs')
```

## Help

```lua
return 'checked: ' .. var.checked
```
````

````text
checked: yes
````

`jump` checks that its argument is a string, records the target, and ends the block right there. A non-string argument fails with `section target must be a string, got {type}`. To end the block, `jump` raises its transfer marker: an error that unwinds the block from the point of the call. When the block ends, the recorded target takes precedence over that error. A `jump` is a control transfer, not a failure, so the jumping block reports success, not an error. `jump` is not a [suspending call](05-lua-environment.md#lua-blocks-section-vms-and-engine-globals), so nothing waits on the Harness when a block jumps.

### What crosses a jump

A section that jumps counts as completed, exactly as when it falls through, and its final `var` becomes the target section's starting `var`. The jumper reports that its section finished before the target reports that its section started (see [Logging and lifecycle reports](05-lua-environment.md#logging-and-lifecycle-reports)).

Only `var` crosses a jump. Locals, globals, and unread pending prose stay behind, because the target starts in a fresh [section VM](05-lua-environment.md#lua-blocks-section-vms-and-engine-globals) of its own, seeded only with `var`:

````markdown
---
name: what-crosses
description: Shows that only var reaches a jump target.
runvil: 0
---

# What crosses

## A

```lua
greeting = 'hi'
var.note = 'kept'
jump('## B')
```

## B

```lua
return tostring(greeting) .. ' ' .. var.note
```
````

````text
nil kept
````

The global `greeting` stays behind in `## A`'s section VM, so it reads `nil` in `## B`, while `var.note` arrives intact. Pass anything the target needs through `var`.

## Child walks

After this section you can start a child walk by jumping into a direct child, and predict where the walk goes when that child walk ends.

````markdown
---
name: child-walk
description: Jumps into a child level and returns to the parent level.
runvil: 0
---

# Child walk

## A

```lua
var.trail = 'A'
jump('### X')
```

### X

```lua
var.trail = var.trail .. ' X'
```

### Y

```lua
var.trail = var.trail .. ' Y'
```

## B

```lua
return var.trail .. ' B'
```
````

````text
A X Y B
````

### Starting a child walk

A `jump` to one of the running section's direct children, such as `jump('### X')` written in `## A`, descends into that section's children and starts a child walk at the named child. The child runs as part of that walk. A child walk falls through from the jump target to the target's following siblings at the same heading level, under the same rules as the top-level walk, so `### Y` runs after `### X` without being named.

A `jump` to a later child starts the child walk at that target, so the target's earlier siblings never run. With children `### X`, `### Off`, and `### Y`, `jump('### Off')` from `## A` runs `### Off` and then `### Y`, and `### X` never runs.

### Where the walk resumes

When a child level runs out of sections, the parent walk resumes at the section after the jumper, not after the jump target. So after `### Y`, the next section to run is `## B`, the sibling after `## A`, which made the jump.

A child level returns control to its parent's level only by running out of siblings. A running child cannot `jump` up to a section at its parent's level, so `jump('## B')` written in `### X` fails with a `not found` error, because the parent's level is not in the running child's visible set.

A running child section can `jump` to its own sibling, and when that child level runs out, the parent walk still resumes after the section that jumped into the child level. So `jump('### Y')` from `### X` runs `### Y`, and `## B` runs next.

A parent section and its child level share `var`. The children start from the jumper's final `var`, and the section the parent walk resumes at starts with the `var` left by the child level's last section, which is why `## B` sees every piece of the trail the earlier sections appended.

### Nested child walks

Child walks nest to any depth. A `jump` from an H3 child to its own H4 child starts an H4-level walk, and each level that runs out resumes its parent level after that level's jumper:

````markdown
---
name: nested-walk
description: Jumps two levels down and climbs back up.
runvil: 0
---

# Nested walk

## A

```lua
var.trail = 'A'
jump('### X')
```

### X

```lua
var.trail = var.trail .. ' X'
jump('#### P')
```

#### P

```lua
var.trail = var.trail .. ' P'
```

#### Q

```lua
var.trail = var.trail .. ' Q'
```

### Y

```lua
var.trail = var.trail .. ' Y'
```

## B

```lua
return var.trail .. ' B'
```
````

````text
A X P Q Y B
````

After `#### Q`, the H4 level runs out, so the walk resumes after its jumper, `### X`, at `### Y`. Then the H3 level runs out, so the walk resumes after `## A` at `## B`. The run climbs back from H4 to H3 to the top level in order.

### Returning from a child walk

A scalar `return` inside a jump-started child walk ends the whole chain (see [Returning values](04-how-a-prompt-runs.md#returning-values)), so neither the child level's remaining sections nor the parent walk's next sibling run. Change `### X` in the first prompt of this section to return a value:

````markdown
### X

```lua
return 'x-value'
```
````

````text
x-value
````

Neither `### Y` nor `## B` runs, and the run result is `x-value`.

## When a jump is refused or fails

After this section you can predict every case where `jump` is refused or fails, and read the message each one gives.

````markdown
---
name: caught-jump
description: Catches a jump with pcall and still lands on the target.
runvil: 0
---

# Caught jump

## A

```lua
local ok = pcall(jump, '## C')
var.ok = tostring(ok)
return 'never the result'
```

## B

```lua
error('the jump must skip B')
```

## C

```lua
return 'C saw ok=' .. var.ok
```
````

````text
C saw ok=false
````

`pcall` catches the transfer marker, so the block keeps running, but the jump is already recorded. When the block ends, the jump wins: `return 'never the result'` is dropped, `## B` is skipped, and `## C` sees the `var` the block left. This table lists every case, and the subsections below explain each one:

| Case | What happens | Message |
| --- | --- | --- |
| `jump` inside `pcall` | the jump still lands | `jump transfer`, returned to `pcall` |
| A non-string target | the `jump` call raises | `section target must be a string, got {type}` |
| A heading outside the visible set | the run fails after the block ends | the not-found error |
| A malformed heading reference | the run fails after the block ends | the matching heading-format error |
| Inside an adhoc tool handler | `jump` is refused | the adhoc tool handler refusal |
| While a library loads | `jump` is refused | the message in [Jumps while a library loads](#jumps-while-a-library-loads) |

### Catching a jump with `pcall`

Each Lua block starts with no pending jump, so a `jump` affects only the block that recorded it and never spills over from one block into the next.

`pcall` cannot cancel a jump. A `jump` made inside `pcall`, directly or in a function that `pcall` runs, still records its target. `pcall(jump, heading)` returns `false`, the error it returns holds the text `jump transfer`, and the rest of the block keeps running after the `pcall`. How the block ends then makes no difference:

- When the block ends normally, the jump wins and the block's return value is discarded. After `local ok = pcall(jump, '## C')`, a later `return 'never the result'` is dropped and the walk moves to `## C`.
- When the block ends with an error, the jump wins and the error is discarded, so the walk moves to the recorded target instead of failing on the block's error.

### Lookup after the block ends

At the call itself, `jump` checks only that its target is a string. The heading reference is looked up only after the jumping block has ended, and the walk then continues at that heading. The jumping section closes as completed, with its final `var` saved, before the heading is looked up, so it still reports that its section finished even when the target fails to resolve. Because the lookup runs after the jumping section has closed, a bad `jump` target never raises an error inside the jumping block, and `pcall` there cannot catch it.

A `jump` whose heading matches no visible section fails the run with the not-found error, ``section heading `{heading}` not found; available sections: {list}``. The list names only the jumper's visible set, so a fanout worker section's failed `jump('## Parent')` lists the worker section's own sibling `### Items` and nothing else.

A `jump` whose heading reference is malformed, such as a bare name, a heading made only of `#` markers, or markers with no whitespace after them, also fails the run after the jumping block has ended, with the matching message from [Malformed heading references](#malformed-heading-references). Every failed `jump` lookup has run error kind `Lua`.

### The adhoc tool handler refusal

An [adhoc tool](12-tools.md#how-adhoc-tool-handlers-run)'s handler cannot jump. Calling `jump` inside an adhoc tool handler raises a Lua error with this message, the adhoc tool handler refusal:

````text
jump is unavailable inside an adhoc tool handler: return a value from the handler and call jump from the block after the tool call returns
````

So the handler returns a value, and the block calls `jump` after the tool call returns. The run fails if nothing catches the refusal. The rules around it:

- `jump` is refused while an adhoc tool's handler runs, whether you called the tool with [`tools.call`](12-tools.md#calling-tools-from-lua-and-counting-calls) or the model called it during [`models.loop`](11-conversations.md#starting-a-conversation), and no jump lands in either case.
- The refusal holds even when the handler calls `jump` through a reference saved before the adhoc tool was defined, such as `local j = jump` followed by `j('## Other')`. The error still begins `jump is unavailable inside an adhoc tool handler`.
- A refusal the handler does not catch fails the handler and is raised where the tool was called. So `pcall(tools.call, ...)` in the block catches it, returns `false` with the refusal text, and the block keeps running.
- `jump` stays refused in an outer handler after a nested handler it called has returned. After the outer handler's `tools.call('adhoc/inner', {})` returns, where `adhoc/inner` is the id of another adhoc tool, named `inner`, `pcall(jump, '## Other')` inside the outer handler still fails with the refusal message.
- `jump` works again in the block once the outermost adhoc tool handler returns or fails, whether the handlers ran under `tools.call` or `models.loop`, including after `pcall(tools.call, ...)` has caught the handler's own error.

Here the block defines an adhoc tool, `grab`, with [`tools.adhoc`](12-tools.md#adhoc-tools-with-toolsadhoc). Its handler calls `jump`, and the block catches the refusal and then jumps on its own:

````markdown
---
name: refused-jump
description: Catches a handler's refused jump, then jumps from the block.
runvil: 0
---

# Refused jump

## Main

```lua
local grab = tools.adhoc({ name = 'grab', description = 'Grab a value' }, function() jump('## Other') end)
local ok, err = pcall(tools.call, grab, {})
jump('## Other')
```

## Skipped

```lua
error('the jump must skip this section')
```

## Other

```lua
return 'jumped'
```
````

````text
jumped
````

`ok` is `false` and `err` holds the refusal text. Once the handler has failed, `jump` works again, so the block's own `jump('## Other')` transfers, `## Skipped` never runs, and `## Other` ends the run with `jumped`.

### Jumps while a library loads

A [library](03-blocks-and-prose.md#sharing-code-with-require) cannot jump while it loads. Calling `jump` while a library loads through `require` raises a Lua error with this message:

````text
jump is unavailable while a library loads: return a value from the library and call jump from the section after require returns
````

So the library returns a value, and the section calls `jump` after `require` returns. Library loads nest, and `jump` stays refused until every load under way has ended, so a library's load-time code still cannot `jump` after a library it required has finished loading. When a library load ends, whether it finishes, raises an error, or is cancelled, `jump` works again for the caller. So after `pcall(require, '## a')` fails on the library's refused jump, `jump '## after'` in the same block transfers to `## after`.

A function that a library defines can call `jump` once the library has finished loading, and the jump transfers the section that called the function:

````markdown
---
name: library-jump
description: Calls a library function that jumps.
runvil: 0
---

# Library jump

## Main

```lua
require '## a'
go()
```

## a

```lua
function go() jump '## next' end
```

## next

```lua
return 'next ran'
```
````

````text
next ran
````

`require '## a'` loads the library, which only defines `go`. By the time `## Main` calls `go()`, the load has ended, so the `jump` inside `go` transfers `## Main` to `## next`, and the walk skips `## a`.

## Calling a section

After this section you can run a section with `call`, use the value it hands back, and place subroutine sections so they run only when called.

````markdown
---
name: research
description: Calls a subroutine section and uses its reply.
runvil: 0
---

# Research

## Main

```lua
local found = call('## Research')
return 'found: ' .. found
```

## Research

```lua
return 'research-reply'
```
````

````text
found: research-reply
````

`call(target, input?)` takes a heading reference for the target section, such as `'## Research'`, plus an optional call input string, and starts a called chain at that target. It runs the named section, such as a sibling, as a contained chain and returns that chain's final returned value to the calling block, so `local found = call('## Research')` gets `research-reply` because `## Research` ends by returning that text. The optional call input reaches the called section as `args`, and the called section can read values the caller stored in `var` (see [Call input and `var`](#call-input-and-var)).

When the called chain returns, its result becomes the value of `call`, the calling block keeps running, and when that block finishes, the walk resumes at the section after the caller. Here `## Main` returns, so the run ends with `found: research-reply`, and the walk never reaches `## Research` on its own.

### Contained chains

A chain started by `call` begins at the target and falls through to the target's following siblings under the same rules as the main walk, until a section returns or the level runs out. The returning section's value is what `call` gives back:

````markdown
---
name: call-chain
description: Calls a section whose chain falls through to the next sibling.
runvil: 0
---

# Call chain

## A

```lua
var.result = call('## S1')
```

## B

```lua
return 'B saw: ' .. var.result
```

## S1

```lua
var.trail = 'S1 '
```

## S2

```lua
return var.trail .. 'S2'
```
````

````text
B saw: S1 S2
````

`## S1` has no `return`, so the contained chain falls through to `## S2`. Within a called chain, `var` rolls forward across the chain's sections as it does on the main walk, so the `var.trail` that `## S1` stores is readable in `## S2`. `## S2`'s return ends the chain, `call` returns `S1 S2` to `## A`, and the walk resumes at `## B`.

A `return value` inside a called chain ends only that chain, not the run (see [Returning values](04-how-a-prompt-runs.md#returning-values)). The value becomes what `call` returns, the chain's remaining sections are skipped, and the outer walk keeps going. `call` returns the final reply of its contained chain from whichever section ends it with a return, even when that section is a later sibling of the target rather than the section you named, so a call to `## Sub` that falls through to a `## Tail` returning `'tail-reply'` gives `tail-reply`. A called chain that runs off its last section without returning gives `call` the empty string. Only the top-level run substitutes a generic completion text in that case (see [The run result and outcomes](04-how-a-prompt-runs.md#the-run-result-and-outcomes)).

A section can `call` any other sibling at its level, earlier or later in the file, even one that sits past other sections, such as `## A` calling a `## Sub` that sits after `## B`.

### Using the value `call` returns

After `call` returns, the calling block keeps running its remaining statements, and the block's own `return` becomes its reply. The value `call` returns is a Lua string, so you can use it in several ways:

- Use it directly inside an expression, such as concatenating it with `..` in `return var.first .. ',' .. call('## Answer')`.
- Write `call('## Sub')` as a plain statement that discards the chain's reply. The block's following statements still run.
- Return it directly with `return call(...)`, which passes the called section's return value straight through as the section's own result.

Calls nest, so a called section can `call` another section and hand that result back to its own caller, as with `return call('## Inner')`.

You can `call` the same section more than once, and each call runs the section again as a separate child with its own [`sys.id`](05-lua-environment.md#section-entry-ids), and returns that run's reply:

````markdown
---
name: call-twice
description: Calls the same section twice.
runvil: 0
---

# Call twice

## Main

```lua
local first = call('## Sub')
local second = call('## Sub')
return first .. ',' .. second
```

## Sub

```lua
return sys.id
```
````

````text
0.0.0,0.1.0
````

### Calling a child section

`call` on a direct child of the calling section, such as `call('### Sub')` from `## Main`, starts a contained chain at that child, which falls through the child's following siblings. `call` resolves its heading against the same visible set as `jump`, and a direct child target is found among the caller's own children, so the chain runs at the child level, while any other target runs at its own level.

A `call` to a later child starts at that child, so the caller's earlier children never run, and the chain falls through the following child siblings and returns the reply of the one that returns:

````markdown
---
name: call-later-child
description: Calls the second of three children.
runvil: 0
---

# Call later child

## Main

```lua
local r = call('### Sub2')
return 'got:' .. r
```

### Sub1

```lua
error('an earlier child must not run')
```

### Sub2

```lua
var.trail = 'Sub2 '
```

### Sub3

```lua
return var.trail .. 'Sub3'
```
````

````text
got:Sub2 Sub3
````

A running child section can `call` one of its own children and use the value that child returns, such as `### X` running `local r = call('#### Grand')` and getting `grand-ran`.

Fall-through in a `call` chain moves to the next sibling and does not walk into the called section's own child sections. A `call` chain also stays within the target's heading level: when that level runs out, the chain ends and control returns to the caller instead of continuing into the parent level, so a call into an H4 child does not continue into the H3 sibling that follows it.

### Subroutine sections

A subroutine section is a section that runs only when something calls it. Children make natural call targets because the walk never reaches them by fall-through. A section the main walk never reaches, such as one placed after the section whose `return` ends the run, still runs when `call` or `tasks.fanout` names it directly, so you put `call` targets there to have them run only when called. That is how `## Research` works in the first prompt of this section.

A called section starts a chain that falls through to the sections after it like any walk. So a call target placed before other sections pulls them into its chain, while the main walk also runs the target on its own. Subroutine sections belong after the section that ends the run.

### Jumps inside a called chain

A `jump` inside a section reached through `call` is contained by the call chain. It is followed rather than rejected, and the chain's final result, such as the jump target's reply, becomes what `call` returns:

````markdown
---
name: jump-in-call
description: A called section jumps to a later sibling.
runvil: 0
---

# Jump in call

## Main

```lua
local r = call('## Sub')
return 'main:' .. r
```

## Sub

```lua
jump('## Peer')
```

## Skipped

```lua
error('the chain jump must move past me')
```

## Peer

```lua
return 'peer-ran'
```
````

````text
main:peer-ran
````

When a `jump` inside a call chain moves to a later sibling, the sections between the jumping section and the target do not run, as `## Skipped` shows. After the jump, the chain continues from the target under the normal fall-through rules, so a target with no return falls through to its next sibling, and the chain's final reply is the call's return value. A called section can also `jump` to one of its own children, which starts a child walk inside the call that falls through the child's following siblings, and that level's final return becomes the result of `call`: when `## Sub` runs `jump('### S1')` and `### S2` returns `'s2-result'`, the caller gets `s2-result`.

The outer walk never moves while a called chain runs. Wherever the chain ends, even after a `jump` inside it to a section past the caller, the caller resumes and the walk continues at the section after the caller.

### How a called chain runs

A `call` runs its target as a new child chain of the caller, one call level deeper (see [How call depth counts](#how-call-depth-counts)) and with the caller's next child `sys.id`. The Engine drives it the same way it drives the walk.

`call` suspends its caller. The calling block pauses at `call` while the target runs in its own section VM, so the caller and the called chain never interleave, and the called chain's model rounds come before the caller's later ones. A called section can make its own model rounds with [`models.infer`](10-models.md#running-a-round-with-modelsinfer), each a separate model round of the run, and it can return a model's reply, which reaches the caller unchanged. When `## Inner` does `return models.infer('inner ask')`, a caller that runs `local inner = call('## Inner')` gets the model's reply in `inner`.

A function from a `require`d library that runs `call` later resolves the heading from the section calling it, so it can call that section's own child. When a library defines `later()` with `call('### mine')` inside, and a section with a child `### mine` that does `return 'mine'` calls `later()`, the `call` returns `mine`.

A called section cannot name itself, and the only Lua values it gets from its caller are the ones [Call input and `var`](#call-input-and-var) describes.

## Call input and `var`

After this section you can pass a call input to a called section and read it there, and know which `var` values and which `argv` the called chain starts with.

````markdown
---
name: pipeline
description: Passes one section's text to the next as its call input.
runvil: 0
---

# Pipeline

## Main

```lua
local draft = 'refined text'
return call('## Deliver', draft)
```

## Deliver

```lua
return 'delivered: ' .. args
```
````

````text
delivered: refined text
````

### Passing a call input

A call input is the string you pass as the second argument to `call`, as in `call('## Child', 'override')`. The called chain receives that string as its own argument string instead of the one it would otherwise inherit. In every section the chain runs, the [`args` global](06-arguments.md#the-argument-string-in-args) holds the call input instead of the run's own argument string, and the prose of any section in the chain reads the call input too. So in a run started with the argument string `{"query":"run"}`, `call('## Sub', '{"query":"chain"}')` makes `args` read `{"query":"chain"}` inside `## Sub`. To build a call input from a table, encode it with [`json.encode`](05-lua-environment.md#encoding-and-decoding-json-with-json), as in `call('## Sub', json.encode({ query = 'chain' }))`, which passes the same text. The chain reads it back through `argv` under an explicit `args:` declaration, as [`argv` in a called chain](#argv-in-a-called-chain) shows, or with `json.decode(args)` under any declaration.

A [placeholder](07-substitution.md#placeholders-in-prose) `{{ args }}` inside a called chain renders the call input instead of the run's argument string. In a run started with the argument string `run-args`, `call('## Sub', 'chain-args')` makes the chain's model round receive `chain-args` and not `run-args`. A `call` with a call input leaves the caller's own `args` unchanged, so the calling section's `args` still holds its own argument string after the call.

You build a pipeline by passing a string one section produced as the next section's call input, as `## Main` does above with `return call('## Deliver', draft)`. The run's own argument string makes no difference: when `## Refine` ends with `return call('## Deliver', messages[#messages].content)`, the run's result is `delivered: refined text`, even though the run itself started with the argument string `quantum`.

### Inheriting the caller's arguments

A `call` with no call input inherits its caller's argument string. From the main walk that is the run's argument string, and inside a called chain that was given a call input it is that chain's call input, not the run's, because a call input applies to the whole chain:

````markdown
---
name: inherit-input
description: A nested call with no call input inherits the chain's call input.
runvil: 0
---

# Inherit input

## Main

```lua
return call('## Sub', 'chain-args')
```

## Sub

```lua
return call('## Inner')
```

## Inner

```lua
return 'Inner saw ' .. args
```
````

````text
Inner saw chain-args
````

A `call` with the call input left out or passed as `nil` makes the called chain inherit the caller's arguments whole: both its `args` and its frozen [`argv`](06-arguments.md#reading-parsed-input-from-argv), including any [`argv` repair](06-arguments.md#checking-and-repairing-argv-in-the-h1-pass) the H1 made. With the run argument string `broken` and an H1 block that sets `argv = { query = 'repaired' }`, `argv.query` reads `repaired` in a `## Sub` reached by `call('## Sub')`. A [`tasks.spawn`](15-tasks.md#starting-a-task-with-tasksspawn) without an argument string inherits the same way.

An empty string counts as a call input, so `call(heading, '')` sets the called chain's `args` to `''` instead of inheriting the caller's argument string. Only a `nil` or missing call input inherits.

### `argv` in a called chain

A call given its own call input gets a fresh `argv` built from that string under the prompt's `args:` declaration, and that `argv` is then [frozen](06-arguments.md#freezing-argv-after-the-h1-pass) for the whole chain:

````markdown
---
name: chain-argv
description: Gives a called chain its own argv.
runvil: 0
args:
  query:
    type: string
---

# Chain argv

## Main

```lua
return call('## Sub', '{"query":"chain"}')
```

## Sub

```lua
local wrote = pcall(function() argv.query = 'x' end)
return argv.query .. ':' .. tostring(wrote)
```
````

Run with the argument string `{"query":"run"}`, the prompt returns:

````text
chain:false
````

Inside `## Sub`, `argv.query` reads `chain` from the call input, not `run` from the run's argument string. The chain's `argv` is frozen, so a field write such as `argv.query = 'x'` fails, and `pcall` catches it, leaving `wrote` false. How the call input becomes `argv` depends on the frontmatter:

| Call input | `args` in the chain | `argv` in the chain |
| --- | --- | --- |
| Left out or `nil` | the caller's `args` | the caller's frozen `argv` |
| A string, no `args:` key | the string | the string as `argv.prose` (see [Prompts with no `args:` key](06-arguments.md#prompts-with-no-args-key)) |
| A string, with an `args:` key | the string | the string parsed as JSON |
| A string that is not JSON, or is JSON `null`, with an `args:` key | the string | `nil` |

Under an explicit `args:` declaration, a call input that does not parse as JSON, or parses as JSON `null`, gives the called chain a nil `argv`, while the chain's `args` still holds the string.

### Call input errors

A call input is a string, `nil`, or left out. Two errors fail the call at the call site:

- Any other value fails with `input must be a string, got {type}`, where `{type}` is the Engine's type name, such as `integer` for `5`, `number` for `1.5`, or `table` for a table.
- A call input string that is not valid UTF-8 fails with `input must be a valid UTF-8 string`.

### The `var` snapshot

Every `call` gives the called chain a `var` snapshot: a plain deep copy of the caller's `var` data, taken at the moment of the call. There is nothing to pass, since every `call` takes the copy on its own:

````markdown
---
name: var-snapshot
description: A called section reads the caller's var but cannot write back.
runvil: 0
---

# Var snapshot

## Main

```lua
var.shared = 'caller'
local r = call('## Sub')
assert(var.child_write == nil)
return r
```

## Sub

```lua
var.child_write = 'sub'
return 'sub saw ' .. var.shared
```
````

````text
sub saw caller
````

A section run through `call` reads the `var` values the caller set before the call, because it receives a copy of the caller's `var`: a caller `var` holding `k = 1` arrives in the chain holding `k = 1`. This holds through nesting too, so a `#### Leaf` reached through a `call` inside a task reads a `var.who` that only the main section set. A `tasks.fanout` takes the same copy.

Writes the called chain makes to its copy of `var` stay in the chain and are discarded when it ends, so they never reach the caller. That is why `assert(var.child_write == nil)` still passes in `## Main` after the call. A child section entered with `call` also starts without the Lua globals its parent defined, so with `greet` defined in `## Parent`, `tostring(greet)` in `### Child` renders `nil`.

Because a called chain's `var` writes never reach the caller, pass results back as the chain's return value, which `call` hands to the caller, as `local r = call('## Sub')` gets `sub saw caller` from the `return` in `## Sub`.

The snapshot needs the `var` global itself to stay in place. After you reassign the `var` global, as in `var = 5`, the next `call` or `tasks.fanout` fails at the call when it takes its copy of `var`, with an [error value](05-lua-environment.md#error-values) of kind `lua` and this message:

````text
the `var` global was reassigned; write `var.<field>` instead
````

So write `var` field by field, as `var.<field>`.

## Catching call failures

After this section you can catch a failed `call` with `pcall`, fall back to another value, and read what the caught error value holds.

````markdown
---
name: risky
description: Falls back when a called section fails.
runvil: 0
---

# Risky

## Main

```lua
local ok, result = pcall(call, '## Risky')
if not ok then
  return 'fallback'
end
return 'risky said ' .. result
```

## Risky

```lua
error('risky failed')
```
````

````text
fallback
````

### What `pcall` catches

You catch a failing `call` by writing `pcall(call, '## Section')`, which returns `false` and the error value instead of ending the run. To fall back after a failed call, check the first value from `pcall(call, ...)`. It is `true` followed by the called chain's result on success, and `false` followed by the failure on error, as in `local ok, result = pcall(call, '## Risky')` with a fallback when `ok` is false. If `## Risky` returned `'fine'` instead, the run above would end with `risky said fine`.

A `pcall` around a `call` catches each of these, and each comes back to the calling block as the call's ordinary error instead of ending the run:

- A call refused by the recursion depth cap (see [The recursion depth cap](#the-recursion-depth-cap)).
- A call whose target heading is outside the visible set, which fails with an error containing `not found`.
- A call whose called chain ends in an error, such as one whose error value has `err.kind == 'tasks_live'` when the called chain ends with a live task (see [Task events and task errors](15-tasks.md#task-events-and-task-errors)).

Heading errors from `call` are raised at the call site as the call's own error, so a `pcall` inside the calling block catches them. That covers a non-string target, as when `pcall(call, 42)` fails with `section target must be a string`, and a heading that names no visible section, which fails with a message that contains `not found` and names the heading. A `call` to a heading that no section has raises a Lua error, unlike the prompt reads, which return `nil` for a missing heading.

### What the caught error value holds

A failed `call` raises the Engine's error value in the calling block, so `pcall(call, ...)` returns `false` and that error value. A failure inside a `call` reaches the caller as the called chain's own error value, with its `kind`, its message, and its kind's fields intact, and it is never rewrapped as a generic `call` failure. Here `## Leaky` starts a task with `tasks.spawn` and ends while that task is still live:

````lua
local ok, err = pcall(call, '## Leaky')
````

`ok` is `false`, `err.kind` is `'tasks_live'`, `err.tasks` is `'0.0.0'`, and the message names `0.0.0`.

### What `pcall` cannot catch

A determinism violation inside a called chain (see [Races, claims, and determinism](09-the-store.md#races-claims-and-determinism)) is not caught by a `pcall` around the `call`. It ends the run on the spot instead of coming back to the caller as the call's error.

## The recursion depth cap

After this section you can predict how deep calls nest, how call depth counts across `call`, tasks, and fanout arms, and what the `CallDepthExceeded` failure looks like.

````markdown
---
name: ping-pong
description: Two sections call each other until the depth cap stops them.
runvil: 0
---

# Ping pong

## Alpha

```lua
return call('## Beta')
```

## Beta

```lua
return call('## Alpha')
```
````

The prompt parses and runs until the recursion depth cap fires, and then the run fails with run error kind `Quota` and this message:

````text
call recursion exceeded cap of 8
````

### Mutual recursion

A section's visible set excludes the section itself, for `call` just as for `tasks.spawn`, so a section cannot target itself: `return call('## Self')` written in `## Self` fails with a `not found` error. Recursion has to go between two different sections that target each other, such as `## Alpha` and `## Beta` above. That is mutual recursion: two sections call each other back and forth, each nested call adding one level of call depth, until the recursion depth cap stops them. In the prompt above, `## Alpha` runs at depths 0, 2, 4, 6, and 8. The pair can be child sections too, such as `### X` running `return call('### Y')` and `### Y` running `return call('### X')`.

The recursion depth cap is the limit of 8 nested calls along one call chain, so unbounded mutual recursion such as `## A` running `return call('## B')` and `## B` running `return call('## A')` fails the run when it reaches the cap of 8. Calls nest up to 8 levels deep, counting the first call, and the ninth nested call fails the run with the message `call recursion exceeded cap of 8`.

You can catch the refusal with `pcall` at the deepest level:

````markdown
---
name: caught-cap
description: Catches the depth cap refusal and reports its kind.
runvil: 0
---

# Caught cap

## Alpha

```lua
local ok, r = pcall(call, '## Beta')
if ok then return r end
return 'caught ' .. r.kind
```

## Beta

```lua
return call('## Alpha')
```
````

````text
caught lua
````

`## Alpha` at depth 8 cannot start `## Beta` at depth 9, so its `pcall` returns `false` and the depth cap error. It returns `caught lua`, and each level above it hands that reply straight back up to the top.

### How call depth counts

Call depth is how many nested calls deep a section runs. A top-level section runs at call depth 0, which is why `## Alpha` first runs at depth 0. Call depth accumulates through nested calls: each `call` runs the called section at its caller's depth plus one, and that called section keeps its depth, while the main walk of top-level sections always starts at depth 0. The [H1 pass](04-how-a-prompt-runs.md#the-h1-pass) and the top-level section walk both start at the root call depth of 0, so `call` nesting and its cap count from there.

The cap is 8. A section or fanout arm may run at depth 8, but nothing it starts may run at depth 9, so an arm already running at depth 8 cannot `call`. The cap is fixed and cannot be configured.

A `jump` never adds a level of call depth. A descent into a child level is not a call, so the child walk shares the chain's call depth, and jumping into children spends no depth. When `## Main` does `jump('### X')` and then `### X` and `### Y` ping-pong with `call`, nine section entries run before the cap trips, in the order `x`, `y`, `x`, `y`, `x`, `y`, `x`, `y`, `x`, where a descent that spent depth would allow only eight.

### Depth across tasks and fanout arms

Each `call`, each task started by `tasks.spawn`, and each [fanout arm](14-fanout.md#what-an-arm-sees) adds one level toward a single recursion depth cap of 8 levels, first call included. Depth adds up across all three, task boundaries included, so sections that call each other in turn add one level per call.

| Started by | Adds a level | Message at the cap |
| --- | --- | --- |
| `call` | yes | `call recursion exceeded cap of 8` |
| `tasks.spawn` | yes | `call recursion exceeded cap of 8` |
| A `tasks.fanout` arm | yes | `tasks.fanout recursion exceeded cap of 8` |
| `jump` | no | none |

Each `tasks.fanout` arm runs one call level deeper than the section that called `tasks.fanout`, so arms count against the same cap that `call` uses. A section at depth 7 can still fan out, but a `call` made inside one of its arms trips the cap: when `B` fans out at depth 7 over `W`, whose body is `return call('## C')`, the arm's `call` fails with `call recursion exceeded cap of 8`, and uncaught it fails the run with a `Quota` error.

Call depth accumulates across a fanout boundary, so fanning out does not reset the depth, and a section already at the cap cannot fan out, because its arms would run one level deeper still. In a ping-pong like the one above where `## Alpha`, on its fifth run at depth 8, does `return tasks.fanout('## W', {'x'})` instead of calling `## Beta`, the run fails with a `Quota` error whose message contains `tasks.fanout recursion exceeded cap of 8`. Calling `tasks.fanout('### Worker', {'x'})` from any section at depth 8 fails the same way, with the depth cap error named after the fanout, since its arms would run at depth 9.

A fanout whose arms would pass the cap starts no arm. Every arm would run at the same depth, so the first arm's start is refused, the fanout stops, and it raises the depth cap error, which `pcall` reads as kind `lua`. Its message reads exactly `tasks.fanout recursion exceeded cap of 8`, with no `call` wording and no prefix, and it reaches the run unchanged through every enclosing `call` level, with no Lua runtime-error prefix or traceback. Even after eight nested `call` levels, the run's error text is exactly `tasks.fanout recursion exceeded cap of 8`.

Tasks started by `tasks.spawn` run one call level deeper than the section that spawned them, count toward the same cap as `call`, and share `call`'s target resolution too. A task refused at the cap reads `call recursion exceeded cap of 8` when `tasks.spawn` started it, the same message as `call`, and `tasks.fanout recursion exceeded cap of 8` when it is a fanout arm. A `tasks.spawn` refused at the cap is the spawn's own answer, so `pcall(tasks.spawn, ...)` catches it in the spawning block instead of ending the run. With `local ok, err = pcall(tasks.spawn, '## Beta')` followed by `log(tostring(err))`, the deepest section logs `call recursion exceeded cap of 8`, and the run then ends with the `done` fallback or a `tasks_live` error rather than with the cap error. When nothing catches the refusal and it ends the run, it is a `Quota` error.

The `depth` field of [`tasks.status`](15-tasks.md#checking-on-a-task-with-tasksstatus-tasksready-and-tasksnote) reports a task's call depth, the same count the cap checks, so `tasks.status(t).depth` reads 1 for a task spawned from the main walk.

### The `CallDepthExceeded` failure

`CallDepthExceeded` is the dedicated depth cap error. Its message holds the cap of 8 and names the kind of call that tripped it, in the form `<call form> recursion exceeded cap of 8`:

- `call recursion exceeded cap of 8` for a `call` or a `tasks.spawn`.
- `tasks.fanout recursion exceeded cap of 8` for a `tasks.fanout` arm.

Going past the cap with `call` fails with the message `call recursion exceeded cap of 8`, exactly.

Caught with `pcall`, the depth cap error is an error value of kind `lua`, the same kind as any Lua-phase failure, with no dedicated kind of its own, so a `pcall` handler that already branches on the `lua` kind keeps working. The caught error value holds only its kind and message, with no extra field for the depth reached or for the cap.

Uncaught, the depth cap error reaches the run outcome unchanged through every calling section, so you see the specific depth cap error and its message rather than a generic wrapper. It fails the run with run error kind `Quota`, for both the `call` form and the `tasks.fanout` form of the message. So a `call` that would nest past the cap ends the run with run error kind `Quota` and a message that names the cap, `call recursion exceeded cap of 8`.

## Jump and call in the H1 pass

After this section you can use `jump` and `call` from the H1 body, and know which sections they can name there.

````markdown
---
name: answer-once
description: Calls a section once from the H1 and keeps the result.
runvil: 0
---

# Answer once

```lua
var.answer = call('## Answer')
```

## Result

```lua
return var.answer
```

## Answer

```lua
return 'called from h1'
```
````

````text
called from h1
````

`call`, `jump`, and `tasks.fanout` work in the H1 `lua` block just as they do in sections. Here the [H1 pass](04-how-a-prompt-runs.md#the-h1-pass) calls `## Answer`, keeps its reply in `var`, and the walk then starts at `## Result`. In the same way, `tasks.fanout('## Worker', {'a', 'b'})` in the H1 runs its arms over the top-level `## Worker`.

### The H1 visible set

The H1 visible set is the whole top level: every `##` section, with nothing excluded and no children. Because of that, code in the H1 body can `jump` to, `call`, or read any top-level section, and only top-level sections are valid `jump` targets from the H1. `prompt.section` and `prompt.list` work from the H1 and read over its visible set, so `prompt.section('## Main')` is not `nil` there while `prompt.section('### Kid')` is `nil`, and with a list section `## Nums` holding `one`, `two`, and `three`, `table.concat(prompt.list('## Nums'), ',')` in the H1 gives `one,two,three`.

A `call` from the H1 block matches its heading by exact level and name, the same rule a `call` from a section uses, applied to the H1 visible set.

### Jumping from the H1

A `jump` from the H1 block ends the H1 pass and starts the main walk at the named top-level section instead of the first one, so every section before the target is skipped:

````markdown
---
name: h1-jump
description: Starts the walk at a later section.
runvil: 0
---

# Skip ahead

```lua
jump('## Target')
```

## Skipped

```lua
error('the jump target must skip this section')
```

## Target

```lua
return 'jumped'
```
````

````text
jumped
````

### Calling from the H1

Inside the H1 block, `call('## Heading')` runs a top-level section as a contained chain and returns whatever that section returns, as `var.answer = call('## Answer')` gets `called from h1` in the first prompt of this section.

To compute a value once before the walk, call a section from the H1, keep the result in `var`, and place the call target after the section that returns. In that prompt, `## Result` returns `var.answer` and sits before `## Answer`, so the walk never runs `## Answer` on its own.

The H1 pass and the walk after it form one root chain, so calls made in either share a child counter and take consecutive child ids. A `call` made in the H1 is child `0.0`, and the walk's first `call` is child `0.1`:

````markdown
---
name: h1-ids
description: Shows the child ids of a call from the H1 and from the walk.
runvil: 0
---

# Ids

```lua
var.first = call('## Answer')
```

## Result

```lua
return var.first .. ',' .. call('## Answer')
```

## Answer

```lua
return sys.id
```
````

````text
0.0.0,0.1.0
````

### Failures from the H1

A heading that matches no top-level section fails differently for `call` and for `jump`, because `call` raises at the call site while `jump` is looked up after the H1 block has finished:

| From the H1 | Where it fails | Uncaught run error kind |
| --- | --- | --- |
| `call` or `tasks.fanout` to an unknown heading | at the call site | `RequirementsUnmet` |
| `jump` to an unknown heading | after the H1 block ends | `Lua` |
| `jump` with a malformed heading | after the H1 block ends | `Lua` |

When `call` in the H1 names a heading that matches no top-level section, such as `call('## Nope')`, it raises a resolution error whose message names the missing heading. It raises at the call site, so `pcall` catches it: `local ok, err = pcall(call, '## Nope'); return tostring(ok) .. ':' .. tostring(err)` gives a result that starts with `false:` and contains `## Nope`.

An uncaught unknown-section error raised from the H1 ends the whole run, and you see it as the run's failure message. An uncaught heading error from `call` or `tasks.fanout` in an H1 block fails the [H1 hard gate](04-how-a-prompt-runs.md#the-h1-hard-gate), so the run ends with run error kind `RequirementsUnmet`. Its message is the notice line, then the gate line, then `: ` and the Lua error text as the cause, which names the heading:

````text
the environment cannot satisfy this prompt:
- the H1 gate failed: {Lua error text}
````

When a `jump` from the H1 block names a heading that matches no top-level section, such as `jump('## Nope')`, the run fails with a `Lua` resolution error whose message names the missing heading. A bad `jump` target from the H1 ends the run with run error kind `Lua`, not `RequirementsUnmet`, because the heading is looked up after the H1 block has finished. A `jump` heading from the H1 must be one or more `#` markers, then whitespace, then a non-empty name, and a malformed heading ends the run with run error kind `Lua` like any other bad `jump` target from the H1.

## Jump and call in arms, tasks, and tool handlers

After this section you can use `jump` and `call` inside fanout arms, tasks, and adhoc tool handlers, and know which sections each one can name.

````markdown
---
name: arm-jump
description: An arm jumps from its worker section to a sibling.
runvil: 0
---

# Arm jump

## Parent

```lua
local r = tasks.fanout('### Worker', {'alpha'})
return r[1].text
```

### Worker

```lua
jump('### Target')
error('the arm remaining blocks are skipped')
```

### Target

```lua
var.trail = 'Target '
```

### Tail

```lua
return var.trail .. 'Tail'
```
````

````text
Target Tail
````

`## Parent` runs a [fanout](14-fanout.md#what-an-arm-sees) over one member, so one arm runs the worker section `### Worker`. Its `jump('### Target')` ends the worker section's code, so the `error` never fires, and the arm's walk runs `### Target` and falls through to `### Tail`, whose reply becomes the arm's text and then the run result.

The rest of this section uses these headings, from a larger prompt where `## Parent` runs `tasks.fanout('### Worker', ...)` and each arm runs `### Worker` for one member, to show what an arm can name and what `jump` and `call` do inside it:

````markdown
## Parent

### Items

### Worker

#### Child

#### ChildTail

### Sub

#### Leaf

### Target

### Tail
````

### The worker visible set

An arm resolves heading references the same way its worker section does, so the headings it can name, the worker visible set, are the worker section's sibling sections other than the worker section itself, plus the worker section's own children. In the headings above, the worker visible set is `### Items`, `### Sub`, `### Target`, `### Tail`, `#### Child`, and `#### ChildTail`. An arm can read the list section `### Items`, a sibling of `### Worker`: with `### Items` listing `x` and `y`, an arm for the member `alpha` that returns `item .. ':' .. table.concat(prompt.list('### Items'), ',')` gives `alpha:x,y`.

The worker visible set never includes the worker section itself, so an arm cannot `call` or `jump` back into its own worker section. The limits on `jump` and `call` hold wherever a section runs, so a worker section running as an arm cannot name its own parent section either. From `### Worker` running as an arm, both `jump('## Parent')` and `pcall(call, '## Parent')` fail with not-found:

````markdown
---
name: arm-reach
description: An arm tries to jump to its parent section.
runvil: 0
---

# Arm reach

## Parent

```lua
tasks.fanout('### Worker', {'x'})
```

### Items

- x
- y

### Worker

```lua
jump('## Parent')
```
````

A `jump` from an arm to a heading outside the worker visible set, such as the top-level `## Parent` that called `tasks.fanout`, fails the run with run error kind `Lua` and a not-found message. The message lists the headings in the worker visible set:

````text
section heading `## Parent` not found; available sections: ### Items
````

### Jumping inside an arm

`jump` works in the Lua blocks of any section, including a section running as a fanout arm. Calling `jump(heading)` inside an arm ends the worker section's code at that point, so the lines after the `jump` never run, as the `error` in `### Worker` in the first prompt of this section shows.

A `jump` from an arm to a sibling of the worker section runs the target section and then falls through to the target's following sibling sections, and the last reply of that walk becomes the arm's text, or the empty string when no section in the walk replies. With the headings above, `### Worker` runs `jump('### Target')`, then `### Target` and `### Tail` run in that order, and when `### Tail` returns `'tail-reply'`, the arm's text is `tail-reply`. When `### Target` returns nothing and no later section replies, the arm's text is `""`.

An arm can also `jump` into one of the worker section's own children. `jump('#### Child')` runs that child and then falls through to the worker section's later children, so `#### Child` and `#### ChildTail` run in that order, and when `#### ChildTail` returns `'child-tail-reply'`, the arm's text is `child-tail-reply`.

### Calling inside an arm

`call` inside an arm runs a contained chain over the worker visible set and returns that chain's final reply as the value of the `call`, as in `local got = call('### Sub')`, where `### Sub` is a sibling of the worker section. A chain started by `call` from an arm falls through from the called section to its following siblings, and the last section's return value becomes the `call` result. When `### Sub` sets `var.leaves` and returns nothing, `### Target` returns nothing either, and `### Tail` does this:

````lua
return 'tail-reply:' .. var.leaves
````

then `got` holds `tail-reply:` followed by the value `### Sub` stored, such as `tail-reply:x!y!`.

A `call` from an arm to a heading outside the worker visible set raises a not-found error that the arm can catch with `pcall`:

````lua
local ok_call, err_call = pcall(call, '## Parent')
````

`ok_call` is false and `tostring(err_call)` contains `not found`, so the arm can go on to return its own text, such as `alpha:rejected`.

### Calls stay inside the calling task

A section run with `call` runs as a chain inside the calling task, not as a new task. It emits no [task events](15-tasks.md#task-events-and-task-errors) of its own, and its effects and events are reported under the task that made the call. The main walk is task `0`, and in a two-arm fanout whose arm makes a `call`, only the two arms, `0.0` and `0.1`, report as succeeded tasks.

A section run with `call` starts with the same [concurrency limit](14-fanout.md#concurrency-and-the-admission-window) as the section that called it, so the tasks it spawns run within the caller's share. It takes no concurrency slot of its own, and while it waits on tasks it spawned, it gives back the slot of the task it runs inside. So a section called from an arm can still fan out after the section that started the fanout set `tasks.concurrency(1)`. When `## Parent` runs `tasks.concurrency(1)` and fans out to `### Worker`, the worker section runs `call('### Sub')`, and `### Sub` fans out over its own child `#### Leaf`, the run completes, with `worker:tail-reply:x!y!:alpha` as its text.

A `call` made after a fanout takes the caller's next child number after the arms, because called sections and spawned tasks share one child counter. In the main walk after a two-arm fanout, the arms are children 0 and 1, the called section is child 2, and a `## Sub` that returns `sys.id` returns `0.2.0`.

### Calling from tasks and adhoc tool handlers

A section running as a spawned task can use `call` to run its own subsection, as when `### Caller`, which runs only as a task started with [`tasks.spawn`](15-tasks.md#starting-a-task-with-tasksspawn), runs `call('#### Leaf')` on its own `#### Leaf` child. A section can also pass its own `###` subsection to `tasks.spawn`, as when `## Main` runs `tasks.spawn('### Caller')` on its own `### Caller` child, and both spawned tasks complete with `r(a)|r(b)`.

You can use `call` inside an [adhoc tool](12-tools.md#how-adhoc-tool-handlers-run) handler, and it works there exactly as it does in a block. `jump` is the one control function a handler cannot use, as [The adhoc tool handler refusal](#the-adhoc-tool-handler-refusal) describes.
