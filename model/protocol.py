"""少量有证据的协议阶段；不是 SAE 内部密码学状态。"""
from enum import Enum


class Phase(str,Enum):
    DISCONNECTED='DISCONNECTED'
    AUTHENTICATING='AUTHENTICATING'
    ASSOCIATING='ASSOCIATING'
    KEY_ESTABLISHING='KEY_ESTABLISHING'


class ProtocolMonitor:
    def __init__(self):self.phase=Phase.DISCONNECTED;self.history=[]
    def observe(self,seq,subtype=None,eapol=False):
        phase=Phase.KEY_ESTABLISHING if eapol else (Phase.AUTHENTICATING if subtype==11 else Phase.ASSOCIATING if subtype==0 else None)
        if phase:
            self.phase=phase;self.history.append({'seq':seq,'phase':phase.value})
        return self.phase
    def allows(self,action):
        return self.phase==({'reject_deauth':Phase.ASSOCIATING,'reject_assoc':Phase.ASSOCIATING,'drop_assoc':Phase.ASSOCIATING,'drop_eapol':Phase.KEY_ESTABLISHING}.get(action))


def owned_request(frame, ap, sta):
    if len(frame)<24:
        return False
    mac=lambda data:':'.join(f'{x:02x}' for x in data)
    return mac(frame[4:10])==ap.lower() and mac(frame[10:16])==sta.lower()
