# Existing documentation issues found during publication

These five README targets were already absent from public main `329cc828` before the V12 changes. They are not V12 test failures and have not been repaired in this change.

- `experiments/README.md`
- `reports/historical-evaluation-20260908/README.md`
- `reports/historical-inspect-audit-20260908/README.md`
- `reports/model-evaluation-20260908/README.md`
- `reports/news-tracing-integration-20260908/README.md`

The original README references remain visible. The V12 report, protocol and independent-audit links resolve in this publication checkout.

PR #18 remains outside this change: its only regression failure was a stale `RELEASE_MANIFEST.json` after editing the example model setting. This change does not modify that PR or the CI workflow.
