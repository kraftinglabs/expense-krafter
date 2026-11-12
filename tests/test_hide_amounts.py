import plotly.graph_objects as go
import pytest

from expensekrafter.services import money
from expensekrafter.ui import dashboard  # imports layout, which installs the app's own check


@pytest.fixture
def hide():
    money.set_hidden_check(lambda: True)
    yield
    money.set_hidden_check(lambda: False)


def test_amounts_show_normally():
    assert money.fmt(-123456) == "-₹1,234.56"


def test_hidden_amounts_become_asterisks(hide):
    assert money.MASK == "∗" * 4  # centred asterisks, level with ₹
    assert money.fmt(-123456) == money.fmt(5) == "₹" + money.MASK
    assert money.fmt(None) == ""


def test_a_failing_check_shows_amounts():
    def no_ui() -> bool:
        raise RuntimeError("no request context")

    money.set_hidden_check(no_ui)
    try:
        assert money.fmt(100) == "₹1.00"
    finally:
        money.set_hidden_check(lambda: False)


def test_charts_lose_money_axes_and_tooltips(hide):
    fig = go.Figure(go.Bar(x=["Food"], y=[1234], hovertemplate="<b>%{x}</b><br>₹%{y:,.0f} · 3 payments<extra></extra>"))
    fig.add_pie(labels=["Food"], values=[1234], hovertemplate="%{label} ₹%{value:,.0f}")
    fig.update_yaxes(tickprefix="₹")
    dashboard._mask(fig)
    assert fig.data[0].hovertemplate == f"<b>%{{x}}</b><br>₹{money.MASK} · 3 payments<extra></extra>"
    assert "value" not in fig.data[1].hovertemplate
    assert fig.layout.yaxis.showticklabels is False and fig.layout.xaxis.showticklabels is not False
    assert dashboard._rupees(-5000) == dashboard._signed(5000) == "₹" + money.MASK
