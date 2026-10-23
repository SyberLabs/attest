<!-- source: https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/managing-repository-settings/setting-repository-visibility -->
<!-- fetched: 2026-10-23, http 200 -->
In this article
# Setting repository visibility
You can choose who can view your repository.
Copy markdown
## In this article
## About repository visibility changes
Note
If you can't change a repository's visibility, the organization owner may have restricted the ability to change repository visibility to organization owners only. For more information, see Restricting repository visibility changes in your organization.
We recommend reviewing the following caveats before you change the visibility of a repository.
### Making a repository private
- GitHub will detach public forks of the public repository and put them into a new network. Public forks are not made private.
- If you're using GitHub Free for personal accounts or organizations, some features won't be available in the repository after you change the visibility to private. Any published GitHub Pages site will be automatically unpublished. If you added a custom domain to the GitHub Pages site, you should remove or update your DNS records before making the repository private, to avoid the risk of a domain takeover. For more information, see GitHub's plans and Managing a custom domain for your GitHub Pages site.
- GitHub will no longer include the repository in the GitHub Archive Program. For more information, see About archiving content and data on GitHub.
- GitHub Advanced Security features, such as code scanning, will stop working unless the repository is owned by an organization that has access to the feature in private repositories with a GitHub Advanced Security, GitHub Code Security, or GitHub Secret Protection license and sufficient spare seats. For more information, see About GitHub Advanced Security.
### Making a repository public
- GitHub will detach private forks and turn them into a standalone private repository. For more information, see Forks
- If you're converting your private repository to a public repository as part of a move toward creating an open source project, see the Open Source Guides for helpful tips and guidelines. You can also take a free course on managing an open source project with GitHub Skills. Once your repository is public, you can also view your repository's community profile to see whether your project meets best practices for supporting contributors. For more information, see About community profiles for public repositories.
- The repository will automatically gain access to GitHub Advanced Security features.
- Actions history and logs will be visible to everyone. If your repository had reusable or required workflows that were shared from a different repository in your organization, the workflow file path including the repository name will be visible in the logs. For more information on how to remove workflow runs and artifacts see Managing workflow runs and REST API endpoints for workflow runs.
For information about improving repository security, see Quickstart for securing your repository.
## Consequences of changing a repository's visibility
Caution
Before you change your repository's visibility, understand the consequences of this change.
### Changing from public to private
- Stars and watchers for this repository will be erased, which will affect repository rankings.
- Custom Dependabot alert rules will be disabled unless GitHub Code Security is enabled for this repository. Dependency graph and Dependabot alerts will remain enabled with permission to perform read-only analysis on this repository.
- Code scanning will become unavailable unless Code Security is enabled for this repository.
- Current forks will remain public and will be detached from this repository.
### Changing from private to public
- The code will be visible to everyone who can visit GitHub.com.
- Anyone can fork your repository.
- All push rulesets will be disabled.
- Your changes will be published as activity.
- Actions history and logs will be visible to everyone.
- Stars and watchers for this repository will be erased.
### Changing from private to internal
- All members of the enterprise will be given read access.
- Outside collaborators can no longer be added to forks unless they're added to the root.
- Stars and watchers for this repository will be erased.
### Changing from internal to private
- Stars and watchers for this repository will be erased, which will affect repository rankings.
- Custom Dependabot alert rules will be disabled unless GitHub Code Security is enabled for this repository. Dependency graph and Dependabot alerts will remain enabled with permission to perform read-only analysis on this repository.
- Code scanning will become unavailable unless Code Security is enabled for this repository.
- Current forks will remain public and will be detached from this repository.
### Changing from internal to public
- The code will be visible to everyone who can visit GitHub.com.
- Anyone can fork your repository.
- All push rulesets will be disabled.
- Your changes will be published as activity.
- Actions history and logs will be visible to everyone.
- Stars and watchers for this repository will be erased.
### Changing from public to internal
- All members of the enterprise will be given read access.
- Outside collaborators can no longer be added to forks unless they're added to the root.
- Stars and watchers for this repository will be erased.
## Changing a repository's visibility
-
On GitHub, navigate to the main page of the repository.
-
Under your repository name, click Settings. If you cannot see the "Settings" tab, select the dropdown menu, then click Settings.
-
In the "Danger Zone" section, to the right of to "Change repository visibility", click Change visibility.
-
Select a visibility.
-
Click to confirm that you are changing the visibility of the correct repository.
-
Click I have read and understand these effects.
-
Click Make this repository public or Make this repository private.
## Further reading
- About repositories
