"""Source adapters: each module exposes PRODUCTS and adapt(lib, product, ...)."""
from importlib import import_module

SOURCES = ("iattc", "wcpfc", "iotc", "iccat", "ccsbt")


def module(source):
    if source not in SOURCES:
        raise ValueError(f"Unknown catch-effort source: {source}")
    return import_module(f"{__name__}.{source}")


def products():
    """All products across adapters: {product: (source, ProductSpec)}."""
    found = {}
    for source in SOURCES:
        for name, spec in module(source).PRODUCTS.items():
            if name in found:
                raise ValueError(f"Duplicate product name: {name}")
            found[name] = (source, spec)
    return found
