# AiVeridiaEvents — pipeline de ML

LLM propio del vertical: **Llama 3.1 8B Instruct + QLoRA**, servido serverless vía
**Bedrock Custom Model Import** (prod) y **Ollama** (dev). Mientras no exista el modelo
fine-tuned, el router (`apps/agents/src/llm/router.py`) degrada limpiamente al Llama base.

## 1. Dataset (`dataset/`)

Tres fuentes, formato chat multi-turno ShareGPT con plantilla `llama3`:

1. **Transcripciones reales** de WhatsApp de Los Jazmines, etiquetadas `convertido|perdido`;
   las ganadoras se sobre-muestrean ×2 (`curar.py`, `OVERSAMPLE`).
2. **Cotizaciones históricas y objeciones** ("¿incluye torta?", "está caro") con las
   respuestas que cerraron venta (mismo formato de export).
3. **Sintético controlado**: variaciones generadas con un modelo frontier y validadas por
   humano (registro peruano: Yape/Plin, "señito", diminutivos; casos borde: lluvia,
   reprogramaciones).

**Anonimización obligatoria** antes de entrenar (`anonimizar.py`): teléfonos → sintéticos
estables, DNI/emails/direcciones → placeholders, nombres → pseudónimos deterministas
(coherentes en toda la conversación). Los montos NO se tocan: la adherencia a precios es
parte del aprendizaje. Meta inicial: 5,000–8,000 ejemplos.

```bash
python dataset/curar.py --export chats/lead1.txt --etiqueta convertido \
                        --negocio "+51944123123" --out dataset/train.jsonl
```

## 2. Fine-tuning QLoRA (`finetune/`)

```bash
# opción A: GPU propia / spot g5 (2-4 h)
pip install axolotl[flash-attn] && axolotl train finetune/axolotl_config.yml

# opción B: SageMaker gestionado (g5.2xlarge spot)
python finetune/sagemaker_train.py --dataset s3://aiveridia-events-finetune-prod/datasets/train.jsonl
```

Config: 4-bit, `lora_r=32`, target linear completo, `sequence_len=4096` con packing,
3 épocas, lr 2e-4 cosine. Checkpoints en `finetune/checkpoints/`.

## 3. Fusión de adapters

CMI requiere el modelo **fusionado** (no acepta adapters):

```bash
python finetune/fusionar_adapters.py --adapters finetune/checkpoints/aiveridia-events-qlora
# -> ./aiveridia-events-8b-fusionado (safetensors + tokenizer)
```

## 4. Bedrock Custom Model Import (prod)

1. Subir el modelo fusionado: `aws s3 sync aiveridia-events-8b-fusionado/ s3://aiveridia-events-finetune-prod/modelos/v1/`
2. Crear el import job (rol con acceso al bucket; región **us-east-1**):
   ```bash
   aws bedrock create-model-import-job \
     --job-name aiveridia-events-v1 \
     --imported-model-name aiveridia-events-8b \
     --role-arn arn:aws:iam::ACCOUNT:role/aiveridia-bedrock-import \
     --model-data-source '{"s3DataSource": {"s3Uri": "s3://aiveridia-events-finetune-prod/modelos/v1/"}}'
   ```
3. Al completar (`aws bedrock list-imported-models`), copiar el ARN del modelo a
   `AIVERIDIA_EVENTS_MODEL_ARN` (Secrets Manager). El router lo toma sin redeploy.
4. Facturación: **por token, sin GPU 24/7** (se cobra por copias de modelo activas en
   ventanas de 5 min) — ideal para tráfico irregular de fines de semana.
5. La plantilla de chat es la misma `llama3` en dev (Ollama) y prod (CMI): "el LLM
   cambia, la chain no".

## 5. Dev local (Ollama)

```bash
cd ollama && ollama create aiveridia-events:8b -f Modelfile
```

## 6. Evaluación de regresión (`eval/`)

`dataset_regresion.jsonl`: **50 conversaciones doradas** generadas por
`generar_dorados.py` (20 cotizaciones directas con precio gold calculado con las reglas
del seed, 10 con datos incompletos, 8 objeciones/FAQ, 6 negociaciones de descuento con
umbral, 6 fechas ocupadas). Evaluadores (`evaluadores.py`):

- **extraccion_calificacion**: fecha/aforo/tipo exactos contra el gold.
- **adherencia_precios**: la respuesta solo puede mencionar montos del motor de reglas —
  **debe ser 100%** (regla innegociable 1).
- **escalamiento_descuento**: >10% ⇒ interrupt; ≤10% ⇒ no.
- **tono**: LLM-as-judge con rúbrica peruana (0–1).

```bash
python eval/crear_dataset_langsmith.py    # sube el dataset (una vez)
python eval/run_eval.py                   # evalúa el modelo activo del router
```

Gate de despliegue: un modelo nuevo (fine-tuned o upgrade de base) solo pasa a prod si
adherencia = 1.0 y extracción/escalamiento no retroceden contra el experimento anterior.
