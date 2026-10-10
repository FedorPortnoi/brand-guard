"""One-click takedown draft generation for matched infringements."""
import asyncio
import base64
import io
import re
import zipfile
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote, urlencode, urlparse

from . import rdap, report
from .sellers import registrable_domain
from .store import SESSIONS, original_for

# Platform/marketplace domains mapped to reporting info
CHANNELS = {
    "aliexpress": {
        "name": "AliExpress",
        "report_url": "https://ipp.alibabagroup.com",
        "how_to": "Use the Alibaba Group IPP (Intellectual Property Protection) portal at https://ipp.alibabagroup.com",
        "verified": True,
        "source": "Confirmed: IPP portal accessible and confirms IP complaint submission",
    },
    "alibaba": {
        "name": "Alibaba.com",
        "report_url": "https://ipp.alibabagroup.com",
        "how_to": "Use the Alibaba IPP portal at https://ipp.alibabagroup.com to submit IP infringement claims",
        "verified": True,
        "source": "Confirmed: Alibaba IPP portal main entry point",
    },
    "amazon": {
        "name": "Amazon",
        "report_url": "https://www.amazon.com/report/infringement",
        "how_to": "Sign in to Amazon first, then use the Report Infringement form (any Amazon account; Brand Registry members can report from Brand Registry instead)",
        "verified": False,
        "source": "The old help link (nodeId=201931950) is dead. The form returns an error page when signed out, so it could not be confirmed",
    },
    "ebay": {
        "name": "eBay",
        "report_url": "https://pages.ebay.com/vero/",
        "how_to": "Report through eBay's VeRO (Verified Rights Owner) program: send a Notice of Claimed Infringement to vero@ebay.com",
        "verified": True,
        "source": "Checked in a browser 2026-10-10: 'Verified Rights Owner program' page, lists vero@ebay.com",
    },
    "dhgate": {
        "name": "DHgate",
        "report_url": "https://brand.dhgate.com/intellectualproperty/home",
        "how_to": "File a complaint in DHgate's IP Complaint System (linked as 'IP Protection Portal' in the dhgate.com footer)",
        "verified": True,
        "source": "Checked in a browser 2026-10-10: 'Intellectual Property Complaint System' with File Complaints",
    },
    "etsy": {
        "name": "Etsy",
        "report_url": "https://www.etsy.com/ipreporting",
        "how_to": "Use the Etsy Reporting Portal (register your IP once, then report listings by URL). The standalone form at etsy.com/legal/ip/report cannot report individual listings",
        "verified": True,
        "source": "Checked in a browser 2026-10-10: 'Etsy Reporting Portal'",
    },
    "temu": {
        "name": "Temu",
        "report_url": "https://www.temu.com/intellectual-property-policy.html",
        "how_to": "Follow the reporting instructions in Temu's Intellectual Property Policy",
        "verified": True,
        "source": "Manager check 2026-10-09: HTTP 200, page title 'Temu | Intellectual property'",
    },
    "shein": {
        "name": "Shein",
        "report_url": "https://www.shein.com/ip-complaint",
        "how_to": "Submit a trademark or copyright notice through SHEIN's IP complaint form (redirects to your regional site)",
        "verified": True,
        "source": "Checked in a browser 2026-10-10: 'Notice of Infringement of Your Rights' form with trademark and copyright sections",
    },
    "walmart": {
        "name": "Walmart Marketplace",
        "report_url": "https://brandportal.walmart.com/",
        "how_to": "Report through the Walmart Brand Portal; without an account, use Walmart's online infringement form from the Help Center or email ipinvest@walmart.com",
        "verified": True,
        "source": "Checked in a browser 2026-10-10: 'Walmart Brand Portal'",
    },
    "shopee": {
        "name": "Shopee",
        "report_url": "https://brandipp.shopee.com/",
        "how_to": "Register on the Shopee Brand IP Portal, add your IP per market, then file reports under Case Management",
        "verified": True,
        "source": "Checked in a browser 2026-10-10: 'Shopee Brand IP Portal' sign-in",
    },
    "mercari": {
        "name": "Mercari",
        "report_url": "https://www.mercari.com/us/help_center/topics/account/policies/intellectual-property-help-center/",
        "how_to": "Use Mercari's Intellectual Property Help Center (MARS reporting for brands); copyright notices can also go through the DMCA form at mercari.com/dmca",
        "verified": True,
        "source": "Checked in a browser 2026-10-10: 'Intellectual Property Help Center | Mercari Help'",
    },
    "facebook": {
        "name": "Facebook (Meta)",
        "report_url": "https://www.facebook.com/help/intellectual_property",
        "how_to": "Open Facebook's Intellectual Property help and choose the copyright, trademark or counterfeit report form (requires logging in); brands with a registered mark can use Meta Brand Rights Protection",
        "verified": True,
        "source": "Checked in a browser 2026-10-10: 'Intellectual Property | Facebook Help Center' (the forms inside need a login)",
    },
    "instagram": {
        "name": "Instagram (Meta)",
        "report_url": "https://help.instagram.com/contact/instagramcounterfeitform",
        "how_to": "Use Instagram's Counterfeit Report Form (or its trademark / copyright forms from the same help section)",
        "verified": True,
        "source": "Checked in a browser 2026-10-10: 'Counterfeit Report Form'",
    },
    "pinterest": {
        "name": "Pinterest",
        "report_url": "https://policy.pinterest.com/en/copyright",
        "how_to": "Follow the reporting instructions in Pinterest's Copyright policy (trademark: policy.pinterest.com/en/trademark)",
        "verified": True,
        "source": "Manager check 2026-10-09: HTTP 200, page title 'Copyright | Pinterest Policy'",
    },
    "tiktok": {
        "name": "TikTok",
        "report_url": "https://ipr.tiktokforbusiness.com/legal/report/copyright",
        "how_to": "File a copyright/intellectual property infringement report through TikTok's official IPR portal",
        "verified": True,
        "source": "Confirmed: TikTok IPR portal with working copyright infringement form",
    },
    "youtube": {
        "name": "YouTube",
        "report_url": "https://support.google.com/youtube/answer/2807622",
        "how_to": "Submit a copyright removal request through YouTube's official copyright removal process",
        "verified": True,
        "source": "Confirmed: YouTube's official copyright removal form page",
    },
    "twitter": {
        "name": "X/Twitter",
        "report_url": "https://help.x.com/forms/ipi",
        "how_to": "Use X's Intellectual Property Issues form and choose trademark, copyright or counterfeit",
        "verified": True,
        "source": "Checked in a browser 2026-10-10: 'Intellectual Property Issues' form",
    },
    "reddit": {
        "name": "Reddit",
        "report_url": "https://support.reddithelp.com/hc/en-us/requests/new?ticket_form_id=16510899084308",
        "how_to": "Use Reddit's support form; it opens with 'Intellectual property requests' selected. Choose copyright or trademark",
        "verified": True,
        "source": "Checked in a browser 2026-10-10: support form opens with 'Intellectual property requests' preselected",
    },
}


