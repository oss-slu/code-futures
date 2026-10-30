# Security Policy

## Reporting a vulnerability

Report privately to **shownde@slu.edu**, or through GitHub's [private vulnerability reporting](https://github.com/oss-slu/code-futures/security/advisories/new).

Please do not open a public issue for a vulnerability. Expect an acknowledgement within a few working days.

## What is in scope

Code Futures reads repositories and writes reports. The interesting surface is small but real:

- **Report content.** Repository data reaches a rendered document. A path or finding that could inject content into a report, or escape a Markdown context, is in scope.
- **Subprocess handling.** The tool shells out to `git`, `gh` and `scorecard`. Anything allowing a repository's contents to influence what gets executed is in scope.
- **Credential handling.** Tokens are read from the environment or `~/.config` and passed to subprocesses. A path that writes one into a report, a log, or an artifact is in scope and serious.
- **The CI path.** The reusable workflow runs against other people's repositories. Anything letting a repository under analysis affect the workflow beyond its own job is in scope.

## What is not a vulnerability

- **A wrong finding.** If a report says something untrue about a repository, that is a bug and we want to hear about it — [open an issue](https://github.com/oss-slu/code-futures/issues). It is not a security report.
- **Findings about an analysed repository.** Code Futures reports on public code. Those findings are derivable by anyone who can clone it, and reporting them here does not make them ours.

## Supported versions

The latest tagged release. This is program tooling rather than a widely depended-upon library, so there are no backports.
