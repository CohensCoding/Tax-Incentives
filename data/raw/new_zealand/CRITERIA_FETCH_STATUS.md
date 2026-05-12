NZ Criteria Document — Fetch Status

The canonical NZSPR International Criteria document is published by NZFC as a DOCX
file, not a PDF:

  URL:  https://www.nzfilm.co.nz/assets/resources/NZSPR-International-Criteria-1-January-2026-v.1.0.docx
  Page: https://www.nzfilm.co.nz/resources/nzspr-criteria-international-productions
  Title: "NZSPR International Criteria - 1 January 2026 v.1.0"
  Size: 235.86 KB
  Effective: Productions starting Principal Photography or PDV Activity on or after 1 January 2026

This file cannot be staged from a web_fetch context — DOCX is binary and the fetch
tool only returns text-extractable content. The DOCX should be downloaded directly
to data/raw/new_zealand/ by whichever environment has network access to nzfilm.co.nz:

  wget https://www.nzfilm.co.nz/assets/resources/NZSPR-International-Criteria-1-January-2026-v.1.0.docx \
       -O data/raw/new_zealand/2026-05-12_nzfc_criteria_international_2026.docx

  python -m docx2txt data/raw/new_zealand/2026-05-12_nzfc_criteria_international_2026.docx \
       > data/raw/new_zealand/2026-05-12_nzfc_criteria_international_2026.txt

Use python-docx or docx2txt (both on PyPI) for text extraction.

WHY THIS IS NOT BLOCKING MILESTONE 3
-------------------------------------
The reason this document was originally flagged as needed was to capture ATL cap
rules from Section 3. The 7 November 2025 ministerial reform REMOVED the ATL cap
on the International Rebate entirely, effective 1 January 2026. This is confirmed
in the NZFC news release captured alongside this file as
2026-05-12_nzfc_news_release_2026_changes.html, which states verbatim:

  "Removal of Above-The-Line Cap — The cap on above-the-line costs will be removed."

For estimate_rebate purposes, NZ International Live Action is now:

  rebate = 0.20 * QNZPE
  rebate_with_uplift = 0.25 * QNZPE  (if Production Rebate 5% Uplift criteria met)

with ATL costs counted toward QNZPE without a separate cap.

The Criteria DOCX is still worth staging for completeness — it contains the precise
QNZPE definitions, the 14-day non-resident-non-cast rule, and PDV activity scope.
But it is no longer load-bearing for milestone 3.

WHAT WE HAVE FROM OFFICIAL SOURCES
-----------------------------------
- 20% base rebate on QNZPE                                          (web page, captured)
- NZ$4M live action / NZ$250K PDV minimum spend, from 1 Jan 2026    (web page, captured)
- Pre-1-Jan-2026 transitional thresholds: NZ$15M live action        (web page, captured)
- 5% Uplift: NZ$20M minimum, 40/85 points, Production Rebate Uplift (uplift page, captured)
- 5% Uplift: PDV Rebate Uplift, no separate threshold, new 1 Jan 26 (uplift page, captured)
- ATL cap removed effective 1 Jan 2026                              (news release, captured)
- QNZPE definition summary: services in NZ, land in NZ, locally-
  sourced goods, overseas goods only if unavailable in NZ           (web page, captured)
- Non-resident non-cast 14-day minimum work threshold               (web page, captured)
- SPV requirement, registration before PP, 6-month final window     (web page, captured)
- Payment timing: 3-month review, 10-day payment post-invoice       (background page, captured)
- Interim payment trigger: NZ$50M QNZPE                             (web page, captured)
