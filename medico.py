import sys
import grpc
import mytimer
import threading

import consultorio_pb2
from ambulatorio_pb2 import RegistraMedicoRequest, ChamaPacienteRequest
import consultorio_pb2_grpc
import ambulatorio_pb2_grpc

from concurrent import futures

class ConsultorioService(consultorio_pb2_grpc.ConsultorioServiceServicer):
    """
    Classe para os métodos e atributos do servidor do consultório.
    Semáforos que permitirão exclusão mútua entre thread principal
    do médico (cliente) e thread do servidor do consultório foram usados.
    Semáforos foram preferidos aqui às variáveis de condição e mutexes
    pois elas não têm memória e exigem booleanos testados em loops while,
    o que exigiria muitas declarações de variáveis.
    """
    def __init__(self, id_medico):
        self.id_medico = id_medico

        self.porta_aberta = threading.Semaphore(0) # main thread -> gRPC server thread
        self.paciente_registrado = threading.Semaphore(0) # gRPC -> main
        self.consulta_terminou = threading.Semaphore(0) # main -> gRPC

        # Dados que serão enviados para thread principal do médico
        self.shared_id_paciente = 0.0
        self.shared_desc_problema = ""

    def Consulta(self, request, context):
        """
        Método que implementa a consulta, chamado pelo cliente do paciente.
        """
        mytimer.tstamp_msg(f"CI({request.id_paciente:4.1f},{request.desc_problema})")

        # Paciente aguarda a porta ser aberta na thread principal do médico para entrar
        self.porta_aberta.acquire()

        # Paciente se registra
        self.shared_id_paciente = request.id_paciente
        self.shared_desc_problema = request.desc_problema

        # Paciente avisa o méidco de seu registro
        self.paciente_registrado.release()

        # Espera final da consulta feita na thread principal do médico
        self.consulta_terminou.acquire()
        mytimer.tstamp_msg(f"CR({request.id_paciente:4.1f},{self.id_medico:4.1f})")
        return consultorio_pb2.ConsultaResponse(id_medico=self.id_medico)

def parse_args():
    args = sys.argv[1:]
    if len(args) != 5:
        print("Uso: python medico.py <porta_consultorio> <id_medico> <tempo> <endereco> <porta_ambulatorio>")
        sys.exit(1)

    try:
        return (int(args[0]), float(args[1]), float(args[2]), str(args[3]), int(args[4]))
    except ValueError:
        print("Erro: argumentos devem ser dos tipos int, float, float, str, int")
        sys.exit(1)

def serve(port, servico):
    # max_workers definido como 1 apenas, para só ocorrer 1 consulta por vez
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=1))
    consultorio_pb2_grpc.add_ConsultorioServiceServicer_to_server(servico, server)
    server.add_insecure_port("[::]:" + str(port))

    server.start()

    return server

def main(): # Thread principal do médico
    porta_consultorio, id_medico, tempo, endereco, porta_ambulatorio = parse_args()

    # Inicia servidor médico (consultório) (disparado em segundo plano, execução continua)
    servico = ConsultorioService(id_medico) # Salva objeto do serviço para acessar semáforos depois
    server = serve(porta_consultorio, servico)

    # Inicia cliente médico com registro
    with grpc.insecure_channel(f"{endereco}:{porta_ambulatorio}") as channel:
        stub = ambulatorio_pb2_grpc.AmbulatorioServiceStub(channel)
        resp = stub.RegistraMedico(RegistraMedicoRequest(
            id_medico=id_medico, porto=porta_consultorio))

        return_code = resp.return_code
        mytimer.tstamp_msg(f"MI({id_medico:4.1f}) x {tempo:3.1f} @ {porta_consultorio} = {resp.return_code}")
        if return_code == -1:
            sys.exit(0)

        # Rotina do médico
        while True:
            mytimer.tstamp_msg("MCPI")
            try:
                resp_chama = stub.ChamaPaciente(ChamaPacienteRequest(id_medico=id_medico))
                mytimer.tstamp_msg(f"MCPR -> {resp_chama.id_prox_paciente:4.1f}")
            except grpc.RpcError: # Caso em que o ambulatorio fecha enquanto o medico dorme
                    mytimer.tstamp_msg("MT ")
                    server.stop(0)
                    sys.exit(0)

            if resp_chama.id_prox_paciente < 1.0: # Fim do expediente ou erro
                mytimer.tstamp_msg("MT ")
                break

            # Abre a porta para o paciente que chega (avisa server)
            servico.porta_aberta.release()

            # Espera registro do paciente no server
            servico.paciente_registrado.acquire()

            # Realiza consulta
            mytimer.tstamp_msg(f"MAI({servico.shared_id_paciente:4.1f},{servico.shared_desc_problema})")
            mytimer.mysleep(tempo) # Aguarda termino da consulta

            # Notifica servidor do término da consulta depois continua rotina
            servico.consulta_terminou.release()
            mytimer.tstamp_msg("MAF")

        # Fim do expediente
        server.stop(0)

if __name__ == "__main__":
  main()
