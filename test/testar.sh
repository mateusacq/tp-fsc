#!/usr/bin/env bash
# ----------------------------------------------------------------------------
# testar.sh - roda os cenários de teste do sistema ambulatório/médico/paciente
#
# Uso:   ./testar.sh            roda todos os cenários (1 a 6)
#        ./testar.sh 3 6        roda só os cenários 3 e 6
#
# Rode a partir da pasta com o código (ou aponte com DIR=/caminho).
# Precisa de: ambulatorio.py medico.py paciente.py fim_de_expediente.py
#             mytimer.py (só para teste local) e os dois .proto.
#
# Variáveis opcionais:
#   DIR     pasta do código                     (padrão: pasta atual)
#   BASE    porta base                          (padrão: 7000)
#   LIMITE  timeout de cada processo, em s      (padrão: 30)
#   PY      interpretador                       (padrão: "python3 -u")
#   QUIET=1 não imprime os logs, só os resultados
#
# Cada processo roda sob `timeout`: se travar, ele é morto e o teste que
# verifica o código de saída acusa "TIMEOUT (travou?)" (código 124).
# Os logs ficam em /tmp/teste_ambulatorio.XXXXXX/cenarioN/<processo>.log
# ----------------------------------------------------------------------------

DIR="${DIR:-$(pwd)}"
BASE="${BASE:-7000}"
LIMITE="${LIMITE:-30}"
PY="${PY:-python3 -u}"   # -u: saída sem buffer (não perde linhas se o processo for morto)
HOST="127.0.0.1"         # IPv4 explícito evita o problema de endereço IPv6 (::1)
RAIZ_LOG="$(mktemp -d /tmp/teste_ambulatorio.XXXXXX)"

declare -A PID RC
TOTAL=0
FALHAS=0
LOG=""

if [[ -t 1 ]]; then
  VERDE=$'\e[32m'; VERMELHO=$'\e[31m'; NEG=$'\e[1m'; FIM=$'\e[0m'
else
  VERDE=""; VERMELHO=""; NEG=""; FIM=""
fi

# ----------------------------- infraestrutura -------------------------------

# inicia NOME comando args...  -> roda em background, saída em $LOG/NOME.log
inicia() {
  local nome="$1"; shift
  ( cd "$DIR" && exec timeout "$LIMITE" "$@" ) >"$LOG/$nome.log" 2>&1 &
  PID[$nome]=$!
}

# espera NOME -> aguarda o processo e guarda o código de saída em RC[NOME]
espera() {
  local nome="$1"
  wait "${PID[$nome]}"
  RC[$nome]=$?
  unset "PID[$nome]"
}

# mata qualquer processo que tenha sobrado (ex.: cenário que travou)
mata_tudo() {
  local n
  for n in "${!PID[@]}"; do kill "${PID[$n]}" 2>/dev/null; done
  wait 2>/dev/null
}

_ok()    { echo "  ${VERDE}[ OK ]${FIM} $1"; }
_falha() { echo "  ${VERMELHO}[FALHA]${FIM} $1"; [[ -n "$2" ]] && echo "         $2"; FALHAS=$((FALHAS+1)); }

# confere ARQUIVO REGEX DESCRIÇÃO -> passa se o log casa com a regex
confere() {
  TOTAL=$((TOTAL+1))
  if grep -Eq -- "$2" "$LOG/$1.log"; then _ok "$3"
  else _falha "$3" "esperava /$2/ em $1.log"; fi
}

# nao_confere ARQUIVO REGEX DESCRIÇÃO -> passa se o log NÃO casa
nao_confere() {
  TOTAL=$((TOTAL+1))
  if grep -Eq -- "$2" "$LOG/$1.log"; then _falha "$3" "não deveria haver /$2/ em $1.log"
  else _ok "$3"; fi
}

# confere_rc NOME ESPERADO DESCRIÇÃO -> confere o código de saída
confere_rc() {
  TOTAL=$((TOTAL+1))
  local got="${RC[$1]}"
  if [[ "$got" == "$2" ]]; then _ok "$3"
  elif [[ "$got" == "124" ]]; then _falha "$3" "TIMEOUT (travou?) - $1 não terminou em ${LIMITE}s"
  else _falha "$3" "código de saída de $1 = $got (esperado $2)"; fi
}

