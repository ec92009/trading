# Environment SOP

- Use the repo virtualenv for Python commands in this workspace.
- Prefer `./.venv/bin/python` over system `python` or `python3`.
- Prefer `./.venv/bin/pytest` for tests.
- Prefer `uv` for environment and package management in this workspace.
- When creating or refreshing the repo virtualenv, prefer `uv venv .venv`.
- When installing dependencies into the repo virtualenv, prefer `uv pip install --python .venv/bin/python -r requirements.txt`.
