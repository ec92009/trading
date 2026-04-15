# Versioning SOP

- Use visible app versions in the form `vX.Y`.
- Use bare `X.Y` in repo metadata such as the top-level `VERSION` file.
- The GUI should add the `v` prefix when rendering the version badge, so internal `46.0` displays as `v46.0`.
- `X` is the number of days since `2026-02-28`.
- `Y` increments with each build/change on that same day.
- When updating the app UI version badge, always bump the minor version for each new build.
