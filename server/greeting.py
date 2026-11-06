"""Greeting helpers for the backend."""


def greet(name: str) -> str:
    """Return a greeting for ``name``.

    Raises ValueError if ``name`` is not a string or is empty/whitespace-only.
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name must be a non-empty string")
    return f"Hello, {name}!"


def farewell(name: str) -> str:
    """Return a farewell for ``name``.

    Raises ValueError if ``name`` is not a string or is empty/whitespace-only.
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name must be a non-empty string")
    return f"Goodbye, {name}!"
