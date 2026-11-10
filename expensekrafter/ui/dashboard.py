"""Spend analysis dashboard."""

from __future__ import annotations

import re
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
from nicegui import app, ui

from ..services import analytics, cards, ledger, money
from ..services.money import fmt
from .layout import frame, is_dark

PERIODS = {"this_month": "This month", "last_month": "Last month", "3m": "Last 3 months",
           "6m": "Last 6 months", "12m": "Last 12 months", "ytd": "This year", "all": "All time"}
PERIOD_SHORT = {"this_month": "This month", "last_month": "Last month", "3m": "3M", "6m": "6M",
                "12m": "12M", "ytd": "YTD", "all": "All"}
# Chart colours per theme (Plotly draws server-side, so CSS tokens don't reach it).
CHART = {
    "light": dict(accent="#007d56", bar="#007d56", baseline="#b4b9b6", surface="#ffffff", grid="#eef0ef",
                  axis="#dfe3e0", muted="#686c69", faint="#8c918e", fill="rgba(0,125,86,0.07)",
                  hover_bg="#171a18", hover_fg="#ffffff", ink="#171a18",
                  up="#D55E00", down="#0072B2", empty="#f1f3f2", heat_lo="#cfe9dd"),
    "dark": dict(accent="#3fbf8c", bar="#2f9e72", baseline="#58605b", surface="#141715", grid="#222624",
                 axis="#2a2f2c", muted="#a3a8a5", faint="#7c827e", fill="rgba(63,191,140,0.10)",
                 hover_bg="#eceeed", hover_fg="#0f1110", ink="#eceeed",
                 up="#E69F00", down="#56B4E9", empty="#1c201e", heat_lo="#1f4a38"),
}
# More / less spend than before: Okabe-Ito vermillion (orange on dark) and blue,
# colour-blind safe, and always paired with a +/− sign in the text.


def _period_name(key: str, months: dict[str, str]) -> str:
    return PERIODS[key].lower() if key in PERIODS else months.get(key, key)


def _c() -> dict:
    return CHART["dark" if is_dark() else "light"]


def _rupees(v: float) -> str:
    if money.hidden():
        return money.masked()
    return f"−₹{-v:,.0f}" if round(v) < 0 else f"₹{v:,.0f}"


_MONEY_FIELD = re.compile(r"[+−-]?₹%\{[^}]*\}")


def _mask(fig: go.Figure) -> go.Figure:
    """With amounts hidden: no money on axes, in tooltips or in colour scales.
    (Bar labels and annotations already come masked from _rupees.)"""
    if not money.hidden():
        return fig
    for trace in fig.data:
        if getattr(trace, "hovertemplate", None):
            trace.hovertemplate = _MONEY_FIELD.sub(money.masked(), trace.hovertemplate)
        if trace.type == "pie":
            trace.hoverinfo, trace.hovertemplate = None, "<b>%{label}</b><br>%{percent}<extra></extra>"
    fig.for_each_xaxis(lambda ax: ax.update(showticklabels=False) if ax.tickprefix == "₹" else None)
    fig.for_each_yaxis(lambda ax: ax.update(showticklabels=False) if ax.tickprefix == "₹" else None)
    return fig


def _plotly(fig: go.Figure):
    return ui.plotly(_mask(fig))


def _style(fig: go.Figure, height: int = 320, legend: bool = False) -> go.Figure:
    fig.update_layout(
        template="plotly_dark" if is_dark() else "plotly_white", height=height, margin=dict(l=8, r=8, t=8, b=8),
        font=dict(family="Geist, Inter, system-ui, sans-serif", size=12, color=_c()["muted"]),
        showlegend=legend,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, title_text="", font=dict(size=11)),
        hoverlabel=dict(bgcolor=_c()["hover_bg"], font_color=_c()["hover_fg"], bordercolor=_c()["hover_bg"],
                        font_family="Inter, system-ui, sans-serif"),
        bargap=0.38, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    )
    fig.update_xaxes(showgrid=False, linecolor=_c()["axis"], title_text="", tickfont=dict(color=_c()["faint"]))
    fig.update_yaxes(gridcolor=_c()["grid"], zeroline=False, title_text="", tickprefix="₹", tickformat="~s",
                     tickfont=dict(color=_c()["faint"]))
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


def _iso(index: pd.DatetimeIndex) -> list[str]:
    """Dates as ISO strings (the chart payload can't carry pandas Timestamps)."""
    return [d.strftime("%Y-%m-%d") for d in index]


