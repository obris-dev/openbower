# Contributing to OpenBower

Thanks for helping out. This page covers the contribution flow, the branch
naming convention, and how to run the checks locally so your PR is green
the first time.

## Flow

1. **Fork** the repo (external contributors) or create a branch (maintainers).
2. Branch from `main` using the naming convention below.
3. Make your change; keep it focused (one concern per PR).
4. Install the pre-commit hooks once (`make hooks`) so the checks run on
   every commit.
5. Open a PR against `main`. CI must pass; `main` only takes changes
   through a PR.

For bugs and feature ideas, open a GitHub issue with what you expected,
what happened, and steps to reproduce (or the workflow you're trying to
accomplish). Security problems go through [SECURITY.md](SECURITY.md), not
public issues.

## Branch naming

Branches are **`<type>/<kebab-slug>`**: a Conventional-Commits type, a
slash, and a short kebab-case description. `main` is exempt.

```
feat/csv-import-quoting     fix/session-refresh-race     ci/branch-naming
```

| type       | when to use it                                          |
|------------|---------------------------------------------------------|
| `feat`     | a new user-facing capability                            |
| `fix`      | a bug fix                                               |
| `perf`     | a performance improvement (behavior unchanged)          |
| `refactor` | restructure code; no behavior or API change             |
| `docs`     | documentation only                                      |
| `test`     | add or correct tests only                               |
| `ci`       | CI / workflows / pipeline config                        |
| `build`    | build system, dependencies, packaging                   |
| `chore`    | maintenance / tooling that doesn't touch src behavior   |
| `style`    | formatting / whitespace only; no logic change           |
| `revert`   | revert a previous change                                |

The slug is hyphen-separated words of lowercase letters and digits.
Append `!` to the type to mark a breaking change, e.g.
`feat!/drop-legacy-api`.

This is enforced in two places by the same script,
[`scripts/check-branch-name.sh`](scripts/check-branch-name.sh):

- **pre-commit**: a commit on a misnamed branch is rejected locally.
- **CI**: the `branch-name` job validates the source branch of PRs from
  branches in this repo (fork PRs run a fork's editable copy of the
  check, so the rule is only enforced for in-repo branches).

Rename a branch with `git branch -m <new-name>`.

## Commit messages

Use the same Conventional-Commits types for commit subjects, e.g.
`feat(lists): add the csv import`. Not enforced, but it keeps the history
scannable and feeds automated releases later.

## Running the checks

```bash
make hooks   # install the pre-commit hooks (once)
```

CI validates branch names for in-repo PRs and gains lint, type, and test
jobs alongside each part of the codebase as it lands. `make help` lists
every current target; development setup docs land the same way, with the
code they describe.

A pnpm MAJOR bump is the one dependency change the containerized stack
does not absorb on its own: the web dependency volumes are named, so they
survive a rebuild by design, and the old major's per-package layout then
stops resolving. Run `make reset-web-deps` once after pulling such a
change, then `make up`.

The app RUNS in Docker (`make up`) and the checks RUN ON THE HOST:
`make test` and `make schema` use the host's uv and pnpm against the
compose database, which is what CI does too, so a green local check and a
green CI run mean the same thing. Running a check inside a container
(`make local-exec`) is for diagnosing the container itself.

## House style

- Match the surrounding code; linters are the authority (ruff for Python,
  eslint + strict tsc for the web workspace).
- Prose in copy and comments: no em dashes and no double hyphens (use
  commas or parentheses); `|` as the separator in UI copy.
- Examples in code, tests, and docs stay vertical-neutral: `acme.com` /
  `example.io` style domains, generic industries and roles.

## Licensing

Contributions are accepted under the repository's [LICENSE](LICENSE):
Apache-2.0 plus the additional terms at the top of that file (an `ee/`
directory, if one ever exists, carries its own license). By submitting a
pull request you agree your contribution is licensed under those terms.
