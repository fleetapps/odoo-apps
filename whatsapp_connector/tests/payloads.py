"""Webhook payloads shaped like Meta's documented examples (OpenAPI v23.0 and the
business-scoped user ID page)."""

WABA_ID = "102290129340398"
PHONE_NUMBER_ID = "106540352242922"
DISPLAY_NUMBER = "15550783881"
BSUID = "US.13491208655302741918"
WA_ID = "16505551234"


def envelope(value, field="messages", waba_id=WABA_ID):
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": waba_id, "changes": [{"value": value, "field": field}]}],
    }


def metadata(phone_number_id=PHONE_NUMBER_ID):
    return {"display_phone_number": DISPLAY_NUMBER, "phone_number_id": phone_number_id}


def contact(name="Sheena Nelson", wa_id=WA_ID, bsuid=BSUID, username=None):
    profile = {"name": name}
    if username:
        profile["username"] = username
    result = {"profile": profile}
    if wa_id:
        result["wa_id"] = wa_id
    if bsuid:
        result["user_id"] = bsuid
    return result


def text_message(msg_id, body, timestamp="1749416383", wa_id=WA_ID, bsuid=BSUID, **extra):
    message = {"id": msg_id, "timestamp": timestamp, "type": "text", "text": {"body": body}}
    if wa_id:
        message["from"] = wa_id
    if bsuid:
        message["from_user_id"] = bsuid
    message.update(extra)
    return message


def inbound(messages, contacts=None, phone_number_id=PHONE_NUMBER_ID):
    return envelope({
        "messaging_product": "whatsapp",
        "metadata": metadata(phone_number_id),
        "contacts": contacts if contacts is not None else [contact()],
        "messages": messages,
    })


def status(msg_id, value, errors=None, recipient_user_id=None, contacts=None):
    item = {"id": msg_id, "status": value, "timestamp": "1750000000", "recipient_id": WA_ID}
    if errors:
        item["errors"] = errors
    if recipient_user_id:
        item["recipient_user_id"] = recipient_user_id
    result = {
        "messaging_product": "whatsapp",
        "metadata": metadata(),
        "statuses": [item],
    }
    if contacts:
        result["contacts"] = contacts
    return envelope(result)
