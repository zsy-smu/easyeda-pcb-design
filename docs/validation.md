# Software validation

The initial publication ran the software regression on Windows with Python 3.10 and Shapely installed: **132 tests, 130 passed, 0 failures, 0 errors, 2 skipped**. The skipped filesystem tests depend on Windows symlink privileges. Coverage is recorded as `PARTIAL`, not a full pass.

The [local report](local-regression.json) records relative input paths and SHA256 values; private environment paths were omitted. This is a scoped software result, not EDA or physical-board qualification.

The [GitHub workflow](../.github/workflows/pcb-skill-checks.yml) additionally runs Linux/Windows and Python 3.10/3.12, and checks behavior without the optional geometry dependency. Read actual job logs and artifacts for each commit's result. Hosted Linux jobs require no skipped tests; Windows may report privilege-related skips.
