"""Runner 的依赖装配点；算法不依赖具体 Lab/Observer。"""
from pathlib import Path
import uuid
from model.contracts import Context, Event, BackendError
from .mock import MockBackend


class MockSession:
    simulated = True

    def __init__(self, output_root, scenario):
        self.context = Context(uuid.uuid4().hex)
        self.out = Path(output_root) / self.context.run_id
        self.out.mkdir(parents=True, exist_ok=False)
        self.observer = self.executor = MockBackend(self.context, scenario)
        self.backend = self.observer

    def start(self):
        self.backend.start()

    def record_request(self, kind):
        return self.backend.emit(kind, source="runner")

    def close(self):
        self.backend.close()
        return {"errors": []}

    def metadata(self):
        return {"run_id": self.context.run_id, "simulated": True, "clock": "virtual"}

    def events(self):
        return self.backend.events


class RealSession:
    simulated = False

    def __init__(self, output_root, client="stock", lab_factory=None):
        if lab_factory is None:
            import os
            import sys
            if sys.platform != "linux" or os.geteuid() != 0:
                raise BackendError("environment_error", "真实模式需要在 Linux 中使用 sudo")
            from rg_platform.lab import Lab
            lab_factory = Lab
        from .platform_observer import PlatformObserver
        from .platform_executor import PlatformExecutor
        self.lab = lab_factory(client, output_root=output_root)
        self.context = Context(self.lab.run_id)
        self.out = self.lab.out
        self.observer = PlatformObserver(self.lab)
        self.executor = PlatformExecutor(self.lab)
        self.requests = []

    def start(self):
        # Lab 唯一读取控制事件；Observer 随后从 journal 的 seq=0 读取首次连接。
        self.lab.start()
        self.observer.start()

    def record_request(self, kind):
        raw = self.lab.record_event("runner", kind, self.context.attempt_id)
        event = Event(f"{raw.run_id}:raw:{raw.seq}:{kind}", raw.run_id,
                      self.context.attempt_id, "runner", kind, raw.seq, raw.monotonic_s,
                      {"evidence_ref": raw.evidence_ref})
        self.requests.append(event)
        return event

    def close(self):
        try:
            self.observer.close()
        finally:
            return_value = self.lab.stop()
        return return_value

    def metadata(self):
        return dict(self.lab.meta)

    def events(self):
        return sorted(self.requests + self.observer.events, key=lambda event: event.seq)
