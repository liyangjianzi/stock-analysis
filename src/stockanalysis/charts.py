"""Plotly chart builders.

Builders return a ``plotly.graph_objects.Figure`` (no ``fig.show()`` — that's a
notebook concern). :func:`save_html` writes a standalone HTML file; a server can
instead call ``fig.to_html()`` / ``fig.to_json()`` on the returned figure.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from . import config
from .indicators import fit_regression_channel, find_support_resistance


def save_html(fig: go.Figure, path) -> str:
    """Write ``fig`` to a standalone HTML file, creating parent dirs. Returns the path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(str(path), include_plotlyjs="cdn")
    return str(path)


def build_technical_dashboard(ticker: str, tech: dict, lookback: int = 252) -> go.Figure | None:
    """Build the interactive 4-panel technical dashboard for ``ticker``.

    ``tech`` is the dict (ticker -> indicator-enriched DataFrame) from
    :func:`stockanalysis.indicators.add_indicators`. Returns the Figure, or
    ``None`` if no data is available for the ticker.
    """
    if ticker not in tech or tech[ticker] is None or tech[ticker].empty:
        return None

    # Trim to the lookback window for a readable, fast chart.
    d = tech[ticker].tail(lookback)

    # 4 stacked panels sharing the x-axis: Price (tall), MACD, RSI, Volume.
    fig = make_subplots(
        rows=4, cols=1, shared_xaxes=True, vertical_spacing=0.03,
        row_heights=[0.46, 0.18, 0.18, 0.18],
        subplot_titles=(f"{ticker} — Price, EMAs, Envelope, Trend Channel & S/R",
                        "MACD (12, 26, 9)", "RSI (14)", "Volume"),
    )

    # ---------- Panel 1: Candlesticks + EMAs + Envelope ----------
    fig.add_trace(go.Candlestick(
        x=d.index, open=d["Open"], high=d["High"], low=d["Low"], close=d["Close"],
        name="Price", increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
    ), row=1, col=1)

    for col, color, width in [("EMA20", "#1f77b4", 1.3),
                              ("EMA50", "#ff7f0e", 1.3),
                              ("EMA200", "#7f3fbf", 1.6)]:
        fig.add_trace(go.Scatter(
            x=d.index, y=d[col], name=col, mode="lines",
            line=dict(color=color, width=width),
        ), row=1, col=1)

    # Envelope band: upper line + lower line filled to the upper (shaded band).
    # The band is data-driven and asymmetric, so derive its actual ± percentages
    # from the last finite EMA20 bar (the band is a constant multiple of EMA20).
    ema20_valid = d["EMA20"].dropna()
    if not ema20_valid.empty:
        i = ema20_valid.index[-1]
        up_pct = d["ENV_UP"][i] / d["EMA20"][i] - 1
        dn_pct = d["ENV_DOWN"][i] / d["EMA20"][i] - 1
        up_name, dn_name = f"Env +{up_pct:.1%}", f"Env {dn_pct:.1%}"
    else:
        up_name, dn_name = "Env upper", "Env lower"
    fig.add_trace(go.Scatter(
        x=d.index, y=d["ENV_UP"], name=up_name, mode="lines",
        line=dict(color="rgba(127,127,127,0.5)", width=1, dash="dash"),
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=d.index, y=d["ENV_DOWN"], name=dn_name, mode="lines",
        line=dict(color="rgba(127,127,127,0.5)", width=1, dash="dash"),
        fill="tonexty", fillcolor="rgba(127,127,127,0.08)",
    ), row=1, col=1)

    # ---------- Panel 1 overlay: regression trend channel ----------
    channel = fit_regression_channel(d["Close"])
    if channel:
        ctrend = "#2e7d32" if channel["slope"] > 0 else "#c62828"  # green up / red down
        fig.add_trace(go.Scatter(
            x=channel["index"], y=channel["mid"], name="Trend (regression)",
            mode="lines", line=dict(color=ctrend, width=1.6),
        ), row=1, col=1)
        for key, lbl in [("upper", "Channel +2σ"), ("lower", "Channel -2σ")]:
            fig.add_trace(go.Scatter(
                x=channel["index"], y=channel[key], name=lbl, mode="lines",
                line=dict(color=ctrend, width=1, dash="dot"), opacity=0.6,
                showlegend=False,
            ), row=1, col=1)

    # ---------- Panel 1 overlay: clustered support / resistance levels ----------
    for L in find_support_resistance(d):
        lcolor = "#26a69a" if L["kind"] == "support" else "#ef5350"
        fig.add_hline(
            y=L["level"], line=dict(color=lcolor, width=1, dash="dash"),
            annotation_text=f"{L['kind'][:3].upper()} {L['level']:.2f}",
            annotation_position="right",
            annotation_font=dict(size=9, color=lcolor),
            row=1, col=1,
        )

    # ---------- Panel 2: MACD line, signal, color-coded histogram ----------
    hist_colors = np.where(d["MACD_HIST"] >= 0, "#26a69a", "#ef5350")  # green/red
    fig.add_trace(go.Bar(
        x=d.index, y=d["MACD_HIST"], name="Histogram",
        marker_color=hist_colors, opacity=0.6,
    ), row=2, col=1)
    fig.add_trace(go.Scatter(
        x=d.index, y=d["MACD"], name="MACD", mode="lines",
        line=dict(color="#1f77b4", width=1.4),
    ), row=2, col=1)
    fig.add_trace(go.Scatter(
        x=d.index, y=d["MACD_SIG"], name="Signal", mode="lines",
        line=dict(color="#ff7f0e", width=1.4),
    ), row=2, col=1)

    # ---------- Panel 3: RSI with 30/70 thresholds + neutral band ----------
    fig.add_trace(go.Scatter(
        x=d.index, y=d["RSI"], name="RSI", mode="lines",
        line=dict(color="#7f3fbf", width=1.4),
    ), row=3, col=1)
    # Overbought (70) and oversold (30) reference lines.
    fig.add_hline(y=70, line=dict(color="#ef5350", width=1, dash="dash"),
                  annotation_text="Overbought 70", annotation_position="top left",
                  row=3, col=1)
    fig.add_hline(y=30, line=dict(color="#26a69a", width=1, dash="dash"),
                  annotation_text="Oversold 30", annotation_position="bottom left",
                  row=3, col=1)
    # Shade the 30–70 neutral zone for quick visual reference.
    fig.add_hrect(y0=30, y1=70, fillcolor="rgba(127,127,127,0.06)",
                  line_width=0, row=3, col=1)

    # ---------- Panel 4: Volume bars (green/red by day) + 20-day average ----------
    vol_colors = np.where(d["Close"] >= d["Open"], "#26a69a", "#ef5350")
    fig.add_trace(go.Bar(
        x=d.index, y=d["Volume"], name="Volume",
        marker_color=vol_colors, opacity=0.5,
    ), row=4, col=1)
    fig.add_trace(go.Scatter(
        x=d.index, y=d["VOL_SMA20"], name="Vol SMA20", mode="lines",
        line=dict(color="#1f77b4", width=1.4),
    ), row=4, col=1)

    # ---------- Layout: crosshairs, unified hover, clean styling ----------
    fig.update_layout(
        height=config.PLOT_HEIGHT,
        title=dict(text=f"📊 Technical Dashboard — {ticker}", x=0.5, xanchor="center"),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=60, r=30, t=90, b=40),
        xaxis_rangeslider_visible=False,  # hide default candlestick rangeslider
        template="plotly_white",
    )
    # Crosshair spikes across all shared x-axes for precise tracking.
    fig.update_xaxes(showspikes=True, spikemode="across", spikethickness=1,
                     spikedash="dot", spikecolor="#999999")
    fig.update_yaxes(showspikes=True, spikethickness=1, spikedash="dot",
                     spikecolor="#999999")
    fig.update_yaxes(title_text="Price", row=1, col=1)
    fig.update_yaxes(title_text="MACD", row=2, col=1)
    fig.update_yaxes(title_text="RSI", range=[0, 100], row=3, col=1)
    fig.update_yaxes(title_text="Volume", row=4, col=1)

    return fig


