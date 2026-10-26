"""ASGI entry point; configured adapters are constructed during lifespan startup."""
from sentinelops.api import create_app

app = create_app()
