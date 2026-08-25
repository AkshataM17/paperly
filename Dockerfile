# A container, not a serverless function: the build holds state in memory for
# the duration of a job and writes the finished page to disk, so it needs a
# process that stays up and a volume that persists.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY paper2vid ./paper2vid
# pages committed to the repo, copied into the library on boot
COPY seed ./seed
RUN pip install --no-cache-dir -e .

# mount a volume here to keep built pages across deploys
ENV PAPER2VID_LIBRARY=/data/library
VOLUME /data

CMD ["sh", "-c", "paper2vid --serve --library ${PAPER2VID_LIBRARY:-library}"]
