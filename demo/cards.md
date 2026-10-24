# Procedural Truth cards

Outcome of each card's real Kane run on event day (Saturday Oct 24 2026). Answers are the
saved GPT answers in `demo/answers/`; runs use `--stop-before` so Kane never clicks an
irreversible button or types into a real form.

| Card | Run | Outcome | On the card set? |
| --- | --- | --- | --- |
| SF parking citation | 12:12, `--stop-before c06` | Not testable here: `www.sfmta.org` resolves only to IPv6 on the venue network and the machine has no IPv6 route, so Kane's Chrome showed "This site can't be reached". Fails for reasons other than the instructions. | Dropped |
| Census county population | 12:14, `--stop-before c18`, 14 steps, 12.9 min, 254.6 credits | Kane completed c04 to c14 (Advanced Search, Geography, County, California, Los Angeles County, Surveys, ACS 5-Year, Decennial, Topics) and failed at c15: it could not "type and select B01003: Total population" in Topics. c03 was a false stale (URL used as the expected label) and click steps stored nearby headings; both fixed in the runner after this run (commit 4a270b4). | Not for the 3-minute demo: too long. Re-run pending to confirm c15. |
| GitHub secret scanning | 13:40, signed in over CDP (`-SignedIn`), `--stop-before c09`, `--loop` | **Catch, fixed by the loop.** c03 and c05 proven; c06 stale: the answer says "Security & analysis", today's sidebar entry is "Advanced Security" (Kane's own click summary: "Clicking Advanced Security settings link"; annotated screenshot in the pack). The agent (Codex) patched step 4 citing the cached GitHub docs page; Kane replayed c03 and c05 and re-authored c06 to c08: 5 of 6 proven, c09 (organization settings) not run. c07 and c08 really clicked Enable: Secret Protection and push protection are on for the public test repo `kane-user/attest-demo`. | **Beat 3 card** |
| GitHub make public | 14:00, signed in, `--skip c05 --stop-before c10` | Not a fair verdict yet. c04 and c06 proven; c05 ("If you aren't signed in") skipped as not applicable. c07 "On the repository page, click Settings": Kane opened `kane-user/attest-demo` itself (the answer never names a repo) and selected Settings, but the step read the label before navigating and stored "Set status", a false stale from Attest's phrasing. Repo visibility unchanged. | Dropped (harness limitation) |
| GitHub add collaborator | not run | Same profile; would stop before sending an invitation (`--stop-before c08`). Cut: two cards with known outcomes are enough for the demo. | Cut |
