#!/usr/bin/env bash
# Applies infra/ to the running LocalStack with tflocal (envs/local.tfvars), checks the -tf buckets,
# the jobs queue (DLQ redrive, visibility) and the secret, then always destroys again.
# State and the .terraform dir are throwaway (infra/.smoke-infra, removed on exit); nothing
# committed is touched. LocalStack must already be up: docker compose up -d localstack
# Needs terraform, tflocal and python on PATH. Takes about 2-3 minutes.
set -euo pipefail
cd "$(dirname "$0")/../infra"

WORK=".smoke-infra"
CHECK="$WORK/check.py"

export LS_ENDPOINT="${LOCALSTACK_ENDPOINT:-http://localhost:4566}"
export TF_VAR_api_key="dev-only-key"
export AWS_ACCESS_KEY_ID="test"
export AWS_SECRET_ACCESS_KEY="test"
export AWS_DEFAULT_REGION="us-east-1"
export TF_DATA_DIR="$WORK/data"
export TF_IN_AUTOMATION=1

mkdir -p "$WORK"

# Checker: no boto, plain urllib against LocalStack (which does not validate signatures).
cat > "$CHECK" <<'PY'
import html, json, os, re, sys, urllib.error, urllib.parse, urllib.request

EP = os.environ["LS_ENDPOINT"].rstrip("/")
FORM = {"Content-Type": "application/x-www-form-urlencoded"}
failures = []


def fail(msg):
    failures.append(msg)
    print("FAIL: " + msg, file=sys.stderr)


def call(method, url, data=None, headers=None, service="s3"):
    # LocalStack does not check signatures but uses the credential scope to pick the service.
    headers = {"Authorization": "AWS4-HMAC-SHA256 Credential=test/20260101/us-east-1/"
               f"{service}/aws4_request, SignedHeaders=host, Signature=x", **(headers or {})}
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def sqs(path, action, **params):
    form = urllib.parse.urlencode({"Action": action, "Version": "2012-11-05", **params}).encode()
    return call("POST", f"{EP}{path}", form, FORM, service="sqs")


def queue_attrs(name):
    status, body = sqs(f"/000000000000/{name}", "GetQueueAttributes", **{"AttributeName.1": "All"})
    if status != 200:
        return None
    pairs = re.findall(r"<Name>(.*?)</Name>\s*<Value>(.*?)</Value>", body, re.S)
    return {k: html.unescape(v) for k, v in pairs}


def secret_exists(name):
    status, _ = call("POST", EP + "/", json.dumps({"SecretId": name}).encode(), {
        "X-Amz-Target": "secretsmanager.DescribeSecret",
        "Content-Type": "application/x-amz-json-1.1"}, service="secretsmanager")
    return status == 200


def snapshot():
    """Bucket and queue names, to prove destroy leaves LocalStack as found."""
    status, s3 = call("GET", EP + "/")
    if status != 200:
        raise SystemExit(f"LocalStack not reachable at {EP} (s3 list returned {status})")
    status, q = sqs("/", "ListQueues")
    if status != 200:
        raise SystemExit(f"LocalStack not reachable at {EP} (sqs list returned {status})")
    return {
        "buckets": sorted(re.findall(r"<Name>(.*?)</Name>", s3)),
        "queues": sorted(u.rsplit("/", 1)[-1] for u in re.findall(r"<QueueUrl>(.*?)</QueueUrl>", q)),
    }


cmd = sys.argv[1]
if cmd == "snapshot":
    print(json.dumps(snapshot(), sort_keys=True))
