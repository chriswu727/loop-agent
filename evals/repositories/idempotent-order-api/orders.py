import uuid


def create_order(state, payload, idempotency_key):
    return {"status": 501, "body": {"error": "not implemented"}}


def _new_order(payload):
    return {"id": str(uuid.uuid4()), "sku": payload["sku"], "quantity": payload["quantity"]}
