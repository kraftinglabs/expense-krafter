"""Spend analysis dashboard."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
from nicegui import ui

from ..services import analytics, cards, ledger
from ..services.money import fmt
from .layout import frame

PERIODS = {"this_month": "This month", "last_month": "Last month", "3m": "Last 3 months",
           "6m": "Last 6 months", "12m": "Last 12 months", "ytd": "This year", "all": "All time"}
PERIOD_SHORT = {"this_month": "This month", "last_month": "Last month", "3m": "3M", "6m": "6M",
                "12m": "12M", "ytd": "YTD", "all": "All"}
ACCENT = "#34e0a1"         # thin lines / "this month"
BAR_GREEN = "#1d9e74"      # filled single-series bars: deeper so they do not outshine the hero
BASELINE_GRAY = "#5d6378"  # comparison series
SURFACE = "#12141c"        # gap between stacked segments
GRID = "rgba(255,255,255,0.06)"
TEXT_MUTED = "#a3a8ba"


def _rupees(v: float) -> str:
    return f"₹{v:,.0f}"


def _style(fig: go.Figure, height: int = 320, legend: bool = False) -> go.Figure:
    fig.update_layout(
        template="plotly_dark", height=height, margin=dict(l=8, r=8, t=8, b=8),
        font=dict(family="Inter, system-ui, sans-serif", size=12, color=TEXT_MUTED),
        showlegend=legend,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, title_text="", font=dict(size=11)),
        hoverlabel=dict(bgcolor="#232736", font_color="#eceef5", bordercolor="rgba(255,255,255,0.13)",
                        font_family="Inter, system-ui, sans-serif"),
        bargap=0.38, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    )
    fig.update_xaxes(showgrid=False, linecolor=GRID, title_text="", tickfont=dict(color="#6d7288"))
    fig.update_yaxes(gridcolor=GRID, zeroline=False, title_text="", tickprefix="₹", tickformat="~s",
                     tickfont=dict(color="#6d7288"))
    return fig


def _chart_card(title: str, subtitle: str = "", classes: str = ""):
    card = ui.card().classes(f"w-full et-card-hover et-rise {classes}")
    with card:
        with ui.column().classes("gap-0"):
            ui.label(title).classes("et-card-title")
            if subtitle:
                ui.label(subtitle).classes("et-faint text-xs")
    return card


def _empty(text: str = "No spend in this period.") -> None:
    with ui.column().classes("w-full items-center py-12 gap-2"):
        ui.icon("insights").classes("text-4xl et-faint")
        ui.label(text).classes("et-muted text-sm")


def _sparkline(values: list[float], width: int = 260, height: int = 56) -> str:
    """Inline SVG area sparkline (no axes) for the hero tile."""
    if len(values) < 2 or max(values) <= 0:
        return ""
    top = max(values)
    step = width / (len(values) - 1)
    pts = [(i * step, height - 4 - (v / top) * (height - 10)) for i, v in enumerate(values)]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = f"0,{height} {line} {width},{height}"
    lx, ly = pts[-1]
    return (
        f'<svg width="100%" height="{height}" viewBox="0 0 {width} {height}" preserveAspectRatio="none">'
        '<defs><linearGradient id="sg" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#34e0a1" stop-opacity="0.40"/>'
        '<stop offset="1" stop-color="#34e0a1" stop-opacity="0"/></linearGradient>'
        '<linearGradient id="sl" x1="0" y1="0" x2="1" y2="0">'
        '<stop offset="0" stop-color="#0fb39b"/><stop offset="1" stop-color="#8af8c8"/></linearGradient></defs>'
        f'<polygon points="{area}" fill="url(#sg)"/>'
        f'<polyline points="{line}" fill="none" stroke="url(#sl)" stroke-width="2" '
        'stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>'
        f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="3.5" fill="#8af8c8"/></svg>'
    )


def _kpi(title: str, value: str, icon: str, sub: str = "", sub_class: str = "et-faint") -> None:
    with ui.card().classes("et-kpi et-card-hover et-rise flex-1 min-w-52"):
        with ui.row().classes("w-full items-center justify-between no-wrap"):
            ui.label(title).classes("et-muted text-sm font-medium")
            with ui.element("div").classes("et-kpi-icon"):
                ui.icon(icon).classes("text-lg")
        ui.label(value).classes("et-kpi-value et-num mt-3 truncate w-full").tooltip(value)
        if sub:
            ui.label(sub).classes(f"text-xs mt-1 {sub_class}")


def _hero_kpi(title: str, total: float, change: float | None, prev: float, spark: list[float],
              count: int, per_day: float) -> None:
    with ui.card().classes("et-kpi hero et-rise flex-[1.6] min-w-80 flex flex-col"):
        with ui.row().classes("w-full items-center justify-between no-wrap"):
            ui.label(title).classes("et-muted text-sm font-medium")
            if change is not None:
                up = change > 0
                ui.html(f'<span class="et-delta {"up" if up else "down"}">{"▲" if up else "▼"} '
                        f'{abs(change):.0f}% vs previous</span>', sanitize=False).tooltip(
                    f"Previous period: {_rupees(prev)}")
        ui.label(_rupees(total)).classes("et-kpi-value et-num et-grad-text mt-3").style("font-size: 40px")
        ui.space()
        if svg := _sparkline(spark, height=90):
            ui.html(svg, sanitize=False).classes("w-full mt-2")
        with ui.row().classes("w-full gap-6 pt-3 mt-1").style("border-top: 1px solid var(--border)"):
            for value, label in ((f"{count:,}", "transactions"), (_rupees(per_day), "per day")):
                with ui.column().classes("gap-0"):
                    ui.label(value).classes("et-num font-semibold")
                    ui.label(label).classes("et-faint text-xs")


def _months_in(df: pd.DataFrame, period: analytics.Period | None) -> int:
    if period:
        return max(round(period.days / 30.4), 1)
    return max(df["date"].dt.to_period("M").nunique(), 1) if not df.empty else 1


def _days_elapsed(df: pd.DataFrame, period: analytics.Period | None) -> int:
    """Days to average over: the period so far (not days still to come), or the
    span of the data for 'All time'."""
    if period:
        end = min(period.end, date.today() + timedelta(days=1))
        return max((end - period.start).days, 1)
    if df.empty:
        return 1
    return (df["date"].max() - df["date"].min()).days + 1


def spend_over_time(df: pd.DataFrame, period: analytics.Period | None) -> None:
    daily = period is not None and period.days <= 62
    with _chart_card("Spend over time", "Daily, stacked by label" if daily else "Monthly, stacked by label"):
        if df.empty:
            return _empty()
        data = analytics.top_labels(df)
        data["bucket"] = data["date"].dt.strftime("%d %b") if daily else data["date"].dt.strftime("%b %Y")
        data["order"] = data["date"].dt.normalize() if daily else data["date"].dt.to_period("M").dt.start_time
        grouped = data.groupby(["order", "bucket", "label", "color"], as_index=False)["amount"].sum()
        buckets = grouped.sort_values("order")["bucket"].unique().tolist()
        label_order = grouped.groupby("label")["amount"].sum().sort_values(ascending=False).index
        fig = go.Figure()
        for label in label_order:
            part = grouped[grouped["label"] == label]
            fig.add_bar(x=part["bucket"], y=part["amount"], name=label, marker_color=part["color"].iloc[0],
                        marker_line=dict(color=SURFACE, width=1.5),
                        hovertemplate=f"<b>{label}</b><br>%{{x}}: ₹%{{y:,.0f}}<extra></extra>")
        _style(fig, height=340, legend=True)
        fig.update_layout(barmode="stack")
        fig.update_traces(marker_cornerradius=4)
        fig.update_xaxes(categoryorder="array", categoryarray=buckets)
        ui.plotly(fig).classes("w-full")


def by_label(df: pd.DataFrame) -> None:
    with _chart_card("Where it went", "Spend by label"):
        if df.empty:
            return _empty()
        totals = (df.groupby(["label", "color"], as_index=False)["amount"].sum()
                  .sort_values("amount"))
        total = totals["amount"].sum()
        totals["text"] = totals["amount"].map(_rupees) + "  " + (totals["amount"] / total * 100).map("{:.0f}%".format)
        fig = go.Figure(go.Bar(
            x=totals["amount"], y=totals["label"], orientation="h", marker_color=totals["color"],
            text=totals["text"], textposition="outside", cliponaxis=False,
            hovertemplate="<b>%{y}</b><br>₹%{x:,.0f}<extra></extra>",
        ))
        _style(fig, height=max(220, 34 * len(totals) + 40))
        fig.update_traces(marker_cornerradius=4)
        fig.update_xaxes(visible=False, range=[0, totals["amount"].max() * 1.6])
        fig.update_yaxes(gridcolor="rgba(0,0,0,0)", tickprefix="")
        ui.plotly(fig).classes("w-full")


def month_pace(account_id: int | None) -> None:
    today = date.today()
    this = analytics.spend_frame(analytics.period_for("this_month"), account_id)
    last = analytics.spend_frame(analytics.period_for("last_month"), account_id)
    with _chart_card("This month vs last month", "Cumulative spend by day of month"):
        if this.empty and last.empty:
            return _empty("No spend this month or last month.")
        fig = go.Figure()
        for df, name, color, dash, upto in ((last, "Last month", BASELINE_GRAY, "dot", 31),
                                            (this, "This month", ACCENT, "solid", today.day)):
            if df.empty:
                continue
            daily = df.groupby(df["date"].dt.day)["amount"].sum().reindex(range(1, upto + 1), fill_value=0)
            daily = daily.loc[: (df["date"].dt.day.max() if name == "Last month" else upto)]
            cum = daily.cumsum()
            is_this = name == "This month"
            fig.add_scatter(x=cum.index, y=cum.values, name=name, mode="lines",
                            line=dict(color=color, width=2.5 if is_this else 2, dash=dash, shape="hv"),
                            fill="tozeroy" if is_this else None, fillcolor="rgba(52,224,161,0.12)",
                            hovertemplate=f"{name}, day %{{x}}: ₹%{{y:,.0f}}<extra></extra>")
            fig.add_annotation(x=cum.index[-1], y=cum.values[-1], text=f"{name} {_rupees(cum.values[-1])}",
                               showarrow=False, xanchor="left", xshift=6, font=dict(size=11, color=TEXT_MUTED))
        _style(fig, legend=True)
        fig.update_layout(hovermode="x unified", margin=dict(r=110))
        fig.update_xaxes(range=[1, 31], dtick=5, title_text="Day of month")
        ui.plotly(fig).classes("w-full")


def top_merchants(df: pd.DataFrame, n: int = 10) -> None:
    with _chart_card("Top merchants", f"Biggest {n} by spend"):
        if df.empty:
            return _empty()
        top = (df.groupby("merchant").agg(amount=("amount", "sum"), count=("amount", "size"))
                 .sort_values("amount", ascending=False).head(n).iloc[::-1])
        fig = go.Figure(go.Bar(
            x=top["amount"], y=top.index, orientation="h", marker_color=BAR_GREEN,
            customdata=top["count"], text=top["amount"].map(_rupees), textposition="outside", cliponaxis=False,
            hovertemplate="<b>%{y}</b><br>₹%{x:,.0f} over %{customdata} payments<extra></extra>",
        ))
        _style(fig, height=max(220, 30 * len(top) + 40))
        fig.update_traces(marker_cornerradius=4)
        fig.update_xaxes(visible=False, range=[0, top["amount"].max() * 1.3])
        fig.update_yaxes(gridcolor="rgba(0,0,0,0)", tickprefix="")
        ui.plotly(fig).classes("w-full")


def by_account(df: pd.DataFrame) -> None:
    with _chart_card("By account", "Which card or bank account the spend went through"):
        if df.empty:
            return _empty()
        totals = df.groupby("account")["amount"].sum().sort_values()
        fig = go.Figure(go.Bar(
            x=totals.values, y=totals.index, orientation="h", marker_color=BAR_GREEN,
            text=[_rupees(v) for v in totals.values], textposition="outside", cliponaxis=False,
            hovertemplate="<b>%{y}</b><br>₹%{x:,.0f}<extra></extra>",
        ))
        _style(fig, height=max(160, 40 * len(totals) + 40))
        fig.update_traces(marker_cornerradius=4)
        fig.update_xaxes(visible=False, range=[0, totals.max() * 1.3])
        fig.update_yaxes(gridcolor="rgba(0,0,0,0)", tickprefix="")
        ui.plotly(fig).classes("w-full")


def recurring() -> None:
    items = analytics.recurring_items()
    subtitle = (f"About {_rupees(items['yearly'].sum())} a year" if not items.empty
                else "Transactions under recurring labels, last 12 months")
    with _chart_card("Recurring payments", subtitle):
        if items.empty:
            return _empty("Label subscriptions and bills with a recurring label to see them here.")
        ui.table(
            columns=[
                {"name": "merchant", "label": "Merchant", "field": "merchant", "align": "left"},
                {"name": "label", "label": "Label", "field": "label", "align": "left"},
                {"name": "frequency", "label": "Every", "field": "frequency", "align": "left"},
                {"name": "last", "label": "Last paid", "field": "last", "align": "left"},
                {"name": "amount", "label": "Amount", "field": "amount", "align": "right"},
                {"name": "yearly", "label": "Per year", "field": "yearly", "align": "right"},
            ],
            rows=[{"merchant": r.merchant, "label": r.label, "frequency": r.frequency,
                   "last": r.last_date.strftime("%d %b %Y"), "amount": _rupees(r.last_amount),
                   "yearly": _rupees(r.yearly)} for r in items.itertuples()],
        ).props("flat dense").classes("w-full")


@ui.page("/")
def dashboard_page() -> None:
    with frame("Dashboard", "Where your money went, across every account and card"):
        accounts = {a.id: a.name for a in ledger.accounts()}
        state = {"period": "6m", "account": None}

        with ui.row().classes("w-full items-center gap-4"):
            ui.toggle(PERIOD_SHORT, value=state["period"],
                      on_change=lambda e: (state.update(period=e.value), content.refresh())) \
                .props("unelevated rounded no-caps toggle-color=primary toggle-text-color=dark color=transparent text-color=grey-5 "
                       "padding='6px 14px'").classes("et-card")
            ui.space()
            ui.select({None: "All accounts"} | accounts, value=None,
                      on_change=lambda e: (state.update(account=e.value), content.refresh())) \
                .props("outlined dense options-dense").classes("w-56")

        @ui.refreshable
        def content() -> None:
            period = analytics.period_for(state["period"])
            df = analytics.spend_frame(period, state["account"])
            total = df["amount"].sum() if not df.empty else 0

            change, prev = None, 0.0
            if period:
                prev = analytics.spend_frame(period.previous(), state["account"])["amount"].sum()
                if prev:
                    change = (total - prev) / prev * 100
            daily = period is not None and period.days <= 62
            # Short periods: running total by day; longer: one point per month.
            if df.empty:
                spark = []
            elif daily:
                spark = df.groupby(df["date"].dt.normalize())["amount"].sum().cumsum().tolist()
            else:
                spark = df.groupby(df["date"].dt.to_period("M"))["amount"].sum().tolist()
            labelled = df[df["label"] != analytics.UNLABELLED]
            top = (labelled.groupby("label")["amount"].sum().sort_values(ascending=False) if not labelled.empty
                   else pd.Series(dtype=float))
            pending = cards.statement_statuses(only_pending=True)
            unlabelled = int((df["label"] == analytics.UNLABELLED).sum()) if not df.empty else 0

            with ui.row().classes("w-full gap-4 items-stretch"):
                _hero_kpi(f"Spent · {PERIODS[state['period']].lower()}", total, change, prev, spark,
                          len(df), total / _days_elapsed(df, period))
                with ui.element("div").classes("grid grid-cols-2 gap-4 flex-[2] min-w-[30rem]"):
                    _kpi("Average per month", _rupees(total / _months_in(df, period)), "insights",
                         "net of reimbursements & refunds")
                    _kpi("Biggest label", top.index[0] if len(top) else "—", "local_offer",
                         f"{_rupees(top.iloc[0])} · {top.iloc[0] / total * 100:.0f}% of spend"
                         if len(top) and total else "")
                    _kpi("Card bills pending", fmt(sum(p.pending_minor for p in pending)), "credit_card",
                         f"{len(pending)} statement{'s' if len(pending) != 1 else ''}")
                    _kpi("Unlabelled", str(unlabelled), "label_off", "expenses in this period",
                         "et-warn" if unlabelled else "et-faint")

            with ui.element("div").classes("grid grid-cols-1 xl:grid-cols-3 gap-4 w-full"):
                with ui.element("div").classes("xl:col-span-2"):
                    spend_over_time(df, period)
                by_label(df)
            with ui.element("div").classes("grid grid-cols-1 xl:grid-cols-2 gap-4 w-full"):
                month_pace(state["account"])
                top_merchants(df)
                by_account(df)
                recurring()

        content()
