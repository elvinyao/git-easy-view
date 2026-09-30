# User Guide

[Documentation index](../README.md) · [简体中文](../zh-CN/user-guide.md) · [日本語](../ja/user-guide.md)

## 1. What the application does

Git Easy View brings branches, latest commits, and PR/MR relationships from several GitHub or GitLab repositories into one project dashboard. Use it to check main / master, release, and feature activity across a dozen repositories at a glance.

The application reads platform APIs and HTTPS Git. It does not perform checkout, merge, push, or remote branch deletion. Pages display local snapshots obtained by background synchronization.

## 2. Access, permissions, and language

The default local address is [http://127.0.0.1:8766](http://127.0.0.1:8766). Your operator supplies the production address.

| Visitor | Available actions |
|---|---|
| Anonymous | Read all projects, project-linked repositories, branches, commits, relationships, and PR/MR history |
| Regular account | The same read access as an anonymous visitor |
| Administrator | Read access plus project, account, token, repository configuration, synchronization, and comparison management |

Project memberships and names such as “administrator only” do not restrict project reading. A repository with no project association is readable only by administrators. Linking a repository publishes its snapshots for anonymous browsing; administrators should confirm the intended audience before importing it.

Choose 中文, 日本語, or English on `/login`. You can then return to the project list without signing in. The browser remembers your choice; other pages do not show a language switcher. Resolution order is the URL `lang` parameter, language cookie, browser language, then Chinese. Repository names, branch names, commit messages, and PR/MR titles remain in their original language. Times are displayed in UTC.

## 3. Read the entire project overview

Select a project from the home page, for example `/projects/1`. Each repository shows:

- Its default branch tip: branch name, UTC time, short hash, and the first line of the commit message.
- Every current branch, grouped as mainline, release, feature, or other.
- Its relationship to the default branch and its ahead / behind counts.
- Git and API synchronization times, job status, stale data, and paused connections.

The platform's declared default branch takes priority. If it is unavailable, the application tries `main`, then `master`. Other branches are ordered by latest commit, with active release branches first. Release branches remain visible even when none is marked active.

A project page contains up to 50 repositories. Use the repository navigation to jump between them. Search branch names or filter by role and relationship; the default branch remains as comparison context. The default layout displays all branches. Compact mode retains all branches in scrollable lists. Filters, density, and pagination are stored in the URL for refresh and sharing. Deleted branches are omitted from the overview.

Click a branch name for evidence or a hash to open that commit in the Git workspace. Expand the detailed relationship table for planned targets and other evidence. Its separate pagination does not reduce the branches in the overview.

## 4. Interpret branch relationships

Relationships refer to the current source and target OIDs shown on the page. The project overview compares against the default branch; branch details can use another explicit target.

| State | Meaning | Example |
|---|---|---|
| Identical | Both branches point to the same commit | `feature/a` and `main` have the same hash |
| Contained | The source tip is reachable from the target's history | The feature has no unique commits; the target may have advanced |
| Unique commits | The source has commits absent from the target | `ahead=3, behind=0` means three ahead; `ahead=3, behind=2` means both sides have diverged |
| Unrelated | There is no common ancestor in the current graph | Do not interpret this as ordinary ahead / behind |
| Not analyzed / unavailable | A cached comparison for the fixed tips is unavailable | Wait for synchronization or ask an administrator to schedule comparison |

Read these kinds of evidence separately:

1. **Source candidates:** a common-ancestor inference or an administrator's declaration. Git does not permanently record which branch a branch was created from.
2. **Planned targets:** open PR/MR targets or manual configuration. These express intent.
3. **Current containment and historical merges:** containment describes present Git reachability; a merged PR/MR records a past platform event. Force pushes, later commits, and squash / rebase merges can make them differ.

A feature can show `ahead=2` after its PR was merged if two more commits were added. Missing historical PR/MR records do not prove a branch was never merged; check the stated history window. The application does not infer content equivalence after cherry-pick, revert, or squash.

## 5. Git workspace and commit details

Open `/repositories/{id}/branches` for an IDEA Git Log-style three-pane workspace:

1. Left: search and select branches, grouped by role.
2. Center: browse the actual commit graph and select a commit.
3. Right: inspect the full hash, author, message, changed files, and branch relationships.

Selecting a branch filters history. Selecting a commit updates its details. Selections, filters, and pagination are stored in the URL and support Back, reload, and sharing.

The workspace reads at most 100 commits and 200 changed files. Merge changes are compared with the first parent. It reads local Git objects and does not trigger remote synchronization. The sample's three commits have empty trees, so they have no changed files; actual repositories display real paths and change statuses.

## 6. PR/MR records and freshness

Repository pages and branch details include PR/MR records and platform links. Normal synchronization fetches all open records and closed / merged records updated in the previous 90 days. Incremental synchronization can retain older records, and an administrator can request a historical start date.

Commit time describes the commit; Git sync time describes branch snapshot retrieval; API sync time describes PR/MR retrieval. Check them separately. Git and API freshness are evaluated independently, using twice the configured synchronization interval with a minimum of 60 seconds.

Refreshing the cached view rereads stored data. Only an administrator's immediate synchronization action schedules remote retrieval. Failed synchronization or a paused connection leaves older snapshots readable, with status indicators explaining the limitation.

## 7. Administrator setup

Installation creates no accounts. An operator first creates an administrator using the [deployment guide](docker-deployment.md#3-create-an-administrator-or-an-optional-demo), then signs in at `/login`.

1. Create a project on `/admin` with a name, unique slug, and description.
2. Create a GitHub or GitLab connection with its HTTPS instance URL, token, and optional company CA path inside the container.
3. Save and check the connection. Credential or CA changes pause it until verification succeeds.
4. Choose the target project and import 1–50 repositories at a time. Initial synchronization is asynchronous; wait for the snapshot.
5. Adjust the interval and release / feature prefixes, and explicitly mark active release branches.
6. Configure manual source claims, planned targets, primary comparison branches, or historical PR/MR backfill as needed.

A repository can belong to several projects and reuse its cache and analysis. A connection with existing repositories cannot change platform or instance; create another connection. Account management supports creation, password reset, activation, and administrator rights. Web password forms require 12–1024 characters. The current administrator cannot deactivate themselves or remove their own administrator role.

Tokens are encrypted in the database and old tokens are not redisplayed. See [Docker and automated deployment](docker-deployment.md#5-platform-tokens-and-company-ca) for scopes and company CA configuration.

## 8. Common questions

| Symptom | Action |
|---|---|
| No projects | A fresh instance is empty; an administrator must create a project and import repositories |
| A branch is missing | Clear filters; check repository pagination, compact scrolling, and Git sync. Deleted branches are omitted |
| A hash has no message yet | Objects may be missing or the cache busy; refresh later and request synchronization if it persists |
| A new commit is absent | View refresh does not fetch; wait for the scheduler or ask an administrator to synchronize |
| A merged PR still has unique commits | Check current OIDs, the target, subsequent commits, and rewritten history |
| No administration link / HTTP 403 | Sign in as an administrator; regular accounts are readers |
| Anonymous repository URL returns 404 | The repository is missing or has no project association |
| Production login returns to the login page | Use HTTPS and check the proxy, Secure cookie, and stable session secret |

When reporting a problem, include the page URL, repository / branch, time, short hash, and a screenshot. Exclude tokens, passwords, env files, and session cookies.
