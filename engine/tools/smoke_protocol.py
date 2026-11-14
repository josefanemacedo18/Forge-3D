"""Teste rápido do protocolo do motor (usado na CI e para diagnóstico).

Inicia "python -m forge3d servidor", envia ping e uma conversão pequena e
imprime cada passo com o tempo decorrido. Nunca trava: cada leitura tem prazo.
"""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]


def main() -> int:
    t0 = time.time()

    def say(msg: str) -> None:
        print(f"[{time.time() - t0:6.2f}s] {msg}", flush=True)

    tmp = Path(tempfile.mkdtemp(prefix="forge3d_smoke_"))
    obj = tmp / "caixa.obj"
    obj.write_text("# forge3d units=mm up=Z\no caixa\n"
                   "v 0 0 0\nv 400 0 0\nv 400 300 0\nv 0 300 0\nv 0 0 200\nv 400 0 200\nv 400 300 200\nv 0 300 200\n"
                   "f 1 3 2\nf 1 4 3\nf 5 6 7\nf 5 7 8\nf 1 2 6\nf 1 6 5\nf 2 3 7\nf 2 7 6\nf 3 4 8\nf 3 8 7\nf 4 1 5\nf 4 5 8\n",
                   encoding="utf-8")
    say(f"python: {sys.executable}")
    proc = subprocess.Popen([sys.executable, "-m", "forge3d", "servidor", "--logs", str(tmp / "logs")], cwd=ENGINE,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out_q: queue.Queue = queue.Queue()
    err: list[str] = []

    def pump():
        for raw in proc.stdout:
            out_q.put(raw)
        out_q.put(None)

    def drain():
        for raw in proc.stderr:
            err.append(raw.decode("utf-8", "replace"))

    threading.Thread(target=pump, daemon=True).start()
    threading.Thread(target=drain, daemon=True).start()

    def send(obj_: dict) -> None:
        proc.stdin.write((json.dumps(obj_) + "\n").encode("utf-8"))
        proc.stdin.flush()

    def wait_for(mid: int, timeout: float = 120) -> dict:
        end = time.time() + timeout
        while time.time() < end:
            try:
                raw = out_q.get(timeout=max(0.1, end - time.time()))
            except queue.Empty:
                break
            if raw is None:
                raise RuntimeError("motor encerrou; stderr:\n" + "".join(err[-40:]))
            msg = json.loads(raw.decode("utf-8"))
            if msg.get("method") == "progress":
                say(f"  progresso {msg['params']['fraction']:.0%} {msg['params']['message']}")
                continue
            if msg.get("id") == mid:
                return msg
        raise TimeoutError("sem resposta do motor; stderr:\n" + "".join(err[-40:]))

    try:
        say("enviando ping")
        send({"jsonrpc": "2.0", "id": 1, "method": "ping"})
        say(f"ping: {wait_for(1, 60)}")
        say("enviando convert_mesh")
        send({"jsonrpc": "2.0", "id": 2, "method": "convert_mesh",
              "params": {"path": str(obj), "output": str(tmp / "caixa.json"), "level": "C"}})
        res = wait_for(2, 180)
        say(f"convert_mesh: {'ok, nível ' + res['result']['summary']['achievedLevel'] if 'result' in res else res}")
        send({"jsonrpc": "2.0", "id": 3, "method": "shutdown"})
        say(f"shutdown: {wait_for(3, 30)}")
        proc.wait(timeout=15)
        say(f"motor encerrado (código {proc.returncode})")
        return 0 if "result" in res else 1
    except Exception as exc:
        say(f"FALHA: {exc}")
        proc.kill()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
