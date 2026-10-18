"""Fictional store data for the refund agent.

Everything here is synthetic and invented for demonstration: the store,
the customers, the orders and the prices. No real people, companies or
transactions are represented.

The fixed "today" keeps every run deterministic: the same input always
produces the same trace, which is what makes offline evaluation possible.
"""

# Fixed evaluation date (ISO). The agent treats this as "today".
TODAY = "2026-10-18"

# Refund policy: purchases older than this many days are ineligible.
REFUND_WINDOW_DAYS = 30

# Fictional storefront. Not a real business.
STORE_NAME = "Northstar Outfitters (fictional demo store)"

# Fictional customers with purchase histories.
# Dates are ISO strings; prices are USD floats.
USERS = {
    "u-1001": {
        "display_name": "Amara O.",
        "orders": [
            {
                "order_id": "ord-9001",
                "items": [
                    {"name": "trail backpack", "price": 45.00,
                     "purchased": "2026-10-02"},
                    {"name": "insulated bottle", "price": 28.00,
                     "purchased": "2026-10-02"},
                ],
            },
        ],
    },
    "u-1002": {
        "display_name": "Ravi K.",
        "orders": [
            {
                "order_id": "ord-9002",
                "items": [
                    {"name": "camp lantern", "price": 32.50,
                     "purchased": "2026-08-10"},  # outside the 30-day window
                    {"name": "trail socks", "price": 15.00,
                     "purchased": "2026-10-10"},  # within the window
                ],
            },
        ],
    },
    "u-1003": {
        "display_name": "Lena M.",
        "orders": [
            {
                "order_id": "ord-9003",
                "items": [
                    {"name": "hiking socks", "price": 12.00,
                     "purchased": "2026-10-15"},
                ],
            },
        ],
    },
}


def get_user(username):
    """Return the user record dict, or None when unknown."""
    return USERS.get(username)


def find_item(user_record, item_name):
    """Find a purchased item by (case-insensitive) name.

    Returns (order_id, item_dict) or (None, None).
    """
    wanted = item_name.strip().lower()
    for order in user_record.get("orders", []):
        for item in order.get("items", []):
            if item["name"].lower() == wanted:
                return order["order_id"], item
    return None, None


def days_between(earlier_iso, later_iso):
    """Whole days from earlier_iso to later_iso (both YYYY-MM-DD)."""
    from datetime import date
    earlier = date.fromisoformat(earlier_iso)
    later = date.fromisoformat(later_iso)
    return (later - earlier).days
