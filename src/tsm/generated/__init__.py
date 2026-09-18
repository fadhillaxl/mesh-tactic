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

# Canonicalize sys.modules so imports are identical everywhere
sys.modules["tsm_pb2"] = tsm_pb2
sys.modules["tsm_pb2_grpc"] = tsm_pb2_grpc

__all__ = ["tsm_pb2", "tsm_pb2_grpc"]