def _hero_trend(series: pd.Series, daily: bool) -> None:
    """Plotly trend inside the emerald hero tile: monthly spend as an area line,
    or for one-month periods the running total by day. Drawn in white on the
    tile's own colour, with exact values on hover."""
    if len(series) < 2 or series.max() <= 0:
        return
    if daily:
        x, y = _iso(series.index), series.cumsum().tolist()
        hover, tick = "%{x|%d %b}: ₹%{y:,.0f} so far<extra></extra>", "%d %b"
    else:
        x, y = _iso(series.index.to_timestamp()), series.tolist()
        hover, tick = "%{x|%b %Y}: ₹%{y:,.0f}<extra></extra>", "%b"
    fig = go.Figure(go.Scatter(
        x=x, y=y, mode="lines" if daily else "lines+markers",
        line=dict(color="#ffffff", width=2.5, shape="linear" if daily else "spline", smoothing=0.6),
        marker=dict(size=6, color="#ffffff"),
        fill="tozeroy", fillgradient=dict(type="vertical", colorscale=[
            [0, "rgba(255,255,255,0)"], [1, "rgba(255,255,255,0.28)"]]),
        hovertemplate=hover,
    ))
    # Emphasise the latest point.
    fig.add_scatter(x=[x[-1]], y=[y[-1]], mode="markers", hoverinfo="skip",
                    marker=dict(size=11, color="#ffffff", line=dict(color="rgba(255,255,255,0.35)", width=6)))
    fig.update_layout(
        height=120, margin=dict(l=4, r=8, t=8, b=0), showlegend=False, hovermode="x",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Geist, Inter, system-ui, sans-serif", size=11, color="rgba(255,255,255,0.72)"),
        hoverlabel=dict(bgcolor="#ffffff", font_color="#0f1110", bordercolor="#ffffff"),
    )
    fig.update_xaxes(showgrid=False, zeroline=False, showline=False, tickformat=tick, ticks="", fixedrange=True,
                     **(dict(nticks=7) if daily else dict(tickvals=x)))
    # Pad the ends so the first/last month labels and the end marker aren't clipped.
    pad = pd.Timedelta(days=1 if daily else 12)
    fig.update_xaxes(range=[(pd.Timestamp(x[0]) - pad).strftime("%Y-%m-%d"),
                            (pd.Timestamp(x[-1]) + pad).strftime("%Y-%m-%d")])
    # Start at zero so heights compare honestly; headroom for the end marker.
    fig.update_yaxes(visible=False, range=[0, max(y) * 1.15], fixedrange=True)
    _plotly(fig).classes("w-full mt-2")


def _kpi(title: str, value: str, icon: str, sub: str = "", sub_class: str = "et-faint") -> None:
    with ui.card().classes("et-kpi et-card-hover et-rise flex-1 min-w-52"):
        with ui.row().classes("w-full items-center justify-between no-wrap"):
            ui.label(title).classes("et-muted text-sm font-medium")
            with ui.element("div").classes("et-kpi-icon"):
                ui.icon(icon).classes("text-lg")
        ui.label(value).classes("et-kpi-value et-num mt-3 truncate w-full").tooltip(value)
        if sub:
            ui.label(sub).classes(f"text-xs mt-1 {sub_class}")


def _hero_kpi(title: str, total: float, change: float | None, prev: float, trend: pd.Series,
              daily: bool, count: int, per_day: float, per_month: float) -> None:
    with ui.card().classes("et-kpi hero et-rise flex-[1.6] min-w-80 flex flex-col"):
        with ui.row().classes("w-full items-center justify-between no-wrap"):
            ui.label(title).classes("et-muted text-sm font-medium")
            if change is not None:
                up = change > 0
                ui.html(f'<span class="et-delta {"up" if up else "down"}">{"▲" if up else "▼"} '
                        f'{abs(change):.0f}% vs previous</span>', sanitize=False).tooltip(
                    f"Previous period: {_rupees(prev)}")
        ui.label(_rupees(total)).classes("et-kpi-value et-num mt-4")
        ui.space()
        _hero_trend(trend, daily)
        with ui.row().classes("w-full gap-6 pt-3 mt-1 et-hero-rule"):
            for value, label in ((f"{count:,}", "transactions"), (_rupees(per_day), "per day"),
                                 (_rupees(per_month), "per month")):
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


