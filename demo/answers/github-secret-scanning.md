---
card: github-secret-scanning
model: gpt-5-mini
asked_at: 2026-10-23T22:48:31+00:00
system_prompt: 'Answer as numbered steps a person can follow in a web browser. Name each button or link by its exact label.'
---
# How do I enable free secret scanning on my GitHub repo?

1. Open the repository on github.com (URL looks like https://github.com/OWNER/REPO).

2. Confirm you have admin permissions on the repository. If you do not, ask an organization owner or repo admin to perform these steps.

3. Click the "Settings" tab near the top of the repo page.

4. In the left-hand sidebar click "Security & analysis".

5. In the "Code security and analysis" section find the "Secret scanning" row and click the "Enable" button (or toggle labeled "Secret scanning") to turn it on for this repository.

6. (Recommended) In the same area find "Push protection" and click the "Enable push protection" button (or toggle labeled "Push protection") to block known secret patterns on git push.

7. If your repo is in an organization and you want to enable secret scanning for multiple repos at once: go to the organization page, click "Settings", then in the left menu click "Code security and analysis", then enable "Secret scanning" and "Push protection" for the organization (buttons labeled "Enable").

Notes:
- Secret scanning for public repositories is available by default. Enabling from the repo or org settings activates scanning for private repos if your account/org has access to that feature.
- You must be a repository admin to change these settings; to change organization-wide defaults you must be an organization owner.
