FROM debian:bookworm-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY . .

RUN cmake -S . -B build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build build -j \
    && ctest --test-dir build --output-on-failure

FROM debian:bookworm-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/build/opti_graph_builder_cli /usr/local/bin/opti_graph_builder_cli

ENTRYPOINT ["/usr/local/bin/opti_graph_builder_cli"]