def _bar_transactions(rows: pd.DataFrame, label: str, when: str) -> None:
    """Dialog listing the transactions behind one bar of 'Spend over time'."""
    rows = rows.sort_values("amount", ascending=False)
    several = rows["label_orig"].nunique() > 1  # "Other" or a group label holds several labels
    with ui.dialog() as dialog, ui.card().classes("w-[60rem] max-w-full gap-3"):
        with ui.row().classes("w-full items-start no-wrap"):
            with ui.column().classes("gap-0"):
                ui.label(f"{label} · {when}").classes("et-card-title")
                ui.label(f"{len(rows)} transaction{'s' if len(rows) != 1 else ''} · "
                         f"{_rupees(rows['amount'].sum())}").classes("et-faint text-xs")
            ui.space()
            ui.button(icon="close", on_click=dialog.close).props("flat round dense")
        columns = [
            {"name": "date", "label": "Date", "field": "date", "align": "left"},
            {"name": "merchant", "label": "Paid to", "field": "merchant", "align": "left"},
            {"name": "description", "label": "Details", "field": "description", "align": "left"},
            {"name": "account", "label": "Account", "field": "account", "align": "left"},
            {"name": "amount", "label": "Amount", "field": "amount", "align": "right"},
        ]
        if several:
            columns.insert(2, {"name": "label", "label": "Label", "field": "label", "align": "left"})
        table = ui.table(
            columns=columns, pagination=0,
            rows=[{"date": r.date.strftime("%d %b %Y"), "merchant": r.merchant, "description": r.description,
                   "account": r.account, "amount": _rupees(r.amount), "label": r.label_orig}
                  for r in rows.itertuples()],
        ).props("flat dense hide-bottom").classes("w-full")
        table.add_slot("body-cell-description", r"""
            <q-td :props="props">
              <div class="truncate max-w-72 et-faint text-xs" :title="props.value">{{ props.value }}</div>
            </q-td>""")
    dialog.on("hide", dialog.delete)
    dialog.open()


def spend_over_time(df: pd.DataFrame, period: analytics.Period | None) -> None:
    daily = period is not None and period.days <= 62
    subtitle = ("Daily" if daily else "Monthly") + ", stacked by label · click a bar to see its transactions"
    with _chart_card("Spend over time", subtitle):
        if df.empty:
            return _empty()
        data = analytics.top_labels(df).assign(label_orig=df["sublabel"])
        data["bucket"] = data["date"].dt.strftime("%d %b") if daily else data["date"].dt.strftime("%b %Y")
        data["order"] = data["date"].dt.normalize() if daily else data["date"].dt.to_period("M").dt.start_time
        grouped = data.groupby(["order", "bucket", "label", "color"], as_index=False)["amount"].sum()
        buckets = grouped.sort_values("order")["bucket"].unique().tolist()
        label_order = grouped.groupby("label")["amount"].sum().sort_values(ascending=False).index
        fig = go.Figure()
        for label in label_order:
            part = grouped[grouped["label"] == label]
            fig.add_bar(x=part["bucket"], y=part["amount"], name=label, marker_color=part["color"].iloc[0],
                        marker_line=dict(color=_c()["surface"], width=1.5),
                        customdata=[[b, label] for b in part["bucket"]],
                        hovertemplate=f"<b>{label}</b><br>%{{x}}: ₹%{{y:,.0f}}<extra></extra>")
        _style(fig, height=340, legend=True)
        fig.update_layout(barmode="stack", dragmode=False)
        fig.update_traces(marker_cornerradius=4)
        fig.update_xaxes(categoryorder="array", categoryarray=buckets)

        def on_click(e) -> None:
            points = (e.args or {}).get("points") or []
            if not points or not points[0].get("customdata"):
                return
            bucket, label = points[0]["customdata"]
            rows = data[(data["bucket"] == bucket) & (data["label"] == label)]
            if not rows.empty:
                when = rows["date"].iloc[0].strftime("%a %d %b %Y") if daily else bucket
                _bar_transactions(rows, label, when)

        _plotly(fig).classes("w-full et-click-bars").on("plotly_click", on_click)


def cash_flow(spend: pd.DataFrame, income: pd.DataFrame, period: analytics.Period | None) -> None:
    with _chart_card("Money in vs out", "Income and spend per month; the line is what you kept"):
        if spend.empty and income.empty:
            return _empty("No income or spend in this period.")
        # From the first month with either income or spend.
        first = min(f["date"].min() for f in (spend, income) if not f.empty).date().replace(day=1)
        end = period.end if period else date.today() + timedelta(days=1)
        shown = analytics.Period(max(first, period.start) if period else first, end)
        out_m = analytics.monthly_totals(spend, shown, from_first_data=False)
        in_m = analytics.monthly_totals(income, shown, from_first_data=False)
        months = _iso(out_m.index.to_timestamp())
        net = in_m - out_m
        fig = go.Figure()
        fig.add_bar(x=months, y=in_m.values, name="Income", marker_color=_c()["accent"],
                    hovertemplate="Income: ₹%{y:,.0f}<extra></extra>")
        fig.add_bar(x=months, y=out_m.values, name="Spend", marker_color=_c()["baseline"],
                    hovertemplate="Spend: ₹%{y:,.0f}<extra></extra>")
        fig.add_scatter(x=months, y=net.values, name="Kept", mode="lines+markers",
                        line=dict(color=_c()["ink"], width=2), marker=dict(size=6),
                        hovertemplate="Kept: ₹%{y:,.0f}<extra></extra>")
        _style(fig, height=320, legend=True)
        fig.update_traces(selector=dict(type="bar"), marker_cornerradius=4)
        fig.update_layout(barmode="group", bargap=0.3, bargroupgap=0.08, hovermode="x unified")
        fig.update_xaxes(tickformat="%b %Y", dtick="M1", hoverformat="%B %Y")
        fig.update_yaxes(zeroline=True, zerolinecolor=_c()["axis"])
        _plotly(fig).classes("w-full")


