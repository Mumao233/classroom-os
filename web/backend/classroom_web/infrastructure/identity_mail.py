"""Synthetic capture only; no SMTP/provider/configuration fallback."""


class CapturingMailAdapter:
    def __init__(self):
        self.messages: list[tuple[str, str]] = []

    def send_verification(self, email: str, token: str) -> None:
        self.messages.append((email, token))