def _extract_channel(url: str) -> Optional[str]:
    """Extract the channel key from a host URL."""
    host = urlparse(url).hostname or ""
    host_lower = host.lower().removeprefix("www.")
    for key in CHANNELS:
        if key in host_lower:
            return key
    return None


def _registrable_domain(hostname: str) -> str:
    """Registrable domain, e.g. shop.x.co.uk -> x.co.uk. Same rule as the Sellers tab (sellers.registrable_domain)."""
    return registrable_domain(hostname)


def notice_type(matches: list[dict]) -> str:
    """Determine notice type from ALL matches in group: 'copyright', 'trademark', or 'both'.

    Combines forensics and risk across all matches:
    - If any has copyright-kind (exact/modified/cropped/altered), score +copyright
    - If any has trademark-kind (lookalike/same_product) or likely_counterfeit, score +trademark
    - both if both scores present, else the higher one present
    """
    has_copyright = False
    has_trademark = False

    for match in matches:
        forensics = match.get("forensics")
        risk = match.get("risk", {})

        # Check for copyright kinds
        if forensics and forensics.get("kind") in ("exact", "modified", "cropped", "altered"):
            has_copyright = True

        # Check for trademark kinds or counterfeit category
        if forensics and forensics.get("kind") in ("lookalike", "same_product"):
            has_trademark = True
        if risk.get("category") in ("likely_counterfeit", "marketplace", "possible_scam"):
            has_trademark = True

    if has_copyright and has_trademark:
        return "both"
    elif has_copyright:
        return "copyright"
    elif has_trademark:
        return "trademark"
    else:
        return "trademark"  # Default for unverified


