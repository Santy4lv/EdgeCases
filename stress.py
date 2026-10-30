import subprocess
import sys
import os

SOL_SOURCE = sys.argv[1] if len(sys.argv) > 1 else "my_sol.cpp"
BRUTE_SOURCE = "brute.cpp"
GEN_SCRIPT = "gen.py"

# 1. Validaciones previas
for archivo in [SOL_SOURCE, BRUTE_SOURCE, GEN_SCRIPT]:
    if not os.path.exists(archivo):
        print(f"Error: No se encuentra el archivo obligatorio '{archivo}'.")
        sys.exit(1)

print(f"Compilando {SOL_SOURCE} y {BRUTE_SOURCE} con g++...")

# 2. Compilar binarios de Windows (.exe)
comp_sol = subprocess.run(["g++", "-O2", "-std=c++17", SOL_SOURCE, "-o", "exec_my.exe"])
comp_brute = subprocess.run(["g++", "-O2", "-std=c++17", BRUTE_SOURCE, "-o", "exec_brute.exe"])

if comp_sol.returncode != 0 or comp_brute.returncode != 0:
    print("Error durante la compilación. Revisa los mensajes de g++ arriba.")
    sys.exit(1)

print("Iniciando 150 ciclos de pruebas de estrés...")

# 3. Ciclo de pruebas
for i in range(1, 151):
    # Generar caso de prueba
    gen_proc = subprocess.run([sys.executable, GEN_SCRIPT], capture_output=True, text=True)
    in_data = gen_proc.stdout

    # Ejecutar solución de fuerza bruta (oráculo)
    try:
        brute_proc = subprocess.run(
            ["./exec_brute.exe"],
            input=in_data,
            capture_output=True,
            text=True,
            timeout=3
        )
    except subprocess.TimeoutExpired:
        print(f"\n[!] TLE en la solución fuerza bruta en el test {i}. Reduce los límites en {GEN_SCRIPT}.")
        sys.exit(1)

    # Ejecutar tu solución
    try:
        my_proc = subprocess.run(
            ["./exec_my.exe"],
            input=in_data,
            capture_output=True,
            text=True,
            timeout=2
        )
    except subprocess.TimeoutExpired:
        print(f"\n❌ TLE en tu solución en el test {i}.")
        print("=== ENTRADA ===")
        print(in_data)
        sys.exit(1)

    # Comparar salidas ignorando espacios residuales y saltos de línea Windows/Linux
    out_brute = brute_proc.stdout.strip().split()
    out_my = my_proc.stdout.strip().split()

    if out_brute != out_my:
        print(f"\n==========================================")
        print(f"❌ DISCREPANCIA ENCONTRADA EN EL TEST {i}")
        print("==========================================")
        print("--- ENTRADA GENERADA ---")
        print(in_data)
        print("--- SALIDA ESPERADA (FUERZA BRUTA) ---")
        print(brute_proc.stdout)
        print("--- TU SALIDA ---")
        print(my_proc.stdout)
        print("==========================================")
        sys.exit(1)

    sys.stdout.write(".")
    sys.stdout.flush()

print("\n✅ 150/150 pruebas pasadas exitosamente sin discrepancias.")