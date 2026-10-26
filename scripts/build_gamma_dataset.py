import argparse
import json
from pathlib import Path

from sentinelops.public_data.dataset import build_grouped_dataset


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--admission", type=Path, required=True)
    parser.add_argument("--scope", type=Path, default=Path("configs/gamma_resource_cohort_scope.json"))
    parser.add_argument("--file-tree-lock", type=Path, default=Path("configs/gamma_resource_cohort_files.json"))
    parser.add_argument("--groups", type=Path, default=Path("configs/gamma_source_groups.json"))
    parser.add_argument("--splits", type=Path, default=Path("configs/gamma_resource_splits.json"))
    parser.add_argument("--profile", type=Path, default=Path("configs/gamma_resource_profile.json"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_grouped_dataset(args.manifest, args.admission, args.scope, args.file_tree_lock,
        args.groups, args.splits, args.profile, args.output, project=Path.cwd())
    print(json.dumps({key: value for key, value in report.items() if key != "per_entity_feature_eligible_labels"}, indent=2))
