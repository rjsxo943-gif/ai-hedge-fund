"""The fork's spending boundary. Paid adapters remain readable but cannot run."""


class PaidServiceDisabled(RuntimeError):
    """A call would leave the subscription + free-data execution path."""


def refuse_paid_service(service: str) -> None:
    raise PaidServiceDisabled(
        f"{service} is disabled by this fork's no-extra-cost policy. "
        "Use `python -m hedge_fund.no_cost`. No paid fallback was attempted."
    )
