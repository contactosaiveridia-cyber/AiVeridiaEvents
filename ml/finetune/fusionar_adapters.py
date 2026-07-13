"""Fusiona los adapters QLoRA con el modelo base -> modelo completo safetensors.

Bedrock Custom Model Import requiere el modelo FUSIONADO (no acepta adapters
sueltos). El resultado se sube tal cual a S3 (ver ml/README.md §4).

  python fusionar_adapters.py \
      --base meta-llama/Meta-Llama-3.1-8B-Instruct \
      --adapters ./checkpoints/aiveridia-events-qlora \
      --out ./aiveridia-events-8b-fusionado
"""

import argparse


def fusionar(base: str, adapters: str, out: str) -> None:
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    print(f"cargando base {base} (bf16, CPU-offload si hace falta)…")
    modelo = AutoModelForCausalLM.from_pretrained(
        base, torch_dtype=torch.bfloat16, device_map="auto")
    modelo = PeftModel.from_pretrained(modelo, adapters)

    print("fusionando adapters (merge_and_unload)…")
    modelo = modelo.merge_and_unload()

    print(f"guardando modelo fusionado en {out}…")
    modelo.save_pretrained(out, safe_serialization=True, max_shard_size="5GB")
    AutoTokenizer.from_pretrained(base).save_pretrained(out)
    print("listo: sube el directorio completo a s3 para Custom Model Import")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="meta-llama/Meta-Llama-3.1-8B-Instruct")
    parser.add_argument("--adapters", required=True)
    parser.add_argument("--out", default="./aiveridia-events-8b-fusionado")
    args = parser.parse_args()
    fusionar(args.base, args.adapters, args.out)
