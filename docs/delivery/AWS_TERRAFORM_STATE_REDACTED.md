# AWS Terraform state snapshot (redacted)

This is the latest refreshed state snapshot for the original SentinelOps AWS
stack, with Terraform serial `126`.

The JSON is a reference snapshot only, not a usable Terraform backend state.
Values for secrets, tokens, credentials, private keys, connection strings,
secret ARNs, and sensitive outputs were replaced with `[REDACTED]`. The
unredacted state remains in the configured Terraform backend and is not stored
in Git.

Regenerate it from a local state file with:

```powershell
pwsh -NoProfile -File scripts/aws_demo/redact_terraform_state.ps1 `
  -InputPath <path-to-terraform.tfstate> `
  -OutputPath docs/delivery/AWS_TERRAFORM_STATE_REDACTED.json
```