async def build_drafts(scan_id: str, idxs: list[int], rights_holder: dict, base_url: str = "") -> list[dict]:
    """Build takedown drafts grouped by channel/registrable domain.

    Args:
        scan_id: Session ID from SESSIONS
        idxs: List of match indices to include
        rights_holder: Dict with keys: name, company, email, phone, address, trademark_reg, original_url
        base_url: Base URL for evidence links

    Returns:
        List of draft dicts with channel, host, notice_type, recipient, report_url, how_to,
        subject, body, idxs, evidence_pdfs, mailto, and note
    """
    sess = SESSIONS.get(scan_id)
    if not sess:
        return []

    # Group matches by channel or registrable domain
    groups: dict[str, list[int]] = {}
    for idx in idxs:
        item = sess["items"].get(idx)
        if not item:
            continue
        match = item["match"]
        page_url = match.get("page_url") or match.get("image_url", "")
        channel = _extract_channel(page_url)
        if channel:
            groups.setdefault(channel, []).append(idx)
        else:
            # Non-platform website: group by registrable domain
            host = urlparse(page_url).hostname or "unknown"
            reg_domain = _registrable_domain(host)
            groups.setdefault(reg_domain, []).append(idx)

    # Collect all group keys for concurrent RDAP lookups
    rdap_domains = [k for k in groups.keys() if k not in CHANNELS]
    rdap_results = {}
    if rdap_domains:
        rdap_tasks = [rdap.lookup(d) for d in rdap_domains]
        responses = await asyncio.gather(*rdap_tasks)
        for domain, result in zip(rdap_domains, responses):
            rdap_results[domain] = result

    drafts: list[dict] = []

    for group_key, group_idxs in groups.items():
        # Determine if this is a known channel
        is_channel = group_key in CHANNELS
        channel_info = CHANNELS.get(group_key, {})

        # Collect all matches in this group
        all_matches = []
        all_hosts = set()
        for idx in group_idxs:
            item = sess["items"].get(idx)
            if item:
                all_matches.append((idx, item["match"]))
                all_hosts.add(item["match"].get("risk", {}).get("host", "unknown"))

        if not all_matches:
            continue

        # Determine notice type from ALL matches in group
        n_type = notice_type([m for _, m in all_matches])

        # Get recipient and report URL
        recipient = None
        report_url = channel_info.get("report_url") if is_channel else None
        how_to = channel_info.get("how_to", "")

        # For non-platform hosts, look up abuse email
        if not is_channel and group_key not in ("unknown",):
            rdap_info = rdap_results.get(group_key, {})
            recipient = rdap_info.get("abuse_email")
            if not recipient:
                how_to = f"Contact the abuse/legal team for {group_key}. Check the domain's WHOIS information or website's contact page."

        if not how_to:
            how_to = f"File a report or contact the platform's trust & safety team."

        # Build notice body
        body = _build_notice_body(
            n_type, all_matches, sess["brand"], rights_holder
        )

        # Build subject
        subject = _build_subject(n_type, sess["brand"])

        # Evidence PDFs
        evidence_pdfs = [f"{base_url}/api/report/{scan_id}/{idx}.pdf" for idx, _ in all_matches]

        # Build mailto if recipient exists
        mailto = None
        if recipient:
            mailto = _build_mailto(recipient, subject, body)

        draft = {
            "channel": group_key,
            "host": list(all_hosts)[0] if all_hosts and all_hosts != {"unknown"} else group_key,
            "notice_type": n_type,
            "recipient": recipient,
            "report_url": report_url,
            "how_to": how_to,
            "subject": subject,
            "body": body,
            "idxs": group_idxs,
            "evidence_pdfs": evidence_pdfs,
            "mailto": mailto,
            "note": "Draft generated by Brand Guard. Review and edit before sending. Not legal advice.",
        }
        drafts.append(draft)

    return drafts


def _build_subject(notice_type: str, brand: str) -> str:
    """Build email subject line."""
    if notice_type == "copyright":
        return f"Notice of claimed copyright infringement - {brand} image"
    elif notice_type == "trademark":
        return f"Report of suspected trademark misuse - {brand}"
    else:  # both
        return f"Notice of claimed infringement - {brand}"


