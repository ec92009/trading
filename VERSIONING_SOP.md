# Versioning SOP

- This versioning scheme is for TeslaBot, CopyBot, and their user-facing control surfaces.
- Apply it when either bot, the viewer/dashboard, the launchd service flow, or a visible bot release badge changes.
- Pure cache-layout changes under `/_cache/` are not automatic version bumps by themselves unless they also change the operator-facing bot flow or visible control surfaces.
- Do not treat simulation-only research reruns, optimizer result files, or backtest parameter experiments as automatic version bumps by themselves.
- Use visible app versions in the form `vX.Y`.
- Use bare `X.Y` in repo metadata such as the top-level `VERSION` file.
- The GUI should add the `v` prefix when rendering the version badge, so internal `46.0` displays as `v46.0`.
- `X` is the number of days since `2026-02-28`.
- `Y` increments with each build/change on that same day.
- When updating the app UI version badge, always bump the minor version for each new build.
- The bot and the web app must read from the same shared version source so live order rationales and the published log viewer stay aligned.
- At the end of each web-app / bot-facing cycle, report:
- localhost viewer URL
- LAN viewer URL
- GitHub Pages viewer URL
- the exact new version to expect on all three surfaces
