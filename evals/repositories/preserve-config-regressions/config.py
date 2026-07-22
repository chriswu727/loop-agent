def load_config(values):
    result = {}
    for raw_key, raw_value in values.items():
        key = raw_key.lower().replace("__", ".")
        value = raw_value
        if key.endswith(".enabled"):
            value = bool(raw_value)
        elif isinstance(raw_value, str) and raw_value.isdigit():
            value = int(raw_value)
        target = result
        parts = key.split(".")
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = value
    return result