def _build_notice_body(notice_type: str, matches: list[tuple[int, dict]], brand: str,
                       rights_holder: dict) -> str:
    """Build the body of the takedown notice using observational language."""
    rh_name = rights_holder.get("name") or "[YOUR NAME]"
    rh_company = rights_holder.get("company") or "[YOUR COMPANY]"
    rh_email = rights_holder.get("email") or "[YOUR EMAIL]"
    rh_phone = rights_holder.get("phone") or "[YOUR PHONE]"
    rh_address = rights_holder.get("address") or "[YOUR ADDRESS]"
    rh_tm_reg = rights_holder.get("trademark_reg") or "[TRADEMARK REGISTRATION NUMBER AND JURISDICTION]"
    original_url = rights_holder.get("original_url") or "[ORIGINAL URL]"

    lines = []
    lines.append("NOTICE OF CLAIMED INFRINGEMENT")
    lines.append("")

    # Observational opener
    lines.append("We have identified the listing(s) below, which appear to use an image that is a copy of,")
    lines.append("or closely resembles, our image.")
    if notice_type in ("trademark", "both"):
        lines.append("The listing(s) also appear to offer goods under our mark without our authorization.")
    lines.append("We request that the material be removed or access to it disabled.")
    lines.append("")

    # Identify the rights holder
    lines.append("NOTIFYING PARTY (RIGHTS HOLDER):")
    lines.append(f"Name: {rh_name}")
    lines.append(f"Company: {rh_company}")
    lines.append(f"Email: {rh_email}")
    lines.append(f"Phone: {rh_phone}")
    lines.append(f"Address: {rh_address}")
    lines.append("")

    # Describe the work
    if notice_type in ("copyright", "both"):
        lines.append("IDENTIFICATION OF COPYRIGHTED WORK:")
        lines.append(f"Brand/Product: {brand}")
        lines.append(f"Original location: {original_url}")
        lines.append("Description: Product image / logo")
        lines.append("")

    if notice_type in ("trademark", "both"):
        lines.append("TRADEMARK IDENTIFICATION:")
        lines.append(f"Mark: {brand}")
        lines.append(f"Registration: {rh_tm_reg}")
        lines.append("")

    # List infringing materials
    lines.append("MATERIAL IDENTIFIED:")
    for idx, match in matches:
        page_url = match.get("page_url") or match.get("image_url", "")
        image_url = match.get("image_url", "")
        captured = match.get("captured_at", "unknown")
        sha256 = match.get("sha256", "not available")
        forensics = match.get("forensics") or {}
        finding_label = forensics.get("label", "similarity detected")

        lines.append("")
        lines.append(f"Listing URL: {page_url}")
        if image_url:
            lines.append(f"Image URL: {image_url}")
        lines.append(f"Date Observed (UTC): {captured}")
        lines.append(f"Image SHA-256: {sha256}")
        if match.get("seller"):
            lines.append(f"Seller: {match['seller']}")
        arc = match.get("archive") or {}
        if arc.get("url"):
            lines.append(f"Archived copy (Wayback Machine, {arc.get('captured_at', 'date unknown')}): {arc['url']}")

        if not forensics:
            lines.append("Finding: image could not be downloaded for verification; reported on listing context")
        else:
            lines.append(f"Finding: {finding_label}")
        if match.get("ref_frame"):
            t = match["ref_frame"].get("t", 0)
            lines.append(f"Matches our video at {int(t // 60)}:{int(t % 60):02d}")

    lines.append("")
    lines.append("LEGAL STATEMENTS:")
    lines.append("")
    lines.append("[CONFIRM: I have a good-faith belief that this use is not authorized by the rights owner,")
    lines.append("its agent, or the law.]")
    lines.append("")
    lines.append("[CONFIRM: The information in this notice is accurate, and under penalty of perjury I am")
    lines.append("the owner or authorized to act on behalf of the owner.]")
    lines.append("")
    lines.append("Signature: [YOUR SIGNATURE / TYPED NAME]")
    lines.append(f"Date: {datetime.now(timezone.utc).isoformat(timespec='seconds')}")

    return "\n".join(lines)


