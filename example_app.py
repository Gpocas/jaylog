"""
Fake order-processing app that runs in a loop to demo jaylog.
Press Ctrl+C to stop gracefully.
"""

import random
import time

from jaylog import JaylogSettings, get_logger, shutdown

logger = get_logger(JaylogSettings())

PRODUCTS = ["Hamburguer", "Frango", "Carne Bovina", "Suíno", "Salmão"]
CUSTOMERS = ["JBS USA", "Seara BR", "Swift AU", "Pilgrim's EU", "Friboi MX"]
STATUSES = ["RECEIVED", "PROCESSING", "SHIPPED", "DELIVERED"]


def fetch_orders() -> list[dict]:
    n = random.randint(1, 4)
    return [
        {
            "order_id": f"ORD-{random.randint(10000, 99999)}",
            "product": random.choice(PRODUCTS),
            "customer": random.choice(CUSTOMERS),
            "qty_tons": round(random.uniform(0.5, 50.0), 2),
        }
        for _ in range(n)
    ]


def process_order(order: dict) -> str:
    time.sleep(random.uniform(0.05, 0.2))
    if random.random() < 0.1:
        raise ValueError(f"Validation failed for order {order['order_id']}")
    return random.choice(STATUSES)


def run():
    logger.info("Order processor started")
    cycle = 0
    try:
        while True:
            time.sleep(5)
            cycle += 1
            logger.debug("Starting cycle %d", cycle)

            orders = fetch_orders()
            logger.info("Fetched %d orders in cycle %d", len(orders), cycle)

            for order in orders:
                try:
                    status = process_order(order)
                    logger.info(
                        "Order %s | %s | %.1ft | customer=%s | status=%s",
                        order["order_id"],
                        order["product"],
                        order["qty_tons"],
                        order["customer"],
                        status,
                    )

                    if status == "DELIVERED":
                        logger.warning(
                            "High-value delivery completed: %s → %s",
                            order["order_id"],
                            order["customer"],
                        )

                except ValueError as exc:
                    logger.exception("Order processing error: %s", exc)

            time.sleep(2)

    except KeyboardInterrupt:
        logger.info("Shutdown signal received — stopping after cycle %d", cycle)
    finally:
        shutdown()
        print("\nLogger shut down cleanly.")


if __name__ == "__main__":
    run()
