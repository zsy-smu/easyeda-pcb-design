# Software validation

The initial publication ran the software regression on Windows with Python 3.10 and Shapely installed: **132 tests, 130 passed, 0 failures, 0 errors, 2 skipped**. The skipped filesystem tests depend on Windows symlink privileges. Coverage is recorded as `PARTIAL`, not a full pass.

The [local report](local-regression.json) belongs to the [initial publication](https://github.com/zsy-smu/easyeda-pcb-design/commit/75f723055ee13ac24a21b11000ea0a4ceee8d717) and records relative input paths and SHA256 values; private environment paths were omitted. This is a scoped software result, not EDA or physical-board qualification.

The [GitHub workflow](../.github/workflows/pcb-skill-checks.yml) additionally runs Linux/Windows and Python 3.10/3.12, and checks behavior without the optional geometry dependency. Read actual job logs and artifacts for each commit's result. Only named platform-specific skips are accepted: the Windows case-insensitive-path test on Linux, and two symlink tests with Windows privilege error 1314. Any other skip fails CI; accepted skips remain visible as PARTIAL. Hosted Windows fixtures use a canonical temporary path because the checkers intentionally reject aliases. Their path protection is unchanged.
