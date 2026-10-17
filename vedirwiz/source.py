"""Use an installed ve-direct package or the user's sibling checkout."""
import os
import sys
from pathlib import Path


def load_library():
    checkout = Path(os.environ.get("VE_DIRECT_SOURCE", Path(__file__).resolve().parents[2] / "ve.direct"))
    if (checkout / "src" / "ve_direct").is_dir():
        sys.path.insert(0, str(checkout / "src"))
    try:
        import ve_direct
    except ImportError as exc:
        raise SystemExit("Cannot find ve.direct. Set VE_DIRECT_SOURCE to its checkout or install ve-direct.") from exc
    return ve_direct


load_library()
