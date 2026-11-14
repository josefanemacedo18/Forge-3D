"""Servidor local do motor (JSON-RPC 2.0, uma mensagem JSON por linha).

O plugin do Revit inicia ``python -m forge3d servidor`` e conversa pela
entrada/saída padrão: sem portas de rede, sem firewall, funciona offline.

Mensagens do servidor para o plugin:
- resposta:    {"jsonrpc":"2.0","id":N,"result":{...}}
- erro:        {"jsonrpc":"2.0","id":N,"error":{"code":C,"message":"...","data":{...}}}
- progresso:   {"jsonrpc":"2.0","method":"progress","params":{"id":N,"fraction":0.4,"message":"..."}}

Mensagens do plugin para o servidor:
- chamada:     {"jsonrpc":"2.0","id":N,"method":"convert_mesh","params":{...}}
- cancelar:    {"jsonrpc":"2.0","method":"cancel","params":{"id":N}}

Métodos: ping, hardware, convert_mesh, analyze_image, convert_image,
validate_spec, shutdown. Uma conversão por vez; ping, hardware e cancel são
respondidos imediatamente mesmo durante uma conversão.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable

from . import __version__
from .errors import CancelledError, EngineError

PROTOCOL_VERSION = 1
ERR_PARSE, ERR_METHOD, ERR_PARAMS, ERR_ENGINE, ERR_INTERNAL, ERR_CANCELLED, ERR_BUSY = -32700, -32601, -32602, 1, 2, 3, 4

log = logging.getLogger("forge3d")


def _setup_logging(log_dir: str | None) -> None:
    base = Path(log_dir) if log_dir else (Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Forge3D" / "logs")
    try:
        base.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(base / "motor.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    except OSError:
        handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


class Server:
    def __init__(self, out_stream):
        self._out = out_stream
        self._lock = threading.Lock()
        self._job: threading.Thread | None = None
        self._job_id: Any = None
        self._cancel = threading.Event()
        self._running = True

    # -- saída -----------------------------------------------------------------
    def send(self, msg: dict) -> None:
        line = json.dumps(msg, ensure_ascii=False, separators=(",", ":"), default=_json_default)
        with self._lock:
            self._out.write(line + "\n")
            self._out.flush()

    def reply(self, mid: Any, result: Any) -> None:
        self.send({"jsonrpc": "2.0", "id": mid, "result": result})

    def error(self, mid: Any, code: int, message: str, data: Any = None) -> None:
        err = {"code": code, "message": message}
        if data is not None:
            err["data"] = data
        self.send({"jsonrpc": "2.0", "id": mid, "error": err})

    def progress(self, mid: Any, fraction: float, message: str) -> None:
        self.send({"jsonrpc": "2.0", "method": "progress",
                   "params": {"id": mid, "fraction": round(max(0.0, min(1.0, fraction)), 4), "message": message}})

    # -- despacho ---------------------------------------------------------------
    def handle_line(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            self.error(None, ERR_PARSE, f"Mensagem inválida: {exc}")
            return
        method = msg.get("method")
        mid = msg.get("id")
        params = msg.get("params") or {}
        if method == "cancel":
            if self._job_id is not None and params.get("id") in (None, self._job_id):
                self._cancel.set()
            return
        if method == "ping":
            self.reply(mid, {"version": __version__, "protocol": PROTOCOL_VERSION, "python": sys.version.split()[0],
                             "busy": self._job is not None and self._job.is_alive()})
            return
        if method == "hardware":
            self._run_inline(mid, self._hardware, params)
            return
        if method == "shutdown":
            self._running = False
            self._cancel.set()
            self.reply(mid, {"ok": True})
            return
        if method == "validate_spec":
            self._run_inline(mid, self._validate_spec, params)
            return
        handler = {"convert_mesh": self._convert_mesh, "analyze_image": self._analyze_image,
                   "convert_image": self._convert_image}.get(method)
        if handler is None:
            self.error(mid, ERR_METHOD, f"Método desconhecido: {method}")
            return
        if self._job is not None and self._job.is_alive():
            self.error(mid, ERR_BUSY, "O motor já está processando outra conversão.")
            return
        self._cancel.clear()
        self._job_id = mid
        self._job = threading.Thread(target=self._run_job, args=(mid, handler, params), daemon=True)
        self._job.start()

    def _run_inline(self, mid: Any, fn: Callable[[dict], Any], params: dict) -> None:
        try:
            self.reply(mid, fn(params))
        except EngineError as exc:
            self.error(mid, ERR_ENGINE, str(exc))
        except Exception as exc:  # pragma: no cover - defensivo
            log.exception("erro interno")
            self.error(mid, ERR_INTERNAL, f"Erro interno do motor: {exc}", {"trace": traceback.format_exc()})

    def _run_job(self, mid: Any, fn: Callable, params: dict) -> None:
        try:
            log.info("início %s %s", fn.__name__, {k: v for k, v in params.items() if k != "spec"})
            result = fn(mid, params)
            self.reply(mid, result)
            log.info("fim %s", fn.__name__)
        except CancelledError as exc:
            self.error(mid, ERR_CANCELLED, str(exc))
        except EngineError as exc:
            log.warning("erro do motor: %s", exc)
            data = {"errors": exc.errors} if hasattr(exc, "errors") else None
            self.error(mid, ERR_ENGINE, str(exc), data)
        except Exception as exc:
            log.exception("erro interno")
            from .spec.validate import SpecValidationError

            if isinstance(exc, SpecValidationError):
                self.error(mid, ERR_ENGINE, "O motor gerou uma família inválida; nada foi criado no Revit.",
                           {"errors": exc.errors})
            else:
                self.error(mid, ERR_INTERNAL, f"Erro interno do motor: {exc}", {"trace": traceback.format_exc()})
        finally:
            self._job_id = None

    # -- métodos ----------------------------------------------------------------------
    def _hardware(self, params: dict) -> dict:
        from . import hardware
        from .image import ai

        info = hardware.detect().to_dict()
        info["ai"] = ai.status()
        return info

    def _validate_spec(self, params: dict) -> dict:
        from .spec import evaluate, validate

        spec = params.get("spec")
        if spec is None and params.get("path"):
            with open(params["path"], encoding="utf-8") as fh:
                spec = json.load(fh)
        if spec is None:
            raise EngineError("Informe 'spec' ou 'path'.")
        errors = validate.schema_errors(spec) or validate.semantic_errors(spec)
        flex = evaluate.flex_test(spec) if not errors else None
        return {"valid": not errors, "errors": errors, "flex": flex.__dict__ if flex else None}

    def _write_spec(self, spec: dict, output: str | None) -> str:
        if not output:
            raise EngineError("Parâmetro 'output' (caminho do spec) é obrigatório.")
        out = Path(output)
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(out.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(spec, fh, ensure_ascii=False, default=_json_default)
        os.replace(tmp, out)
        return str(out)

    def _convert_mesh(self, mid: Any, params: dict) -> dict:
        from .pipeline import ConvertOptions, convert_mesh_file

        path = _require(params, "path")
        opts = ConvertOptions(
            level=params.get("level", "C"), units=params.get("units", "auto"), up_axis=params.get("upAxis", "auto"),
            category=params.get("category", "GenericModel"), family_name=params.get("familyName"),
            fit_tol=float(params.get("fitTol", 1.0)), allow_approx=bool(params.get("allowApprox", True)),
            sheet_thickness=float(params.get("sheetThickness", 3.0)),
            max_freeform_faces=int(params.get("maxFreeformFaces", 15000)),
            compute_fidelity=bool(params.get("computeFidelity", True)))
        res = convert_mesh_file(path, opts, progress=lambda f, m: self.progress(mid, f, m), cancel=self._cancel.is_set)
        spec_path = self._write_spec(res.spec, params.get("output"))
        return {"specPath": spec_path, "summary": res.summary}

    def _analyze_image(self, mid: Any, params: dict) -> dict:
        from .image.pipeline_image import ImageOptions, analyze_image

        path = _require(params, "path")
        self.progress(mid, 0.1, "Analisando a imagem")
        return analyze_image(path, ImageOptions(use_ai_mask=bool(params.get("useAiMask", True))), params.get("workDir"))

    def _convert_image(self, mid: Any, params: dict) -> dict:
        from .image.pipeline_image import ImageOptions, convert_image

        path = _require(params, "path")
        opts = ImageOptions(
            strategy=params.get("strategy", "auto"), reference=params.get("reference", "altura"),
            reference_mm=_opt_float(params.get("referenceMm")), depth_mm=_opt_float(params.get("depthMm")),
            level=params.get("level", "C"), category=params.get("category", "GenericModel"),
            family_name=params.get("familyName"), use_ai_mask=bool(params.get("useAiMask", True)))
        res = convert_image(path, opts, progress=lambda f, m: self.progress(mid, f, m), cancel=self._cancel.is_set,
                            work_dir=params.get("workDir"))
        spec_path = self._write_spec(res.spec, params.get("output"))
        return {"specPath": spec_path, "summary": res.summary}

    # -- laço principal -------------------------------------------------------------
    def serve(self, in_stream) -> None:
        for line in in_stream:
            self.handle_line(line)
            if not self._running:
                break
        if self._job is not None:
            self._cancel.set()
            self._job.join(timeout=5)


def _require(params: dict, key: str) -> Any:
    if key not in params or params[key] in (None, ""):
        raise EngineError(f"Parâmetro obrigatório ausente: {key}")
    return params[key]


def _opt_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    return float(v)


def _json_default(o: Any):
    try:
        import numpy as np

        if isinstance(o, np.ndarray):
            return o.tolist()
        if isinstance(o, np.generic):
            return o.item()
    except ImportError:
        pass
    if isinstance(o, Path):
        return str(o)
    raise TypeError(f"Objeto não serializável: {type(o).__name__}")


def run(log_dir: str | None = None) -> None:
    """Inicia o servidor. Tudo que bibliotecas imprimirem vai para stderr,
    mantendo stdout exclusivo para o protocolo."""
    _setup_logging(log_dir)
    proto_fd = os.dup(1)
    os.dup2(2, 1)  # escritas nativas em stdout vão para stderr
    out = os.fdopen(proto_fd, "w", encoding="utf-8", newline="\n", buffering=1)
    sys.stdout = sys.stderr
    try:
        sys.stdin.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    server = Server(out)
    log.info("motor %s iniciado (python %s)", __version__, sys.version.split()[0])
    server.serve(sys.stdin)
    log.info("motor encerrado")
