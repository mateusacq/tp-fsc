import sys
import grpc
import mytimer

from ambulatorio_pb2 import TerminoRequest
from ambulatorio_pb2_grpc import AmbulatorioServiceStub

def parse_args():
    args = sys.argv[1:]
    if len(args) != 2:
        print("Uso: python fim_de_expediente.py <endereco_servidor> <port>")
        sys.exit(1)

    try:
        return (args[0], int(args[1]))
    except ValueError:
        print("Erro: argumentos devem ser dos tipos str, int")
        sys.exit(1)

def main():
    endereco, port = parse_args()

    mytimer.tstamp_msg(f"FE({endereco},{port})")

    # Abre conexão com este cliente apenas para chamar o método Termino()
    with grpc.insecure_channel(f"{endereco}:{port}") as channel:
        stub = AmbulatorioServiceStub(channel)
        resposta = stub.Termino(TerminoRequest(), wait_for_ready=True)
        n_liberados = resposta.n_liberados

    mytimer.tstamp_msg(f"FE -> {n_liberados}")

if __name__ == "__main__":
  main()
