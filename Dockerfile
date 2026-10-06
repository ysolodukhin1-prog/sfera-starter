FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app:/app/runtime_support
WORKDIR /app
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && useradd --uid 1001 --create-home sfera
COPY runtime_support /app/runtime_support
COPY instance/runtime /app
COPY app /app
COPY schema/002_standalone.sql /app/002_standalone.sql
COPY tools/bootstrap.py /app/bootstrap.py
COPY tests /app/tests
COPY knowledge-seed /app/knowledge-seed
COPY skill-seed /app/skill-seed
RUN mkdir /knowledge /skill-artifacts && chown -R sfera:sfera /knowledge /skill-artifacts
USER 1001:1001
CMD ["uvicorn","client_mcp_server:app","--host","0.0.0.0","--port","8080","--no-access-log"]
