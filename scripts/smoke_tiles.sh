#!/usr/bin/env bash
# Writes a small classification COG, uploads it to LocalStack and checks TiTiler serves a PNG tile.
set -euo pipefail
cd "$(dirname "$0")/.."

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
COG="$TMP/smoke.tif"

uv run python - "$COG" <<'PY'
import sys
import numpy as np
from rasterio.transform import from_origin
from rasterio.windows import Window
from ml.cog import ClassificationCogWriter

n = 512
arr = (np.arange(n * n).reshape(n, n) // (n * n // 4)).astype("uint8")
with ClassificationCogWriter(
    sys.argv[1], width=n, height=n, transform=from_origin(400000, 6100000, 10, 10)
) as w:
    w.write(Window(0, 0, n, n), arr)
PY

export MSYS_NO_PATHCONV=1
docker compose exec -T localstack sh -c 'cat > /tmp/smoke.tif' < "$COG"
docker compose exec -T localstack awslocal s3 cp /tmp/smoke.tif s3://peatwatch-artifacts/smoke/smoke.tif >/dev/null 2>&1

# Fetch one tile at the centre of the COG and require a 200 PNG with the PNG signature.
python - "http://localhost:8001" "s3://peatwatch-artifacts/smoke/smoke.tif" <<'PY'
import json
import math
import sys
import urllib.parse
import urllib.request

base, src = sys.argv[1], urllib.parse.quote(sys.argv[2], safe="")
urllib.request.urlopen(f"{base}/healthz")
urllib.request.urlopen(f"{base}/cog/info?url={src}")
tj = json.load(urllib.request.urlopen(f"{base}/cog/WebMercatorQuad/tilejson.json?url={src}"))
lon, lat, z = tj["center"]
z = int(z)
n = 2**z
x = int((lon + 180) / 360 * n)
y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
r = urllib.request.urlopen(f"{base}/cog/tiles/WebMercatorQuad/{z}/{x}/{y}.png?url={src}")
body = r.read()
ok = r.status == 200 and body[:4] == bytes([0x89, 0x50, 0x4E, 0x47])
print(f"tile z{z}/{x}/{y}: status={r.status} bytes={len(body)} png={ok}")
sys.exit(0 if ok else 1)
PY
echo "smoke_tiles: OK"
