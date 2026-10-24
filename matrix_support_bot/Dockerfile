FROM python:3.12-slim

WORKDIR /app
ENV PYTHONPATH=/app

COPY requirements*.txt ./
ARG WITH_SEMANTIC=0
RUN pip install --no-cache-dir -r requirements.lock.txt
RUN if [ "$WITH_SEMANTIC" = "1" ]; then pip install --no-cache-dir --no-deps -r requirements.semantic.lock.txt && pip check; fi

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
