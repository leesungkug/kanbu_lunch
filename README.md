# Instagram to Slack Notifier

This repository checks the public Instagram profile `@lunch11_14` and sends its latest post image to Slack during the 10:30-11:00 Asia/Seoul window. It checks again when no new post is found and stops automatic notifications after a successful delivery that day.

## Instagram access status

### Hosted recovery setup (2026-09-22)

Local anonymous Chromium still retrieves posts, but the September 22 hosted run
redirected to login/verification before Slack delivery. The upstream repository
has since switched to Apify and [sent its scheduled September 21 message](https://github.com/hangyeollim-conpa/kanbu_lunch/actions/runs/35551799560).
This change adds the same bounded source as an explicit option; it does not claim
that this fork's hosted retrieval or Slack delivery has been verified.

To enable the cloud source after deploying this change:

1. Use an Apify account you control and save its API token as the repository
   Actions secret `APIFY_TOKEN`. Do not put it in code, config files, or chat.
2. Set the repository Actions variable `INSTAGRAM_SOURCE` to `apify`.
3. Run the notifier on `main` with `dry_run` enabled and verify that it prints the
   correct latest post. This uses Apify credits but sends no Slack message and
   changes no delivery state. A passing unit test or time-window skip is not this check.
4. Retain the existing weekday 10:43 KST external trigger. Verify the next
   scheduled delivery separately.

Each Apify request asks for at most 12 public posts, with a $0.04 run charge limit,
120-second actor timeout, and no automatic retry or source fallback. Free credits
and other account usage must be checked in Apify; this repository does not upgrade
plans or configure billing. See [Apify pricing](https://apify.com/pricing).
No Instagram login or restaurant account access is required by this adapter.

The default source remains `browser` until explicitly configured. Local config
can set `"instagram_source": "apify"` and supply `APIFY_TOKEN` in the environment.
Unset the repository variable (or set it to `browser`) to restore browser mode;
that does not resolve the known hosted browser access failure.

In `browser` mode, the notifier opens the ordinary public profile in an anonymous Chromium browser and reads the visible post tiles. It does not require control of the restaurant account or an Instagram login. Chromium runs JavaScript because the initial HTML alone does not contain the menu tiles.

It selects the newest publication date from the visible image descriptions, so an old pinned notice is not selected merely because it comes first. These dates have day precision and can differ from the menu date printed inside an image. If the newest date has multiple distinct posts, dates are missing, or login is required, retrieval fails without sending or changing state. Public-page markup and access can change; local browser success does not establish availability on a GitHub runner.

The old internal API client is retained for regression coverage, but the notifier no longer calls it. Its recent hosted requests returned HTTP 429 and a local request returned HTTP 401; these responses alone do not establish an IP ban. The browser does not fall back to those endpoints, reuse login cookies, or bypass login challenges.

## Repair verification (2026-09-11)

The updated CLI successfully retrieved public post `DdGPb7EzO7R` in a local anonymous Chromium dry-run. Its publication date is September 9; the image contains the September 11 menu. No Slack message was sent and the existing state file was unchanged.

The code is deployed to `leesungkug/kanbu_lunch` on `main` as of 2026-09-21, and both workflows are active. [Code tests passed on GitHub Actions](https://github.com/leesungkug/kanbu_lunch/actions/runs/35569757019). However, the [hosted Chromium dry-run](https://github.com/leesungkug/kanbu_lunch/actions/runs/35569771406) received HTTP 429 from the public Instagram profile. Browser installation succeeded; retrieval failed before any Slack delivery or state change. This does not establish a permanent IP ban. Registering a Slack webhook alone will not resolve this retrieval failure. A future successful hosted dry-run is required before considering automated delivery operational.

## Files

- `instagram_slack_notifier.py`: main checker script
- `instagram_browser.py`: anonymous public profile browser reader
- `instagram_apify.py`: opt-in bounded cloud retrieval and exact timestamp selection
- `instagram_public.py`: typed public tile parsing and latest-date selection
- `instagram_client.py`: shared post model and legacy API client
- `config.example.json`: optional local test config template
- `.github/workflows/instagram-slack-notifier.yml`: daily GitHub Actions workflow
- `.github/workflows/tests.yml`: isolated regression tests on Python 3.12–3.14

## Trigger setup

1. In [Slack app management](https://api.slack.com/apps), create or select an app for your workspace.
2. Enable `Incoming Webhooks`, choose `Add New Webhook to Workspace`, and select the destination channel. Copy its webhook URL. See the [Slack guide](https://docs.slack.dev/messaging/sending-messages-using-incoming-webhooks/).
3. Open this repository's [Actions secrets](https://github.com/leesungkug/kanbu_lunch/settings/secrets/actions) and choose `New repository secret`.
4. Set the name to `SLACK_WEBHOOK_URL` and the value to the webhook URL. Keep the URL out of source files and chat messages.
5. Open [Instagram Slack Notifier](https://github.com/leesungkug/kanbu_lunch/actions/workflows/instagram-slack-notifier.yml), select `Run workflow` on `main`, and keep `dry_run` checked to verify retrieval without sending.
6. After a successful dry-run, an explicit manual Slack test uses `dry_run` unchecked and `force_notify` checked. This sends one real message to the webhook's channel.

The built-in Actions schedule needs no personal access token or external scheduler.
`SLACK_WEBHOOK_URL` was registered on September 21. The hosted retrieval failure
above must also be resolved before delivery works.

### Optional external scheduler

The configured cron-job.org trigger runs at 10:43 KST on weekdays. Its fine-grained
GitHub token needs `Actions: Write` for this repository. It sends a `POST` request to:

```text
https://api.github.com/repos/leesungkug/kanbu_lunch/actions/workflows/instagram-slack-notifier.yml/dispatches
```

Use these headers in `cron-job.org`:

```text
Accept: application/vnd.github+json
Authorization: Bearer YOUR_GITHUB_TOKEN
Content-Type: application/json
X-GitHub-Api-Version: 2026-03-10
```

Use this JSON request body:

```json
{"ref":"main","inputs":{"dry_run":"false","force_notify":"false"}}
```

## Schedule

- The workflow also has a GitHub Actions fallback schedule at `10:38`, `10:46`, and `10:54` KST
- `cron-job.org` can call the same workflow during the `10:30-11:00` window in `Asia/Seoul`
- The configured `cron-job.org` schedule is weekdays at `10:43` KST
- The script checks the automatic time window before contacting Instagram and again after retrieval. Runs outside the window log a skip; an Actions success alone does not prove delivery.
- A check with no new post leaves the state unchanged, allowing a later check to catch a new menu.
- Once a successful automatic delivery is saved for the day, further automatic runs skip without contacting Instagram.
- You can also run it manually from the `Actions` tab with `Run workflow`
- Manual runs default to `dry_run`: inspect the latest post without Slack delivery or state changes. To send a manual test, disable `dry_run` and enable `force_notify`.
- All workflow triggers share one concurrency group, including manual tests. A running sender is never cancelled by a newer trigger.
- GitHub schedules can run late. Keep the external scheduler configured for checks within the window; this workflow cannot guarantee an exact execution minute.

## State and delivery guarantees

The state file records `last_automated_notification_date` only after Slack delivery succeeds. The old `last_automated_check_date` is retained but no longer used to suppress a day. Existing numeric post IDs and saved shortcodes are both checked for deduplication when switching to the public browser source. Publication timestamps from public tiles are UTC midnight date labels, recorded with `last_notified_timestamp_precision: "day"`; they do not represent the exact posting time. On the migration day, a different post can be sent even if the legacy check marker is already today, because that marker cannot establish whether a message was sent.

The workflow persists state to `main`, retrying a rejected push up to three times. It rebases only if remote history has moved forward without changing the state file; it refuses conflicting state rather than overwriting another writer.

When a saved publication timestamp exists, a different candidate must be provably
newer before automatic delivery. Equal or older timestamps are rejected; a saved
day-precision timestamp requires a candidate beyond that entire UTC day.
Apify results supply exact timestamps in seconds.

This is not an exactly-once delivery guarantee. A crash, lost Slack response, or state-persistence failure after Slack accepts a message can leave delivery uncertain and allow a duplicate on a later run. Check Slack and the Actions logs before rerunning such failures. Workflow concurrency does not lock independent local processes or copies of this repository.

## First run behavior

- On the first run, the workflow saves the current latest Instagram post as the baseline
- The first run does not send a Slack message
- A later new post on the same day can still be sent; saving a baseline does not mark that day as notified.
- After that, the script only posts the latest update found during the day's 10:30-11:00 Asia/Seoul check
- A manual run with `dry_run` disabled and `force_notify` enabled sends a test notification even when nothing new was posted

## Optional local test

Python 3.12 or later, Playwright Chromium, and the locked Python dependencies are required. Install them with [uv](https://docs.astral.sh/uv/) and [Playwright](https://playwright.dev/python/docs/library):

```sh
uv sync --locked
uv run playwright install chromium
```

On Linux runners, use `uv run playwright install --with-deps chromium`. The workflow installs these dependencies automatically.

To inspect Instagram without sending or modifying state (no Slack credential needed):

```sh
uv run python instagram_slack_notifier.py --config config.example.json --dry-run
```

For a manual delivery, first copy `config.example.json` to `config.json` and set your real Slack webhook URL. A normal run observes the time window and deduplication rules:

```powershell
uv run python .\instagram_slack_notifier.py --config .\config.json
```

To send a local test Slack message with the latest post:

```powershell
uv run python .\instagram_slack_notifier.py --config .\config.json --force-notify
```

## Development checks

```sh
uv sync --locked
uv run pytest
uv run ruff check .
uv run basedpyright
```

Tests isolate Instagram and Slack, use temporary state files, and exercise the workflow's actual Bash against temporary local Git remotes. They require no secrets and send no real Slack messages. Runtime and development dependencies are locked in `uv.lock`.
