"""Direct invocation smoke test for the 4 MCP tools (bypasses MCP protocol)."""

from __future__ import annotations

import asyncio
import json

from db import dispose, get_session, setup_logging
from tools import (
    add_to_stock,
    list_customers,
    match_shipment,
    pending_orders,
    search_products,
    update_order_item_status,
)


def _banner(title: str) -> None:
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


async def main() -> None:
    setup_logging()
    async with get_session() as s:
        _banner("match_shipment(['Veritas Shooting Board','Wonder Dog','Shapton','Higo No Kami'])")
        r = await match_shipment.run(
            s,
            items=[
                "Veritas Shooting Board",
                "Wonder Dog",
                "Shapton",
                "Higo No Kami",
            ],
        )
        print(match_shipment.format_text(r))
        print("\n--- raw JSON ---")
        print(json.dumps(r, ensure_ascii=False, indent=2, default=str))

        _banner("pending_orders()")
        r = await pending_orders.run(s)
        print(pending_orders.format_text(r))

        _banner("list_customers(search='Хаустов')")
        r = await list_customers.run(s, search="Хаустов")
        print(list_customers.format_text(r))

        _banner("list_customers() — всего")
        r = await list_customers.run(s)
        print(f"(всего клиентов: {len(r['customers'])})")

        _banner("search_products(query='veritas')")
        r = await search_products.run(s, query="veritas")
        print(search_products.format_text(r))

        _banner("add_to_stock('Higo No Kami') — нет в каталоге")
        r = await add_to_stock.run(s, product_name="Higo No Kami")
        print(add_to_stock.format_text(r))
        print(f"   status={r['status']}")

        _banner("add_to_stock('стамеска') — ambiguous")
        r = await add_to_stock.run(s, product_name="стамеска")
        print(add_to_stock.format_text(r))
        print(f"   status={r['status']}")

        _banner("add_to_stock('Veritas Shooting Board', quantity=2) — первый раз")
        r1 = await add_to_stock.run(
            s, product_name="Veritas Shooting Board", quantity=2
        )
        print(add_to_stock.format_text(r1))
        assert r1["status"] == "ok"

        _banner("add_to_stock('Veritas Shooting Board', quantity=3) — повтор, инкремент")
        r2 = await add_to_stock.run(
            s, product_name="Veritas Shooting Board", quantity=3
        )
        print(add_to_stock.format_text(r2))
        assert r2["status"] == "ok"
        assert r2["stock_item_id"] == r1["stock_item_id"], "должен быть тот же stock row"
        assert r2["new_total_quantity"] == r1["new_total_quantity"] + 3, (
            f"ожидалось {r1['new_total_quantity'] + 3}, "
            f"получено {r2['new_total_quantity']}"
        )
        print(
            f"   OK: stock_item_id={r2['stock_item_id']} "
            f"quantity {r1['new_total_quantity']} → {r2['new_total_quantity']}"
        )

        _banner("match_shipment(['Higo No Kami']) — unmatched содержит suggested_action")
        r = await match_shipment.run(s, items=["Higo No Kami"])
        print(match_shipment.format_text(r))
        print("\n--- JSON unmatched ---")
        print(json.dumps(r["unmatched"], ensure_ascii=False, indent=2))
        assert r["unmatched"][0]["suggested_action"]["action"] == "add_to_stock"
        assert r["unmatched"][0]["suggested_action"]["product_name"] == "Higo No Kami"

        # ── update_order_item_status ─────────────────────────────────────────

        _banner("update_order_item_status — товар не найден")
        r = await update_order_item_status.run(
            s, product_name="абсолютно несуществующий товар xyz", new_status="arrived"
        )
        print(update_order_item_status.format_text(r))
        assert r["status"] == "not_found"

        _banner("update_order_item_status — нераспознанный статус")
        r = await update_order_item_status.run(
            s, product_name="Veritas Shooting Board", new_status="непонятный_статус"
        )
        print(update_order_item_status.format_text(r))
        assert r["status"] == "error"

        _banner("update_order_item_status — русский статус «заказан» → ordered")
        r = await update_order_item_status.run(
            s, product_name="Veritas Shooting Board", new_status="заказан"
        )
        print(update_order_item_status.format_text(r))
        print(f"   status={r['status']}, new_item_status={r.get('new_item_status')}")
        # May be ok or ambiguous depending on DB state; new_item_status must be 'ordered' if ok
        if r["status"] == "ok":
            assert r["new_item_status"] == "ordered", (
                f"Expected 'ordered', got {r['new_item_status']}"
            )

        _banner("update_order_item_status('стамеска') — ambiguous или not_found")
        r = await update_order_item_status.run(
            s, product_name="стамеска", new_status="ordered"
        )
        print(update_order_item_status.format_text(r))
        assert r["status"] in ("ambiguous", "not_found", "ok")
        print(f"   status={r['status']}")

    await dispose()


if __name__ == "__main__":
    asyncio.run(main())
