FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_INPUT=1

WORKDIR /app

COPY requirements.docker.txt .
COPY wheelhouse-linux/ /wheelhouse/
RUN pip install --no-index --find-links=/wheelhouse --no-deps --progress-bar off \
      -r requirements.docker.txt

COPY . /app

EXPOSE 8080

CMD ["python", "bot.py", "--host", "0.0.0.0", "--port", "8080"]
