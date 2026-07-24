# Warehouse WMS — Codex Working Rules

## General safety

- Preserve all unrelated working-tree changes.
- Never create a commit unless the user explicitly requests it.
- Never push changes unless the user explicitly requests it.
- Do not use `git reset`, `git restore`, `git checkout`, `git clean`, or delete untracked files.
- Do not discard existing modifications.
- Do not create migrations unless model changes genuinely require one.
- Do not broaden a focused correction into an unrelated refactor.
- Prefer the smallest coherent fix.

## Fast iteration mode

Use fast iteration mode by default for:

- bug fixes,
- small frontend corrections,
- styling fixes,
- serializer corrections,
- seed scenario corrections,
- isolated API fixes,
- test fixes.

During fast iteration:

1. Inspect only the relevant code paths.
2. Make the smallest coherent correction.
3. Run only the smallest relevant tests.
4. Stop when the focused tests pass.
5. Report what was changed and what was not fully validated.

Do not run full validation unless the user explicitly requests it.

## Focused backend validation

When backend code changes:

- run the relevant test method or test class,
- run Django system check only when useful,
- prefer the already running development container,
- prefer `--keepdb` for repeated test runs.

Preferred command pattern:

`docker compose -f docker-compose.yml -f docker-compose.dev.yml exec backend python manage.py test operations.tests.<RelevantTestClass> --keepdb --noinput --verbosity 1`

Use a single test method when possible.

Do not run the complete `operations` test suite after every edit.

## Focused frontend validation

When frontend code changes:

- run only the directly affected Vitest file,
- run the production build only when TypeScript or bundling correctness needs verification.

Run frontend commands from the `frontend` directory.

Preferred command pattern:

`npm.cmd test -- <RelevantTestFile>`

Do not run the entire frontend suite for a change confined to one component.

## Docker usage

When the development stack is already running, prefer:

`docker compose -f docker-compose.yml -f docker-compose.dev.yml exec backend ...`

Do not use `run --rm` when `exec` is sufficient.

Do not rebuild Docker images unless dependencies, Dockerfiles, or Compose configuration changed.

## Commands prohibited during normal iteration

Do not run these after every small change:

- full operations test suite,
- accounts test suite,
- full frontend test suite,
- production frontend build,
- `seed_demo_data` twice,
- operational consistency checker,
- migration drift check,
- Docker image rebuild.

Run only the checks directly relevant to the current correction.

## Full validation mode

Run full validation only when the user explicitly says something equivalent to:

- full validation,
- final tests,
- before commit,
- ready to commit,
- sprawdź wszystko,
- możemy commitować,
- odpal wszystkie testy.

Full validation may include:

1. Django system check.
2. Migration drift check.
3. Full operations test suite.
4. Accounts test suite when authentication or authorization may be affected.
5. Full frontend test suite.
6. Frontend production build.
7. Seed twice when seed or demo operational data changed.
8. Operational consistency checker when operational data changed.
9. `git diff --check`.

Run each full command at most once unless a later code change could affect its result.

## Seed rules

- Do not run the seed repeatedly during normal implementation.
- Run focused seed tests when correcting a seed scenario.
- Run the real seed twice only during final validation when seed code changed.
- Never alter non-demo user data.
- Repair only records that are safely identifiable as demo-owned.

## Migration rules

- Inspect existing models and migrations first.
- Do not create migrations for serializer, service, frontend, seed-only, or display-only changes.
- Run `makemigrations --check --dry-run` during final validation or when model drift is suspected.
- Preserve all existing migration files and operational history.

## Test failure handling

- Fix only failures related to the current change.
- Do not alter production behavior merely to satisfy an incorrect test.
- Do not add arbitrary sleeps to fix asynchronous tests.
- Inspect shared mocks, timers, query clients, router state, and persisted state.
- Do not rerun already passing full suites unless later changes could affect them.

## Windows editing rules

- Prefer normal file-editing or patch tools.
- When Windows sandbox patching fails, use narrow assertion-guarded replacements.
- Never rewrite an entire file for a small edit.
- After fallback replacements:
  - read the changed section back,
  - confirm the expected change occurs exactly once,
  - check for literal escape sequences,
  - run `git diff --check`.
- Preserve original line endings where practical.

## Frontend business logic

- The backend remains authoritative for business rules.
- Do not duplicate quantity, readiness, route, inventory, or lifecycle calculations in React.
- Frontend code should render backend-provided projections.
- Do not silently round invalid backend quantity values.
- Preserve URL filter state and React Query invalidation behavior.

## Backend business logic

- Operational writes should use existing domain services.
- Avoid duplicating side effects in serializers, viewsets, and services.
- Use transactions and row locking where the existing workflow requires them.
- Expected business failures should return readable domain errors, not database exceptions.

## Scope control

Before making changes, identify:

- the exact observed problem,
- the actual root cause,
- the smallest affected code path,
- the focused tests that prove the correction.

Do not implement speculative future functionality unless requested.

## Final response in fast iteration mode

Report:

1. Root cause.
2. Files changed.
3. Behavior after the correction.
4. Focused tests executed.
5. Test results.
6. Full validation not run.
7. Remaining manual verification.
8. Migration and commit status.

Do not apologize for not running full validation during fast iteration mode.

## Final response in full validation mode

Report:

1. Implementation summary.
2. Changed files.
3. Full backend results.
4. Full frontend results.
5. Build result.
6. Seed/checker results when applicable.
7. Migration result.
8. `git diff --check` result.
9. Manual verification still required.
10. Whether the work is ready to commit.