elif cmd == "verify":
    out = {k: v["value"] for k, v in json.load(open(sys.argv[2])).items()}
    for key in ("data_bucket", "artifacts_bucket", "jobs_queue_url", "jobs_queue_arn",
                "jobs_dlq_arn", "api_key_secret_name"):
        if not out.get(key):
            fail(f"terraform output '{key}' missing or empty")
    for key in ("data_bucket", "artifacts_bucket"):
        name = out.get(key, "")
        if not name.endswith("-tf"):
            fail(f"{key}={name!r} does not carry the -tf suffix")
        elif call("HEAD", f"{EP}/{name}")[0] != 200:
            fail(f"bucket {name} does not exist in LocalStack")
    if out.get("jobs_queue_url"):
        qname = out["jobs_queue_url"].rstrip("/").rsplit("/", 1)[-1]
        a = queue_attrs(qname)
        if a is None:
            fail(f"queue {qname} does not exist in LocalStack")
        else:
            if a.get("VisibilityTimeout") != "900":
                fail(f"queue {qname} VisibilityTimeout={a.get('VisibilityTimeout')!r}, expected '900'")
            try:
                rp = json.loads(a["RedrivePolicy"])
            except (KeyError, ValueError):
                rp = None
                fail(f"queue {qname} has no RedrivePolicy")
            if rp is not None:
                if int(rp.get("maxReceiveCount", -1)) != 3:
                    fail(f"queue {qname} maxReceiveCount={rp.get('maxReceiveCount')!r}, expected 3")
                target = str(rp.get("deadLetterTargetArn", ""))
                if target != out.get("jobs_dlq_arn"):
                    fail(f"queue {qname} redrive target {target!r} != DLQ output {out.get('jobs_dlq_arn')!r}")
                if queue_attrs(target.rsplit(":", 1)[-1]) is None:
                    fail(f"dead-letter queue {target} does not exist in LocalStack")
    if out.get("api_key_secret_name") and not secret_exists(out["api_key_secret_name"]):
        fail(f"secret {out['api_key_secret_name']} does not exist in LocalStack")
    if failures:
        sys.exit(1)
    print("checked: -tf buckets, jobs queue (visibility 900, redrive maxReceiveCount 3 to DLQ), secret")
PY

if ! SNAP_BEFORE="$(python "$CHECK" snapshot)"; then
  rm -rf "$WORK"
  echo "smoke_infra: FAILED, cannot read LocalStack at $LS_ENDPOINT (is the stack up?)" >&2
  exit 1
fi

cleanup() {
  rc=$?
  trap - EXIT
  if [ -d "$TF_DATA_DIR" ]; then
    echo "smoke_infra: destroying"
    if ! tflocal destroy -input=false -auto-approve -no-color \
        -var-file=envs/local.tfvars >"$WORK/destroy.log" 2>&1; then
      tail -n 20 "$WORK/destroy.log" >&2
      echo "smoke_infra: destroy did not complete; resources may remain in LocalStack" >&2
      rc=1
    fi
  fi
  SNAP_AFTER="$(python "$CHECK" snapshot 2>/dev/null || true)"
  if [ "$SNAP_AFTER" != "$SNAP_BEFORE" ]; then
    echo "smoke_infra: LocalStack differs from before the run" >&2
    echo "  before: $SNAP_BEFORE" >&2
    echo "  after:  $SNAP_AFTER" >&2
    rc=1
  fi
  rm -rf "$WORK"
  if [ "$rc" -eq 0 ]; then echo "smoke_infra: OK"; else echo "smoke_infra: FAILED" >&2; fi
  exit "$rc"
}
trap cleanup EXIT

# Local backend with state inside the throwaway dir; the committed lock file pins the provider.
echo "smoke_infra: init"
tflocal init -input=false -no-color -backend-config="path=$WORK/terraform.tfstate" \
  >"$WORK/init.log" 2>&1 || { tail -n 20 "$WORK/init.log" >&2; echo "smoke_infra: init failed" >&2; exit 1; }

echo "smoke_infra: apply (slow on LocalStack)"
tflocal apply -input=false -auto-approve -no-color -var-file=envs/local.tfvars \
  >"$WORK/apply.log" 2>&1 || { tail -n 30 "$WORK/apply.log" >&2; echo "smoke_infra: apply failed" >&2; exit 1; }

tflocal output -json >"$WORK/outputs.json"
python "$CHECK" verify "$WORK/outputs.json"