# sem_traceback -> nenhum processo pode ter despejado exceção Python
sem_traceback() {
  TOTAL=$((TOTAL+1))
  local achados
  achados=$(grep -l "Traceback" "$LOG"/*.log 2>/dev/null | xargs -r -n1 basename 2>/dev/null | tr '\n' ' ')
  if [[ -z "$achados" ]]; then _ok "nenhum Traceback nos logs"
  else _falha "Traceback encontrado em: $achados" "veja $LOG/"; fi
}

# mostra_logs -> junta os logs de todos os processos, ordenados pelo timestamp
mostra_logs() {
  [[ -n "$QUIET" ]] && return
  echo "  --- logs (ordenados por tempo) ---"
  local f nome
  for f in "$LOG"/*.log; do
    nome="$(basename "$f" .log)"
    grep -v "mytime started" "$f" | sed -E "s/^( *[0-9]+\.[0-9]+:) /\1 [$nome] /"
  done | LC_ALL=C sort -s -n | sed 's/^/  /'
  echo "  -----------------------------------"
}

inicio_cenario() {   # número, título
  N="$1"
  LOG="$RAIZ_LOG/cenario$N"; mkdir -p "$LOG"
  PID=(); RC=()
  PA=$((BASE+N))                  # porta do ambulatório
  PM1=$((BASE+100+N*10+1))        # porta do consultório do médico 1
  PM2=$((BASE+100+N*10+2))        # porta do consultório do médico 2
  echo
  echo "${NEG}=== Cenário $N: $2 ===${FIM}"
}

# ------------------------------- cenários -----------------------------------

# 1) Fluxo feliz: 1 médico, 2 pacientes (o 2º espera o 1º), depois fim de expediente
cenario1() {
  inicio_cenario 1 "fluxo feliz (1 médico, 2 pacientes)"
  inicia amb $PY ambulatorio.py 2 1 $PA;                      sleep 1
  inicia med $PY medico.py $PM1 2 1.0 $HOST $PA;              sleep 1
  inicia p1  $PY paciente.py 5 "dor de cabeca" $HOST $PA;     sleep 0.3
  inicia p2  $PY paciente.py 6 "febre" $HOST $PA
  espera p1; espera p2
  sleep 0.5                       # dá tempo do médico voltar a esperar na fila
  inicia fim $PY fim_de_expediente.py $HOST $PA
  espera fim; espera med; espera amb
  mostra_logs

  confere amb "ARM\( 2\.0\) @ ${HOST//./\\.}:$PM1 = 0"   "médico registrado (ARM = 0)"
  confere amb "APMR\( 5\.0\) -> M\( 2\.0\) @ ${HOST//./\\.}:$PM1" "paciente 5.0 pareado com médico 2.0"
  confere amb "APME\( 6\.0\)"                              "paciente 6.0 esperou (médico ocupado)"
  confere amb "APMR\( 6\.0\) -> M\( 2\.0\)"                "paciente 6.0 foi pareado depois"
  confere med "MI\( 2\.0\) x 1\.0 @ $PM1 = 0"              "mensagem MI no formato certo"
  confere med "MAI\( 5\.0,dor de cabeca\)"                 "médico atendeu paciente 5.0"
  confere med "MAI\( 6\.0,febre\)"                         "médico atendeu paciente 6.0"
  confere p1  "PT -> +2\.0"                                "paciente 5.0 terminou (PT)"
  confere p2  "PT -> +2\.0"                                "paciente 6.0 terminou (PT)"
  confere fim "FE -> 1$"                                   "fim de expediente liberou 1 médico"
  confere med "MT *$"                                      "médico imprimiu MT"
  confere amb "ATER -> 1"                                  "ATER -> 1"
  for p in amb med p1 p2 fim; do confere_rc $p 0 "$p terminou com código 0"; done
  sem_traceback
  mata_tudo
}

# 2) Fim de expediente com médico ocioso (esperando paciente)
cenario2() {
  inicio_cenario 2 "fim_de_expediente com médico ocioso"
  inicia amb $PY ambulatorio.py 2 1 $PA;                      sleep 1
  inicia med $PY medico.py $PM1 2 1.0 $HOST $PA;              sleep 1.5
  inicia fim $PY fim_de_expediente.py $HOST $PA
  espera fim; espera med; espera amb
  mostra_logs

  confere amb "ACPE\( 2\.0\)"        "médico ficou esperando na fila (ACPE)"
  confere fim "FE -> 1$"             "FE -> 1 (um médico liberado, valor positivo)"
  confere amb "ATER -> 1$"           "ATER -> 1"
  confere med "MCPR -> +0\.0"        "médico recebeu id 0.0 ao ser liberado"
  confere med "MT *$"                "médico imprimiu MT"
  for p in amb med fim; do confere_rc $p 0 "$p terminou com código 0"; done
  sem_traceback
  mata_tudo
}

# 3) Fim de expediente com pacientes esperando (sem médico) -> n_liberados negativo
cenario3() {
  inicio_cenario 3 "fim_de_expediente com pacientes esperando (esperado negativo)"
  inicia amb $PY ambulatorio.py 3 1 $PA;                      sleep 1
  inicia p1  $PY paciente.py 5 "dor de cabeca" $HOST $PA;     sleep 0.2
  inicia p2  $PY paciente.py 6 "febre" $HOST $PA;             sleep 1
  inicia fim $PY fim_de_expediente.py $HOST $PA
  espera fim; espera p1; espera p2; espera amb
  mostra_logs

  confere amb "APME\( 5\.0\)"                       "paciente 5.0 ficou esperando (APME)"
  confere amb "APME\( 6\.0\)"                       "paciente 6.0 ficou esperando (APME)"
  confere fim "FE -> -2$"                           "FE -> -2 (dois pacientes liberados, negativo)"
  confere amb "ATER -> -2$"                         "ATER -> -2"
  confere amb "APMR\( 5\.0\) -> M\( 0\.0\) @ :0"    "paciente 5.0 liberado sem médico (M 0.0 @ :0)"
  confere p1  "PPMR\(:0\)"                          "paciente 5.0 viu porto 0"
  confere p2  "PPMR\(:0\)"                          "paciente 6.0 viu porto 0"
  nao_confere p1 "PT ->"                            "paciente 5.0 não imprimiu PT"
  nao_confere p2 "PT ->"                            "paciente 6.0 não imprimiu PT"
  for p in amb p1 p2 fim; do confere_rc $p 0 "$p terminou com código 0"; done
  sem_traceback
  mata_tudo
}

# 4) Fila cheia: 1 cadeira, médico ocupado. P1 consulta, P2 senta, P3 é rejeitado
cenario4() {
  inicio_cenario 4 "fila cheia (1 cadeira)"
  inicia amb $PY ambulatorio.py 1 1 $PA;                      sleep 1
  inicia med $PY medico.py $PM1 2 3.0 $HOST $PA;              sleep 1
  inicia p1  $PY paciente.py 5 "dor de cabeca" $HOST $PA;     sleep 0.3   # pareia com o médico
  inicia p2  $PY paciente.py 6 "febre" $HOST $PA;             sleep 0.3   # ocupa a única cadeira
  inicia p3  $PY paciente.py 7 "tosse" $HOST $PA                          # fila cheia -> vai embora
  espera p3                       # deve terminar na hora, sem esperar as consultas
  espera p1; espera p2
  sleep 0.5
  inicia fim $PY fim_de_expediente.py $HOST $PA
  espera fim; espera med; espera amb
  mostra_logs

  confere amb "APMR\( 7\.0\) -> M\( 0\.0\) @ :0"    "ambulatório rejeitou paciente 7.0 (fila cheia)"
  nao_confere amb "APME\( 7\.0\)"                   "paciente 7.0 não chegou a esperar"
  confere amb "APME\( 6\.0\)"                       "paciente 6.0 sentou na cadeira (APME)"
  confere p3  "PPMR\(:0\)"                          "paciente 7.0 recebeu porto 0"
  nao_confere p3 "PT ->"                            "paciente 7.0 não imprimiu PT"
  confere p1  "PT -> +2\.0"                         "paciente 5.0 foi atendido"
  confere p2  "PT -> +2\.0"                         "paciente 6.0 foi atendido"
  nao_confere med "MAI\( 7\.0"                      "médico nunca atendeu paciente 7.0"
  confere fim "FE -> 1$"                            "fim de expediente liberou o médico ocioso"
  for p in amb med p1 p2 p3 fim; do confere_rc $p 0 "$p terminou com código 0"; done
  sem_traceback
  mata_tudo
}

# 5) Segundo médico excedente: ambulatório comporta 1 médico, o 2º recebe -1
cenario5() {
  inicio_cenario 5 "segundo médico excedente (esperado -1)"
  inicia amb  $PY ambulatorio.py 2 1 $PA;                     sleep 1
  inicia medA $PY medico.py $PM1 2 1.0 $HOST $PA;             sleep 1
  inicia medB $PY medico.py $PM2 3 1.0 $HOST $PA
  espera medB                     # deve encerrar sozinho, logo após o MI
  sleep 0.5
  inicia fim  $PY fim_de_expediente.py $HOST $PA
  espera fim; espera medA; espera amb
  mostra_logs

  confere amb  "ARM\( 2\.0\) @ ${HOST//./\\.}:$PM1 = 0"   "médico A registrado (0)"
  confere amb  "ARM\( 3\.0\) @ ${HOST//./\\.}:$PM2 = -1"  "médico B rejeitado no ambulatório (-1)"
  confere medB "MI\( 3\.0\) x 1\.0 @ $PM2 = -1"           "médico B imprimiu MI com -1"
  nao_confere medB "MCPI"                                 "médico B não tentou chamar paciente"
  confere_rc medB 0 "médico B terminou sozinho com código 0 (não pendurou)"
  confere medA "MI\( 2\.0\) x 1\.0 @ $PM1 = 0"            "médico A imprimiu MI com 0"
  confere fim  "FE -> 1$"                                 "fim de expediente liberou só o médico A"
  confere medA "MT *$"                                    "médico A imprimiu MT"
  for p in amb medA fim; do confere_rc $p 0 "$p terminou com código 0"; done
  sem_traceback
  mata_tudo
}

# 6) Fim de expediente DURANTE uma consulta: o médico deve imprimir MT e terminar
cenario6() {
  inicio_cenario 6 "fim_de_expediente durante uma consulta"
  inicia amb $PY ambulatorio.py 2 1 $PA;                      sleep 1
  inicia med $PY medico.py $PM1 2 4.0 $HOST $PA;              sleep 1
  inicia p1  $PY paciente.py 5 "dor de cabeca" $HOST $PA;     sleep 1.5   # consulta (4 s) em andamento
  inicia fim $PY fim_de_expediente.py $HOST $PA
  espera fim                      # nenhuma chamada ativa -> deve retornar logo
  espera amb                      # servidor encerra
  espera p1                       # a consulta termina normalmente (não depende do ambulatório)
  espera med                      # ao terminar a consulta, o ambulatório já não existe
  mostra_logs

  confere fim "FE -> 0$"                       "FE -> 0 (ninguém esperando)"
  confere med "MAI\( 5\.0,dor de cabeca\)"     "consulta do paciente 5.0 começou"
  confere med "MAF"                            "consulta terminou normalmente (MAF)"
  confere p1  "PT -> +2\.0"                    "paciente terminou a consulta mesmo com o ambulatório fechado"
  confere med "MT *$"                          "médico imprimiu MT"
  confere_rc med 0 "médico terminou com código 0 (não pendurou)"
  for p in amb p1 fim; do confere_rc $p 0 "$p terminou com código 0"; done
  sem_traceback
  mata_tudo
}

# --------------------------------- principal ---------------------------------

prepara() {
  local f
  for f in ambulatorio.py medico.py paciente.py fim_de_expediente.py mytimer.py \
           ambulatorio.proto consultorio.proto; do
    [[ -f "$DIR/$f" ]] || { echo "ERRO: falta $f em $DIR (use DIR=/caminho ./testar.sh)"; return 1; }
  done
  if [[ ! -f "$DIR/ambulatorio_pb2.py" || ! -f "$DIR/consultorio_pb2_grpc.py" ]]; then
    echo "Stubs não encontrados; rodando 'make stubs'..."
    (cd "$DIR" && make stubs) || { echo "ERRO: make stubs falhou"; return 1; }
  fi
  if pgrep -f "ambulatorio.py" >/dev/null 2>&1; then
    echo "AVISO: já existe um ambulatorio.py rodando (use 'pkill -f ambulatorio.py'); pode haver conflito de portas."
  fi
  echo "Código em: $DIR | portas a partir de $BASE | logs em: $RAIZ_LOG"
}

resumo() {
  echo
  echo "${NEG}==================== RESUMO ====================${FIM}"
  if [[ $FALHAS -eq 0 ]]; then
    echo "${VERDE}Todas as $TOTAL verificações passaram.${FIM}"
  else
    echo "${VERMELHO}$FALHAS de $TOTAL verificações falharam.${FIM}  (logs completos em $RAIZ_LOG)"
  fi
  exit $(( FALHAS > 0 ))
}

principal() {
  prepara || exit 2
  local escolhidos=("$@") n
  [[ ${#escolhidos[@]} -eq 0 ]] && escolhidos=(1 2 3 4 5 6)
  for n in "${escolhidos[@]}"; do
    case "$n" in
      1|2|3|4|5|6) "cenario$n" ;;
      *) echo "Cenário inválido: $n (use 1 a 6)" ;;
    esac
  done
  resumo
}

# só executa se for chamado diretamente (permite `source testar.sh` para depurar)
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  trap mata_tudo EXIT
  principal "$@"
fi