def balance_chart(daily: pd.DataFrame, period: analytics.Period | None) -> None:
    with _chart_card("Bank balance", "End-of-day balance from your statements, per account"):
        if daily.empty:
            return _empty("No statement with a balance column yet.")
        shown = daily
        if period:
            start = max(pd.Timestamp(period.start) - pd.Timedelta(days=1), daily.index.min())
            shown = daily.loc[start:pd.Timestamp(min(period.end - timedelta(days=1), date.today()))]
        x = _iso(shown.index)
        fig = go.Figure()
        for acc in shown.columns:
            fig.add_scatter(x=x, y=shown[acc].values, name=acc, mode="lines", stackgroup="one",
                            line=dict(width=1.5, shape="hv"),
                            hovertemplate=f"{acc}: ₹%{{y:,.0f}}<extra></extra>")
        total = shown.sum(axis=1)
        fig.add_scatter(x=x, y=total.values, name="Total", mode="lines",
                        line=dict(color=_c()["ink"], width=2, shape="hv"),
                        hovertemplate="Total: ₹%{y:,.0f}<extra></extra>")
        _style(fig, height=320, legend=True)
        fig.update_layout(hovermode="x unified", colorway=[_c()["accent"], "#2a78d6", "#eda100", "#6250d6"])
        fig.update_xaxes(hoverformat="%d %b %Y")
        _plotly(fig).classes("w-full")


LABEL_TOP = 5
LABEL_CHARTS = {"bar": "Bar", "pie": "Pie"}


def _label_figure(totals: pd.DataFrame, kind: str, height: int | None = None) -> go.Figure:
    """Spend by label as horizontal bars (biggest on top) or a donut. Labels where
    refunds beat spend (net negative) are left out of the chart; the breakdown
    table still lists them."""
    shown = totals[totals["amount"] > 0]
    if kind == "pie":
        fig = go.Figure(go.Pie(
            labels=shown["label"], values=shown["amount"], hole=0.58, sort=False, direction="clockwise",
            marker=dict(colors=shown["color"], line=dict(color=_c()["surface"], width=2)),
            textinfo="percent", textposition="inside", insidetextorientation="horizontal",
            hovertemplate="<b>%{label}</b><br>₹%{value:,.0f} · %{percent}<extra></extra>",
        ))
        _style(fig, height=height or 340, legend=True)
        fig.update_layout(legend=dict(orientation="h", yanchor="top", y=-0.02, x=0.5, xanchor="center"),
                          annotations=[dict(text=f"<b>{_rupees(totals['amount'].sum())}</b><br>spent",
                                            showarrow=False, font=dict(size=13, color=_c()["ink"]))])
        return fig
    rows = shown.iloc[::-1]  # Plotly draws the first bar at the bottom
    text = rows["amount"].map(_rupees) + "  " + rows["share"].map("{:.0f}%".format)
    fig = go.Figure(go.Bar(
        x=rows["amount"], y=rows["label"], orientation="h", marker_color=rows["color"],
        text=text, textposition="outside", cliponaxis=False,
        hovertemplate="<b>%{y}</b><br>₹%{x:,.0f}<extra></extra>",
    ))
    _style(fig, height=height or max(220, 34 * len(rows) + 40))
    fig.update_traces(marker_cornerradius=4)
    fig.update_xaxes(visible=False, range=[0, rows["amount"].max() * 1.85])  # room for the value text
    fig.update_yaxes(gridcolor="rgba(0,0,0,0)", tickprefix="")
    return fig


