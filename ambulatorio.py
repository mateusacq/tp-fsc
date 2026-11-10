import sys
import grpc
import mytimer
import warnings
import threading
import ipaddress
import ambulatorio_pb2
import ambulatorio_pb2_grpc
from typing import Optional
from concurrent import futures

def split_peer(addr_str: str):
    # Remove gRPC's transport prefix, if present.
    if addr_str.startswith(("ipv4:", "ipv6:")):
        addr_str = addr_str.split(":", 1)[1]

    address, separator, port = addr_str.rpartition(":")
    # Remove brackets around an IPv6 address.
    if address.startswith("[") and address.endswith("]"):
        address = address[1:-1]

    return address, int(port)

class Prontuario:
    def __init__(self, lock_global: threading.Lock | None):
        self.cond = threading.Condition(lock_global)
        self.id_paciente = 0.0
        self.id_medico = 0.0
        self.pronto = False

class Queue:
    def __init__(self, size: int):
        self.items = [Prontuario(None) for _ in range(size)]
        self.count = 0
        self.front = 0
        self.tail  = 0
        self.max_size = size

    def size(self):
        return self.count
    def empty(self):
        return self.count == 0
    def full(self):
        return self.count == self.max_size

    def enqueue(self, item) -> bool:
        if self.full():
            print("Error: enqueue attempt in full queue")
            return False

        self.tail = (self.tail + 1) % self.max_size
        self.items[self.tail] = item
        self.count += 1
        return True
    def dequeue(self) -> Prontuario:
        if self.empty():
            print("Error: dequeue attempt in empty queue")
            return Prontuario(None)

        front = self.items[self.front]
        self.front = (self.front + 1) % self.max_size
        self.count -= 1
        return front