def _build_mailto(recipient: str, subject: str, body: str) -> Optional[str]:
    """Build a mailto: link, encoding carefully. Fall back to short body if over 1800 chars.

    Uses quote(..., safe="") so spaces become %20, not +.
    """
    # Build with full body first
    params = {"subject": subject, "body": body}
    mailto_base = "mailto:" + quote(recipient, safe="")

    # Encode with quote for safe spaces
    encoded_params = urlencode(params, safe="")
    full_mailto = mailto_base + "?" + encoded_params

    # If over 1800 chars, use short body
    if len(full_mailto) > 1800:
        short_body = "See attached takedown draft and evidence PDFs."
        params = {"subject": subject, "body": short_body}
        encoded_params = urlencode(params, safe="")
        full_mailto = mailto_base + "?" + encoded_params

    return full_mailto


def bundle_zip(scan_id: str, idxs: list[int], drafts: list[dict]) -> bytes:
    """Create a ZIP file containing notices and evidence PDFs.

    Args:
        scan_id: Session ID
        idxs: List of match indices
        drafts: List of draft dicts from build_drafts

    Returns:
        ZIP file bytes
    """
    sess = SESSIONS.get(scan_id)
    if not sess:
        return b""

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Add README
        readme = _build_readme()
        zf.writestr("README.txt", readme)

        # Add notices with note header
        for draft in drafts:
            channel = draft["channel"]
            safe_channel = re.sub(r"[/\\]", "_", channel)[:40]
            notice_path = f"notices/{safe_channel}.txt"

            # Prepend note header
            notice_text = draft["note"] + "\n\n" + "=" * 60 + "\n\n" + draft["body"]
            zf.writestr(notice_path, notice_text)

        # Add evidence PDFs
        for idx in idxs:
            item = sess["items"].get(idx)
            if not item:
                continue
            match = item["match"]
            host = match.get("risk", {}).get("host", "unknown")
            safe_host = re.sub(r"[/\\]", "_", host)[:40]

            # Build PDF
            evidence_b64 = item.get("evidence")
            evidence = base64.b64decode(evidence_b64) if evidence_b64 else None
            pdf = report.build(sess["brand"], original_for(sess, match), evidence, match)

            pdf_path = f"evidence/{idx:02d}-{safe_host}.pdf"
            zf.writestr(pdf_path, pdf)

    return buf.getvalue()


def _build_readme() -> str:
    """Build README for the ZIP bundle."""
    lines = [
        "TAKEDOWN DRAFT BUNDLE",
        "======================",
        "",
        "This bundle contains draft takedown notices and evidence PDFs for selected matches.",
        "",
        "FILES:",
        "  - README.txt (this file)",
        "  - notices/ - One .txt file per marketplace/platform with a draft notice",
        "  - evidence/ - PDF evidence reports for each match (one per PDF)",
        "",
        "Listings archived with 'Save pages to Wayback Machine' show the archived link in the notice",
        "and the PDF, so the evidence stays available if the seller deletes the page.",
        "",
        "INSTRUCTIONS:",
        "1. Open each notice file in notices/. Each starts with a disclaimer header.",
        "2. DELETE the disclaimer header (the line starting with 'Draft generated by Brand Guard').",
        "3. Review the notice carefully. These are DRAFTS and must be edited.",
        "4. Fill in all [PLACEHOLDER] fields with accurate information.",
        "5. Confirm the [CONFIRM: ...] statements if accurate.",
        "6. Sign and date each notice.",
        "7. For marketplace notices (Amazon, eBay, etc), use the platform's official form.",
        "8. For website hosts, email to the abuse contact with the notice and evidence PDFs attached.",
        "",
        "IMPORTANT:",
        "- Delete the disclaimer header before sending.",
        "- These are signals for human review, not legal determinations.",
        "- Edit notices before sending. Do not send unverified or inaccurate notices.",
        "- You are responsible for ensuring all notices comply with applicable law.",
        "- Attach the relevant evidence PDFs to your submission.",
        "",
        "LEGAL DISCLAIMER:",
        "Brand Guard is automated analysis software. It provides signals and observations,",
        "not legal findings or conclusions of infringement. The rights holder is responsible",
        "for ensuring all notices are factually accurate and comply with applicable law,",
        "including the DMCA or similar legislation. The operator must have a good-faith basis",
        "for each claim. All confirmations must be truthful and subject to perjury penalties.",
    ]
    return "\n".join(lines)
