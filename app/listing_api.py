"""API routes for the URL-first listing flow."""
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from . import ask, listing, risk

router = APIRouter()


class ListingRequest(BaseModel):
    url: str
    brand: str = ""  # optional brand settings: say whether the link is the brand's own site or a reseller
    official_domains: str = ""
    resellers: str = ""


class SupplierRequest(BaseModel):
    url: str
    mode: str = "source"  # source: where copies are bought; maker: who makes the product


@router.post("/api/listing/check")
async def check(req: ListingRequest):
    try:
        extra = {k: v for k, v in (("brand", req.brand.strip()), ("official", risk.parse_domains(req.official_domains)),
                                   ("resellers", risk.parse_domains(req.resellers))) if v}
        return await listing.check_url(req.url, **extra)
    except listing.ListingError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/api/listing/suppliers")
async def find_suppliers(req: SupplierRequest):
    try:
        return await listing.suppliers(req.url, req.mode)
    except listing.ListingError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/api/listing/deep")
async def deep(req: ListingRequest):
    try:
        return await listing.deep_research(req.url)
    except listing.ListingError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/api/listing/ask")
async def ask_legit(text: str = Form(""), image: UploadFile | None = File(None)):
    """Is this legit? From a link, a screenshot or a store's name (the iMessage agent's question)."""
    try:
        return await ask.ask(text, await image.read() if image else None)
    except listing.ListingError as exc:
        raise HTTPException(422, str(exc)) from exc
