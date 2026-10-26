from dataclasses import dataclass,field
import os


@dataclass(frozen=True)
class Settings:
    environment: str = "local"
    control_database_url: str | None = field(default=None,repr=False)
    api_token: str | None = field(default=None,repr=False)
    demo_mode: bool = False

    def __post_init__(self):
        if self.api_token is not None and len(self.api_token)<32:
            raise ValueError("SENTINELOPS_API_TOKEN must contain at least 32 characters")
        if self.demo_mode and self.environment not in ("local","test"):
            raise ValueError("demo mode is restricted to local/test environments")

    @classmethod
    def from_env(cls):
        return cls(environment=os.getenv("SENTINELOPS_ENV","local"),
                   control_database_url=os.getenv("CONTROL_DATABASE_URL"),
                   api_token=os.getenv("SENTINELOPS_API_TOKEN"),
                   demo_mode=os.getenv("SENTINELOPS_ENABLE_DEMO","false").lower()=="true")
