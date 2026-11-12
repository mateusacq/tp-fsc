import sys
import grpc
import mytimer
import threading

import ambulatorio_pb2
import ambulatorio_pb2_grpc

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
    """
    Classe dos objetos que serao enfileirados e gerenciados no servidor do ambulatório
    cond: variável de condição para avisar médicos ou pacientes em espera
    id_paciente e id_medico: armazenam identificadores dos participantes da consulta
    pronto: variável verificada no loop que espera por Condition.notify()
    """
    def __init__(self, lock_global=None):
        self.cond = threading.Condition(lock_global)
        self.id_paciente = 0.0
        self.id_medico = 0.0
        self.pronto = False

class Queue:
    """
    Fila que armazenará prontuários de pacientes e de médicos esperando
    front e tail: armazena primeiro e ultimos elementos da fila
    enqueue() e dequeue(): operações de adição e remoção de elementos (FIFO) em O(1)
    """
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
            return False

        self.items[self.tail] = item
        # Armazena elementos em O(1), sem deslocamento interno O(n) do python
        self.tail = (self.tail + 1) % self.max_size
        self.count += 1
        return True
    def dequeue(self) -> Prontuario:
        if self.empty():
            return Prontuario(None)

        front = self.items[self.front]
        self.front = (self.front + 1) % self.max_size
        self.count -= 1
        return front

