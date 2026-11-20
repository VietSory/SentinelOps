#!/usr/bin/env bash
set +x
set -euo pipefail

context=sentinelops-demo
namespace=sentinelops-b-actions
deployment=remediation-controller
secret=sentinelops-llm
key=SENTINELOPS_LLM_API_KEY
containers='["remediation-controller","operator-ui","oom-demo-controller"]'
owned_names='["SENTINELOPS_LLM_BASE_URL","SENTINELOPS_LLM_MODEL","SENTINELOPS_LLM_API_KEY","SENTINELOPS_LLM_ACTOR_AUTHORIZATION"]'
kubectl_target=(kubectl "--context=$context" "--namespace=$namespace")

usage() {
  cat <<'EOF'
Usage:
  deploy_llm_explanation.sh [deploy] --state-dir DIR --image REPO@sha256:DIGEST --llm-model MODEL [--llm-base-url URL]
  deploy_llm_explanation.sh rollback --state-dir DIR [--force]

Default URL: https://api.openai.com/v1 (HTTPS only).
Default mode: read-only dry run. Set LLM_DEPLOY_EXECUTE=1 to write.
Use a fresh state directory for each deploy. Rollback reads DIR/rollback.json.
Use --force to override the rollback generation check.
EOF
}

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

mode=deploy
case "${1:-}" in
  deploy|rollback) mode="$1"; shift ;;
esac
state_dir= image= llm_model= llm_base_url=https://api.openai.com/v1 force=0
while (( $# )); do
  case "$1" in
    --state-dir|--image|--llm-model|--llm-base-url)
      [[ -n "${2:-}" && "$2" != --* ]] || fail "$1 requires a value"
      [[ "$mode" == deploy || "$1" == --state-dir ]] || fail "$1 is only valid for deploy"
      case "$1" in
        --state-dir) state_dir="$2" ;;
        --image) image="$2" ;;
        --llm-model) llm_model="$2" ;;
        --llm-base-url) llm_base_url="$2" ;;
      esac
      shift 2 ;;
    --force)
      [[ "$mode" == rollback ]] || fail '--force is only valid for rollback'
      force=1
      shift ;;
    -h|--help) usage; exit 0 ;;
    *) fail 'unknown argument; see --help' ;;
  esac
done

