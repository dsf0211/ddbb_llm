from __future__ import annotations
import re
from typing import Any, List
import mysql.connector
import requests
import urllib3
import yaml

# ---------------------------------------------------------------------------
# Configuración del LLM remoto
# ---------------------------------------------------------------------------

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)  # Desactiva las advertencias de seguridad de urllib3
URL_BASE_LM = "http://192.168.1.60:1234/v1"  # URL base del modelo remoto
NOMBRE_MODELO = "gemma-3-12b-it-qat"  # Nombre del modelo remoto
ENCABEZADOS = {"Content-Type": "application/json"}  # Encabezados para las solicitudes HTTP


class ModeloRemoto:
    """Envoltura mínima para un modelo remoto."""
    def __init__(self, url_base: str, nombre_modelo: str):
        self.url_base = url_base.rstrip("/")  # Asegura que la URL base no termine con "/"
        self.nombre_modelo = nombre_modelo  # Asigna el nombre del modelo

    def generar_respuesta(
        self,
        *,
        mensajes: list[dict[str, str]],
        temperatura: float = 0.7,
        top_p: float = 0.9,
        max_tokens: int,
    ) -> dict[str, Any]:
        """
        Envía una solicitud al modelo remoto para generar una respuesta de chat.

        Parámetros:
        - mensajes: Lista de mensajes en formato dict con roles y contenido.
        - temperatura: Controla la aleatoriedad de las respuestas.
        - top_p: Controla la probabilidad acumulativa para la selección de palabras.
        - max_tokens: Número máximo de tokens en la respuesta.

        Retorna:
        - Respuesta JSON del modelo remoto.
        """
        carga = {
            "model": self.nombre_modelo,  # Especifica el modelo a usar
            "messages": mensajes,  # Mensajes enviados al modelo
            "temperature": temperatura,  # Parámetro de aleatoriedad
            "top_p": top_p,  # Parámetro de probabilidad acumulativa
            "max_tokens": max_tokens,  # Límite de tokens en la respuesta
        }
        try:
            respuesta = requests.post(
                f"{self.url_base}/chat/completions",  # Endpoint para completar chats
                headers=ENCABEZADOS,  # Encabezados HTTP
                json=carga,  # Cuerpo de la solicitud en formato JSON
                verify=False,  # Desactiva la verificación SSL
                timeout=120,  # Tiempo máximo de espera para la solicitud
            )
            respuesta.raise_for_status()  # Lanza una excepción si la solicitud falla
            return respuesta.json()  # Retorna la respuesta en formato JSON
        except requests.exceptions.Timeout:
            return {"choices": [{"message": {"content": "Error: Tiempo de espera excedido al consultar el modelo remoto."}}]}
        except requests.exceptions.RequestException as e:
            return {"choices": [{"message": {"content": f"Error al consultar el modelo remoto: {e}"}}]}

# ---------------------------------------------------------------------------
# Funciones de base de datos
# ---------------------------------------------------------------------------

def cargar_config_bd(archivo: str) -> dict[str, str]:
    """
    Carga la configuración de la base de datos desde un archivo docker-compose.yml.

    Parámetros:
    - archivo: Ruta al archivo docker-compose.yml.

    Retorna:
    - Diccionario con la configuración de la base de datos.
    """
    with open(archivo, 'r') as f:
        docker_compose = yaml.safe_load(f)  # Carga el archivo YAML
        puertos = docker_compose['services']['mysql']['ports']  # Obtiene los puertos configurados
        entorno = docker_compose['services']['mysql']['environment']  # Obtiene las variables de entorno
    config_bd = {
        "host": "localhost",  # Dirección del servidor MySQL
        "port": puertos[0].split(':')[0],  # Extrae el puerto local
        "user": entorno['MYSQL_USER'],  # Usuario de la base de datos
        "password": str(entorno['MYSQL_PASSWORD']),  # Contraseña del usuario
        "database": entorno['MYSQL_DATABASE'],  # Nombre de la base de datos
    }
    return config_bd

def conectar_bd(config: dict[str, str]):
    """
    Conecta a la base de datos MySQL usando la configuración proporcionada.

    Parámetros:
    - config: Diccionario con la configuración de la base de datos.

    Retorna:
    - Conexión a la base de datos.
    """
    return mysql.connector.connect(**config)  # Establece la conexión usando los parámetros

def obtener_descripcion_esquema(conexion, base_datos) -> str:
    """
    Devuelve una descripción del esquema de la base de datos.

    Parámetros:
    - conexion: Conexión a la base de datos.
    - base_datos: Nombre de la base de datos.

    Retorna:
    - Cadena con la descripción del esquema.
    """
    with conexion.cursor() as cursor:
        cursor.execute("SELECT table_name FROM information_schema.tables")  # Obtiene los nombres de las tablas
        tablas = cursor.fetchall()  # Recupera los resultados
        descripcion = f"Esquema de {base_datos}:\n"  # Encabezado del esquema
        for (tabla,) in tablas:
            descripcion += f"Tabla: {tabla}\nColumnas:\n"  # Agrega el nombre de la tabla
            cursor.execute(
                "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = %s;",
                (tabla,),  # Obtiene las columnas de la tabla
            )
            for columna, tipo_dato in cursor.fetchall():
                descripcion += f"  - {columna} ({tipo_dato})\n"  # Agrega las columnas y sus tipos
            descripcion += "\n"
    return descripcion  # Retorna la descripción completa

