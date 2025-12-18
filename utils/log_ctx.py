import logging

class CtxLogger(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        prefix = self.extra.get("prefix", "")
        return f"{prefix} {msg}", kwargs