[[ -n "$state_dir" ]] || fail '--state-dir is required'
state_file=${state_dir%/}/rollback.json
if [[ "$mode" == deploy ]]; then
  [[ "$image" =~ ^[a-z0-9][a-z0-9._:/-]*@sha256:[a-f0-9]{64}$ ]] ||
    fail '--image must be a digest-pinned REPO@sha256:DIGEST'
  [[ "$llm_model" =~ ^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,127}$ ]] || fail '--llm-model requires a valid model ID'
  [[ "$llm_base_url" =~ ^https://[a-zA-Z0-9.-]+(:[0-9]+)?(/[^[:space:][:cntrl:]?#]*)?$ ]] ||
    fail '--llm-base-url must be an HTTPS URL without credentials, query or fragment'
  [[ ! -e "$state_file" && ! -L "$state_file" ]] || fail 'rollback.json already exists; use a fresh --state-dir'
else
  [[ -f "$state_file" && -r "$state_file" ]] || fail 'rollback.json missing or unreadable in --state-dir'
fi

export AWS_PROFILE="${AWS_PROFILE:-console}"
[[ "$AWS_PROFILE" == console ]] || fail 'AWS_PROFILE must be console'
command -v jq >/dev/null || fail 'jq is required'
command -v kubectl >/dev/null || fail 'kubectl is required'
[[ "$(kubectl config current-context)" == "$context" ]] || fail "current context must be $context"
[[ "$("${kubectl_target[@]}" get namespace "$namespace" -o name)" == "namespace/$namespace" ]] ||
  fail "namespace $namespace must exist"

check_secret() {
  local key_names
  key_names=$("${kubectl_target[@]}" get secret "$secret" \
    -o 'go-template={{range $name, $ignored := .data}}{{println $name}}{{end}}' 2>/dev/null) ||
    fail "cannot check Secret $secret"
  grep -Fxq -- "$key" <<< "$key_names" || fail "Secret $secret is missing key $key"
}

env_filters='
  def owned: .name as $name | ($owned | index($name)) != null;
  def sensitive_literal:
    (.name | test("KEY|SECRET|TOKEN|AUTHORIZATION"; "i"))
    and (has("value") or (.valueFrom | type != "object"));
'

project_deployment() {
  jq -ce --argjson names "$containers" --argjson owned "$owned_names" "$env_filters"'
    .metadata as $metadata | .spec.template.spec.containers as $containers
    | select(($containers | map(.name)) == $names and ($metadata.uid | type == "string"))
    | select(($metadata.resourceVersion | type == "string" and length > 0)
        and ($metadata.generation | type == "number" and . >= 1))
    | $containers[1] as $ui | ($ui.env // []) as $env
    | {uid:$metadata.uid, resourceVersion:$metadata.resourceVersion, generation:$metadata.generation,
       revision:$metadata.annotations["deployment.kubernetes.io/revision"], replicas:(.spec.replicas // 1),
       images:($containers | map(.image)), has_env:($ui.env | type == "array"),
       indices:[$env | to_entries[] | select(.value | owned) | {index:.key,name:.value.name}],
       unsafe_env:any($env[]; sensitive_literal), env:[$env[] | select(owned and (sensitive_literal | not))]}' 2>/dev/null
}

read_deployment() {
  "${kubectl_target[@]}" get deployment "$deployment" -o json | project_deployment
}

current_deployment=$(read_deployment) || fail 'unexpected container layout or Deployment metadata'
jq -e '.indices | length == (map(.name) | unique | length)' <<< "$current_deployment" >/dev/null ||
  fail 'operator-ui contains duplicate LLM env entries'

if [[ "$mode" == deploy ]]; then
  jq -e '.unsafe_env | not' <<< "$current_deployment" >/dev/null ||
    fail 'operator-ui contains a sensitive literal env entry'
  check_secret
  snapshot=$(jq -c '{image:.images[1], env, resourceVersion:null, generation:null}' <<< "$current_deployment")
  desired_env=$(jq -cn --arg url "$llm_base_url" --arg model "$llm_model" --arg key "$key" --arg secret "$secret" '
    [{name:"SENTINELOPS_LLM_BASE_URL",value:$url},
     {name:"SENTINELOPS_LLM_MODEL",value:$model},
     {name:$key,valueFrom:{secretKeyRef:{name:$secret,key:$key,optional:false}}}]')
  desired_image="$image"
else
  snapshot=$(jq -ce --argjson owned "$owned_names" "$env_filters"'
    select(keys == ["env","generation","image","resourceVersion"]
      and (.resourceVersion | type == "string" and length > 0)
      and (.generation | type == "number" and . >= 1 and floor == .)
      and (.image | type == "string" and length > 0)
      and (.env | type == "array" and length == (map(.name) | unique | length)
        and all(.[]; owned and (sensitive_literal | not)
          and if has("valueFrom") then keys == ["name","valueFrom"] and (.valueFrom | type == "object")
              else (keys == ["name"] or keys == ["name","value"]) and ((.value // "") | type == "string") end)))' \
    "$state_file" 2>/dev/null) || fail 'invalid or unconfirmed rollback.json'
  saved_generation=$(jq -r '.generation' <<< "$snapshot")
  current_generation=$(jq -r '.generation' <<< "$current_deployment")
  [[ "$saved_generation" == "$current_generation" || "$force" == 1 ]] ||
    fail "Deployment generation changed ($saved_generation -> $current_generation); review before using --force"
  desired_image=$(jq -r '.image' <<< "$snapshot")
  desired_env=$(jq -c '.env' <<< "$snapshot")
fi

deployment_patch=$(jq -c --arg image "$desired_image" --argjson desired "$desired_env" '
  "/spec/template/spec/containers/1" as $ui
  | ($ui + "/env") as $env_path
  | .indices as $indices
  | ($desired | INDEX(.name)) as $by_name
  | [{op:"test", path:"/metadata/resourceVersion", value:.resourceVersion},
     {op:"test", path:($ui + "/name"), value:"operator-ui"},
     {op:"test", path:($ui + "/image"), value:.images[1]},
     {op:"replace", path:($ui + "/image"), value:$image}]
  + (if .has_env then
       [$indices[] | select($by_name[.name] != null)
        | {op:"replace", path:($env_path + "/" + (.index | tostring)), value:$by_name[.name]}]
       + [$indices | reverse[] | select($by_name[.name] == null)
          | {op:"remove", path:($env_path + "/" + (.index | tostring))}]
       + [$desired[] | .name as $name | select(all($indices[]; .name != $name))
          | {op:"add", path:($env_path + "/-"), value:.}]
     elif $desired != [] then [{op:"add", path:$env_path, value:$desired}]
     else [] end)' <<< "$current_deployment")

if [[ "${LLM_DEPLOY_EXECUTE:-0}" != 1 ]]; then
  printf 'DRY-RUN: %s validated for %s/%s on %s. Target operator-ui image: %s\n' \
    "$mode" "$namespace" "$deployment" "$context" "$desired_image"
  exit 0
fi

if [[ "$mode" == deploy ]]; then
  (umask 077; mkdir -p -- "$state_dir" && (set -o noclobber; printf '%s\n' "$snapshot" > "$state_file")) ||
    fail 'cannot save rollback.json without overwriting an existing file'
  state_tmp=$(mktemp -- "$state_dir/.rollback.XXXXXX") || fail 'cannot prepare confirmed rollback.json'
  trap 'rm -f -- "$state_tmp"' EXIT
fi
printf 'Rollback state: %s\n' "$state_file"

patched=$(printf '%s\n' "$deployment_patch" | "${kubectl_target[@]}" patch deployment "$deployment" \
  --type=json --patch-file=/dev/stdin -o json 2>/dev/null | project_deployment) ||
  fail 'Deployment patch did not return a confirmed result'
if [[ "$mode" == deploy ]]; then
  jq -ce --argjson patched "$patched" '
    .resourceVersion = $patched.resourceVersion | .generation = $patched.generation' \
    <<< "$snapshot" > "$state_tmp" && mv -f -- "$state_tmp" "$state_file" ||
    fail 'cannot save confirmed rollback.json'
  trap - EXIT
fi

"${kubectl_target[@]}" rollout status "deployment/$deployment" --timeout=300s
after=$(read_deployment) || fail 'could not verify the Deployment after rollout'
expected_images=$(jq -c --arg image "$desired_image" '.images | .[1] = $image' <<< "$current_deployment")
jq -e --argjson patched "$patched" --argjson images "$expected_images" --argjson env "$desired_env" '
  .uid == $patched.uid and .generation == $patched.generation and .images == $images
  and (.env | sort_by(.name)) == ($env | sort_by(.name))
  and (.indices | length) == ($env | length)
  and (.revision | type == "string" and length > 0)' <<< "$after" >/dev/null ||
  fail 'operator-ui settings or sibling images differ from the expected state'

replica_set=$("${kubectl_target[@]}" get replicasets -l "app=$deployment" -o json |
  jq -ce --argjson deployed "$after" '
    [.items[] | select(.metadata.annotations["deployment.kubernetes.io/revision"] == $deployed.revision
      and any(.metadata.ownerReferences[]?; .controller and .kind == "Deployment" and .uid == $deployed.uid))
     | {uid:.metadata.uid, hash:.spec.selector.matchLabels["pod-template-hash"]}]
    | select(length == 1) | .[0] | select(.hash != null and .uid != null)') ||
  fail 'cannot identify the rolled-out ReplicaSet'
hash=$(jq -r '.hash' <<< "$replica_set")
pod_names=$("${kubectl_target[@]}" get pods -l "pod-template-hash=$hash" -o json |
  jq -er --argjson rs "$replica_set" --argjson deployed "$after" '
    [.items[] | select(.metadata.deletionTimestamp == null
      and any(.metadata.ownerReferences[]?; .controller and .kind == "ReplicaSet" and .uid == $rs.uid)
      and any(.status.conditions[]?; .type == "Ready" and .status == "True")) | .metadata.name]
    | select(length > 0 and length == $deployed.replicas) | .[]') ||
  fail 'cannot identify the ready Pods of the rolled-out ReplicaSet'

while IFS= read -r pod_name; do
  "${kubectl_target[@]}" get pod "$pod_name" -o \
    'jsonpath={.spec.containers[?(@.name=="operator-ui")].env}{"\n"}{.spec.containers[*].name}{"\n"}{.spec.containers[*].image}' |
    jq -Rse --argjson owned "$owned_names" --argjson env "$desired_env" \
      --argjson names "$containers" --argjson images "$expected_images" "$env_filters"'
      split("\n") as $lines
      | ($lines[0] | if . == "" then [] else fromjson end) as $actual_env
      | ([$actual_env[] | select(owned)] | sort_by(.name)) == ($env | sort_by(.name))
        and ($lines[1] | split(" ")) == $names and ($lines[2] | split(" ")) == $images' >/dev/null 2>&1 ||
    fail "operator-ui env or container images differ on Pod $pod_name"
done <<< "$pod_names"

jq -nr --argjson names "$containers" --argjson images "$expected_images" '
  $images | to_entries[] | "\($names[.key])\t\(.value)"'
