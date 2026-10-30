"""Live currency rates (open.er-api.com, keyless; refreshed daily by the provider)."""

from __future__ import annotations

from .http import ProviderError, get_json, ttl_cache


@ttl_cache(seconds=3600)
async def rates(base: str) -> dict:
    base = base.upper()
    data = await get_json(f"https://open.er-api.com/v6/latest/{base}")
    if not data or data.get("result") != "success":
        raise ProviderError(f"No exchange rates available for {base}")
    return {"base": base, "rates": data["rates"], "updated": data.get("time_last_update_utc")}


async def convert(amount: float, from_currency: str, to_currency: str) -> dict:
    src, dst = from_currency.upper(), to_currency.upper()
    if src == dst:
        return {"amount": amount, "from": src, "to": dst, "rate": 1.0, "converted": round(amount, 2)}
    table = await rates(src)
    if dst not in table["rates"]:
        raise ProviderError(f"Currency {dst} is not supported")
    rate = float(table["rates"][dst])
    return {
        "amount": amount,
        "from": src,
        "to": dst,
        "rate": rate,
        "converted": round(amount * rate, 2),
        "rates_updated": table["updated"],
        "source": "open.er-api.com",
    }