def _label_breakdown(df: pd.DataFrame, kind: str) -> None:
    """Dialog with every label: the chart plus a table of amounts and shares."""
    totals = analytics.label_totals(df)
    groups = set(df.loc[df["sublabel"] != df["label"], "label"])  # group labels with labels inside
    inside = df[df["label"].isin(groups)].groupby(["label", "sublabel"])["amount"].sum()
    with ui.dialog() as dialog, ui.card().classes("w-[56rem] max-w-full gap-4"):
        with ui.row().classes("w-full items-center no-wrap"):
            with ui.column().classes("gap-0"):
                ui.label("Where it went").classes("et-card-title")
                ui.label(f"All {len(totals)} labels · {_rupees(totals['amount'].sum())}").classes("et-faint text-xs")
            ui.space()
            ui.button(icon="close", on_click=dialog.close).props("flat round dense")
        with ui.element("div").classes("grid grid-cols-1 md:grid-cols-2 gap-6 w-full items-start"):
            _plotly(_label_figure(totals, kind, height=None if kind == "bar" else 420)).classes("w-full")
            with ui.column().classes("w-full gap-0"):
                for r in totals.itertuples():
                    with ui.row().classes("w-full items-center no-wrap gap-3 py-2 et-hero-rule"):
                        ui.element("span").classes("w-3 h-3 rounded-full shrink-0") \
                            .style(f"background:{r.color}")
                        ui.label(r.label).classes("flex-1 truncate text-sm")
                        ui.label(f"{r.share:.1f}%").classes("et-faint text-xs et-num w-12 text-right")
                        ui.label(_rupees(r.amount)).classes("et-num text-sm font-medium w-24 text-right")
                    if r.label in inside.index.get_level_values(0):
                        for sub, amount in inside[r.label].sort_values(ascending=False).items():
                            with ui.row().classes("w-full items-center no-wrap gap-3 py-1 pl-6"):
                                ui.icon("subdirectory_arrow_right").classes("et-faint text-sm")
                                ui.label(sub).classes("flex-1 truncate text-xs et-muted")
                                ui.label(_rupees(amount)).classes("et-num text-xs et-muted w-24 text-right")
    dialog.on("hide", dialog.delete)
    dialog.open()


def by_label(df: pd.DataFrame) -> None:
    store = app.storage.user
    with ui.card().classes("w-full et-card-hover et-rise"):
        with ui.row().classes("w-full items-start no-wrap"):
            with ui.column().classes("gap-0"):
                ui.label("Where it went").classes("et-card-title")
                ui.label(f"Spend by label, top {LABEL_TOP}").classes("et-faint text-xs")
            ui.space()
            kind = ui.toggle(LABEL_CHARTS, value=store.get("label_chart", "bar"),
                             on_change=lambda e: (store.update(label_chart=e.value), body.refresh())) \
                .props("unelevated no-caps dense toggle-color=primary color=transparent text-color=grey-8 "
                       "padding='2px 10px'").classes("et-segment")

        @ui.refreshable
        def body() -> None:
            if df.empty:
                return _empty()
            totals = analytics.label_totals(df, top=LABEL_TOP)
            _plotly(_label_figure(totals, kind.value)).classes("w-full")
            if analytics.MISC in totals["label"].values:
                hidden = df["label"].nunique() - LABEL_TOP
                ui.button(f"Show all labels ({hidden} more in Miscellaneous)", icon="open_in_full",
                          on_click=lambda: _label_breakdown(df, kind.value)) \
                    .props("flat no-caps dense").classes("self-center text-sm")

        body()


def _signed(v: float) -> str:
    if money.hidden():
        return money.masked()
    return f"+₹{v:,.0f}" if v > 0 else f"−₹{-v:,.0f}"


