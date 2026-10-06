FROM python:3.12-slim
WORKDIR /app
COPY prototype/server.py prototype/runtime.py prototype/web.html /app/
WORKDIR /data
VOLUME /data
EXPOSE 8765
CMD ["python", "-u", "/app/server.py"]
