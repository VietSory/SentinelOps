"""Resolve contract identifiers from package resources, never over the network."""
from referencing import Registry,Resource
from referencing.exceptions import NoSuchResource

PREFIX = "https://sentinelops.local/contracts/"


def retrieve_contract(uri: str):
    from sentinelops.contracts import load_schema
    if not uri.startswith(PREFIX):
        raise NoSuchResource(ref=uri)
    try:
        return Resource.from_contents(load_schema(uri.removeprefix(PREFIX)))
    except ValueError as exc:
        raise NoSuchResource(ref=uri) from exc


CONTRACT_REGISTRY = Registry(retrieve=retrieve_contract)