class AmbulatorioService(ambulatorio_pb2_grpc.AmbulatorioServiceServicer):
    """
    Classe para os métodos e atributos do servidor do ambulatório (gRPC)
    self.lock: região mutex global
    self.cond: variável de condição global
    self.chamadas_ativas: atributo auxiliar para Termino(). Conta quantas tarefas estão ativas
    self.encerrando: atributo auxiliar para Termino(). Se ativo, impede qualquer chamada nova
    _fim_chamada(): método auxiliar de Termino(). Atualiza contador de chamadas e usa Condition()
    """
    def __init__(self, n_pacientes, n_medicos, stop_server):
        self.lock = threading.Lock()
        self.cond = threading.Condition(self.lock)
        self.stop_event = stop_server
        self.chamadas_ativas = 0
        self.encerrando = False

        # Filas
        self.fila_pacientes = Queue(n_pacientes)
        self.fila_medicos = Queue(n_medicos)

        # Dicionário manipulado pelo método RegistraMedico
        # Chave: id_medico (float)
        # Valor: dicionário com {"ender": string, "porto": int}
        self.diretorio_medicos = {}

    def _fim_chamada(self):
        self.chamadas_ativas -= 1
        if self.chamadas_ativas == 0:
            self.cond.notify_all() # Acorda Termino se esta tiver sido a ultima chamada

    def ProcuraMedico(self, request, context):
        """
        Método que paciente usa para requisitar médico no ambulatório
        """
        with self.lock:
            if self.encerrando: # Servidor encerrando (Termino() chamado no fim de expediente)
                return ambulatorio_pb2.ProcuraMedicoResponse(
                    end_maquina ="", porto=0, id_medico=0.0)

            self.chamadas_ativas += 1
            mytimer.tstamp_msg(f"APMI({request.id_paciente:4.1f})")
            ender, porto, id_medico = "", 0, 0.0

            if not self.fila_pacientes.full(): # Lugar disponível para paciente se assentar
                if self.fila_medicos.empty(): # Sem médicos disponíveis
                    p = Prontuario(self.lock)
                    p.id_paciente = request.id_paciente
                    self.fila_pacientes.enqueue(p)
                    mytimer.tstamp_msg(f"APME({request.id_paciente:4.1f})")
                    while not p.pronto: # Paciente espera na fila
                        p.cond.wait()
                else: # Médico disponível
                    p = self.fila_medicos.dequeue()
                    p.id_paciente = request.id_paciente
                    p.pronto = True
                    p.cond.notify() # Aciona médico disponível

                # Paciente foi chamado e prontuário foi preenchido por médico
                # na chamada do ChamaPaciente() concomitante a esta
                id_medico = p.id_medico
                if id_medico != 0.0:
                    info = self.diretorio_medicos[p.id_medico]
                    ender, porto = info["ender"], info["porto"]

            mytimer.tstamp_msg(
                f"APMR({request.id_paciente:4.1f}) -> M({id_medico:4.1f}) @ {ender}:{porto}"
            )
            self._fim_chamada()
            return ambulatorio_pb2.ProcuraMedicoResponse(
                end_maquina=ender, porto=porto, id_medico=id_medico)

    def RegistraMedico(self, request, context):
        """
        Método usado por médico para se registrar no ambulatório.
        Extrai IP do médico pelo context.peer() do gRPC (a porta é efêmera,
        foi apenas a que o cliente usou para se conectar) e o salva com o id
        e com o porto da request (corresponde ao porto real do consultório)
        """
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

                self._fim_chamada()
                mytimer.tstamp_msg(
                    f"ARM({request.id_medico:4.1f}) @ {ip_medico}:{request.porto} = {return_code}"
                )
                return ambulatorio_pb2.RegistraMedicoResponse(return_code=return_code)

    def ChamaPaciente(self, request, context):
        """
        Método usado pelo médico para chamar paciente esperando no ambulatório
        """
        with self.lock:
            if self.encerrando:
                return ambulatorio_pb2.ChamaPacienteResponse(id_prox_paciente=0.0)

            self.chamadas_ativas += 1
            mytimer.tstamp_msg(
                f"ACPI({request.id_medico:4.1f})"
            )
            if (self.fila_pacientes.empty()): # Sem pacientes buscando médico
                p = Prontuario(lock_global=self.lock)
                p.id_medico = request.id_medico
                self.fila_medicos.enqueue(p)
                mytimer.tstamp_msg(
                    f"ACPE({request.id_medico:4.1f})"
                )
                while (not p.pronto): # Espera por paciente
                    p.cond.wait()
                # Encontrou paciente, preenche prontuário
                id_paciente = p.id_paciente
            else: # Paciente disponível, preenche prontuário
                p = self.fila_pacientes.dequeue()
                p.id_medico = request.id_medico
                p.pronto = True
                p.cond.notify()
                id_paciente = p.id_paciente

        # Libera mutex e dorme por 0.1s (para paciente se movimentar e médico ir tomar café)
        if id_paciente >= 1.0: mytimer.mysleep(0.1)

        with self.lock:
            self._fim_chamada()
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
        Este método (e apenas ele) usa o self.cond.wait() atrelado ao self.lock global
        para aguardar o esvaziamento das chamadas ativas (possui um notify_all() atrelado).
        Demais metodos usam Condition() do prontuário (p.cond).
        """
        mytimer.tstamp_msg("ATEI")

        try:
            with self.lock:
                self.encerrando = True
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

                while self.chamadas_ativas > 0:
                    self.cond.wait()

                mytimer.tstamp_msg(f"ATER -> {n_liberados}")
                return ambulatorio_pb2.TerminoResponse(n_liberados=n_liberados)
        finally:
            # Este bloco é executado antes do return, garantindo que servidor gRPC receberá sinal Event() para desligar
            self.stop_event.set()

def parse_args():
    args = sys.argv[1:]
    if len(args) != 3:
        print("Uso: python ambulatorio.py <n_cadeiras> <n_medicos> <port>")
        sys.exit(1)

    try:
        return (int(args[0]), int(args[1]), int(args[2]))
    except ValueError:
        print("Erro: dois primeiros argumentos devem ser inteiros")
        sys.exit(1)

def serve(port, n_cadeiras, n_medicos):
    stop_server = threading.Event()

    # Numero de threads que comporta o maximo de pessoas fazendo requisições + uma folga de 4
    max_workers = max(n_cadeiras, n_medicos) + 4
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers))
    ambulatorio_pb2_grpc.add_AmbulatorioServiceServicer_to_server(AmbulatorioService(n_cadeiras, n_medicos, stop_server), server)
    server.add_insecure_port("[::]:" + str(port))
    server.start()

    stop_server.wait()
    server.stop(1).wait()

def main():
    n_cadeiras, n_medicos, port = parse_args()

    mytimer.tstamp_msg(f"AI({n_cadeiras},{n_medicos})@{port}")

    serve(port, n_cadeiras, n_medicos) # Começa a servir

if __name__ == "__main__":
  main()
