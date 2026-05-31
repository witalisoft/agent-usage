test:
    uv run pytest

pre-commit:
    uv run pre-commit run --all-files
    uv run ty check
