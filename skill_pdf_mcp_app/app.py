"""
Skill "geração de PDF" empacotada como MCP server — para o Unity AI Gateway.

Este é o ponto central do Pilar 5 da POC Ero: uma *skill* (no sentido Genie Code =
instruções + script + arquivo de referência) NÃO é consumível por um agente externo
nem testável no Playground. O que o AI Gateway serve a um agente é uma *tool*. Então
empacotamos a capacidade da skill como TOOLS de um MCP próprio (Streamable HTTP,
stateless), hospedado como Databricks App e registrado no Unity AI Gateway.

- A "instrução" da skill  -> vira a docstring/description da tool (o agente lê e decide usar).
- O "script" da skill      -> vira o corpo da tool (reportlab gerando o PDF).
- O "arquivo de referência"-> vira o catálogo de templates (tool listar_templates).

Dados/branding 100% fictícios (empresa de mineração "Andes Metais").
"""
import base64
import io
from datetime import datetime, timezone

from fastmcp import FastMCP

mcp = FastMCP(name="skill-pdf-mcp")


# --- "arquivos de referência" da skill: catálogo de templates -------------
_TEMPLATES = {
    "relatorio_operacional": {
        "titulo_padrao": "Relatório Operacional",
        "cor": (0.13, 0.29, 0.51),  # navy
        "rodape": "Andes Metais — Uso interno",
    },
    "memorando": {
        "titulo_padrao": "Memorando",
        "cor": (0.20, 0.20, 0.20),
        "rodape": "Andes Metais — Confidencial",
    },
    "sumario_executivo": {
        "titulo_padrao": "Sumário Executivo",
        "cor": (0.00, 0.44, 0.40),  # teal
        "rodape": "Andes Metais — Diretoria",
    },
}

# Volume UC onde o PDF é persistido (governado). O SP do App precisa de WRITE VOLUME.
_VOLUME_DIR = "/Volumes/larissa_xm/mcps/skill_artifacts"


def _render_pdf(titulo: str, conteudo: str, template: str) -> bytes:
    """Gera os bytes de um PDF simples com reportlab, aplicando o template."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.pdfgen import canvas

    tpl = _TEMPLATES.get(template, _TEMPLATES["relatorio_operacional"])
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4

    # cabeçalho colorido
    c.setFillColorRGB(*tpl["cor"])
    c.rect(0, h - 3 * cm, w, 3 * cm, fill=1, stroke=0)
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 20)
    c.drawString(2 * cm, h - 2 * cm, titulo or tpl["titulo_padrao"])

    # corpo
    c.setFillColorRGB(0.1, 0.1, 0.1)
    c.setFont("Helvetica", 11)
    text = c.beginText(2 * cm, h - 4.5 * cm)
    for line in (conteudo or "").splitlines() or ["(sem conteúdo)"]:
        # quebra grosseira pra não estourar a largura
        while len(line) > 95:
            text.textLine(line[:95])
            line = line[95:]
        text.textLine(line)
    c.drawText(text)

    # rodapé
    c.setFont("Helvetica-Oblique", 8)
    c.setFillColorRGB(0.5, 0.5, 0.5)
    c.drawString(2 * cm, 1.2 * cm,
                 f"{tpl['rodape']}  ·  gerado em {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}")
    c.showPage()
    c.save()
    return buf.getvalue()


@mcp.tool
def gerar_pdf(titulo: str, conteudo: str, template: str = "relatorio_operacional") -> dict:
    """
    Gera um documento PDF a partir de um título e um corpo de texto, aplicando um
    template de marca. Use quando o usuário pedir um relatório, memorando ou sumário
    em PDF. Templates disponíveis: relatorio_operacional, memorando, sumario_executivo
    (chame listar_templates para ver detalhes). O PDF é persistido num Volume governado
    do Unity Catalog e o caminho é retornado.
    """
    pdf = _render_pdf(titulo, conteudo, template)
    size = len(pdf)
    fname = f"{template}_{datetime.now(timezone.utc):%Y%m%dT%H%M%S}.pdf"
    dest = f"{_VOLUME_DIR}/{fname}"

    persisted, note = False, ""
    try:
        # Persiste no Volume via Files API (SP do App precisa de WRITE VOLUME).
        from databricks.sdk import WorkspaceClient
        WorkspaceClient().files.upload(dest, io.BytesIO(pdf), overwrite=True)
        persisted = True
    except Exception as e:  # best-effort: não falha a tool se o volume não estiver pronto
        note = f"PDF gerado em memória (não persistido): {type(e).__name__}: {e}"

    return {
        "status": "ok",
        "template": template,
        "size_bytes": size,
        "volume_path": dest if persisted else None,
        "persisted": persisted,
        "note": note,
        # prévia pequena pra provar o conteúdo sem poluir o Playground com o base64 inteiro
        "base64_preview": base64.b64encode(pdf).decode()[:80] + "...",
    }


@mcp.tool
def listar_templates() -> list:
    """Lista os templates de PDF disponíveis (os 'arquivos de referência' da skill)."""
    return [{"template": k, "titulo_padrao": v["titulo_padrao"], "rodape": v["rodape"]}
            for k, v in _TEMPLATES.items()]


@mcp.tool
def now(timezone_name: str = "UTC") -> str:
    """Retorna a data/hora atual em ISO 8601 (UTC). Sanity check."""
    return datetime.now(timezone.utc).isoformat()


# Health check na raiz — o proxy do Databricks Apps checa GET "/" (senão 502).
from starlette.requests import Request  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402


@mcp.custom_route("/", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "mcp": "/mcp", "skill": "gerar_pdf"})


# FastMCP gerencia o próprio servidor/lifespan. NÃO usar uvicorn externo app:app (502).
if __name__ == "__main__":
    import os

    mcp.run(
        transport="http",
        host="0.0.0.0",
        port=int(os.getenv("DATABRICKS_APP_PORT", "8080")),
        path="/mcp",
        stateless_http=True,
    )
