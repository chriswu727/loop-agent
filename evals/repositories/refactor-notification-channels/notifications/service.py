from notifications import email, sms


def send_notification(channel, recipient, message):
    if channel == "email":
        return email.send(recipient, message)
    if channel == "sms":
        return sms.send(recipient, message)
    raise ValueError(f"unknown notification channel: {channel}")