class AmbulatorioService(ambulatorio_pb2_grpc.AmbulatorioServiceServicer):
    """Classe para os métodos e atributos do servidor do ambulatório"""
    def __init__(self, n_pacientes, n_medicos, stop_server):
        self.lock = threading.Lock()
        self.cond = threading.Condition(self.lock)
        self.stop_event = stop_server
        self.chamadas_ativas = 0

        # Filas
        self.fila_pacientes = Queue(n_pacientes)
        self.fila_medicos = Queue(n_medicos)

        # Dicionário manipulado pelo método RegistraMedico
        # Chave: id_medico (float)
        # Valor: dicionário com {"ender": string, "porto": int}
        self.diretorio_medicos = {}

    def ProcuraMedico(self, request, context):
        with self.lock:
            self.chamadas_ativas += 1
            mytimer.tstamp_msg(f"APMI({request.id_paciente:4.1f})")
            if self.fila_pacientes.full(): # Fila de espera cheia
                self.chamadas_ativas -= 1
                return ambulatorio_pb2.ProcuraMedicoResponse(
                    end_maquina="",
                    porto=0,
                    id_medico=0.0
                )

            if self.fila_medicos.empty(): # Sem médicos disponíveis
                p = Prontuario(self.lock)
                p.id_paciente = request.id_paciente
                self.fila_pacientes.enqueue(p)

                mytimer.tstamp_msg(f"APME({request.id_paciente:4.1f})")
                while not p.pronto: # Paciente espera
                    p.cond.wait()

                # Paciente chamado
                info = self.diretorio_medicos[p.id_medico]
                self.chamadas_ativas -= 1
                return ambulatorio_pb2.ProcuraMedicoResponse(
                    end_maquina=info["ender"],
                    porto=info["porto"],
                    id_medico=p.id_medico
                )

            p = self.fila_medicos.dequeue()
            p.id_paciente = request.id_paciente
            p.pronto = True
            p.cond.notify()
            info = self.diretorio_medicos[p.id_medico]
            self.chamadas_ativas -= 1
            if self.chamadas_ativas == 0: self.cond.notify_all() # Acorda Termino se esta tiver sido a ultima chamada
            mytimer.tstamp_msg(
                f"APMR({p.id_medico:4.1f}) -> P({p.id_paciente:4.1f}) @ {info['ender']}:{info['porto']}"
            )
            return ambulatorio_pb2.ProcuraMedicoResponse(
                end_maquina=info["ender"],
                porto=info["porto"],
                id_medico=p.id_medico
            )

    def RegistraMedico(self, request, context):
        """Método que extrai IP do context.peer() do gRPC (a porta é efêmera,
        foi apenas a que o cliente usou para se conectar) e o salva com mais
        porto da request (corresponde ao porto real do consultório) e id"""
        with self.lock:
                self.chamadas_ativas += 1

                raw_peer = context.peer()
                ip_medico, _ = split_peer(raw_peer)
                if ip_medico is None:
                    print("IP do médico não encontrado")
                    return ambulatorio_pb2.RegistraMedicoResponse(return_code=-1)

                if len(self.diretorio_medicos) >= self.fila_medicos.max_size:
                    return_code = -1
                else:
                    self.diretorio_medicos[request.id_medico] = {
                        "ender": ip_medico,
                        "porto": request.porto
                    }
                    return_code = 0

                self.chamadas_ativas -= 1
                if self.chamadas_ativas == 0: self.cond.notify_all() # Acorda Termino se esta tiver sido a ultima chamada
                mytimer.tstamp_msg(
                    f"ARM({request.id_medico:4.1f}) @ {ip_medico}:{request.porto} = {return_code}"
                )
                return ambulatorio_pb2.RegistraMedicoResponse(return_code=return_code)

    def ChamaPaciente(self, request, context):
        with self.lock:
            self.chamadas_ativas += 1
            mytimer.tstamp_msg(
                f"ACPI({request.id_medico:4.1f})"
            )
            if (self.fila_pacientes.empty()):
                p = Prontuario(lock_global=self.lock)
                p.id_medico = request.id_medico
                self.fila_medicos.enqueue(p)
                mytimer.tstamp_msg(
                    f"ACPE({request.id_medico:4.1f})"
                )
                while (not p.pronto): # Sem pacientes buscando médico
                    p.cond.wait()
                # Encontrou paciente
                id_paciente = p.id_paciente
            else:
                p = self.fila_pacientes.dequeue()
                p.id_medico = request.id_medico
                p.pronto = True
                p.cond.notify()
                id_paciente = p.id_paciente

        # Libera mutex e dorme por 0.1s para paciente se movimentar e médico ir tomar café
        if id_paciente != 0: mytimer.mysleep(0.1)

        with self.lock:
            self.chamadas_ativas -= 1
            if self.chamadas_ativas == 0: self.cond.notify_all() # Acorda Termino se esta tiver sido a ultima chamada
            mytimer.tstamp_msg(
                f"ACPR({request.id_medico:4.1f}) -> P({id_paciente:4.1f})"
            )
            return ambulatorio_pb2.ChamaPacienteResponse(
                id_prox_paciente=id_paciente
            )

    def Termino(self, request, context):
        """
        Libera médicos e pacientes (processando valor de retorno) e depois
        espera todas as threads encerrarem para terminar o servidor.
        """
        mytimer.tstamp_msg("ATEI")

        with self.lock:
            n_liberados = 0

            while (not self.fila_medicos.empty()):
                p = self.fila_medicos.dequeue()
                p.id_paciente = 0.0
                p.pronto = True
                p.cond.notify()
                n_liberados += 1

            while (not self.fila_pacientes.empty()):
                p = self.fila_pacientes.dequeue()
                p.id_medico = 0.0
                p.pronto = True
                p.cond.notify()
                n_liberados -= 1

            # O método Termino (e apenas ele) usa o self.cond.wait() atrelado ao self.lock global para aguardar
            # o esvaziamento das chamadas ativas (possui um notify_all() atrelado)
            while self.chamadas_ativas > 0:
                self.cond.wait()

            self.stop_event.set()
            mytimer.tstamp_msg(f"ATER -> {n_liberados}")
            return ambulatorio_pb2.TerminoResponse(n_liberados=n_liberados)

def serve(port, n_cadeiras, n_medicos):
    stop_server = threading.Event()

     # Numero de threads que comporta o maximo de pessoas fazendo requisições + uma que seria um novo cliente para liberar o server
    max_workers = max(n_cadeiras, n_medicos) + 1
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers))
    ambulatorio_pb2_grpc.add_AmbulatorioServiceServicer_to_server(AmbulatorioService(n_cadeiras, n_medicos, stop_server), server)
    server.add_insecure_port("[::]:" + str(port))
    server.start()

    stop_server.wait()
    server.stop(1)

def main():
    args = sys.argv[1:]

    n_cadeiras = int(args[0])
    n_medicos = int(args[1])
    port = args[2]

    mytimer.tstamp_msg(f"AI({n_cadeiras},{n_medicos})@{port}")

    serve(port, n_cadeiras, n_medicos)

if __name__ == "__main__":
  main()
