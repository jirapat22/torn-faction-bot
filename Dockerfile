FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as a normal user, not root (11/11/2026): a bug in the bot then can't change
# anything else in the container. All its state is in data/ (mounted from the
# Mac at /app/data), which this user can write to.
RUN useradd -r -u 999 apexpunk
USER apexpunk

CMD ["python", "bot.py"]
