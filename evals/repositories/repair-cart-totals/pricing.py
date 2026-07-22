def calculate_total(lines):
    total = 0.0
    for line in lines:
        quantity = line["quantity"]
        if quantity < 0:
            raise ValueError("quantity cannot be negative")
        subtotal = float(line["price"])
        discount = float(line.get("discount_pct", 0)) / 100
        total += subtotal - discount
    return round(total, 2)
