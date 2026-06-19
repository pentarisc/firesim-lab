# Sim reference — AWS verify-only preflight + recurring SSO login (steps 7–8)

`firesim-lab-sim` **verifies and logs in**; it does **not** provision. On any gap,
point the user back to `firesim-lab-setup`. All AWS calls run inside the container
(AWS CLI v2) with an explicit `--profile`, via the shared seam.

## Step 7 — readiness probe (VERIFY-ONLY)

Run `scripts/verify-aws.sh <profile> <region>` (in `firesim-lab-setup/scripts`,
resolved via `$CLAUDE_PLUGIN_ROOT`). It checks, read-only: active SSO session,
build role + instance profile (`fslab-fpga-builder`), run role
(`fslab-fpga-runner`), `iam:PassRole` grant, key pair in region, **F2 quota > 0**
(surfacing a pending request), and an FPGA Developer AMI for the region. Each gap
prints its **layer** + remediation. Do NOT attempt any creation here — that is
Setup's job.

## Step 8 — recurring SSO login (device-code)

The container is **headless**, so login **always** uses `--use-device-code` — both
in the helper script and in any command you show the user. A plain `aws sso login`
tries to open a browser that isn't there. **SSO mode is a questionnaire field:**

- **`skill-driven`** (default): a two-call cadence so the code surfaces instantly:
  1. `scripts/scrape-sso-code.sh <profile> --launch` — backgrounds
     `aws sso login --use-device-code`, scrapes the **verification URL + user
     code**, prints `SSO_VERIFICATION_URL=…` / `SSO_USER_CODE=…` /
     `SSO_STATUS=awaiting_approval`, and **exits fast**. Surface the URL+code to
     the user **immediately** (`needs_decision`).
  2. Then **loop** `scripts/scrape-sso-code.sh <profile> --poll` on your own short
     sleeps until `SSO_STATUS=logged_in` (or you give up and re-prompt). Each call
     returns instantly — never one long blocking call.
- **`user-paste`**: the user runs the login themselves and pastes the result back
  (remind them to include `--use-device-code`); verify after with `--verify-only`.
- **`already-logged-in`**: skip login; confirm with `--verify-only`.

Do **not** run `--launch` (or any login) inside a single long Bash call expecting
to read the code from it: a blocking call doesn't stream stdout until it exits and
can hit the Bash-tool timeout. The split `--launch` / `--poll` design avoids both.

`SSO_STATUS` values: `already_valid`, `awaiting_approval`, `logged_in`,
`not_logged_in`, `scrape_failed`. On `scrape_failed`, show `SSO_LOG_TAIL` and
re-prompt (relaunch or fall back to `user-paste`). `--timeout <sec>` bounds only the
`--launch` scrape wait (default 60).

The **first-time** `aws configure sso --use-device-code` (creating the profile)
belongs to `firesim-lab-setup` (S4), not here. Credentials persist via the `~/.aws`
bind mount; there is no AWS CLI on the host.
