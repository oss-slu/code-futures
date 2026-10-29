# Credentials

Code Futures needs one credential to run a sweep, and a second to include SonarQube Cloud analysis.

**Nothing here belongs in the repository.** Credentials live in `~/.config/code-futures/config.toml` or in the environment. The repository ships `config.example.toml` and gitignores the real thing.

| Credential | Needed for | Without it |
|---|---|---|
| `github.token` | Everything | Nothing runs |
| `sonar.token` | The Code Quality Analysis section | Those three probes report `unreadable`, the rest of the report is unaffected |

---

## 1. GitHub token

### Generating it

1. Go to **<https://github.com/settings/tokens>** and choose **Tokens (classic)**. Classic rather than fine-grained: Scorecard's branch-protection check needs the classic `repo` scope, and fine-grained tokens do not expose it the same way.
2. **Generate new token (classic)**.
3. Name it something you will recognise in a year — `code-futures sweep` does.
4. Expiry: 90 days is a reasonable default. Note the date somewhere, because an expired token fails in a way that looks like a permissions problem.
5. Select exactly these scopes:
   - **`repo`** — full control of private repositories. Needed to read branch protection settings, which are not public even on a public repository.
   - **`read:org`** — read org and team membership. Needed to enumerate the organisation.
6. **Generate token** and copy it immediately. GitHub shows it once.

### Authorising it for SSO — do not skip this

`oss-slu` sits inside an SSO-enforced enterprise. A token with correct scopes that has not been SSO-authorised returns 403 on every repository.

**This failure is the reason to care.** It does not look like an authentication error. Every check returns nothing, every probe reports a gap, and the report renders as *every team failed everything*. It has already happened once on the feedback pipeline.

To authorise:

1. On the token list at <https://github.com/settings/tokens>, find your new token.
2. Click **Configure SSO** beside it.
3. **Authorize** for the **SLU** organisation.

### Verifying it

```bash
code-futures doctor
```

This runs before any sweep and refuses to proceed if authorisation fails. It checks that private repositories are listable and that branch protection returns a semantic answer rather than a 403. Run it by hand whenever a sweep produces surprising zeros.

---

## 2. SonarQube Cloud

### What you are signing up for

SonarQube Cloud is free for public repositories under an OSI-approved licence. The free tier's condition is that **analysis of public projects is public**: a permanently visible dashboard on sonarcloud.io, carrying a maintainability rating and issue counts, readable by anyone without an account.

The code is already public, so this discloses nothing new. It is still worth telling teams before switching it on rather than after they find it.

### Creating the organisation

1. Go to **<https://sonarcloud.io>** and **Log in with GitHub**.
2. Choose **Import an organization from GitHub**.
3. Pick **oss-slu**. You will be asked to install the **SonarQube Cloud** GitHub App. This requires organisation owner permission, which you have.
4. When the app asks which repositories to grant access to, choose **All repositories**. Selecting individually means revisiting this every time a new product starts.
5. Choose the **Free plan**. It is the one that covers public repositories.
6. Note the **organization key** it assigns — probably `oss-slu`. You need it for the config file.

### Turning on automatic provisioning

This is what makes 29 projects a setting rather than 29 manual steps.

1. In the organisation, go to **Administration → Organization settings**.
2. Find **Provisioning** (sometimes **Repository binding**) and enable **Automatic provisioning**.
3. Use **bulk import** to bring in the repositories that already exist. New repositories are then provisioned when they are created.

### Generating the token

1. Click your avatar → **My Account** → **Security**.
2. Under **Generate Tokens**, give it a name — `code-futures` works.
3. For **Type**, choose **Organization Analysis Token** and select **oss-slu**.
4. **Generate**, and copy it immediately.

**Why an organisation-scoped token rather than per-project.** Sonar's own documentation prefers project tokens, on the grounds that a leaked token then compromises one project rather than all of them. That argument carries less weight here: the source is already public, and an analysis token grants *submitting* analysis results rather than reading anything. The realistic damage from a leak is poisoned quality data. One organisation token for 29 public repositories is the better trade, and it means no team manages a secret.

### Adding it to GitHub for the CI half

The reusable workflow reaches the token through `secrets: inherit`, so it must exist at organisation level:

1. **<https://github.com/organizations/oss-slu/settings/secrets/actions>**
2. **New organization secret**
3. Name: **`SONAR_TOKEN`** — exactly this, the workflow looks for it by name
4. Value: the token from above
5. Repository access: **All repositories**

---

## 3. The config file

```bash
mkdir -p ~/.config/code-futures
cp config.example.toml ~/.config/code-futures/config.toml
chmod 600 ~/.config/code-futures/config.toml
$EDITOR ~/.config/code-futures/config.toml
```

Environment variables override the file, so CI can supply the same values without one:

| Config key | Environment variable |
|---|---|
| `github.token` | `GITHUB_TOKEN` |
| `sonar.token` | `SONAR_TOKEN` |
| `sonar.organization` | `SONAR_ORGANIZATION` |

If `github.token` is absent from both, Code Futures falls back to `gh auth token`. On a workstation where `gh` is already logged in and SSO-authorised, that means no configuration at all.

---

## What to do when a sweep looks wrong

**Every repository scores badly.** Almost always SSO authorisation. Run `code-futures doctor`.

**One probe reads `unreadable` everywhere.** A service is down or a check is timing out. The report says which, and that is the correct output rather than a failure — the probe describes our tooling, not anybody's repository.

**The Code Quality Analysis section is entirely `unreadable`.** No `sonar.token`, or the organisation key is wrong.

**A finding is wrong about a repository you know.** Likely a gap in detection rather than in the repository — a tool invoked through a Makefile target or an aggregate task, which naive matching misses. Worth reporting as a bug.
