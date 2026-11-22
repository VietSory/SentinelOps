# AWS Terraform state history

[Serial 126 inventory](AWS_TERRAFORM_STATE_REDACTED.json) records the original
stack before teardown. It contains counts by resource type and mode. Provider
state, attributes, resource identifiers and outputs are excluded.

| Stack | Final state serial read on 2026-11-22 | Entries in the state resource list |
| --- | ---: | ---: |
| `sentinelops-dev` | 235 | 0 |
| `sentinelops-trial-dev` | 238 | 0 |

The table records what the final local state files contained. A live AWS
inventory is a separate check.

To create another environment, follow the
[clone-to-rebuild guide](../../scripts/aws_demo/REBUILD.md) and initialize a
dedicated Terraform working directory and state for that environment. Keep
active state and plan files in restricted storage. They can contain
provider-generated values and account-specific resource bindings.

To generate a shareable inventory from another state file, use PowerShell 7
and choose a new output filename in an existing directory:

```powershell
pwsh -File scripts/aws_demo/redact_terraform_state.ps1 -InputPath ./terraform.tfstate -OutputPath ./terraform-inventory.json
```
