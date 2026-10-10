"""API endpoint for seller intelligence."""
from fastapi import APIRouter, HTTPException

from . import sellers

router = APIRouter()


@router.get("/api/sellers/{scan_id}")
async def get_sellers(scan_id: str, enrich: int = 1):
    """Get grouped sellers for a scan."""
    try:
        result = await sellers.sellers_for_scan(scan_id, enrich=bool(enrich))
    except KeyError:
        raise HTTPException(404, f"Unknown scan: {scan_id}")

    return {"scan_id": scan_id, "sellers": result}
