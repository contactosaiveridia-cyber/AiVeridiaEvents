"""Alternativa gestionada al fine-tuning local: SageMaker training job.

1× ml.g5.2xlarge spot (~$0.6/h con spot, 2-4 h por corrida). El job ejecuta
axolotl dentro del contenedor HF y sube checkpoints al bucket de fine-tuning
(aws_s3_bucket.finetune de infra/main.tf).

  python sagemaker_train.py --dataset s3://aiveridia-events-finetune-prod/datasets/train.jsonl
"""

import argparse


def lanzar(dataset_s3: str, rol_arn: str, bucket: str) -> None:
    from sagemaker.huggingface import HuggingFace

    estimador = HuggingFace(
        entry_point="entrypoint_axolotl.sh",
        source_dir=".",
        instance_type="ml.g5.2xlarge",
        instance_count=1,
        use_spot_instances=True,
        max_wait=6 * 3600,
        max_run=5 * 3600,
        role=rol_arn,
        transformers_version="4.49",
        pytorch_version="2.5",
        py_version="py311",
        hyperparameters={"config": "axolotl_config.yml"},
        output_path=f"s3://{bucket}/checkpoints/",
        environment={"HF_TOKEN": "desde SecretsManager"},
    )
    estimador.fit({"train": dataset_s3})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--rol", default="arn:aws:iam::ACCOUNT:role/aiveridia-sagemaker")
    parser.add_argument("--bucket", default="aiveridia-events-finetune-prod")
    args = parser.parse_args()
    lanzar(args.dataset, args.rol, args.bucket)