def what_changed(df: pd.DataFrame, period: analytics.Period | None, account_id: int | None) -> None:
    if period is None:
        with _chart_card("What changed", "Spend per label vs the previous period"):
            return _empty("Pick a time period to compare it with the one before.")
    compare = analytics.comparable_previous(period)
    running = compare.days < period.days
    before = analytics.spend_frame(compare, account_id)
    subtitle = (f"Spend per label vs the first {compare.days} days of the previous period" if running
                else "Spend per label vs the previous period")
    with _chart_card("What changed", subtitle):
        if before.empty:  # every label would show as a pure rise
            return _empty("No spend recorded in the previous period to compare with.")
        changes = analytics.label_changes(df, before)
        if changes.empty:
            return _empty("No change from the previous period.")
        rows = changes.iloc[::-1]  # biggest rise on top
        colors = [_c()["up"] if v > 0 else _c()["down"] for v in rows["change"]]
        fig = go.Figure(go.Bar(
            x=rows["change"], y=rows["label"], orientation="h", marker_color=colors,
            text=[_signed(v) for v in rows["change"]], textposition="outside", cliponaxis=False,
            customdata=list(zip(rows["now"].map(_rupees), rows["before"].map(_rupees))),
            hovertemplate="<b>%{y}</b><br>Now %{customdata[0]}, before %{customdata[1]}<extra></extra>",
        ))
        _style(fig, height=max(220, 34 * len(rows) + 40))
        fig.update_traces(marker_cornerradius=4)
        lo, hi = rows["change"].min(), rows["change"].max()
        pad = (max(hi, 0) - min(lo, 0)) * 0.35  # room for the value text
        fig.update_xaxes(visible=False, range=[lo - pad if lo < 0 else 0, hi + pad if hi > 0 else 0])
        fig.update_yaxes(gridcolor="rgba(0,0,0,0)", tickprefix="")
        fig.add_vline(x=0, line=dict(color=_c()["axis"], width=1))
        _plotly(fig).classes("w-full")
        up, down = changes["change"].clip(lower=0).sum(), -changes["change"].clip(upper=0).sum()
        ui.label(f"▲ {_rupees(up)} more on {int((changes['change'] > 0).sum())} labels · "
                 f"▼ {_rupees(down)} less on {int((changes['change'] < 0).sum())}").classes("et-faint text-xs")


CALENDAR_MAX_DAYS = 371  # 53 weeks; longer periods show their latest year


def spending_calendar(df: pd.DataFrame, period: analytics.Period | None) -> None:
    today = date.today()
    end = min(period.end, today + timedelta(days=1)) if period else today + timedelta(days=1)
    first = period.start if period else (df["date"].min().date() if not df.empty else end)
    start = max(first, end - timedelta(days=CALENDAR_MAX_DAYS))
    note = " (latest year)" if start > first else ""
    with _chart_card("Spending calendar", f"Spend per day, by week{note}; darker is more"):
        if df.empty or start >= end:
            return _empty()
        days = analytics.daily_spend(df, start, end)
        days["week"] = days["date"] - pd.to_timedelta(days["date"].dt.weekday, unit="D")
        weeks = sorted(pd.Timestamp(w) for w in days["week"].unique())
        weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        z = [[None] * len(weeks) for _ in weekdays]
        text = [[""] * len(weeks) for _ in weekdays]
        col = {w: i for i, w in enumerate(weeks)}
        for r in days.itertuples():
            i, j = r.date.weekday(), col[r.week]
            z[i][j] = r.amount
            text[i][j] = (f"{r.date:%a %d %b %Y}<br>{_rupees(r.amount)} · {r.count} payment{'s' if r.count != 1 else ''}"
                          if r.count else f"{r.date:%a %d %b %Y}<br>No spend")
        # Cap the scale at the 95th percentile so one rent day doesn't wash out the rest.
        spent = days.loc[days["amount"] > 0, "amount"]
        cap = float(spent.quantile(0.95)) if len(spent) else 1.0
        fig = go.Figure(go.Heatmap(
            z=z, x=_iso(pd.DatetimeIndex(weeks)), y=weekdays, text=text, hovertemplate="%{text}<extra></extra>",
            colorscale=[[0, _c()["empty"]], [0.001, _c()["heat_lo"]], [1, _c()["accent"]]], zmin=0, zmax=max(cap, 1),
            xgap=3, ygap=3, showscale=False,
        ))
        _style(fig, height=230)
        fig.update_yaxes(autorange="reversed", gridcolor="rgba(0,0,0,0)", tickprefix="", tickformat="",
                         fixedrange=True)
        fig.update_xaxes(tickformat="%d %b", nticks=8, linecolor="rgba(0,0,0,0)", fixedrange=True)
        _plotly(fig).classes("w-full")
        by_day = days.assign(wd=days["date"].dt.weekday).groupby("wd")["amount"].mean()
        busiest = weekdays[int(by_day.idxmax())] if by_day.max() > 0 else None
        quiet = int((days["count"] == 0).sum())
        if busiest:
            ui.label(f"Most on {busiest}s on average ({_rupees(by_day.max())}) · "
                     f"{quiet} of {len(days)} days with no spend").classes("et-faint text-xs")


