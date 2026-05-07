## 🐉 Shokan-LLM: Resumen de Proyecto

**Shokan-LLM** es una plataforma de infraestructura de IA agéntica diseñada para orquestar un "Data Lake vivo". Su nombre rinde homenaje al Príncipe Goro (Mortal Kombat), representando la capacidad de una "mente central" (LLM) de operar simultáneamente con múltiples "brazos" (conectores MCP) sobre datos en tiempo real y repositorios estáticos.

### 1. Propósito y Valor Añadido
El objetivo principal es eliminar la brecha entre el modelo de lenguaje y los datos operativos de una empresa.
*   **Contexto Híbrido:** Combina **RAG** (conocimiento histórico y masivo) con **MCP** (datos vivos y capacidad de acción).
*   **Soberanía de Datos:** Ejecución local mediante **Ollama** para privacidad, con opción de escalado a nubes comerciales.
*   **Despliegue Simplificado:** Un ecosistema basado en **Kubernetes** con un instalador visual que democratiza la configuración de infraestructuras complejas de IA.

---

### 2. Diseño de Arquitectura (Modelo Mixto)
La arquitectura se divide en cuatro capas modulares que garantizan escalabilidad y eficiencia de tokens:

*   **Capa de Orquestación (Core en Python):** El corazón del sistema. Gestiona la lógica de los agentes, decide qué herramientas activar y ensambla el "Mega-Prompt" final.
*   **Capa de Inteligencia (LLM Gateway):** Utiliza **LiteLLM** como proxy unificado. Permite alternar entre modelos locales (**Ollama**) y modelos cloud de pago (**Claude, Gemini, GPT-4o**) mediante llaves API, manteniendo una interfaz estándar.
*   **Capa de Memoria Estática (RAG):** Una base de datos vectorial (ej. **ChromaDB**) que almacena documentación técnica, manuales y archivos "fríos" para búsqueda semántica.
*   **Capa de Acción y Contexto Vivo (MCP Servers):** Microservicios independientes que conectan la IA con:
    *   **Desarrollo:** Git (repositorios locales/remotos), Filesystem.
    *   **Gestión:** Jira, Confluence.
    *   **Comunicación:** Slack, Gmail, Google Drive.
    *   **Infraestructura:** Amazon S3.



---

### 3. Stack Tecnológico Relacionado
Para la construcción del "Core" y la infraestructura, se han seleccionado las siguientes herramientas:

| Componente | Tecnología Seleccionada |
| :--- | :--- |
| **Lenguaje Core** | **Python** (por su ecosistema de librerías de IA y conectividad). |
| **Orquestación de Contenedores** | **Kubernetes** (K8s/K3s) para la gestión dinámica de pods. |
| **Motor LLM Local** | **Ollama** (ejecución eficiente de Llama 3, Mistral, etc.). |
| **Proxy de Modelos** | **LiteLLM** (unifica APIs locales y cloud en formato OpenAI). |
| **Protocolo de Contexto** | **Model Context Protocol (MCP)** para la comunicación con herramientas. |
| **Base de Datos Vectorial** | **ChromaDB** o **Milvus** (para el almacenamiento de embeddings RAG). |
| **Interfaz de Usuario (UI)** | **LibreChat** o **Dify** (como frontend para el usuario final). |

---

### 4. Flujo de Trabajo (Workflow)
1.  **Instalación:** El usuario despliega el **Shokan-LLM** (el instalador visual) en un clúster K8s.
2.  **Configuración:** Desde la UI, se activan los servidores MCP necesarios (ej. "Conectar mi Git" o "Conectar mi Jira").
3.  **Consulta:** El usuario pregunta al Core.
4.  **Ejecución:** El Core en Python recupera fragmentos del RAG y datos vivos de los servidores MCP, limpia el ruido para ahorrar tokens y envía el prompt optimizado al LLM seleccionado.
5.  **Respuesta:** La IA responde con datos reales y actualizados al segundo.