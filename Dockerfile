FROM python:3.11-slim

WORKDIR /app

RUN mkdir -p /etc/secrets /tmp

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x startup.sh

EXPOSE 7860

CMD ["./startup.sh"]
