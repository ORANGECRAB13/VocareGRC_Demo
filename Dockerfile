FROM vocaregrcregistry.azurecr.io/vocare-grc-base:latest

COPY . /app

EXPOSE 8080

CMD ["python", "bot.py", "--host", "0.0.0.0", "--port", "8080"]
