from dataclasses import dataclass,field
import os

from sentinelops.oidc import OidcConfig

LOCAL_ENVIRONMENTS = ("local","test")


@dataclass(frozen=True)
class Settings:
    environment: str = "local"
    control_database_url: str | None = field(default=None,repr=False)
    api_token: str | None = field(default=None,repr=False)
    demo_mode: bool = False
    model_store: str | None = None
    model_release_ref: str | None = None
    action_database_url: str | None = field(default=None,repr=False)
    operator_token: str | None = field(default=None,repr=False)
    oidc: OidcConfig | None = None

    def __post_init__(self):
        if self.oidc is not None and (self.api_token or self.operator_token):
            raise ValueError("configure either OIDC or static bearer tokens, not both")
        if self.environment not in LOCAL_ENVIRONMENTS and (self.api_token or self.operator_token):
            raise ValueError("static bearer tokens are restricted to local/test environments; configure OIDC")
        if self.api_token is not None and len(self.api_token)<32:
            raise ValueError("SENTINELOPS_API_TOKEN must contain at least 32 characters")
        if self.operator_token is not None and (not isinstance(self.operator_token,str) or len(self.operator_token)<32):
            raise ValueError("SENTINELOPS_OPERATOR_TOKEN must contain at least 32 characters")
        if self.api_token is not None and self.operator_token == self.api_token:
            raise ValueError("operator and producer credentials must be distinct")
        if self.demo_mode and self.environment not in ("local","test"):
            raise ValueError("demo mode is restricted to local/test environments")
        if bool(self.model_store) != bool(self.model_release_ref):
            raise ValueError("model activation requires both trusted store and release reference")

    @classmethod
    def from_env(cls):
        return cls(environment=os.getenv("SENTINELOPS_ENV","local"),
                   control_database_url=os.getenv("CONTROL_DATABASE_URL"),
                   action_database_url=os.getenv("SENTINELOPS_ACTION_READ_DATABASE_URL"),
                   api_token=os.getenv("SENTINELOPS_API_TOKEN"),
                   operator_token=os.getenv("SENTINELOPS_OPERATOR_TOKEN"),
                   demo_mode=os.getenv("SENTINELOPS_ENABLE_DEMO","false").lower()=="true",
                   model_store=os.getenv("SENTINELOPS_MODEL_STORE"),
                   model_release_ref=os.getenv("SENTINELOPS_MODEL_RELEASE_REF"),
                   oidc=oidc_from_env())


OIDC_ENV = {"issuer":"SENTINELOPS_OIDC_ISSUER","audience":"SENTINELOPS_OIDC_AUDIENCE",
            "jwks_url":"SENTINELOPS_OIDC_JWKS_URL","audience_claim":"SENTINELOPS_OIDC_AUDIENCE_CLAIM",
            "producer_scope":"SENTINELOPS_OIDC_PRODUCER_SCOPE","operator_scope":"SENTINELOPS_OIDC_OPERATOR_SCOPE"}


def oidc_from_env():
    values = {name:os.getenv(variable) for name,variable in OIDC_ENV.items() if os.getenv(variable)}
    if not values:
        return None
    missing = [OIDC_ENV[name] for name in ("issuer","audience","jwks_url") if name not in values]
    if missing:
        raise ValueError("incomplete OIDC configuration: missing " + ", ".join(missing))
    return OidcConfig(**values)
