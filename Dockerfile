FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY . /app

RUN pip install --no-cache-dir uv \
 && uv pip install --system \
    "pipecat-ai[deepgram,elevenlabs,mistral,groq,silero,smallwebrtc]" \
    fastapi \
    uvicorn[standard] \
    python-dotenv \
    loguru \
    cerebras-cloud-sdk \
    neo4j

EXPOSE 8080

CMD ["python", "bot.py", "--host", "0.0.0.0", "--port", "8080"]
