ARG BASE_IMAGE=vocaregrcregistry.azurecr.io/vocare-grc-base@sha256:4e0f21ac39fe2f72651cd178f354e44540d610f9669b800f4ad16b9aebeade32
FROM ${BASE_IMAGE}

COPY . /app

EXPOSE 8080

CMD ["python", "bot.py", "--host", "0.0.0.0", "--port", "8080"]
