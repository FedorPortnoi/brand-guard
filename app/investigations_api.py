"""Endpoints for the investigation log: past investigations, their listings, and servers seen more than once."""
from fastapi import APIRouter

from . import investigations

router = APIRouter()


@router.get("/api/investigations")
def list_investigations(limit: int = 100):
    return investigations.history(max(1, min(limit, 500)))


@router.get("/api/investigations/{inv_id}")
def get_investigation(inv_id: str):
    return {"id": inv_id, "listings": investigations.listings(inv_id)}


@router.get("/api/servers")
def repeat_servers(min_listings: int = 2):
    """IPs that hosted at least min_listings listings across the log, dedicated servers first."""
    return investigations.repeat_servers(max(2, min_listings))
