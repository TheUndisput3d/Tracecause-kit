"""Streaming structural limit before a JSON parser allocates nested containers."""

from tracecause.api.errors import ApiError


class JsonDepthLimit:
    def __init__(self, maximum: int = 64) -> None:
        self.maximum = maximum
        self.depth = 0
        self.quoted = False
        self.escaped = False

    def feed(self, data: bytes) -> None:
        for value in data:
            if self.quoted:
                if self.escaped:
                    self.escaped = False
                elif value == 92:
                    self.escaped = True
                elif value == 34:
                    self.quoted = False
            elif value == 34:
                self.quoted = True
            elif value in (91, 123):
                self.depth += 1
                if self.depth > self.maximum:
                    raise ApiError(413, "json_too_deep", "The JSON nesting limit was exceeded.")
            elif value in (93, 125):
                self.depth -= 1
                # Syntax errors remain the responsibility of the JSON parser.
