"""Deep fundamental profile.

``build_profile`` returns a structured dict (scores + a pre-rendered ``report``
string) with no printing/``display`` side effects, so a server or the CLI can
use the data or print ``profile["report"]`` as it sees fit.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .ingest import fetch_profile


def _fmt_val(val, fmt="pct"):
    """Format a single value for display; return '—' on NaN/None."""
    try:
        if val is None or (isinstance(val, float) and np.isnan(val)):
            return "—"  # em dash
        if fmt == "pct":
            return f"{val:.1%}"
        if fmt == "ratio":
            return f"{val:.2f}"
        if fmt == "cash":
            if abs(val) >= 1e12:
                return f"${val/1e12:.1f}T"
            if abs(val) >= 1e9:
                return f"${val/1e9:.1f}B"
            if abs(val) >= 1e6:
                return f"${val/1e6:.0f}M"
            return f"${val:,.0f}"
        if fmt == "int":
            return f"{int(val):,}"
        return str(val)
    except Exception:
        return "—"


def build_profile(ticker: str, screened_df: pd.DataFrame | None = None) -> dict:
    """Build a 5-section deep fundamental profile for ``ticker``.

    Returns a dict with the raw profile, derived sub-scores (management, moat,
    long-term potential), and a ready-to-print ``report`` string. Optionally
    reuse a screened DataFrame (Step 2) for EPS/Rev growth, FCF, Debt/Equity,
    P/E, and the 0–6 Fundamental Score instead of re-fetching.
    """
    p = fetch_profile(ticker)
    W = 70
    HDBL = "=" * W
    SEP = "-" * W
    out: list[str] = []

    # Pull preferred values from screened_df when available.
    fund_score = None
    pe_val = np.nan
    eps_growth = p["earningsGrowth"]
    rev_growth = p["revenueGrowth"]
    fcf_val = np.nan
    debt_eq = np.nan

    if screened_df is not None and not screened_df.empty and ticker in screened_df.index:
        row = screened_df.loc[ticker]
        fs = row.get("Fundamental_Score", np.nan)
        fund_score = int(fs) if pd.notna(fs) else None
        pe_val = row.get("PE", np.nan)
        eps_growth = row.get("EPS_Growth", eps_growth)
        rev_growth = row.get("Rev_Growth", rev_growth)
        fcf_val = row.get("FCF", np.nan)
        debt_eq = row.get("Debt_Equity", np.nan)

    name = p["shortName"] or ticker.upper()
    sector = p["sector"] or "—"
    industry = p["industry"] or "—"
    country = p["country"] or "—"
    emp_str = _fmt_val(p["numberOfEmployees"], "int")
    fs_str = f"  Fundamental Score: {fund_score}/6" if fund_score is not None else ""

    # -- Header --
    out.append(HDBL)
    out.append(f"  {name} ({ticker.upper()}) · {sector} · {industry}")
    out.append(f"  Employees: {emp_str}  ·  Country: {country}{fs_str}")
    out.append(HDBL)

    # -- Section 1: Business Overview --
    out.append("\nSECTION 1 · BUSINESS OVERVIEW")
    summary = (p.get("longBusinessSummary") or "")[:400]
    if summary:
        words, line, lines = summary.split(), [], []
        for w in words:
            if len(" ".join(line + [w])) > 66:
                lines.append("  " + " ".join(line))
                line = [w]
            else:
                line.append(w)
        if line:
            lines.append("  " + " ".join(line))
        if len(p.get("longBusinessSummary") or "") > 400:
            lines[-1] += "..."
        out.append("\n".join(lines))
    else:
        out.append("  No business summary available.")

    # -- Section 2: Financial Ratios --
    out.append(f"\n{SEP}")
    out.append("SECTION 2 · FINANCIAL RATIOS")

    gm = _fmt_val(p["grossMargins"], "pct")
    opm = _fmt_val(p["operatingMargins"], "pct")
    npm = _fmt_val(p["profitMargins"], "pct")
    roe = _fmt_val(p["returnOnEquity"], "pct")
    roa = _fmt_val(p["returnOnAssets"], "pct")
    rg = _fmt_val(rev_growth, "pct")
    eg = _fmt_val(eps_growth, "pct")
    cr = _fmt_val(p["currentRatio"], "ratio")
    qr = _fmt_val(p["quickRatio"], "ratio")
    de = _fmt_val(debt_eq, "ratio")
    cash = _fmt_val(p["totalCash"], "cash")
    pe_s = _fmt_val(pe_val, "ratio")
    pb = _fmt_val(p["priceToBook"], "ratio")
    ps = _fmt_val(p["priceToSalesTrailing12Months"], "ratio")
    peg = _fmt_val(p["pegRatio"], "ratio")
    eveb = _fmt_val(p["enterpriseToEbitda"], "ratio")

    C1, C2 = 22, 24

    def _r(a, b, c=""):
        return f"  {a:<{C1}}│  {b:<{C2}}│  {c}"

    out.append(_r("Profitability", "Growth", "Health"))
    out.append(_r(f"Gross Margin  {gm}", f"Revenue Growth  {rg}", f"Current Ratio  {cr}"))
    out.append(_r(f"Op. Margin    {opm}", f"EPS Growth     {eg}", f"Quick Ratio    {qr}"))
    out.append(_r(f"Net Margin    {npm}", "", f"Debt/Equity    {de}"))
    out.append(_r(f"ROE           {roe}", "Valuation", f"Total Cash     {cash}"))
    out.append(_r(f"ROA           {roa}", f"P/E {pe_s}  PEG  {peg}", ""))
    out.append(_r("", f"P/B {pb}  P/S  {ps}", ""))
    out.append(_r("", f"EV/EBITDA    {eveb}", ""))

    # -- Section 3: Management Quality --
    out.append(f"\n{SEP}")
    out.append("SECTION 3 · MANAGEMENT QUALITY (proxies)")

    ins = p["heldPercentInsiders"]
    inst = p["heldPercentInstitutions"]
    short_f = p["shortPercentOfFloat"]
    is_mega = pd.notna(p["numberOfEmployees"]) and p["numberOfEmployees"] > 50_000

    ins_s = _fmt_val(ins, "pct")
    inst_s = _fmt_val(inst, "pct")
    short_s = _fmt_val(short_f, "pct")

    ins_pass = pd.notna(ins) and ins > 0.05
    inst_pass = pd.notna(inst) and inst > 0.50
    short_pass = pd.notna(short_f) and short_f < 0.05
    mgmt_score = int(sum([ins_pass, inst_pass, short_pass]))

    ins_lbl = ("Strong alignment" if ins_pass
               else "Low (typical for mega-cap)" if is_mega
               else "Low insider ownership")
    inst_lbl = "High confidence" if inst_pass else "Below 50% threshold"
    short_lbl = "Low bearish pressure" if short_pass else "Elevated short interest"
    mgmt_lbl = {3: "Strong", 2: "Neutral", 1: "Weak", 0: "Concern"}[mgmt_score]

    out.append(f"  Insider Ownership:        {ins_s:>7}  →  {ins_lbl}")
    out.append(f"  Institutional Ownership:  {inst_s:>7}  →  {inst_lbl}")
    out.append(f"  Short Interest (Float):   {short_s:>7}  →  {short_lbl}")
    out.append(f"  Management Score: {mgmt_score}/3  [{mgmt_lbl}]")

    # -- Section 4: Moat --
    out.append(f"\n{SEP}")
    out.append("SECTION 4 · COMPETITIVE ADVANTAGES (moat heuristics)")

    roe_raw = p["returnOnEquity"]
    gm_raw = p["grossMargins"]
    nm_raw = p["profitMargins"]

    roe_pass2 = pd.notna(roe_raw) and roe_raw > 0.15
    gm_pass2 = pd.notna(gm_raw) and gm_raw > 0.30
    nm_pass2 = pd.notna(nm_raw) and nm_raw > 0.10
    moat_score = int(sum([roe_pass2, gm_pass2, nm_pass2]))

    def _moat_row(label, val_str, passed, pass_lbl, fail_lbl):
        sym = "✓" if passed else "✗"  # ✓ or ✗
        lbl = pass_lbl if passed else fail_lbl
        return f"  {label:<14}{val_str:<8} {sym}  {lbl}"

    moat_lbl = {3: "Wide Moat", 2: "Narrow Moat", 1: "Uncertain", 0: "Weak/None"}[moat_score]
    out.append(_moat_row("ROE", roe, roe_pass2, "Exceptional (>15%)", "Below threshold (≤15%)"))
    out.append(_moat_row("Gross Margin", gm, gm_pass2, "Strong pricing power (>30%)", "Below threshold (≤30%)"))
    out.append(_moat_row("Net Margin", npm, nm_pass2, "High profitability (>10%)", "Below threshold (≤10%)"))
    out.append(f"  Moat Score: {moat_score}/3  [{moat_lbl}]")

    # -- Section 5: Long-Term Potential --
    out.append(f"\n{SEP}")
    out.append("SECTION 5 · LONG-TERM POTENTIAL")

    peg_raw = p["pegRatio"]
    peg_pass = pd.notna(peg_raw) and peg_raw < 1.5
    rg_pass = pd.notna(rev_growth) and rev_growth > 0.10
    eg_pass = pd.notna(eps_growth) and eps_growth > 0.15
    fcf_pass = pd.notna(fcf_val) and fcf_val > 0
    lt_score = int(sum([peg_pass, rg_pass, eg_pass, fcf_pass]))

    peg_s2 = _fmt_val(peg_raw, "ratio")
    rg_pct = _fmt_val(rev_growth, "pct")
    eg_pct = _fmt_val(eps_growth, "pct")
    fcf_s = _fmt_val(fcf_val, "cash")

    peg_lbl = ("✓ Growth undervalued (< 1.5)" if peg_pass
               else "Growth priced in (< 1.5 = undervalued)" if pd.notna(peg_raw)
               else "—")
    rg_lbl = "✓ Strong top-line momentum (>10%)" if rg_pass else ("Moderate (<10%)" if pd.notna(rev_growth) else "—")
    eg_lbl = "✓ Strong earnings power (>15%)" if eg_pass else ("Moderate (<15%)" if pd.notna(eps_growth) else "—")
    fcf_lbl = "✓ Positive cash generation" if fcf_pass else ("Negative FCF" if pd.notna(fcf_val) else "—")
    lt_lbl = {4: "Exceptional", 3: "Strong", 2: "Moderate", 1: "Speculative", 0: "Caution"}[lt_score]

    out.append(f"  PEG Ratio:       {peg_s2:>7}  →  {peg_lbl}")
    out.append(f"  Revenue Growth:  {rg_pct:>7}  →  {rg_lbl}")
    out.append(f"  EPS Growth:      {eg_pct:>7}  →  {eg_lbl}")
    out.append(f"  FCF:             {fcf_s:>7}  →  {fcf_lbl}")
    out.append(f"  Long-Term Score: {lt_score}/4  [{lt_lbl}]")
    out.append(HDBL)

    return {
        "ticker": ticker.upper(),
        "name": name,
        "sector": sector,
        "industry": industry,
        "country": country,
        "fundamental_score": fund_score,
        "scores": {"management": mgmt_score, "moat": moat_score, "long_term": lt_score},
        "raw": p,
        "report": "\n".join(out),
    }
