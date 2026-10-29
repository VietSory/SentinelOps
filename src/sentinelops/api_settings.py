from dataclasses import dataclass,field
import os


@dataclass(frozen=True)
class Settings:
    environment: str = "local"
    control_database_url: str | None = field(default=None,repr=False)
    api_token: str | None = field(default=None,repr=False)
    demo_mode: bool = False
    model_store: str | None = None
    model_release_ref: str | None = None

    def __post_init__(self):
        if self.api_token is not None and len(self.api_token)<32:
            raise ValueError("SENTINELOPS_API_TOKEN must contain at least 32 characters")
        if self.demo_mode and self.environment not in ("local","test"):
            raise ValueError("demo mode is restricted to local/test environments")
        if bool(self.model_store) != bool(self.model_release_ref):
            raise ValueError("model activation requires both trusted store and release reference")

    @classmethod
    def from_env(cls):
        return cls(environment=os.getenv("SENTINELOPS_ENV","local"),
                   control_database_url=os.getenv("CONTROL_DATABASE_URL"),
                   api_token=os.getenv("SENTINELOPS_API_TOKEN"),
                   demo_mode=os.getenv("SENTINELOPS_ENABLE_DEMO","false").lower()=="true",
                   model_store=os.getenv("SENTINELOPS_MODEL_STORE"),
                   model_release_ref=os.getenv("SENTINELOPS_MODEL_RELEASE_REF"))
