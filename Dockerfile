FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .

RUN apt-get update && \
    apt-get install -y postgresql-client locales && \
    pip install --upgrade pip && pip install -r requirements.txt && \
    sed -i '/ru_RU.UTF-8/s/^# //g' /etc/locale.gen && \
    locale-gen ru_RU.UTF-8

ENV LANG=ru_RU.UTF-8
ENV LANGUAGE=ru_RU:ru
ENV LC_ALL=ru_RU.UTF-8

COPY . .

RUN sed -i "s|DATABASE_URL = .*|DATABASE_URL = '${DATABASE_URL}'|" config.py

CMD ["python", "main.py"]
