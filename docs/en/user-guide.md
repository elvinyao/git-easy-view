# User Guide

[Documentation index](../README.md) · [简体中文](../zh-CN/user-guide.md) · [日本語](../ja/user-guide.md)

## Release plans and production versions

Open **Release management** from a project. Reading is public; changes require an administrator. The **Release plans** tab shows draft, active and completed plans. The separate **Production overview** tab lets you search repositories, check versions actually running and record production where it is missing.

Create an RLS plan in three steps:

1. **Select repositories**: Search by name or namespace and choose 1–50 repositories. You can select the filtered results, clear the selection and check the selected count. Selecting repositories in the project overview opens creation at step 2; you can still go back to add or remove repositories.
2. **Choose versions**: Fill in only the selected repositories. Search and select actual branches and tags from the lists, then check the short hash, commit summary and time. Expand **More settings** to change the default merge target or link an MR. Tags are synchronized from Git, including lightweight and annotated tags pointing to commits. Non-commit tags are excluded. You can save a draft before tags are ready.
3. **Review plan**: Enter a name, scheduled date in Japan time and optional notes. Review each repository's version, then choose **Save draft** or **Save and start plan**. Incomplete version settings show a reason and disable starting; you can continue editing a draft later. Starting pins all versions and prevents further edits.

Going back preserves your input. If submission fails, the original form retains the name, date, selections, MR and notes, with errors beside the affected fields. Select an unavailable branch or tag again; the form does not silently switch to the latest version.

Plan details show each repository in a row with its planned version, deployment status, merge status and **Next action**, plus completion counts at the top. After actual deployment, **Confirm deployment** opens the corresponding drawer form. Confirm that the pinned version is running to update production for this project. Expand the optional controls to adjust deployment time or add notes. Then use **Verify merge** to link a specific MR for verification after synchronization, or enter the required merge evidence for manual confirmation. Read-only users can inspect the same status and evidence through **Details**. A repository is complete only when deployment and merge are both confirmed; every repository must complete before the plan completes. Overdue plans are highlighted. Cancelling a plan preserves deployments and does not undo them.

Production is scoped to a project and repository. From **Production overview**, choose **Record production**, select the branch actually running and an optional tag, review the version to be recorded and save. Notes are optional for initial production; a blank deployment time means now. Expand **Production history** for history and rollback controls. Select an older entry and supply the required rollback evidence to record a rollback already performed. This appends a new record and preserves the original history. The application tracks deployment facts; it does not deploy, roll back, create tags or merge MRs. New branch commits and moved or deleted tags never change a pinned version; changes are flagged.

For a local repository, expand the optional **Automatic MR verification** section. Choose a verified read-only GitLab/GitHub connection, click **Load repositories**, search and select a repository by name, then **Verify and link**. If the repository is missing from the list, **Advanced: use repository ID** accepts a numeric GitLab project ID or GitHub repository ID. Failed lookups retain the current selection and can be retried. The platform is contacted for the list only when an administrator explicitly loads it, not on normal page views. The binding reads only MR metadata; code still comes from the local directory. It applies to all projects sharing that repository. Remote repositories use their existing connection. MR status updates run through background synchronization; manual confirmation with evidence remains available without a platform binding.

Automatic confirmation requires a specific linked MR with matching repository identities, source branch, target branch and exact pinned source SHA, plus the platform's merge timestamp. Platform evidence supports squash merges. Different SHAs, failed/stale APIs, missing bindings or missing records do not complete releases automatically. Synchronize again or provide auditable manual evidence. Late MR events never overwrite newer production. Scheduled dates and deployment inputs use Asia/Tokyo; historical timestamps display UTC.

## 1. What the application does

Git Easy View brings branches and latest commits from GitHub, GitLab, or local Git repositories into one project dashboard, with PR/MR relationships for remote sources. Use it to check main / master, release, and feature activity across a dozen repositories at a glance.

The application reads platform APIs, HTTPS Git, or local repositories mounted read-only. It does not perform checkout, merge, push, or remote branch deletion. Pages display local snapshots obtained by background synchronization.

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

Start with the **repository summary table**. Each row compares the default tip, current or latest release, newest feature, and separate Git/API synchronization status. Summaries retain the repository's latest tips while branch filters apply to the list below. Count links clear branch filters and jump to the full group. Release branch status describes commits, not deployment to an environment.

A page contains up to 50 repositories. Search repository and branch names separately, filter by role or relationship, or show only synchronization needing attention. The default branch remains as comparison context, and matching counts are distinct from totals. The **complete branch lists** start expanded. Use “Collapse all” to close repository sections, then open a heading or summary link as needed. Lists no longer require scrolling inside small panels. Filters and pagination remain shareable in the URL. Deleted branches are omitted.

Click a branch name or hash to open the **detail drawer** without leaving the project. Inspect full names, commit titles, authors, hashes, changed files, comparison targets, and PR/MR evidence. Escape or Close restores your position; Back, Forward, and reload restore selections. Open the full Git workspace from the drawer for history, then use “Back to project” to retain your filters. Relative times have full UTC tooltips. The optional detailed relationship table retains separate pagination without reducing the complete branch lists.

## 4. Interpret branch relationships

Relationships refer to the current source and target OIDs shown on the page. The project overview compares against the default branch; branch details can use another explicit target.