def largest_expenses(df: pd.DataFrame, n: int = 10) -> None:
    with _chart_card("Largest expenses", f"The {n} biggest single payments in this period"):
        if df.empty:
            return _empty()
        top = df.nlargest(n, "amount")
        total = df["amount"].sum()
        table = ui.table(
            columns=[
                {"name": "date", "label": "Date", "field": "date", "align": "left"},
                {"name": "merchant", "label": "Paid to", "field": "merchant", "align": "left"},
                {"name": "label", "label": "Label", "field": "label", "align": "left"},
                {"name": "account", "label": "Account", "field": "account", "align": "left"},
                {"name": "amount", "label": "Amount", "field": "amount", "align": "right"},
            ],
            rows=[{"date": r.date.strftime("%d %b %Y"), "merchant": r.merchant, "label": r.sublabel,
                   "color": r.color, "account": r.account, "amount": _rupees(r.amount)} for r in top.itertuples()],
        ).props("flat dense").classes("w-full")
        table.add_slot("body-cell-label", r'''
            <q-td :props="props">
              <span class="inline-block w-2 h-2 rounded-full mr-2" :style="{background: props.row.color}"></span>
              {{ props.value }}
            </q-td>''')
        table.add_slot("body-cell-merchant", r'''
            <q-td :props="props"><div class="truncate max-w-32">{{ props.value }}</div></q-td>''')
        if total > 0:
            ui.label(f"These {len(top)} make up {top['amount'].sum() / total * 100:.0f}% of the period's spend") \
                .classes("et-faint text-xs")


def month_pace(account_id: int | None) -> None:
    today = date.today()
    this = analytics.spend_frame(analytics.period_for("this_month"), account_id)
    last = analytics.spend_frame(analytics.period_for("last_month"), account_id)
    month_days = analytics.period_for("this_month").days
    spent = this["amount"].sum() if not this.empty else 0.0
    projected = spent / today.day * month_days
    subtitle = "Cumulative spend by day of month"
    if spent > 0 and today.day < month_days:
        subtitle = f"Cumulative spend by day; at this pace about {_rupees(projected)} by month end"
    with _chart_card("This month vs last month", subtitle):
        if this.empty and last.empty:
            return _empty("No spend this month or last month.")
        fig = go.Figure()
        for df, name, color, dash, upto in ((last, "Last month", _c()["baseline"], "dot", 31),
                                            (this, "This month", _c()["accent"], "solid", today.day)):
            if df.empty:
                continue
            daily = df.groupby(df["date"].dt.day)["amount"].sum().reindex(range(1, upto + 1), fill_value=0)
            daily = daily.loc[: (df["date"].dt.day.max() if name == "Last month" else upto)]
            cum = daily.cumsum()
            is_this = name == "This month"
            fig.add_scatter(x=cum.index, y=cum.values, name=name, mode="lines",
                            line=dict(color=color, width=2.5 if is_this else 2, dash=dash, shape="hv"),
                            fill="tozeroy" if is_this else None, fillcolor=_c()["fill"],
                            hovertemplate=f"{name}, day %{{x}}: ₹%{{y:,.0f}}<extra></extra>")
            fig.add_annotation(x=cum.index[-1], y=cum.values[-1], text=f"{name} {_rupees(cum.values[-1])}",
                               showarrow=False, xanchor="left", xshift=6, font=dict(size=11, color=_c()["muted"]))
        if spent > 0 and today.day < month_days:  # straight-line projection from today to month end
            fig.add_scatter(x=[today.day, month_days], y=[spent, projected], name="At this pace", mode="lines",
                            line=dict(color=_c()["accent"], width=1.5, dash="dash"),
                            hovertemplate="At this pace, day %{x}: ₹%{y:,.0f}<extra></extra>")
        _style(fig, legend=True)
        fig.update_layout(hovermode="x unified", margin=dict(r=110))
        fig.update_xaxes(range=[1, 31], dtick=5, title_text="Day of month")
        _plotly(fig).classes("w-full")


def top_merchants(df: pd.DataFrame, n: int = 10) -> None:
    with _chart_card("Top merchants", f"Biggest {n} by spend"):
        if df.empty:
            return _empty()
        top = (df.groupby("merchant").agg(amount=("amount", "sum"), count=("amount", "size"))
                 .sort_values("amount", ascending=False).head(n).iloc[::-1])
        fig = go.Figure(go.Bar(
            x=top["amount"], y=top.index, orientation="h", marker_color=_c()["bar"],
            customdata=top["count"], text=top["amount"].map(_rupees), textposition="outside", cliponaxis=False,
            hovertemplate="<b>%{y}</b><br>₹%{x:,.0f} over %{customdata} payments<extra></extra>",
        ))
        _style(fig, height=max(220, 30 * len(top) + 40))
        fig.update_traces(marker_cornerradius=4)
        fig.update_xaxes(visible=False, range=[0, top["amount"].max() * 1.3])
        fig.update_yaxes(gridcolor="rgba(0,0,0,0)", tickprefix="")
        _plotly(fig).classes("w-full")


