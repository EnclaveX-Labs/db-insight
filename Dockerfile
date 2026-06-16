FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir .

ENV DB_INSIGHT_MCP_HOST=0.0.0.0
ENV DB_INSIGHT_MCP_PORT=8000
ENV DB_INSIGHT_OLLAMA_URL=http://host.docker.internal:11434
ENV DB_INSIGHT_MODEL=gemma3:latest

VOLUME ["/data"]
EXPOSE 8000

CMD ["db-insight", "mcp"]
