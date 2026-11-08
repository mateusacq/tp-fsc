# Variáveis de configuração
PYTHON = python3
PROTOC = $(PYTHON) -m grpc_tools.protoc

# Arquivos de entrada e arquivos gerados
PROTOS = ambulatorio.proto consultorio.proto
GENERATED = ambulatorio_pb2.py ambulatorio_pb2_grpc.py consultorio_pb2.py consultorio_pb2_grpc.py

# Alvo padrão do make
all: stubs

# Regra para compilar stubs a partir dos .proto
stubs: $(PROTOS)
	$(PROTOC) -I. --python_out=. --grpc_python_out=. $(PROTOS)

# Regra para limpar os arquivos gerados após compilação
clean:
	rm -f $(GENERATED)

.PHONY: all stubs clean
