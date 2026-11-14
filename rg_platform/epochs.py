"""Receive-order connection epochs, independent of runner consumption time."""
class ConnectionEpochs:
    def __init__(self):
        self.attempt_id = 1
        self.disconnected = False

    def observe(self, raw):
        if "CTRL-EVENT-DISCONNECTED" in raw:
            self.disconnected = True
        elif "CTRL-EVENT-CONNECTED" in raw and self.disconnected:
            self.attempt_id += 1
            self.disconnected = False
        return self.attempt_id
