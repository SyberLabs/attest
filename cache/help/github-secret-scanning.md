<!-- source: https://docs.github.com/en/code-security/how-tos/secure-your-secrets/detect-secret-leaks/enable-secret-scanning -->
<!-- fetched: 2026-10-23, http 200 -->
In this article
# Enabling secret scanning for your repository
You can configure how GitHub scans your repositories for leaked secrets and generates alerts.
Copy markdown
## Who can use this feature?
Secret scanning is available for the following repository types:
- Public repositories: Secret scanning runs automatically for free.
- Organization-owned private and internal repositories: Available with GitHub Secret Protection enabled on GitHub Team or GitHub Enterprise Cloud.
- User-owned repositories: Available on GitHub Enterprise Cloud with Enterprise Managed Users. Available on GitHub Enterprise Server when the enterprise has GitHub Secret Protection enabled.
## In this article
## About enabling secret scanning alerts for users
Secret scanning alerts for users can be enabled on any free public repository that you own.
Secret scanning alerts for users can be enabled for any repository that is owned by an organization.
If you're an organization owner, you can enable secret scanning for multiple repositories at a time using security configurations. For more information, see Enabling security features at scale.
## Enabling secret scanning alerts for users
Secret scanning alerts for users are enabled when you enable Secret Protection for your repository.
-
On GitHub, navigate to the main page of the repository.
-
Under your repository name, click Settings. If you cannot see the "Settings" tab, select the dropdown menu, then click Settings.
-
In the "Security and quality" section of the sidebar, click Advanced Security.
-
To the right of "Secret Protection", click Enable.
-
Review the impact of enabling Secret Protection, then click Enable Secret Protection.
A repository administrator can choose to disable secret scanning for a repository at any time. For more information, see Managing security and analysis settings for your repository.
## Extending detection with public monitoring
The enablement steps above configure secret scanning for repositories your organization or enterprise owns. To detect secrets leaked by your enterprise members in public repositories across GitHub, you can enable public monitoring at the enterprise level. See Enabling public monitoring for your enterprise.
## Next steps
- Viewing and filtering alerts from secret scanning
- About secret scanning alerts
