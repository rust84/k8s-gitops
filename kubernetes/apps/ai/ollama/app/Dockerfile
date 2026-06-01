ARG UBUNTU_VERSION=26.04
ARG GOVERSION=1.24.1
ARG OV_GENAI_ARCHIVE=openvino_genai_ubuntu24_2026.0.0.0_x86_64

## Build Stage
FROM ubuntu:${UBUNTU_VERSION} AS build

ARG GOVERSION
ARG OV_GENAI_ARCHIVE

SHELL ["/bin/bash", "-c"]

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates git wget curl gcc g++ \
        software-properties-common libtbb-dev \
        ocl-icd-opencl-dev && \
    rm -rf /var/lib/apt/lists/*

# Python 3.10 (required by openvino_genai)
RUN add-apt-repository ppa:deadsnakes/ppa && \
    apt-get update && \
    apt-get install -y --no-install-recommends python3.10 && \
    ln -sf /usr/bin/python3.10 /usr/bin/python3 && \
    rm -rf /var/lib/apt/lists/*

# Go
RUN curl -fsSL https://golang.org/dl/go${GOVERSION}.linux-amd64.tar.gz | tar xz -C /usr/local
ENV PATH=/usr/local/go/bin:$PATH

# OpenVINO GenAI
WORKDIR /opt
RUN wget -q https://storage.openvinotoolkit.org/repositories/openvino_genai/packages/2026.0/linux/${OV_GENAI_ARCHIVE}.tar.gz && \
    tar -xzf ${OV_GENAI_ARCHIVE}.tar.gz && \
    rm ${OV_GENAI_ARCHIVE}.tar.gz

ENV GENAI_DIR=/opt/${OV_GENAI_ARCHIVE}
ENV CGO_ENABLED=1
ENV GODEBUG=cgocheck=0
ENV CGO_LDFLAGS=-L${GENAI_DIR}/runtime/lib/intel64
ENV CGO_CFLAGS=-I${GENAI_DIR}/runtime/include

# Build Ollama with OpenVINO GenAI
WORKDIR /build
RUN git clone --depth 1 https://github.com/openvinotoolkit/openvino_contrib.git
WORKDIR /build/openvino_contrib/modules/ollama_openvino
# Patch typo'd constant names (STREAMMING→STREAMING) fixed in genai 2026.0.0.0 headers
RUN sed -i 's/OV_GENAI_STREAMMING_STATUS_/OV_GENAI_STREAMING_STATUS_/g' genai/genai.go
RUN source ${GENAI_DIR}/setupvars.sh && \
    go build -trimpath -ldflags="-w -s" -o /usr/bin/ollama .

# Collect runtime libraries
RUN mkdir -p /runtime-libs && \
    cp -P ${GENAI_DIR}/runtime/lib/intel64/*.so* /runtime-libs/

## Runtime Stage
FROM ubuntu:${UBUNTU_VERSION}

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        libtbb12 \
        curl \
        intel-opencl-icd \
        python3 \
        python3-pip \
        vim && \
    pip3 install --no-cache-dir --break-system-packages \
        huggingface_hub[cli] \
        modelscope && \
    rm -rf /var/lib/apt/lists/*

COPY --from=build /usr/bin/ollama /usr/bin/ollama
COPY --from=build /runtime-libs/ /usr/lib/

RUN ldconfig

ENV OLLAMA_HOST=0.0.0.0:11434
ENV GODEBUG=cgocheck=0
EXPOSE 11434

ENTRYPOINT ["/usr/bin/ollama"]
CMD ["serve"]