def build_index_overview(index_data: dict) -> go.Figure:
    """Build the 2-panel market-overview chart: rebased index prices + VIX.

    ``index_data`` is the dict returned by
    :func:`stockanalysis.overview.fetch_index_data`.
    """
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.7, 0.3], vertical_spacing=0.04,
        subplot_titles=("Major Indices — Relative Performance (rebased to 100)",
                        "VIX (Volatility Index)"),
    )
    _colors = {"S&P 500": "#1f77b4", "NASDAQ": "#ff7f0e", "TSX": "#2ca02c"}
    for name, df in index_data.items():
        if name == "VIX":
            continue
        chart_df = df["chart"] if isinstance(df, dict) else df
        if chart_df.empty:
            continue
        close = chart_df["Close"].dropna()
        if close.empty:
            continue
        rebased = close / close.iloc[0] * 100
        fig.add_trace(
            go.Scatter(x=rebased.index, y=rebased.values, name=name,
                       line=dict(color=_colors.get(name), width=2)),
            row=1, col=1,
        )
    vix_entry = index_data.get("VIX", {})
    vix_df = vix_entry.get("chart") if isinstance(vix_entry, dict) else vix_entry
    if vix_df is not None and not vix_df.empty:
        vix_close = vix_df["Close"].dropna()
        fig.add_trace(
            go.Scatter(x=vix_close.index, y=vix_close.values, name="VIX",
                       line=dict(color="#d62728", width=2),
                       fill="tozeroy", fillcolor="rgba(214,39,40,0.1)"),
            row=2, col=1,
        )
        for level, label in [(15, "Low/Moderate"), (25, "Moderate/Elevated")]:
            fig.add_hline(y=level, row=2, col=1,
                          line=dict(color="gray", dash="dash", width=1),
                          annotation_text=label, annotation_position="right")
    fig.update_layout(
        height=config.PLOT_HEIGHT, hovermode="x unified", template="plotly_white",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    fig.update_xaxes(showspikes=True, spikemode="across")
    fig.update_yaxes(showspikes=True)
    return fig


def build_backtest_report(results, title: str = "Backtest Report") -> go.Figure:
    """Two-panel report: equity curve (+ benchmark) and hit-rate by horizon."""
    fig = make_subplots(
        rows=2, cols=1, row_heights=[0.62, 0.38], vertical_spacing=0.12,
        subplot_titles=("Equity Curve vs Benchmark", "Forward-Return Hit Rate by Horizon"),
    )

    eq = results.portfolio_curve
    if eq is not None and not eq.empty:
        fig.add_trace(go.Scatter(x=eq.index, y=eq.values, name="Strategy",
                                 line=dict(color="#1F77B4")), row=1, col=1)
    bm = results.benchmark_curve
    if bm is not None and not bm.empty:
        fig.add_trace(go.Scatter(x=bm.index, y=bm.values, name="Benchmark",
                                 line=dict(color="#999999", dash="dot")), row=1, col=1)

    bucket = results.config.get("entry_bucket")
    stats = results.event_stats.get(bucket, {})
    horizons = list(stats)
    hit = [stats[h].get("hit_rate") for h in horizons]
    if horizons:
        fig.add_trace(go.Bar(x=horizons, y=hit, name="Hit rate",
                             marker_color="#2CA02C"), row=2, col=1)

    caveat = "" if results.mode == "technical" else \
        "   ⚠ COMPOSITE MODE — fundamentals frozen at today (lookahead bias)"
    fig.update_layout(title=title + caveat, height=config.PLOT_HEIGHT, showlegend=True)
    fig.update_yaxes(title_text="Equity", row=1, col=1)
    fig.update_yaxes(title_text="Hit rate", range=[0, 1], row=2, col=1)
    return fig
