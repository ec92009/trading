# Environment SOP

Follow the canonical environment SOP:
`~/Dev/.SOPs/ENVIRONMENT_SOP.md`.

Trading local deltas:

- When creating or refreshing the repo virtualenv, prefer `uv venv .venv`.
- When installing dependencies into the repo virtualenv, prefer
  `uv pip install --python .venv/bin/python -r requirements.txt`.
