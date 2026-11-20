from dataclasses import dataclass,field
import os
import re
from urllib.parse import urlsplit

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
    runbook_proposal_store: str | None = None
    bedrock_region: str | None = None
    bedrock_model_id: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = field(default=None,repr=False)
    llm_actor_authorization: str | None = field(default=None,repr=False)

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
        if bool(self.bedrock_region) != bool(self.bedrock_model_id):
            raise ValueError("Bedrock requires both SENTINELOPS_BEDROCK_REGION and SENTINELOPS_BEDROCK_MODEL_ID")
        if self.bedrock_region is not None and not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-[0-9]+", self.bedrock_region):
            raise ValueError("SENTINELOPS_BEDROCK_REGION must be an explicit AWS region")
        if self.bedrock_model_id is not None and (not isinstance(self.bedrock_model_id, str)
                                                   or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,2047}", self.bedrock_model_id)
                                                   or ":prompt/" in self.bedrock_model_id
                                                   or ":prompt-router/" in self.bedrock_model_id):
            raise ValueError("SENTINELOPS_BEDROCK_MODEL_ID must be an explicit model or inference-profile identity")
        llm_configured = (self.llm_base_url, self.llm_model, self.llm_api_key)
        if any(value is not None for value in llm_configured) and not all(llm_configured):
            raise ValueError("SENTINELOPS_LLM_BASE_URL, SENTINELOPS_LLM_MODEL and SENTINELOPS_LLM_API_KEY are required together")
        if self.llm_base_url is not None:
            parts = urlsplit(self.llm_base_url)
            if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
                or parts.query or parts.fragment or any(char.isspace() for char in self.llm_base_url)):
                raise ValueError("SENTINELOPS_LLM_BASE_URL must be explicit credential-free HTTPS")
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", self.llm_model):
                raise ValueError("SENTINELOPS_LLM_MODEL must be an explicit model ID")
            if (not isinstance(self.llm_api_key, str) or not 8 <= len(self.llm_api_key) <= 4096
                or any(ord(char) < 32 for char in self.llm_api_key)):
                raise ValueError("SENTINELOPS_LLM_API_KEY is invalid")
        if self.llm_actor_authorization is not None and (
            not isinstance(self.llm_actor_authorization, str)
            or not 1 <= len(self.llm_actor_authorization) <= 256
            or any(ord(char) < 32 for char in self.llm_actor_authorization)
        ):
            raise ValueError("SENTINELOPS_LLM_ACTOR_AUTHORIZATION is invalid")
        if self.llm_actor_authorization is not None and self.llm_base_url is None:
            raise ValueError("SENTINELOPS_LLM_ACTOR_AUTHORIZATION requires a configured LLM provider")
        if self.llm_base_url is not None and self.bedrock_region is not None:
            raise ValueError("configure only one explanation provider")

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
                   runbook_proposal_store=os.getenv("SENTINELOPS_RUNBOOK_PROPOSAL_STORE"),
                   bedrock_region=os.getenv("SENTINELOPS_BEDROCK_REGION"),
                   bedrock_model_id=os.getenv("SENTINELOPS_BEDROCK_MODEL_ID"),
                   llm_base_url=os.getenv("SENTINELOPS_LLM_BASE_URL"),
                   llm_model=os.getenv("SENTINELOPS_LLM_MODEL"),
                   llm_api_key=os.getenv("SENTINELOPS_LLM_API_KEY"),
                   llm_actor_authorization=os.getenv("SENTINELOPS_LLM_ACTOR_AUTHORIZATION"),
                   oidc=oidc_from_env())


OIDC_ENV = {"issuer":"SENTINELOPS_OIDC_ISSUER","audience":"SENTINELOPS_OIDC_AUDIENCE",
            "jwks_url":"SENTINELOPS_OIDC_JWKS_URL","audience_claim":"SENTINELOPS_OIDC_AUDIENCE_CLAIM",
            "producer_scope":"SENTINELOPS_OIDC_PRODUCER_SCOPE","operator_scope":"SENTINELOPS_OIDC_OPERATOR_SCOPE",
            "approval_scope":"SENTINELOPS_OIDC_APPROVAL_SCOPE","login_domain":"SENTINELOPS_OIDC_LOGIN_DOMAIN"}


def oidc_from_env():
    values = {name:os.getenv(variable) for name,variable in OIDC_ENV.items() if os.getenv(variable)}
    if not values:
        return None
    missing = [OIDC_ENV[name] for name in ("issuer","audience","jwks_url") if name not in values]
    if missing:
        raise ValueError("incomplete OIDC configuration: missing " + ", ".join(missing))
    return OidcConfig(**values)