| State | Meaning | Example |
|---|---|---|
| Identical | Both branches point to the same commit | `feature/a` and `main` have the same hash |
| Commits contained | The source tip is reachable from the target's history | The feature has no unique commits; the target may have advanced |
| Not fully contained | The source has commits absent from the target | `ahead=3, behind=0` means three ahead; `ahead=3, behind=2` means both sides have diverged |
| Unrelated | There is no common ancestor in the current graph | Do not interpret this as ordinary ahead / behind |
| Not analyzed / unavailable | A cached comparison for the fixed tips is unavailable | Wait for synchronization or ask an administrator to schedule comparison |

Read these kinds of evidence separately:

1. **Source candidates:** a common-ancestor inference or an administrator's declaration. Git does not permanently record which branch a branch was created from.
2. **Planned targets:** open PR/MR targets or manual configuration. These express intent.
3. **Current containment and historical merges:** containment describes present Git reachability; a merged PR/MR records a past platform event. Force pushes, later commits, and squash / rebase merges can make them differ.

A feature can show `ahead=2` after its PR was merged if two more commits were added. Missing historical PR/MR records do not prove a branch was never merged; check the stated history window. The application does not infer content equivalence after cherry-pick, revert, or squash.

Navigation from the project into the drawer, workspace, or evidence keeps the project's default branch as the explicit comparison target. Labels identify that baseline; manual planned targets appear separately. Uncontained commits are normal development relationships, not synchronization failures.

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

After import or manual synchronization, progress lists queued, running, completed, or failed outcomes per repository. Successful completion refreshes snapshots; failures are never labelled successful. Retry an unavailable status request, or refresh status manually after automatic polling pauses for a long-running job. First-sync waiting, initial failure, an empty repository, and no filter matches have distinct messages. Existing snapshots remain readable during synchronization.

## 7. Administrator setup

Installation creates no accounts. An operator first creates an administrator using the [deployment guide](docker-deployment.md#3-create-an-administrator-or-an-optional-demo), then signs in at `/login`.

1. Create a project on `/admin` with a name, unique slug, and description.
2. Create a GitHub or GitLab connection with its HTTPS instance URL, token, and optional company CA path inside the container.
3. Save and check the connection. Credential or CA changes pause it until verification succeeds.
4. Search names or namespaces, choose the target project, and select 1–50 repositories. Select the current page and retain selections while paging. Check imported labels and the selection count before submitting. Follow asynchronous initial synchronization on the project page.
5. Adjust the interval and release / feature prefixes, and explicitly mark active release branches.
6. Configure manual source claims, planned targets, primary comparison branches, or historical PR/MR backfill as needed.

A repository can belong to several projects and reuse its cache and analysis. A connection with existing repositories cannot change platform or instance; create another connection. Account management supports creation, password reset, activation, and administrator rights. Web password forms require 12–1024 characters. The current administrator cannot deactivate themselves or remove their own administrator role.

Tokens are encrypted in the database and old tokens are not redisplayed. See [Docker and automated deployment](docker-deployment.md#5-platform-tokens-and-company-ca) for scopes and company CA configuration.

Validation errors appear on the original form and retain nonsecret inputs. Correct the identified fields; passwords and tokens must be entered again. Signing in for administration returns you to the original destination. Account identity and logout remain available on mobile.

### 7.1 Import local Git repositories in batches

To work without GitHub or GitLab, first [mount a repository root](docker-deployment.md#61-mount-local-repositories) from the application server. For example, `/Users/elvinyao/workspace` appears as `/data/local-git` in the page.

1. Open `/admin/connections/new`, choose “Local Git directory” as the repository source, and enter a name.
2. Click “Choose directory…”, navigate to a subdirectory, then select “Use this directory”. You can also enter a container path inside the mounted root.
3. Click “Save directory and scan repositories”. Discovery recursively finds regular and bare repositories, stops inside detected repositories, and skips hidden and dependency directories.
4. Choose the destination project, select repositories, and import. Each batch supports up to 50 repositories with selections retained across pages. Follow synchronization progress and browse the complete branch overview on the project page.

Local sources need no token and never contact remote platforms. They read local `refs/heads` branches and committed history; remote-tracking branches and uncommitted changes are excluded. The default branch prefers `main`, then `master`, then HEAD. API and PR/MR are “Not applicable”. Scheduled or manual synchronization rereads the local directory into a separate analysis cache; source repositories remain read-only.

The picker browses the application server, not a visitor's computer. Scans allow up to 1,000 repositories, 5,000 directories, and 12 levels. If a limit is reached or a directory is unreadable, the page explicitly reports partial results. Import the repositories found, then choose a smaller directory to continue scanning. Linked worktree metadata must also be inside the mounted root, with paths valid inside the container. Keep the mapping stable after import; create a new source when moving repositories or changing roots.

## 8. Common questions

| Symptom | Action |
|---|---|
| No projects | A fresh instance is empty; an administrator must create a project and import repositories |
| A branch is missing | Clear filters and expand its repository; check pagination and Git sync. Deleted branches are omitted |
| A hash has no message yet | Objects may be missing or the cache busy; refresh later and request synchronization if it persists |
| A new commit is absent | View refresh does not fetch; wait for the scheduler or ask an administrator to synchronize |
| A merged PR still has unique commits | Check current OIDs, the target, subsequent commits, and rewritten history |
| No administration link / HTTP 403 | Sign in as an administrator; regular accounts are readers |
| Anonymous repository URL returns 404 | The repository is missing or has no project association |
| Production login returns to the login page | Use HTTPS and check the proxy, Secure cookie, and stable session secret |

When reporting a problem, include the page URL, repository / branch, time, short hash, and a screenshot. Exclude tokens, passwords, env files, and session cookies.
