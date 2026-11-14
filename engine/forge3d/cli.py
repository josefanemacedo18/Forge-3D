"""Linha de comando do motor Forge 3D (em português).

Exemplos:
    python -m forge3d servidor
    python -m forge3d converter mesa.obj --nivel C -o mesa.familyspec.json
    python -m forge3d imagem vaso.jpg --altura 450 --estrategia revolucao -o vaso.json
    python -m forge3d hardware
    python -m forge3d validar mesa.familyspec.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .errors import EngineError


def _print_summary(summary: dict) -> None:
    print(f"Família: {summary.get('name')}")
    print(f"Nível pedido/alcançado: {summary.get('requestedLevel')} / {summary.get('achievedLevel')}")
    env = summary.get("envelope", {})
    print(f"Envelope (mm): {env.get('width', 0):.1f} x {env.get('depth', 0):.1f} x {env.get('height', 0):.1f}")
    print(f"Peças: {summary.get('parts')}  Planos: {summary.get('planes')}  Parâmetros: {summary.get('parameters')}")
    for b in summary.get("levelCBlockers", []):
        print(f"  Nível C impedido: {b}")
    for w in summary.get("warnings", []):
        print(f"  Aviso: {w}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="forge3d", description="Motor do Forge 3D")
    parser.add_argument("--versao", action="version", version=f"forge3d {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("servidor", help="inicia o servidor local usado pelo plugin")
    s.add_argument("--logs", default=None, help="pasta dos registros (log)")

    c = sub.add_parser("converter", help="converte um arquivo 3D em FamilySpec")
    c.add_argument("arquivo")
    c.add_argument("--nivel", choices=["A", "B", "C"], default="C")
    c.add_argument("--unidade", default="auto", choices=["auto", "mm", "cm", "dm", "m", "in", "ft"])
    c.add_argument("--eixo", default="auto", choices=["auto", "Z", "Y", "-Z", "-Y", "X", "-X"])
    c.add_argument("--categoria", default="GenericModel")
    c.add_argument("--nome", default=None)
    c.add_argument("-o", "--saida", default=None)

    i = sub.add_parser("imagem", help="gera FamilySpec a partir de uma imagem")
    i.add_argument("arquivo")
    i.add_argument("--estrategia", default="auto", choices=["auto", "revolucao", "extrusao", "ia"])
    g = i.add_mutually_exclusive_group()
    g.add_argument("--altura", type=float, default=None, help="altura real do objeto (mm)")
    g.add_argument("--largura", type=float, default=None, help="largura real do objeto (mm)")
    i.add_argument("--profundidade", type=float, default=None, help="profundidade da extrusão (mm)")
    i.add_argument("--nivel", choices=["A", "B", "C"], default="C")
    i.add_argument("--categoria", default="GenericModel")
    i.add_argument("--nome", default=None)
    i.add_argument("-o", "--saida", default=None)

    sub.add_parser("hardware", help="mostra GPU, memória e perfil de execução da IA")

    v = sub.add_parser("validar", help="valida um FamilySpec")
    v.add_argument("arquivo")

    args = parser.parse_args(argv)
    try:
        if args.cmd == "servidor":
            from .server import run

            run(args.logs)
            return 0
        if args.cmd == "converter":
            from .pipeline import ConvertOptions, convert_mesh_file

            res = convert_mesh_file(args.arquivo, ConvertOptions(level=args.nivel, units=args.unidade, up_axis=args.eixo,
                                                                 category=args.categoria, family_name=args.nome),
                                    progress=lambda f, m: print(f"[{f:4.0%}] {m}", file=sys.stderr))
            out = args.saida or str(Path(args.arquivo).with_suffix(".familyspec.json"))
            Path(out).write_text(json.dumps(res.spec, ensure_ascii=False), encoding="utf-8")
            _print_summary(res.summary)
            print(f"Spec gravado em: {out}")
            return 0
        if args.cmd == "imagem":
            from .image.pipeline_image import ImageOptions, convert_image

            ref, ref_mm = ("largura", args.largura) if args.largura else ("altura", args.altura)
            res = convert_image(args.arquivo, ImageOptions(strategy=args.estrategia, reference=ref, reference_mm=ref_mm,
                                                           depth_mm=args.profundidade, level=args.nivel,
                                                           category=args.categoria, family_name=args.nome),
                                progress=lambda f, m: print(f"[{f:4.0%}] {m}", file=sys.stderr))
            out = args.saida or str(Path(args.arquivo).with_suffix(".familyspec.json"))
            Path(out).write_text(json.dumps(res.spec, ensure_ascii=False), encoding="utf-8")
            _print_summary(res.summary)
            print(f"Estratégia: {res.summary.get('strategy')}")
            print(f"Spec gravado em: {out}")
            return 0
        if args.cmd == "hardware":
            from . import hardware
            from .image import ai

            info = hardware.detect()
            print(f"Sistema: {info.os}  Python {info.python}  CPUs: {info.cpu_count}  RAM: {info.ram_gb} GB")
            print(f"PyTorch: {info.torch or 'não instalado'}  CUDA: {'sim' if info.cuda_available else 'não'}")
            for gpu in info.gpus:
                sup = {True: "suportada", False: "NÃO suportada", None: "?"}[gpu.supported_by_torch]
                print(f"GPU: {gpu.name}  {gpu.vram_gb:.1f} GB  capacidade {gpu.capability or '?'}  ({sup})")
            print(f"Perfil da IA: {info.profile}")
            st = ai.status()
            print(f"Módulo de IA (TripoSR): {'pronto' if st['available'] else 'indisponível — ' + st['reason']}")
            for n in info.notes:
                print(f"Nota: {n}")
            return 0
        if args.cmd == "validar":
            from .spec import evaluate, validate

            spec = json.loads(Path(args.arquivo).read_text(encoding="utf-8"))
            errors = validate.schema_errors(spec) or validate.semantic_errors(spec)
            if errors:
                print("Spec INVÁLIDO:")
                for e in errors:
                    print(f"  - {e}")
                return 2
            flex = evaluate.flex_test(spec)
            print("Spec válido.")
            print("Teste de flexão: " + ("ok" if flex.ok else "falhas:"))
            for f in flex.failures:
                print(f"  - {f}")
            return 0 if flex.ok else 3
    except EngineError as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1
    return 0
