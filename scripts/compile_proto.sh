#!/usr/bin/env bash
# ==============================================================================
# compile_proto.sh - Compile TSM Protobuf definitions to Python gRPC modules
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

PROTO_DIR="${REPO_ROOT}/proto"
OUT_DIR="${REPO_ROOT}/src/tsm/generated"

mkdir -p "${OUT_DIR}"

echo "Compiling Protobuf definitions from ${PROTO_DIR} to ${OUT_DIR}..."

python3 -m grpc_tools.protoc \
    -I "${PROTO_DIR}/tsm/v1" \
    --python_out="${OUT_DIR}" \
    --grpc_python_out="${OUT_DIR}" \
    "${PROTO_DIR}/tsm/v1/tsm.proto"

cat << 'EOF' > "${OUT_DIR}/__init__.py"
"""Generated gRPC and Protobuf bindings for Tactical SDR Mesh."""
import sys
from pathlib import Path

_GEN_DIR = str(Path(__file__).resolve().parent)
if _GEN_DIR not in sys.path:
    sys.path.insert(0, _GEN_DIR)

try:
    from . import tsm_pb2, tsm_pb2_grpc
except ImportError:
    import tsm_pb2, tsm_pb2_grpc  # type: ignore

__all__ = ["tsm_pb2", "tsm_pb2_grpc"]
EOF

echo "[SUCCESS] Protobuf compilation complete."
