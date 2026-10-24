# Procedural Truth cards

Outcome of each card's real Kane run on event day (Saturday Oct 24 2026). Answers are the
saved GPT answers in `demo/answers/`; runs use `--stop-before` so Kane never clicks an
irreversible button or types into a real form.

| Card | Run | Outcome | On the card set? |
| --- | --- | --- | --- |
| SF parking citation | 12:12, `--stop-before c06` | Not testable here: `www.sfmta.org` resolves only to IPv6 on the venue network and the machine has no IPv6 route, so Kane's Chrome showed "This site can't be reached". Fails for reasons other than the instructions. | Dropped |
| Census county population | 12:14, `--stop-before c18`, 14 steps, 12.9 min, 254.6 credits | Kane completed c04 to c14 (Advanced Search, Geography, County, California, Los Angeles County, Surveys, ACS 5-Year, Decennial, Topics) and failed at c15: it could not "type and select B01003: Total population" in Topics. c03 was a false stale (URL used as the expected label) and click steps stored nearby headings; both fixed in the runner after this run (commit 4a270b4). | Not for the 3-minute demo: too long. Re-run pending to confirm c15. |
| GitHub secret scanning | pending | Repo `kane-user/attest-demo` (public, created 13:10). The saved answer's placeholder URL `https://github.com/OWNER/REPO` was replaced with that repo, the only edit to GPT's text. Run with `--stop-before c09` (organization settings; the throwaway account has no organization). Needs the throwaway account signed in in its own Chrome profile. Expected catch: "Security & analysis" and "Secret scanning" (older wording) against today's "Code security" and "Secret Protection". | Planned catch |
| GitHub make public | pending | Repo `kane-user/attest-private` (private, created 13:12). Same profile. Stop before the visibility change: `--stop-before c10`. Expected: proven, shows the tool does not cry wolf. | Planned clean pass |
| GitHub add collaborator | pending | Same profile. Stop before sending an invitation: `--stop-before c08`. | Candidate |
