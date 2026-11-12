import sys
import grpc
import mytimer

from consultorio_pb2 import ConsultaRequest
from ambulatorio_pb2 import ProcuraMedicoRequest
from ambulatorio_pb2_grpc import AmbulatorioServiceStub
from consultorio_pb2_grpc import ConsultorioServiceStub

def parse_args():
    args = sys.argv[1:]
    if len(args) != 4:
        print("Uso: python paciente.py <id_paciente> <desc_problema> <endereco_servidor> <porta>")
        sys.exit(1)

    try:
        return (float(args[0]), str(args[1]), str(args[2]), int(args[3]))
    except ValueError:
        print("Erro: argumentos devem ser dos tipos float, str, str, int")
        sys.exit(1)

def main():
    id_paciente, desc_problema, endereco, port = parse_args()

    mytimer.tstamp_msg(f"PI({id_paciente:4.1f},{desc_problema})")

    # Procura por um médico
    with grpc.insecure_channel(f"{endereco}:{port}") as channel:
        stub = AmbulatorioServiceStub(channel)
        resp = stub.ProcuraMedico(ProcuraMedicoRequest(id_paciente=id_paciente))

        endereco = resp.end_maquina
        porto = resp.porto
        id_medico = resp.id_medico
        mytimer.tstamp_msg(f"PPMR({endereco}:{porto})")

        if porto == 0:
            # mytimer.tstamp_msg(f"PT -> {id_medico:4.1f}") # Não sei se essa parte deve ser impressa ainda
            sys.exit(0) # Fila cheia leva o paciente a encerrar e ir embora

    # Participa da consulta
    with grpc.insecure_channel(f"{endereco}:{porto}") as channel:
        stub = ConsultorioServiceStub(channel)
        resp = stub.Consulta(
            ConsultaRequest(
                id_paciente=id_paciente, desc_problema=desc_problema))

        id_medico = resp.id_medico
        mytimer.tstamp_msg(f"PT -> {id_medico:4.1f}")

if __name__ == "__main__":
  main()
