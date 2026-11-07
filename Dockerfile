FROM python:3.12-slim
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV MODE=app
ENV PORT=8000

EXPOSE 8000

CMD ["sh", "-c", \
  "if [ \"$MODE\" = 'job' ]; then python job.py; \
   else uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}; fi"]
