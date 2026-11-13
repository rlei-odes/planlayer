"""Cost forwarding (ADR 0018): what a forwarding passes on per month, how it splits over the
receivers, and the order forwardings run in. Pure: no I/O.

Amounts are in cents exactly: a month's amount splits over the receivers by the largest
remainder, so the source's credit is the sum of the receivers' charges.
"""

from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from planlayer.domain.spreading import equal, spread

Method = Literal["full", "fixed", "rate"]
Shares = Literal["percent", "portion"]

MONTHS = 12
CENT = Decimal("0.01")
HUNDRED = Decimal(100)


class ForwardingError(ValueError):
    """A forwarding that can't be computed as defined."""


@dataclass(frozen=True)
class Forwarding[K: Hashable]:
    """A forwarding with its receivers' numbers, keyed by receiver (e.g. a cost center id)."""

    method: Method
    source: K
    quantities: Mapping[K, Decimal]  # shares (percent or portion) or quantities of the unit
    shares: Shares | None = None  # full and fixed
    amount: Decimal | None = None  # fixed: the year's amount
    rate: Decimal | None = None  # rate: per unit and year


def check[K: Hashable](f: Forwarding[K]) -> None:
    """Refuse a forwarding whose numbers don't fit its method."""
    if f.source in f.quantities:
        raise ForwardingError("the source can't receive its own forwarding")
    if any(q < 0 for q in f.quantities.values()):
        raise ForwardingError("shares and quantities can't be negative")
    match f.method:
        case "full" | "fixed":
            if f.shares is None:
                raise ForwardingError("say whether shares are percentages or portions")
            if f.shares == "percent" and sum(f.quantities.values(), Decimal(0)) > HUNDRED:
                raise ForwardingError("percentages add up to more than 100")
            if f.method == "fixed" and (f.amount is None or f.amount < 0):
                raise ForwardingError("a fixed forwarding needs an amount of at least 0")
        case "rate":
            if f.rate is None or f.rate < 0:
                raise ForwardingError("a rate forwarding needs a rate of at least 0")


SHARED: frozenset[Method] = frozenset({"full", "fixed"})


def check_round[F: Hashable, K: Hashable](
    forwardings: Mapping[F, tuple[Method, K, set[K]]],
) -> None:
    """A cost center that receives by shares (all costs or a fixed amount) passes nothing on:
    it is the source of no forwarding. So no forwarding by shares can come back to its source,
    and none needs another's result. Rate forwardings may charge a source."""
    sources = {source: f for f, (_, source, _) in forwardings.items()}
    for f, (method, _, receivers) in forwardings.items():
        if method not in SHARED:
            continue
        for cc in sorted(receivers, key=str):
            if cc in sources:
                raise ForwardingError(
                    f"{cc} receives by shares from {f}, so it can't pass costs on ({sources[cc]})"
                )


def year_months(amount: Decimal) -> list[Decimal]:
    """A year's amount in twelve months, a twelfth each, the cents kept exact."""
    return spread(amount.quantize(CENT, ROUND_HALF_UP), equal(MONTHS))


def charges[K: Hashable](
    f: Forwarding[K], base: Sequence[Decimal | None] | None = None
) -> dict[K, list[Decimal]]:
    """Each receiver's charge per month. ``base`` is the source's planned costs per month, for a
    full forwarding (None: a blank month, nothing to pass on). Receivers with nothing to receive
    are left out; the source's credit per month is the sum over receivers."""
    receivers = [k for k, q in f.quantities.items() if q > 0]
    if not receivers:
        return {}
    weights = [f.quantities[k] for k in receivers]
    out: dict[K, list[Decimal]] = {k: [Decimal(0)] * MONTHS for k in receivers}
    match f.method:
        case "rate":
            assert f.rate is not None
            for k in receivers:
                out[k] = year_months(f.quantities[k] * f.rate)
            return out
        case "fixed":
            assert f.amount is not None
            months: list[Decimal | None] = list(year_months(f.amount))
        case "full":
            if base is None or len(base) != MONTHS:
                raise ForwardingError("a full forwarding needs the source's twelve months")
            months = list(base)
    passed = _passed(f, sum(weights, Decimal(0)))
    for m, amount in enumerate(months):
        if amount is None or amount == 0:
            continue
        for k, part in zip(
            receivers, spread((amount * passed).quantize(CENT, ROUND_HALF_UP), weights), strict=True
        ):
            out[k][m] = part
    return out


def _passed[K: Hashable](f: Forwarding[K], total: Decimal) -> Decimal:
    """The part of the amount passed on: all of it for portions, the percentages' sum else."""
    return total / HUNDRED if f.shares == "percent" else Decimal(1)


def order[F: Hashable, K: Hashable](
    sources: Mapping[F, K], full: set[F], receivers: Mapping[F, set[K]]
) -> list[F]:
    """Forwardings in the order they run: a full forwarding passes on what is left on its
    source, so it runs after every forwarding that charges its source or credits it. Refused if
    full forwardings depend on each other in a circle (two full forwardings of one source
    included: one forwarding with shares does that)."""
    after: dict[F, set[F]] = {
        f: {
            g
            for g in sources
            if g != f and (sources[f] in receivers[g] or sources[g] == sources[f])
        }
        if f in full
        else set()
        for f in sources
    }
    done: list[F] = []
    state: dict[F, Literal["visiting", "done"]] = {}

    def visit(f: F, path: list[F]) -> None:
        if state.get(f) == "done":
            return
        if state.get(f) == "visiting":
            raise CycleError([*path[path.index(f) :], f])
        state[f] = "visiting"
        for g in sorted(after[f], key=str):
            visit(g, [*path, f])
        state[f] = "done"
        done.append(f)

    for f in sorted(sources, key=str):
        visit(f, [])
    return done


class CycleError(ForwardingError):
    def __init__(self, circle: Sequence[object]) -> None:
        self.circle = list(circle)
        super().__init__(
            "forwardings depend on each other in a circle: " + " → ".join(map(str, circle))
        )


def downstream[F: Hashable, K: Hashable](
    sources: Mapping[F, K], full: set[F], receivers: Mapping[F, set[K]], changed: K
) -> set[F]:
    """The forwardings to recompute when ``changed``'s plan values change: the full forwardings
    it feeds, and every full forwarding fed by their receivers, and so on."""
    out: set[F] = set()
    todo = [changed]
    seen: set[K] = set()
    while todo:
        cc = todo.pop()
        if cc in seen:
            continue
        seen.add(cc)
        for f in full:
            if sources[f] == cc and f not in out:
                out.add(f)
                todo.extend(receivers[f])
    return out