def by_account(df: pd.DataFrame) -> None:
    with _chart_card("By account", "Which card or bank account the spend went through"):
        if df.empty:
            return _empty()
        totals = df.groupby("account")["amount"].sum().sort_values()
        fig = go.Figure(go.Bar(
            x=totals.values, y=totals.index, orientation="h", marker_color=_c()["bar"],
            text=[_rupees(v) for v in totals.values], textposition="outside", cliponaxis=False,
            hovertemplate="<b>%{y}</b><br>₹%{x:,.0f}<extra></extra>",
        ))
        _style(fig, height=max(160, 40 * len(totals) + 40))
        fig.update_traces(marker_cornerradius=4)
        fig.update_xaxes(visible=False, range=[0, totals.max() * 1.3])
        fig.update_yaxes(gridcolor="rgba(0,0,0,0)", tickprefix="")
        _plotly(fig).classes("w-full")


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
        months = {ms.month: date.fromisoformat(ms.month + "-01").strftime("%B %Y")
                  for ms in ledger.month_summaries()}  # newest first

        def pick(key: str | None, other) -> None:
            if not key:  # cleared because the other control was used
                return
            state["period"] = key
            other.value = None
            content.refresh()

        with ui.row().classes("w-full items-center gap-4"):
            presets = ui.toggle(PERIOD_SHORT, value=state["period"], on_change=lambda e: pick(e.value, month_pick)) \
                .props("unelevated no-caps toggle-color=primary color=transparent text-color=grey-8 "
                       "padding='6px 14px'").classes("et-segment")
            month_pick = ui.select(months, value=None, label="Month", on_change=lambda e: pick(e.value, presets)) \
                .props("outlined dense options-dense").classes("w-52")
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
            income = analytics.income_frame(period, state["account"])
            earned = income["amount"].sum() if not income.empty else 0
            # One-month periods: running total by day; longer: one point per month.
            daily = period is not None and period.days <= 31
            trend = analytics.daily_totals(df, period) if daily else analytics.monthly_totals(df, period)
            labelled = df[df["label"] != analytics.UNLABELLED]
            top = (labelled.groupby("label")["amount"].sum().sort_values(ascending=False) if not labelled.empty
                   else pd.Series(dtype=float))
            pending = cards.statement_statuses(only_pending=True)
            balances = analytics.daily_balances(analytics.balance_history(state["account"]))
            now_bal = analytics.balance_on(balances, date.today())
            start_bal = analytics.balance_on(balances, period.start - timedelta(days=1)) if period else None
            if now_bal is not None and start_bal is None and not balances.empty:
                start_bal = float(balances.iloc[0].sum())  # period starts before the first statement
            unlabelled = int((df["label"] == analytics.UNLABELLED).sum()) if not df.empty else 0

            with ui.row().classes("w-full gap-4 items-stretch"):
                _hero_kpi(f"Spent · {_period_name(state['period'], months)}", total, change, prev, trend, daily,
                          len(df), total / _days_elapsed(df, period), total / _months_in(df, period))
                kept = earned - total
                with ui.element("div").classes("grid grid-cols-2 2xl:grid-cols-3 gap-4 flex-[2] min-w-[30rem]"):
                    in_salary = income["merchant"] == analytics.PAID_IN_SALARY
                    credits = int((~in_salary).sum())
                    _kpi("Income", _rupees(earned), "south_west",
                         f"{credits} credit{'s' if credits != 1 else ''} marked income"
                         + (f", less {_rupees(-income.loc[in_salary, 'amount'].sum())} reimbursed"
                            if in_salary.any() else ""))
                    _kpi("Kept", _rupees(kept), "savings",
                         f"{kept / earned * 100:.0f}% of income" if earned else "no income in this period",
                         "et-warn" if kept < 0 else "et-faint")
                    if now_bal is None:
                        _kpi("Bank balance", "—", "account_balance", "no statement with a balance column")
                    else:
                        moved = now_bal - start_bal
                        _kpi("Bank balance", _rupees(now_bal), "account_balance",
                             f"{'▲' if moved >= 0 else '▼'} {_rupees(abs(moved))} from {_rupees(start_bal)}",
                             "et-warn" if moved < 0 else "et-faint")
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
                cash_flow(df, income, period)
                balance_chart(balances, period)
            with ui.element("div").classes("grid grid-cols-1 xl:grid-cols-2 gap-4 w-full"):
                what_changed(df, period, state["account"])
                spending_calendar(df, period)
            with ui.element("div").classes("grid grid-cols-1 xl:grid-cols-2 gap-4 w-full"):
                month_pace(state["account"])
                top_merchants(df)
                largest_expenses(df)
                by_account(df)
            recurring()

        content()
