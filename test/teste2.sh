echo "Ambulatório: 2 cadeiras, 2 médicos, porto 9999"
python3 ambulatorio.py 2 2 9999 > amb.out 2>&1 &

sleep 1

echo "Paciente"
python3 paciente.py 6.1 dengue localhost 9999 > pac1.out 2>&1 &

sleep 1

echo Médico
python3 medico.py 9991 99.1 2 localhost 9999 > med1.out 2>&1 &

sleep 5

echo "Fim do expediente"
python3 fim_de_expediente.py localhost 9999 > fim.out 2>&1
