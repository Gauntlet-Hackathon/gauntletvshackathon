def greet(name):
    """Return a greeting for ``name``; raise ValueError if blank."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name must be a non-empty string")
    return f"Hello, {name}!"
