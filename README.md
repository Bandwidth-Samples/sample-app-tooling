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
| `php.yml` | `php-version` | `8.3` | `composer install` | `php -S localhost:$LOCAL_PORT -t public` |

The job uses fake credentials only, so no secrets are needed and it is safe on fork PRs. If a run fails, the app's output is printed at the end of the job log.

## Contracts

| Contract | Description |
|---|---|
| [`contracts/voice-record.yml`](contracts/voice-record.yml) | Inbound call, voicemail recording, `recordingAvailable` webhook |

Each contract lists the env vars the app must read, the startup failure cases, and the requests to send with their expected status, content type and body. Request bodies live in [`fixtures/`](fixtures). A case with `skip: true` is defined but not run.

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

## License

[MIT](LICENSE)
