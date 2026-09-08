FROM python:3.12-slim
WORKDIR /app

COPY requirement.txt .
RUN pip install --no-cache-dir -r requirement.txt

COPY . .

# La base est construite pendant le build : l'image démarre sans dépendre
# du réseau, et le premier appel n'attend pas le parsing du GTFS.
RUN python -m gtfs_metz.loader gtfs_metz/LEMET-gtfs.zip data/gtfs.sqlite

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
