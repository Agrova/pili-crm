from fastapi import FastAPI

from app.api.routes import customers, orders, products, shipment

app = FastAPI(title="ПилиСтрогай CRM", version="0.1.0")

app.include_router(products.router)
app.include_router(customers.router)
app.include_router(orders.router)
app.include_router(shipment.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