# ---------------------------------------------------------------------------
# Utilidades de texto
# ---------------------------------------------------------------------------

def limpiar_markdown(texto: str) -> str:
    """
    Elimina caracteres básicos de formato Markdown.

    Parámetros:
    - texto: Texto con formato Markdown.

    Retorna:
    - Texto limpio sin formato Markdown.
    """
    patrones = [r"\*\*(.+?)\*\*", r"__(.+?)__", r"\*(.+?)\*", r"_(.+?)_", r"`(.+?)`"]  # Patrones de Markdown
    for patron in patrones:
        texto = re.sub(patron, r"\1", texto)  # Reemplaza los patrones por texto limpio
    return texto.strip()  # Retorna el texto limpio y sin espacios extra

# ---------------------------------------------------------------------------
# Funciones que interactúan con el LLM
# ---------------------------------------------------------------------------

def generar_sql(esquema: str, pregunta: str, modelo: ModeloRemoto) -> str:
    """Genera una consulta SQL basada en el esquema y la pregunta."""
    mensajes = [
        {
        "role": "system",
        "content": "You are an expert in SQL and MySQL."
        },
        {
        "role": "user",
        "content": (
            "Given the following schema:\n" + esquema + "\n\n"
            f"Generate an SQL query to answer the question: {pregunta}. "
            "Use only the available tables and columns and return only the query, without any formatting."
        ),
        }
    ]
    respuesta = modelo.generar_respuesta(mensajes=mensajes, temperatura=0.1, top_p=0.9, max_tokens=4096)
    sql = respuesta["choices"][0]["message"]["content"].strip()
    return re.sub(r"```sql\s*|\s*```", "", sql)


def ejecutar_sql(conexion, consulta: str) -> List[Any]:
    """Ejecuta la consulta y maneja errores."""
    cursor = conexion.cursor()
    try:
        cursor.execute(consulta)
        if consulta.strip().lower().startswith("select"):
            resultado = cursor.fetchall()
            if resultado is None or len(resultado) == 0:
                return ["No se encontraron resultados."]
            return resultado
        else:
            return ["La consulta SQL debe ser un SELECT."]
    except Exception as e:
        return [f"Error al ejecutar la consulta SQL: {str(e)}"]


def formatear_resultado(resultado: List[Any]) -> str:
    """Convierte la lista/tuplas en una cadena legible."""
    if len(resultado) == 1:
        resultado_formateado = resultado[0]
    else:
        resultado_formateado = "\n".join([
            f"{i+1}. {', '.join(str(v) for v in fila)}"
            for i, fila in enumerate(resultado)
        ])
    return resultado_formateado


def generar_respuesta_natural(pregunta: str, resultado: str, modelo: ModeloRemoto) -> str:
    """Genera una respuesta en lenguaje natural basada en la pregunta y el resultado."""
    mensajes = [
        {
            "role": "system",
            "content": (
                "Responde en español usando solo la información de 'Datos' y 'Pregunta'. "
                "No menciones SQL ni tablas. Sé claro y conciso. Sin Markdown."
            ),
        },
        {"role": "user", "content": f"Pregunta: {pregunta}\nDatos: {resultado}"},
    ]
    respuesta = modelo.generar_respuesta(mensajes=mensajes, temperatura=0.3, top_p=0.9, max_tokens=4096)
    return limpiar_markdown(respuesta["choices"][0]["message"]["content"])


# ---------------------------------------------------------------------------
# Programa principal
# ---------------------------------------------------------------------------

def main() -> None:
    config_bd = cargar_config_bd("docker-compose.yml")
    with conectar_bd(config_bd) as conexion:
        esquema = obtener_descripcion_esquema(conexion, config_bd['database'])
        print("Esquema cargado con éxito.")
        modelo = ModeloRemoto(URL_BASE_LM, NOMBRE_MODELO)
        print("Modelo cargado con éxito.")
        while True:
            pregunta = input("Introduce tu pregunta (o 'exit'): ").strip()
            if pregunta.lower() == "exit":
                break
            if not pregunta:
                print("Pregunta vacía.")
                continue

            sql = generar_sql(esquema, pregunta, modelo)
            if not sql: 
                sql = "No se pudo generar la consulta."
                continue

            print(f"\nSQL Generado:\n{sql}\n")
            resultado = formatear_resultado(ejecutar_sql(conexion, sql))
            print(f"Resultado:\n{resultado}\n")
            respuesta = generar_respuesta_natural(pregunta, resultado, modelo)
            print(f"Respuesta:\n{respuesta}\n")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nPrograma interrumpido por el usuario.")