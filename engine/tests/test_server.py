import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

import synth
from forge3d.geometry.mesh_io import Part, save_obj

ENGINE = Path(__file__).resolve().parents[1]


class Client:
    def __init__(self, tmp_path):
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "forge3d", "servidor", "--logs", str(tmp_path / "logs")],
            cwd=ENGINE, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", bufsize=1)
        self.next_id = 1

    def send(self, method, params=None, mid=True):
        msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if mid:
            msg["id"] = self.next_id
            self.next_id += 1
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        return msg.get("id")

    def read(self, timeout=60):
        start = time.time()
        while time.time() - start < timeout:
            line = self.proc.stdout.readline()
            if line:
                return json.loads(line)
        raise TimeoutError

    def call(self, method, params=None, timeout=120):
        mid = self.send(method, params)
        progress = []
        while True:
            msg = self.read(timeout)
            if msg.get("method") == "progress":
                progress.append(msg["params"])
                continue
            assert msg["id"] == mid
            return msg, progress

    def close(self):
        try:
            self.call("shutdown")
        finally:
            self.proc.wait(timeout=10)


def test_protocol_roundtrip(tmp_path):
    parts = [Part(f"p{i}", np.asarray(m.vertices), np.asarray(m.faces)) for i, m in enumerate(synth.table())]
    obj = tmp_path / "mesa.obj"
    save_obj(obj, parts)
    c = Client(tmp_path)
    try:
        msg, _ = c.call("ping")
        assert msg["result"]["protocol"] == 1
        out = tmp_path / "saida" / "mesa.json"
        msg, progress = c.call("convert_mesh", {"path": str(obj), "level": "C", "output": str(out)})
        assert "result" in msg, msg
        assert msg["result"]["summary"]["achievedLevel"] == "C"
        assert progress and progress[-1]["fraction"] == 1.0
        spec = json.loads(out.read_text(encoding="utf-8"))
        assert spec["schema"] == "forge3d.familyspec"
        msg, _ = c.call("validate_spec", {"path": str(out)})
        assert msg["result"]["valid"] and msg["result"]["flex"]["ok"]
        msg, _ = c.call("convert_mesh", {"path": str(tmp_path / "nao_existe.obj"), "output": str(out)})
        assert msg["error"]["code"] == 1 and "não encontrado" in msg["error"]["message"]
        msg, _ = c.call("metodo_inexistente")
        assert msg["error"]["code"] == -32601
    finally:
        c.close()


def test_cancel(tmp_path):
    big = [synth.blob(seed=s) for s in range(12)]
    for k, m in enumerate(big):
        m.apply_translation([k * 500, 0, 0])
    parts = [Part(f"p{i}", np.asarray(m.vertices), np.asarray(m.faces)) for i, m in enumerate(big)]
    obj = tmp_path / "pesado.obj"
    save_obj(obj, parts)
    c = Client(tmp_path)
    try:
        mid = c.send("convert_mesh", {"path": str(obj), "output": str(tmp_path / "x.json")})
        c.send("cancel", {"id": mid}, mid=False)
        while True:
            msg = c.read()
            if msg.get("id") == mid:
                break
        # Ou cancelou, ou terminou antes do cancelamento chegar: os dois são válidos.
        assert "result" in msg or msg["error"]["code"] == 3
        msg, _ = c.call("ping")
        assert msg["result"]["busy"] is False
    finally:
        c.close()
