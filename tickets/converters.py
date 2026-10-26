class TicketNumberConverter:
    regex = r"[0-9]{6,}"

    def to_python(self, value: str) -> int:
        return int(value)

    def to_url(self, value: int) -> str:
        return f"{int(value):06d}"
