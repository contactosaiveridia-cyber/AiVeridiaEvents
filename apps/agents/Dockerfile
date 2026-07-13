# Dev image for docker-compose. The Bedrock AgentCore runtime images (one per
# graph) are built from infra/ in F8.
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir -e .[dev]

EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
