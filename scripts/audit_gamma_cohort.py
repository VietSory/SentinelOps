import argparse
import json
from pathlib import Path

from sentinelops.public_data.admission import audit_cohort_admission


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--scope", type=Path, default=Path("configs/gamma_resource_cohort_scope.json"))
    parser.add_argument("--file-tree-lock", type=Path, default=Path("configs/gamma_resource_cohort_files.json"))
    parser.add_argument("--profile", type=Path, default=Path("configs/gamma_admission_profile.json"))
    parser.add_argument("--inspections", type=Path, default=Path("configs/gamma_development_inspections.json"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit_cohort_admission(args.manifest, args.scope, args.file_tree_lock, args.profile,
                                    args.inspections, args.output, project=Path.cwd())
    print(json.dumps(result, indent=2))
