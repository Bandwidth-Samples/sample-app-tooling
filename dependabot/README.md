# Dependabot template

Copy [`template.yml`](template.yml) to `.github/dependabot.yml` in a sample repo and fill in the three placeholders. Dependabot has no shared config, so each repo carries its own copy.

The policy: SDK updates only open a PR for a new major version, and every other dependency is grouped into one monthly PR.

| Language | `<ecosystem>` | `<directory>` | `<sdk-package>` |
|---|---|---|---|
| Ruby | `bundler` | `/` | `bandwidth-sdk` |
| Node | `npm` | `/` | `bandwidth-sdk` |
| Python | `pip` | `/` | `bandwidth-sdk` |
| C# | `nuget` | the project folder, e.g. `/VoiceRecord` | `Bandwidth.Sdk` |
| Java | `maven` | `/` | `com.bandwidth.sdk:bandwidth-sdk` |
| PHP | `composer` | `/` | `bandwidth/sdk` |
