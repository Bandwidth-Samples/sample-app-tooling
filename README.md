# sample-app-tooling

Shared tooling for the Bandwidth sample apps. A sample app calls a reusable GitHub Actions workflow from this repo, which boots the app and checks it against a contract: the env vars, routes, status codes and BXML every sample of that type must have.

The checks are black-box. A runner starts the app as a subprocess, sends it shared webhook fixtures over HTTP, and compares the responses to the contract. The contract is the same for every language, so the samples can't drift apart.

## Using it in a sample app

Add `.github/workflows/test.yml` to the sample repo:

```yaml
name: Test

on:
  pull_request:
    branches:
      - main
  schedule:
    - cron: "0 9 * * 1"

jobs:
  contract-test:
    name: Contract Test
    uses: Bandwidth-Samples/sample-app-tooling/.github/workflows/node.yml@main
    with:
      sample: voice-record
```

Swap `node.yml` for the workflow matching the sample's language. The contract is read from this repo's `main` branch, so a contract change applies to every sample on its next run.

### Inputs

Every workflow takes these inputs:

| Input | Required | Description |
|---|---|---|
| `sample` | yes | Contract to check against, e.g. `voice-record` |
| `working-directory` | no | Directory of the app inside the repo. Default `.` |
| `install-command` | no | Language default below |
| `start-command` | no | Language default below |
| `<language>-version` | no | Language default below |

Language defaults:

| Workflow | Version input | Default version | `install-command` | `start-command` |
|---|---|---|---|---|
| `node.yml` | `node-version` | `22` | `npm ci` | `npm start` |
| `python.yml` | `python-version` | `3.12` | `pip install -r requirements.txt` | `python main.py` |
| `ruby.yml` | `ruby-version` | `3.3` | `bundle install` | `bundle exec ruby app.rb` |
| `csharp.yml` | `dotnet-version` | `8.0.x` | `dotnet restore` | `dotnet run` |
| `java.yml` | `java-version` | `21` | `mvn -B -DskipTests package` | `mvn -B spring-boot:run` |
| `php.yml` | `php-version` | `8.3` | `composer install` | `composer start` |

The job uses fake credentials only, so no secrets are needed and it is safe on fork PRs. If a run fails, the app's output is printed at the end of the job log.

## Contracts

| Contract | Description |
|---|---|
| [`contracts/messaging-send-receive.yml`](contracts/messaging-send-receive.yml) | Send SMS/MMS via `POST /messages`, status callbacks, inbound messages with MMS media download |
| [`contracts/messaging-auto-respond.yml`](contracts/messaging-auto-respond.yml) | Keyword auto-replies to inbound messages, status callbacks |
| [`contracts/mfa-cli.yml`](contracts/mfa-cli.yml) | CLI over stdin/stdout: phone number and method prompts, sending and verifying a code |
| [`contracts/voice-bridge.yml`](contracts/voice-bridge.yml) | Inbound call that creates an outbound call and bridges the two |
| [`contracts/voice-gather.yml`](contracts/voice-gather.yml) | Outbound call via `POST /calls`, Gather menu, digit handling |
| [`contracts/voice-record.yml`](contracts/voice-record.yml) | Inbound call, voicemail recording, `recordingAvailable` webhook |
| [`contracts/voice-redirect-loop.yml`](contracts/voice-redirect-loop.yml) | Inbound call held in a redirect loop, active calls via `GET /calls`, ended via `DELETE /calls/{callId}` |

Each contract lists the env vars the app must read, the startup failure cases, and the requests to send with their expected status, content type and body. Request bodies live in [`fixtures/`](fixtures). A case with `skip: true` is defined but not run.

`mfa-cli` is a CLI, so its contract sets `mode: cli`. Instead of requests, each case gives the lines to send on stdin and the expected exit code and output. The app is ready once it prints `ready_output`.

## Running the runner locally

Needs Python 3 and the dependencies in `runner/requirements.txt`.

```sh
pip install -r runner/requirements.txt

python runner/run_contract.py \
  --contract contracts/voice-record.yml \
  --app-dir ../voice-record-js \
  --start-command "npm start"
```

The runner picks a free port, sets fake `BW_*` env vars, and drops any `BW_*`, `CLIENT_*` and `LOCAL_PORT` variables from your shell so real credentials never reach the app. It exits non-zero if any case fails. Use `--skip <case-id>` to skip a case and `--log-file` to keep the app's output.

## Checklist lint

On pull requests, the contract job also runs a "Checklist lint" step. It checks the sample against the mechanical items in the [`bandwidth-template` checklist](https://github.com/Bandwidth-Samples/bandwidth-template/blob/main/checklist.md). It runs even if the contract failed, and either one failing fails the job. It doesn't run on scheduled runs.

The sample type and language come from the repo name (`<type>-<lang>`), and the expected icons, env var order and Dependabot config come from `bandwidth-template` at `main`.

Each failure names the file, what it found and what was expected.

To run it locally against a clone:

```sh
python runner/lint_sample.py \
  --app-dir ../voice-record-js \
  --template-dir ../bandwidth-template \
  --repo-name voice-record-js
```

It exits 0 when clean, 1 when it finds problems, and 2 when the template checkout is missing something it needs.

## License

[MIT](LICENSE)
