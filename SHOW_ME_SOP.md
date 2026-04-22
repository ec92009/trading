# Show Me SOP

- When the user asks to "show me" the web app, default to running the local static viewer server for `docs/`.
- If the user also wants the public site updated, push the committed `main` branch to GitHub so GitHub Pages can deploy `/docs`.
- Report all three viewer URLs in the handoff:
- localhost URL
- LAN URL
- public GitHub Pages URL
- Also report the exact visible UI version the user should expect on those surfaces.
- Be explicit about scope: uncommitted local changes are not part of the GitHub Pages deploy unless they are committed first.
